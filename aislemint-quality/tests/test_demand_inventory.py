from promo.storage import AssetPath
import io
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from promo.demand import (DemandEngine, DemandRequest, examples, history_states,
                          DemandBundle, calibrate_residual_paths,
                          load_demand_panel, origin_frame, schedule_features,
                          train_demand, validate_demand_panel)
from promo.inventory import InventoryRequest, StockPosition, replenishment_plan, simulate
import promo.api as service
import promo.demand_api as demand_api


@pytest.fixture(scope='module')
def daily_panel():
    """Synthetic history exercises daily plumbing; it is not model-accuracy evidence."""
    rng=np.random.default_rng(4)
    dates=pd.date_range('2024-01-01',periods=240)
    records=[]
    for store in range(5):
        for product in range(2):
            for t,date in enumerate(dates):
                discount=.2 if t%23<3 else 0
                level=12+store*2+product*8+5*(date.dayofweek>=5)+20*discount
                records.append(dict(date=date,store_id=str(store),product_id=str(product),
                    category='Grocery',name=f'Product {product}',units=float(rng.poisson(level)),
                    price=5*(1-discount),base_price=5))
    return pd.DataFrame(records)


def test_daily_contract_and_missing_observations(daily_panel):
    d,q=validate_demand_panel(daily_panel.drop(index=[20]))
    assert q['frequency']=='daily' and q['missing_periods']==1
    assert not q['availability_supplied'] and not q['holiday_supplied']
    assert q['annual_history_limited']
    assert not ((d.store_id=='0')&(d.product_id=='0')&(d.date==pd.Timestamp('2024-01-21'))).any()


@pytest.mark.parametrize('change', ['duplicate','calendar','price','stockout','short'])
def test_daily_contract_rejects_invalid_input(daily_panel,change):
    d=daily_panel.copy()
    if change=='duplicate':d=pd.concat([d,d.iloc[:1]])
    if change=='calendar':d=d[d.date!=d.date.min()+pd.Timedelta(days=10)]
    if change=='price':d.loc[0,'price']=-1
    if change=='stockout':d['stockout']=2
    if change=='short':d=d[d.date<d.date.min()+pd.Timedelta(days=100)]
    with pytest.raises(ValueError):validate_demand_panel(d)


def test_forecast_lags_never_read_sales_after_origin(daily_panel):
    d,_=validate_demand_panel(daily_panel)
    origin=d.date.max()-pd.Timedelta(days=28)
    original=origin_frame(d,history_states(d,1),1,origin,28)
    modified=d.copy();modified.loc[modified.date>origin,'units']=999999
    changed=origin_frame(modified,history_states(modified,1),1,origin,28)
    cols=['last_units','mean_4','mean_13','mean_28','seasonal_reference','lag_year','std_13']
    pd.testing.assert_frame_equal(original[cols],changed[cols])
    assert changed.units.max()==999999
    training=examples(d,history_states(d,1),1,[1,7,28])
    row=training[(training.store_id=='0')&(training.product_id=='0')&(training.horizon==7)].iloc[-1]
    expected=d[(d.store_id=='0')&(d.product_id=='0')&(d.date<=row.origin)].tail(4).units.mean()
    assert row.mean_4==pytest.approx(expected)


def test_stockouts_are_censored_in_history_and_training(daily_panel):
    d,_=validate_demand_panel(daily_panel)
    d.loc[(d.date==d.date.max())&(d.store_id=='0')&(d.product_id=='0'),['stockout','units']]=[1,0]
    states=history_states(d,1)
    assert np.isnan(states.loc[('0','0',d.date.max()),'last_units'])
    training=examples(d,states,1,[1])
    assert not training.stockout.any()


def test_scheduled_post_offer_features_follow_planned_calendar(daily_panel):
    d,_=validate_demand_panel(daily_panel)
    origin=d.date.max()
    future=origin_frame(d,history_states(d,1),1,origin,7,known_schedule=False)
    changed=future.copy()
    changed.loc[(changed.horizon==1)&(changed.product_id=='0'),'discount']=.4
    cols=['store_id','product_id','date','category','discount','feature','display']
    with_offer=schedule_features(changed,pd.concat([d[cols],changed[cols]]),1)
    own=with_offer[(with_offer.product_id=='0')&(with_offer.horizon==2)]
    peer=with_offer[(with_offer.product_id=='1')&(with_offer.horizon==2)]
    assert (own.offer_lag_1==.4).all() and (peer.peer_lag_1==.4).all()
    pd.testing.assert_series_equal(future.mean_13,with_offer.mean_13)


