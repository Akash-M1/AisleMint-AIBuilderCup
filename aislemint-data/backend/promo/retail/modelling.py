"""Durable shop-scoped candidates and atomic serving pointers. Never auto-promote a fit."""
from functools import lru_cache
from importlib.metadata import version,PackageNotFoundError
from pathlib import Path
import hashlib,json,logging,os,threading
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from ..pg import database,connection
from ..storage import AssetPath
from ..jobs import training_lease
from .repository import uid,now,required_shop,shop,audit
from . import datasets

POLICY='retail-validation-v1'
def code_fingerprint():
    from promo import __path__ as roots
    digest=hashlib.sha256()
    # Stable logical module keys: repository paths never change the model identity.
    sources={}
    for root in map(Path,roots):
        for path in root.rglob('*.py'):
            key=path.relative_to(root).as_posix()
            if key.endswith('__init__.py'):continue
            if key in sources and sources[key]!=path.read_bytes():
                raise RuntimeError('Duplicate module ownership: '+key)
            sources[key]=path.read_bytes()
    for key,content in sorted(sources.items()):
        digest.update(key.encode());digest.update(content)
    return digest.hexdigest()

def queue(workspace,snapshot_id,profile,key,actor):
    row=datasets.get(workspace,snapshot_id)
    if row['status']!='ready':raise HTTPException(409,'Resolve this snapshot’s data readiness issues before training.')
    db=database();previous=db.retail_runs.find_one({'workspace':workspace,'request_key':key})
    if previous:
        if (previous['snapshot_id'],previous['profile'])!=(snapshot_id,profile):raise HTTPException(409,'Training key already identifies another request.')
        return previous
    if db.retail_runs.count_documents({'workspace':workspace,'status':{'$in':['queued','running']}})>=4:raise HTTPException(409,'This shop already has four queued or running candidates.')
    id=uid();record=dict(_id=id,id=id,workspace=workspace,retailer_id=required_shop(workspace)['retailer_id'],
        snapshot_id=snapshot_id,kind=row['kind'],profile=profile,request_key=key,status='queued',stage='Waiting for the training worker',
        created=now(),created_by=actor,panel_sha256=row['panel_sha256'],data_sha256=row['content_sha256'],
        artifact_path=f'retail/{workspace}/runs/{id}',validation_policy=POLICY)
    db.retail_runs.insert_one(record);audit('training_queued',workspace,actor,{'run_id':id,'snapshot_id':snapshot_id})
    return record

def gate(card,kind):
    if kind=='promotion':
        f=card['folds'][-1];score=f['model'];coverage=f['interval_coverage']
        naive=min(f['historical_mean']['wape'],f['seasonal_naive']['wape'])
        baseline=f.get('baseline_model');clean=bool(baseline and f.get('baseline_test_rows',0)>=20)
    else:
        score=card['test'];coverage=card['interval_90_coverage'];clean=True
        naive=min(c['test']['wape'] for c in card['candidates'] if c['name'] in ('recent_mean','seasonal_naive'))
    checks=[dict(name='Holdout demand beats the strongest naive benchmark',passed=score['wape']<naive,value=score['wape'],threshold=naive),
        dict(name='Absolute holdout bias ≤ 15%',passed=abs(score['bias'])<=.15,value=score['bias'],threshold=.15),
        dict(name='90% interval coverage ≥ 80%',passed=coverage is not None and coverage>=.8,value=coverage,threshold=.8),
        dict(name='Clean baseline test has at least 20 observations',passed=clean)] if kind=='promotion' else [
        dict(name='Holdout demand matches or beats strongest naive benchmark',passed=score['wape']<=naive+1e-10,value=score['wape'],threshold=naive),
        dict(name='Absolute holdout bias ≤ 15%',passed=abs(score['bias'])<=.15,value=score['bias'],threshold=.15),
        dict(name='90% interval coverage ≥ 80%',passed=coverage is not None and coverage>=.8,value=coverage,threshold=.8)]
    return dict(policy=POLICY,passed=all(c['passed'] for c in checks),checks=checks,
        note='Operational screening thresholds, not a guarantee of causal promotion lift, ROI or inventory service. Validate prospectively in this shop.')

