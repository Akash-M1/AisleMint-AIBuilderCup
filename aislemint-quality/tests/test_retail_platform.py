"""Tenant boundary, immutable data lifecycle and real isolated ML fits on disposable MongoDB."""
import hashlib, json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from promo.api import app
from promo.pg import database
from promo.retail import auth,datasets,modelling
from promo.retail.repository import now,uid
from promo.retail.contracts import normalize,readiness
from promo.storage import AssetPath

ORIGIN={'Origin':'http://localhost:3000'}
@pytest.fixture(autouse=True)
def secure(monkeypatch):monkeypatch.setenv('AISLEMINT_TEST_AUTH_BYPASS','0')

def identity(retailer=None,role='owner',admin=False):
    db=database();id=uid();token=uid();email=id+'@example.test'
    db.retail_users.insert_one(dict(_id=id,id=id,email=email,name='Test user',admin=admin,disabled=False,memberships={retailer:role} if retailer else {},**auth.credentials('Strong-test-password-12')))
    db.retail_sessions.insert_one(dict(_id=auth.digest(token),user_id=id,expires_at=now()+timedelta(hours=1)))
    client=TestClient(app);client.cookies.set(auth.COOKIE,token);client.headers.update(ORIGIN)
    return client,id

def tenant(code='SHOP-1'):
    db=database();r=uid();w=uid()
    db.retailers.insert_one(dict(_id=r,id=r,name='Test retailer '+code,created=now()))
    s=dict(_id=w,id=w,retailer_id=r,name='Test shop '+code,store_code=code,country='IN',currency='INR',timezone='Asia/Kolkata',revision=0,active={},created=now())
    db.retail_shops.insert_one(s);return r,s

def records(code='SHOP-1',count=104,products=10,frequency='weekly',scale=1):
    dates=pd.date_range('2023-01-01',periods=count,freq='7D' if frequency=='weekly' else 'D');out=[]
    for j in range(products):
        for i,dt in enumerate(dates):
            promo=int(i%12 in (7,8) and j%2==0);base=100.;price=base*(.85 if promo else 1)
            units=float(round(scale*(60+2*j+12*np.sin(i*2*np.pi/(52 if frequency=='weekly' else 365))+.15*i+promo*30)))
            out.append(dict(date=str(dt.date()),store_id=code,product_id=f'SKU-{j}',name=f'Product {j}',category='Grocery',units=units,price=price,base_price=base,feature=0,display=promo,promotion_available=promo,country='IN',currency='INR',stockout=0,holiday=0,unit_cost=60.,SPEND=units*price))
    return out

def ingest(s,kind='promotion',frequency='weekly',count=104,scale=1):
    row=datasets.create(s['id'],'Shop observed history',kind,frequency,uid(),'test')
    data=records(s['store_code'],count=count,frequency=frequency,scale=scale)
    for i in range(0,len(data),2000):datasets.append(s['id'],row['id'],str(i),data[i:i+2000])
    return datasets.finalize(s['id'],row['id'],'test')

def test_authentication_required_and_legacy_demo_hidden():
    anonymous=TestClient(app)
    assert anonymous.get('/api/summary').status_code==401
    assert anonymous.get('/api/pos/stores').status_code==401
    r,s=tenant();client,_=identity(r)
    assert client.get('/api/summary').status_code==404
    assert client.get('/api/summary',headers={'X-Workspace-ID':s['id']}).json()['model'] is None
    assert client.get('/api/demand/sources').json()[0]['id']==s['id']
    assert client.get('/api/datasets').json()==[]
    assert client.post('/api/pos/demo',json={}).status_code==403
    assert client.get('/api/demand/summary',params={'source_id':'benchmark'}).status_code==404

def test_cross_retailer_ids_headers_and_nested_forecast_are_rejected():
    r1,s1=tenant();r2,s2=tenant('SHOP-2');client,_=identity(r1)
    assert len(client.get('/api/retail/me').json()['retailers'])==1
    for path in [f'/api/retail/shops/{s2["id"]}/workbench','/api/summary']:
        assert client.get(path,headers={'X-Workspace-ID':s2['id']}).status_code==403
    assert client.post('/api/demand/inventory',json={'forecast':{'source_id':s2['id']}}).status_code==403
    assert client.post('/api/forecast',headers={'X-Workspace-ID':s2['id']},json={}).status_code==403
    assert client.post(f'/api/retail/retailers/{r2}/shops',json={'name':'Forbidden','store_code':'OTHER'}).status_code==403
    assert client.get('/api/demand/summary',params={'source_id':s1['id']}).json()['model'] is None