@pytest.fixture(scope='module')
def trained_daily(tmp_path_factory,daily_panel):
    root=tmp_path_factory.mktemp('demand-daily')
    d,q=validate_demand_panel(daily_panel)
    d.to_parquet(root/'panel.parquet',index=False)
    (root/'quality.json').write_text(json.dumps(q))
    card=train_demand(root/'panel.parquet',root/'artifacts',include_xgboost=False)
    return root,card


def test_daily_training_selection_and_serving(trained_daily):
    root,card=trained_daily
    assert card['weights']==min(card['candidates'],key=lambda c:c['selection']['wape'])['weights']
    assert card['train_end']==card['selection_origins'][0]<card['calibration_origins'][0]<card['calibration_origins'][1]<card['test_origin']
    assert card['uncertainty_protocol']=='separate-selection-absolute-paths-v2'
    assert not card['quality']['availability_supplied'] and not card['quality']['holiday_supplied']
    assert card['train_end']<card['test_origin']<card['data_end']
    assert len(card['horizon_metrics'])==28
    engine=DemandEngine(root/'panel.parquet',root/'artifacts')
    req=DemandRequest(store_id='0',product_ids=['0'],horizon=14)
    result,paths=engine.forecast(req,include_paths=True)
    assert len(result['series'][0]['rows'])==14 and paths[('0','0')].shape==(400,14)
    assert (paths[('0','0')]>=0).all()
    assert result['as_of']==card['data_end']
    assert result['series'][0]['rows'][0]['date']==str((pd.Timestamp(card['data_end'])+pd.Timedelta(days=1)).date())
    assert result==engine.forecast(req)
    with pytest.raises(ValueError,match='already trained'):train_demand(root/'panel.parquet',root/'artifacts',False)


def test_absolute_calibration_keeps_tails_and_whole_path_dependence():
    # Entirely negative historical errors must not put the upper band below the forecast.
    blocks=-np.arange(1.,21.)[:,None]*np.array([[1.,2.,3.]])
    bank,radius=calibrate_residual_paths(blocks)
    np.testing.assert_array_equal(radius,[19.,38.,57.])
    np.testing.assert_allclose(np.quantile(bank,[.05,.95],axis=0),[-radius,radius])
    np.testing.assert_allclose(bank[:,1],2*bank[:,0])
    np.testing.assert_allclose(bank[:,2],3*bank[:,0])
    bundle=DemandBundle({}, {},7);bundle.residuals={'__pooled__':bank}
    bundle.residual_radius_90={'__pooled__':radius}
    lo,hi=bundle.interval([100.,100.],'unseen category')
    np.testing.assert_array_equal(lo,[0.,0.])
    np.testing.assert_array_equal(hi,[290.,480.])
    # Exact intervals do not depend on a Monte Carlo seed or requested prefix length.
    np.testing.assert_array_equal(bundle.interval([100.],'unseen category')[1],hi[:1])
    assert (bundle.paths([100.,100.],'unseen category')>=0).all()
    with pytest.raises(ValueError,match='20 complete'):calibrate_residual_paths(blocks[:19])
    broken=blocks.copy();broken[0,0]=np.nan
    with pytest.raises(ValueError,match='finite'):calibrate_residual_paths(broken)


def test_legacy_demand_interval_preserves_original_paths():
    bundle=DemandBundle({}, {},7)
    bundle.residuals={'__pooled__':np.arange(20.)[:,None]}
    np.testing.assert_array_equal(bundle.interval([100.],'legacy',seed=987),
                                  np.quantile(bundle.paths([100.],'legacy',seed=987),[.05,.95],axis=0))


