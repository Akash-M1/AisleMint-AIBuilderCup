"""Authenticated, idempotent JSON ingestion for ERP/commerce connectors (no file uploads)."""
import hashlib,hmac,json,os,secrets
from datetime import datetime,timezone
from fastapi import APIRouter,Header,HTTPException,Depends
from pydantic import BaseModel,ConfigDict,Field
import pandas as pd
from .pg import database
from .storage import AssetPath,write_frame
from .data import validate_panel
from .demand import validate_demand_panel

from .pg import MongoRoute
router=APIRouter(route_class=MongoRoute,prefix='/api/integrations',tags=['Business data integrations'])
def authorize_integration(authorization:str=Header(default='')):
    expected=os.environ.get('INTEGRATION_API_KEY','')
    if not expected:raise HTTPException(503,'Configure the integration API credential before connecting an external system.')
    if not hmac.compare_digest(authorization.removeprefix('Bearer '),expected):raise HTTPException(401,'Invalid integration credential.')

class SnapshotInput(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    name:str=Field(min_length=2,max_length=120)
    kind:str=Field(pattern='^(promotion|demand)$')
    idempotency_key:str=Field(min_length=8,max_length=128,pattern=r'^[A-Za-z0-9_-]+$')
    records:list[dict]=Field(min_length=1,max_length=100000)

@router.post('/snapshots',dependencies=[Depends(authorize_integration)])
def snapshot(req:SnapshotInput):
    db=database();digest=hashlib.sha256(req.model_dump_json().encode()).hexdigest();key=req.idempotency_key
    previous=db.integration_requests.find_one({'_id':key})
    if previous:
        if previous['hash']!=digest:raise HTTPException(409,'This ingestion key already identifies a different snapshot.')
        if previous.get('status')=='ready':return previous['response']
    frame=pd.DataFrame(req.records);frame,quality=(validate_panel if req.kind=='promotion' else validate_demand_panel)(frame)
    source=(('demand_' if req.kind=='demand' else '')+hashlib.sha256(key.encode()).hexdigest()[:32])
    if not previous:
        try:db.integration_requests.insert_one({'_id':key,'hash':digest,'status':'publishing','source_id':source,'created':datetime.now(timezone.utc)})
        except Exception as exc:
            if getattr(exc, 'sqlstate', '') == '23505' or 'duplicate' in str(exc).lower(): raise HTTPException(409,'This snapshot is already being ingested. Retry with the same key.')
            raise
    from . import api
    path=api.DATA/('demand' if req.kind=='demand' else 'uploads')/source
    quality.update(source=req.name,scope='Authenticated business integration')
    write_frame(frame,path/'panel.parquet',index=False);(path/'quality.json').write_text(json.dumps(quality))
    table='demand_datasets' if req.kind=='demand' else 'datasets'
    record={'id':source,'name':req.name,'quality':quality,'created':datetime.now(timezone.utc).isoformat()}
    if req.kind=='promotion':record.update(status='validated',error=None)
    db[table].update_one({'id':source},{'$setOnInsert':record},upsert=True)
    response={'id':source,'quality':quality,'status':'validated'}
    db.integration_requests.update_one({'_id':key,'hash':digest},{'$set':{'status':'ready','response':response}})
    return response

@router.get('/status',dependencies=[Depends(authorize_integration)])
def status():
    db=database();db.command('ping')
    return {'storage':'mongodb','assets':'gridfs','file_uploads':False,'snapshot_endpoint':'/api/integrations/snapshots','max_records_per_snapshot':100000,'ledger_source':'OneSales closed trading days'}
