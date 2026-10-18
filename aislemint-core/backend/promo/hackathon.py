"""No-login synthetic sandbox. Normal tenant authorization remains enforced."""
from datetime import timedelta,datetime
from zoneinfo import ZoneInfo
import gzip,hashlib,json,os,re,secrets,uuid
from fastapi import APIRouter,HTTPException,Request,Response
from psycopg import sql
from psycopg.types.json import Jsonb
from .pg import database,pool
from .storage import AssetPath
from .retail import auth
from .retail.repository import now,required_shop

router=APIRouter(prefix='/api/demo',tags=['Isolated hackathon simulation'])
TEMPLATE_KEY='hackathon/template.json.gz'
ID_RE=re.compile(r'(?<![a-f0-9])(?:[a-f0-9]{64}|[a-f0-9]{32})(?![a-f0-9])')

def assert_isolated():
 if os.environ.get('AISLEMINT_HACKATHON')!='1' or os.environ.get('POSTGRES_DATABASE')!='aislemint_hackathon':
  raise RuntimeError('The public demo is restricted to its dedicated database.')
 from psycopg.conninfo import conninfo_to_dict
 from .pg import _conninfo
 if conninfo_to_dict(_conninfo()).get('dbname')!='aislemint_hackathon':
  raise RuntimeError('Database URL does not target the isolated demo database.')

def quota(bucket,limit,seconds=3600):
 assert_isolated()
 window=int(now().timestamp())//seconds
 with pool().connection() as c:
  row=c.execute('INSERT INTO hackathon_limits(bucket,window_start,count) VALUES(%s,%s,1) ON CONFLICT(bucket,window_start) DO UPDATE SET count=hackathon_limits.count+1 WHERE hackathon_limits.count<%s RETURNING count',(bucket,window,limit)).fetchone()
 if not row:raise HTTPException(429,'Demo capacity reached. Please retry later; existing results remain available.')

def claim_model_call():
 quota('model-calls-day',200,86400)
 p=auth.principal.get() or {}
 quota('model:'+p.get('id','operator'),24,3600)

async def guard(request):
 assert_isolated();path=request.url.path
 if path.startswith('/api/retail/provisioning') or path in ('/api/retail/ai/configure','/api/retail/auth/login','/api/retail/auth/password'):
  raise HTTPException(403,'Account and payment administration are disabled in this synthetic demo.')
 if path.startswith(('/api/integrations/','/api/datasets/')) or re.search(r'/retailers(?:/|$)',path):
  raise HTTPException(403,'Use the prepared simulation. Account creation and external integrations are disabled.')
 if request.method=='POST':
  if not request.headers.get('origin'):
   raise HTTPException(403,'Demo changes require the browser origin.')
  # Opening another page resumes a workspace; it does not create or mutate one.
  # Authenticate on every request so expired/revoked/forged cookies stay bounded.
  if path=='/api/demo/session':
   p=existing_guest(request)
   if p:
    request.state.demo_guest=p
    return
  identity=hashlib.sha256((request.cookies.get(auth.COOKIE) or request.headers.get('x-real-ip') or request.client.host).encode()).hexdigest()[:24]
  quota('writes:'+identity,100,60)
  quota('all-demo-writes',2000,86400)
  if path.endswith('/train'):quota('training-day',12,86400)
  if path in ('/api/demo/session','/api/demo/reset'):quota('sessions:'+identity,8,3600)
  if re.search(r'/sources(?:/[^/]+/configure)?$',path):
   body=await request.json()
   if body.get('kind','push') not in ('push','onesales'):
    raise HTTPException(403,'This isolated demo accepts synthetic push feeds and its own OneSales ledger only.')

@router.get('/info')
def info():
 assert_isolated()
 from .gemini import get_api_key,DEFAULT_MODEL,FAST_MODEL
 return dict(name='AisleMint · AI Builder Cup',synthetic=True,no_login=True,
  ai_configured=bool(get_api_key()),models=[DEFAULT_MODEL,FAST_MODEL],
  scenario='Match-day promotion rescue',products=12,weekly_periods=156,daily_periods=1092,
  notice='All trading, suppliers, inventory and outcomes are synthetic. No real payments or messages are sent.')

def transform(value,mapping,key=''):
 if isinstance(value,dict):return {mapping.get(k,k):transform(v,mapping,k) for k,v in value.items()}
 if isinstance(value,list):return [transform(v,mapping,key) for v in value]
 if isinstance(value,str):
  # Synthetic fitted artifacts are immutable and shared; visitor operations are separate.
  if key in ('path','artifact_path') or value.startswith(('retail/','artifacts/')):return value
  return ID_RE.sub(lambda m:mapping.get(m[0],m[0]),value)
 return value

