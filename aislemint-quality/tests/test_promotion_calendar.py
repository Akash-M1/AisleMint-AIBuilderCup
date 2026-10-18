from promo.storage import AssetPath
import copy
import hashlib
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient
import promo.api as service
import promo.demand_api as demand_api
from promo.holidays import SOURCE,us_calendar


def test_supplied_holiday_file_is_preserved_and_checked():
    cal=us_calendar();meta=cal.metadata
    assert meta['supplied_rows']==342 and meta['corrected_metadata_rows']==6
    assert meta['rule_differences']==[]
    assert meta['source_sha256']==hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    assert len([e for e in cal.events if e['origin']=='supplied_csv'])==342


def test_us_observance_and_retail_dates_are_separate_and_future_rules_work():
    events=us_calendar().events
    assert any(e['date']=='2021-07-04' and e['name']=='4th of July' and e['kind']=='occasion' for e in events)
    assert any(e['date']=='2021-07-05' and e['kind']=='federal_observed' for e in events)
    assert any(e['date']=='2021-06-18' and e['kind']=='federal_observed' and 'Juneteenth' in e['name'] for e in events)
    assert not any(e['date'].startswith('2010') and e['kind']=='federal_observed' and 'Juneteenth' in e['name'] for e in events)
    assert any(e['date']=='2026-11-26' and e['name']=='Thanksgiving Day' and e['origin']=='calendar_rule' for e in events)


def test_holidays_align_to_week_ending_periods_and_adjacent_features():
    dates=pd.Series(pd.to_datetime(['2012-12-19','2012-12-26','2013-01-02']))
    f=us_calendar().features(dates,7)
    assert f.iloc[0].us_christmas_day==0 and f.iloc[0].us_holiday_before==1
    assert f.iloc[1].us_christmas_day==1 and f.iloc[1].us_christmas_eve==1
    assert f.iloc[2].us_holiday_after==1
    daily=us_calendar().features(pd.Series(pd.to_datetime(['2026-11-25','2026-11-26'])),1)
    assert daily.iloc[0].us_thanksgiving_day==0 and daily.iloc[1].us_thanksgiving_day==1


@pytest.fixture
def integrated(tmp_path,monkeypatch,engine):
    source=service.DATA/'processed/panel.parquet'
    (tmp_path/'processed').mkdir()
    (AssetPath('test/'+tmp_path.name)/'processed/panel.parquet').write_bytes(source.read_bytes())
    monkeypatch.setattr(service,'DATA',AssetPath('test/'+tmp_path.name))
    monkeypatch.setattr(service,'get_engine',lambda workspace='benchmark':engine)
    demand_api.engine.cache_clear()
    yield TestClient(service.app)
    demand_api.engine.cache_clear()


def reviewed(client,products=None,start='2012-01-11'):
    body=dict(store_id='367',start_date=start,name='Test confirmed campaign',promotions=[
        dict(product_id=pid,discount=.2,duration=2) for pid in (products or ['3800031838'])])
    forecast=client.post('/api/forecast',json=body)
    assert forecast.status_code==200,forecast.text
    return dict(scenario=body,review_token=forecast.json()['confirmation_token'],idempotency_key='test-confirmation-123')


def test_confirmation_flows_to_demand_inventory_and_export(integrated):
    client=integrated
    demand=dict(source_id='benchmark',store_id='367',product_ids=['3800031838'],horizon=8)
    before=client.post('/api/demand/forecast',json=demand).json()
    draft=reviewed(client)
    # Draft review and save are not an operational confirmation.
    assert client.post('/api/scenarios',json=draft['scenario']).status_code==200
    assert client.get('/api/promotions').json()['events']==[]
    response=client.post('/api/promotions/confirm',json=draft)
    assert response.status_code==200,response.text
    promotion=response.json()['promotion'];id=promotion['id']
    after=client.post('/api/demand/forecast',json=demand).json()
    assert after['promotion_calendar']['campaign_ids']==[id]
    assert len(after['promotion_calendar']['events'])==2
    rows=after['series'][0]['rows']
    assert rows[0]['price']==pytest.approx(rows[0]['regular_price']*.8)
    assert rows[2]['price']==rows[2]['regular_price']
    assert after['total_units']!=before['total_units']
    what_if=client.post('/api/demand/forecast',json={**demand,'use_confirmed_promotions':False}).json()
    assert what_if['series']==before['series']
    other_store=client.post('/api/demand/forecast',json={**demand,'store_id':'387'})
    if other_store.status_code==200:assert other_store.json()['promotion_calendar']['events']==[]
    inventory=dict(forecast={**demand,'expected_calendar_revision':1},stock_as_of=after['as_of'],
        positions=[dict(store_id='367',product_id='3800031838',on_hand=50,unit_cost=2)],name='Confirmed stock plan')
    saved=client.post('/api/demand/plans',json=inventory)
    assert saved.status_code==200,saved.text
    assert saved.json()['result']['forecast']['promotion_calendar']['campaign_ids']==[id]
    assert client.get('/api/demand/plans/'+saved.json()['id']+'/export').status_code==200
    frozen=copy.deepcopy(saved.json()['result'])
    assert client.post('/api/promotions/'+id+'/cancel',json={}).status_code==200
    assert client.post('/api/demand/inventory',json=inventory).status_code==422
    restored=client.post('/api/demand/forecast',json=demand).json()
    assert restored['series']==before['series'] and restored['promotion_calendar']['events']==[]
    assert client.get('/api/demand/plans').json()[0]['result']==frozen
    assert client.get('/api/promotions').json()['campaigns'][0]['status']=='cancelled'