@pytest.mark.parametrize('window',['calibration','test'])
def test_later_outcomes_do_not_choose_model_or_fit_test_intervals(trained_daily,tmp_path,window):
    from promo.storage import load_model
    root,card=trained_daily
    d=pd.read_parquet(root/'panel.parquet')
    start=card['calibration_origins'][0] if window=='calibration' else card['test_origin']
    # Change only sales observed after model selection, retaining schedules and history.
    d.loc[d.date>pd.Timestamp(start),'units']+=1000
    d.to_parquet(tmp_path/'panel.parquet',index=False)
    (tmp_path/'quality.json').write_text((root/'quality.json').read_text())
    changed=train_demand(tmp_path/'panel.parquet',tmp_path/'artifacts',include_xgboost=False)
    assert changed['weights']==card['weights']
    assert changed['selection']==card['selection']
    before=load_model(root/'artifacts'/'model.joblib')
    after=load_model(tmp_path/'artifacts'/'model.joblib')
    if window=='test':
        assert changed['test']!=card['test']
        for key in before.residuals:
            np.testing.assert_array_equal(after.residuals[key],before.residuals[key])
            np.testing.assert_array_equal(after.residual_radius_90[key],before.residual_radius_90[key])
    else:
        assert changed['calibration']!=card['calibration']

def test_weekly_promotion_covers_all_daily_dates(trained_daily):
    root,_=trained_daily
    from promo.offer_mechanics import OfferTerms
    engine=DemandEngine(root/'panel.parquet',root/'artifacts')
    period_end=str((engine.last+pd.Timedelta(days=7)).date())
    engine.calendar_provider=lambda *_:dict(revision=1,campaigns=[],events=[dict(campaign_id='week',store_id='0',product_id='0',period_end=period_end,terms=OfferTerms(product_id='0',discount=.2).model_dump())])
    result=engine.forecast(DemandRequest(store_id='0',product_ids=['0'],horizon=10))
    rows=result['series'][0]['rows']
    assert [round(r['price']/r['regular_price'],2) for r in rows]==[.8]*7+[1.]*3


class FixedDemand:
    def __init__(self,paths,products=('A',),step=7,price=5):
        self.paths=np.asarray(paths,dtype=float)
        if self.paths.ndim==1:self.paths=np.tile(self.paths,(400,1))
        self.products=products;self.step=step;self.price=price

    def forecast(self,req,include_paths=False):
        series=[];bank={};horizon=self.paths.shape[1]
        for pid in self.products:
            point=self.paths.mean(axis=0)
            series.append(dict(store_id='S',product_id=pid,name=pid,rows=[dict(
                date=str((pd.Timestamp('2024-01-01')+pd.Timedelta(days=(t+1)*self.step)).date()),
                forecast=float(q),price=self.price,regular_price=self.price) for t,q in enumerate(point)]))
            bank[('S',pid)]=self.paths
        result=dict(series=series,as_of='2024-01-01',step_days=self.step,horizon=horizon,
                    frequency='daily' if self.step==1 else 'weekly',model_id='fixed-test-only')
        return (result,bank) if include_paths else result


def stock(**overrides):
    return StockPosition(**dict(store_id='S',product_id='A',on_hand=0,unit_cost=2,
                               lead_time_days=7,review_period_days=7,**overrides))


def request(positions=None,**overrides):
    return InventoryRequest(forecast=DemandRequest(store_id='S',product_ids=['A'],horizon=4),
                            stock_as_of='2024-01-01',positions=positions or [stock()],**overrides)


def test_late_delivery_does_not_erase_early_lost_sales_or_double_order():
    r=replenishment_plan(FixedDemand([10,10,10,10]),request())['rows'][0]
    assert r['order_quantity']==10
    assert r['lead_time_shortfall']==10 and r['expected_shortfall']==10
    assert r['post_arrival_service']==1 and r['estimated_cycle_service']==0
    assert r['projection'][0]['expected_lost_sales']==10
    assert r['projection'][1]['expected_lost_sales']==0
    assert r['arrival_date']=='2024-01-08'


def test_late_po_is_not_subtracted_as_if_it_were_available_today():
    p=stock(receipts=[dict(quantity=100,due_date='2024-01-22')])
    r=replenishment_plan(FixedDemand([10,10,10,10]),request([p]))['rows'][0]
    assert r['order_quantity']==10 and r['inventory_position']==0
    assert r['projection'][0]['expected_lost_sales']==10
    assert r['projection'][3]['ending_stock']==90


def test_commitments_settled_once_and_new_lost_sales_are_not_backlogged():
    p=stock(reserved=3,backlog=4)
    sim=simulate(np.array([[10.,10.,10.]]),p,pd.Timestamp('2024-01-01'),7,30,1)
    np.testing.assert_array_equal(sim['lost'],[[10,0,0]])
    np.testing.assert_array_equal(sim['pending'],[[7,0,0]])
    np.testing.assert_array_equal(sim['ending'],[[0,13,3]])


