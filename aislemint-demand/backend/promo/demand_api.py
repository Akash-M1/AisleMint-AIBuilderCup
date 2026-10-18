from __future__ import annotations
from .storage import AssetPath,managed,read_frame,write_frame,load_model,dump_model
import csv
import io
import json
import logging
import os
import re
import threading
import uuid
from functools import lru_cache
from pathlib import Path
import pandas as pd
from fastapi import APIRouter,HTTPException
from fastapi.responses import Response
from pydantic import BaseModel,ConfigDict
from .demand import DemandEngine,DemandRequest,train_demand,validate_demand_panel,load_demand_panel,REQUIRED
from .inventory import InventoryRequest,StockPosition,replenishment_plan
from .promotions import calendar_snapshot

from .pg import MongoRoute
router=APIRouter(route_class=MongoRoute,prefix='/api/demand',tags=['Demand and inventory'])


def service():
    from . import api
    return api


def paths(source_id):
    from .retail import auth,modelling
    from .retail.repository import shop
    if auth.principal.get():auth.require_workspace(source_id)
    if shop(source_id):
        p,a=modelling.model_paths(source_id,'demand',required=False)
        return p/'panel.parquet',a
    s=service()
    if source_id.startswith('demand_'):
        if not re.fullmatch(r'demand_[a-f0-9]{32}',source_id):raise HTTPException(404,'Unknown demand source.')
        with s.connection() as c:r=c.run('demand_api_paths_aggregate_demand_datasets', (source_id,)).fetchone()
        if not r:raise HTTPException(404,'Unknown demand source.')
        p=s.DATA/'demand'/source_id
        return p/'panel.parquet',p/'artifacts'
    p,a=s.workspace_path(source_id)
    if not (p/'panel.parquet').exists():raise HTTPException(404,'Demand source has no validated sales history.')
    return p/'panel.parquet',a/'demand'


def engine(source_id):
    from .retail import auth,modelling
    from .retail.repository import shop
    if auth.principal.get():auth.require_workspace(source_id)
    if shop(source_id):
        run=modelling.resolve(source_id,'demand')
        return modelling.demand_engine(source_id,run['id'])
    return legacy_engine(source_id)

@lru_cache(maxsize=2)
def legacy_engine(source_id):
    p,a=paths(source_id)
    if not (a/'model_card.json').exists():raise HTTPException(409,'Train this source’s demand model first.')
    return DemandEngine(p,a,calendar_provider=calendar_snapshot)


engine.cache_clear=legacy_engine.cache_clear

@router.get('/sources')
def sources():
    from .retail.auth import principal,allowed_retailers
    from .pg import database
    if principal.get():
        allowed=allowed_retailers()
        from .retail.permissions import allows
        shops=[s for s in database().retail_shops.find({} if allowed is None else {'retailer_id':{'$in':allowed}}) if allows(principal.get(),s['retailer_id'],'demand')]
        rows=[dict(id=r['id'],name=r['name'],status='ready' if r.get('active',{}).get('demand') else 'untrained',quality={},error=None) for r in shops]
        if allowed is not None:return rows
    else:rows=[]
    retail_rows=rows
    s=service()
    with s.connection() as c:
        imported=c.run('demand_api_sources_aggregate_datasets', ()).fetchall()
        dedicated=c.run('demand_api_sources_aggregate_demand_datasets', ()).fetchall()
        jobs={r['source_id']:dict(r) for r in c.run('demand_api_sources_aggregate_demand_jobs', ()).fetchall()}
    rows=[dict(id='benchmark',name='dunnhumby weekly benchmark')]+[dict(r) for r in imported]+[dict(r) for r in dedicated]
    for r in rows:
        p,a=paths(r['id']);job=jobs.get(r['id'],{})
        r['status']='ready' if (a/'model_card.json').exists() else job.get('status','untrained')
        r['error']=job.get('error')
        q=p.parent/'quality.json'
        r['quality']=json.loads(q.read_text()) if q.exists() else {}
    return retail_rows+rows


