"""Chunked idempotent ingestion; sealing prevents batches changing underneath validation."""
import json
from fastapi import HTTPException
from ..pg import database,connection
from ..storage import AssetPath,write_frame
from .repository import uid,now,required_shop,audit
from .contracts import normalize,readiness,checksum,VERSION,MAX_ROWS

def create(workspace,name,kind,frequency,key,actor):
    db=database();required_shop(workspace)
    identity=checksum(dict(name=name,kind=kind,frequency=frequency,version=VERSION))
    row=db.retail_snapshots.find_one({'workspace':workspace,'idempotency_key':key})
    if row:
        if row['request_hash']!=identity:raise HTTPException(409,'This request key already identifies a different snapshot.')
        return row
    row=dict(_id=uid(),id=None,workspace=workspace,name=name,kind=kind,frequency=frequency,contract_version=VERSION,
        idempotency_key=key,request_hash=identity,status='receiving',rows=0,created=now(),created_by=actor)
    row['id']=row['_id'];db.retail_snapshots.insert_one(row)
    audit('snapshot_created',workspace,actor,{'snapshot_id':row['id']})
    return row

def get(workspace,id):
    row=database().retail_snapshots.find_one({'_id':id,'workspace':workspace})
    if not row:raise HTTPException(404,'Snapshot not found in this shop.')
    return row

def append(workspace,id,key,records):
    shop=required_shop(workspace);normalized,errors=normalize(records,shop)
    if errors:raise HTTPException(422,{'message':'Invalid sales observations. No rows in this batch were accepted.','errors':errors})
    digest=checksum(normalized)
    with connection() as c:
        c.begin();db=c.db;s=c.session
        snapshot=db.retail_snapshots.find_one({'_id':id,'workspace':workspace},session=s)
        if not snapshot:raise HTTPException(404,'Snapshot not found.')
        old=db.retail_batches.find_one({'snapshot_id':id,'batch_key':key},session=s)
        if old:
            if old['checksum']!=digest:raise HTTPException(409,'Batch key already contains different records.')
            return {'accepted':old['count'],'replayed':True}
        if snapshot['status']!='receiving':raise HTTPException(409,'Snapshot is sealed. Create a new version for corrections.')
        if snapshot['rows']+len(normalized)>MAX_ROWS:raise HTTPException(422,f'This worker supports at most {MAX_ROWS:,} rows per shop snapshot.')
        # This shared write serializes append and finalize, including simultaneous distinct batches.
        db.retail_snapshots.update_one({'_id':id,'status':'receiving'},{'$inc':{'rows':len(normalized)}},session=s)
        db.retail_batches.insert_one(dict(snapshot_id=id,batch_key=key,checksum=digest,count=len(normalized),records=normalized),session=s)
    return {'accepted':len(normalized),'replayed':False}

def finalize(workspace,id,actor):
    db=database();row=get(workspace,id)
    if row['status'] in ('ready','needs_data'):return row
    # Retry a crashed finalize from the same sealed batches. Content-derived paths make retries identical.
    db.retail_snapshots.update_one({'_id':id,'workspace':workspace,'status':'receiving'},{'$set':{'status':'validating'}})
    row=get(workspace,id)
    if row['status']!='validating':raise HTTPException(409,'Snapshot cannot be finalized.')
    records=[r for batch in db.retail_batches.find({'snapshot_id':id}).sort('batch_key',1) for r in batch['records']]
    records.sort(key=lambda r:(r['store_id'],r['product_id'],r['date']))
    digest=checksum(records);frame,report=readiness(records,row['kind'],row['frequency'])
    result=dict(status='ready' if report['ready'] else 'needs_data',report=report,content_sha256=digest,sealed=now())
    if report['ready']:
        path=AssetPath(f'retail/{workspace}/snapshots/{id}/{digest}')
        write_frame(frame,path/'panel.parquet',index=False)
        quality={**report['quality'],'source':row['name'],'scope':'Retailer observed history','workspace':workspace,'snapshot_id':id}
        (path/'quality.json').write_text(json.dumps(quality))
        asset=db.assets.find_one({'key':(path/'panel.parquet').key})
        result.update(path=path.key,panel_sha256=asset['sha256'])
    db.retail_snapshots.update_one({'_id':id,'status':'validating'},{'$set':result})
    audit('snapshot_validated',workspace,actor,{'snapshot_id':id,'ready':report['ready'],'checksum':digest})
    return get(workspace,id)
