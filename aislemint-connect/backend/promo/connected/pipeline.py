"""Durable page checkpoints with fenced writes, immutable input and row-level quarantine."""
from datetime import timedelta
import json
import os
import threading
from fastapi import HTTPException
from pymongo import ReplaceOne, UpdateOne
from ..pg import database, connection, retry_transaction
from ..retail.repository import uid, now, audit, required_shop
from ..retail.contracts import normalize
from .repository import digest, object_id, object_type
from .schema import map_record
from .connectors import fetch_page

@retry_transaction
def queue(source, key, actor, records=None):
    if not source['enabled']: raise HTTPException(409, 'This connection is paused.')
    if (source['kind'] == 'push') != (records is not None): raise HTTPException(422, 'Push connections require a records batch; pull connections use Sync.')
    request_hash = digest(records)
    with connection() as c:
        c.begin(); db=c.db; session=c.session
        existing=db.connect_runs.find_one({'source_id':source['id'], 'request_key':key},session=session)
        if existing:
            if existing['request_hash'] != request_hash: raise HTTPException(409, 'This request key already contains different data.')
            return existing
        id=uid(); row=dict(_id=id, id=id, workspace=source['workspace'], source_id=source['id'], request_key=key, request_hash=request_hash,
            source=source, schema=object_type(source['workspace'], source['object_type']), status='queued', created=now(), actor=actor,
            processed=0, accepted=0, rejected=0, page=0, cursor=None, attempts=0)
        db.connect_runs.insert_one(row, session=session)
        if records is not None: save_batch(db, session, row, records, None)
        audit('connection_sync_queued', source['workspace'], actor, {'run_id':id,'source_id':source['id']},session=session)
    return row

def save_batch(db, session, run, records, cursor):
    if len(json.dumps(records, default=str).encode()) > 4*1024*1024: raise ValueError('A source batch must not exceed 4 MiB.')
    batch=dict(_id=uid(),workspace=run['workspace'],source_id=run['source_id'],run_id=run['id'],page=run['page'],records=records,
        checksum=digest(records),next_cursor=cursor,created=now())
    db.connect_batches.insert_one(batch,session=session)
    return batch

@retry_transaction
def materialize(run, batch, worker):
    db=database(); workspace=run['workspace']; source=run['source']; spec=run['schema']
    accepted=0; rejected=0; duplicates=set(); prepared=[]; failures=[]
    shop=required_shop(workspace) if spec['name']=='SalesObservation' else None
    for index, raw in enumerate(batch['records']):
        try:
            external_id,props=map_record(raw,source,spec)
            if external_id in duplicates: raise ValueError('Duplicate source identifier in the same page; supply one authoritative record.')
            duplicates.add(external_id)
            if shop:
                normalized,errors=normalize([props],shop)
                if errors: raise ValueError(errors[0]['message'])
                props=normalized[0]
            prepared.append((index,external_id,props,object_id(workspace,spec['name'],external_id)))
        except (ValueError,TypeError,KeyError,OverflowError) as exc:
            failures.append((index,str(exc)[:800]))
    with connection() as c:
        c.begin(); session=c.session
        owned=db.connect_runs.find_one({'_id':run['id'],'status':'running','worker':worker,'page':run['page'],'expires_at':{'$gt':now()}},session=session)
        if not owned: raise RuntimeError('Sync worker lease was superseded.')
        previous={r['_id']:r for r in db.connect_objects.find({'_id':{'$in':[r[3] for r in prepared]}},session=session)}
        writes=[]; versions=[]
        for index,external_id,props,id in prepared:
            try:
                old=previous.get(id)
                if old and old['source_id']!=source['id']:
                    raise ValueError('Identity conflict: this identifier is owned by another connection. Align source identifiers or choose one authoritative source.')
                hashed=digest(props)
                provenance=dict(run_id=run['id'],batch_id=batch['_id'],batch_sha256=batch['checksum'],row=index+1,
                    mapping_revision=source['revision'],schema_revision=spec['revision'])
                if old and old['properties_sha256']==hashed:
                    writes.append(UpdateOne({'_id':id},{'$set':{'last_seen':now(),'latest_observation':provenance}}))
                else:
                    references=[dict(property=p['name'], type=p['reference'], external_id=props[p['name']],target=object_id(workspace,p['reference'],props[p['name']]))
                        for p in spec['properties'] if p.get('reference') and props.get(p['name'])]
                    row=dict(_id=id,id=id,workspace=workspace,type=spec['name'],external_id=external_id,source_id=source['id'],properties=props,
                        properties_sha256=hashed,revision=(old['revision']+1 if old else 1),references=references,
                        label=props.get('name') or props.get('title') or external_id,updated=now(),last_seen=now(),provenance=provenance,latest_observation=provenance)
                    writes.append(ReplaceOne({'_id':id},row,upsert=True))
                    versions.append({**row,'_id':uid(),'object_id':id})
                accepted+=1
            except (ValueError,TypeError,KeyError,OverflowError) as exc:
                failures.append((index,str(exc)[:800]))
        if writes: db.connect_objects.bulk_write(writes,ordered=True,session=session)
        if versions: db.connect_versions.insert_many(versions,session=session)
        rejected=len(failures)
        if failures:
            db.connect_quarantine.insert_many([dict(_id=uid(),workspace=workspace,source_id=source['id'],run_id=run['id'],batch_id=batch['_id'],
                row=index+1,reason=reason,created=now()) for index,reason in failures],session=session)
        complete=batch['next_cursor'] is None
        status=('complete_with_issues' if run['rejected']+rejected else 'complete') if complete else 'queued'
        values=dict(status=status,page=run['page']+1,cursor=batch['next_cursor'],updated=now())
        if complete: values['finished']=now()
        result=db.connect_runs.update_one({'_id':run['id'],'worker':worker,'page':run['page']},
            {'$set':values,'$inc':{'processed':len(batch['records']),'accepted':accepted,'rejected':rejected}},session=session)
        if not result.matched_count: raise RuntimeError('Sync checkpoint ownership changed.')
        if complete:
            db.connect_sources.update_one({'_id':source['id']},{'$set':{'last_sync':now(),'last_status':status,'last_run_id':run['id']}},session=session)
            from ..data_platform.service import source_changed
            source_changed(db,session,workspace,source['id'])
            audit('connection_sync_completed',workspace,run['actor'],{'run_id':run['id'],'accepted':run['accepted']+accepted,'rejected':run['rejected']+rejected},session=session)

