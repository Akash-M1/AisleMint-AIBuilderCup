from datetime import datetime, timezone
import uuid
from fastapi import HTTPException
from ..pg import database

COLLECTIONS = ['retailers', 'retail_shops', 'retail_users', 'retail_sessions', 'retail_keys',
               'retail_snapshots', 'retail_batches', 'retail_runs', 'retail_ledger_jobs', 'retail_audit', 'retail_login_attempts']
from ..connected import COLLECTIONS as CONNECTED_COLLECTIONS
from ..data_platform import COLLECTIONS as DATA_COLLECTIONS
COLLECTIONS = COLLECTIONS + CONNECTED_COLLECTIONS + DATA_COLLECTIONS
def now(): return datetime.now(timezone.utc)
def uid(): return uuid.uuid4().hex
def public(row): return {k:v for k,v in row.items() if k != '_id'} if row else None

def initialize():
    from ..connected.repository import initialize as initialize_connected
    initialize_connected()
    from ..data_platform.service import initialize as initialize_data
    initialize_data()

def shop(workspace): return database().retail_shops.find_one({'_id':workspace})
def required_shop(workspace):
    row=shop(workspace)
    if not row: raise HTTPException(404,'Shop workspace not found.')
    return row

def audit(event, workspace, actor, details=None, session=None):
    database().retail_audit.insert_one(dict(id=uid(),event=event,workspace=workspace,
        actor=actor,details=details or {},created=now()),session=session)
