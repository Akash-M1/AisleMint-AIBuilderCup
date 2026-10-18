import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError
from promo.engine import Scenario,Promotion,OptimizeRequest
from promo.model import exposures

PID='3800031838'

def scenario(**kwargs):return Scenario(promotions=[Promotion(product_id=PID,**kwargs)])

def test_no_promotion_is_an_exact_zero_effect(engine):
    r=engine.forecast(scenario(discount=0))
    assert r['incremental_units']==pytest.approx(0,abs=1e-8)
    assert r['incremental_profit']==pytest.approx(0,abs=1e-8)
    assert r['portfolio_net_units']==pytest.approx(0,abs=1e-8)
    assert all(v==pytest.approx(0,abs=1e-8) for v in r['effects'].values())
    assert r['investment']==0 and r['roi'] is None

def test_effect_and_profit_accounting_reconcile(engine):
    r=engine.forecast(scenario(discount=.2,fixed_cost=12,funding_per_unit=.1))
    assert sum(r['effects'].values())==pytest.approx(r['portfolio_net_units'])
    assert sum(r['effect_profit'].values())==pytest.approx(r['incremental_profit'])
    assert r['planned_contribution']-r['baseline_contribution']==pytest.approx(r['incremental_profit'])
    assert r['total_units']-r['baseline_units']==pytest.approx(r['incremental_units'])
    assert r['roi']==pytest.approx(r['incremental_profit']/r['investment'])
    assert r['effects']['cannibalization']<=0 and r['effects']['halo']>=0
    assert r['effects']['pantry_loading']<=0 and r['effects']['post_promo_uplift']>=0
    assert sum(r['planning_effect_profit'].values())==pytest.approx(r['planning_profit'])
    assert r['planning_profit']<=r['incremental_profit']

def test_funding_and_fixed_cost_are_counted_once(engine):
    base=engine.forecast(scenario())
    funded=engine.forecast(scenario(funding_per_unit=.25,fixed_cost=10))
    assert funded['total_units']==base['total_units']
    assert funded['incremental_profit']-base['incremental_profit']==pytest.approx(base['total_units']*.25-10)
    assert funded['investment']-base['investment']==pytest.approx(10)

def test_inventory_checks_do_not_hide_unmet_demand(engine):
    r=engine.forecast(Scenario(inventory_units=1,promotions=[Promotion(product_id=PID)]))
    assert not r['feasible']
    assert r['inventory_shortfall']==pytest.approx(r['total_units']-1)
    assert r['total_units']>1

def test_unknown_products_and_bad_week_cadence_rejected(engine):
    with pytest.raises(ValueError,match='Unknown'):engine.forecast(Scenario(promotions=[Promotion(product_id='unknown')]))
    with pytest.raises(ValueError,match='weekly cadence'):engine.forecast(Scenario(start_date='2012-01-12',promotions=[Promotion(product_id=PID)]))
    with pytest.raises(ValueError,match='One promotion'):engine.forecast(Scenario(promotions=[Promotion(product_id=PID),Promotion(product_id=PID)]))

def test_nonfinite_money_and_unsupported_discount_rejected():
    for value in [float('inf'),float('nan'),-1]:
        with pytest.raises(ValidationError):Promotion(product_id=PID,fixed_cost=value)
    with pytest.raises(ValidationError):Promotion(product_id=PID,discount=.7)

def test_hindsight_never_uses_in_sample_rows(engine):
    h=engine.hindsight(PID)
    assert h['start']>engine.model.trained_until
    assert h['incremental']==pytest.approx(h['modeled_incremental']+h['unexplained'])
    assert sum(h['effects'].values())==pytest.approx(h['portfolio_net_units'])
    assert sum(s['actual'] for s in h['series'])==pytest.approx(h['actual'])
    assert h['effects']['pantry_loading']<=0 and h['effects']['post_promo_uplift']>=0
    assert h['effects']['direct']+h['effects']['pantry_loading']+h['effects']['post_promo_uplift']+h['unexplained']==pytest.approx(h['incremental'])

def test_zero_exposure_has_no_future_carryover(engine):
    base,planned,start=engine._future(scenario(discount=0))
    assert len(base.date.unique())==6
    assert base.date.max()==start+pd.Timedelta(weeks=5)
    pd.testing.assert_series_equal(base.discount,planned.discount)

def test_calendar_lags_do_not_jump_over_missing_weeks():
    d=pd.DataFrame({'store_id':['1']*3,'product_id':['a']*3,'category':['c']*3,
        'date':pd.to_datetime(['2020-01-01','2020-01-15','2020-01-22']),
        'discount':[.2,0,0],'feature':[0]*3,'display':[0]*3})
    x=exposures(d)
    assert np.isnan(x.loc[1,'lag_dose_1'])
    assert x.loc[1,'lag_dose_2']==.2
    assert (x['category_dose_c']==0).all()

