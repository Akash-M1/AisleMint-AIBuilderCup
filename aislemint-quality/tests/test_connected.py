"""Connected data contracts on PostgreSQL, plus an external MongoDB adapter."""
import json
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from promo.api import app
from promo.pg import database
from promo.retail.repository import uid, now
from promo.connected import pipeline, connectors, insights
from promo.connected.repository import object_id
from promo.connected.schema import SourceInput
from test_retail_platform import tenant, identity, records

@pytest.fixture(autouse=True)
def secure(monkeypatch):
    monkeypatch.setenv('AISLEMINT_TEST_AUTH_BYPASS','0')
    monkeypatch.delenv('AISLEMINT_LOCAL_AI_MODEL',raising=False)
    monkeypatch.setenv('AISLEMINT_SOURCE_BINDINGS','{}')

def setup(role='owner'):
    retailer, shop=tenant(); client,_=identity(retailer,role=role)
    return client,shop,f'/api/retail/shops/{shop["id"]}/connected'

def source(client,base,kind='Product',**options):
    r=client.post(base+'/sources',json=dict(name='Test '+kind,kind='push',object_type=kind,**options))
    assert r.status_code==200,r.text
    return r.json()

def push(client,base,s,rows,key=None,process=True):
    r=client.post(base+f'/sources/{s["id"]}/batches',json={'request_key':key or uid(),'records':rows})
    assert r.status_code==200,r.text
    if process:assert pipeline.process_one()
    return database().connect_runs.find_one({'_id':r.json()['id']})

def body(s):return {k:v for k,v in s.items() if k in SourceInput.model_fields}

def test_ingestion_idempotency_quarantine_and_source_conflicts():
    c,shop,b=setup();s=source(c,b);k=uid()
    rows=[dict(id='A',name='Apples',on_hand=3,reorder_point=5),dict(id='B',name='',price='wrong')]
    run=push(c,b,s,rows,k);assert (run['status'],run['accepted'],run['rejected'])==('complete_with_issues',1,1)
    replay=push(c,b,s,rows,k,process=False);assert replay['id']==run['id']
    assert c.post(b+f'/sources/{s["id"]}/batches',json={'request_key':k,'records':[dict(id='A',name='Changed')]}).status_code==409
    second=source(c,b);conflict=push(c,b,second,[dict(id='A',name='Different source')]);assert conflict['rejected']==1
    obj=c.get(b+'/objects').json()['items'][0];assert obj['properties']['name']=='Apples'
    detail=c.get(b+'/objects/'+obj['id']).json();assert detail['lineage']['row']==1 and len(detail['versions'])==1
    assert len(c.get(b+'/quarantine').json()['items'])==2

def test_frozen_mapping_versions_and_unchanged_observations():
    c,shop,b=setup();s=source(c,b,mapping=[dict(target='name',source='label')])
    queued=push(c,b,s,[dict(id='A',label='Old mapping',name='New mapping')],process=False)
    updated=c.post(b+f'/sources/{s["id"]}/configure',json={**body(s),'mapping':[dict(target='name',source='name')]}).json()
    assert updated['revision']==2 and pipeline.process_one()
    id=object_id(shop['id'],'Product','A');assert c.get(b+'/objects/'+id).json()['object']['properties']['name']=='Old mapping'
    push(c,b,updated,[dict(id='A',name='New mapping')]);push(c,b,updated,[dict(id='A',name='New mapping')])
    detail=c.get(b+'/objects/'+id).json();assert detail['object']['revision']==2 and len(detail['versions'])==2
    assert detail['versions'][1]['provenance']['mapping_revision']==1

