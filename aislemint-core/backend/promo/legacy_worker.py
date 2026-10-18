"""Drain durable historical training requests on the existing modelling worker."""
from .jobs import training_lease
from .pg import database


def process_one():
    db=database()
    pending={'status':{'$in':['queued','training']}}
    if not any(db[name].find_one(pending) for name in ('datasets','demand_jobs','pos_model_jobs')):return False
    if not training_lease.acquire(blocking=False):return False
    try:
        # Only the renewable global lease holder can recover an interrupted fit.
        for name in ('datasets','demand_jobs','pos_model_jobs'):
            db[name].update_many({'status':'training'},{'$set':{'status':'failed','error':'Previous worker lease expired; queue a fresh training request.'}})
        job=db.datasets.find_one_and_update({'status':'queued'},{'$set':{'status':'training'}},return_document=True)
        if job:
            from .legacy_data_api import train_job
            train_job(job['id'],release=False);return True
        job=db.demand_jobs.find_one_and_update({'status':'queued'},{'$set':{'status':'training'}},return_document=True)
        if job:
            from .demand_api import training_job
            training_job(job['source_id'],release=False);return True
        job=db.pos_model_jobs.find_one({'status':'queued'})
        if job:
            from .pos_learning import run_job
            run_job(job['id'],lease_held=True);return True
        return False
    finally:training_lease.release()
