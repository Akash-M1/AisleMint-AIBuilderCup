"""Shop-scoped connected-data API. Read/write access is enforced on every route."""
from ..brand import setting
from datetime import datetime
import json
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from pydantic import Field
from ..pg import database, connection, MongoRoute
from ..retail import auth, datasets
from ..retail.contracts import MAX_ROWS, checksum
from ..retail.repository import public, uid, now, audit
from . import repository as repo, pipeline, connectors, insights
from .schema import Input, ObjectType, SourceInput, map_record

router=APIRouter(prefix='/api/retail/shops/{workspace}/connected',route_class=MongoRoute,tags=['Connected intelligence'])
class Batch(Input):
    request_key:str=Field(pattern=r'^[A-Za-z0-9_-]{8,128}$')
    records:list[dict]=Field(min_length=1,max_length=500)
class Sync(Input): request_key:str=Field(pattern=r'^[A-Za-z0-9_-]{8,128}$')
class Preview(Input): records:list[dict]=Field(min_length=1,max_length=20)
class Decision(Input):
    object_id:str=Field(pattern=r'^[a-f0-9]{64}$')
    object_revision:int=Field(ge=1)
    title:str=Field(min_length=4,max_length=160)
    rationale:str=Field(min_length=8,max_length=2000)
    kind:Literal['investigate','replenishment_review','supplier_followup']='investigate'
    request_key:str=Field(pattern=r'^[A-Za-z0-9_-]{8,128}$')
class Transition(Input):
    revision:int=Field(ge=1)
    status:Literal['approved','rejected','completed']
    note:str=Field(min_length=8,max_length=2000)
class ExportSales(Sync):
    kind:Literal['promotion','demand']
    frequency:Literal['daily','weekly']
class Ask(Input):
    question:str=Field(min_length=3,max_length=1200)
    use_gemini:bool=False

def run_public(row):
    return {k:v for k,v in public(row).items() if k not in ('source','schema','worker','request_hash')}

@router.get('/overview')
def overview(workspace:str):
    shop=repo.scope(workspace);db=database();sources=list(db.connect_sources.find(repo.sources_filter(workspace)).sort('created',-1));ids=[s['id'] for s in sources]
    demo=None
    if shop.get('demo') and shop.get('demo_info'):
        demo=dict(shop['demo_info'])
        visible={r['_id'] for r in db.connect_objects.find({**repo.object_filter(workspace),'_id':{'$in':[s['object_id'] for s in demo.get('stories',[])]}},{'_id':1})}
        demo['stories']=[s for s in demo.get('stories',[]) if s['object_id'] in visible]
    counts=list(db.connect_objects.aggregate([{'$match':repo.object_filter(workspace)},{'$group':{'_id':'$type','count':{'$sum':1}}}]))
    return dict(sources=[public(s) for s in sources],types=repo.types(workspace),object_counts={r['_id']:r['count'] for r in counts},
        runs=[run_public(r) for r in db.connect_runs.find({'workspace':workspace,'source_id':{'$in':ids}}).sort('created',-1).limit(20)],
        decisions=[public(r) for r in db.connect_decisions.find({'workspace':workspace,'source_id':{'$in':ids}}).sort('created',-1).limit(50)],
        catalog=connectors.CATALOG,bindings=[dict(name=k,kind=v['kind']) for k,v in connectors.bindings(workspace).items()] if repo.is_owner(workspace) else [],
        demo=demo,can_manage=repo.is_owner(workspace),worker_mode=__import__('os').environ.get('CONNECTED_WORKER','inline'),
        assistant_mode='local_model_available' if setting('LOCAL_AI_MODEL') else 'evidence_search',risks=insights.risks(workspace))

