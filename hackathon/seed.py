"""Operator-only deterministic seed into aislemint_hackathon. Never imports production data."""
from pathlib import Path
import gzip,json,os,sys,time
from datetime import date,timedelta,datetime,time as day_time
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
ROOT=Path(__file__).resolve().parents[1]
if len(sys.argv)>1:load_dotenv(sys.argv[1],override=True)
sys.path.insert(0,str(ROOT/'aislemint-ops/backend'))
if os.environ.get('POSTGRES_DATABASE')!='aislemint_hackathon':raise RuntimeError('Dedicated hackathon DB required.')
from promo.pg import initialize,database,pool,connection
from promo.retail.repository import uid,now
from promo.retail import auth,datasets,modelling
from promo.storage import AssetPath
from hackathon.catalog import products,history,STORE_CODE,FLYER

BASE_RETAILER='a1b01de2c3d44556aabbccddeeff0001'
BASE_WORKSPACE='a1b01de2c3d44556aabbccddeeff0002'
ACTOR='system:synthetic-demo'

def seed():
 initialize();db=database()
 with pool().connection() as c:
  assert c.execute('SELECT current_database()').fetchone()[0]=='aislemint_hackathon'
  c.execute('CREATE TABLE IF NOT EXISTS hackathon_limits(bucket text,window_start bigint,count integer,PRIMARY KEY(bucket,window_start))')
 if AssetPath('hackathon/template.json.gz').exists():print('Template already seeded; leaving visitor data unchanged.');return
 if db.retailers.find_one({'_id':BASE_RETAILER}):print('Resuming incomplete baseline seed.')
 else:
  db.retailers.insert_one(dict(_id=BASE_RETAILER,id=BASE_RETAILER,name='MintMart · Fictional Bengaluru Retail',created=now(),demo_package='aibuildercup-v1'))
  db.retail_shops.insert_one(dict(_id=BASE_WORKSPACE,id=BASE_WORKSPACE,retailer_id=BASE_RETAILER,name='Bengaluru Match-Day Market',store_code=STORE_CODE,country='IN',currency='INR',timezone='Asia/Kolkata',revision=0,active={},created=now(),demo=True,demo_package='aibuildercup-v1'))
 principal=auth.principal.set(dict(id=ACTOR,admin=True,memberships={}))
 try:
  seed_models();seed_pos();seed_connected();export_template()
 finally:auth.principal.reset(principal)
 print('Synthetic baseline ready. No production data was read.')

def seed_models():
 db=database()
 for kind in ('promotion','demand'):
  if db.retail_shops.find_one({'_id':BASE_WORKSPACE}).get('active',{}).get(kind):continue
  print('Preparing',kind,'history',flush=True)
  snap=datasets.create(BASE_WORKSPACE,'SIMULATED · '+kind+' history',kind,'weekly' if kind=='promotion' else 'daily','cup-seed-'+kind,ACTOR)
  rows=history(kind)
  if snap['status']=='receiving':
   for off in range(0,len(rows),2000):datasets.append(BASE_WORKSPACE,snap['id'],str(off),rows[off:off+2000])
  snap=datasets.finalize(BASE_WORKSPACE,snap['id'],ACTOR)
  if snap['status']!='ready':raise RuntimeError('Synthetic history failed readiness: '+str(snap['report']['issues']))
  job=modelling.queue(BASE_WORKSPACE,snap['id'],'efficient','cup-model-'+kind,ACTOR)
  while job['status'] in ('queued','running'):
   modelling.process_one();job=db.retail_runs.find_one({'_id':job['id']})
  print(kind,job['status'],'gate:',job.get('gate'),flush=True)
  if job['status']!='candidate':raise RuntimeError('Synthetic model training failed: '+str(job.get('error')))
  if not job['gate']['passed']:raise RuntimeError('Synthetic model did not pass actual activation policy; no bypass was used.')
  shop=db.retail_shops.find_one({'_id':BASE_WORKSPACE})
  modelling.activate(BASE_WORKSPACE,job['id'],shop['revision'],'Seeded synthetic demonstration; metrics do not establish real retailer performance.',ACTOR)


