from .storage import AssetPath,managed,read_frame,write_frame,load_model,dump_model
"""Confirmed operational promotion calendar; saving a scenario alone never schedules an offer."""
import hashlib
import json
import uuid
import pandas as pd
from fastapi import APIRouter,Header,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from .engine import Scenario
from .offer_mechanics import OfferTerms,compatible,terms_dict

from .pg import MongoRoute
router=APIRouter(route_class=MongoRoute,prefix='/api/promotions',tags=['Confirmed promotions'])


def service():
    from . import api
    return api


def review_token(scenario,result):
    payload=scenario.model_dump();payload.pop('name',None)
    payload['start_date']=result['start_date']
    payload['promotions']=sorted(payload['promotions'],key=lambda p:p['product_id'])
    payload['model_id']=result['model_id']
    payload['calculation_version']=result.get('calculation_version')
    payload['calendar_revision']=result.get('promotion_calendar',{}).get('revision',0)
    payload['mechanics_models']=sorted({p['model_id'] for p in result.get('offer_profiles',[]) if p.get('model_id')})
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()


class ConfirmRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    scenario:Scenario
    review_token:str=Field(pattern=r'^[a-f0-9]{64}$')
    idempotency_key:str=Field(min_length=8,max_length=128,pattern=r'^[A-Za-z0-9_-]+$')


def revision(con,source):
    row=con.run('promotions_revision_aggregate_promotion_calendar_revisions', (source,)).fetchone()
    return row['revision'] if row else 0


def advance(con,source):
    con.run('promotions_advance_upsert_promotion_calendar_revisions', (source,))
    return revision(con,source)


def decode(row):
    return dict(id=row['id'],name=row['name'],workspace=row['workspace'],status=row['status'],
        created=row['created'],cancelled=row['cancelled'],request=json.loads(row['request']),result=json.loads(row['result']))


@router.post('/confirm')
def confirm(req:ConfirmRequest,x_workspace_id:str=Header(default='benchmark')):
    s=service();s.workspace_path(x_workspace_id)
    with s.connection() as con:
        old=con.run('promotions_confirm_aggregate_confirmed_promotions', (x_workspace_id, req.idempotency_key)).fetchone()
        if old:
            if old['review_token']!=req.review_token or review_token(req.scenario,json.loads(old['result']))!=req.review_token:raise HTTPException(409,'This confirmation key was already used for another scenario.')
            if old['status']!='confirmed':raise HTTPException(409,'This confirmation was cancelled. Review and confirm a new decision.')
            return dict(promotion=decode(old),calendar_revision=revision(con,x_workspace_id),replayed=True)
    snapshot=calendar_snapshot(x_workspace_id,req.scenario.store_id)
    e=s.get_engine(x_workspace_id)
    try:result=e.forecast(req.scenario,calendar=snapshot)
    except ValueError as exc:
        if 'overlap' in str(exc):raise HTTPException(409,str(exc))
        raise
    if review_token(req.scenario,result)!=req.review_token:
        raise HTTPException(409,'The reviewed forecast no longer matches these inputs or the current model. Run the scenario again before confirming.')
    if not result['feasible']:raise HTTPException(409,'The forecast exceeds the supplied promotion inventory constraint. Resolve the shortage or update the constraint before confirmation.')
    normalized=req.scenario.model_copy(update={'start_date':result['start_date']})
    _,future,start=e._future(normalized,snapshot)
    events=[];id=uuid.uuid4().hex
    for offer in normalized.promotions:
        active=future[future.product_id.eq(offer.product_id)&future.date.ge(start)&future.date.lt(start+pd.Timedelta(weeks=offer.duration))]
        for r in active.itertuples():
            events.append((id,x_workspace_id,str(r.store_id),offer.product_id,str(r.date.date()),offer.discount,int(offer.feature),int(offer.display),1))
    if not events:raise HTTPException(422,'No active product/store dates were available to confirm.')
    with s.connection() as con:
        con.begin()
        # Recheck after acquiring the writer lock for concurrent retries.
        old=con.run('promotions_confirm_aggregate_confirmed_promotions', (x_workspace_id, req.idempotency_key)).fetchone()
        if old:
            if old['review_token']!=req.review_token or review_token(req.scenario,json.loads(old['result']))!=req.review_token or old['status']!='confirmed':raise HTTPException(409,'Confirmation key already used.')
            return dict(promotion=decode(old),calendar_revision=revision(con,x_workspace_id),replayed=True)
        shop=con.db.retail_shops.find_one({'_id':x_workspace_id},session=con.session)
        if shop:
            expected=result['model_id'].removeprefix('promotion-')
            checked=con.db.retail_shops.update_one({'_id':x_workspace_id,'active.promotion':expected},{'$inc':{'calendar_confirmation_sequence':1}},session=con.session)
            if not checked.matched_count:raise HTTPException(409,'The active shop model changed during confirmation. Run the scenario again.')
        if revision(con,x_workspace_id)!=snapshot['revision']:raise HTTPException(409,'The promotion calendar changed during confirmation. Run the scenario again.')
        for event in events:
            conflicts=[x['terms'] for x in snapshot['events'] if (x['store_id'],x['product_id'],x['period_end'])==event[2:5]]
            current=next(p for p in normalized.promotions if p.product_id==event[3])
            try:compatible([OfferTerms(**terms_dict(current))]+[OfferTerms(**t) for t in conflicts])
            except ValueError as exc:raise HTTPException(409,str(exc))
        con.run('promotions_confirm_insert_confirmed_promotions', (id, x_workspace_id, req.scenario.name, 'confirmed', normalized.model_dump_json(), json.dumps(result), req.review_token, req.idempotency_key, s.stamp(), None, result['model_id']))
        con.run_many('promotions_confirm_insert_promotion_events', events)
        rev=advance(con,x_workspace_id)
        con.run('promotions_confirm_insert_audit', ('promotion_confirmed', x_workspace_id, json.dumps(dict(id=id, calendar_revision=rev, model_id=result['model_id'])), s.stamp()))
        row=con.run('promotions_confirm_aggregate_confirmed_promotions_2', (id,)).fetchone()
    return dict(promotion=decode(row),calendar_revision=rev,replayed=False)


