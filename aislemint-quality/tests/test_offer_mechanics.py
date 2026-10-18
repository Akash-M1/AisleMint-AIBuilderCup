import copy
import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError
from promo.offer_mechanics import ConditionalOffer as Offer,OfferTerms,checkout,offer_profile,apply_schedule,apply_rewards,redemption_model
from promo.engine import Promotion,Scenario
from test_promotion_calendar import integrated


def test_tpr_then_bxgy_prices_whole_bundles_in_cents():
    offer=Offer(kind='bxgy',buy_quantity=1,reward_quantity=1)
    a=checkout({'p':2},{'p':10},'p',.1,[offer])
    assert a['total']==9 and a['effective_discount']==pytest.approx(.55)
    assert checkout({'p':1},{'p':10},'p',.1,[offer])['total']==9
    assert checkout({'p':3},{'p':10},'p',.1,[offer])['total']==18
    assert checkout({'p':2},{'p':1.99},'p',.1,[offer])['total']==1.79


def test_conditional_groups_choose_best_or_stack_remaining_price():
    first=Offer(kind='bulk',threshold_quantity=2,discount=.1)
    second=Offer(kind='bulk',threshold_quantity=2,discount=.2)
    assert checkout({'p':2},{'p':10},'p',.1,[first,second])['total']==14.4
    second=second.model_copy(update={'stack_group':'second'})
    assert checkout({'p':2},{'p':10},'p',.1,[first,second])['total']==12.96


def test_spend_qualification_uses_remaining_subtotal_and_caps_savings():
    offer=Offer(kind='spend_threshold',threshold_spend=20,amount_off=100)
    assert checkout({'p':2},{'p':10},'p',.1,[offer])['total']==18
    assert checkout({'p':3},{'p':10},'p',.1,[offer])['total']==0
    assert checkout({'r':1},{'r':10},'p',0,[Offer(kind='tiered',tiers=[dict(quantity=2,discount=.1)])])['total']==10


def test_choice_means_one_free_eligible_product_and_preserves_qualifier_revenue():
    offer=Offer(kind='bxgcy',buy_quantity=2,reward_choices=['r','s'])
    receipt=checkout({'p':2,'r':1,'s':1},{'p':10,'r':6,'s':8},'p',.1,[offer],{'conditional':'s'})
    assert receipt['total']==24 and next(r for r in receipt['lines'] if r['product_id']=='p')['paid']==18
    profile=offer_profile(10,.1,[offer],redemption_override=1,reward_prices={'r':6,'s':8})
    assert profile['paid_discount']==pytest.approx(.1) and profile['effective_discount']>.1
    assert sum(r['units_per_qualifier'] for r in profile['rewards'])==pytest.approx(.5)
    with pytest.raises(ValidationError):Offer(kind='bxgcy',reward_choices=['r'],reward_discount=.5)
    with pytest.raises(ValidationError):Offer(kind='bxgcy',reward_choices=['r'],reward_quantity=2)
    with pytest.raises(ValidationError):OfferTerms(product_id='p',mechanics=[offer.model_copy(update={'reward_choices':['p']})])


def test_profiles_include_higher_tiers_and_zero_redemption_removes_conditional_effect():
    a=Offer(kind='tiered',tiers=[dict(quantity=2,discount=.1),dict(quantity=4,discount=.2)])
    b=Offer(kind='tiered',tiers=[dict(quantity=2,discount=.1),dict(quantity=4,discount=.5)])
    assert offer_profile(10,.1,[b],normal_basket_units=5,redemption_override=1)['effective_discount']>offer_profile(10,.1,[a],normal_basket_units=5,redemption_override=1)['effective_discount']
    profile=offer_profile(10,.1,[b],redemption_override=0)
    assert profile['effective_discount']==pytest.approx(.1) and profile['intensity']==0
    assert redemption_model()['card']['data_basis']=='synthetic' and not redemption_model()['card']['production_validated']


def test_reward_units_displace_paid_sales_before_adding_physical_demand():
    f=pd.DataFrame(dict(store_id=['s','s'],product_id=['p','r'],date=pd.to_datetime(['2026-01-01']*2),prediction=[10.,3.],price=[10.,6.]))
    profile=dict(store_id='s',product_id='p',date='2026-01-01',rewards=[dict(product_id='r',units_per_qualifier=.25)])
    out,alloc=apply_rewards(f,[profile],'prediction')
    assert out.prediction.tolist()==[10,3] and out.price.tolist()==[10,1]
    assert alloc[0]['displaced_paid_units']==2.5 and alloc[0]['additional_physical_units']==0
    profile['rewards'][0]['units_per_qualifier']=.5
    out,alloc=apply_rewards(f,[profile],'prediction')
    assert out.prediction.tolist()==[10,5] and out.price.iloc[1]==0
    assert alloc[0]['additional_physical_units']==2