@router.get('/summary')
def summary(source_id:str='benchmark'):
    p,a=paths(source_id)
    if not p.exists():
        from .retail.repository import shop
        row=shop(source_id)
        if row:return dict(source_id=source_id,quality={'currency':row['currency'],'rows':0,'frequency':'Awaiting history','start':'','end':'','step_days':1},model=None,stores=[row['store_code']])
        raise HTTPException(404,'Demand source is missing.')
    frame,quality=load_demand_panel(p)
    card=json.loads((a/'model_card.json').read_text()) if (a/'model_card.json').exists() else None
    return dict(source_id=source_id,quality=quality,model=card,stores=sorted(frame.store_id.astype(str).unique()))


@router.get('/catalog')
def catalog(source_id:str='benchmark',store_id:str='367'):return engine(source_id).catalog(store_id)


@router.get('/calendar')
def planning_calendar(source_id:str='benchmark',store_id:str='367',horizon:int=8):
    e=engine(source_id)
    if not 1<=horizon<=e.bundle.horizon:raise ValueError('Calendar horizon exceeds this model’s forecast horizon.')
    cal=calendar_snapshot(source_id,store_id)
    dates=pd.date_range(e.last+pd.Timedelta(days=e.step),periods=horizon,freq=f'{e.step}D')
    holidays=getattr(e.bundle,'holiday_calendar',None)
    holiday_events=holidays.calendar(dates,e.step) if holidays else []
    for h in cal.get('local_holidays',[]):
        for end in dates:
            if end-pd.Timedelta(days=e.step)<pd.Timestamp(h['date'])<=end:
                if not any(x['date']==h['date'] and x['name']==h['name'] for x in holiday_events):holiday_events.append(dict(date=h['date'],name=h['name'],kind='occasion',origin='client_store_calendar',period_end=str(end.date())))
    return dict(**cal,as_of=str(e.last.date()),period_ends=[str(d.date()) for d in dates],
        holidays=holiday_events,
        holiday_metadata=holidays.metadata if holidays else None,
        events_in_horizon=[v for v in cal['events'] if pd.Timestamp(v['period_end'])>e.last and pd.Timestamp(v['period_end'])-pd.Timedelta(days=6 if e.step==1 else 0)<=dates[-1]])


class SourceRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_id:str='benchmark'


def training_job(source_id,release=True):
    s=service()
    try:
        p,a=paths(source_id);card=train_demand(p,a)
        with s.connection() as c:c.run('demand_api_training_job_update_demand_jobs', ('ready', s.stamp(), source_id))
        engine.cache_clear();s.audit('demand_model_trained',source_id,{'model_id':card['model_id']})
    except Exception as exc:
        logging.exception('Demand training failed')
        message=str(exc) if isinstance(exc,ValueError) else 'Training failed; inspect the local API log.'
        with s.connection() as c:c.run('demand_api_training_job_update_demand_jobs_2', ('failed', message, s.stamp(), source_id))
    finally:
        if release:s.training_lock.release()


@router.post('/train')
def start_training(req:SourceRequest):
    s=service();p,a=paths(req.source_id)
    if (a/'model.joblib').exists() and (a/'model_card.json').exists():raise HTTPException(409,'This version is trained. Import a new sales snapshot to retrain without replacing it.')
    if os.environ.get('LEGACY_TRAINING_WORKER')=='external':
        from .pg import database
        from psycopg.errors import UniqueViolation
        db=database();job=db.demand_jobs.find_one({'source_id':req.source_id})
        if job and job['status'] in ('queued','training'):raise HTTPException(409,'This source is already queued for training.')
        try:
            if job:
                changed=db.demand_jobs.update_one({'source_id':req.source_id,'status':job['status']},{'$set':{'status':'queued','error':None,'updated':s.stamp()}})
                if not changed.matched_count:raise HTTPException(409,'This source changed; refresh before training.')
            else:db.demand_jobs.insert_one(dict(source_id=req.source_id,status='queued',error=None,updated=s.stamp()))
        except UniqueViolation:raise HTTPException(409,'This source is already queued for training.')
        return dict(status='queued',source_id=req.source_id)
    if not s.training_lock.acquire(blocking=False):raise HTTPException(409,'Another local training job is already running.')
    try:
        with s.connection() as c:c.run('demand_api_start_training_insert_demand_jobs', (req.source_id, 'training', None, s.stamp()))
        threading.Thread(target=training_job,args=(req.source_id,),daemon=True).start()
    except Exception:s.training_lock.release();raise
    return dict(status='training',source_id=req.source_id)