def seed_pos():
 from promo.pos_api import create_store,put_product,StockInput,open_shift,OpenShift,checkout_sale,CheckoutInput,close_shift,CloseShift
 from promo.pos_core import StoreInput,ProductInput,Cart,CartLine,quote,store_row,stock_move
 from promo import api
 db=database();shop=db.retail_shops.find_one({'_id':BASE_WORKSPACE})
 if shop.get('pos_store_id'):return
 with connection() as c:
  result=create_store(c,StoreInput(name='MintMart · Synthetic OneSales',address='Fictional Bengaluru store — AI Builder Cup demo',state_code='29',gst_registered=True,gstin='29ABCDE1234F1Z5',owner_name='Demo manager',pin='20261018'),demo=True)
  sid=result['store']['id'];staff=result['staff'];store=result['store']
  c.db.pos_stores.update_one({'id':sid},{'$set':{'retailer_id':BASE_RETAILER,'forecast_workspace':BASE_WORKSPACE,'forecast_store_id':STORE_CODE,'promo_reference_date':(date.today()-timedelta(days=(date.today().weekday()-6)%7)).isoformat()}},session=c.session)
  c.db.retail_shops.update_one({'_id':BASE_WORKSPACE},{'$set':{'pos_store_id':sid}},session=c.session)
  for p in products():
   put_product(c,store,staff,ProductInput(product_id=p['product_id'],barcode=p['product_id'],name=p['name'],category=p['category'],hsn='19059090',price_paise=p['price_paise'],cost_paise=p['cost_paise'],gst_bps=p['gst_bps']))
   stock_move(c,store,p['product_id'],300,'demo_opening',sid,'Synthetic opening inventory',staff['id'],p['cost_paise'])
 token='Bearer '+result['token'];shift=open_shift(sid,OpenShift(opening_paise=200000),authorization=token)
 for i in range(24):
  basket=Cart(lines=[CartLine(product_id=products()[(i+j)%12]['product_id'],quantity=1+(i+j)%3) for j in range(3)])
  with connection() as c:q=quote(c,store_row(c,sid),basket)
  checkout_sale(sid,CheckoutInput(**basket.model_dump(),shift_id=shift['id'],quote_token=q['quote_token'],idempotency_key='seed-sale-'+str(i),tenders=[dict(method='cash',amount_paise=q['total_paise'])]),authorization=token)
 # Set the physical shelf to the invented risk scenario using audited stock moves.
 with connection() as c:
  store=store_row(c,sid)
  for p in products():
   existing=c.db.pos_products.find_one({'store_id':sid,'product_id':p['product_id']},session=c.session)
   diff=p['on_hand']-existing['on_hand']
   if diff:stock_move(c,store,p['product_id'],diff,'demo_count',sid,'Invented demonstration shelf count',staff['id'],p['cost_paise'])
 # Preserve seed transactions in a completed synthetic shift; guests get a new current-day shift.
 from promo.pos_core import expected_cash
 with connection() as c:
  opening=dict(c.db.pos_shifts.find_one({'id':shift['id']},session=c.session))
  expected=expected_cash(c,opening)
 close_shift(sid,shift['id'],CloseShift(counted_paise=expected),authorization=token)
 # A distinct, labelled offer makes the self-guided shopper useful before the oats mission.
 from promo.engine import Scenario,Promotion
 from promo.promotion_api import forecast
 from promo.promotions import confirm,ConfirmRequest
 scenario=Scenario(store_id=STORE_CODE,promotions=[Promotion(product_id='MINT-CHIPS',discount=.10,duration=2)],name='SIMULATED · pre-approved chips special')
 reviewed=forecast(scenario,BASE_WORKSPACE)
 confirm(ConfirmRequest(scenario=scenario,review_token=reviewed['confirmation_token'],idempotency_key='seed-chips-offer-001'),BASE_WORKSPACE)
 print('OneSales seeded with 24 synthetic sales and real integer-paise ledger calculations.',flush=True)


