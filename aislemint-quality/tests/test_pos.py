from promo.storage import AssetPath
import json
import pytest
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from promo import api

@pytest.fixture
def pos(tmp_path,monkeypatch):
    monkeypatch.setattr(api,'DATA',AssetPath('test/'+tmp_path.name))
    client=TestClient(api.app)
    r=client.post('/api/pos/stores',json=dict(name='Test market',address='Test address Bengaluru',state_code='29',gst_registered=True,gstin='29ABCDE1234F1Z5',owner_name='Owner',pin='123456'))
    assert r.status_code==200,r.text
    session=r.json();base='/api/pos/stores/'+session['store']['id'];client.headers['Authorization']='Bearer '+session['token']
    def post(path,body):
        r=client.post(base+path,json=body);assert r.status_code==200,r.text
        return r.json()
    post('/products',dict(product_id='milk',barcode='001',name='Milk <test>',category='Dairy',hsn='0401',price_paise=11800,cost_paise=7000,gst_bps=1800))
    post('/stock',dict(product_id='milk',quantity=20,reason='Opening stock',idempotency_key='opening-stock'))
    shift=post('/shifts',dict(opening_paise=100000))
    return client,base,post,shift,session

def checkout(pos,qty=2,**extra):
    client,base,post,shift,_=pos;cart=dict(lines=[dict(product_id='milk',quantity=qty)])
    q=post('/quote',cart)
    body=dict(**cart,quote_token=q['quote_token'],shift_id=shift['id'],idempotency_key='checkout-test',tenders=[dict(method='cash',amount_paise=q['total_paise']+100)])
    body.update(extra);return post('/checkout',body),body

def test_sale_return_tax_cash_and_retry(pos):
    client,base,post,shift,_=pos;sale,body=checkout(pos)
    assert sale['subtotal_paise']==20000 and sale['tax_paise']==3600
    assert sale['lines'][0]['cgst_paise']==1800 and sale['change_paise']==100
    assert post('/checkout',body)['replayed']
    assert client.get(base+'/products').json()[0]['on_hand']==18
    lines=[dict(product_id='milk',quantity=1,disposition='restock',reason='Unopened pack')]
    review=post('/sales/'+sale['id']+'/return-quote',dict(lines=lines))
    refund=dict(lines=lines,quote_token=review['quote_token'],shift_id=shift['id'],idempotency_key='return-test',tenders=[dict(method='cash',amount_paise=11800)])
    returned=post('/sales/'+sale['id']+'/return',refund)
    assert post('/sales/'+sale['id']+'/return',refund)['replayed']
    assert client.get(base+'/products').json()[0]['on_hand']==19
    report=client.get(base+'/analytics').json()
    assert report['kpis']['net_sales_paise']==10000
    assert report['kpis']['contribution_paise']==3000
    assert report['kpis']['net_collected_paise']==11800
    assert sum(r['net_sales_paise'] for r in report['daily'])==10000
    assert report['taxes'][0]['cgst_paise']==900
    receipt=client.get(base+'/sales/'+sale['id']+'/receipt')
    assert receipt.status_code==200 and 'Milk &lt;test&gt;' in receipt.text
    assert client.get(base+'/returns/'+returned['id']+'/receipt').status_code==200
    close=post('/shifts/'+shift['id']+'/close',dict(counted_paise=111800))
    assert close['variance_paise']==0

def test_stale_quote_oversell_permissions(pos):
    client,base,post,shift,session=pos
    cart=dict(lines=[dict(product_id='milk',quantity=21)])
    assert client.post(base+'/quote',json=cart).status_code==409
    q=post('/quote',dict(lines=[dict(product_id='milk',quantity=1)]))
    post('/products',dict(product_id='milk',barcode='001',name='Milk',category='Dairy',hsn='0401',price_paise=12000,cost_paise=7000,gst_bps=1800))
    r=client.post(base+'/checkout',json=dict(lines=[dict(product_id='milk',quantity=1)],quote_token=q['quote_token'],shift_id=shift['id'],idempotency_key='stale-key',tenders=[dict(method='cash',amount_paise=12000)]))
    assert r.status_code==409
    assert client.get(base+'/sales').json()['total']==0
    staff=post('/staff',dict(name='Cashier',pin='654321'))
    login=post('/login',dict(staff_id=staff['id'],pin='654321'))
    client.headers['Authorization']='Bearer '+login['token']
    assert client.get(base+'/analytics').status_code==403
    assert client.get('/api/pos/stores/another/products').status_code==401