@router.post('/{id}/cancel')
def cancel(id:str,x_workspace_id:str=Header(default='benchmark')):
    s=service();s.workspace_path(x_workspace_id)
    with s.connection() as con:
        con.begin()
        row=con.run('promotions_cancel_aggregate_confirmed_promotions', (id, x_workspace_id)).fetchone()
        if not row:raise HTTPException(404,'Confirmed promotion not found in this workspace.')
        if row['status']=='confirmed':
            con.run('promotions_cancel_update_confirmed_promotions', (s.stamp(), id))
            con.run('promotions_cancel_update_promotion_events', (id,))
            advance(con,x_workspace_id)
            con.run('promotions_confirm_insert_audit', ('promotion_cancelled', x_workspace_id, json.dumps(dict(id=id)), s.stamp()))
        return dict(id=id,status='cancelled',calendar_revision=revision(con,x_workspace_id))


def calendar_snapshot(source_id,store_id='all'):
    s=service()
    requested_store=store_id;linked_store=None;local_holidays=[];source_revision=0
    with s.connection() as con:
        con.begin()
        # A OneSales daily demand snapshot follows its operational store's
        # confirmed weekly planning calendar, including later cancellations.
        if source_id.startswith('demand_'):
            source=con.run('promotions_calendar_snapshot_aggregate_demand_datasets', (source_id,)).fetchone()
            quality=json.loads(source['quality']) if source else {}
            pos_id=quality.get('pos_store_id')
            pos=con.run('promotions_calendar_snapshot_aggregate_pos_stores', (pos_id,)).fetchone() if pos_id else None
            if pos:
                local_holidays=[{**dict(r),'store_id':requested_store if requested_store!='all' else quality.get('model_store_id',pos_id)} for r in con.run('promotions_calendar_snapshot_aggregate_pos_holidays_2', (pos_id,))]
                source_revision=pos['revision']
                if pos['forecast_workspace']:
                    source_id=pos['forecast_workspace'];store_id=pos['forecast_store_id'];linked_store=requested_store if requested_store!='all' else quality.get('model_store_id',pos_id)
        if not local_holidays and not source_id.startswith('demand_'):
            local_holidays=[dict(r) for r in con.run('promotions_calendar_snapshot_aggregate_pos_holidays', (source_id, store_id, store_id))]
        rev=revision(con,source_id)
        rows=con.run('promotions_calendar_snapshot_aggregate_confirmed_promotions', (source_id,)).fetchall()
        selector={'workspace':source_id,'active':1}
        if store_id!='all':selector['store_id']=store_id
        events=con.aggregate('promotion_events',[{'$match':selector},{'$sort':{'period_end':1,'product_id':1,'store_id':1}}]).fetchall()

    campaigns=[decode(r) for r in rows if store_id=='all' or json.loads(r['request'])['store_id'] in [store_id,'all']]
    offers={(r['id'],p['product_id']):p for r in campaigns for p in r['request']['promotions']}
    return dict(revision=rev+source_revision*1000000000,calendar_source_id=source_id,local_holidays=local_holidays,campaigns=campaigns,events=[{**dict(r),**({'store_id':linked_store} if linked_store else {}),'terms':terms_dict(offers[(r['campaign_id'],r['product_id'])]),
        'funding_per_unit':offers[(r['campaign_id'],r['product_id'])].get('funding_per_unit',0)} for r in events])


@router.get('')
def listing(store_id:str='all',x_workspace_id:str=Header(default='benchmark')):
    service().workspace_path(x_workspace_id)
    return calendar_snapshot(x_workspace_id,store_id)
