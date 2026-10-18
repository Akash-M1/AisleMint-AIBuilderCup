from __future__ import annotations
from .storage import AssetPath,managed,read_frame,write_frame,load_model,dump_model
import os
os.environ.setdefault('OMP_NUM_THREADS','4')
os.environ.setdefault('OPENBLAS_NUM_THREADS','4')
from contextlib import asynccontextmanager, contextmanager
import csv
import io
import json
import logging
import re
import threading
import uuid
from datetime import datetime,timezone
from functools import lru_cache
from pathlib import Path
import pandas as pd
from fastapi import FastAPI,HTTPException,Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse,StreamingResponse,Response
from pydantic import BaseModel,Field,ConfigDict
from .data import DATA,ARTIFACTS,validate_panel,REQUIRED
from .engine import Engine,Scenario,OptimizeRequest
from .model import train

@asynccontextmanager
async def lifespan(app):
    from .pg import initialize
    initialize()
    from .pg import assert_migration_ready
    assert_migration_ready()
    from .retail.repository import initialize as initialize_retail
    from .retail.modelling import start_worker
    initialize_retail()
    stop_worker=start_worker()
    from .connected.pipeline import start_worker as start_connected_worker
    stop_connected_worker=start_connected_worker()
    from .data_platform.service import start_worker as start_data_worker
    stop_data_worker=start_data_worker()
    from .pos_learning import recover
    try:
        if os.environ.get('LEGACY_TRAINING_WORKER')!='external':recover()
    except Exception:
        logging.warning('Legacy training recovery is deferred until database writes are available; restart after restoring capacity.')
    try: yield
    finally:
        stop_worker.set()
        stop_connected_worker.set()
        stop_data_worker.set()

app=FastAPI(lifespan=lifespan,docs_url='/api/docs',openapi_url='/api/openapi.json',redoc_url='/api/redoc',title='AisleMint Retail Intelligence',version='0.3.0',description='Retailer-scoped promotion, demand, inventory and AisleMint OneSales platform with a versioned modelling workbench.')
from .pg import PgRoute,MongoRoute
app.router.route_class=PgRoute
LOCAL_ORIGINS=['http://localhost:3000','http://127.0.0.1:3000','http://localhost:8000','http://127.0.0.1:8000']
PUBLIC_ORIGIN=os.environ.get('PUBLIC_ORIGIN','').strip().rstrip('/')
ORIGINS=[*LOCAL_ORIGINS,*([PUBLIC_ORIGIN] if PUBLIC_ORIGIN else []),
         *[origin.strip().rstrip('/') for origin in os.environ.get('PUBLIC_ORIGINS','').split(',') if origin.strip()]]
TRUSTED_HOSTS=[host.strip() for host in os.environ.get('TRUSTED_HOSTS','localhost,127.0.0.1,testserver').split(',') if host.strip()]
app.add_middleware(CORSMiddleware,allow_origins=ORIGINS,allow_credentials=True,allow_methods=['GET','POST'],allow_headers=['Content-Type','X-Workspace-ID','Authorization'])
app.add_middleware(TrustedHostMiddleware,allowed_hosts=TRUSTED_HOSTS)