def test_tenant_and_restricted_source_policies_cover_graph_risks_and_assistant():
    c,shop,b=setup();p=source(c,b);s=source(c,b,'Supplier',visibility='restricted')
    push(c,b,s,[dict(id='V',name='Secret supplier')]);push(c,b,p,[dict(id='A',name='Apples',supplier_id='V',on_hand=1,reorder_point=2)])
    id=object_id(shop['id'],'Product','A');assert len(c.get(b+'/objects/'+id).json()['neighbors'])==1
    viewer,_=identity(shop['retailer_id'],role='viewer');detail=viewer.get(b+'/objects/'+id).json();assert detail['neighbors']==[] and detail['edges']==[]
    supplier_id=object_id(shop['id'],'Supplier','V');assert viewer.get(b+'/objects/'+supplier_id).status_code==404
    # Traverse incoming array relationships too, without losing source permissions.
    assert [row['id'] for row in c.get(b+'/objects/'+supplier_id).json()['neighbors']]==[id]
    assert viewer.post(b+'/ask',json={'question':'Secret supplier'}).json()['evidence']==[]
    assert viewer.post(b+'/sources',json=dict(name='Forbidden',object_type='Product')).status_code==403
    assert viewer.post(b+f'/sources/{p["id"]}/batches',json={'request_key':uid(),'records':[dict(id='bad',name='Forbidden')]}).status_code==403
    other,_=identity(tenant()[0]);assert other.get(b+'/overview').status_code==403
    key=c.post(f'/api/retail/retailers/{shop["retailer_id"]}/keys',json={'name':'Connected ingestion'}).json()['token']
    adapter=TestClient(app,headers={'Authorization':'Bearer '+key});assert adapter.get(b+'/overview').status_code==403
    assert adapter.post(b+f'/sources/{p["id"]}/batches',json={'request_key':uid(),'records':[dict(id='C',name='Carrots')]}).status_code==200
    # Restricting a previously shared source takes effect on existing objects, not only future syncs.
    assert c.post(b+f'/sources/{p["id"]}/configure',json={**body(p),'visibility':'restricted'}).status_code==200
    assert viewer.get(b+'/objects/'+id).status_code==404
    assert viewer.get(b+'/overview').json()['risks']['counts']['low_stock']==0

def test_pipeline_checkpoints_survive_transient_fetch_failure(monkeypatch):
    c,shop,b=setup();monkeypatch.setenv('AISLEMINT_SOURCE_BINDINGS',json.dumps({'test':{'workspace':shop['id'],'kind':'rest','url':'https://example.test/products'}}))
    r=c.post(b+'/sources',json=dict(name='Paged source',kind='rest',binding='test',object_type='Product'));assert r.status_code==200
    s=r.json();job=c.post(b+f'/sources/{s["id"]}/sync',json={'request_key':uid()}).json();calls=[]
    def fetch(source,cursor):
        calls.append(cursor)
        if cursor is None:return [dict(id='A',name='Apples')],2
        raise TimeoutError('Do not leak endpoint secrets')
    monkeypatch.setattr(pipeline,'fetch_page',fetch)
    assert pipeline.process_one() and pipeline.process_one()
    failed=database().connect_runs.find_one({'_id':job['id']});assert failed['status']=='failed' and failed['accepted']==1
    assert 'secrets' not in failed['error']
    monkeypatch.setattr(pipeline,'fetch_page',lambda source,cursor:([dict(id='B',name='Bananas')],None))
    assert c.post(b+'/runs/'+job['id']+'/retry',json={}).status_code==200
    assert pipeline.process_one();done=database().connect_runs.find_one({'_id':job['id']});assert done['accepted']==2 and done['status']=='complete'
    assert database().connect_batches.count_documents({'run_id':job['id']})==2

def test_custom_contracts_and_additive_evolution():
    c,shop,b=setup();contract=dict(name='Warehouse',description='Stock storage location',properties=[dict(name='name',type='string',required=True)])
    first=c.post(b+'/types',json=contract);assert first.status_code==200,first.text
    updated={**contract,'revision':1,'properties':contract['properties']+[dict(name='capacity',type='number')]}
    assert c.post(b+'/types',json=updated).status_code==200
    assert c.post(b+'/types',json=updated).status_code==409
    assert c.post(b+'/types',json={**contract,'revision':2,'properties':[dict(name='name',type='number',required=True)]}).status_code==409
    s=source(c,b,'Warehouse');assert push(c,b,s,[dict(id='W',name='Central',capacity=12)])['accepted']==1

