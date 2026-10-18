"""Immutable data releases with a coalescing outbox and fenced publication.

Connectors only signal change. Consumers read sealed chunks, never mutable source
objects. Failed deliveries retain their release and are safe to replay.
"""
from datetime import datetime, timedelta
import os
import threading
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from ..pg import database, connection, retry_transaction
from ..retail.repository import uid, now, required_shop, public, audit
from ..retail import datasets, modelling
from ..connected.repository import digest
from . import COLLECTIONS
from .contracts import DOMAINS, VERSION, MAX_ROWS, Subscription, merge_sales, weekly_sales, analytics

def initialize():
    pass

def source_ids(config): return [id for domain in DOMAINS for id in config.get(domain,[])]

def assert_sources(config, workspace, db=None, session=None):
    db=database() if db is None else db
    for domain,kind in DOMAINS.items():
        for id in config.get(domain,[]):
            source=db.connect_sources.find_one({'_id':id,'workspace':workspace},session=session)
            if not source or source['object_type']!=kind: raise HTTPException(422,f'{domain}: select a matching connection from this shop.')
            if source['visibility']!='team': raise HTTPException(409,'Shared data products require team-visible sources. Restricted data cannot flow into shared models or analytics.')

@retry_transaction
def configure(workspace, req:Subscription, actor):
    db=database(); config=req.model_dump()
    with connection() as c:
        c.begin(); assert_sources(config,workspace,db,c.session)
        old=db.data_products.find_one({'_id':workspace},session=c.session)
        if (old['revision'] if old else 0)!=req.revision: raise HTTPException(409,'Data subscription changed. Refresh before saving.')
        # Write source rows too, so a simultaneous visibility change conflicts.
        for id in source_ids(config): db.connect_sources.update_one({'_id':id},{'$set':{'shared_product':workspace}},session=c.session)
        row=dict(_id=workspace,workspace=workspace,config={**config,'revision':req.revision+1},revision=req.revision+1,
            generation=(old.get('generation',0) if old else 0)+1,dirty=True,due_at=now(),updated=now(),actor=actor)
        if old and old.get('active_release'): row['active_release']=old['active_release']
        db.data_products.replace_one({'_id':workspace},row,upsert=True,session=c.session)
        audit('data_subscription_saved',workspace,actor,{'revision':row['revision'],'sources':source_ids(config)},session=c.session)
    return row

def source_changed(db, session, workspace, source_id):
    """Transactional outbox: committed source checkpoint and dirty generation agree."""
    db.data_products.update_one({'_id':workspace,'$or':[{f'config.{d}':source_id} for d in DOMAINS]},
        {'$inc':{'generation':1},'$set':{'dirty':True,'due_at':now()+timedelta(seconds=10),'updated':now()}},session=session)

@retry_transaction
def request_publish(workspace, actor):
    db=database()
    with connection() as c:
        c.begin(); product=db.data_products.find_one({'_id':workspace},session=c.session)
        if not product: raise HTTPException(409,'Save a data subscription first.')
        pending=db.data_jobs.find_one({'workspace':workspace,'status':{'$in':['queued','running']}},session=c.session)
        if pending: return pending
        generation=product['generation']+1
        db.data_products.update_one({'_id':workspace},{'$set':{'generation':generation,'dirty':True,'due_at':now()}},session=c.session)
        return enqueue(db,c.session,{**product,'generation':generation},actor)

def enqueue(db, session, product, actor):
    row=dict(_id=uid(),id=None,workspace=product['workspace'],generation=product['generation'],revision=product['revision'],
        config=product['config'],status='queued',created=now(),actor=actor,attempts=0)
    row['id']=row['_id']; db.data_jobs.insert_one(row,session=session)
    return row

@retry_transaction
def schedule_one(workspace=None):
    db=database()
    with connection() as c:
        c.begin()
        for p in db.data_products.find({**({'_id':workspace} if workspace else {}),'dirty':True,'config.auto_publish':True,'due_at':{'$lte':now()}},session=c.session).limit(50):
            if db.data_jobs.find_one({'workspace':p['workspace'],'$or':[{'generation':p['generation']},{'status':{'$in':['queued','running']}}]},session=c.session): continue
            db.data_products.update_one({'_id':p['_id']},{'$set':{'scheduled_at':now()}},session=c.session)
            enqueue(db,c.session,p,'data-platform'); return True
    return False

