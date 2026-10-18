"""MongoDB repository and transaction boundary. No local persistence or SQL execution.

Checked-in native query plans keep financial projections explicit and parameterized.
A connection is a Mongo session/transaction: related ledger changes commit together.
"""
from __future__ import annotations
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
import hashlib,json,os,re,time,threading
import certifi
from datetime import datetime,timezone
from dotenv import load_dotenv
from pymongo import MongoClient,ReturnDocument,ASCENDING
from pymongo.errors import PyMongoError,DuplicateKeyError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern
from pymongo.read_preferences import ReadPreference

from .runtime import ROOT, ENV_FILE
load_dotenv(ENV_FILE,override=False)
SCHEMA=json.loads((Path(__file__).parent/'mongo_schema.json').read_text())
PLANS=json.loads((Path(__file__).parent/'mongo_queries.json').read_text())
JSON_FIELDS={'request','result','quality','snapshot','payload','body'}
_lock=threading.Lock();_initialized=set()

@lru_cache(maxsize=1)
def client():
    uri=os.environ.get('MONGODB_URI','')
    if not uri:raise RuntimeError('MongoDB is not configured. Set MONGODB_URI and MONGODB_DATABASE. Local storage fallback is disabled.')
    if not uri.startswith(('mongodb://','mongodb+srv://')):raise RuntimeError('MONGODB_URI must be a MongoDB connection string.')
    tls_options={'tlsCAFile':os.environ.get('MONGODB_TLS_CA_FILE') or certifi.where()} if uri.startswith('mongodb+srv://') else {}
    return MongoClient(uri,**tls_options,appname='AisleMint',serverSelectionTimeoutMS=8000,connectTimeoutMS=8000,socketTimeoutMS=120000,maxPoolSize=int(os.environ.get('MONGODB_POOL_SIZE','40')),retryWrites=True,retryReads=True,tz_aware=True)

def database():
    name=os.environ.get('MONGODB_DATABASE','')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,63}',name):raise RuntimeError('Set MONGODB_DATABASE to a valid, dedicated application database name.')
    return client().get_database(name,write_concern=WriteConcern('majority',wtimeout=15000),read_concern=ReadConcern('majority'))

def check_connection():
    db=database();hello=client().admin.command('hello')
    if not hello.get('setName') and hello.get('msg')!='isdbgrid':raise RuntimeError('A MongoDB replica set or Atlas cluster is required for atomic sales, returns and stock changes. Standalone MongoDB is not supported.')
    db.command('ping');return db

def primary_keys(name):
    if name=='pos_events':return ['store_id','sequence']
    if name=='audit':return ['workspace','id']
    return [f['name'] for f in sorted(SCHEMA[name]['fields'],key=lambda f:f['pk']) if f['pk']]

def defaults(name):
    result={}
    for f in SCHEMA[name]['fields']:
        v=f['default']
        if v is None:result[f['name']]=None
        elif v.startswith("'"):result[f['name']]=v[1:-1]
        else:result[f['name']]=int(v)
    return result

def encode_values(values):
    return {k:json.loads(v) if k in JSON_FIELDS and isinstance(v,str) else v for k,v in values.items()}

def decode_row(row,fields=None):
    keys=fields or [k for k in row if k not in ('_id','_sequence')]
    return Row({k:json.dumps(row[k],separators=(',',':')) if k in JSON_FIELDS and isinstance(row.get(k),(dict,list)) else row.get(k) for k in keys})

class Row(dict):
    def __getitem__(self,key):return super().__getitem__(list(self)[key] if isinstance(key,int) else key)
class Result:
    def __init__(self,rows=(),rowcount=0):self.rows=list(rows);self.rowcount=rowcount;self.index=0
    def fetchone(self):
        if self.index>=len(self.rows):return None
        row=self.rows[self.index];self.index+=1;return row
    def fetchall(self):result=self.rows[self.index:];self.index=len(self.rows);return result
    def __iter__(self):return iter(self.fetchall())

def bind(value,params):
    if isinstance(value,dict):
        if set(value)=={'$parameter'}:return params[value['$parameter']]
        if set(value)=={'$likeParameter'}:
            text=str(params[value['$likeParameter']]);return '^'+''.join('.*' if c=='%' else '.' if c=='_' else re.escape(c) for c in text)+'$'
        return {k:bind(v,params) for k,v in value.items()}
    if isinstance(value,list):return [bind(v,params) for v in value]
    return value