def execute(job):
    db=database();id=job['id'];out=AssetPath(job['artifact_path']);snapshot=datasets.get(job['workspace'],job['snapshot_id'])
    panel=AssetPath(snapshot['path'])/'panel.parquet'
    if hashlib.sha256(panel.read_bytes()).hexdigest()!=job['panel_sha256']:raise ValueError('Snapshot checksum differs from the queued training request.')
    db.retail_runs.update_one({'_id':id,'status':'running'},{'$set':{'stage':'Fitting candidates and evaluating chronological holdouts'}})
    if job['kind']=='demand':
        from ..demand import train_demand
        card=train_demand(panel,out,include_xgboost=job['profile']=='comparison')
    elif job['profile']=='comparison':
        from ..challengers import train_challengers
        _,card=train_challengers(panel,out)
    else:
        from ..model import train
        card=train(panel,out)
    training_lease.assert_owned()
    deps={}
    for name in ('scikit-learn','numpy','pandas','xgboost','catboost'):
        try:deps[name]=version(name)
        except PackageNotFoundError:deps[name]='not installed'
    provenance=dict(workspace=job['workspace'],retailer_id=job['retailer_id'],run_id=id,snapshot_id=job['snapshot_id'],
        panel_sha256=job['panel_sha256'],content_sha256=job['data_sha256'],code_sha256=code_fingerprint(),
        dependencies=deps,profile=job['profile'],contract_version=snapshot['contract_version'],cross_retailer_training=False)
    if snapshot.get('data_release_id'):
        provenance.update(data_release_id=snapshot['data_release_id'],source_ids=snapshot.get('source_ids',[]),source_lineage=snapshot.get('lineage',[]))
    synthetic=bool(required_shop(job['workspace']).get('demo'))
    provenance['synthetic_demo']=synthetic
    card['model_id']=job['kind']+'-'+id
    # Connected-source lineage contains native database timestamps. Normalize
    # before the evidence is embedded in bundles, JSON manifests, and registry rows.
    card['provenance']=jsonable_encoder(provenance)
    card['limitations']=[
        'Observational promotion effects do not identify causal lift, cannibalization, halo or pantry loading.',
        'Conditional offer redemption remains an experimental proxy unless explicitly supplied and validated with this shop.',
        'Seasonality and holiday effects require repeated observations; unusual future events remain uncertain.',
        'This evaluated version retains its chronological holdouts. Refresh history and retrain before its forecast horizon becomes stale.',
        'Prediction coverage and inventory service can drift; run prospective checks in this shop.',
    ]
    if synthetic:
        card['limitations'].insert(0,'This model was trained on explicitly simulated demo trading history. Its measured accuracy does not establish performance for a real retailer.')
    if not snapshot['report']['quality'].get('availability_supplied',not snapshot['report']['quality'].get('missing_inventory',True)):
        card['limitations'].append('Stock availability was not supplied; observed sales may understate true demand.')
    if 'protocol' in card:
        card['protocol']['test_usage']='Chronological holdouts reported after calibration-based selection; do not tune repeatedly on these holdouts.'
        card['protocol']['seasonal_features']='Annual Fourier terms, calendar features and the frozen shop-market holiday calendar.'
    # Use the immutable registry run ID in every downstream forecast and receipt of model evidence.
    from ..storage import load_model,dump_model
    bundle=load_model(out/'model.joblib');bundle.model_id=card['model_id']
    if hasattr(bundle,'card'):bundle.card={**card}
    dump_model(bundle,out/'model.joblib')
    card['artifact_sha256']=hashlib.sha256((out/'model.joblib').read_bytes()).hexdigest()
    card['validation_scope']='Retrospective chronological holdouts from this shop only; prospective validation is still required.'
    selection_note=('Candidate selection precedes separate uncertainty-calibration windows.'
        if card.get('uncertainty_protocol')=='separate-selection-absolute-paths-v2'
        else 'Candidate selection uses calibration windows.')
    card['development_note']=selection_note+' Holdout results must not be treated as independent after repeated experiments on the same snapshot.'
    (out/'model_card.json').write_text(json.dumps(card,allow_nan=False))
    policy=gate(card,job['kind']);manifest={name:db.assets.find_one({'key':(out/name).key})['sha256'] for name in ['model.joblib','model_card.json','evaluation.parquet']}
    result=db.retail_runs.update_one({'_id':id,'status':'running','worker':training_lease.owner},
        {'$set':{'status':'candidate','stage':'Validation complete — review before activation','finished':now(),'card':card,'gate':policy,'manifest':manifest}})
    if not result.matched_count:raise RuntimeError('Training run ownership changed; result cannot be published.')
    audit('candidate_completed',job['workspace'],'worker',{'run_id':id,'gate_passed':policy['passed']})

