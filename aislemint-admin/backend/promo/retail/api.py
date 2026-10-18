"""Retail onboarding and modelling workbench HTTP API."""
from typing import Literal
import os,secrets
from fastapi import APIRouter,Request,Response,HTTPException
from pydantic import BaseModel,ConfigDict,Field,model_validator
from ..pg import database,connection,MongoRoute
from . import auth
from .repository import public,uid,now,required_shop,audit


router=APIRouter(prefix='/api/retail',route_class=MongoRoute,tags=['Retailer platform'])
class Input(BaseModel): model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
class Login(Input):
    email:str=Field(max_length=254)
    password:str=Field(min_length=1,max_length=128)
class RetailerInput(Input):name:str=Field(min_length=2,max_length=120)
class ShopInput(Input):
    name:str=Field(min_length=2,max_length=120)
    store_code:str=Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    country:Literal['IN','US']='IN'
    timezone:str|None=Field(default=None,max_length=64)
    @model_validator(mode='after')
    def market_timezone(self):
        from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
        self.timezone=self.timezone or ('Asia/Kolkata' if self.country=='IN' else 'America/New_York')
        try:ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:raise ValueError('Use a valid IANA timezone.')
        return self
class UserInput(Input):
    name:str=Field(min_length=2,max_length=120)
    email:str=Field(max_length=254)
    password:str=Field(min_length=12,max_length=128)
    role:Literal['owner','planner','viewer']='planner'
class KeyInput(Input):name:str=Field(min_length=2,max_length=100)

@router.post('/provisioning/status')
async def provisioning_status(request:Request):
    from .provisioning import verify
    from starlette.concurrency import run_in_threadpool
    verify(request,await request.body())
    await run_in_threadpool(lambda: database().retailers.find_one({}, {'_id':1}))
    return {'ready':True}

@router.post('/provisioning/orders')
async def provision_order(request:Request):
    from .provisioning import verify, Purchase, provision
    from starlette.concurrency import run_in_threadpool
    from pydantic import ValidationError
    raw=await request.body()
    if len(raw)>8192:raise HTTPException(413,'Request too large.')
    verify(request,raw)
    try: purchase=Purchase.model_validate_json(raw)
    except (ValidationError, ValueError): raise HTTPException(422,'Invalid purchase details.')
    return await run_in_threadpool(provision,purchase)

@router.get('/auth/status')
def auth_status():return {'configured':bool(database().retail_users.find_one({'admin':True,'disabled':False},{'_id':1}))}

@router.post('/provisioning/recovery/{action}')
async def password_recovery(action:str,request:Request):
    from .provisioning import verify
    from .recovery import ResetRequest,ResetComplete,prepare,complete
    from pydantic import ValidationError
    from starlette.concurrency import run_in_threadpool
    raw=await request.body()
    if len(raw)>4096:raise HTTPException(413,'Request too large.')
    verify(request,raw)
    if action not in ('request','complete'):raise HTTPException(404,'Not found.')
    try:details=(ResetRequest if action=='request' else ResetComplete).model_validate_json(raw)
    except (ValidationError,ValueError):raise HTTPException(422,'Invalid recovery details.')
    try:return await run_in_threadpool(prepare if action=='request' else complete,details)
    except ValueError:raise HTTPException(422,'Invalid recovery details.')

@router.post('/auth/login')
def login(req:Login,request:Request,response:Response):
    token=auth.login(req.email,req.password,request.client.host if request.client else 'unknown')
    response.set_cookie(auth.COOKIE,token,httponly=True,secure=request.url.hostname not in ('localhost','127.0.0.1') or request.url.scheme=='https',samesite='strict',max_age=28800,path='/')
    return {'status':'signed_in'}

@router.post('/auth/logout')
def logout(request:Request,response:Response):
    if request.cookies.get(auth.COOKIE):database().retail_sessions.delete_one({'_id':auth.digest(request.cookies[auth.COOKIE])})
    response.delete_cookie(auth.COOKIE,path='/');return {'status':'signed_out'}

@router.get('/me')
def me():
    p=auth.account()
    if p.get('must_change_password'):return dict(user=p,retailers=[])
    allowed=auth.allowed_retailers();db=database()
    rows=list(db.retailers.find({} if allowed is None else {'_id':{'$in':allowed}}).sort('name',1))
    from ..brand import retailer_name
    from .permissions import roles
    shops={r['id']:[] for r in rows}
    if shops:
        for s in db.retail_shops.find({'retailer_id':{'$in':list(shops)}}).sort('name',1):
            shops[s['retailer_id']].append(public(s))
    return dict(user=p,retailers=[{**public(r),'roles':roles(p,r['id']),'name':retailer_name(r),'shops':shops[r['id']]} for r in rows])

@router.get('/contract')
def data_contract():
    from .contracts import contract
    auth.account();return contract()

@router.post('/retailers')
def create_retailer(req:RetailerInput):
    auth.admin();id=uid();r=dict(_id=id,id=id,name=req.name,created=now())
    database().retailers.insert_one(r);audit('retailer_created',id,auth.actor());return public(r)

