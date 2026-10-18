"""Shared source contracts, immutable releases and recoverable consumer delivery."""
from datetime import datetime, timedelta
import json
import pytest
from promo.pg import database
from promo.retail.repository import now, uid
from promo.data_platform import service
from promo.data_platform.contracts import Subscription, merge_sales, weekly_sales, analytics
from promo.connected import connectors, pipeline
from test_connected import setup, source, push, body
from test_retail_platform import tenant, identity, records


@pytest.mark.parametrize('kind,profile',[
    ('demand','efficient'),('demand','comparison'),
    ('promotion','efficient'),('promotion','comparison'),
])
def test_published_connected_history_trains_with_timestamp_lineage(kind,profile):
    from promo.retail import modelling
    from promo.storage import AssetPath,load_model
    c,shop,b=setup();feed=source(c,b,'SalesObservation')
    rows=[dict(id=r['product_id']+':'+r['date'],**r)
          for r in records(shop['store_code'],count=104,products=10)]
    for offset in range(0,len(rows),500):push(c,b,feed,rows[offset:offset+500])
    base,_=configure(c,shop,sales=[feed['id']],frequency='weekly')
    delivery=publish(c,shop,base);assert delivery['status']=='complete',delivery
    release=service.active(shop['id'])
    snapshot=database().retail_snapshots.find_one({'_id':release['consumers'][kind]['snapshot_id']})
    assert snapshot['status']=='ready'
    assert isinstance(snapshot['lineage'][0]['last_sync'],datetime)
    job=modelling.queue(shop['id'],snapshot['id'],profile,uid(),'test')
    assert modelling.process_one()
    run=database().retail_runs.find_one({'_id':job['id']})
    assert run['status']=='candidate',run.get('error')
    path=AssetPath(run['artifact_path'])
    manifest=json.loads((path/'model_card.json').read_text())
    bundle=load_model(path/'model.joblib')
    assert bundle.model_id==manifest['model_id']==run['card']['model_id']
    evidence_cards=[run['card'],manifest]
    if hasattr(bundle,'card'):evidence_cards.append(bundle.card)
    for card in evidence_cards:
        evidence=card['provenance']
        assert evidence['data_release_id']==release['id']
        assert evidence['source_ids']==[feed['id']]
        assert evidence['source_lineage'][0]['last_sync']==snapshot['lineage'][0]['last_sync'].isoformat()
    assert isinstance(database().retail_snapshots.find_one({'_id':snapshot['id']})['lineage'][0]['last_sync'],datetime)
    assert not database().retail_shops.find_one({'_id':shop['id']})['active']

@pytest.fixture(autouse=True)
def secure(monkeypatch):
    monkeypatch.setenv('AISLEMINT_TEST_AUTH_BYPASS','0')
    monkeypatch.setenv('AISLEMINT_SOURCE_BINDINGS','{}')

def configure(c,shop,**options):
    base=f'/api/retail/shops/{shop["id"]}/data'
    response=c.post(base+'/subscription',json=dict(auto_publish=False,**options))
    assert response.status_code==200,response.text
    return base,response.json()

def publish(c,shop,base):
    response=c.post(base+'/publish',json={}); assert response.status_code==200,response.text
    assert service.process_one(shop['id'])
    return database().data_jobs.find_one({'_id':response.json()['id']})

def sales(c,shop,b,channel,count=7,products=1):
    s=source(c,b,'SalesObservation')
    rows=records(shop['store_code'],count=count,products=products,frequency='daily')
    rows=[dict(id=channel+':'+r['product_id']+':'+r['date'],returned_units=0.,refund_ex_gst=0.,**r) for r in rows]
    for offset in range(0,len(rows),500): push(c,b,s,rows[offset:offset+500])
    return s,rows

def test_multiple_sources_publish_snapshots_and_analytics_without_double_counting():
    c,shop,b=setup();one,rows=sales(c,shop,b,'store');two,_=sales(c,shop,b,'online')
    base,p=configure(c,shop,sales=[one['id'],two['id']])
    failed=publish(c,shop,base)
    assert failed['status']=='failed' and 'Overlapping' in failed['error']
    assert c.get(base+'/analytics').status_code==409
    base,p=configure(c,shop,revision=p['revision'],sales=[one['id'],two['id']],sales_overlap='disjoint_channels')
    job=publish(c,shop,base); assert job['status']=='complete',job
    r=c.get(base+'/analytics').json()
    assert r['totals']['gross_units']==2*sum(x['units'] for x in rows)
    assert r['totals']['net_revenue']==r['totals']['gross_revenue']
    assert len(r['release']['lineage'])==2 and r['release']['counts']['canonical_sales']==7
    assert len(service.records(r['release'],'sales'))==7
    consumers=r['release']['consumers']; assert set(consumers)=={'demand','promotion'}
    assert all(v['status']=='needs_data' for v in consumers.values())
    assert consumers['promotion']['excluded_partial_week_rows']==7
    snapshot=database().retail_snapshots.find_one({'_id':consumers['demand']['snapshot_id']})
    assert snapshot['data_release_id']==r['release']['id'] and snapshot['source_ids']==r['release']['source_ids']
    second=publish(c,shop,base)
    assert second['release_id']==job['release_id']
    assert database().data_releases.count_documents({})==1 and database().retail_snapshots.count_documents({})==2