@router.post('/types')
def define_type(workspace:str, req:ObjectType):
    repo.scope(workspace,owner=True);db=database()
    if any(t['name']==req.name and t.get('builtin') for t in repo.types(workspace)): raise HTTPException(409,'Built-in contracts are versioned with the application. Define a custom type to extend the ontology.')
    for p in req.properties:
        if p.reference and p.reference!=req.name: repo.object_type(workspace,p.reference)
    with connection() as c:
        c.begin();old=db.connect_types.find_one({'workspace':workspace,'name':req.name},session=c.session)
        if (old['revision'] if old else 0)!=req.revision: raise HTTPException(409,'Type changed. Refresh before saving.')
        if old:
            previous={p['name']:p for p in old['properties']};next_fields={p.name:p.model_dump() for p in req.properties}
            if any(next_fields.get(k)!=v for k,v in previous.items()) or any(p.required and p.name not in previous for p in req.properties):
                raise HTTPException(409,'Existing types allow optional additive properties only. Breaking changes require a new type and migration.')
        row=dict(_id=old['_id'] if old else uid(),workspace=workspace,**req.model_dump());row['revision']+=1;row['updated']=now()
        db.connect_types.replace_one({'workspace':workspace,'name':req.name},row,upsert=True,session=c.session)
        db.connect_type_versions.insert_one({**row,'_id':uid()},session=c.session)
        audit('ontology_type_saved',workspace,auth.actor(),{'type':req.name,'revision':row['revision']},session=c.session)
    return public(row)

def source_values(workspace,req):
    spec=repo.object_type(workspace,req.object_type);targets={p['name'] for p in spec['properties']}
    if any(m.target not in targets for m in req.mapping): raise HTTPException(422,'A mapped property is not in the target object contract.')
    if req.kind in ('shopify','woocommerce','zoho','tally') and req.object_type!='Product': raise HTTPException(422,'This vendor adapter currently supplies Product data. Use REST ingestion for other entities.')
    if req.kind=='onesales' and req.object_type not in ('Product','InventoryPosition','SalesObservation'): raise HTTPException(422,'OneSales supports Product, InventoryPosition and closed-day SalesObservation.')
    values=dict(workspace=workspace,**req.model_dump())
    if req.kind not in ('push','onesales'): connectors.binding(values)
    return values

@router.post('/sources')
def create_source(workspace:str,req:SourceInput):
    repo.scope(workspace,owner=True);values=source_values(workspace,req);id=uid()
    if req.revision!=0: raise HTTPException(422,'New connections start at revision zero.')
    row=dict(_id=id,id=id,**values,created=now());row['revision']=1
    with connection() as c:
        c.begin();c.db.connect_sources.insert_one(row,session=c.session)
        audit('connection_created',workspace,auth.actor(),{'source_id':id,'kind':req.kind},session=c.session)
    return public(row)

@router.post('/sources/{id}/configure')
def configure(workspace:str,id:str,req:SourceInput):
    repo.scope(workspace,owner=True);old=repo.source(workspace,id);values=source_values(workspace,req)
    if any(values[k]!=old[k] for k in ('kind','object_type','id_field','binding')): raise HTTPException(409,'Identity, connector and binding changes require a new connection.')
    values['revision']=req.revision+1
    with connection() as c:
        c.begin()
        current=c.db.connect_sources.find_one({'_id':id,'workspace':workspace},session=c.session)
        if req.visibility!='team' and current and current.get('shared_product'):
            raise HTTPException(409,'This source has been shared with shop-wide data products. Create a separate restricted connection for private data; published models and releases retain their shared provenance.')
        result=c.db.connect_sources.update_one({'_id':id,'workspace':workspace,'revision':req.revision},{'$set':values},session=c.session)
        if not result.matched_count: raise HTTPException(409,'Connection changed. Refresh before saving.')
        from ..data_platform.service import source_changed
        source_changed(c.db,c.session,workspace,id)
        audit('connection_configured',workspace,auth.actor(),{'source_id':id,'revision':values['revision']},session=c.session)
    return public(repo.source(workspace,id))

@router.post('/sources/{id}/preview')
def preview(workspace:str,id:str,req:Preview):
    repo.scope(workspace,owner=True);s=repo.source(workspace,id);spec=repo.object_type(workspace,s['object_type']);results=[]
    for i,record in enumerate(req.records):
        try:
            key,props=map_record(record,s,spec);results.append(dict(row=i+1,external_id=key,properties=props,valid=True))
        except (ValueError,TypeError,KeyError) as exc:results.append(dict(row=i+1,valid=False,error=str(exc)[:800]))
    return dict(results=results,mapping_revision=s['revision'],schema_revision=spec['revision'],writes=False)

