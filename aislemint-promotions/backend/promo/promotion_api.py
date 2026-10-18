"""Promotion HTTP adapter; runtime composition belongs to Core."""
import csv,io,json,re,uuid
from functools import lru_cache
from fastapi import APIRouter,HTTPException,Header
from fastapi.responses import Response
from .pg import MongoRoute,connection
from .data import DATA,ARTIFACTS
from .engine import Engine,Scenario,OptimizeRequest
from .promotions import review_token,calendar_snapshot
router=APIRouter(route_class=MongoRoute,tags=['Promotions'])
def service():
    from . import api
    return api

def audit(*args):return service().audit(*args)
def stamp():return service().stamp()
def workspace_path(workspace):
    from .retail import auth,modelling
    from .retail.repository import shop
    if auth.principal.get(): auth.require_workspace(workspace)
    if shop(workspace): return modelling.model_paths(workspace,'promotion',required=False)
    if workspace=='benchmark':return service().DATA/'processed',ARTIFACTS
    if not re.fullmatch(r'[a-f0-9]{32}',workspace):raise HTTPException(404,'Unknown workspace.')
    p=service().DATA/'uploads'/workspace
    if not p.exists():raise HTTPException(404,'Unknown workspace.')
    return p,p/'artifacts'

def get_engine(workspace='benchmark'):
    from .retail import auth,modelling
    from .retail.repository import shop
    if auth.principal.get(): auth.require_workspace(workspace)
    if shop(workspace):
        run=modelling.resolve(workspace,'promotion')
        return modelling.promotion_engine(workspace,run['id'])
    return legacy_engine(workspace)

@lru_cache(maxsize=3)
def legacy_engine(workspace='benchmark'):
    path,art=workspace_path(workspace)
    if workspace!='benchmark':
        with connection() as c:row=c.run('api_get_engine_aggregate_datasets', (workspace,)).fetchone()
        if row is None or row['status']!='ready':raise HTTPException(503,'Model is not trained yet. Open Data & models to train this workspace.')
    if not (art/'model.joblib').exists():raise HTTPException(503,'Model is not trained yet. Open Data & models to train this workspace.')
    return Engine(path/'panel.parquet',art)

get_engine.cache_clear=legacy_engine.cache_clear

@router.get('/api/summary')
def summary(x_workspace_id:str=Header(default='benchmark')):
    from .retail.repository import shop
    retail_shop=shop(x_workspace_id)
    p,a=service().workspace_path(x_workspace_id)
    quality=json.loads((p/'quality.json').read_text()) if (p/'quality.json').exists() else {}
    card=json.loads((a/'model_card.json').read_text()) if (a/'model_card.json').exists() else None
    baskets=json.loads((service().DATA/'processed/baskets.json').read_text()) if x_workspace_id=='benchmark' and (service().DATA/'processed/baskets.json').exists() else None
    stores=[]
    if card:
        e=service().get_engine(x_workspace_id);stores=[dict(store_id=s,name='Store '+s) for s in sorted(e.panel.store_id.unique(),key=str)]
        if x_workspace_id=='benchmark':
            lookup={str(x['STORE_ID']):x['STORE_NAME'].title() for x in json.loads((service().DATA/'processed/stores.json').read_text())}
            stores=[dict(store_id=x['store_id'],name=lookup.get(x['store_id'],x['name'])) for x in stores]
    if retail_shop:
        quality={**quality,'currency':retail_shop['currency'],'country':retail_shop['country']}
        stores=[dict(store_id=retail_shop['store_code'],name=retail_shop['name'])]
    return dict(quality=quality,model=card,baskets=baskets,stores=stores,workspace=x_workspace_id)

@router.get('/api/catalog')
def catalog(store_id:str='all',x_workspace_id:str=Header(default='benchmark')):return service().get_engine(x_workspace_id).catalog(store_id)

@router.get('/api/hindsight')
def hindsight(product_id:str,store_id:str='367',x_workspace_id:str=Header(default='benchmark')):
    return service().get_engine(x_workspace_id).hindsight(product_id,store_id)