def test_current_discount_monotonicity_for_same_product(engine):
    low=engine.forecast(scenario(discount=.1,duration=1))
    high=engine.forecast(scenario(discount=.3,duration=1))
    assert high['total_units']>=low['total_units']-1e-8

def test_prediction_bounds_and_portfolio_aggregation(engine):
    r=engine.forecast(Scenario(store_id='all',promotions=[Promotion(product_id=PID,discount=.15)]))
    assert all(0<=w['lower']<=w['forecast']<=w['upper'] for w in r['weeks'])
    assert sum(x['incremental_units'] for x in r['affected_products'])==pytest.approx(r['portfolio_net_units'],abs=.1)

def test_optimizer_returns_feasible_profitable_joint_portfolio(engine):
    req=OptimizeRequest(product_ids=[PID],margin_rate=.6,funding_per_unit=.5,budget=100,max_promotions=1)
    r=engine.optimize(req)
    assert r['selected'], 'Funded positive candidate should be discoverable'
    assert r['forecast']['incremental_profit']>0
    assert r['forecast']['planning_profit']>0
    assert r['forecast']['investment']<=req.budget+1e-6
    assert len(r['selected'])<=req.max_promotions
    assert len({c['promotion']['product_id'] for c in r['selected']})==len(r['selected'])

def test_zero_budget_retains_no_promotion_alternative(engine):
    r=engine.optimize(OptimizeRequest(product_ids=[PID],margin_rate=.6,funding_per_unit=.5,budget=0))
    assert r['selected']==[] and r['forecast'] is None

def test_baseline_is_a_separately_fitted_model_with_no_treatment_inputs(engine):
    baseline=engine.model.baseline_model
    assert baseline is not engine.model.response_model
    from sklearn.utils.validation import check_is_fitted
    for member in getattr(baseline,'models',[baseline]):
        check_is_fitted(member.estimator)
    assert not set(['discount','feature','display','lag_dose_1']).intersection(baseline.features)
    assert engine.card['baseline']['human_entered'] is False
    assert engine.card['baseline']['training_rows']>0
    assert engine.card['baseline']['trained_until']<=engine.model.trained_until

def test_baseline_cannot_read_actual_sales_or_human_forecast_columns(engine):
    base,_,_=engine._future(scenario())
    original=engine.model.baseline_predict(base)
    changed=base.copy();changed['units']=10000000;changed['SPEND']=99999999
    changed['human_baseline']=123456;changed['forecast']=987654
    np.testing.assert_allclose(engine.model.baseline_predict(changed),original)

def test_baseline_learner_excludes_treated_rows_and_unknown_lag_history(engine):
    from promo.model import clean_baseline_mask
    frame=exposures(engine.panel)
    frame=frame[frame.date<=pd.Timestamp(engine.model.trained_until)]
    clean=frame[clean_baseline_mask(frame)]
    assert len(clean)==engine.card['baseline']['training_rows']
    assert clean.discount.max()<=.001
    assert clean.feature.max()==0 and clean.display.max()==0
    assert clean[[f'lag_dose_{i}' for i in range(1,5)]].max().max()<=.001

def test_hindsight_incrementals_use_actual_units_and_realized_sales_value(engine):
    h=engine.hindsight(PID)
    assert h['calculation']=='actual_units - ml_baseline_units'
    assert h['incremental']==pytest.approx(h['actual']-h['baseline'])
    assert h['incremental_revenue']==pytest.approx(h['actual_revenue']-h['baseline_revenue'])
    for week in h['series']:
        assert week['incremental']==pytest.approx(week['actual']-week['baseline'])
        assert week['incremental_revenue']==pytest.approx(week['actual_revenue']-week['baseline_revenue'])
    for campaign in h['campaigns']:
        assert campaign['incremental']==pytest.approx(campaign['actual']-campaign['baseline'])
        assert campaign['incremental_revenue']==pytest.approx(campaign['actual_revenue']-campaign['baseline_revenue'])
    assert h['promotion_incremental']==pytest.approx(h['promotion_actual']-h['promotion_baseline'])