def test_fifo_expiry_and_stock_flow_balance():
    p=StockPosition(store_id='S',product_id='A',on_hand=10,unit_cost=2,
        existing_expiry_date='2024-01-03',shelf_life_days=3,receipts=[dict(quantity=5,due_date='2024-01-02')])
    paths=np.array([[3.,4.,8.,8.]])
    sim=simulate(paths,p,pd.Timestamp('2024-01-01'),1,7,1)
    fulfilled=(paths-sim['lost']).sum()
    assert fulfilled+sim['waste'].sum()+sim['ending'][0,-1]==pytest.approx(22)
    assert sim['waste'][0,2]==3
    assert sim['lost'][0,3]==4


def test_case_pack_minimum_budget_and_capacity_constraints():
    pos=StockPosition(store_id='S',product_id='A',on_hand=0,unit_cost=2,
        lead_time_days=0,review_period_days=14,case_pack=6,minimum_order=12,max_stock=19)
    r=replenishment_plan(FixedDemand([10,10,10,10]),request([pos],budget=30))
    assert r['rows'][0]['order_quantity']==12 and r['budget_used']==24
    r=replenishment_plan(FixedDemand([10,10,10,10]),request([pos],budget=0))
    assert r['orders']==0 and r['budget_used']==0 and r['rows'][0]['expected_shortfall']==20


def test_portfolio_budget_is_shared():
    positions=[StockPosition(store_id='S',product_id=pid,on_hand=0,unit_cost=2,
                lead_time_days=0,review_period_days=14) for pid in ['A','B']]
    r=replenishment_plan(FixedDemand([10,10,10,10],products=['A','B']),request(positions,budget=50))
    assert len(r['rows'])==2 and r['budget_used']<=50
    assert sum(row['order_quantity'] for row in r['rows'])==25


def test_profit_mode_declines_negative_margin_and_does_not_double_charge_cogs():
    p=StockPosition(store_id='S',product_id='A',on_hand=0,unit_cost=2,lead_time_days=0)
    r=replenishment_plan(FixedDemand([10,10,10,10]),request([p],objective='profit',annual_holding_rate=0))
    assert r['rows'][0]['order_quantity']==10 and r['incremental_contribution']==30
    loss=replenishment_plan(FixedDemand([10,10,10,10],price=1),request([p],objective='profit'))
    assert loss['orders']==0


def test_expiry_can_make_service_target_unattainable():
    p=StockPosition(store_id='S',product_id='A',on_hand=0,unit_cost=2,
        lead_time_days=0,review_period_days=3,shelf_life_days=1)
    r=replenishment_plan(FixedDemand([10,10,10,10],step=1),request([p]))
    assert r['rows'][0]['post_arrival_service']==0
    assert any('target is not met' in note for note in r['rows'][0]['notes'])


def test_profit_includes_outstanding_commitments_fulfilled_from_new_stock():
    p=StockPosition(store_id='S',product_id='A',on_hand=0,backlog=7,unit_cost=2,lead_time_days=0)
    r=replenishment_plan(FixedDemand([0,0,0,0]),request([p],objective='profit',annual_holding_rate=0))
    assert r['rows'][0]['order_quantity']==7
    assert r['incremental_contribution']==21 and r['rows'][0]['post_arrival_service']==1


def test_service_target_accounts_for_uncertain_cumulative_demand():
    paths=np.repeat(np.arange(1,101,dtype=float)[:,None],4,axis=1)
    p=StockPosition(store_id='S',product_id='A',on_hand=0,unit_cost=2,lead_time_days=0,review_period_days=14)
    low=replenishment_plan(FixedDemand(paths),request([p],service_level=.5))['rows'][0]
    high=replenishment_plan(FixedDemand(paths),request([p],service_level=.95))['rows'][0]
    assert high['order_quantity']>=low['order_quantity']
    assert high['post_arrival_service']>=.95 and low['post_arrival_service']>=.5


@pytest.mark.parametrize('overrides',[
    {'receipts':[dict(quantity=5,due_date='2024-01-01')]},
    {'existing_expiry_date':'2024-01-02T00:00:00Z'},
    {'receipts':[dict(quantity=5,due_date='2024-01-02',expiry_date='NaT')]},
    {'shelf_life_days':2},
])
def test_inventory_rejects_ambiguous_or_unresolvable_timing(overrides):
    with pytest.raises(ValueError):replenishment_plan(FixedDemand([10]*4),request([stock(**overrides)]))