@router.post('/api/forecast')
def forecast(req:Scenario,x_workspace_id:str=Header(default='benchmark')):
    r=service().get_engine(x_workspace_id).forecast(req,calendar=calendar_snapshot(x_workspace_id,req.store_id));r['confirmation_token']=review_token(req,r)
    audit('forecast',x_workspace_id,dict(request=req.model_dump(),model_id=r['model_id']));return r

@router.post('/api/optimize')
def optimize(req:OptimizeRequest,x_workspace_id:str=Header(default='benchmark')):
    e=service().get_engine(x_workspace_id)
    if not e.card['recommendation_gate']:raise HTTPException(409,'Model did not beat the naive benchmarks on the final time holdout. Recommendations are gated; scenarios remain available for analysis.')
    r=e.optimize(req,calendar=calendar_snapshot(x_workspace_id,req.store_id))
    if r.get('forecast'):r['forecast']['confirmation_token']=review_token(Scenario(**r['scenario']),r['forecast'])
    audit('optimize',x_workspace_id,dict(request=req.model_dump(),selected=len(r['selected']),model_id=e.model.model_id));return r

@router.post('/api/scenarios')
def save_scenario(req:Scenario,x_workspace_id:str=Header(default='benchmark')):
    result=service().get_engine(x_workspace_id).forecast(req,calendar=calendar_snapshot(x_workspace_id,req.store_id));id=uuid.uuid4().hex
    with connection() as c:c.run('api_save_scenario_insert_scenarios', (id, x_workspace_id, req.name, req.model_dump_json(), json.dumps(result), stamp()))
    audit('scenario_saved',x_workspace_id,dict(id=id,model_id=result['model_id']))
    return dict(id=id,name=req.name)

@router.get('/api/scenarios')
def list_scenarios(x_workspace_id:str=Header(default='benchmark')):
    with connection() as c:rows=c.run('api_list_scenarios_aggregate_scenarios', (x_workspace_id,)).fetchall()
    return [dict(id=r['id'],name=r['name'],created=r['created'],request=json.loads(r['request']),result=json.loads(r['result'])) for r in rows]

@router.get('/api/scenarios/{id}/export')
def export_scenario(id:str,x_workspace_id:str=Header(default='benchmark')):
    with connection() as c:r=c.run('api_export_scenario_aggregate_scenarios', (id, x_workspace_id)).fetchone()
    if not r:raise HTTPException(404,'Scenario not found.')
    data=json.loads(r['result']);buf=io.StringIO();w=csv.DictWriter(buf,fieldnames=['date','baseline','forecast','lower','upper','phase','portfolio_incremental']);w.writeheader();w.writerows(data['weeks'])
    return Response(buf.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="aislemint-{id[:8]}.csv"'})

@router.get('/api/audit')
def audit_trail(x_workspace_id:str=Header(default='benchmark')):
    with connection() as c:rows=c.run('api_audit_trail_aggregate_audit', (x_workspace_id,)).fetchall()
    return [dict(r) for r in rows]

from .promotion_ai import FlyerScanRequest,CampaignKitRequest,scan_competitor_flyer,generate_campaign_kit

@router.post('/api/promotions/flyer-scan')
def flyer_scan(req:FlyerScanRequest,x_workspace_id:str=Header(default='benchmark')):
    e=service().get_engine(x_workspace_id)
    res=scan_competitor_flyer(e,x_workspace_id,req)
    audit('flyer_scan',x_workspace_id,dict(store_id=req.store_id,offers=len(res.get('counter_offers',[]))))
    return res

@router.post('/api/promotions/campaign-kit')
def campaign_kit(req:CampaignKitRequest,x_workspace_id:str=Header(default='benchmark')):
    res=generate_campaign_kit(req, workspace=x_workspace_id)
    audit('campaign_kit',x_workspace_id,dict(store_id=req.store_id,product_id=req.product_id))
    return res