@router.post('/sources/{id}/batches')
def ingest(workspace:str,id:str,req:Batch):
    repo.scope(workspace,write=True,ingestion=True);s=repo.source(workspace,id,visible=not auth.account().get('integration'))
    return run_public(pipeline.queue(s,req.request_key,auth.actor(),req.records))

@router.post('/sources/{id}/sync')
def sync(workspace:str,id:str,req:Sync):
    repo.scope(workspace,write=True);s=repo.source(workspace,id)
    return run_public(pipeline.queue(s,req.request_key,auth.actor()))

@router.get('/runs')
def runs(workspace:str,before:str|None=None,limit:int=Query(default=25,ge=1,le=100)):
    repo.scope(workspace);query={'workspace':workspace,'source_id':{'$in':repo.visible_ids(workspace)}}
    if before:
        cursor=database().connect_runs.find_one({**query,'_id':before})
        if not cursor:raise HTTPException(404,'Run page cursor is unavailable.')
        query['$or']=[{'created':{'$lt':cursor['created']}},{'created':cursor['created'],'_id':{'$lt':before}}]
    rows=list(database().connect_runs.find(query).sort([('created',-1),('_id',-1)]).limit(limit+1))
    return dict(items=[run_public(r) for r in rows[:limit]],next_cursor=rows[limit-1]['_id'] if len(rows)>limit else None)

@router.post('/runs/{id}/retry')
def retry(workspace:str,id:str):
    repo.scope(workspace,write=True);db=database();row=db.connect_runs.find_one({'_id':id,'workspace':workspace,'source_id':{'$in':repo.visible_ids(workspace)}})
    if not row: raise HTTPException(404,'Run not found.')
    if not repo.source(workspace,row['source_id'])['enabled']: raise HTTPException(409,'Resume this connection before retrying.')
    if db.connect_runs.find_one({'source_id':row['source_id'],'created':{'$gt':row['created']},'processed':{'$gt':0}}): raise HTTPException(409,'Newer data has already been processed. Start a fresh sync or submit a new corrected batch.')
    result=db.connect_runs.update_one({'_id':id,'status':'failed'},{'$set':{'status':'queued'},'$unset':{'error':'','finished':''}})
    if not result.matched_count: raise HTTPException(409,'Only failed runs can be retried.')
    audit('connection_sync_retried',workspace,auth.actor(),{'run_id':id});return {'status':'queued'}

@router.get('/quarantine')
def quarantine(workspace:str,source_id:str|None=None,before:str|None=None,limit:int=Query(default=25,ge=1,le=100)):
    repo.scope(workspace);ids=repo.visible_ids(workspace);query={'workspace':workspace,'source_id':{'$in':ids}}
    if source_id:
        repo.source(workspace,source_id);query['source_id']=source_id
    if before:
        cursor=database().connect_quarantine.find_one({**query,'_id':before})
        if not cursor:raise HTTPException(404,'Quarantine page cursor is unavailable.')
        query['$or']=[{'created':{'$lt':cursor['created']}},{'created':cursor['created'],'_id':{'$lt':before}}]
    rows=list(database().connect_quarantine.find(query).sort([('created',-1),('_id',-1)]).limit(limit+1))
    return dict(items=[{**public(r),'id':r['_id']} for r in rows[:limit]],next_cursor=rows[limit-1]['_id'] if len(rows)>limit else None)

@router.get('/runs/{id}')
def run_detail(workspace:str,id:str):
    repo.scope(workspace);db=database();row=db.connect_runs.find_one({'_id':id,'workspace':workspace,'source_id':{'$in':repo.visible_ids(workspace)}})
    if not row:raise HTTPException(404,'Run not found.')
    return dict(run=run_public(row),mapping=row['source']['mapping'],schema=row['schema'],
        batches=[{k:v for k,v in public(b).items() if k!='records'} for b in db.connect_batches.find({'run_id':id}).sort('page',1).limit(100)],
        note='Up to 100 input-page manifests; raw connector inputs require owner access.')