def test_owner_can_create_shops_and_codes_are_scoped_to_retailer():
    r,s=tenant();other,_=tenant();owner,_=identity(r);other_owner,_=identity(other)
    body={'name':'Mandipet shop','store_code':'Shop-001','country':'IN','timezone':'Asia/Kolkata'}
    created=owner.post(f'/api/retail/retailers/{r}/shops',json=body)
    assert created.status_code==200,created.text
    row=created.json();assert row['retailer_id']==r and row['active']=={} and row['currency']=='INR'
    second=owner.post(f'/api/retail/retailers/{r}/shops',json={**body,'store_code':'Shop-002'})
    assert second.status_code==200,second.text
    shared_code=other_owner.post(f'/api/retail/retailers/{other}/shops',json=body)
    assert shared_code.status_code==200,shared_code.text
    assert shared_code.json()['id']!=row['id']
    shops=owner.get('/api/retail/me').json()['retailers'][0]['shops']
    assert {shop['id'] for shop in shops}=={s['id'],row['id'],second.json()['id']}
    assert database().retail_audit.count_documents({'workspace':row['id'],'event':'shop_created'})==1

def test_duplicate_shop_code_explains_existing_workspace_without_changing_it():
    r,s=tenant('Shop-001');owner,_=identity(r)
    body={'name':'Another shop name','store_code':'Shop-001','country':'US'}
    for _ in range(2):
        response=owner.post(f'/api/retail/retailers/{r}/shops',json=body)
        assert response.status_code==409
        assert 'Shop-001' in response.json()['detail'] and s['name'] in response.json()['detail']
        assert 'different store code' in response.json()['detail']
        assert 'retry-after' not in response.headers
    existing=database().retail_shops.find_one({'_id':s['id']})
    assert existing['name']==s['name'] and existing['country']=='IN'
    assert database().retail_shops.count_documents({'retailer_id':r})==1
    assert database().retail_audit.count_documents({'event':'shop_created'})==0

def test_concurrent_shop_creation_saves_one_workspace_and_one_audit():
    from threading import Barrier
    r,_=tenant();clients=[identity(r)[0] for _ in range(4)];start=Barrier(len(clients))
    body={'name':'Concurrent shop','store_code':'Shop-001'}
    def create(client):
        start.wait(timeout=10)
        return client.post(f'/api/retail/retailers/{r}/shops',json=body)
    with ThreadPoolExecutor(max_workers=len(clients)) as executor:responses=list(executor.map(create,clients))
    assert sorted(response.status_code for response in responses)==[200,409,409,409]
    for response in responses:
        if response.status_code==409:assert 'different store code' in response.json()['detail']
    db=database();saved=db.retail_shops.find_one({'retailer_id':r,'store_code':'Shop-001'})
    assert db.retail_shops.count_documents({'retailer_id':r,'store_code':'Shop-001'})==1
    assert db.retail_audit.count_documents({'event':'shop_created','workspace':saved['id']})==1

def test_failed_shop_audit_does_not_leave_a_partially_created_workspace(monkeypatch):
    from promo.retail import api as retail_api
    r,_=tenant();owner,_=identity(r)
    def fail_audit(*args,**kwargs):raise RuntimeError('Simulated audit failure')
    monkeypatch.setattr(retail_api,'audit',fail_audit)
    with pytest.raises(RuntimeError,match='Simulated audit failure'):
        owner.post(f'/api/retail/retailers/{r}/shops',json={'name':'Audit failure shop','store_code':'Shop-001'})
    assert database().retail_shops.count_documents({'retailer_id':r,'store_code':'Shop-001'})==0

def test_viewer_cannot_mutate_or_escalate():
    r,s=tenant();client,_=identity(r,role='viewer')
    assert client.get(f'/api/retail/shops/{s["id"]}/workbench').status_code==200
    assert client.post(f'/api/retail/shops/{s["id"]}/snapshots',json={'name':'History','kind':'demand','frequency':'daily','idempotency_key':'example-01'}).status_code==403
    assert client.post(f'/api/retail/retailers/{r}/keys',json={'name':'forbidden'}).status_code==403

