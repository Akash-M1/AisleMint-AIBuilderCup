"""Shop-scoped data readiness, training, candidate evaluation and activation."""
from typing import Literal
import os
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from ..pg import database,connection,MongoRoute
from . import auth,datasets,modelling
from .repository import public,uid,now,required_shop,audit
from .contracts import contract
router=APIRouter(prefix='/api/retail',route_class=MongoRoute,tags=['Modelling workbench'])
class Input(BaseModel):model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
class SnapshotInput(Input):
    name:str=Field(min_length=2,max_length=120)
    kind:Literal['promotion','demand']
    frequency:Literal['daily','weekly']
    idempotency_key:str=Field(pattern=r'^[A-Za-z0-9_-]{8,128}$')
class BatchInput(Input):
    batch_key:str=Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    records:list[dict]=Field(min_length=1,max_length=2000)
class TrainInput(Input):
    snapshot_id:str=Field(pattern=r'^[a-f0-9]{32}$')
    profile:Literal['efficient','comparison']='comparison'
    idempotency_key:str=Field(pattern=r'^[A-Za-z0-9_-]{8,128}$')
class Activate(Input):
    revision:int=Field(ge=0)
    reason:str=Field(min_length=8,max_length=500)
class LedgerInput(Input):
    kind:Literal['promotion','demand']='demand'
    idempotency_key:str=Field(pattern=r'^[A-Za-z0-9_-]{8,128}$')

@router.get('/shops/{workspace}/workbench')
def workbench(workspace:str):
    auth.require_workspace(workspace);db=database();s=required_shop(workspace)
    if auth.account().get('integration'):raise HTTPException(403,'Integration keys cannot read model artifacts.')
    jobs=[public(r) for r in db.retail_runs.find({'workspace':workspace}).sort('created',-1).limit(100)]
    present={r['id'] for r in jobs}
    jobs += [public(r) for r in db.retail_runs.find({'workspace':workspace,'_id':{'$in':[id for id in s.get('active',{}).values() if id not in present]}})]
    from datetime import date,datetime
    from zoneinfo import ZoneInfo
    freshness=[]
    for kind,run_id in s.get('active',{}).items():
        run=next((r for r in jobs if r['id']==run_id),None)
        if run:
            through=run['card']['data_end'];age=(datetime.now(ZoneInfo(s['timezone'])).date()-date.fromisoformat(through)).days
            freshness.append(dict(kind=kind,data_end=through,age_days=age,stale=age>(14 if kind=='promotion' or run['card'].get('step_days')==7 else 7)))
    for r in jobs:
        if r['status']=='running':
            lease=db.leases.find_one({'_id':'model-training','owner':r.get('worker')})
            r['worker_alive']=bool(lease and lease['expires_at']>now())
    return dict(shop=public(s),snapshots=[public(r) for r in db.retail_snapshots.find({'workspace':workspace}).sort('created',-1).limit(100)],
        ledger_jobs=[public(r) for r in db.retail_ledger_jobs.find({'workspace':workspace,'status':{'$ne':'complete'}}).sort('created',-1).limit(50)],runs=jobs,audit=[public(r) for r in db.retail_audit.find({'workspace':workspace}).sort('created',-1).limit(50)],
        freshness=freshness,contract=contract(),worker_mode=os.environ.get('RETAIL_TRAINING_WORKER','inline'))

@router.post('/shops/{workspace}/snapshots')
def new_snapshot(workspace:str,req:SnapshotInput):
    auth.require_workspace(workspace,write=True)
    return public(datasets.create(workspace,req.name,req.kind,req.frequency,req.idempotency_key,auth.actor()))

@router.post('/shops/{workspace}/snapshots/{id}/batches')
def batch(workspace:str,id:str,req:BatchInput):
    auth.require_workspace(workspace,write=True);return datasets.append(workspace,id,req.batch_key,req.records)

@router.post('/shops/{workspace}/snapshots/{id}/finalize')
def finalize(workspace:str,id:str):
    auth.require_workspace(workspace,write=True);return public(datasets.finalize(workspace,id,auth.actor()))

@router.get('/shops/{workspace}/snapshots/{id}')
def snapshot(workspace:str,id:str):
    auth.require_workspace(workspace);return public(datasets.get(workspace,id))

@router.post('/shops/{workspace}/train')
def train(workspace:str,req:TrainInput):
    auth.require_workspace(workspace,write=True)
    if auth.account().get('integration'):raise HTTPException(403,'Training requires a planner account.')
    return public(modelling.queue(workspace,req.snapshot_id,req.profile,req.idempotency_key,auth.actor()))