def seed_connected():
 from promo.connected import api,pipeline
 from promo.connected.schema import SourceInput
 from promo.data_platform import service
 from promo.data_platform.contracts import Subscription
 db=database()
 if db.connect_sources.find_one({'workspace':BASE_WORKSPACE}):return
 source_ids={}
 def publish(kind,name,rows):
  source=api.create_source(BASE_WORKSPACE,SourceInput(name=name,object_type=kind,kind='push',freshness_hours=168))
  source_ids[kind]=source['id']
  for off in range(0,len(rows),400):
   job=pipeline.queue(source,'seed-batch-'+str(off),ACTOR,rows[off:off+400])
   for _ in range(120):
    if db.connect_runs.find_one({'_id':job['id']})['status'] in ('complete','complete_with_issues','failed'):break
    pipeline.process_one(workspace=BASE_WORKSPACE)
   result=db.connect_runs.find_one({'_id':job['id']})
   if result['status']!='complete':raise RuntimeError('Synthetic feed failed: '+kind+str(result.get('error')))
  return source
 suppliers=[dict(id='SUP-'+str(i),name=n,status='active',lead_time_days=2+i) for i,n in enumerate(['Nandi Dairy (fictional)','Harvest Pantry (fictional)','Weekend Foods (fictional)','Fresh Route (fictional)'])]
 publish('Supplier','SIMULATED · supplier master',suppliers)
 publish('Product','SIMULATED · ERP product master',[dict(id=p['product_id'],name=p['name'],sku=p['product_id'],category=p['category'],price=p['price_paise']/100,currency='INR',on_hand=p['on_hand'],reorder_point=25,supplier_id=suppliers[i%4]['id']) for i,p in enumerate(products())])
 captured=datetime.combine(date.today()-timedelta(days=1),day_time(18,0),tzinfo=ZoneInfo('Asia/Kolkata')).isoformat()
 publish('InventoryPosition','SIMULATED · warehouse stock',[dict(id='STOCK-'+p['product_id'],product_id=p['product_id'],on_hand=p['on_hand'],reorder_point=25,captured_at=captured,unit_cost=p['cost_paise']/100,currency='INR',lead_time_days=3,review_period_days=7,case_pack=6,reserved=0,backlog=0) for p in products()])
 orders=[dict(id='PO-'+str(i+1),name='Synthetic replenishment '+str(i+1),supplier_id=suppliers[i%4]['id'],product_id=products()[i]['product_id'],status='in_transit',due_date=(date.today()-timedelta(days=1+i%3)).isoformat(),quantity=60+i*10) for i in range(6)]
 publish('PurchaseOrder','SIMULATED · purchase orders',orders)
 publish('Shipment','SIMULATED · carrier feed',[dict(id='SHIP-'+str(i+1),name='Synthetic delivery '+str(i+1),order_id=p['id'],status='delayed' if i<2 else 'in_transit',expected_date=p['due_date']) for i,p in enumerate(orders)])
 publish('Document','SIMULATED · competitor and supplier documents',[dict(id='FLYER-01',title='Match-day competitor flyer (fictional)',text=FLYER,product_id='MINT-OATS'),dict(id='SUPPLY-01',title='Supplier delay note (fictional)',text='The fictional oats replenishment is delayed by two days. This is scenario evidence, not a real supplier message.',product_id='MINT-OATS',supplier_id='SUP-1')])
 sales=[dict(id=row['date']+'-'+row['product_id'],**row) for row in history('demand') if row['date']>=(date.today()-timedelta(days=90)).isoformat()]
 publish('SalesObservation','SIMULATED · 90 completed trading days',sales)
 # An intentionally bad feed is separate from the authoritative shared data product.
 bad=api.create_source(BASE_WORKSPACE,SourceInput(name='SIMULATED · quality lab (one rejected row)',object_type='InventoryPosition',kind='push'))
 job=pipeline.queue(bad,'quality-lab-001',ACTOR,[dict(id='BAD-STOCK',product_id='MINT-OATS',on_hand='many',captured_at=captured)])
 for _ in range(10):
  if db.connect_runs.find_one({'_id':job['id']})['status'] in ('complete_with_issues','complete','failed'):break
  pipeline.process_one(workspace=BASE_WORKSPACE)
 obj=db.connect_objects.find_one({'workspace':BASE_WORKSPACE,'type':'Product','external_id':'MINT-OATS'})
 api.propose(BASE_WORKSPACE,api.Decision(object_id=obj['id'],object_revision=obj['revision'],title='Review oats replenishment before match-day',rationale='Synthetic shelf has 8 packs, below reorder point 25. Review the delayed supplier evidence before acting.',kind='replenishment_review',request_key='cup-review-oats-001'))
 service.configure(BASE_WORKSPACE,Subscription(sales=[source_ids['SalesObservation']],catalog=[source_ids['Product']],inventory=[source_ids['InventoryPosition']],auto_publish=False,auto_train=False),ACTOR)
 service.request_publish(BASE_WORKSPACE,ACTOR)
 for _ in range(20):
  if not service.process_one(workspace=BASE_WORKSPACE):break
 print('Connected evidence, supplier network and immutable shared data product seeded.',flush=True)


def export_template():
 from psycopg.rows import dict_row
 excluded={'schema_versions','assets','asset_versions','asset_blobs','leases','hackathon_limits','retail_users','retail_sessions','retail_login_attempts','retail_keys','pos_sessions','training_jobs','retail_batches'}
 tables=[]
 with pool().connection() as c:
  names=[r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")]
  for name in names:
   if name in excluded:continue
   columns=[(r[0],r[1]) for r in c.execute("SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",(name,))]
   result=c.execute('SELECT * FROM "'+name+'"');rows=[list(r) for r in result]
   if rows:tables.append(dict(name=name,columns=[n for n,t in columns],json_columns=[n for n,t in columns if t in ('json','jsonb')],rows=rows))
 from promo.pg_documents import encode
 data=dict(workspace=BASE_WORKSPACE,retailer=BASE_RETAILER,tables=tables,synthetic=True,created=now().isoformat())
 AssetPath('hackathon/template.json.gz').write_bytes(gzip.compress(json.dumps(encode(data)).encode()))
 print('Resettable template:',len(tables),'tables',sum(len(t['rows']) for t in tables),'rows',flush=True)

if __name__=='__main__':seed()