def test_cookie_mutations_require_origin_and_password_login():
    r,s=tenant();client,user_id=identity(r);client.headers.pop('origin')
    assert client.post('/api/retail/auth/logout',json={}).status_code==403
    email=database().retail_users.find_one({'_id':user_id})['email'];anonymous=TestClient(app,base_url='https://testserver')
    for _ in range(8):assert anonymous.post('/api/retail/auth/login',json={'email':email,'password':'wrong'}).status_code==401
    assert anonymous.post('/api/retail/auth/login',json={'email':email,'password':'Strong-test-password-12'}).status_code==429
    database().retail_login_attempts.delete_many({})
    good=anonymous.post('/api/retail/auth/login',json={'email':email,'password':'Strong-test-password-12'})
    assert good.status_code==200 and 'HttpOnly' in good.headers['set-cookie']
    assert anonymous.get('/api/retail/me').status_code==200
    anonymous.headers.update(ORIGIN);assert anonymous.post('/api/retail/auth/logout').status_code==200
    assert anonymous.get('/api/retail/me').status_code==401

def test_integration_key_only_writes_own_snapshots_and_revokes():
    r,s=tenant();_,other=tenant('OTHER');owner,_=identity(r)
    key=owner.post(f'/api/retail/retailers/{r}/keys',json={'name':'ERP connector'}).json()
    connector=TestClient(app,headers={'Authorization':'Bearer '+key['token']})
    assert connector.get('/api/retail/contract').status_code==200
    assert connector.get(f'/api/retail/shops/{s["id"]}/workbench').status_code==403
    assert connector.post(f'/api/retail/shops/{s["id"]}/train',json={}).status_code==403
    body={'name':'ERP history','kind':'demand','frequency':'daily','idempotency_key':'erp-test-01'}
    assert connector.post(f'/api/retail/shops/{other["id"]}/snapshots',json=body).status_code==403
    assert connector.post(f'/api/retail/shops/{s["id"]}/snapshots',json=body).status_code==200
    assert owner.post(f'/api/retail/retailers/{r}/keys/{key["id"]}/revoke',json={}).status_code==200
    assert connector.get('/api/retail/contract').status_code==401

def test_ingestion_replays_seals_and_keeps_corrections_separate():
    r,s=tenant();client,_=identity(r);base=f'/api/retail/shops/{s["id"]}/snapshots'
    req=dict(name='Short history',kind='promotion',frequency='weekly',idempotency_key='erp-snapshot-1')
    snapshot=client.post(base,json=req).json();assert client.post(base,json=req).json()['id']==snapshot['id']
    payload={'batch_key':'001','records':records(count=2)}
    endpoint=base+'/'+snapshot['id'];assert client.post(endpoint+'/batches',json=payload).json()['accepted']==20
    assert client.post(endpoint+'/batches',json=payload).json()['replayed']
    payload['records'][0]['name']='Changed product';assert client.post(endpoint+'/batches',json=payload).status_code==409
    result=client.post(endpoint+'/finalize').json();assert result['status']=='needs_data';assert result['report']['issues']
    assert client.post(endpoint+'/batches',json={'batch_key':'002','records':records(count=1)}).status_code==409
    assert client.post(f'/api/retail/shops/{s["id"]}/train',json={'snapshot_id':snapshot['id'],'idempotency_key':'training-1'}).status_code==409

def test_data_contract_catches_market_duplicates_prices_and_missing_calibration_paths():
    _,s=tenant();data=records(count=1);data[0]['store_id']='OTHER'
    assert normalize(data,s)[1][0]['row']==1
    data=records(count=1);data[0]['SPEND']=.01;assert normalize(data,s)[1]
    _,report=readiness(records()+records(count=1),'promotion','weekly');assert not report['ready'];assert 'Duplicate' in ' '.join(report['issues'])
    _,report=readiness(records(count=180,products=2,frequency='daily'),'demand','daily');assert not report['ready'];assert report['quality']['complete_calibration_paths']==4
    _,report=readiness(records(),'promotion','weekly');assert report['ready'],report