@app.middleware('http')
async def origin_guard(request,call_next):
    if request.method not in ['GET','HEAD','OPTIONS'] and request.headers.get('origin') and request.headers['origin'] not in ORIGINS:
        return JSONResponse(status_code=403,content={'detail':'Origin is not allowed.'})
    from .retail import auth
    from starlette.concurrency import run_in_threadpool
    token=None;entitlement_token=None
    from .retail import permissions
    try:
        if os.environ.get('AISLEMINT_HACKATHON')=='1':
            from .hackathon import guard
            await guard(request)
        public=request.url.path in ('/api/health','/api/retail/auth/status','/api/retail/auth/login','/api/retail/provisioning/orders','/api/retail/provisioning/status','/api/retail/provisioning/recovery/request','/api/retail/provisioning/recovery/complete') or (os.environ.get('AISLEMINT_HACKATHON')=='1' and request.url.path in ('/api/demo/info','/api/demo/session')) or request.method=='OPTIONS'
        if not public:
            p=await run_in_threadpool(auth.authenticate,request)
            token=auth.principal.set(p)
            entitlement_token=permissions.entitlement_cache.set({})
            if request.method not in ('GET','HEAD','OPTIONS') and request.cookies.get(auth.COOKIE) and not request.headers.get('origin'):
                raise HTTPException(403,'Browser mutations require an allowed Origin header.')
            await run_in_threadpool(auth.check_scope,request,p)
            from .retail import permissions
            await run_in_threadpool(permissions.check_request,request,p)
            if request.url.path in ('/api/demand/train','/api/demand/forecast','/api/demand/forecast-export','/api/demand/inventory','/api/demand/plans') and request.method=='POST':
                body=await request.json()
                if not isinstance(body,dict) or ('forecast' in body and not isinstance(body['forecast'],dict)):raise HTTPException(422,'Supply a JSON object with a forecast source.')
                source=body.get('source_id',body.get('forecast',{}).get('source_id','benchmark'))
                if not isinstance(source,str):raise HTTPException(422,'source_id must be a string.')
                await run_in_threadpool(auth.require_workspace,source,True)
                level='editor' if request.url.path in ('/api/demand/train','/api/demand/plans') else 'user'
                await run_in_threadpool(permissions.workspace,source,'demand',level)
        response=await call_next(request)
    except json.JSONDecodeError:
        response=JSONResponse(status_code=422,content={'detail':'Supply valid JSON.'})
    except Exception as exc:
        # Check for PostgreSQL database errors
        sqlstate=getattr(exc,'sqlstate',None)
        if sqlstate:
            response=JSONResponse(status_code=503,content={'detail':storage_message(exc,'Database temporarily unavailable. Retry shortly.')})
        elif isinstance(exc, HTTPException):
            response=JSONResponse(status_code=exc.status_code,content={'detail':exc.detail})
        else:
            raise
    finally:
        if token is not None:auth.principal.reset(token)
        if entitlement_token is not None:permissions.entitlement_cache.reset(entitlement_token)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Cache-Control']='no-store'
    return response

@app.exception_handler(ValueError)
async def invalid_request(request,exc):return JSONResponse(status_code=422,content={'detail':str(exc)})


from .pg import connection,check_connection

def storage_message(exc,fallback):
    return fallback

from psycopg import Error as PostgreSQLError
from psycopg_pool import PoolTimeout

@app.exception_handler(PostgreSQLError)
@app.exception_handler(PoolTimeout)
async def database_failure(request,exc):
    sqlstate=getattr(exc,'sqlstate',None)
    # 23505 = unique_violation, 40001 = serialization_failure, 40P01 = deadlock
    conflict=sqlstate in ('23505','40001','40P01')
    return JSONResponse(status_code=409 if conflict else 503,content={'detail':'A concurrent change occurred. Retry the same request.' if conflict else 'Database temporarily unavailable. Your request may have committed; retry using the same idempotency key.'},headers={'Retry-After':'1'})

def stamp():return datetime.now(timezone.utc).isoformat()
def audit(event,workspace,payload):
    with connection() as c:c.run('api_audit_insert_audit', (event, workspace, json.dumps(payload), stamp()))

@app.get('/api/health')
def health():
    check_connection()
    from .cache import health as cache_health
    return dict(status='ok',model_ready=(ARTIFACTS/'model.joblib').exists(),storage='postgresql',assets='bytea',mode='retailer-isolated',local_fallback=False,cache=cache_health())

# Stable composition facade for existing jobs and model consumers.
from .promotion_api import router as forecasting_router,workspace_path,get_engine,legacy_engine
from .legacy_data_api import router as legacy_data_router,training_lock,train_job
app.include_router(forecasting_router)
app.include_router(legacy_data_router)

from .demand_api import router as demand_router
app.include_router(demand_router)
from .promotions import router as promotion_router,review_token,calendar_snapshot
app.include_router(promotion_router)
from .offer_api import router as offer_router
app.include_router(offer_router)
from .pos_api import router as pos_router
app.include_router(pos_router)
from .pos_reports import router as pos_reports_router
app.include_router(pos_reports_router)
from .pos_learning import router as pos_learning_router
app.include_router(pos_learning_router)

from .ingestion import router as ingestion_router
app.include_router(ingestion_router)

from .retail.api import router as retail_router
app.include_router(retail_router)
from .retail.iam import router as iam_router
app.include_router(iam_router)
from .connected.api import router as connected_router
app.include_router(connected_router)
from .data_platform.api import router as data_platform_router
app.include_router(data_platform_router)

from .retail.limits import RequestLimit
app.add_middleware(RequestLimit)

from .retail.modelling_api import router as modelling_router
from .retail.sales_api import router as sales_onboarding_router
from .retail.inventory_api import router as inventory_position_router
for router in (modelling_router,sales_onboarding_router,inventory_position_router):
    app.include_router(router)