@router.post('/shops/{workspace}/runs/{id}/cancel')
def cancel(workspace:str,id:str):
    auth.require_workspace(workspace,write=True)
    result=database().retail_runs.update_one({'_id':id,'workspace':workspace,'status':'queued'},{'$set':{'status':'cancelled','stage':'Cancelled before fitting','finished':now()}})
    if not result.matched_count:raise HTTPException(409,'Only queued jobs can be cancelled. A running fit will finish as an inactive candidate.')
    audit('training_cancelled',workspace,auth.actor(),{'run_id':id});return {'status':'cancelled'}

@router.post('/shops/{workspace}/runs/{id}/activate')
def activate(workspace:str,id:str,req:Activate):
    s=required_shop(workspace);auth.require_retailer(s['retailer_id'],owner=True)
    return public(modelling.activate(workspace,id,req.revision,req.reason,auth.actor()))

@router.get('/shops/{workspace}/runs/{id}/preview')
def preview_candidate(workspace:str,id:str):
    """Read-only shadow evaluation; never issues a promotion confirmation token."""
    auth.require_workspace(workspace)
    if auth.account().get('integration'):raise HTTPException(403,'Model previews require a shop account.')
    shop=required_shop(workspace);db=database()
    run=db.retail_runs.find_one({'_id':id,'workspace':workspace,'retailer_id':shop['retailer_id'],'status':'candidate'})
    if not run:raise HTTPException(404,'Completed candidate not found in this shop.')
    import hashlib
    from ..storage import AssetPath
    for name,expected in run['manifest'].items():
        if hashlib.sha256((AssetPath(run['artifact_path'])/name).read_bytes()).hexdigest()!=expected:
            raise HTTPException(409,'Candidate artifacts failed integrity verification.')
    if run['kind']=='demand':
        from ..demand import DemandRequest
        engine=modelling.demand_engine(workspace,id)
        product=engine.catalog(shop['store_code'])[0]
        result=engine.forecast(DemandRequest(source_id=workspace,store_id=shop['store_code'],product_ids=[product['product_id']],horizon=min(8,engine.bundle.horizon),use_confirmed_promotions=False))
        points=[dict(date=r['date'],forecast=r['forecast'],lower=r['lower'],upper=r['upper']) for r in result['series'][0]['rows']]
        assumption=f'Eight periods for {product["name"]} at regular price without promotions; existing local holiday flags are retained.'
    else:
        from ..engine import Scenario,Promotion
        engine=modelling.promotion_engine(workspace,id);product=engine.catalog(shop['store_code'])[0]
        result=engine.forecast(Scenario(store_id=shop['store_code'],promotions=[Promotion(product_id=product['product_id'],discount=.15,duration=1,display=True)]))
        points=[dict(date=r['date'],forecast=r['forecast'],lower=r['lower'],upper=r['upper'],baseline=r['baseline']) for r in result['weeks']]
        assumption=f'One week at 15% off with display on {product["name"]}, followed by four weeks. The curve shows this product. This preview does not create an offer.'
    return dict(run_id=id,kind=run['kind'],preview_only=True,serving=shop.get('active',{}).get(run['kind'])==id,
        synthetic_demo=bool(shop.get('demo')),gate=run['gate'],assumption=assumption,points=points,
        warning='Candidate preview only. Activation checks and owner review still govern live planning. No promotion, order, inventory movement or model activation is created.')

@router.post('/shops/{workspace}/ledger-snapshot')
def ledger_snapshot(workspace:str,req:LedgerInput):
    auth.require_workspace(workspace,write=True);s=required_shop(workspace)
    if not s.get('pos_store_id'):raise HTTPException(409,'Connect this shop to OneSales first.')
    from ..pos_learning import retail_panel
    with connection() as c:
        pos=c.db.pos_stores.find_one({'id':s['pos_store_id']});frame=retail_panel(c,pos,req.kind)
    records=frame.to_dict('records')
    if not records:raise HTTPException(409,'Close observed OneSales trading days before creating a snapshot.')
    row=datasets.create(workspace,'OneSales closed trading days',req.kind,'weekly' if req.kind=='promotion' else 'daily',req.idempotency_key,auth.actor())
    for offset in range(0,len(records),2000):datasets.append(workspace,row['id'],str(offset),records[offset:offset+2000])
    return public(datasets.finalize(workspace,row['id'],auth.actor()))