def test_full_snapshot_checksum_and_ownership():
    _,s=tenant();_,other=tenant('OTHER');row=ingest(s)
    assert row['status']=='ready',row
    assert hashlib.sha256((AssetPath(row['path'])/'panel.parquet').read_bytes()).hexdigest()==row['panel_sha256']
    with pytest.raises(Exception):datasets.get(other['id'],row['id'])
    assert datasets.finalize(s['id'],row['id'],'test')['content_sha256']==row['content_sha256']

def test_pos_onboarding_links_permanent_calendar_and_hides_other_store():
    r,s=tenant();other_r,other=tenant('OTHER');owner,_=identity(r);second,_=identity(other_r)
    req={'new_store':dict(name='Observed store',address='Bengaluru store address',state_code='29',gst_registered=False,owner_name='Store manager',pin='987654')}
    response=owner.post(f'/api/retail/shops/{s["id"]}/pos',json=req);assert response.status_code==200,response.text
    store=response.json()['pos_store_id'];assert database().pos_stores.find_one({'id':store})['forecast_workspace']==s['id']
    assert len(owner.get('/api/pos/stores').json())==1
    assert second.get('/api/pos/stores').json()==[]
    assert second.get(f'/api/pos/stores/{store}/context').status_code==403
    assert owner.post(f'/api/retail/shops/{s["id"]}/pos',json=req).status_code==409
    assert owner.get('/api/promotions',headers={'X-Workspace-ID':s['id']}).json()['events']==[]

def test_real_training_two_shop_versions_and_activation_fencing():
    r,s=tenant();other_r,other=tenant('OTHER');a=ingest(s);b=ingest(other,kind='demand',frequency='daily',count=220,scale=3)
    assert a['status']==b['status']=='ready',(a,b)
    job=modelling.queue(s['id'],a['id'],'efficient','request-111','test')
    second=modelling.queue(other['id'],b['id'],'efficient','request-222','test')
    assert modelling.queue(s['id'],a['id'],'efficient','request-111','test')['id']==job['id']
    assert modelling.process_one();assert modelling.process_one()
    db=database();job=db.retail_runs.find_one({'_id':job['id']});second=db.retail_runs.find_one({'_id':second['id']})
    assert job['status']=='candidate',job.get('error')
    assert second['status']=='candidate',second.get('error')
    assert job['card']['provenance']['workspace']==s['id']
    assert second['card']['provenance']['workspace']==other['id']
    assert job['artifact_path']!=second['artifact_path']
    assert db.retail_shops.find_one({'_id':s['id']})['active']=={}
    # Policy outcomes are genuinely measured; fixture activation policy is controlled separately to test switching.
    owner,_=identity(r);path=f'/api/retail/shops/{s["id"]}/runs/'
    preview=owner.get(path+job['id']+'/preview')
    assert preview.status_code==200,preview.text
    assert preview.json()['preview_only'] and preview.json()['points']
    assert db.retail_shops.find_one({'_id':s['id']})['active']=={}
    assert owner.get(path+second['id']+'/preview').status_code==404
    other_owner,_=identity(other_r)
    demand_preview=other_owner.get(f'/api/retail/shops/{other["id"]}/runs/{second["id"]}/preview')
    assert demand_preview.status_code==200,demand_preview.text
    assert len(demand_preview.json()['points'])==8 and demand_preview.json()['kind']=='demand'
    assert owner.post(path+second['id']+'/activate',json={'revision':0,'reason':'Reviewed test run'}).status_code==404
    db.retail_runs.update_one({'_id':job['id']},{'$set':{'gate.passed':False}})
    assert owner.post(path+job['id']+'/activate',json={'revision':0,'reason':'Reviewed test run'}).status_code==409
    db.retail_runs.update_one({'_id':job['id']},{'$set':{'gate.passed':True}})
    response=owner.post(path+job['id']+'/activate',json={'revision':0,'reason':'Reviewed test run'})
    assert response.status_code==200,response.text
    assert owner.post(path+job['id']+'/activate',json={'revision':0,'reason':'Stale browser tab'}).status_code==409
    summary=owner.get('/api/summary',headers={'X-Workspace-ID':s['id']}).json()
    assert summary['model']['provenance']['workspace']==s['id']
    assert summary['stores'][0]['store_id']==s['store_code']
    demand=modelling.demand_engine(other['id'],second['id']);assert set(demand.panel.store_id)=={other['store_code']}
    assert db.retail_shops.find_one({'_id':other['id']})['active']=={}

