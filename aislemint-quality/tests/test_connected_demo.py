"""Demo provisioning must be isolated, reproducible and honest about public inputs."""
from datetime import date
from zoneinfo import ZoneInfo
import pytest
from promo.pg import database
from promo.connected import demo, pipeline
from promo.connected.repository import digest, object_id
from promo.retail import auth
from test_retail_platform import tenant, identity


@pytest.fixture(autouse=True)
def real_account_boundary(monkeypatch):
    monkeypatch.setenv('AISLEMINT_TEST_AUTH_BYPASS','0')
    monkeypatch.delenv('AISLEMINT_LOCAL_AI_MODEL',raising=False)


def public_packets():
    payloads = {
        'open_food_facts': {'products': [dict(code=str(8900000000000+i), product_name=f'Public biscuit {i}', brands='Fixture brand', categories='Biscuits') for i in range(12)]},
        'dummyjson': {'products': [dict(id=i, title=f'Fictional grocery {i}', category='groceries') for i in range(12)]},
        'nasa_power': {'header': {'fill_value': -999, 'api': {'version': 'test'}},
            'parameters': {'T2M': {'units': 'C'}, 'PRECTOTCORR': {'units': 'mm/day'}},
            'properties': {'parameter': {'T2M': {'20260801': 25.5, '20260802': -999}, 'PRECTOTCORR': {'20260801': 5.5, '20260802': -999}}}},
    }
    return {key: {**metadata, 'url': metadata.get('url', 'https://power.larc.nasa.gov/api/test-fixture'),
        'fetched_at': '2026-08-03T10:00:00+00:00', 'payload': payloads[key], 'sha256': digest(payloads[key])} for key, metadata in demo.PUBLIC.items()}


def test_public_data_remains_attributed_and_missing_weather_is_not_invented():
    packets=public_packets(); scenario=demo.build_scenario(packets,date(2026,8,4))
    groups={g[0]:g for g in scenario['groups']}
    weather=groups['weather'][3]
    assert len(weather)==1 and weather[0]['temperature_c']==25.5 and weather[0]['precipitation_mm']==5.5
    assert groups['erp_products'][3][0]['id']!=groups['erp_products'][3][12]['id']
    assert groups['open_food_facts'][3][0]['license']==demo.PUBLIC['open_food_facts']['license']
    assert 'fictional' in groups['dummyjson'][3][0]['data_kind'].lower()
    packets['nasa_power']['payload']['properties']['parameter']['T2M']['20260801']=-999
    with pytest.raises(ValueError,match='no usable days'):demo.build_scenario(packets,date(2026,8,4))
    packets=public_packets();packets['open_food_facts']['payload']['products']=[]
    with pytest.raises(ValueError,match='No usable open_food_facts'):demo.build_scenario(packets,date(2026,8,4))


def test_demo_provision_and_resume_do_not_touch_customer_models_or_permissions(monkeypatch):
    retailer,shop=tenant();owner,_=identity(retailer);db=database()
    before_users=list(db.retail_users.find({}));before_shop=db.retail_shops.find_one({'_id':shop['id']})
    first=demo.provision(packets=public_packets(),as_of=demo.now().astimezone(ZoneInfo('Asia/Kolkata')).date(),progress=lambda _:None)
    assert first['sources']==11 and first['quarantine']==1
    assert db.retail_shops.find_one({'_id':shop['id']})==before_shop
    assert list(db.retail_users.find({}))==before_users
    assert db.retail_runs.count_documents({})==0 and db.pos_sales.count_documents({})==0
    assert owner.get(f'/api/retail/shops/{demo.WORKSPACE}/connected/overview').status_code==403
    run_count=db.connect_runs.count_documents({});version_count=db.connect_versions.count_documents({})
    decision=db.connect_decisions.find_one({'workspace':demo.WORKSPACE})
    db.connect_decisions.update_one({'_id':decision['_id']},{'$set':{'status':'rejected'}})
    monkeypatch.setattr(demo,'fetch_public',lambda *_:pytest.fail('Resume must reuse MongoDB snapshots'))
    second=demo.provision(progress=lambda _:None)
    assert first==second
    assert db.connect_runs.count_documents({})==run_count and db.connect_versions.count_documents({})==version_count
    assert db.connect_decisions.find_one({'_id':decision['_id']})['status']=='rejected'
    token=auth.principal.set({'id':'demo-test-owner','admin':False,'memberships':{demo.RETAILER:'owner'}})
    try:
        overview=demo.api.overview(demo.WORKSPACE)
        assert overview['risks']['counts']=={'low_stock':6,'overdue':8}
        assert len(overview['demo']['stories'])==3
        stock=db.connect_sources.find_one({'workspace':demo.WORKSPACE,'object_type':'InventoryPosition'})
        db.connect_sources.update_one({'_id':stock['id']},{'$set':{'visibility':'restricted'}})
    finally:auth.principal.reset(token)
    token=auth.principal.set({'id':'demo-test-viewer','admin':False,'memberships':{demo.RETAILER:'viewer'}})
    try:
        overview=demo.api.overview(demo.WORKSPACE)
        assert len(overview['demo']['stories'])==2 and overview['risks']['counts']['low_stock']==0
    finally:auth.principal.reset(token)


def test_demo_namespace_collision_fails_without_overwriting_a_customer():
    db=database();db.retail_shops.insert_one({'_id':demo.WORKSPACE,'id':demo.WORKSPACE,'name':'Existing real shop','retailer_id':'customer'})
    before=db.retail_shops.find_one({'_id':demo.WORKSPACE})
    with pytest.raises(ValueError,match='occupied'):demo.ensure_workspace()
    assert db.retail_shops.find_one({'_id':demo.WORKSPACE})==before
    assert db.retailers.find_one({'_id':demo.RETAILER}) is None


def test_scoped_demo_worker_never_claims_another_shops_pending_run():
    db=database();db.connect_runs.insert_one({'_id':'unrelated','source_id':'unrelated','request_key':'unrelated','workspace':'customer','status':'queued','created':demo.now()})
    assert pipeline.process_one(demo.WORKSPACE) is False
    assert db.connect_runs.find_one({'_id':'unrelated'})['status']=='queued'
