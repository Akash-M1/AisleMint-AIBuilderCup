from hashlib import sha256
import json
from fastapi import HTTPException
from ..pg import database
from ..retail import auth
from ..retail.repository import required_shop, now, public
from .schema import builtin_types
from . import COLLECTIONS

def digest(value): return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False, default=str).encode()).hexdigest()
def object_id(workspace, kind, external_id): return digest([workspace, kind, external_id])

def initialize():
    pass

def scope(workspace, write=False, owner=False, ingestion=False):
    shop = required_shop(workspace)
    auth.require_retailer(shop['retailer_id'], write=write, owner=owner)
    if auth.account().get('integration') and not ingestion: raise HTTPException(403, 'Integration keys can only submit connected data batches.')
    return shop

def is_owner(workspace):
    shop = required_shop(workspace); p = auth.account()
    return bool(p.get('admin') or (not p.get('integration') and p.get('memberships', {}).get(shop['retailer_id']) == 'owner'))

def sources_filter(workspace):
    return {'workspace': workspace, **({} if is_owner(workspace) else {'visibility': 'team'})}

def visible_ids(workspace): return [r['_id'] for r in database().connect_sources.find(sources_filter(workspace), {'_id': 1})]
def object_filter(workspace): return {'workspace': workspace, 'source_id': {'$in': visible_ids(workspace)}}

def source(workspace, id, visible=True):
    query = sources_filter(workspace) if visible else {'workspace': workspace}
    row = database().connect_sources.find_one({**query, '_id': id})
    if not row: raise HTTPException(404, 'Connection not found in this shop.')
    return row

def types(workspace): return builtin_types() + [public(r) for r in database().connect_types.find({'workspace': workspace}).sort('name', 1)]
def object_type(workspace, name):
    spec = next((r for r in types(workspace) if r['name'] == name), None)
    if not spec: raise HTTPException(422, 'Unknown object type. Define its contract in Ontology first.')
    return spec

def get_object(workspace, id):
    row = database().connect_objects.find_one({**object_filter(workspace), '_id': id})
    if not row: raise HTTPException(404, 'Object not found in your accessible sources.')
    return row