def test_discard_and_interstate_external_refund(pos):
    client,base,post,shift,_=pos
    cart=dict(lines=[dict(product_id='milk',quantity=1)],place_of_supply='27')
    q=post('/quote',cart);assert q['lines'][0]['igst_paise']==1800
    bad=dict(**cart,quote_token=q['quote_token'],shift_id=shift['id'],idempotency_key='external-test',tenders=[dict(method='external_upi',amount_paise=11800)])
    assert client.post(base+'/checkout',json=bad).status_code==422
    bad['tenders'][0]['reference']='TEST-REFERENCE';sale=post('/checkout',bad)
    lines=[dict(product_id='milk',quantity=1,disposition='discard',reason='Damaged pack')]
    q=post('/sales/'+sale['id']+'/return-quote',dict(lines=lines))
    post('/sales/'+sale['id']+'/return',dict(lines=lines,quote_token=q['quote_token'],shift_id=shift['id'],idempotency_key='discard-return',tenders=[dict(method='external_upi',amount_paise=11800,reference='TEST-REFUND')]))
    assert client.get(base+'/products').json()[0]['on_hand']==19
    report=client.get(base+'/analytics').json()
    assert report['kpis']['net_sales_paise']==0 and report['kpis']['contribution_paise']==-7000
    assert report['taxes'][0]['igst_paise']==0
    assert client.post(base+'/sales/'+sale['id']+'/return-quote',json=dict(lines=lines)).status_code==409

def install_offer(pos,mechanics=None,discount=.1):
    from promo.pos_core import now,local_day
    _,_,_,_,session=pos;store=session['store'];day=local_day(store)
    terms=dict(product_id='milk',discount=discount,mechanics=mechanics or [],stacking_policy='stack',feature=False,display=False)
    with api.connection() as c:
        c.run('pos_install_offer_update_pos_stores', ('fixture', 'mapped', store['id']))
        c.run('pos_install_offer_insert_confirmed_promotions', ('campaign-test', 'fixture', 'Test offer', 'confirmed', json.dumps(dict(store_id='mapped', promotions=[terms])), json.dumps({}), '0' * 64, 'offer-test-key', now(), None, 'fixture-model'))
        c.run('pos_install_offer_insert_promotion_events', ('campaign-test', 'fixture', 'mapped', 'milk', day, discount, 0, 0, 1))
        c.run('pos_install_offer_insert_promotion_calendar_revisions', ('fixture', 1))
    return terms

def test_confirmed_conditional_offer_and_promotion_safe_return(pos):
    _,base,post,shift,_=pos
    install_offer(pos,[dict(kind='bxgy',buy_quantity=1,reward_quantity=1)])
    sale,_=checkout(pos)
    assert sale['total_paise']==10620  # 2 x 118 less TPR, then one free unit.
    assert {o['kind'] for o in sale['applied_promotions']}=={'tpr','bxgy'}
    one=dict(lines=[dict(product_id='milk',quantity=1,reason='Customer return')])
    r=pos[0].post(base+'/sales/'+sale['id']+'/return-quote',json=one)
    assert r.status_code==409 and 'linked free reward' in r.text
    q=post('/sales/'+sale['id']+'/return-quote',dict(lines=[dict(product_id='milk',quantity=2,reason='Full bundle return')]))
    assert q['total_paise']==10620
    with api.connection() as c:c.run('pos_test_confirmed_conditional_offer_and_promotion_safe_return_update_promotion_events', ());c.run('pos_test_confirmed_conditional_offer_and_promotion_safe_return_update_promotion_calendar_revisions', ())
    assert post('/quote',dict(lines=[dict(product_id='milk',quantity=2)]))['total_paise']==23600
    assert post('/sales/'+sale['id']+'/return-quote',dict(lines=[dict(product_id='milk',quantity=2,reason='Full bundle return')]))['total_paise']==10620