def test_decision_review_rejects_changed_evidence_and_requires_owner():
    c,shop,b=setup();s=source(c,b);push(c,b,s,[dict(id='A',name='Apples',on_hand=1,reorder_point=4)])
    planner,_=identity(shop['retailer_id'],role='planner');id=object_id(shop['id'],'Product','A')
    proposal=dict(object_id=id,object_revision=1,title='Review apple replenishment',rationale='Stock is below the configured threshold.',kind='replenishment_review',request_key=uid())
    decision=planner.post(b+'/decisions',json=proposal).json()
    assert planner.post(b+f'/decisions/{decision["id"]}/transition',json=dict(revision=1,status='approved',note='Review completed')).status_code==403
    push(c,b,s,[dict(id='A',name='Apples',on_hand=8,reorder_point=4)])
    assert c.post(b+f'/decisions/{decision["id"]}/transition',json=dict(revision=1,status='approved',note='Review completed')).status_code==409
    assert c.post(b+f'/decisions/{decision["id"]}/transition',json=dict(revision=1,status='rejected',note='Source stock has changed')).status_code==200
    fresh=planner.post(b+'/decisions',json={**proposal,'object_revision':2,'request_key':uid()}).json()
    assert c.post(b+f'/decisions/{fresh["id"]}/transition',json=dict(revision=1,status='approved',note='Reviewed the updated evidence')).status_code==200
    assert c.post(b+f'/decisions/{fresh["id"]}/transition',json=dict(revision=2,status='completed',note='Investigation completed in ERP')).status_code==200
    assert database().connect_objects.find_one({'_id':id})['properties']['on_hand']==8

def test_sales_snapshot_freezes_lineage_and_uses_existing_readiness(monkeypatch):
    c,shop,b=setup();s=source(c,b,'SalesObservation');rows=[dict(id=str(i),**r) for i,r in enumerate(records(count=2))]
    push(c,b,s,rows);request=dict(request_key=uid(),kind='promotion',frequency='weekly')
    from promo.connected import api as connected_api
    real_finalize=connected_api.datasets.finalize
    def interrupt(*args):raise RuntimeError('Simulated shutdown after atomic freeze')
    monkeypatch.setattr(connected_api.datasets,'finalize',interrupt)
    with pytest.raises(RuntimeError,match='Simulated shutdown'):c.post(b+f'/sources/{s["id"]}/sales-snapshot',json=request)
    snapshot=database().retail_snapshots.find_one({'idempotency_key':request['request_key']});assert snapshot['status']=='validating'
    rows[0]['units']+=1;rows[0]['SPEND']=rows[0]['units']*rows[0]['price'];push(c,b,s,rows)
    monkeypatch.setattr(connected_api.datasets,'finalize',real_finalize)
    result=c.post(b+f'/sources/{s["id"]}/sales-snapshot',json=request);assert result.status_code==200,result.text
    assert result.json()['status']=='needs_data' and result.json()['rows']==20
    batches=list(database().retail_batches.find({'snapshot_id':snapshot['id']}));assert batches[0]['connected_lineage'][0]['revision']==1
    assert c.post(b+f'/sources/{s["id"]}/sales-snapshot',json=request).json()['id']==snapshot['id']

def test_evidence_assistant_rules_and_invalid_model_citations(monkeypatch):
    c,shop,b=setup();s=source(c,b);push(c,b,s,[dict(id='A',name='Apples',on_hand=2,reorder_point=5)])
    answer=c.post(b+'/ask',json={'question':'Which products have low stock?'}).json();assert answer['mode']=='evidence_search' and len(answer['evidence'])==1
    assert '[1]' in answer['answer'] and answer['model'] is None
    monkeypatch.setenv('AISLEMINT_LOCAL_AI_MODEL','test-local-model')
    monkeypatch.setattr(insights,'request_data',lambda *args,**kw:json.dumps({'message':{'content':'Unsupported [99]'}}).encode())
    fallback=c.post(b+'/ask',json={'question':'Which products have low stock?'}).json();assert fallback['warning'] and fallback['mode']=='evidence_search'
    po=source(c,b,'PurchaseOrder');push(c,b,po,[dict(id='P',name='Late order',status='open',due_date='2020-01-01'),dict(id='Q',name='Received order',status='RECEIVED',due_date='2020-01-01')])
    assert c.get(b+'/overview').json()['risks']['counts']['overdue']==1

