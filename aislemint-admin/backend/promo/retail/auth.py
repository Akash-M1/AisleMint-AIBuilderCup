"""Account boundary in addition to OneSales staff PINs. Credentials never enter model data."""
from contextvars import ContextVar
from datetime import timedelta
import hashlib, hmac, os, re, secrets
from fastapi import HTTPException, Request
from ..pg import database
from .repository import now, shop

principal=ContextVar('retail_principal',default=None)
COOKIE='hindsight_session'

def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def password_hash(password,salt):
    return hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
def credentials(password):
    if not 12<=len(password)<=128: raise ValueError('Use a password of 12–128 characters.')
    salt=secrets.token_hex(16)
    return {'salt':salt,'password_hash':password_hash(password,salt)}
def email_address(value):
    value=value.strip().lower()
    if len(value)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',value): raise ValueError('Enter a valid email address.')
    return value

def test_bypass():
    # Deliberately impossible for Atlas or a non-test database.
    from psycopg.conninfo import conninfo_to_dict
    from ..pg import _conninfo
    if os.environ.get('AISLEMINT_TEST_AUTH_BYPASS',os.environ.get('HINDSIGHT_TEST_AUTH_BYPASS'))!='1':return False
    config=conninfo_to_dict(_conninfo())
    return config.get('dbname')=='aislemint_test' and config.get('host') in ('127.0.0.1','localhost')


def actor(): return (principal.get() or {}).get('id','system')
def account():
    p=principal.get()
    if not p: raise HTTPException(401,'Sign in to your retailer account.')
    return p

def allowed_retailers():
    p=account()
    return None if p.get('admin') else list(p.get('memberships',{}))

def require_retailer(retailer_id,write=False,owner=False):
    p=account()
    if p.get('admin'): return
    role=p.get('memberships',{}).get(retailer_id)
    if not role or (write and role=='viewer') or (owner and role!='owner'):
        raise HTTPException(403,'Your account cannot perform this operation for this retailer.')
    if p.get('integration') and owner: raise HTTPException(403,'This operation requires an owner account.')

def require_workspace(workspace,write=False):
    p=account();row=shop(workspace)
    if row: require_retailer(row['retailer_id'],write=write)
    elif not p.get('admin'): raise HTTPException(404,'Workspace not found in your retailer account.')
    return row

def admin():
    p=account()
    if not p.get('admin'): raise HTTPException(403,'A platform administrator is required.')
    return p

def authenticate(request:Request):
    if test_bypass(): return {'id':'test-admin','admin':True,'memberships':{}}
    token=request.cookies.get(COOKIE)
    db=database()
    if token:
        from ..pg import operation_connection
        from ..pg_documents import decode, json_value
        # Check revocation/expiry and current user access in one database snapshot.
        # Never cache session validity or permissions across requests.
        with operation_connection(read_only=True) as conn:
            row=conn.execute(
                "SELECT u.data FROM retail_sessions s JOIN retail_users u "
                "ON u._id=s.data->>'user_id' WHERE s._id=%s "
                "AND s.data->'expires_at'>%s AND u.data->'disabled'='false'::jsonb",
                (digest(token),json_value(now()))
            ).fetchone()
        user=decode(row[0]) if row else None
        if user and user.get('must_change_password') and user.get('temporary_password_expires_at',now())<=now():
            raise HTTPException(401,'Your temporary password has expired. Contact hello@aislemint.com.')
        if user: return {k:user.get(k, {} if k in ('memberships','module_roles') else None) for k in ('id','email','name','admin','memberships','module_roles','must_change_password')}
    authorization=request.headers.get('authorization','')
    if authorization.startswith('Bearer hsk_'):
        key=db.retail_keys.find_one({'_id':digest(authorization[7:]),'revoked':False})
        if key: return {'id':key['id'],'admin':False,'integration':True,'memberships':{key['retailer_id']:'planner'}}
    raise HTTPException(401,'Sign in to your retailer account.')

def check_scope(request,p):
    """Reject guessed workspace IDs before endpoint dispatch; endpoint repositories repeat ownership checks."""
    path=request.url.path;write=request.method not in ('GET','HEAD','OPTIONS')
    if p.get('must_change_password') and path not in ('/api/retail/me','/api/retail/auth/password','/api/retail/auth/logout'):
        raise HTTPException(403,'Change your temporary password before accessing your workspace.')
    if p.get('integration') and not path.startswith('/api/retail/'):
        raise HTTPException(403,'Integration keys are limited to retailer data ingestion.')
    if path.startswith('/api/retail/'):
        if p.get('integration') and not (path in ('/api/retail/me','/api/retail/contract') or re.fullmatch(r'/api/retail/shops/[a-f0-9]{32}/snapshots(?:/[a-f0-9]{32}(?:/batches|/finalize)?)?',path) or (request.method=='POST' and re.fullmatch(r'/api/retail/shops/[a-f0-9]{32}/connected/sources/[a-f0-9]{32}/batches',path))):
            raise HTTPException(403,'This integration key only permits sales snapshot ingestion.')
        return
    if path.startswith('/api/pos/'):
        match=re.match(r'/api/pos/stores/([^/]+)',path)
        if match:
            row=database().pos_stores.find_one({'id':match[1]})
            if not row: raise HTTPException(401,'Store access is not authorized.')
            if not p.get('admin'): require_retailer(row.get('retailer_id',''),write=write)
        elif write: admin() # New shops are onboarded through the retailer workflow.
        return
    if path.startswith('/api/integrations/'):
        admin();return # Legacy integration is admin-only; new keys cannot write here.
    for value in (request.headers.get('x-workspace-id') if not path.startswith('/api/demand/') and path!='/api/datasets' else None,request.query_params.get('source_id')):
        if value: require_workspace(value,write=write)
    if path.startswith('/api/datasets/') or path=='/api/demand/train': admin()
    if path in ('/api/summary','/api/catalog','/api/hindsight','/api/forecast','/api/optimize','/api/scenarios','/api/audit') or path.startswith(('/api/promotions','/api/scenarios/')):
        require_workspace(request.headers.get('x-workspace-id','benchmark'),write=write)

def login(email,password,ip):
    db=database();email=email_address(email);bucket=digest(ip+'|'+email);time=now()
    attempt=db.retail_login_attempts.find_one_and_update({'_id':bucket},
        {'$inc':{'attempts':1},'$setOnInsert':{'expires_at':time+timedelta(minutes=15)}},upsert=True,return_document=True)
    if attempt['expires_at']<=time:
        db.retail_login_attempts.update_one({'_id':bucket},{'$set':{'attempts':1,'expires_at':time+timedelta(minutes=15)}})
    elif attempt['attempts']>8: raise HTTPException(429,'Too many attempts. Try again in 15 minutes.')
    user=db.retail_users.find_one({'email':email,'disabled':False})
    salt=user['salt'] if user else '0'*32
    candidate=password_hash(password,salt)
    if not user or not hmac.compare_digest(candidate,user['password_hash']): raise HTTPException(401,'Incorrect email or password.')
    if user.get('must_change_password') and user.get('temporary_password_expires_at',time)<=time:
        raise HTTPException(401,'Your temporary password has expired. Contact hello@aislemint.com for a new invitation.')
    token=secrets.token_urlsafe(48)
    db.retail_sessions.insert_one({'_id':digest(token),'user_id':user['_id'],'expires_at':time+timedelta(hours=8)})
    db.retail_login_attempts.delete_one({'_id':bucket})
    return token