def test_concurrent_sale_cannot_oversell_or_duplicate(pos):
    client,base,post,shift,session=pos
    post('/stock',dict(product_id='milk',quantity=-18,kind='adjustment',reason='Count adjustment',idempotency_key='reduce-to-two'))
    cart=dict(lines=[dict(product_id='milk',quantity=2)]);q=post('/quote',cart)
    body=dict(**cart,quote_token=q['quote_token'],shift_id=shift['id'],idempotency_key='parallel-sale',tenders=[dict(method='cash',amount_paise=q['total_paise'])])
    # Do not enter app lifespan while another simulated request is in flight.
    def send(b):return TestClient(api.app).post(base+'/checkout',json=b,headers={'Authorization':'Bearer '+session['token']})
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(send,[body,body]))
    assert all(r.status_code==200 for r in results), [(r.status_code,r.text) for r in results]
    assert len({r.json()['id'] for r in results})==1
    assert post('/checkout',body)['replayed']
    body['idempotency_key']='different-sale'
    assert send(body).status_code==409
    assert client.get(base+'/products').json()[0]['on_hand']==0
    assert client.get(base+'/sales').json()['total']==1

def test_day_close_exports_gross_demand_and_detects_readiness(pos):
    client,base,post,shift,session=pos;sale,_=checkout(pos)
    lines=[dict(product_id='milk',quantity=1,disposition='restock',reason='Sealed pack return')]
    q=post('/sales/'+sale['id']+'/return-quote',dict(lines=lines))
    post('/sales/'+sale['id']+'/return',dict(lines=lines,quote_token=q['quote_token'],shift_id=shift['id'],idempotency_key='day-close-return',tenders=[dict(method='cash',amount_paise=11800)]))
    assert client.post(base+'/days/close',json={}).status_code==409
    post('/holidays',dict(date=sale['business_date'],name='Local occasion'))
    post('/shifts/'+shift['id']+'/close',dict(counted_paise=111800))
    closed=post('/days/close',{});assert closed['rows']==1
    assert post('/days/close',{})['replayed']
    assert client.post(base+'/shifts',json={}).status_code==409
    import io
    d=pd.read_csv(io.StringIO(client.get(base+'/learning/export/demand').text))
    assert d.units.iloc[0]==2 and d.returned_units.iloc[0]==1
    assert d.price.iloc[0]==100 and d.SPEND.iloc[0]==200 and d.refund_ex_gst.iloc[0]==100
    assert d.currency.iloc[0]=='INR' and d.country.iloc[0]=='IN' and d.holiday.iloc[0]==1
    readiness=client.get(base+'/learning').json()
    assert not readiness['readiness']['demand']['ready']
    assert readiness['drift']['status']=='collecting'
    assert client.post(base+'/learning/train',json=dict(kind='demand')).status_code==422

def test_choice_reward_and_net_spend_threshold():
    from promo.pos_core import price_basket
    from promo.offer_mechanics import OfferTerms
    products={p:dict(price_paise=11800,gst_bps=1800) for p in ['a','b']}
    terms=OfferTerms(product_id='a',discount=0,mechanics=[dict(kind='bxgcy',buy_quantity=2,reward_choices=['b'])]).model_dump()
    prices,applied,_=price_basket({'a':2,'b':1},products,[dict(campaign_id='x',terms=terms)],{'a:conditional':'b'})
    assert sum(prices['b'])==0 and sum(prices['a'])==23600
    terms=OfferTerms(product_id='a',discount=0,mechanics=[dict(kind='spend_threshold',threshold_spend=100,amount_off=10)]).model_dump()
    prices,_,_=price_basket({'a':1},products,[dict(campaign_id='x',terms=terms)])
    assert sum(prices['a'])==10620  # ₹10 ex-GST benefit -> ₹11.80 off the retail price.

def test_india_calendar_and_explicit_free_units():
    from promo.holidays import calendar_for
    from promo.demand import validate_demand_panel
    d=pd.DataFrame([dict(date=str(dt.date()),store_id='india',product_id='milk',name='Milk',category='Dairy',units=2,price=100,base_price=100,holiday=int(i==10),currency='INR',country='IN') for i,dt in enumerate(pd.date_range('2025-01-01',periods=180))])
    d.loc[20,'price']=0;d['free_promotion']=0
    with pytest.raises(ValueError):validate_demand_panel(d)
    d.loc[20,'free_promotion']=1;frame,quality=validate_demand_panel(d)
    assert quality['currency']=='INR' and quality['country']=='IN'
    calendar=calendar_for(frame);assert calendar.metadata['region']=='IN'
    features=calendar.features(pd.Series(pd.to_datetime(['2025-01-11','2025-07-04'])),1)
    assert features.client_holiday_any.tolist()==[1,0]
    assert features.us_holiday_any.sum()==0