def owned(db, job, session=None):
    if not db.data_jobs.find_one({'_id':job['id'],'status':'running','worker':job['worker'],'expires_at':{'$gt':now()}},session=session):
        raise RuntimeError('Data publication lease was superseded.')

def write_chunks(db, session, release, domain, records):
    for offset in range(0,len(records),500):
        rows=records[offset:offset+500]
        db.data_chunks.insert_one(dict(_id=uid(),workspace=release['workspace'],release_id=release['id'],domain=domain,
            page=offset//500,records=rows,checksum=digest(rows)),session=session)

def records(release,domain):
    result=[]
    for chunk in database().data_chunks.find({'workspace':release['workspace'],'release_id':release['id'],'domain':domain}).sort('page',1):
        if digest(chunk['records'])!=chunk['checksum']: raise ValueError('Data release checksum mismatch. Restore the immutable release before use.')
        result.extend(chunk['records'])
    return result

@retry_transaction
def freeze(job):
    """One Mongo snapshot reads all sources and commits a sealed, replayable release."""
    db=database(); workspace=job['workspace']; config=job['config']; shop=required_shop(workspace)
    with connection() as c:
        c.begin(); s=c.session; owned(db,job,s)
        p=db.data_products.find_one({'_id':workspace},session=s)
        if p['generation']!=job['generation']: raise ValueError('Sources changed before publication. A newer release will be scheduled.')
        ids=source_ids(config); assert_sources(config,workspace,db,s)
        if db.connect_runs.find_one({'workspace':workspace,'source_id':{'$in':ids},'status':{'$in':['queued','running']}},session=s):
            raise ValueError('Selected sources have unfinished syncs. Finish them before publishing.')
        sources=list(db.connect_sources.find({'workspace':workspace,'_id':{'$in':ids}},session=s))
        for source in sources:
            if not source.get('enabled') or source.get('last_status')!='complete': raise ValueError(f'{source["name"]}: an enabled connection with a clean completed sync is required.')
            last=db.connect_runs.find_one({'source_id':source['id']},sort=[('created',-1),('_id',-1)],session=s)
            if not last or last['status']!='complete': raise ValueError(f'{source["name"]}: the latest sync has failed or has quality issues. Correct it before publishing.')
            if last['source']['revision']!=source['revision']: raise ValueError(f'{source["name"]}: sync using the current mapping revision before publishing.')
        objects={}
        for domain in DOMAINS:
            objects[domain]=list(db.connect_objects.find({'workspace':workspace,'source_id':{'$in':config[domain]}},
                {'_id':0,'id':1,'external_id':1,'source_id':1,'revision':1,'properties':1,'provenance':1},session=s).sort('id',1).limit(MAX_ROWS+1))
            if len(objects[domain])>MAX_ROWS: raise ValueError(f'{domain} exceeds {MAX_ROWS:,} source records. Partition this data product before publishing.')
            if config[domain] and {o['source_id'] for o in objects[domain]} != set(config[domain]): raise ValueError(f'{domain}: every selected source needs records.')
        sales=merge_sales(objects['sales'],config,shop) if config['sales'] else []
        catalog={o['external_id']:o['properties'] for o in objects['catalog']}
        if config['catalog']:
            missing={r['product_id'] for r in sales}-catalog.keys()
            if missing: raise ValueError(f'{len(missing)} sales product IDs are absent from the selected catalogue. Align identities before publishing.')
            if any(p.get('currency',shop['currency'])!=shop['currency'] for p in catalog.values()): raise ValueError('Catalogue currency must match the shop.')
        stock_keys=set()
        for obj in objects['inventory']:
            r=obj['properties']; key=(r['product_id'],datetime.fromisoformat(r['captured_at'].replace('Z','+00:00')))
            if key in stock_keys: raise ValueError('Duplicate inventory product/timestamp across sources. Choose one inventory authority.')
            stock_keys.add(key)
            if config['catalog'] and r['product_id'] not in catalog: raise ValueError('Inventory product ID is absent from the selected catalogue.')
            for field in ('on_hand','unit_cost','reserved','backlog','lead_time_days','review_period_days','case_pack'):
                if field in r and (r[field]<0 or (field in ('unit_cost','case_pack') and r[field]==0)): raise ValueError(f'Inventory {field} must be nonnegative (cost and case pack must be positive).')
            if r.get('currency',shop['currency'])!=shop['currency']: raise ValueError('Inventory currency must match the shop.')
            if datetime.fromisoformat(r['captured_at'].replace('Z','+00:00'))>now(): raise ValueError('Observed inventory cannot be dated in the future.')
            if r.get('unit_cost'):
                from ..inventory import StockPosition
                StockPosition.model_validate({**{k:r[k] for k in ('product_id','on_hand','unit_cost','reserved','backlog','lead_time_days','review_period_days','case_pack') if k in r},'store_id':shop['store_code']})
        content=digest(dict(version=VERSION,frequency=config['frequency'],overlap=config['sales_overlap'],
            objects={d:[{k:o[k] for k in ('id','revision','source_id','properties')} for o in rows] for d,rows in objects.items()}))
        release=db.data_releases.find_one({'workspace':workspace,'content_sha256':content},session=s)
        if not release:
            id=uid(); release=dict(_id=id,id=id,workspace=workspace,contract_version=VERSION,content_sha256=content,created=now(),
                status='sealed',source_ids=ids,frequency=config['frequency'],sales_overlap=config['sales_overlap'],demo=bool(shop.get('demo')),
                counts={**{d:len(rows) for d,rows in objects.items()},'canonical_sales':len(sales)},
                date_start=sales[0]['date'] if sales else None,date_end=max(r['date'] for r in sales) if sales else None,
                lineage=[dict(source_id=r['id'],name=r['name'],mapping_revision=r['revision'],run_id=r['last_run_id'],last_sync=r['last_sync']) for r in sources])
            if sales: release['date_start']=min(r['date'] for r in sales)
            db.data_releases.insert_one(release,session=s)
            for domain,rows in objects.items(): write_chunks(db,s,release,'source_'+domain,rows)
            write_chunks(db,s,release,'sales',sales)
        db.data_jobs.update_one({'_id':job['id'],'worker':job['worker']},{'$set':{'release_id':release['id'],'stage':'Delivering model snapshots'}},session=s)
    return release

def deliver(release,config,actor,heartbeat=lambda:None):
    db=database(); workspace=release['workspace']; rows=records(release,'sales'); consumers={}
    if not rows: return consumers
    for kind in ('demand','promotion'):
        heartbeat()
        data,omitted=weekly_sales(rows) if kind=='promotion' and release['frequency']=='daily' else (rows,0)
        frequency='weekly' if kind=='promotion' else release['frequency']
        key='data-'+release['id']+'-'+kind
        snap=datasets.create(workspace,'Connected data '+release['id'][:8]+' · '+kind,kind,frequency,key,actor)
        db.retail_snapshots.update_one({'_id':snap['id']},{'$set':{'data_release_id':release['id'],'source_ids':release['source_ids'],'lineage':release['lineage']}})
        for offset in range(0,len(data),2000):
            heartbeat(); datasets.append(workspace,snap['id'],str(offset),data[offset:offset+2000])
        snap=datasets.finalize(workspace,snap['id'],actor)
        consumer=dict(snapshot_id=snap['id'],status=snap['status'],report=snap['report'],excluded_partial_week_rows=omitted)
        if config['auto_train'] and snap['status']=='ready':
            heartbeat(); run=modelling.queue(workspace,snap['id'],config['profile'],key+'-'+config['profile'],actor)
            consumer['run_id']=run['id']
        consumers[kind]=consumer
        db.data_releases.update_one({'_id':release['id']},{'$set':{f'consumers.{kind}':consumer}})
    return consumers

@retry_transaction
def publish(job,release):
    db=database()
    with connection() as c:
        c.begin(); owned(db,job,c.session)
        result=db.data_products.update_one({'_id':job['workspace'],'generation':job['generation'],'revision':job['revision']},
            {'$set':{'active_release':release['id'],'dirty':False,'published_at':now()},'$unset':{'error':''}},session=c.session)
        status='complete' if result.matched_count else 'superseded'
        db.data_jobs.update_one({'_id':job['id'],'worker':job['worker']},{'$set':{'status':status,'finished':now(),'stage':status}},session=c.session)
        if result.matched_count:
            db.data_releases.update_one({'_id':release['id']},{'$set':{'status':'published','published_at':now()}},session=c.session)
            audit('data_release_published',job['workspace'],job['actor'],{'release_id':release['id'],'checksum':release['content_sha256']},session=c.session)

def process_one(workspace=None):
    db=database(); worker=uid()
    job=db.data_jobs.find_one_and_update({**({'workspace':workspace} if workspace else {}),'$or':[{'status':'queued'},{'status':'running','expires_at':{'$lt':now()}}]},
        {'$set':{'status':'running','worker':worker,'expires_at':now()+timedelta(minutes=3)},'$inc':{'attempts':1}},sort=[('created',1)],return_document=True)
    if not job: return False
    def heartbeat():
        result=db.data_jobs.update_one({'_id':job['id'],'worker':worker,'status':'running','expires_at':{'$gt':now()}}, {'$set':{'expires_at':now()+timedelta(minutes=3)}})
        if not result.matched_count: raise RuntimeError('Data publication lease expired.')
    try:
        release=db.data_releases.find_one({'_id':job.get('release_id'),'workspace':job['workspace']}) if job.get('release_id') else None
        if not release: release=freeze(job)
        heartbeat(); deliver(release,job['config'],job['actor'],heartbeat)
        heartbeat(); publish(job,release)
    except Exception as exc:
        message=(str(exc.detail) if isinstance(exc,HTTPException) else str(exc))[:1200] if isinstance(exc,(ValueError,HTTPException)) else f'{type(exc).__name__}: publication failed. Retry this delivery; sealed data and the previous release are retained.'
        db.data_jobs.update_one({'_id':job['id'],'worker':worker,'status':'running'},{'$set':{'status':'failed','error':message,'finished':now()}})
        db.data_products.update_one({'_id':job['workspace'],'generation':job['generation']},{'$set':{'error':message}})
    return True

def active(workspace):
    p=database().data_products.find_one({'_id':workspace})
    release=database().data_releases.find_one({'_id':p.get('active_release'),'workspace':workspace}) if p else None
    if not release: raise HTTPException(409,'Publish a validated data release in Data connections first.')
    return release

def stock_position(workspace,as_of,product_ids=None):
    release=active(workspace); shop=required_shop(workspace); latest={}
    for obj in records(release,'source_inventory'):
        p=obj['properties']; captured=datetime.fromisoformat(p['captured_at'].replace('Z','+00:00'))
        if captured.astimezone(ZoneInfo(shop['timezone'])).date().isoformat()!=as_of: continue
        old=latest.get(p['product_id'])
        if not old or captured>old[0]: latest[p['product_id']]=(captured,p)
    if not latest: raise HTTPException(409,f'The published release has no observed stock for forecast origin {as_of}. Sync matching end-of-day inventory and publish again.')
    positions=[]
    for _,p in latest.values():
        if not p.get('unit_cost'): continue
        positions.append({**{k:p[k] for k in ('product_id','on_hand','unit_cost','reserved','backlog','lead_time_days','review_period_days','case_pack') if k in p},'store_id':shop['store_code']})
    if not positions: raise HTTPException(409,'Inventory planning needs positive observed unit_cost in the inventory contract.')
    if product_ids:
        missing=set(product_ids)-{p['product_id'] for p in positions}
        if missing: raise HTTPException(409,f'{len(missing)} forecast products lack matching dated stock and positive unit cost. Complete the inventory feed before loading it.')
        positions=[p for p in positions if p['product_id'] in product_ids]
    return dict(as_of=as_of,positions=positions,source='Published connected inventory',release_id=release['id'])

def report(workspace,start=None,end=None):
    from ..cache import remember
    release=active(workspace); shop=required_shop(workspace)
    def compute():
        rows=records(release,'sales')
        rows=[r for r in rows if (not start or r['date']>=start) and (not end or r['date']<=end)]
        return analytics(rows,records(release,'source_catalog'),records(release,'source_inventory'),shop['currency'])
    result=remember('analytics',workspace,[release['id'],shop['currency'],start,end],compute)
    return {**result,'release':public(release)}

def catalogue(workspace,offset=0,limit=100):
    from ..cache import remember
    release=active(workspace)
    def compute():
        rows=records(release,'source_catalog')
        return dict(release_id=release['id'],total=len(rows),items=rows[offset:offset+limit])
    return remember('catalogue',workspace,[release['id'],offset,limit],compute)

def worker(stop):
    delay=5
    while not stop.is_set():
        try:
            schedule_one()
            worked=process_one();delay=5
            if worked: continue
        except Exception:
            import logging
            logging.getLogger(__name__).warning('Shared data worker cycle failed; retrying.')
            delay=min(60,delay*2)
        stop.wait(delay)

def start_worker():
    stop=threading.Event()
    if os.environ.get('DATA_PLATFORM_WORKER','inline')=='inline': threading.Thread(target=worker,args=(stop,),daemon=True,name='shared-data').start()
    return stop

if __name__=='__main__':
    from ..pg import initialize as init_mongo
    init_mongo(); initialize(); worker(threading.Event())