@router.get('/quarantine/{id}')
def rejected_record(workspace:str,id:str):
    repo.scope(workspace,owner=True);db=database();row=db.connect_quarantine.find_one({'_id':id,'workspace':workspace})
    if not row:raise HTTPException(404,'Quarantined record not found.')
    batch=db.connect_batches.find_one({'_id':row['batch_id'],'workspace':workspace})
    return dict(issue=public(row),record=batch['records'][row['row']-1],batch_sha256=batch['checksum'])

@router.get('/objects')
def objects(workspace:str,type:str|None=None,q:str=Query(default='',max_length=120),after:str|None=None,limit:int=Query(default=30,ge=1,le=100)):
    import re
    repo.scope(workspace);query=repo.object_filter(workspace)
    if type:query['type']=type
    if after:query['_id']={'$gt':after}
    if q:query['$or']=[{f:{'$regex':re.escape(q),'$options':'i'}} for f in ('label','external_id','properties.text')]
    rows=list(database().connect_objects.find(query).sort('_id',1).limit(limit+1))
    # References are hydrated only through permission-checked object queries.
    return dict(items=[{k:v for k,v in public(r).items() if k!='references'} for r in rows[:limit]],next_cursor=rows[limit-1]['_id'] if len(rows)>limit else None)

@router.get('/objects/{id}')
def object_detail(workspace:str,id:str):
    repo.scope(workspace);db=database();row=repo.get_object(workspace,id);query=repo.object_filter(workspace)
    outgoing=[r['target'] for r in row['references']]
    related=list(db.connect_objects.find({**query,'$or':[{'_id':{'$in':outgoing}},{'references.target':id}]}).sort('_id',1).limit(60))
    ids={r['id'] for r in related}|{id};edges=[]
    for r in [row,*related]:
        edges += [dict(source=r['id'],target=ref['target'],label=ref['property']) for ref in r['references'] if ref['target'] in ids]
    versions=list(db.connect_versions.find({'workspace':workspace,'object_id':id}).sort('revision',-1).limit(20))
    safe=lambda r:{k:v for k,v in public(r).items() if k!='references'}
    return dict(object=safe(row),neighbors=[safe(r) for r in related],edges=edges,versions=[safe(r) for r in versions],
        lineage=dict(source=public(repo.source(workspace,row['source_id'])),**row['provenance']),
        note='Only accessible resolved links are shown. References may be unresolved until their source arrives; graph is limited to 60 neighbors.')

@router.post('/decisions')
def propose(workspace:str,req:Decision):
    repo.scope(workspace,write=True);obj=repo.get_object(workspace,req.object_id);db=database();hashed=repo.digest(req.model_dump())
    old=db.connect_decisions.find_one({'workspace':workspace,'request_key':req.request_key})
    if old:
        if old['request_hash']!=hashed:raise HTTPException(409,'Request key already identifies another decision.')
        return public(old)
    if obj['revision']!=req.object_revision:raise HTTPException(409,'Evidence changed. Review the current object before proposing a decision.')
    id=uid();row=dict(_id=id,id=id,workspace=workspace,source_id=obj['source_id'],**req.model_dump(),request_hash=hashed,status='proposed',revision=1,
        evidence=dict(label=obj['label'],properties=obj['properties'],provenance=obj['provenance']),created=now(),created_by=auth.actor())
    with connection() as c:
        c.begin();db.connect_decisions.insert_one(row,session=c.session);audit('operational_decision_proposed',workspace,auth.actor(),{'decision_id':id},session=c.session)
    return public(row)