def test_binding_scope_and_read_only_vendor_adapter_payloads(monkeypatch):
    c,shop,b=setup();foreign=tenant()[1]
    monkeypatch.setenv('AISLEMINT_SOURCE_BINDINGS',json.dumps({'foreign':{'workspace':foreign['id'],'kind':'rest','url':'https://example.test'}}))
    assert c.post(b+'/sources',json=dict(name='Forbidden endpoint',kind='rest',binding='foreign',object_type='Product')).status_code==422
    monkeypatch.setenv('AISLEMINT_CONNECT_TOKEN','test-value')
    base={'workspace':shop['id'],'url':'https://example.test/api','token_env':'AISLEMINT_CONNECT_TOKEN','currency':'INR'}
    config={k:{**base,'kind':k,'single_location':False,'organization_id':'organization'} for k in ('shopify','zoho','rest')}
    monkeypatch.setenv('AISLEMINT_SOURCE_BINDINGS',json.dumps(config))
    requests=[]
    def fetch(url,headers=None,payload=None):
        requests.append((url,headers,payload))
        return {'data':{'productVariants':{'nodes':[{'id':'variant-1','title':'100g','sku':'ABC','price':'100.00','inventoryQuantity':9,'product':{'title':'Spice','productType':'Food'}}],'pageInfo':{'hasNextPage':True,'endCursor':'next'}}}}
    monkeypatch.setattr(connectors,'json_request',fetch)
    rows,cursor=connectors.fetch_page({'workspace':shop['id'],'kind':'shopify','binding':'shopify'});assert cursor=='next' and 'on_hand' not in rows[0]
    assert requests[0][2]['query'].startswith('query(') and 'mutation' not in requests[0][2]['query']
    monkeypatch.setattr(connectors,'json_request',lambda *args,**kw:{'code':0,'items':[dict(item_id='A',name='Apples',rate=5,stock_on_hand=20)],'page_context':{'has_more_page':False}})
    rows,cursor=connectors.fetch_page({'workspace':shop['id'],'kind':'zoho','binding':'zoho'});assert len(rows)==1 and cursor is None and 'on_hand' not in rows[0]
    with pytest.raises(ValueError,match='HTTPS'):connectors.request_data('http://169.254.169.254/latest/meta-data/')

def test_onesales_native_adapter(monkeypatch):
    c,shop,b=setup();db=database();store='test-register';db.retail_shops.update_one({'_id':shop['id']},{'$set':{'pos_store_id':store}})
    # Mock native read only; POS ledger invariants are covered separately in the existing suite.
    class Cursor:
        def sort(self,*args):return self
        def limit(self,*args):return [dict(product_id='SKU-1',name='Spices',category='Food',price_paise=10000,cost_paise=6000,on_hand=3,reorder_level=5)]
    class Products:
        def find(self,query):assert query['store_id']==store;return Cursor()
    class DB:pos_products=Products()
    monkeypatch.setattr(connectors,'database',lambda:DB())
    rows,cursor=connectors.fetch_page({'workspace':shop['id'],'kind':'onesales','object_type':'Product'});assert rows[0]['price']==100 and cursor is None
    rows,cursor=connectors.fetch_page({'workspace':shop['id'],'kind':'onesales','object_type':'InventoryPosition'});assert rows[0]['product_id']=='SKU-1' and rows[0]['captured_at']

def test_mongo_adapter_pages_fixed_projection_without_source_writes(monkeypatch):
    from pymongo import MongoClient
    import os
    uri=os.environ.get('TEST_CONNECTOR_MONGODB_URI','mongodb://127.0.0.1:27029/')
    from urllib.parse import urlsplit
    assert urlsplit(uri).hostname in ('127.0.0.1','localhost')
    c,shop,b=setup();external=MongoClient(uri,serverSelectionTimeoutMS=5000);db=external['aislemint_connector_test'];collection=db.tests_connector_fixture
    try:
        collection.insert_many([{'_id':f'{i:03d}','name':f'Product {i}','secret_column':'excluded'} for i in range(101)])
        import os
        monkeypatch.setenv('AISLEMINT_CONNECT_MONGO',uri)
        monkeypatch.setenv('AISLEMINT_SOURCE_BINDINGS',json.dumps({'mongo':dict(workspace=shop['id'],kind='mongodb',uri_env='AISLEMINT_CONNECT_MONGO',database='aislemint_connector_test',collection='tests_connector_fixture',projection={'name':1})}))
        s=dict(workspace=shop['id'],kind='mongodb',binding='mongo')
        rows,cursor=connectors.fetch_page(s);assert len(rows)==100 and cursor['value']=='099'
        assert all('secret_column' not in r for r in rows)
        last,end=connectors.fetch_page(s,cursor);assert len(last)==1 and last[0]['_id']=='100' and end is None
        assert collection.count_documents({'secret_column':'excluded'})==101
    finally:collection.drop();external.close()