def test_snapshot_date_and_horizon_must_match():
    r=request();r.stock_as_of='2024-01-02'
    with pytest.raises(ValueError,match='snapshot date'):replenishment_plan(FixedDemand([10]*4),r)
    p=StockPosition(store_id='S',product_id='A',on_hand=0,unit_cost=2,lead_time_days=28)
    with pytest.raises(ValueError,match='cover lead time'):replenishment_plan(FixedDemand([10]*4),request([p]))


def test_import_forecast_inventory_save_export_and_isolation(tmp_path,monkeypatch,daily_panel,trained_daily):
    monkeypatch.setattr(service,'DATA',AssetPath('test/'+tmp_path.name))
    (tmp_path/'processed').mkdir()
    from promo.storage import write_frame
    write_frame(daily_panel,service.DATA/'processed/panel.parquet',index=False)
    demand_api.engine.cache_clear()
    client=TestClient(service.app)
    response=client.post('/api/integrations/snapshots',headers={'Authorization':'Bearer test-integration-key'},json=dict(name='Daily history',kind='demand',idempotency_key='daily-history-key',records=json.loads(daily_panel.to_json(orient='records',date_format='iso'))))
    assert response.status_code==200,response.text
    source=response.json()['id']
    assert client.get('/api/demand/catalog',params={'source_id':source,'store_id':'0'}).status_code==409
    root,card=trained_daily
    # Install the already tested model through the actual background-training job path.
    def trainer(panel_path,output_dir):
        import joblib,hashlib
        output_dir.mkdir(parents=True,exist_ok=True)
        artifact=(root/'artifacts/model.joblib').read_bytes()
        (output_dir/'model.joblib').write_bytes(artifact)
        copied={**card,'panel_sha256':hashlib.sha256(panel_path.read_bytes()).hexdigest()}
        (output_dir/'model_card.json').write_text(json.dumps(copied))
        return copied
    monkeypatch.setattr(demand_api,'train_demand',trainer)
    assert client.post('/api/demand/train',json={'source_id':source}).status_code==200
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        status=next(s for s in client.get('/api/demand/sources').json() if s['id']==source)['status']
        if status!='training':break
        time.sleep(.05)
    assert status=='ready'
    summary=client.get('/api/demand/summary',params={'source_id':source}).json()
    assert not summary['quality']['availability_supplied']
    assert client.post('/api/demand/train',json={'source_id':source}).status_code==409
    forecast=dict(source_id=source,store_id='0',product_ids=['0'],horizon=7)
    assert client.post('/api/demand/forecast',json=forecast).status_code==200
    forecast_csv=client.post('/api/demand/forecast-export',json=forecast)
    assert forecast_csv.status_code==200
    exported_forecast=pd.read_csv(io.StringIO(forecast_csv.text))
    assert len(exported_forecast)==7 and (exported_forecast.forecast>=0).all()
    body=dict(forecast=forecast,stock_as_of=card['data_end'],positions=[dict(store_id='0',product_id='0',on_hand=20,unit_cost=2,lead_time_days=2,review_period_days=2)],name='Client inventory decision')
    calculated=client.post('/api/demand/inventory',json=body)
    assert calculated.status_code==200,calculated.text
    saved=client.post('/api/demand/plans',json=body)
    assert saved.status_code==200,saved.text
    assert saved.json()['result']==calculated.json()
    id=saved.json()['id']
    plans=client.get('/api/demand/plans',params={'source_id':source}).json()
    assert len(plans)==1 and plans[0]['request']==InventoryRequest(**body).model_dump()
    exported=client.get(f'/api/demand/plans/{id}/export',params={'source_id':source})
    assert exported.status_code==200 and 'order_quantity' in exported.text
    assert client.get(f'/api/demand/plans/{id}/export',params={'source_id':'demand_'+'a'*32}).status_code==404
    assert client.get('/api/demand/summary',params={'source_id':'../../etc'}).status_code==404
    assert client.post('/api/demand/inventory',json=body,headers={'Origin':'https://attacker.example'}).status_code==403
    bad={**body,'stock_as_of':'2020-01-01'}
    assert client.post('/api/demand/plans',json=bad).status_code==422
    assert len(client.get('/api/demand/plans',params={'source_id':source}).json())==1
    demand_api.engine.cache_clear()