@router.post('/decisions/{id}/transition')
def transition(workspace:str,id:str,req:Transition):
    repo.scope(workspace,owner=True);db=database()
    with connection() as c:
        c.begin();row=db.connect_decisions.find_one({'_id':id,'workspace':workspace},session=c.session)
        if not row:raise HTTPException(404,'Decision not found.')
        allowed={'proposed':{'approved','rejected'},'approved':{'completed','rejected'}}
        if req.status not in allowed.get(row['status'],set()):raise HTTPException(409,'This decision cannot make that status transition.')
        if req.status=='approved':
            evidence=db.connect_objects.find_one({'_id':row['object_id'],'workspace':workspace,'revision':row['object_revision']},session=c.session)
            if not evidence:raise HTTPException(409,'The evidence changed. Reject this proposal and review current data.')
            # Touch the evidence to fence concurrent ingestion during approval.
            db.connect_objects.update_one({'_id':row['object_id']},{'$set':{'last_decision_review':now()}},session=c.session)
        result=db.connect_decisions.update_one({'_id':id,'revision':req.revision},{'$set':{'status':req.status,'updated':now()},'$inc':{'revision':1},
            '$push':{'history':dict(status=req.status,note=req.note,actor=auth.actor(),created=now())}},session=c.session)
        if not result.matched_count:raise HTTPException(409,'Decision changed. Refresh before saving.')
        audit('operational_decision_'+req.status,workspace,auth.actor(),{'decision_id':id,'note':req.note},session=c.session)
    return public(db.connect_decisions.find_one({'_id':id}))

@router.post('/sources/{id}/sales-snapshot')
def sales_snapshot(workspace:str,id:str,req:ExportSales):
    repo.scope(workspace,write=True);source=repo.source(workspace,id);db=database()
    if source['object_type']!='SalesObservation':raise HTTPException(422,'Only validated sales observations can create a training snapshot.')
    if db.connect_runs.find_one({'source_id':id,'status':{'$in':['queued','running']}}):raise HTTPException(409,'Wait for this source’s pending syncs to finish before freezing model history.')
    snapshot=datasets.create(workspace,source['name']+' connected history',req.kind,req.frequency,req.request_key,auth.actor())
    if snapshot['status'] in ('ready','needs_data'):return public(snapshot)
    # Freeze all rows and provenance atomically. A retry never mixes different sync versions.
    with connection() as c:
        c.begin();current=db.retail_snapshots.find_one({'_id':snapshot['id']},session=c.session)
        if current.get('connected_source_id') not in (None,id):raise HTTPException(409,'This snapshot key belongs to another connected source.')
        if not current.get('connected_source_id'):
            if current['rows'] or current['status']!='receiving':raise HTTPException(409,'This snapshot key already contains another ingestion. Use a new key.')
            query={'workspace':workspace,'source_id':id,'type':'SalesObservation'}
            count=db.connect_objects.count_documents(query,session=c.session)
            if not count or count>MAX_ROWS:raise HTTPException(422,f'The current modelling worker requires 1–{MAX_ROWS:,} observations. Connected object storage is separate from this training limit.')
            rows=list(db.connect_objects.find(query,session=c.session).sort('_id',1))
            for offset in range(0,len(rows),1000):
                chunk=rows[offset:offset+1000];records=[r['properties'] for r in chunk]
                db.retail_batches.insert_one(dict(snapshot_id=snapshot['id'],batch_key=str(offset),checksum=checksum(records),count=len(records),records=records,
                    connected_lineage=[dict(object_id=r['id'],revision=r['revision'],**r['provenance']) for r in chunk]),session=c.session)
            db.retail_snapshots.update_one({'_id':snapshot['id'],'status':'receiving'},{'$set':{'rows':count,'connected_source_id':id,
                'connected_mapping_revision':source['revision'],'connected_frozen_at':now(),'status':'validating'}},session=c.session)
    result=datasets.finalize(workspace,snapshot['id'],auth.actor())
    audit('connected_history_frozen',workspace,auth.actor(),{'source_id':id,'snapshot_id':snapshot['id']})
    return public(result)

@router.post('/ask')
def ask(workspace:str,req:Ask):
    repo.scope(workspace);return insights.answer(workspace,req.question,auth.actor(),use_gemini=req.use_gemini)