@router.post('/retailers/{retailer_id}/shops')
def create_shop(retailer_id:str,req:ShopInput):
    auth.require_retailer(retailer_id,owner=True)
    db=database()
    if not db.retailers.find_one({'_id':retailer_id}):raise HTTPException(404,'Retailer not found.')
    identity={'retailer_id':retailer_id,'store_code':req.store_code}
    def reject_duplicate(existing):
        if existing:
            raise HTTPException(409,f'Store code "{req.store_code}" is already used by "{existing["name"]}" in this retailer. Open that workspace, or use a different store code for a new shop.')
    id=uid();row=dict(_id=id,id=id,retailer_id=retailer_id,**req.model_dump(),currency='INR' if req.country=='IN' else 'USD',
        created=now(),revision=0,active={})
    try:
        with connection() as c:
            c.begin()
            reject_duplicate(db.retail_shops.find_one(identity,session=c.session))
            db.retail_shops.insert_one(row,session=c.session)
            audit('shop_created',id,auth.actor(),session=c.session)
    except Exception as exc:
        if getattr(exc, 'sqlstate', '') == '23505' or 'duplicate' in str(exc).lower():
            # The unique index also protects simultaneous submissions from different sessions.
            # Read after the transaction abort so the winning request is visible.
            reject_duplicate(db.retail_shops.find_one(identity))
            raise
        raise
    return public(row)

@router.post('/retailers/{retailer_id}/users')
def create_user(retailer_id:str,req:UserInput):
    auth.require_retailer(retailer_id,owner=True)
    if not database().retailers.find_one({'_id':retailer_id}):raise HTTPException(404,'Retailer not found.')
    email=auth.email_address(req.email);db=database()
    if db.retail_users.find_one({'email':email}):raise HTTPException(409,'That account already exists. Ask the platform administrator to manage its retailer membership.')
    id=uid();db.retail_users.insert_one(dict(_id=id,id=id,email=email,name=req.name,admin=False,disabled=False,
        memberships={retailer_id:req.role},created=now(),**auth.credentials(req.password)))
    audit('retailer_account_created',retailer_id,auth.actor(),{'user_id':id,'role':req.role})
    return {'id':id,'email':email,'role':req.role}

@router.get('/retailers/{retailer_id}/users')
def users(retailer_id:str):
    auth.require_retailer(retailer_id,owner=True)
    return [{k:r[k] for k in ['id','name','email','memberships','disabled']} for r in database().retail_users.find({f'memberships.{retailer_id}':{'$exists':True}})]

@router.post('/retailers/{retailer_id}/keys')
def key(retailer_id:str,req:KeyInput):
    auth.require_retailer(retailer_id,owner=True);value='hsk_'+secrets.token_urlsafe(40);id=uid()
    database().retail_keys.insert_one(dict(_id=auth.digest(value),id=id,name=req.name,retailer_id=retailer_id,created=now(),revoked=False))
    audit('integration_key_created',retailer_id,auth.actor(),{'key_id':id})
    return dict(id=id,token=value,note='Copy once into your connector secret manager. Scope: read contract/shop IDs and ingest data for this retailer.')

@router.get('/retailers/{retailer_id}/keys')
def keys(retailer_id:str):
    auth.require_retailer(retailer_id,owner=True)
    return [public(r) for r in database().retail_keys.find({'retailer_id':retailer_id,'revoked':False})]

@router.post('/retailers/{retailer_id}/keys/{key_id}/revoke')
def revoke_key(retailer_id:str,key_id:str):
    auth.require_retailer(retailer_id,owner=True)
    database().retail_keys.update_one({'id':key_id,'retailer_id':retailer_id},{'$set':{'revoked':True}})
    audit('integration_key_revoked',retailer_id,auth.actor(),{'key_id':key_id});return {'status':'revoked'}

class PasswordChange(Input):
    current_password:str=Field(min_length=1,max_length=128)
    new_password:str=Field(min_length=12,max_length=128)

@router.post('/auth/password')
def change_password(req:PasswordChange,response:Response):
    import hmac
    p=auth.account();db=database();u=db.retail_users.find_one({'_id':p['id']})
    if not u or not hmac.compare_digest(auth.password_hash(req.current_password,u['salt']),u['password_hash']):raise HTTPException(401,'Current password is incorrect.')
    if hmac.compare_digest(req.current_password,req.new_password):raise HTTPException(422,'Choose a different new password.')
    with connection():
        changed=db.retail_users.update_one({'_id':p['id'],'password_hash':u['password_hash']},{'$set':{**auth.credentials(req.new_password),'must_change_password':False},'$unset':{'temporary_password_expires_at':'','password_reset':''}})
        if changed.matched_count!=1:raise HTTPException(409,'Password changed in another session. Sign in again.')
        db.retail_sessions.delete_many({'user_id':p['id']})
    response.delete_cookie(auth.COOKIE,path='/')
    return {'status':'password_changed','message':'Sign in again with your new password.'}

class MembershipInput(Input):role:Literal['owner','planner','viewer']|None

@router.post('/retailers/{retailer_id}/users/{user_id}/access')
def set_access(retailer_id:str,user_id:str,req:MembershipInput):
    auth.require_retailer(retailer_id,owner=True);db=database()
    user=db.retail_users.find_one({'_id':user_id,'admin':False})
    if not user or (retailer_id not in user.get('memberships',{}) and not auth.account().get('admin')):raise HTTPException(404,'Retailer account not found.')
    from .iam import update_access,AccessInput
    from .permissions import AREAS
    return update_access(retailer_id,user_id,AccessInput(
        revision=user.get('access_revision',0),membership='owner' if req.role=='owner' else ('member' if req.role else None),
        roles=dict.fromkeys(AREAS,'editor' if req.role=='planner' else 'viewer')))

class GeminiKeyInput(Input):
    api_key:str=Field(min_length=8,max_length=256)

@router.get('/ai/status')
def gemini_ai_status():
    auth.account()
    from ..gemini import ai_status
    return ai_status()

@router.post('/ai/configure')
def configure_gemini_ai(req:GeminiKeyInput):
    p=auth.account()
    if p.get('integration'):raise HTTPException(403,'Integration keys cannot configure AI credentials.')
    from ..gemini import set_runtime_api_key,ai_status
    set_runtime_api_key(req.api_key)
    return ai_status()