class IndexSetup:
    """Validate existing indexes before issuing any DDL; never weaken a constraint."""
    def __init__(self,db):self.db=db;self.known={}
    def ensure(self,collection_name,keys,**options):
        collection=self.db[collection_name]
        if collection_name not in self.known:self.known[collection_name]=collection.index_information()
        requested=[(keys,1)] if isinstance(keys,str) else list(keys)
        expected={k:options.get(k,False if k in ('unique','sparse') else None) for k in ('unique','sparse','partialFilterExpression','expireAfterSeconds','collation')}
        for existing_name,index in self.known[collection_name].items():
            actual={k:index.get(k,False if k in ('unique','sparse') else None) for k in expected}
            if list(index['key'])==requested and actual==expected and ('name' not in options or options['name']==existing_name):
                return existing_name
        result=collection.create_index(keys,**options)
        self.known[collection_name]=collection.index_information()
        return result

def initialize():
    db=database();key=(id(db.client),db.name)
    with _lock:
        if key in _initialized:return
        check_connection()
        collections={c['name']:c.get('options',{}) for c in db.list_collections()}
        indexes=IndexSetup(db)
        for name,spec in SCHEMA.items():
            props={};required=[]
            for f in spec['fields']:
                keyname=f['name'];types=['int','long'] if f['type']=='INTEGER' else ['int','long','double'] if f['type']=='REAL' else ['string']
                if keyname in JSON_FIELDS:types=['object','array']
                if not f['notnull']:types+=['null']
                props[keyname]={'bsonType':types}
                if f['notnull']:required.append(keyname)
                if name=='pos_products' and keyname in ('on_hand','price_paise','cost_paise','gst_bps'):props[keyname]['minimum']=1 if keyname=='price_paise' else 0
            # Index migrations are explicit and idempotent. Validators reject malformed writes.
            validator={'$jsonSchema':{'bsonType':'object','required':required,'properties':props}}
            if name not in collections:db.create_collection(name,validator=validator)
            elif collections[name].get('validator')!=validator or collections[name].get('validationLevel','strict')!='strict' or collections[name].get('validationAction','error')!='error':
                db.command({'collMod':name,'validator':validator,'validationLevel':'strict','validationAction':'error'})
            pk=primary_keys(name)
            if pk:indexes.ensure(name,[(x,1) for x in pk],unique=True,name='primary_identity')
            for index in spec['indexes']:
                fields=index['fields']
                if fields==pk or not fields:continue
                options={'name':'key_'+'_'.join(fields),'unique':index['unique']}
                if name=='pos_events' and fields==['sequence']:continue
                if name=='audit' and fields==['id']:continue
                if index['name']=='idx_pos_open_register':options['partialFilterExpression']={'closed':None}
                if index['name']=='idx_pos_pending_training':options['partialFilterExpression']={'status':{'$in':['queued','training']}}
                if index['unique'] and 'idempotency_key' in fields:options['partialFilterExpression']={'idempotency_key':{'$type':'string'}}
                indexes.ensure(name,[(x,1) for x in fields],**options)
        indexes.ensure('operation_recovery',[('store_id',1),('staff_id',1)],unique=True)
        indexes.ensure('assets','key',unique=True)
        indexes.ensure('asset_versions',[('key',1),('created',-1)])
        indexes.ensure('migration_runs','source_id',unique=True)
        indexes.ensure('leases','expires_at',expireAfterSeconds=0)
        indexes.ensure('pos_inventory_movements',[('store_id',1),('product_id',1),('business_date',1),('created',-1),('_sequence',-1)])
        indexes.ensure('pos_sale_lines',[('store_id',1),('product_id',1)])
        indexes.ensure('pos_return_lines',[('store_id',1),('product_id',1)])
        _initialized.add((id(db.client),db.name))

