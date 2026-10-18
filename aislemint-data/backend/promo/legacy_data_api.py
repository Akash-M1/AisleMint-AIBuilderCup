"""Operator-only historical dataset adapter. New shops use immutable snapshots."""
import logging,threading,os
from fastapi import APIRouter,HTTPException
from .pg import MongoRoute,connection
from .model import train
router=APIRouter(route_class=MongoRoute,tags=['Historical datasets'])
def service():
    from . import api
    return api

def audit(*args):return service().audit(*args)
@router.get('/api/datasets')
def datasets():
    from .retail.auth import principal,allowed_retailers
    if principal.get() and allowed_retailers() is not None:return []
    with connection() as c:rows=c.run('api_datasets_aggregate_datasets', ()).fetchall()
    return [dict(r) for r in rows]


from .jobs import training_lease
training_lock=training_lease
def train_job(id,release=True):
    p,a=service().workspace_path(id)
    try:
        train(p/'panel.parquet',a)
        with connection() as c:c.run('api_train_job_update_datasets', ('ready', id))
        service().get_engine.cache_clear();audit('training_complete',id,{})
    except Exception:
        logging.exception('Training failed')
        with connection() as c:c.run('api_train_job_update_datasets_2', ('failed', 'Training failed. Inspect the local API log for details.', id))
    finally:
        if release:training_lock.release()

@router.post('/api/datasets/{id}/train')
def train_dataset(id:str):
    service().workspace_path(id)
    with connection() as c:r=c.run('api_get_engine_aggregate_datasets', (id,)).fetchone()
    if not r:raise HTTPException(404,'Imported dataset not found.')
    if r['status']=='ready':raise HTTPException(409,'This version is already trained. Import a new data snapshot to retrain without replacing its model.')
    if os.environ.get('LEGACY_TRAINING_WORKER')=='external':
        from .pg import database
        updated=database().datasets.update_one({'id':id,'status':{'$nin':['queued','training','ready']}},{'$set':{'status':'queued','error':None}})
        if not updated.matched_count:raise HTTPException(409,'This dataset is already queued or trained.')
        return dict(id=id,status='queued')
    if not training_lock.acquire(blocking=False):raise HTTPException(409,'Another training job is running on this device.')
    with connection() as c:c.run('api_train_job_update_datasets', ('training', id))
    threading.Thread(target=train_job,args=(id,),daemon=True).start()
    return dict(id=id,status='training')

