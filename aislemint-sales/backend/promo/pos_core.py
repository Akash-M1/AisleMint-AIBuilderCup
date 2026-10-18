"""OneSales ledger primitives. Money is integer paise; checkout uses executed rules, never ML redemption."""
from __future__ import annotations
from .storage import AssetPath,managed,read_frame,write_frame,load_model,dump_model
from datetime import datetime,timezone,timedelta,date
from zoneinfo import ZoneInfo
from decimal import Decimal,ROUND_HALF_UP
import hashlib,hmac,json,re,secrets
from pydantic import BaseModel,ConfigDict,Field,model_validator
from fastapi import HTTPException
from .offer_mechanics import OfferTerms,ConditionalOffer,compatible,discounted,apply_conditional

def uid():return secrets.token_hex(16)
def now():return datetime.now(timezone.utc).isoformat()
def local_day(store):return datetime.now(ZoneInfo(store['timezone'])).date().isoformat()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def paise(value):return int((Decimal(str(value))*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
def round_int(value):return int(Decimal(value).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
def pin_hash(pin,salt):return hashlib.scrypt(pin.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()

class StrictModel(BaseModel):model_config=ConfigDict(extra='forbid',allow_inf_nan=False,str_strip_whitespace=True)
class StoreInput(StrictModel):
    name:str=Field(min_length=2,max_length=100)
    address:str=Field(min_length=5,max_length=400)
    state_code:str=Field(pattern=r'^(0[1-9]|[12][0-9]|3[0-8])$')
    gst_registered:bool=False
    gstin:str=Field(default='',max_length=15)
    owner_name:str=Field(min_length=2,max_length=80)
    pin:str=Field(pattern=r'^\d{6,12}$')
    @model_validator(mode='after')
    def tax_registration(self):
        self.gstin=self.gstin.upper()
        if self.gst_registered and (not re.fullmatch(r'\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]',self.gstin) or self.gstin[:2]!=self.state_code):raise ValueError('Enter the store GSTIN with a state code matching the store. Registration must be verified by the business.')
        if not self.gst_registered and self.gstin:raise ValueError('GSTIN requires a registered store.')
        return self

class ProductInput(StrictModel):
    product_id:str=Field(min_length=1,max_length=60,pattern=r'^[A-Za-z0-9_-]+$')
    barcode:str=Field(min_length=1,max_length=60)
    name:str=Field(min_length=2,max_length=140)
    category:str=Field(min_length=1,max_length=80)
    hsn:str=Field(default='',max_length=8,pattern=r'^\d{0,8}$')
    unit:str=Field(default='NOS',min_length=1,max_length=10)
    price_paise:int=Field(gt=0,le=100000000,strict=True)
    cost_paise:int=Field(ge=0,le=100000000,strict=True)
    gst_bps:int=Field(default=0,ge=0,le=10000,strict=True)
    reorder_level:int=Field(default=10,ge=0,le=1000000,strict=True)
    active:bool=True

class CartLine(StrictModel):
    product_id:str=Field(min_length=1,max_length=60)
    quantity:int=Field(ge=1,le=500,strict=True)
class Cart(StrictModel):
    lines:list[CartLine]=Field(min_length=1,max_length=60)
    customer_id:str|None=None
    place_of_supply:str|None=Field(default=None,pattern=r'^(0[1-9]|[12][0-9]|3[0-8])$')
    selected_rewards:dict[str,str]=Field(default_factory=dict,max_length=30)
    @model_validator(mode='after')
    def unique_lines(self):
        if len({l.product_id for l in self.lines})!=len(self.lines):raise ValueError('Combine duplicate products in the basket.')
        return self
class Tender(StrictModel):
    method:str=Field(pattern=r'^(cash|external_card|external_upi)$')
    amount_paise:int=Field(gt=0,le=1000000000,strict=True)
    reference:str=Field(default='',max_length=100)
    @model_validator(mode='after')
    def external_reference(self):
        if self.method!='cash' and not self.reference:raise ValueError('Record the successful external terminal or UPI reference. OneSales does not process that payment.')
        return self
class CheckoutInput(Cart):
    shift_id:str
    quote_token:str=Field(pattern=r'^[a-f0-9]{64}$')
    idempotency_key:str=Field(min_length=8,max_length=128,pattern=r'^[A-Za-z0-9_-]+$')
    tenders:list[Tender]=Field(default_factory=list,max_length=6)

def store_row(con,store_id):
    row=con.run('pos_core_store_row_aggregate_pos_stores', (store_id,)).fetchone()
    if not row:raise HTTPException(404,'OneSales store not found.')
    return dict(row)
def authorize(con,store_id,authorization,manager=False):
    token=(authorization or '').removeprefix('Bearer ')
    row=con.run('pos_core_authorize_aggregate_pos_sessions', (hashlib.sha256(token.encode()).hexdigest(), now())).fetchone()
    if not row or row['store_id']!=store_id:raise HTTPException(401,'Unlock OneSales for this store to continue.')
    if manager and row['role']!='manager':raise HTTPException(403,'A manager must perform this action.')
    return dict(row)
def session_for(con,staff_id):
    token=secrets.token_urlsafe(32)
    con.run('pos_core_session_for_insert_pos_sessions', (hashlib.sha256(token.encode()).hexdigest(), staff_id, (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()))
    return token
def audit_event(con,store_id,kind,ref,payload):
    con.run('pos_core_audit_event_insert_pos_events', (store_id, kind, ref, json.dumps(payload), now()))
def next_number(con,store_id,day,kind='OS'):
    day=date.fromisoformat(day);year=day.year if day.month>=4 else day.year-1;fy=f'{year%100:02d}{(year+1)%100:02d}'
    con.run('pos_core_next_number_upsert_pos_counters', (store_id, fy, kind))
    n=con.run('pos_core_next_number_aggregate_pos_counters', (store_id, fy, kind)).fetchone()[0]
    if n>999999:raise ValueError('Invoice series exhausted; configure a new series before trading.')
    return f'{kind}/{fy}/{n:06d}'
def stock_move(con,store,product,quantity,kind,ref,reason,staff,unit_cost=0,key=None):
    updated=con.run('pos_core_stock_move_update_pos_products', (quantity, store['id'], product, quantity))
    if updated.rowcount!=1:raise HTTPException(409,f'Insufficient stock for {product}. Refresh the basket or stock balance.')
    balance=con.run('pos_core_stock_move_aggregate_pos_products', (store['id'], product)).fetchone()[0]
    con.run('pos_core_stock_move_insert_pos_inventory_movements', (uid(), store['id'], product, quantity, balance, kind, ref, reason, unit_cost, staff, now(), local_day(store), key))
def require_shift(con,store,shift_id,staff=None):
    row=con.run('pos_core_require_shift_aggregate_pos_shifts', (shift_id, store['id'])).fetchone()
    if not row or row['business_date']!=local_day(store):raise HTTPException(409,'Open a register shift for today before recording a sale or refund.')
    if staff and row['staff_id']!=staff['id'] and staff['role']!='manager':raise HTTPException(403,'This register belongs to another cashier.')
    if con.run('pos_core_require_shift_aggregate_pos_days', (store['id'], local_day(store))).fetchone():raise HTTPException(409,'This trading day is closed. New transactions belong to the next trading day.')
    return dict(row)

def executed_offers(con,store):
    if not store['forecast_workspace']:return [],0
    day=date.fromisoformat(store['promo_reference_date']) if store['demo'] and store['promo_reference_date'] else date.fromisoformat(local_day(store))
    events=con.run('pos_core_executed_offers_aggregate_promotion_events', (store['forecast_workspace'], store['forecast_store_id'], day.isoformat(), (day + timedelta(days=6)).isoformat())).fetchall()
    result=[]
    for row in events:
        p=next(p for p in json.loads(row['request'])['promotions'] if p['product_id']==row['product_id'])
        result.append(dict(campaign_id=row['campaign_id'],name=row['name'],period_end=row['period_end'],terms=OfferTerms(**{k:v for k,v in p.items() if k in OfferTerms.model_fields}).model_dump()))
    revision=con.run('pos_core_executed_offers_aggregate_promotion_calendar_revisions', (store['forecast_workspace'],)).fetchone()
    return result,revision[0] if revision else 0

def price_basket(cart,products,offers,selected_rewards=None):
    """Exact executed basket rules shared with planning's checkout primitives.

    Highest TPR first across stacked campaigns. Conditional groups then apply in
    product/group order. Free units cannot qualify for another product's reward.
    ML redeemed-unit-share assumptions have no role in a checkout charge.
    """
    quantities={k:int(v) for k,v in cart.items() if v};selected_rewards=selected_rewards or {}
    def execution_offer(data,pid):
        offer=ConditionalOffer(**data)
        if offer.kind=='spend_threshold':
            # Planning prices/absolute amounts exclude GST. Retail basket prices
            # include it. Percentages and unit thresholds need no conversion.
            factor=1+products[pid].get('gst_bps',0)/10000
            offer=offer.model_copy(update={'threshold_spend':offer.threshold_spend*factor,'amount_off':offer.amount_off*factor})
        return offer
    regular={k:[products[k]['price_paise']]*q for k,q in quantities.items()}
    prices={k:list(v) for k,v in regular.items()};by_product={};applied=[];opportunities=[];selected={}
    for event in offers:
        p=event['terms']['product_id']
        if p in quantities:by_product.setdefault(p,[]).append(event)
    graph={p:{choice for e in events for o in e['terms']['mechanics'] if o['kind']=='bxgcy' for choice in o.get('reward_choices',[]) if choice in quantities} for p,events in by_product.items()}
    def visit(node,path):
        if node in path:raise ValueError('Choice-reward offers form a qualification cycle in this basket. Revise the overlapping campaigns before checkout.')
        for child in graph.get(node,[]):visit(child,path|{node})
    for node in graph:visit(node,set())
    for pid,events in sorted(by_product.items()):
        terms=[OfferTerms(**e['terms']) for e in events];compatible(terms)
        if len(events)>1 and terms[0].stacking_policy=='best_offer':
            def benefit(event):
                t=OfferTerms(**event['terms']);candidate={k:list(v) for k,v in regular.items()};candidate[pid]=[discounted(p,t.discount) for p in candidate[pid]]
                for group in sorted({o.stack_group for o in t.mechanics}):
                    candidates=[apply_conditional(quantities,candidate,pid,execution_offer(o.model_dump(),pid),selected_rewards.get(f'{pid}:{group}')) for o in t.mechanics if o.stack_group==group]
                    candidate=min(candidates,key=lambda x:sum(map(sum,x.values())))
                return sum(map(sum,candidate.values()))
            events=[min(events,key=lambda e:(benefit(e),e['campaign_id']))]
        selected[pid]=events
        discount=max(e['terms']['discount'] for e in events)
        prices[pid]=[discounted(p,discount) for p in prices[pid]]
        saving=sum(regular[pid])-sum(prices[pid])
        if saving:applied.append(dict(product_id=pid,kind='tpr',group='tpr',saving_paise=saving,campaign_ids=[e['campaign_id'] for e in events if e['terms']['discount']==discount]))
    for pid,events in sorted(selected.items()):
        groups={}
        for event in events:
            for data in event['terms']['mechanics']:
                offer=execution_offer(data,pid);groups.setdefault(offer.stack_group,[]).append((offer,event))
        for group,options in sorted(groups.items()):
            choices=selected_rewards.get(f'{pid}:{group}')
            if choices and not any(choices in o.reward_choices for o,_ in options):raise ValueError('The selected choice reward is not eligible for this offer group.')
            candidates=[]
            for offer,event in options:
                if choices and offer.kind=='bxgcy' and (choices not in offer.reward_choices or quantities.get(choices,0)==0):continue
                eligible_cart=dict(quantities)
                if offer.kind=='bxgcy':
                    eligible_cart[pid]=sum(p>0 for p in prices[pid])
                    if eligible_cart[pid]>=offer.buy_quantity:
                        opportunities.append(dict(product_id=pid,group=group,buy_quantity=offer.buy_quantity,reward_choices=offer.reward_choices,selected=choices,campaign_id=event['campaign_id']))
                candidate=apply_conditional(eligible_cart,prices,pid,offer,choices)
                candidates.append((candidate,offer,event))
            if not candidates:continue
            candidate,offer,event=min(candidates,key=lambda x:(sum(map(sum,x[0].values())),x[2]['campaign_id']))
            saving=sum(map(sum,prices.values()))-sum(map(sum,candidate.values()))
            if saving:applied.append(dict(product_id=pid,kind=offer.kind,group=group,saving_paise=saving,campaign_ids=[event['campaign_id']],terms=offer.model_dump()))
            prices=candidate
    return prices,applied,opportunities

def split_gst(paid,rate,interstate):
    net=round_int(Decimal(paid)*10000/Decimal(10000+rate));tax=paid-net
    return dict(net_paise=net,tax_paise=tax,cgst_paise=0 if interstate else tax//2,sgst_paise=0 if interstate else tax-tax//2,igst_paise=tax if interstate else 0)

def quote(con,store,request,check_stock=True):
    cart={l.product_id:l.quantity for l in request.lines}
    products={r['product_id']:dict(r) for r in con.run('pos_core_quote_aggregate_pos_products', (store['id'],))}
    if set(cart)-set(products):raise ValueError('A basket product no longer exists in this store.')
    if any(not products[p]['active'] for p in cart):raise ValueError('Remove inactive products before checkout.')
    if check_stock and any(q>products[p]['on_hand'] for p,q in cart.items()):raise HTTPException(409,'The basket exceeds available stock. Reduce quantities or receive inventory.')
    customer=None
    if request.customer_id:
        row=con.run('pos_core_quote_aggregate_pos_customers', (request.customer_id, store['id'])).fetchone()
        if not row:raise ValueError('Customer does not belong to this store.')
        customer=dict(row)
    offers,revision=executed_offers(con,store)
    prices,applied,opportunities=price_basket(cart,products,offers,request.selected_rewards)
    if store['gst_registered'] and not store['demo']:
        for offer in applied:
            if offer['kind']=='bxgcy':
                pid=offer['product_id'];rewards=offer['terms']['reward_choices'];selected=request.selected_rewards.get(pid+':'+offer['group'])
                if any(r in prices and (not selected or selected==r) and 0 in prices[r] and products[r]['gst_bps']!=products[pid]['gst_bps'] for r in rewards):
                    raise ValueError('This choice bundle combines different GST rates. Use a separately classified bundle SKU: automatic composite/mixed-supply tax classification is not supported.')
        reward_ids={r for o in applied if o['kind']=='bxgcy' for r in o['terms']['reward_choices']}
        if any(sum(values)==0 and pid not in reward_ids for pid,values in prices.items()):raise ValueError('Standalone zero-value gifts need gift/ITC treatment outside this checkout. Revise the zero-price offer before recording a live GST invoice.')
    place=request.place_of_supply or store['state_code'];interstate=place!=store['state_code']
    lines=[]
    for pid,q in cart.items():
        p=products[pid];paid=sum(prices[pid]);regular=p['price_paise']*q
        tax=split_gst(paid,p['gst_bps'] if store['gst_registered'] else 0,interstate)
        lines.append(dict(product_id=pid,name=p['name'],barcode=p['barcode'],category=p['category'],hsn=p['hsn'],unit=p['unit'],quantity=q,regular_paise=regular,unit_price_paise=p['price_paise'],paid_paise=paid,discount_paise=regular-paid,cost_paise=p['cost_paise'],gst_bps=p['gst_bps'] if store['gst_registered'] else 0,free_units=sum(v==0 for v in prices[pid]),**tax))
    total=sum(l['paid_paise'] for l in lines)
    if store['gst_registered'] and not store['demo'] and (total>=5000000 or customer and customer['gstin']) and (not customer or not customer['name'] or not customer['address'] or not customer['state_code']):raise ValueError('Recipient name, address and state are required for this invoice. Add or complete the customer record.')
    result=dict(store={k:v for k,v in store.items() if k not in ['revision']},customer=customer,business_date=local_day(store),place_of_supply=place,interstate=interstate,selected_rewards=request.selected_rewards,lines=lines,applied_promotions=applied,reward_opportunities=opportunities,promotion_terms=offers,calendar_revision=revision,currency='INR',subtotal_paise=sum(l['net_paise'] for l in lines),tax_paise=sum(l['tax_paise'] for l in lines),total_paise=total,discount_paise=sum(l['discount_paise'] for l in lines),cogs_paise=sum(l['quantity']*l['cost_paise'] for l in lines),checkout_version='onesales-v1')
    result['quote_token']=digest(dict(request=Cart(**{k:v for k,v in request.model_dump().items() if k in Cart.model_fields}).model_dump(),store_revision=store['revision'],result=result))
    return result

def expected_cash(con,shift):
    sales=con.run('pos_core_expected_cash_aggregate_pos_sales', (shift['id'],)).fetchone()[0]
    refunds=con.run('pos_core_expected_cash_aggregate_pos_returns', (shift['id'],)).fetchone()[0]
    moves=con.run('pos_core_expected_cash_aggregate_pos_cash_movements', (shift['id'],)).fetchone()[0]
    return shift['opening_paise']+sales-refunds+moves

def complete_sale(con,store,staff,request):
    request_hash=digest(request.model_dump())
    old=con.run('pos_core_complete_sale_aggregate_pos_sales', (store['id'], request.idempotency_key)).fetchone()
    if old:
        if old['request_hash']!=request_hash:raise HTTPException(409,'Checkout retry key was already used with different inputs.')
        return {**json.loads(old['snapshot']),'replayed':True}
    require_shift(con,store,request.shift_id,staff)
    reviewed=quote(con,store,request)
    if not hmac.compare_digest(request.quote_token,reviewed['quote_token']):raise HTTPException(409,'Prices, offers or customer details changed. Review the refreshed total before payment.')
    total=reviewed['total_paise'];cash=sum(t.amount_paise for t in request.tenders if t.method=='cash');external=sum(t.amount_paise for t in request.tenders if t.method!='cash')
    change=cash+external-total
    if change<0 or external>total or change>cash:raise ValueError('Payment must cover the total. Change can be paid only from cash tendered.')
    if total==0 and request.tenders:raise ValueError('No payment is needed for a zero-total basket.')
    id=uid();number=next_number(con,store['id'],reviewed['business_date']);created=now()
    result={**reviewed,'id':id,'number':number,'created':created,'cashier':dict(id=staff['id'],name=staff['name']),'shift_id':request.shift_id,'tenders':[t.model_dump() for t in request.tenders],'change_paise':change,'cash_net_paise':cash-change,'return_policy':'Refund original paid value. If a partial return breaks a conditional offer, include the linked reward or remaining bundle items.','replayed':False}
    con.run('pos_core_complete_sale_insert_pos_sales', (id, store['id'], number, request.shift_id, staff['id'], request.customer_id, reviewed['business_date'], created, reviewed['subtotal_paise'], reviewed['tax_paise'], total, reviewed['discount_paise'], reviewed['cogs_paise'], cash - change, request_hash, request.idempotency_key, json.dumps(result)))
    for line in reviewed['lines']:
        con.run('pos_core_complete_sale_insert_pos_sale_lines', (id, store['id'], line['product_id'], line['quantity'], line['paid_paise'], line['net_paise'], line['tax_paise'], line['cgst_paise'], line['sgst_paise'], line['igst_paise'], line['cost_paise'], line['regular_paise'], line['gst_bps']))
        stock_move(con,store,line['product_id'],-line['quantity'],'sale',id,number,staff['id'],line['cost_paise'])
    for index,offer in enumerate(reviewed['applied_promotions']):con.run('pos_core_complete_sale_insert_pos_sale_offers', (id, index, store['id'], offer['product_id'], offer['kind'], offer['saving_paise'], json.dumps(offer)))
    audit_event(con,store['id'],'sale_completed',id,dict(number=number,total_paise=total,staff_id=staff['id']))
    return result