def test_woocommerce_and_tally_read_only_contracts(monkeypatch):
    c,shop,b=setup();monkeypatch.setenv('AISLEMINT_CONNECT_KEY','test');monkeypatch.setenv('AISLEMINT_CONNECT_SECRET','test')
    monkeypatch.setenv('AISLEMINT_SOURCE_BINDINGS',json.dumps({'woo':dict(workspace=shop['id'],kind='woocommerce',url='https://example.test/wp-json/wc/v3/products',currency='INR',key_env='AISLEMINT_CONNECT_KEY',secret_env='AISLEMINT_CONNECT_SECRET'),
        'tally':dict(workspace=shop['id'],kind='tally',url='http://127.0.0.1:9000',company='Example & Sons')}))
    monkeypatch.setattr(connectors,'json_request',lambda *args,**kw:[dict(id=4,name='Apples',price='12.50',manage_stock=False,stock_quantity=None,categories=[])])
    rows,cursor=connectors.fetch_page(dict(workspace=shop['id'],kind='woocommerce',binding='woo'));assert rows[0]['price']==12.5 and 'on_hand' not in rows[0] and cursor is None
    requests=[]
    def tally(url,headers,body,local):
        requests.append(body.decode());return b'<ENVELOPE><STOCKITEM NAME="Spices"><GUID>ABC</GUID><PARENT>Food</PARENT></STOCKITEM></ENVELOPE>'
    monkeypatch.setattr(connectors,'request_data',tally)
    rows,cursor=connectors.fetch_page(dict(workspace=shop['id'],kind='tally',binding='tally'));assert rows[0]['id']=='ABC'
    assert '<TALLYREQUEST>Export</TALLYREQUEST>' in requests[0] and 'Example &amp; Sons' in requests[0]
    monkeypatch.setattr(connectors,'request_data',lambda *a,**kw:b'<!DOCTYPE unsafe><ENVELOPE/>')
    with pytest.raises(ValueError,match='external entities'):connectors.fetch_page(dict(workspace=shop['id'],kind='tally',binding='tally'))

def test_expired_worker_fencing_and_run_pagination():
    c,shop,b=setup();s=source(c,b);jobs=[]
    for i in range(3):jobs.append(push(c,b,s,[dict(id=str(i),name='Product '+str(i))],process=False))
    db=database();run=jobs[0];db.connect_runs.update_one({'_id':run['id']},{'$set':{'worker':'old','status':'running','expires_at':now()-timedelta(seconds=1)}})
    batch=db.connect_batches.find_one({'run_id':run['id']})
    with pytest.raises(RuntimeError,match='superseded'):pipeline.materialize(run,batch,'old')
    assert db.connect_objects.count_documents({'workspace':shop['id']})==0
    for _ in range(3):assert pipeline.process_one()
    first=c.get(b+'/runs?limit=2').json();second=c.get(b+'/runs?limit=2&before='+first['next_cursor']).json()
    assert len({r['id'] for r in first['items']+second['items']})==3 and second['next_cursor'] is None

def test_mapping_preview_does_not_write_and_raw_quarantine_requires_owner():
    c,shop,b=setup();s=source(c,b)
    preview=c.post(b+f'/sources/{s["id"]}/preview',json={'records':[dict(id='A',name='Apples')]}).json()
    assert preview['writes'] is False and preview['results'][0]['valid']
    assert database().connect_objects.count_documents({'workspace':shop['id']})==0
    job=push(c,b,s,[dict(id='A',name='')]);issue=c.get(b+'/quarantine').json()['items'][0]
    planner,_=identity(shop['retailer_id'],role='planner')
    assert planner.get(b+'/quarantine/'+issue['id']).status_code==403
    assert c.get(b+'/quarantine/'+issue['id']).json()['record']['name']==''
    run=c.get(b+'/runs/'+job['id']).json();assert run['schema']['name']=='Product' and 'records' not in run['batches'][0]