def test_interrupted_worker_and_cancelled_job_never_become_active():
    r,s=tenant();row=ingest(s);job=modelling.queue(s['id'],row['id'],'efficient',uid(),'test')
    client,_=identity(r);assert client.post(f'/api/retail/shops/{s["id"]}/runs/{job["id"]}/cancel').status_code==200
    database().retail_runs.insert_one(dict(_id='dead',id='dead',workspace=s['id'],request_key='dead-key',status='running',worker='expired'))
    assert not modelling.process_one()
    assert database().retail_runs.find_one({'_id':'dead'})['status']=='failed'
    assert database().retail_shops.find_one({'_id':s['id']})['active']=={}

def test_concurrent_batches_are_idempotent_and_sealing_blocks_late_writes():
    r,s=tenant();owner,_=identity(r)
    snap=datasets.create(s['id'],'Concurrency history','demand','daily',uid(),'test')
    path=f'/api/retail/shops/{s["id"]}/snapshots/{snap["id"]}'
    payload={'batch_key':'same-key','records':records(count=1)}
    with ThreadPoolExecutor(2) as pool:responses=list(pool.map(lambda _:owner.post(path+'/batches',json=payload),range(2)))
    assert all(r.status_code==200 for r in responses)
    assert datasets.get(s['id'],snap['id'])['rows']==10
    assert database().retail_batches.count_documents({'snapshot_id':snap['id']})==1
    with ThreadPoolExecutor(2) as pool:
        sealed=pool.submit(owner.post,path+'/finalize',json={})
        appended=pool.submit(owner.post,path+'/batches',json={'batch_key':'later','records':records(count=1)})
        assert sealed.result().status_code==200
        assert appended.result().status_code in (200,409)
    final=datasets.get(s['id'],snap['id'])
    stored=sum(r['count'] for r in database().retail_batches.find({'snapshot_id':snap['id']}))
    assert final['report']['quality']['rows']==stored==final['rows']

def test_oversized_and_malformed_requests_are_bounded():
    r,s=tenant();owner,_=identity(r)
    response=owner.post('/api/retail/shops/'+s['id']+'/snapshots',content=b'x'*(8*1024*1024+1))
    assert response.status_code==413
    assert owner.post('/api/demand/inventory',json=[]).status_code==422
    assert owner.post('/api/demand/inventory',content=b'{invalid',headers={'Content-Type':'application/json'}).status_code==422

def test_shop_comparison_profile_trains_its_own_candidates():
    _,s=tenant();snapshot=ingest(s)
    job=modelling.queue(s['id'],snapshot['id'],'comparison',uid(),'test')
    assert modelling.process_one()
    run=database().retail_runs.find_one({'_id':job['id']})
    assert run['status']=='candidate',run.get('error')
    assert run['card']['model_id']=='promotion-'+run['id']
    assert run['card']['provenance']['cross_retailer_training'] is False
    assert len(run['card']['folds'][-1]['baseline_candidates'])>=2
    assert not database().retail_shops.find_one({'_id':s['id']})['active']

def test_access_revocation_and_password_change_invalidate_sessions():
    r,s=tenant();owner,_=identity(r);viewer,user=identity(r,role='viewer')
    result=owner.post(f'/api/retail/retailers/{r}/users/{user}/access',json={'role':None})
    assert result.status_code==200
    assert viewer.get('/api/retail/me').status_code==401
    assert r not in database().retail_users.find_one({'_id':user})['memberships']
    result=owner.post('/api/retail/auth/password',json={'current_password':'Strong-test-password-12','new_password':'Changed-password-123'})
    assert result.status_code==200
    assert owner.get('/api/retail/me').status_code==401