def test_failed_feed_retains_good_release_and_new_generation_publishes_correction():
    c,shop,b=setup();s,rows=sales(c,shop,b,'store');base,p=configure(c,shop,sales=[s['id']])
    first=publish(c,shop,base);good=service.active(shop['id'])
    old_total=service.report(shop['id'])['totals']['gross_units']
    bad=push(c,b,s,[{**rows[0],'units':-1}]); assert bad['status']=='complete_with_issues'
    failed=publish(c,shop,base);assert failed['status']=='failed'
    assert service.active(shop['id'])['id']==good['id'] and service.report(shop['id'])['totals']['gross_units']==old_total
    changed={**rows[0],'units':rows[0]['units']+5,'SPEND':(rows[0]['units']+5)*rows[0]['price']}
    push(c,b,s,[changed]);next_job=publish(c,shop,base)
    assert next_job['status']=='complete' and next_job['release_id']!=first['release_id']
    assert service.report(shop['id'])['totals']['gross_units']==old_total+5
    assert service.records(good,'sales')[0]['units']==rows[0]['units']

def test_shop_access_and_published_visibility_boundaries():
    c,shop,b=setup();s=source(c,b);push(c,b,s,[dict(id='A',name='Apples',currency='INR')])
    base,p=configure(c,shop,catalog=[s['id']]);job=publish(c,shop,base);assert job['status']=='complete',job
    stranger,_=identity(tenant()[0]);viewer,_=identity(shop['retailer_id'],role='viewer')
    for path in ('/overview','/contracts','/analytics','/catalog','/releases/'+job['release_id']):
        assert stranger.get(base+path).status_code==403
        assert viewer.get(base+path).status_code==200
    assert viewer.post(base+'/publish',json={}).status_code==403
    assert viewer.post(base+'/subscription',json={'catalog':[s['id']]}).status_code==403
    # Published data is explicitly shared. Do not allow a misleading privacy toggle.
    assert c.post(b+f'/sources/{s["id"]}/configure',json={**body(s),'visibility':'restricted'}).status_code==409
    restricted=source(c,b,visibility='restricted')
    assert c.post(base+'/subscription',json={'revision':p['revision'],'catalog':[restricted['id']]}).status_code==409
    key=c.post(f'/api/retail/retailers/{shop["retailer_id"]}/keys',json={'name':'Feed writer'}).json()['token']
    from fastapi.testclient import TestClient
    from promo.api import app
    adapter=TestClient(app,headers={'Authorization':'Bearer '+key})
    assert adapter.get(base+'/overview').status_code==403
    assert adapter.post(base+'/publish',json={}).status_code==403

def test_retry_after_consumer_failure_reuses_frozen_rows(monkeypatch):
    c,shop,b=setup();s,_=sales(c,shop,b,'store');base,_=configure(c,shop,sales=[s['id']])
    deliver=service.deliver
    monkeypatch.setattr(service,'deliver',lambda *_: (_ for _ in ()).throw(ValueError('Consumer unavailable')))
    failed=publish(c,shop,base);assert failed['status']=='failed' and failed['release_id']
    assert c.post(base+'/jobs/'+failed['id']+'/retry',json={}).status_code==200
    monkeypatch.setattr(service,'deliver',deliver);assert service.process_one(shop['id'])
    finished=database().data_jobs.find_one({'_id':failed['id']})
    assert finished['status']=='complete' and finished['release_id']==failed['release_id']
    assert database().data_releases.count_documents({})==1

def test_newer_generation_cannot_be_overwritten_and_auto_outbox_delivers(monkeypatch):
    c,shop,b=setup();s,_=sales(c,shop,b,'store');base,p=configure(c,shop,sales=[s['id']])
    deliver=service.deliver
    def changed(release,config,actor,heartbeat):
        result=deliver(release,config,actor,heartbeat)
        database().data_products.update_one({'_id':shop['id']},{'$inc':{'generation':1},'$set':{'config.auto_publish':True,'due_at':now()}})
        return result
    monkeypatch.setattr(service,'deliver',changed)
    job=publish(c,shop,base);assert job['status']=='superseded'
    assert database().data_products.find_one({'_id':shop['id']}).get('active_release') is None
    monkeypatch.setattr(service,'deliver',deliver)
    assert service.schedule_one(shop['id']);assert service.process_one(shop['id'])
    assert service.active(shop['id'])['id']==job['release_id']
    assert not service.schedule_one(shop['id'])
    push(c,b,s,[dict(id='new',**records(shop['store_code'],count=8,products=1,frequency='daily')[-1])])
    p=database().data_products.find_one({'_id':shop['id']})
    assert p['dirty'] and p['generation']>job['generation']

def test_expired_worker_cannot_publish():
    c,shop,b=setup();s=source(c,b);push(c,b,s,[dict(id='A',name='Apples')]);base,_=configure(c,shop,catalog=[s['id']])
    job=publish(c,shop,base);release=service.active(shop['id'])
    with pytest.raises(RuntimeError,match='lease'):service.publish({**job,'worker':'stale-worker'},release)

