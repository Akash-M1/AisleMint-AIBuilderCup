"""Shared data contracts, subscriptions, releases and consumer status."""
from datetime import date, timedelta
import os
from fastapi import APIRouter, HTTPException, Query
from ..pg import database, MongoRoute
from ..retail import auth
from ..retail.permissions import allows
from ..retail.repository import public, audit, now
from ..connected import repository as connected
from . import service
from .contracts import Subscription, registry

router=APIRouter(prefix='/api/retail/shops/{workspace}/data',route_class=MongoRoute,tags=['Shared data platform'])

@router.get('/contracts')
def contracts(workspace:str):
    connected.scope(workspace); return registry()

@router.get('/overview')
def overview(workspace:str):
    shop=connected.scope(workspace); db=database()
    product=db.data_products.find_one({'_id':workspace})
    releases=list(db.data_releases.find({'workspace':workspace}).sort('created',-1).limit(20))
    current=next((r for r in releases if product and r['id']==product.get('active_release')),None)
    if product and product.get('active_release') and not current:
        current=db.data_releases.find_one({'_id':product['active_release'],'workspace':workspace})
    jobs=list(db.data_jobs.find({'workspace':workspace}).sort('created',-1).limit(20))
    sources=list(db.connect_sources.find(connected.sources_filter(workspace)).sort('name',1))
    if current:
        for consumer in current.get('consumers',{}).values():
            if consumer.get('run_id'):
                run=db.retail_runs.find_one({'_id':consumer['run_id'],'workspace':workspace},{'status':1,'gate.passed':1})
                consumer.update(run_status=run['status'] if run else 'unavailable',gate_passed=run.get('gate',{}).get('passed') if run else None)
    return dict(subscription=public(product), release=public(current),releases=[public(r) for r in releases],
        jobs=[{k:v for k,v in public(j).items() if k not in ('worker','config')} for j in jobs],
        sources=[public(s) for s in sources], contracts=registry(),can_manage=connected.is_owner(workspace),
        can_write=allows(auth.account(),shop['retailer_id'],'data','editor'),
        active_models=shop.get('active',{}),demo=bool(shop.get('demo')),worker_mode=os.environ.get('DATA_PLATFORM_WORKER','inline'),
        stale_source_ids=[s['id'] for s in sources if not s.get('last_sync') or s['last_sync']+timedelta(hours=s['freshness_hours'])<now()])

@router.post('/subscription')
def configure(workspace:str,req:Subscription):
    connected.scope(workspace,owner=True)
    return public(service.configure(workspace,req,auth.actor()))

@router.post('/publish')
def publish(workspace:str):
    connected.scope(workspace,write=True)
    return public(service.request_publish(workspace,auth.actor()))

@router.post('/jobs/{id}/retry')
def retry(workspace:str,id:str):
    connected.scope(workspace,write=True); db=database()
    job=db.data_jobs.find_one({'_id':id,'workspace':workspace})
    if not job: raise HTTPException(404,'Delivery not found in this shop.')
    product=db.data_products.find_one({'_id':workspace})
    if job['generation']!=product['generation']: raise HTTPException(409,'This delivery has been superseded. Publish the current sources instead.')
    result=db.data_jobs.update_one({'_id':id,'status':'failed'},{'$set':{'status':'queued'},'$unset':{'error':'','finished':''}})
    if not result.matched_count: raise HTTPException(409,'Only failed deliveries can be retried.')
    audit('data_delivery_retried',workspace,auth.actor(),{'job_id':id}); return {'status':'queued'}

@router.get('/analytics')
def analytics(workspace:str,start:date|None=None,end:date|None=None):
    connected.scope(workspace)
    if start and end and start>end: raise HTTPException(422,'Start date must precede end date.')
    return service.report(workspace,start.isoformat() if start else None,end.isoformat() if end else None)

@router.get('/catalog')
def catalog(workspace:str,offset:int=Query(default=0,ge=0),limit:int=Query(default=100,ge=1,le=500)):
    connected.scope(workspace)
    return service.catalogue(workspace,offset,limit)

@router.get('/releases/{id}')
def release(workspace:str,id:str):
    connected.scope(workspace); row=database().data_releases.find_one({'_id':id,'workspace':workspace})
    if not row: raise HTTPException(404,'Release not found in this shop.')
    return public(row)