def materialize_ledger(job):
    from ..pos_learning import retail_panel
    with connection() as c:
        c.begin();pos=c.db.pos_stores.find_one({'id':job['pos_store_id']},session=c.session)
        frame=retail_panel(c,pos,job['kind'],job['through'])
    snapshot=datasets.create(job['workspace'],'OneSales closed trading days',job['kind'],'weekly' if job['kind']=='promotion' else 'daily',job['id'],'onesales')
    if snapshot['status']=='receiving':
        rows=frame.to_dict('records')
        for offset in range(0,len(rows),2000):datasets.append(job['workspace'],snapshot['id'],str(offset),rows[offset:offset+2000])
    snapshot=datasets.finalize(job['workspace'],snapshot['id'],'onesales')
    training_lease.assert_owned()
    run=queue(job['workspace'],snapshot['id'],'comparison',job['id'],'onesales')
    database().retail_ledger_jobs.update_one({'_id':job['id'],'status':'processing'},{'$set':{'status':'complete','run_id':run['id'],'finished':now()}})


def process_one():
    db=database();job=None;ledger=None
    if not db.retail_runs.find_one({'status':{'$in':['queued','running']}},{'_id':1}) and not db.retail_ledger_jobs.find_one({'status':{'$in':['queued','processing']}},{'_id':1}):return False
    if not training_lease.acquire(blocking=False):return False
    try:
        # Acquiring the global renewable lease proves any previous fit no longer owns it.
        db.retail_runs.update_many({'status':'running'},{'$set':{'status':'failed','stage':'Worker interrupted','error':'Previous worker lease expired. Queue a new candidate from this immutable snapshot.','finished':now()}})
        db.retail_ledger_jobs.update_many({'status':'processing'},{'$set':{'status':'queued'}})
        ledger=db.retail_ledger_jobs.find_one_and_update({'status':'queued'},{'$set':{'status':'processing'}},sort=[('created',1)],return_document=True)
        if ledger:
            materialize_ledger(ledger);return True
        job=db.retail_runs.find_one_and_update({'status':'queued'},
            {'$set':{'status':'running','started':now(),'worker':training_lease.owner,'stage':'Verifying snapshot'}},sort=[('created',1)],return_document=True)
        if not job:return False
        execute(job);return True
    except Exception as exc:
        logging.exception('Shop training candidate failed')
        if ledger:
            db.retail_ledger_jobs.update_one({'_id':ledger['id']},{'$set':{'status':'failed','error':str(exc)[:600] if isinstance(exc,ValueError) else 'Could not create a ledger snapshot. Review data readiness and queue again.','finished':now()}})
        if job:
            db.retail_runs.update_one({'_id':job['_id'],'status':'running','worker':training_lease.owner},
                {'$set':{'status':'failed','stage':'Training failed','error':str(exc)[:600] if isinstance(exc,ValueError) else 'Training failed. Check the worker log and retry with a new run.','finished':now()}})
        return bool(job)
    finally:training_lease.release()

def worker(stop):
    delay=5
    while not stop.is_set():
        try:
            worked=process_one()
            if not worked and os.environ.get('LEGACY_TRAINING_WORKER')=='external':
                from ..legacy_worker import process_one as historical
                worked=historical()
            delay=5
        except Exception as exc:
            logging.warning('Training storage unavailable (%s); retrying with backoff.',type(exc).__name__)
            worked=False;delay=min(60,delay*2)
        if not worked:stop.wait(delay)

def start_worker():
    stop=threading.Event()
    if os.environ.get('RETAIL_TRAINING_WORKER','inline')=='inline':threading.Thread(target=worker,args=(stop,),daemon=True,name='retail-training').start()
    return stop