def test_daily_model_links_to_current_pos_calendar(pos):
    from promo.promotions import calendar_snapshot
    from promo.pos_core import now
    install_offer(pos)
    sid='demand_'+'a'*32;pos_id=pos[-1]['store']['id']
    with api.connection() as c:c.run('pos_test_daily_model_links_to_current_pos_calendar_insert_demand_datasets', (sid, 'Test source', json.dumps(dict(pos_store_id=pos_id, model_store_id='historical')), now()))
    snapshot=calendar_snapshot(sid,'historical')
    assert snapshot['calendar_source_id']=='fixture'
    assert snapshot['events'][0]['store_id']=='historical'
    assert snapshot['events'][0]['campaign_id']=='campaign-test'
    with api.connection() as c:c.run('pos_test_confirmed_conditional_offer_and_promotion_safe_return_update_promotion_events', ());c.run('pos_test_confirmed_conditional_offer_and_promotion_safe_return_update_promotion_calendar_revisions', ())
    after=calendar_snapshot(sid,'historical')
    assert not after['events'] and after['revision']!=snapshot['revision']

def test_writeoff_reconciles_product_and_category_contribution(pos):
    client,base,post,_,_=pos;checkout(pos)
    post('/stock',dict(product_id='milk',quantity=-1,kind='discard',reason='Damaged on shelf',idempotency_key='shelf-discard'))
    report=client.get(base+'/analytics').json()
    assert report['kpis']['contribution_paise']==-1000
    for group in ['daily','products','categories']:assert sum(r['contribution_paise'] for r in report[group])==-1000

def test_choice_cycles_and_unclassified_gst_bundles_are_rejected(pos):
    from promo.pos_core import price_basket
    from promo.offer_mechanics import OfferTerms
    products={p:dict(price_paise=100,gst_bps=500) for p in ['a','b']}
    offers=[dict(campaign_id=p,terms=OfferTerms(product_id=p,discount=0,mechanics=[dict(kind='bxgcy',reward_choices=[r])]).model_dump()) for p,r in [('a','b'),('b','a')]]
    with pytest.raises(ValueError,match='cycle'):price_basket({'a':1,'b':1},products,offers)
    client,base,post,_,_=pos
    post('/products',dict(product_id='gift',barcode='002',name='Gift',category='Packaged',hsn='1905',price_paise=5000,cost_paise=1000,gst_bps=500))
    post('/stock',dict(product_id='gift',quantity=5,reason='Opening stock',idempotency_key='gift-opening'))
    install_offer(pos,[dict(kind='bxgcy',buy_quantity=1,reward_choices=['gift'])],discount=0)
    r=client.post(base+'/quote',json=dict(lines=[dict(product_id='milk',quantity=1),dict(product_id='gift',quantity=1)],selected_rewards={'milk:conditional':'gift'}))
    assert r.status_code==422 and 'GST rates' in r.text

def test_zero_sales_offer_day_is_not_a_clean_baseline_observation(pos):
    client,base,post,shift,_=pos
    install_offer(pos,[dict(kind='bulk',threshold_quantity=3,discount=.1)],discount=.1)
    post('/shifts/'+shift['id']+'/close',dict(counted_paise=100000))
    post('/days/close',{})
    import io
    exported=pd.read_csv(io.StringIO(client.get(base+'/learning/export/demand').text))
    assert exported.units.iloc[0]==0 and exported.promotion_available.iloc[0]==1
    assert exported.price.iloc[0]==90 and exported.base_price.iloc[0]==100
    from promo.model import clean_baseline_mask,exposures
    frame=pd.DataFrame(dict(date=pd.date_range('2025-01-05',periods=10,freq='7D'),store_id='s',product_id='p',category='C',discount=0,feature=0,display=0,units=5,promotion_available=[0]*5+[1]+[0]*4))
    clean=clean_baseline_mask(exposures(frame))
    assert clean.iloc[4] and not clean.iloc[5:].any()