def test_committed_pos_training_outbox_then_shop_candidate():
    from promo.pg import connection
    from promo.pos_core import store_row
    from promo.pos_learning import queue_job
    r,s=tenant();owner,_=identity(r)
    req={'new_store':dict(name='Training store',address='Bengaluru test store',state_code='29',gst_registered=False,owner_name='Store manager',pin='987654')}
    pos_id=owner.post(f'/api/retail/shops/{s["id"]}/pos',json=req).json()['pos_store_id']
    rows=records(count=220,frequency='daily')
    # Only test fixtures: closed-day observations use the same canonical IDs as the planning shop.
    database().pos_daily.insert_many([dict(store_id=pos_id,product_id=r['product_id'],business_date=r['date'],body={**r,'returned_units':0,'refund_ex_gst':0,'free_promotion':0}) for r in rows])
    with pytest.raises(RuntimeError):
        with connection() as c:
            c.begin();job=queue_job(c,store_row(c,pos_id),'demand')
            # A separate worker connection must not observe an uncommitted outbox job.
            from promo.pg import pool
            with pool().connection() as reader:
                assert reader.execute('SELECT _id FROM retail_ledger_jobs WHERE _id=%s',(job['id'],)).fetchone() is None
            raise RuntimeError('Simulate an aborted day-close transaction')
    assert database().retail_ledger_jobs.count_documents({})==0
    assert database().retail_snapshots.count_documents({})==0
    with connection() as c:
        c.begin();job=queue_job(c,store_row(c,pos_id),'demand')
    assert modelling.process_one()
    outbox=database().retail_ledger_jobs.find_one({'_id':job['id']})
    assert outbox['status']=='complete',outbox
    candidate=database().retail_runs.find_one({'_id':outbox['run_id']})
    assert candidate['status']=='queued' and candidate['workspace']==s['id']
    assert datasets.get(s['id'],candidate['snapshot_id'])['status']=='ready'

def test_shop_promotion_calendar_survives_activation_and_feeds_own_demand(monkeypatch):
    r,s=tenant();owner,_=identity(r);db=database();jobs=[]
    for kind in ['promotion','demand']:
        snapshot=ingest(s,kind=kind)
        job=modelling.queue(s['id'],snapshot['id'],'efficient',uid(),'test');assert modelling.process_one()
        result=db.retail_runs.find_one({'_id':job['id']});assert result['status']=='candidate',result.get('error')
        # Policy switching mechanics are tested separately from measured model fitness.
        db.retail_runs.update_one({'_id':job['id']},{'$set':{'gate.passed':True}})
        revision=db.retail_shops.find_one({'_id':s['id']})['revision']
        modelling.activate(s['id'],job['id'],revision,'Test calendar integration','test');jobs.append(job)
    headers={'X-Workspace-ID':s['id']}
    scenario=dict(name='Shop offer',store_id=s['store_code'],promotions=[dict(product_id='SKU-0',discount=.15,duration=1)])
    forecast=owner.post('/api/forecast',headers=headers,json=scenario);assert forecast.status_code==200,forecast.text
    payload={'scenario':scenario,'review_token':forecast.json()['confirmation_token'],'idempotency_key':uid()}
    confirmed=owner.post('/api/promotions/confirm',headers=headers,json=payload);assert confirmed.status_code==200,confirmed.text
    old=owner.get('/api/promotions',headers=headers).json()
    assert old['calendar_source_id']==s['id'] and len(old['events'])==1
    demand=owner.post('/api/demand/forecast',json={'source_id':s['id'],'store_id':s['store_code'],'product_ids':['SKU-0'],'horizon':4})
    assert demand.status_code==200,demand.text
    assert demand.json()['model_id']=='demand-'+jobs[1]['id']
    # Re-activation fences pointer revisions but never moves the calendar or its events.
    modelling.activate(s['id'],jobs[0]['id'],2,'Review existing candidate again','test')
    current=owner.get('/api/promotions',headers=headers).json();assert current==old
    # Simulate a model change between calculation and the transaction that confirms a second offer.
    other=dict(name='Another offer',store_id=s['store_code'],promotions=[dict(product_id='SKU-2',discount=.15,duration=1)])
    review=owner.post('/api/forecast',headers=headers,json=other);assert review.status_code==200,review.text
    import promo.api as service
    real_engine=service.get_engine(s['id'])
    class SwitchingEngine:
        def __getattr__(self,name):return getattr(real_engine,name)
        def forecast(self,*args,**kwargs):
            result=real_engine.forecast(*args,**kwargs)
            db.retail_shops.update_one({'_id':s['id']},{'$set':{'active.promotion':'changed-by-another-worker'}})
            return result
    monkeypatch.setattr(service,'get_engine',lambda workspace:SwitchingEngine())
    response=owner.post('/api/promotions/confirm',headers=headers,json={'scenario':other,'review_token':review.json()['confirmation_token'],'idempotency_key':uid()})
    assert response.status_code==409,response.text
    assert db.confirmed_promotions.count_documents({'workspace':s['id']})==1