def resolve(workspace,kind,required=True):
    row=shop(workspace)
    if not row:return None
    id=row.get('active',{}).get(kind)
    if not id:
        if required:raise HTTPException(409,f'This shop has no active {kind} model. Open Modelling workbench to validate data, train and activate a candidate.')
        return None
    job=database().retail_runs.find_one({'_id':id,'workspace':workspace,'retailer_id':row['retailer_id'],'kind':kind,'status':'candidate'})
    if not job:raise HTTPException(503,'The active model registry entry is unavailable.')
    return job

def model_paths(workspace,kind,required=True):
    job=resolve(workspace,kind,required)
    if job:
        snapshot=datasets.get(workspace,job['snapshot_id'])
        return AssetPath(snapshot['path']),AssetPath(job['artifact_path'])
    row=database().retail_snapshots.find_one({'workspace':workspace,'kind':kind,'status':'ready'},sort=[('created',-1)])
    return (AssetPath(row['path']) if row else AssetPath(f'retail/{workspace}/untrained'),AssetPath(f'retail/{workspace}/untrained-model'))

def activate(workspace,run_id,revision,reason,actor):
    db=database();row=required_shop(workspace)
    job=db.retail_runs.find_one({'_id':run_id,'workspace':workspace,'retailer_id':row['retailer_id'],'status':'candidate'})
    if not job:raise HTTPException(404,'Candidate not found in this shop.')
    if not job['gate']['passed']:raise HTTPException(409,'Candidate did not pass the activation policy. Improve data or modelling and train a new version.')
    out=AssetPath(job['artifact_path'])
    for name,expected in job['manifest'].items():
        if hashlib.sha256((out/name).read_bytes()).hexdigest()!=expected:raise HTTPException(409,'Candidate artifacts failed integrity verification.')
    snap=datasets.get(workspace,job['snapshot_id'])
    if hashlib.sha256((AssetPath(snap['path'])/'panel.parquet').read_bytes()).hexdigest()!=job['panel_sha256']:raise HTTPException(409,'Candidate data failed integrity verification.')
    with connection() as c:
        c.begin();result=db.retail_shops.update_one({'_id':workspace,'revision':revision},
            {'$set':{f'active.{job["kind"]}':run_id},'$inc':{'revision':1}},session=c.session)
        if not result.matched_count:raise HTTPException(409,'Shop models changed while you were reviewing. Refresh before activating.')
        audit('model_activated',workspace,actor,dict(kind=job['kind'],run_id=run_id,previous=row.get('active',{}).get(job['kind']),reason=reason),session=c.session)
    return required_shop(workspace)

@lru_cache(maxsize=4)
def promotion_engine(workspace,run_id):
    from ..engine import Engine
    job=database().retail_runs.find_one({'_id':run_id,'workspace':workspace,'kind':'promotion'})
    if not job:raise HTTPException(404,'Model not found.')
    snapshot=datasets.get(workspace,job['snapshot_id'])
    engine = Engine(AssetPath(snapshot['path'])/'panel.parquet',AssetPath(job['artifact_path']))
    if os.environ.get('AISLEMINT_HACKATHON') == '1':
        engine.model.model_id = 'promotion-' + run_id
        engine.card = {**engine.card, 'model_id': engine.model.model_id,
                       'demo_note': 'Visitor-scoped registry of a shared fit on synthetic template history.'}
    return engine

@lru_cache(maxsize=4)
def demand_engine(workspace,run_id):
    from ..demand import DemandEngine
    from ..promotions import calendar_snapshot
    job=database().retail_runs.find_one({'_id':run_id,'workspace':workspace,'kind':'demand'})
    if not job:raise HTTPException(404,'Model not found.')
    snapshot=datasets.get(workspace,job['snapshot_id'])
    engine = DemandEngine(AssetPath(snapshot['path'])/'panel.parquet',AssetPath(job['artifact_path']),calendar_provider=calendar_snapshot)
    if os.environ.get('AISLEMINT_HACKATHON') == '1':
        engine.bundle.model_id = 'demand-' + run_id
    return engine

if __name__=='__main__':
    from ..pg import initialize
    from .repository import initialize as initialize_retail
    initialize();initialize_retail();worker(threading.Event())