@router.post('/forecast')
def forecast(req:DemandRequest):
    result=engine(req.source_id).forecast(req)
    service().audit('demand_forecast',req.source_id,dict(request=req.model_dump(),model_id=result['model_id']))
    return result


@router.post('/forecast-export')
def forecast_export(req:DemandRequest):
    result=engine(req.source_id).forecast(req)
    buf=io.StringIO();fields=['store_id','product_id','name','date','forecast','lower','upper','price','model_id','as_of']
    writer=csv.DictWriter(buf,fieldnames=fields);writer.writeheader()
    for series in result['series']:
        for point in series['rows']:
            row={**series,**point,'model_id':result['model_id'],'as_of':result['as_of']}
            writer.writerow({k:("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v) for k,v in row.items() if k in fields})
    service().audit('demand_forecast_exported',req.source_id,dict(model_id=result['model_id']))
    return Response(buf.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="aislemint-demand-forecast.csv"'})


@router.post('/inventory')
def inventory(req:InventoryRequest):
    result=replenishment_plan(engine(req.forecast.source_id),req)
    service().audit('inventory_plan',req.forecast.source_id,dict(model_id=result['model_id'],orders=result['orders'],spend=result['budget_used']))
    return result






@router.post('/plans')
def save(req:InventoryRequest):
    s=service();result=replenishment_plan(engine(req.forecast.source_id),req);id=uuid.uuid4().hex
    with s.connection() as c:c.run('demand_api_save_insert_inventory_plans', (id, req.forecast.source_id, req.name, req.model_dump_json(), json.dumps(result), s.stamp()))
    s.audit('inventory_plan_saved',req.forecast.source_id,dict(id=id,model_id=result['model_id']))
    return dict(id=id,result=result)


@router.get('/plans')
def plans(source_id:str='benchmark'):
    paths(source_id)
    with service().connection() as c:rows=c.run('demand_api_plans_aggregate_inventory_plans', (source_id,)).fetchall()
    return [dict(id=r['id'],name=r['name'],created=r['created'],request=json.loads(r['request']),result=json.loads(r['result'])) for r in rows]


@router.get('/plans/{id}/export')
def export(id:str,source_id:str='benchmark'):
    paths(source_id)
    with service().connection() as c:row=c.run('demand_api_export_aggregate_inventory_plans', (id, source_id)).fetchone()
    if not row:raise HTTPException(404,'Inventory plan not found in this source.')
    result=json.loads(row['result']);buf=io.StringIO()
    fields=['store_id','product_id','name','action','order_quantity','purchase_cost','arrival_date','next_review_date','safety_stock','expected_shortfall','fill_rate','post_arrival_service','incremental_contribution','model_id','as_of','inventory_basis']
    w=csv.DictWriter(buf,fieldnames=fields);w.writeheader()
    for row in result['rows']:
        record={**row,'model_id':result['model_id'],'as_of':result['as_of'],'inventory_basis':result['inventory_basis']}
        safe={k:("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v) for k,v in record.items() if k in fields}
        w.writerow(safe)
    return Response(buf.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="aislemint-inventory-order-suggestions.csv"'})