def test_inventory_requires_matching_origin_cost_and_identity():
    c,shop,b=setup();s=source(c,b,'InventoryPosition')
    row=dict(id='stock-A',product_id='A',on_hand=8.,unit_cost=60.,currency='INR',captured_at='2025-01-07T23:59:00+05:30',lead_time_days=4)
    push(c,b,s,[row]);base,_=configure(c,shop,inventory=[s['id']]);job=publish(c,shop,base);assert job['status']=='complete',job
    pos=service.stock_position(shop['id'],'2025-01-07');assert pos['positions'][0]['lead_time_days']==4 and pos['positions'][0]['unit_cost']==60
    from fastapi import HTTPException
    with pytest.raises(HTTPException,match='2025-01-08'):service.stock_position(shop['id'],'2025-01-08')
    push(c,b,s,[{**row,'on_hand':-2}]);assert publish(c,shop,base)['status']=='failed'
    assert service.stock_position(shop['id'],'2025-01-07')['positions'][0]['on_hand']==8

def test_channel_coverage_and_reference_disagreement_are_not_guessed():
    _,shop,_=setup();rows=records(count=2,products=1,frequency='daily');config={'sales':['a','b'],'sales_overlap':'disjoint_channels'}
    objects=[dict(source_id=s,properties=r) for s in ('a','b') for r in rows]
    assert len(merge_sales(objects,config,shop))==2
    with pytest.raises(ValueError,match='coverage'):merge_sales(objects[:-1],config,shop)
    objects[-1]['properties']={**rows[-1],'base_price':99.}
    with pytest.raises(ValueError,match='reference'):merge_sales(objects,config,shop)

def test_weekly_rollup_preserves_ex_tax_revenue_returns_and_partial_week_coverage():
    rows=records(count=15,products=1,frequency='daily')
    for i,r in enumerate(rows):
        r.update(returned_units=1.,refund_ex_gst=2.,unit_cost=40.+i)
    weekly,omitted=weekly_sales(rows)
    assert len(weekly)==2 and omitted==1
    selected=rows[1:]
    assert sum(r['SPEND'] for r in weekly)==sum(r['SPEND'] for r in selected)
    assert sum(r['refund_ex_gst'] for r in weekly)==28
    assert sum(r['units']*r['unit_cost'] for r in weekly)==pytest.approx(sum(r['units']*r['unit_cost'] for r in selected))

def test_missing_refunds_and_costs_are_unknown_not_fabricated_profit():
    row=records(count=1,products=1)[0];row.pop('unit_cost')
    r=analytics([row],[],[],'INR')
    assert r['totals']['net_revenue'] is None and r['totals']['refunds'] is None and r['totals']['gross_cogs'] is None

def test_release_checksum_failure_is_detected():
    c,shop,b=setup();s=source(c,b);push(c,b,s,[dict(id='A',name='Apples')]);base,_=configure(c,shop,catalog=[s['id']]);publish(c,shop,base)
    database().data_chunks.update_one({'domain':'source_catalog'},{'$set':{'records.0.properties.name':'Tampered'}})
    with pytest.raises(ValueError,match='checksum'):service.report(shop['id'])

def test_native_onesales_sales_adapter_pages_completed_history(monkeypatch):
    _,shop,_=setup();db=database();db.retail_shops.update_one({'_id':shop['id']},{'$set':{'pos_store_id':'native-test'}})
    rows=records(shop['store_code'],count=3,products=1,frequency='daily')
    for r in rows: db.pos_daily.insert_one(dict(store_id='native-test',product_id=r['product_id'],business_date=r['date'],body=r))
    monkeypatch.setattr(connectors,'PAGE_SIZE',2)
    s=dict(workspace=shop['id'],object_type='SalesObservation',kind='onesales')
    first,cursor=connectors.fetch_page(s);second,end=connectors.fetch_page(s,cursor)
    assert len(first)==2 and len(second)==1 and end is None
    assert first[0]['store_id']==shop['store_code'] and first[0]['units']==rows[0]['units']


@pytest.mark.parametrize('compact,observations',[(False,9600),(True,900)])
def test_demo_powers_both_workbenches_from_two_simulated_channels(compact,observations):
    from promo.connected import demo as connected_demo
    from promo.data_platform import demo
    from test_connected_demo import public_packets
    connected_demo.provision(packets=public_packets(),as_of=now().date(),progress=lambda _:None)
    result=demo.provision(progress=lambda _:None,compact=compact)
    assert result['observations']==observations
    assert set(result['consumers'])=={'demand','promotion'}
    assert all(c['status']=='ready' and c['run_id'] for c in result['consumers'].values())
    assert database().retail_runs.count_documents({'workspace':connected_demo.WORKSPACE,'status':'queued'})==2
    assert database().pos_sales.count_documents({})==0
    repeat=demo.provision(progress=lambda _:None,compact=compact)
    assert repeat==result
    assert database().retail_runs.count_documents({'workspace':connected_demo.WORKSPACE})==2