def clone_template():
 assert_isolated();template=json.loads(gzip.decompress(AssetPath(TEMPLATE_KEY).read_bytes()))
 mapping={}
 for table in template['tables']:
  for row in table['rows']:
   for column,value in zip(table['columns'],row):
    if column in ('_id','id') and isinstance(value,str) and re.fullmatch('[a-f0-9]{32,64}',value):mapping[value]=uuid.uuid4().hex
 workspace=mapping[template['workspace']];retailer=mapping[template['retailer']]
 from .connected.repository import object_id
 for table in template['tables']:
  if table['name']=='connect_objects':
   for row in table['rows']:
    obj=row[table['columns'].index('data')]
    mapping[obj['id']]=object_id(workspace,obj['type'],obj['external_id'])
 from .connected.repository import digest
 from .pg_documents import decode
 prepared=[]; chunks={}
 for table in template['tables']:
  rows=[[transform(v,mapping,col) for col,v in zip(table['columns'],row)] for row in table['rows']]
  if 'data' in table['columns']:
   index=table['columns'].index('data')
   for row in rows:
    doc=row[index]
    if table['name']=='data_chunks':
     doc['checksum']=digest(decode(doc['records']))
     chunks.setdefault(doc['release_id'],{}).setdefault(doc['domain'],[]).append(doc)
    if table['name']=='retail_runs':
     doc['card'].setdefault('provenance',{}).update(shared_synthetic_fit=True,template_workspace=template['workspace'])
  prepared.append((table,rows))
 for table,rows in prepared:
  if table['name']=='data_releases':
   for row in rows:
    doc=row[table['columns'].index('data')];objects={}
    for domain in ('sales','catalog','inventory'):
     records=[]
     for chunk in sorted(chunks.get(doc['id'],{}).get('source_'+domain,[]),key=lambda x:x['page']):records.extend(decode(chunk['records']))
     objects[domain]=[{k:o[k] for k in ('id','revision','source_id','properties')} for o in records]
    doc['content_sha256']=digest(dict(version=doc['contract_version'],frequency=doc['frequency'],overlap=doc['sales_overlap'],objects=objects))
 with pool().connection() as c:
  for table,rows in prepared:
   columns=table['columns'];statement=sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(
    sql.Identifier(table['name']),sql.SQL(',').join(map(sql.Identifier,columns)),sql.SQL(',').join(sql.Placeholder() for _ in columns))
   data=[[Jsonb(v) if col in table['json_columns'] else v for col,v in zip(columns,row)] for row in rows]
   if data:
    with c.cursor() as cursor:cursor.executemany(statement,data)
 return workspace,retailer

def create_guest(response):
 quota('new-guests-day',200,86400)
 workspace,retailer=clone_template();db=database();user=uuid.uuid4().hex;token=secrets.token_urlsafe(48)
 db.retail_users.insert_one(dict(_id=user,id=user,name='Hackathon explorer',email=user+'@demo.invalid',admin=False,disabled=False,
  memberships={retailer:'owner'},created=now(),demo_workspace=workspace,synthetic_guest=True))
 db.retail_sessions.insert_one(dict(_id=auth.digest(token),user_id=user,expires_at=now()+timedelta(hours=8)))
 response.set_cookie(auth.COOKIE,token,httponly=True,secure=os.environ.get('DEMO_LOCAL')!='1',samesite='strict',max_age=28800,path='/')
 return dict(ready=True,workspace=workspace,synthetic=True)

def existing_guest(request):
 try:
  p=auth.authenticate(request)
 except HTTPException as error:
  if error.status_code==401:return None
  raise
 if p.get('id') and database().retail_users.find_one({'_id':p['id'],'synthetic_guest':True}):return p
 return None

@router.post('/session')
def session(request:Request,response:Response):
 if getattr(request.state,'demo_guest',None) or existing_guest(request):return dict(ready=True,synthetic=True)
 return create_guest(response)

@router.post('/reset')
def reset(request:Request,response:Response):
 p=auth.account()
 if not database().retail_users.find_one({'_id':p['id'],'synthetic_guest':True}):raise HTTPException(403,'Only demo guests can reset.')
 result=create_guest(response)
 database().retail_users.update_one({'_id':p['id']},{'$set':{'disabled':True}})
 database().retail_sessions.delete_many({'user_id':p['id']})
 return result

@router.post('/pos-session')
def pos_session():
 p=auth.account();user=database().retail_users.find_one({'_id':p['id'],'synthetic_guest':True})
 if not user:raise HTTPException(403,'Demo guest required.')
 shop=required_shop(user['demo_workspace']);auth.require_workspace(shop['id'])
 from .pg import connection
 from .pos_core import session_for,store_row,local_day
 with connection() as c:
  staff=c.db.pos_staff.find_one({'store_id':shop['pos_store_id'],'role':'manager'},session=c.session)
  store=store_row(c,shop['pos_store_id'])
  c.conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('demo-register:'+store['id'],))
  if not c.db.pos_shifts.find_one({'store_id':store['id'],'closed':None},session=c.session):
   c.run('pos_api_open_shift_insert_pos_shifts',(uuid.uuid4().hex,store['id'],'Register 1',staff['id'],local_day(store),200000,now().isoformat()))
  return dict(store=store,staff={k:staff[k] for k in ('id','name','role')},token=session_for(c,staff['id']))