def test_model_selection_uses_calibration_not_the_test_window(engine):
    for fold in engine.card['folds']:
        candidates=fold['baseline_candidates']
        winner=min(candidates,key=lambda c:c['calibration_wape'])
        if 'baseline_selected' in fold:
            assert fold['baseline_selected']['weights']==winner['weights']
            assert fold['response_selected']['weights']==min(fold['response_candidates'],key=lambda c:c['calibration_wape'])['weights']
        else:
            assert fold['baseline_selected_iterations']==winner['iterations']
        assert fold['calibration_end']<fold['test_start']

def test_positive_net_tail_does_not_hide_dips_or_qualify_a_loss_making_offer(engine,monkeypatch):
    from types import SimpleNamespace
    start=pd.Timestamp('2012-01-11')
    b=pd.DataFrame(dict(date=pd.date_range(start,periods=5,freq='7D'),
        store_id='367',product_id=PID,name='Test cereal',category='cereal',
        base_price=10.,price=10.,unit_cost=6.,expected=100.))
    s=b.copy();s['expected']=[180.,70.,160.,90.,100.];s.loc[0,'price']=8.
    monkeypatch.setattr(engine,'_future',lambda req,calendar=None:(b.copy(),s.copy(),start))
    monkeypatch.setattr(engine,'model',SimpleNamespace(predict=lambda x:x.expected.to_numpy(),
        trained_until='2011-07-06',model_id='controlled-response',q_product={},q=1))
    r=engine.forecast(scenario(discount=.2,duration=1))
    # +60 later units must not erase -30 and -10 in other weeks.
    assert r['effects']['pantry_loading']==-40
    assert r['effects']['post_promo_uplift']==60
    assert r['effect_profit']['pantry_loading']==-160  # lost regular-price margin
    assert r['incremental_profit']==40
    assert r['excluded_post_promo_profit']==240
    assert r['planning_profit']==-200
    assert r['planning_revenue']==40
    assert r['planning_roi']==pytest.approx(-200/360)
    assert [w['forecast'] for w in r['weeks']]==[180,70,160,90,100]
    assert sum(r['effects'].values())==r['portfolio_net_units']
    assert sum(r['planning_effect_profit'].values())==r['planning_profit']

def test_tail_split_is_per_store_and_each_products_offer_end():
    from promo.engine import demand_effects
    # A's offer ended while B is still promoted; stores for A have opposite signs.
    targeted=pd.Series([True,True,True,True,False,False])
    active=pd.Series([False,False,True,False,False,False])
    effects=demand_effects(np.array([-30,50,70,-5,-12,9]),active,targeted)
    assert effects==dict(direct=70,cannibalization=-12,halo=9,pantry_loading=-35,post_promo_uplift=50)

@pytest.mark.parametrize('objective',['profit','revenue','roi'])
def test_optimizer_rejects_candidates_profitable_only_from_positive_tail(engine,monkeypatch,objective):
    monkeypatch.setattr(engine,'forecast',lambda *args,**kwargs:dict(
        incremental_profit=1000,planning_profit=-10,feasible=True,warnings=[]))
    r=engine.optimize(OptimizeRequest(product_ids=[PID],margin_rate=.6,objective=objective))
    assert r['selected']==[] and r['forecast'] is None

@pytest.mark.parametrize('objective',['profit','revenue','roi'])
def test_optimizer_ranks_planning_metrics(engine,monkeypatch,objective):
    def forecast(req,detail=True,calendar=None):
        p=req.promotions[0]
        preferred=p.discount==.2 and p.duration==1
        score=20 if preferred else 5
        return dict(incremental_profit=100 if preferred else 1000,incremental_revenue=1000,roi=100,
            planning_profit=score,planning_revenue=score,planning_roi=score/10,
            investment=10,total_units=1,feasible=True,warnings=[])
    monkeypatch.setattr(engine,'forecast',forecast)
    r=engine.optimize(OptimizeRequest(product_ids=[PID],margin_rate=.6,objective=objective,max_promotions=1))
    assert len(r['selected'])==1
    assert r['selected'][0]['promotion']['discount']==.2
    assert r['selected'][0]['promotion']['duration']==1

def test_joint_recheck_rejects_positive_raw_profit_with_negative_planning_profit(engine,monkeypatch):
    def forecast(req,detail=True,calendar=None):
        return dict(incremental_profit=100,incremental_revenue=100,roi=10,
            planning_profit=-1 if detail else 20,planning_revenue=20,planning_roi=2,
            investment=10,total_units=1,feasible=True,warnings=[])
    monkeypatch.setattr(engine,'forecast',forecast)
    r=engine.optimize(OptimizeRequest(product_ids=[PID],margin_rate=.6,max_promotions=1))
    assert r['candidates']  # Standalone candidates passed; joint policy check rejected them.
    assert r['selected']==[] and r['forecast'] is None and r['scenario'] is None