def process_one(workspace=None):
    db=database(); worker=uid(); time=now()
    run=db.connect_runs.find_one_and_update({**({'workspace':workspace} if workspace else {}),'$or':[{'status':'queued'},{'status':'running','expires_at':{'$lt':time}}]},
        {'$set':{'status':'running','worker':worker,'expires_at':time+timedelta(minutes=3)},'$inc':{'attempts':1}},sort=[('created',1)],return_document=True)
    if not run: return False
    try:
        current=db.connect_sources.find_one({'_id':run['source_id'],'workspace':run['workspace']})
        if not current or not current['enabled']: raise ValueError('Connection is paused. Resume it before retrying.')
        # Only one run for a source may publish at a time; later jobs wait for earlier ones.
        earlier=db.connect_runs.find_one({'source_id':run['source_id'],'$or':[{'created':{'$lt':run['created']}},{'created':run['created'],'_id':{'$lt':run['id']}}],'status':{'$in':['queued','running']}})
        if earlier:
            db.connect_runs.update_one({'_id':run['id'],'worker':worker},{'$set':{'status':'queued'}}); return False
        if run['page']>=10000: raise ValueError('Sync reached 10,000 pages. Partition this source with a fixed binding filter.')
        batch=db.connect_batches.find_one({'run_id':run['id'],'page':run['page']})
        if not batch:
            rows,cursor=fetch_page(run['source'],run['cursor'])
            if not isinstance(rows,list) or len(rows)>500 or any(not isinstance(r,dict) for r in rows): raise ValueError('Source pages must contain at most 500 JSON objects.')
            if cursor is not None and (not rows or cursor==run['cursor']): raise ValueError('Source pagination did not advance.')
            with connection() as c:
                c.begin()
                owned=db.connect_runs.update_one({'_id':run['id'],'worker':worker,'status':'running','expires_at':{'$gt':now()}}, {'$set':{'updated':now()}},session=c.session)
                if not owned.matched_count: raise RuntimeError('Sync worker lease was superseded.')
                batch=save_batch(db,c.session,run,rows,cursor)
        materialize(run,batch,worker)
    except Exception as exc:
        message=str(exc)[:800] if isinstance(exc,ValueError) else f'{type(exc).__name__}: sync failed. Check connector access and retry; completed page checkpoints are retained.'
        db.connect_runs.update_one({'_id':run['id'],'worker':worker,'status':'running'}, {'$set':{'status':'failed','error':message,'finished':now()}})
    return True

def schedule_due():
    db=database(); time=now()
    # Native sales are refreshed from the committed ledger. The local-day key
    # includes yesterday's close even if there is no new trading event today.
    from zoneinfo import ZoneInfo
    for source in db.connect_sources.find({'enabled':True,'kind':'onesales','object_type':'SalesObservation'}).limit(100):
        shop=required_shop(source['workspace'])
        day=time.astimezone(ZoneInfo(shop['timezone'])).date().isoformat()
        change=source.get('native_change','initial')+'-'+day
        if source.get('native_scheduled')==change or db.connect_runs.find_one({'source_id':source['id'],'status':{'$in':['queued','running']}}): continue
        queue(source,'ledger-'+digest(change)[:40],'onesales-outbox')
        db.connect_sources.update_one({'_id':source['id']},{'$set':{'native_scheduled':change}})
    for source in db.connect_sources.find({'enabled':True,'schedule_minutes':{'$gte':15}}).limit(100):
        if db.connect_runs.find_one({'source_id':source['id'],'status':{'$in':['queued','running']}}): continue
        last=source.get('last_scheduled') or source['created']
        if last+timedelta(minutes=source['schedule_minutes'])>time: continue
        bucket=int(time.timestamp())//(source['schedule_minutes']*60)
        queue(source,'scheduled-'+str(bucket),'scheduler')
        db.connect_sources.update_one({'_id':source['id']},{'$set':{'last_scheduled':time}})

def worker(stop):
    delay=5
    while not stop.is_set():
        try:
            schedule_due()
            worked=process_one();delay=5
            if worked: continue
        except Exception:
            # Never log connector URLs, request payloads, or credentials.
            import logging
            logging.getLogger(__name__).warning('Connected-data worker cycle failed; retrying.')
            delay=min(60,delay*2)
        stop.wait(delay)

def start_worker():
    stop=threading.Event()
    if os.environ.get('CONNECTED_WORKER','inline')=='inline': threading.Thread(target=worker,args=(stop,),daemon=True,name='connected-data').start()
    return stop

if __name__=='__main__':
    from ..pg import initialize
    from .repository import initialize as initialize_connected
    initialize();initialize_connected();worker(threading.Event())