class UnitOfWork:
    def __init__(self,db,session):self.db=db;self.session=session
    def begin(self):
        if not self.session.in_transaction:self.session.start_transaction(read_concern=ReadConcern('snapshot'),write_concern=WriteConcern('majority',wtimeout=15000),read_preference=ReadPreference.PRIMARY,max_commit_time_ms=15000)
        return Result()
    def aggregate(self,collection,pipeline,fields=None,default=None):
        self.begin()
        rows=list(self.db[collection].aggregate(pipeline,session=self.session,maxTimeMS=30000))
        if not rows and default is not None:rows=[default]
        return Result([decode_row(r,fields) for r in rows])
    def next_sequence(self,collection,scope='global'):
        return self.db.sequences.find_one_and_update({'_id':collection+':'+str(scope)},{'$inc':{'value':1}},upsert=True,return_document=ReturnDocument.AFTER,session=self.session)['value']
    def insert(self,name,values,replace=False,keys=None,set_values=None,increments=None):
        self.begin();values=encode_values(values);record={**defaults(name),**values}
        if name in ('audit','pos_events'):
            field='id' if name=='audit' else 'sequence'
            if record[field] is None:record[field]=self.next_sequence(name,record.get('store_id',record.get('workspace','global')))
        if name=='pos_inventory_movements':record['_sequence']=self.next_sequence(name,record['store_id'])
        coll=self.db[name]
        if set_values is not None or increments is not None:
            keys=keys or primary_keys(name);query={k:values[k] for k in keys};updates=encode_values(set_values or {});inc=increments or {}
            # An increment-upsert starts from zero, with the same final first value.
            initial={k:v for k,v in record.items() if k not in updates and k not in inc}
            update={'$setOnInsert':initial}
            if updates:update['$set']=updates
            if inc:update['$inc']=inc
            r=coll.update_one(query,update,upsert=True,session=self.session)
        elif replace:
            query={k:values[k] for k in primary_keys(name)};r=coll.replace_one(query,record,upsert=True,session=self.session)
        else:r=coll.insert_one(record,session=self.session)
        return Result(rowcount=1)
    def run(self,name,params=()):
        spec=bind(PLANS[name],params);collection=spec['collection'];operation=spec['operation']
        if operation=='aggregate':return self.aggregate(collection,spec['pipeline'],spec['fields'],spec.get('default'))
        self.begin()
        if operation=='insert':return self.insert(collection,spec['values'],spec.get('replace',False))
        if operation=='upsert':return self.insert(collection,spec['values'],keys=spec['keys'],set_values=spec['set'],increments=spec['inc'])
        if operation=='update':
            changes={}
            if spec['set']:changes['$set']=encode_values(spec['set'])
            if spec['inc']:changes['$inc']=spec['inc']
            r=self.db[collection].update_many(spec['filter'],changes,session=self.session);return Result(rowcount=r.matched_count)
        if operation=='delete':return Result(rowcount=self.db[collection].delete_many(spec['filter'],session=self.session).deleted_count)
        raise RuntimeError('Unknown repository operation')
    def run_many(self,name,rows):
        for row in rows:self.run(name,row)
        return Result()

@contextmanager
def connection():
    initialize();db=database()
    with db.client.start_session() as session:
        con=UnitOfWork(db,session)
        try:
            yield con
            if session.in_transaction:
                deadline=time.monotonic()+30
                while True:
                    try:session.commit_transaction();break
                    except PyMongoError as exc:
                        if exc.has_error_label('UnknownTransactionCommitResult') and time.monotonic()<deadline:continue
                        raise
        except BaseException:
            if session.in_transaction:session.abort_transaction()
            raise


def retry_transaction(function):
    """Retry the entire business operation only after a confirmed transient abort."""
    from functools import wraps
    import random
    @wraps(function)
    def wrapped(*args,**kwargs):
        for attempt in range(4):
            try:return function(*args,**kwargs)
            except PyMongoError as exc:
                if not (exc.has_error_label('TransientTransactionError') or isinstance(exc,DuplicateKeyError)) or attempt==3:raise
                time.sleep((.02 * 2**attempt)+random.random()*.02)
    return wrapped

from fastapi.routing import APIRoute
import inspect
class MongoRoute(APIRoute):
    def __init__(self,path,endpoint,**kwargs):
        if not inspect.iscoroutinefunction(endpoint) and not getattr(endpoint,'_mongo_retry',False):
            signature=inspect.signature(endpoint,eval_str=True)
            endpoint=retry_transaction(endpoint);endpoint.__signature__=signature;endpoint._mongo_retry=True
        super().__init__(path,endpoint,**kwargs)
