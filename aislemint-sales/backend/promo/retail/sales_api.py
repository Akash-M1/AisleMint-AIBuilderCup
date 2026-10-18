"""Explicit owner-controlled link between a shop and its OneSales ledger."""
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict
from ..pg import connection,MongoRoute
from . import auth
from .repository import public,required_shop,audit
router=APIRouter(prefix='/api/retail',route_class=MongoRoute,tags=['Sales onboarding'])
class Input(BaseModel):model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
from ..pos_core import StoreInput
class PosLink(Input):
    existing_store_id:str|None=None
    new_store:StoreInput|None=None

@router.post('/shops/{workspace}/pos')
def connect_pos(workspace:str,req:PosLink):
    s=required_shop(workspace);auth.require_retailer(s['retailer_id'],owner=True)
    if s['country']!='IN':raise HTTPException(409,'OneSales currently supports India / INR stores.')
    if bool(req.existing_store_id)==bool(req.new_store):raise ValueError('Choose an existing store or create a new one.')
    from ..pos_api import create_store
    with connection() as c:
        c.begin();current=c.db.retail_shops.find_one({'_id':workspace},session=c.session)
        if current.get('pos_store_id'):raise HTTPException(409,'This shop is already connected to OneSales.')
        if req.existing_store_id:
            auth.admin();pos=c.db.pos_stores.find_one({'id':req.existing_store_id},session=c.session)
            if not pos or pos['demo'] or pos.get('retailer_id'):raise HTTPException(409,'Only an unassigned, real OneSales store can be adopted.')
        else:pos=create_store(c,req.new_store)['store']
        # Do not reassign a legacy promotion calendar silently.
        if pos.get('forecast_workspace') and pos['forecast_workspace']!=workspace:raise HTTPException(409,'This existing store has a planning connection. Review its existing calendar before adopting it.')
        c.db.pos_stores.update_one({'id':pos['id']},{'$set':{'retailer_id':s['retailer_id'],'forecast_workspace':workspace,'forecast_store_id':s['store_code']}},session=c.session)
        c.db.retail_shops.update_one({'_id':workspace},{'$set':{'pos_store_id':pos['id']}},session=c.session)
        audit('pos_connected',workspace,auth.actor(),{'pos_store_id':pos['id']},session=c.session)
    return public(required_shop(workspace))