def test_stack_and_best_offer_resolve_one_exposure_and_reject_mixed_policy():
    date=pd.Timestamp('2026-01-01');f=pd.DataFrame(dict(store_id=['s'],product_id=['p'],date=[date],base_price=[10.],price=[10.],discount=[0.],feature=[0],display=[0]))
    a=OfferTerms(product_id='p',discount=.1,stacking_policy='stack').model_dump()
    b=OfferTerms(product_id='p',discount=.2,stacking_policy='stack',mechanics=[Offer(kind='bulk',threshold_quantity=2,discount=.1)],redemption_rate=1).model_dump()
    out,profiles=apply_schedule(f,{('s','p',date):[a,b]})
    assert profiles[0]['tpr']==.2 and out.discount.iloc[0]==pytest.approx(.28)
    a['stacking_policy']=b['stacking_policy']='best_offer'
    out,profiles=apply_schedule(f,{('s','p',date):[a,b]})
    assert out.discount.iloc[0]==pytest.approx(.28)
    a['stacking_policy']='exclusive'
    with pytest.raises(ValueError,match='overlap'):apply_schedule(f,{('s','p',date):[a,b]})


def confirm(client,scenario,key):
    r=client.post('/api/forecast',json=scenario);assert r.status_code==200,r.text
    c=client.post('/api/promotions/confirm',json=dict(scenario=scenario,review_token=r.json()['confirmation_token'],idempotency_key=key))
    assert c.status_code==200,c.text
    return c.json(),r.json()


def test_overlapping_confirmed_campaigns_flow_once_and_cancel_independently(integrated):
    p='3800031838';first=dict(store_id='367',promotions=[dict(product_id=p,discount=.1,duration=2,stacking_policy='stack')])
    one,_=confirm(integrated,first,'first-stacking-campaign')
    second=copy.deepcopy(first);second['promotions'][0].update(discount=.2,mechanics=[dict(kind='bulk',threshold_quantity=2,discount=.1)],redemption_rate=1)
    two,review=confirm(integrated,second,'second-stacking-campaign')
    assert review['promotion_calendar']['revision']==1
    body=dict(store_id='367',product_ids=[p],horizon=4)
    forecast=integrated.post('/api/demand/forecast',json=body).json()
    assert len(forecast['promotion_calendar']['events'])==4 and len(forecast['offer_profiles'])==2
    row=forecast['series'][0]['rows'][0]
    receipt=checkout({'p':2},{'p':row['regular_price']},'p',.2,[Offer(kind='bulk',threshold_quantity=2,discount=.1)])
    assert row['price']==pytest.approx(receipt['total']/2)
    assert forecast['offer_profiles'][0]['effective_discount']==pytest.approx(1-row['price']/row['regular_price'])
    assert integrated.post('/api/promotions/'+two['promotion']['id']+'/cancel').status_code==200
    remaining=integrated.post('/api/demand/forecast',json=body).json()
    assert remaining['promotion_calendar']['campaign_ids']==[one['promotion']['id']]
    assert remaining['offer_profiles'][0]['effective_discount']==.1


def test_choice_confirmation_adds_reward_stock_and_keeps_financial_identity(integrated,engine):
    p='3800031838';reward=next(x['product_id'] for x in engine.catalog('367') if x['product_id']!=p)
    scenario=dict(store_id='367',promotions=[dict(product_id=p,discount=.05,duration=1,redemption_rate=.5,
        mechanics=[dict(kind='bxgcy',buy_quantity=2,reward_choices=[reward])])])
    campaign,review=confirm(integrated,scenario,'choice-gift-campaign')
    assert sum(review['effects'].values())==pytest.approx(review['portfolio_net_units'])
    assert sum(review['effect_profit'].values())==pytest.approx(review['incremental_profit'])
    assert review['effect_profit']['choice_reward_cost']<=0
    body=dict(store_id='367',product_ids=[p],horizon=8)
    demand=integrated.post('/api/demand/forecast',json=body);assert demand.status_code==200,demand.text
    demand=demand.json();assert set(demand['selected_product_ids'])=={p,reward}
    allocations=demand['reward_allocations'];assert allocations and allocations[0]['reward_units']>0
    inventory=dict(forecast=body,stock_as_of=demand['as_of'],positions=[dict(store_id='367',product_id=p,on_hand=100,unit_cost=2)])
    assert integrated.post('/api/demand/inventory',json=inventory).status_code==422
    inventory['positions'].append(dict(store_id='367',product_id=reward,on_hand=100,unit_cost=2))
    r=integrated.post('/api/demand/inventory',json=inventory);assert r.status_code==200,r.text
    assert {s['product_id'] for s in r.json()['rows']}=={p,reward}


def test_conditional_offer_does_not_change_clean_holiday_baseline(engine):
    p='3800031838';request=Scenario(store_id='367',start_date='2012-02-15',promotions=[Promotion(product_id=p,discount=.1,mechanics=[Offer(kind='bulk')])])
    base,planned,_=engine._future(request)
    # Other products' conditional baselines may change through peer exposures.
    # The qualifier's own treatment is excluded from its baseline learner.
    np.testing.assert_allclose(engine.model.baseline_predict(base[base.product_id.eq(p)]),engine.model.baseline_predict(planned[planned.product_id.eq(p)]))
    assert engine.card['holiday_calendar']['supplied_rows']==342