def test_confirmation_retry_overlap_and_stale_review_are_safe(integrated):
    client=integrated;draft=reviewed(client)
    response=client.post('/api/promotions/confirm',json=draft);assert response.status_code==200
    repeated=client.post('/api/promotions/confirm',json=draft)
    assert repeated.json()['replayed'] and repeated.json()['promotion']['id']==response.json()['promotion']['id']
    duplicate={**draft,'idempotency_key':'different-request-key'}
    assert client.post('/api/promotions/confirm',json=duplicate).status_code==409
    assert client.get('/api/promotions').json()['revision']==1
    modified=copy.deepcopy(duplicate);modified['scenario']['promotions'][0]['discount']=.3
    assert client.post('/api/promotions/confirm',json=modified).status_code==409
    modified['idempotency_key']=draft['idempotency_key']
    assert client.post('/api/promotions/confirm',json=modified).status_code==409
    bad=copy.deepcopy(draft);bad['review_token']='f'*64;bad['idempotency_key']='forged-review-test'
    assert client.post('/api/promotions/confirm',json=bad).status_code==409
    assert client.post('/api/promotions/confirm',json=draft,headers={'X-Workspace-ID':'../elsewhere'}).status_code==404
    assert client.post('/api/promotions/'+response.json()['promotion']['id']+'/cancel',json={},headers={'X-Workspace-ID':'f'*32}).status_code==404


def test_concurrent_confirmation_is_idempotent(integrated):
    draft=reviewed(integrated)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:integrated.post('/api/promotions/confirm',json=draft),range(2)))
    assert all(r.status_code==200 for r in responses)
    assert len({r.json()['promotion']['id'] for r in responses})==1
    assert integrated.get('/api/promotions').json()['revision']==1


def test_draft_overlap_is_rejected_and_out_of_horizon_offers_do_not_fail(integrated):
    client=integrated;draft=reviewed(client)
    assert client.post('/api/promotions/confirm',json=draft).status_code==200
    request=dict(store_id='367',product_ids=['3800031838'],horizon=1,
        promotions=[dict(product_id='3800031838',start_date='2012-01-11',duration=1,discount=.1)])
    assert client.post('/api/demand/forecast',json=request).status_code==422
    assert client.post('/api/demand/forecast',json={**request,'use_confirmed_promotions':False}).status_code==200
    request.pop('promotions')
    # Only the visible period is applied; the rest stays in the shared calendar.
    response=client.post('/api/demand/forecast',json=request)
    assert response.status_code==200 and len(response.json()['promotion_calendar']['events'])==1


def test_portfolio_confirmation_is_atomic_when_one_product_conflicts(integrated,engine):
    first=reviewed(integrated)
    other=next(p['product_id'] for p in engine.catalog('367') if p['product_id']!='3800031838')
    portfolio=reviewed(integrated,['3800031838',other]);portfolio['idempotency_key']='two-product-portfolio'
    assert integrated.post('/api/promotions/confirm',json=first).status_code==200
    assert integrated.post('/api/promotions/confirm',json=portfolio).status_code==409
    assert {e['product_id'] for e in integrated.get('/api/promotions').json()['events']}=={'3800031838'}


def test_all_store_confirmation_expands_to_real_locations(integrated):
    scenario=dict(store_id='all',start_date='2012-01-11',name='All-store promotion',promotions=[dict(product_id='3800031838',discount=.2,duration=1)])
    preview=integrated.post('/api/forecast',json=scenario);assert preview.status_code==200
    result=integrated.post('/api/promotions/confirm',json=dict(scenario=scenario,review_token=preview.json()['confirmation_token'],idempotency_key='all-store-calendar'))
    assert result.status_code==200,result.text
    events=integrated.get('/api/promotions').json()['events']
    assert len({e['store_id'] for e in events})>1 and all(e['store_id']!='all' for e in events)
    location=events[0]['store_id']
    scoped=integrated.get('/api/promotions',params={'store_id':location}).json()['events']
    assert len(scoped)==1 and scoped[0]['store_id']==location
