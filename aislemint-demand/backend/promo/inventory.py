"""Inventory decisions from demand paths, timed receipts and constrained order choices."""
from __future__ import annotations
from .storage import AssetPath,managed,read_frame,write_frame,load_model,dump_model
import math
import numpy as np
import pandas as pd
from pydantic import BaseModel,ConfigDict,Field
from typing import Literal
from scipy.optimize import milp,Bounds,LinearConstraint
from .demand import DemandRequest


class Receipt(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    quantity:float=Field(gt=0,le=1e8)
    due_date:str
    expiry_date:str|None=None


class StockPosition(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    store_id:str
    product_id:str
    on_hand:float=Field(ge=0,le=1e9)
    reserved:float=Field(default=0,ge=0,le=1e9)
    backlog:float=Field(default=0,ge=0,le=1e9)
    unit_cost:float=Field(gt=0,le=1e6)
    selling_price:float|None=Field(default=None,gt=0,le=1e6)
    lead_time_days:int=Field(default=7,ge=0,le=365)
    lead_time_delay_days:int=Field(default=0,ge=0,le=365)
    review_period_days:int=Field(default=7,ge=1,le=365)
    case_pack:int=Field(default=1,ge=1,le=100000)
    minimum_order:int=Field(default=0,ge=0,le=10000000)
    max_stock:float|None=Field(default=None,ge=0,le=1e9)
    shelf_life_days:int|None=Field(default=None,ge=1,le=3650)
    existing_expiry_date:str|None=None
    receipts:list[Receipt]=Field(default_factory=list,max_length=30)


class InventoryRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    forecast:DemandRequest
    stock_as_of:str
    positions:list[StockPosition]=Field(min_length=1,max_length=200)
    service_level:float=Field(default=.95,ge=.5,le=.995)
    budget:float|None=Field(default=None,ge=0,le=1e9)
    annual_holding_rate:float=Field(default=.2,ge=0,le=2)
    order_fee:float=Field(default=0,ge=0,le=1e6)
    objective:Literal['service','profit']='service'
    name:str=Field(default='Inventory replenishment plan',min_length=1,max_length=120)
    inventory_basis:Literal['user_supplied','illustrative']='user_supplied'


def local_date(date):
    dt=pd.Timestamp(date)
    if pd.isna(dt) or dt.tzinfo is not None or dt!=dt.normalize():raise ValueError('Use valid local calendar dates without times or timezones.')
    return dt


def period_index(date,as_of,step):
    return math.ceil((local_date(date)-as_of).days/step)


def simulate(paths,pos,as_of,step,order_quantity,lead_periods):
    """FIFO cohorts; receipts at period starts, sales lost when unavailable, no fabricated backlog."""
    count,horizon=paths.shape
    arrivals={}
    existing_expiry=math.floor((local_date(pos.existing_expiry_date)-as_of).days/step) if pos.existing_expiry_date else horizon+1
    available=max(0,pos.on_hand-pos.reserved-pos.backlog)
    debt=np.full(count,max(0,pos.reserved+pos.backlog-pos.on_hand),dtype=float)
    cohorts=[dict(quantity=np.full(count,available,dtype=float),expiry=existing_expiry)]
    for receipt in pos.receipts:
        index=period_index(receipt.due_date,as_of,step)
        if index<=0:raise ValueError('Past-due receipts must be confirmed in on-hand stock or rescheduled; they are not assumed available.')
        expiry=math.floor((local_date(receipt.expiry_date)-as_of).days/step) if receipt.expiry_date else horizon+1
        if receipt.expiry_date and local_date(receipt.expiry_date)<=local_date(receipt.due_date):raise ValueError('Receipt expiry must follow its due date.')
        arrivals.setdefault(index,[]).append((receipt.quantity,expiry))
    expiry=lead_periods+math.floor(pos.shelf_life_days/step) if pos.shelf_life_days else horizon+1
    if order_quantity>0:arrivals.setdefault(lead_periods,[]).append((order_quantity,expiry))
    lost=np.zeros_like(paths);ending=np.zeros_like(paths);opening=np.zeros_like(paths);waste=np.zeros_like(paths);pending=np.zeros_like(paths);committed=np.zeros_like(paths)
    for t in range(horizon):
        for cohort in cohorts:
            if cohort['expiry']<=t:
                waste[:,t]+=cohort['quantity'];cohort['quantity']=np.zeros(count)
        for quantity,exp in arrivals.get(t,[]):
            if exp<=t:waste[:,t]+=quantity
            else:cohorts.append(dict(quantity=np.full(count,quantity,dtype=float),expiry=exp))
        # Commitments are settled once before new customer demand.
        for cohort in cohorts:
            allocation=np.minimum(cohort['quantity'],debt);cohort['quantity']-=allocation;debt-=allocation;committed[:,t]+=allocation
        opening[:,t]=sum(c['quantity'] for c in cohorts)
        remaining=paths[:,t].copy()
        for cohort in cohorts:
            take=np.minimum(cohort['quantity'],remaining);cohort['quantity']-=take;remaining-=take
        lost[:,t]=remaining;ending[:,t]=sum(c['quantity'] for c in cohorts);pending[:,t]=debt
    return dict(lost=lost,ending=ending,opening=opening,waste=waste,pending=pending,committed=committed)


def replenishment_plan(demand_engine,request):
    result,path_bank=demand_engine.forecast(request.forecast,include_paths=True)
    series={(s['store_id'],s['product_id']):s for s in result['series']}
    keys=[(p.store_id,p.product_id) for p in request.positions]
    if len(keys)!=len(set(keys)):raise ValueError('One inventory position per product/store is required.')
    if set(keys)-set(series):raise ValueError('Inventory positions must belong to the forecast product/store scope.')
    reward_keys={(p['store_id'],r['product_id']) for p in result.get('offer_profiles',[]) if (p['store_id'],p['product_id']) in keys for r in p['rewards']}
    missing=reward_keys-set(keys)
    if missing:raise ValueError('Supply stock and landed cost for every choice reward product: '+', '.join(f'{p} at store {s}' for s,p in sorted(missing)))
    as_of=pd.Timestamp(result['as_of']);step=result['step_days'];horizon=result['horizon']
    if local_date(request.stock_as_of)!=as_of:raise ValueError('Inventory snapshot date must match the demand forecast origin. Update the sales history or use a matching stock snapshot.')
    candidates=[];details=[]
    for index,pos in enumerate(request.positions):
        key=(pos.store_id,pos.product_id);s=series[key];paths=path_bank[key]
        lead=math.ceil((pos.lead_time_days+pos.lead_time_delay_days)/step)
        review=math.ceil(pos.review_period_days/step);protection=lead+review
        if protection>horizon:raise ValueError(f'{pos.product_id}: forecast at least {protection} periods to cover lead time plus review period.')
        if pos.shelf_life_days is not None and pos.shelf_life_days<step:
            raise ValueError('Shelf life shorter than the data period needs daily data; weekly allocation is not reliable.')
        base=simulate(paths,pos,as_of,step,0,lead)
        needed=base['lost'][:,lead:protection].sum(axis=1)+base['pending'][:,protection-1]
        raw_target=float(np.quantile(needed,request.service_level,method='higher'))
        target=math.ceil(max(0,raw_target)/pos.case_pack)*pos.case_pack
        if target>0:target=math.ceil(max(target,pos.minimum_order)/pos.case_pack)*pos.case_pack
        quantities={0,target}
        if target>0:
            max_packs=target//pos.case_pack
            grid=range(1,max_packs+1) if max_packs<=128 else np.linspace(1,max_packs,128).astype(int)
            quantities.update(int(packs)*pos.case_pack for packs in grid if packs*pos.case_pack>=pos.minimum_order)
            minimum=math.ceil(max(pos.case_pack,pos.minimum_order)/pos.case_pack)*pos.case_pack
            quantities.add(minimum)
            if request.budget is not None:
                affordable=math.floor(max(0,request.budget-request.order_fee)/pos.unit_cost/pos.case_pack)*pos.case_pack
                if affordable>=minimum:quantities.add(min(target,affordable))
            if pos.max_stock is not None:
                # At arrival, stock increases one-for-one with this order after pending commitments.
                debt_at_arrival=base['pending'][:,lead-1] if lead else np.full(len(paths),max(0,pos.reserved+pos.backlog-pos.on_hand))
                capacity=math.floor(max(0,float(np.min(pos.max_stock-base['opening'][:,lead]+debt_at_arrival)))/pos.case_pack)*pos.case_pack
                if capacity>=minimum:quantities.add(min(target,capacity))
        demand_total=paths[:,:protection].sum(axis=1)
        point=np.array([r['forecast'] for r in s['rows']])
        prices=np.array([r['price'] for r in s['rows']])
        if pos.selling_price is not None:
            regular=np.array([r.get('regular_price',r['price']) for r in s['rows']])
            prices=pos.selling_price*prices/regular
        notes=[]
        if pos.lead_time_delay_days:notes.append(f'Includes a {pos.lead_time_delay_days}-day supplier delay stress.')
        if step>1 and ((pos.lead_time_days+pos.lead_time_delay_days)%step or pos.review_period_days%step):
            notes.append('Lead/review times rounded up to whole weekly periods; receipts become usable at the next period boundary.')
        if pos.existing_expiry_date is None and pos.shelf_life_days is not None:notes.append('Existing stock expiry was not supplied; only new-order expiry is modeled.')
        base_shortfall=float(base['lost'][:,:protection].sum(axis=1).mean())
        base_value=float(((paths[:,:protection]-base['lost'][:,:protection]+base['committed'][:,:protection])*(prices[:protection]-pos.unit_cost)).sum(axis=1).mean())
        base_holding=float(((base['opening'][:,:protection]+base['ending'][:,:protection])/2).sum(axis=1).mean()*pos.unit_cost*request.annual_holding_rate*step/365.25)
        base_waste=float(base['waste'][:,:protection].sum(axis=1).mean()*pos.unit_cost)
        accepted=[]
        for qty in sorted(quantities):
            sim=base if qty==0 else simulate(paths,pos,as_of,step,qty,lead)
            if qty and pos.max_stock is not None and (sim['opening'][:,lead:protection]>pos.max_stock+1e-8).any():continue
            shortfall=float(sim['lost'][:,:protection].sum(axis=1).mean())
            margin=float(((paths[:,:protection]-sim['lost'][:,:protection]+sim['committed'][:,:protection])*(prices[:protection]-pos.unit_cost)).sum(axis=1).mean())
            holding=float(((sim['opening'][:,:protection]+sim['ending'][:,:protection])/2).sum(axis=1).mean()*pos.unit_cost*request.annual_holding_rate*step/365.25)
            waste=float(sim['waste'][:,:protection].sum(axis=1).mean()*pos.unit_cost)
            fee=request.order_fee if qty else 0
            incremental=margin-base_value-(holding-base_holding)-(waste-base_waste)-fee
            post_service=float((sim['lost'][:,lead:protection].sum(axis=1)+sim['pending'][:,protection-1]<1e-8).mean())
            # Service mode prioritizes shortage reduction and reaching the requested cycle target.
            score=incremental if request.objective=='profit' else ((base_shortfall-shortfall+float((base['pending'][:,protection-1]-sim['pending'][:,protection-1]).mean()))*max(pos.unit_cost,1)
                +min(post_service,request.service_level)*max(float(demand_total.mean()),1)*max(pos.unit_cost,1)-qty*pos.unit_cost*1e-7)
            row=dict(position=index,quantity=qty,purchase_cost=qty*pos.unit_cost,order_fee=fee,score=score,
                shortfall=shortfall,incremental_contribution=incremental,holding_cost=holding,expected_writeoff=waste,
                estimated_cycle_service=float((sim['lost'][:,:protection].sum(axis=1)+sim['pending'][:,protection-1]<1e-8).mean()),post_arrival_service=post_service,
                fill_rate=1-shortfall/float(demand_total.mean()) if demand_total.mean()>0 else 1)
            candidates.append(row);accepted.append(row)
        on_time=sum(r.quantity for r in pos.receipts if period_index(r.due_date,as_of,step)<protection)
        details.append(dict(position=pos,series=s,paths=paths,lead=lead,protection=protection,target=target,notes=notes,prices=prices,
            expected_cycle_demand=float(point[:protection].sum()),
            reorder_point=float(np.quantile(paths[:,:lead].sum(axis=1),request.service_level)) if lead else 0,
            order_up_to=float(np.quantile(demand_total,request.service_level)),
            safety_stock=max(0,float(np.quantile(demand_total,request.service_level)-point[:protection].sum())),
            inventory_position=pos.on_hand-pos.reserved-pos.backlog+on_time,
            lead_shortfall=float(base['lost'][:,:lead].sum(axis=1).mean()),
            lead_risk=float((base['lost'][:,:lead].sum(axis=1)>1e-8).mean()) if lead else 0,
            constrained=max(x['quantity'] for x in accepted)<target))
    n=len(candidates);constraints=[];lower=[];upper=[]
    for i in range(len(details)):
        constraints.append([int(c['position']==i) for c in candidates]);lower.append(1);upper.append(1)
    if request.budget is not None:
        constraints.append([c['purchase_cost']+c['order_fee'] for c in candidates]);lower.append(-np.inf);upper.append(request.budget)
    solution=milp(c=-np.array([c['score'] for c in candidates]),integrality=np.ones(n),bounds=Bounds(np.zeros(n),np.ones(n)),
        constraints=LinearConstraint(np.array(constraints),lower,upper),options={'time_limit':10})
    selected=[c for i,c in enumerate(candidates) if solution.x is not None and solution.x[i]>.5]
    if len(selected)!=len(details):
        selected=[next(c for c in candidates if c['position']==i and c['quantity']==0) for i in range(len(details))]
    rows=[]
    for choice in selected:
        d=details[choice['position']];pos=d['position'];sim=simulate(d['paths'],pos,as_of,step,choice['quantity'],d['lead']);notes=list(d['notes'])
        if d['lead_risk']>1-request.service_level:notes.append('Current supply risks a stockout before normal replenishment arrives; expedite or transfer stock.')
        if choice['quantity']<d['target']:notes.append('Budget, capacity or profit objective reduced the service-target order.')
        if choice['post_arrival_service']+1e-8<request.service_level:notes.append('The requested post-arrival service target is not met in the sampled demand paths.')
        if pos.max_stock is not None and (sim['opening']>pos.max_stock+1e-8).any():notes.append('Existing stock or inbound receipts exceed the available-stock limit in some paths.')
        if np.any(d['prices']<pos.unit_cost):notes.append('Some selling prices are below unit cost; check markdown economics.')
        if pos.minimum_order and choice['quantity']>d['target']:notes.append('Minimum order quantity increases stock above the unconstrained need.')
        projection=[]
        for t,row in enumerate(d['series']['rows']):
            lo,hi=np.quantile(sim['ending'][:,t],[.05,.95])
            projection.append(dict(date=row['date'],demand=row['forecast'],ending_stock=float(sim['ending'][:,t].mean()),
                lower_stock=float(lo),upper_stock=float(hi),expected_lost_sales=float(sim['lost'][:,t].mean()),expected_waste=float(sim['waste'][:,t].mean()),
                stockout_risk=float((sim['lost'][:,t]>1e-8).mean()),in_protection_period=t<d['protection']))
        action='Expedite + replenish' if d['lead_risk']>1-request.service_level else 'Order now' if choice['quantity'] else 'Hold'
        first_risk=next((r['date'] for r in projection[:d['protection']] if r['stockout_risk']>1-request.service_level),None)
        rows.append(dict(store_id=pos.store_id,product_id=pos.product_id,name=d['series']['name'],action=action,
            on_hand=pos.on_hand,reserved=pos.reserved,backlog=pos.backlog,unit_cost=pos.unit_cost,
            order_quantity=choice['quantity'],unconstrained_order=d['target'],purchase_cost=choice['purchase_cost'],order_fee=choice['order_fee'],
            arrival_date=str((as_of+pd.Timedelta(days=d['lead']*step)).date()),
            next_review_date=str((as_of+pd.Timedelta(days=math.ceil(pos.review_period_days/step)*step)).date()),
            expected_cycle_demand=d['expected_cycle_demand'],inventory_position=d['inventory_position'],
            reorder_point=d['reorder_point'],order_up_to=d['order_up_to'],safety_stock=d['safety_stock'],
            expected_shortfall=choice['shortfall'],lead_time_shortfall=d['lead_shortfall'],first_risk_date=first_risk,
            estimated_cycle_service=choice['estimated_cycle_service'],post_arrival_service=choice['post_arrival_service'],fill_rate=choice['fill_rate'],
            incremental_contribution=choice['incremental_contribution'],holding_cost=choice['holding_cost'],expected_writeoff=choice['expected_writeoff'],
            excess_at_next_receipt=float(sim['ending'][:,d['protection']-1].mean()),notes=notes,projection=projection))
    spend=sum(r['purchase_cost']+r['order_fee'] for r in rows)
    if request.budget is not None and spend>request.budget+1e-6:raise ValueError('Solver returned an order portfolio outside the budget.')
    return dict(name=request.name,currency=result.get('currency','USD'),calculation_version='inventory-v1',model_id=result['model_id'],as_of=result['as_of'],source_id=request.forecast.source_id,
        frequency=result['frequency'],step_days=step,service_target=request.service_level,inventory_basis=request.inventory_basis,
        total_purchase=sum(r['purchase_cost'] for r in rows),total_order_fees=sum(r['order_fee'] for r in rows),budget_used=spend,
        budget_remaining=None if request.budget is None else request.budget-spend,
        expected_shortfall=sum(r['expected_shortfall'] for r in rows),incremental_contribution=sum(r['incremental_contribution'] for r in rows),
        stock_risk_positions=sum(r['first_risk_date'] is not None for r in rows),orders=sum(r['order_quantity']>0 for r in rows),
        rows=rows,forecast=result,solver_status=int(solution.status),
        assumptions=['Draft purchase suggestions only; no orders are sent to suppliers.',
            'Service target is the modeled probability of no stockout after this order arrives through the lead-plus-review protection window; it is not a fill-rate guarantee.',
            'Unmet new customer demand is treated as lost sales. Existing commitments are settled once before new demand.',
            'Receipts arrive at period boundaries; weekly data cannot resolve within-week stockouts. Delay is a user-specified stress, not a learned lead-time distribution.',
            'Projections assume this order and listed inbound only. Future review orders must be replanned; financial comparisons cover the protection period.',
            'The optimizer compares case-pack order choices up to the service-target quantity; large ranges use a bounded grid. It does not optimize an entire future purchasing calendar.',
            'Purchase spend consumes budget; COGS is recognized on served units and expiry writeoffs, so unsold inventory is not charged twice.',
            'Outstanding commitments served from future receipts use the modeled period selling price for contribution; supply contract-specific prices upstream when they differ.',
            'Choice reward products require stock and cost inputs. Per-product service does not guarantee complete-basket fulfillment when a qualifier or reward is unavailable; substitution and correlated redemption require transaction validation.',
            'Residual-path risk and inventory inputs require client validation. Costs, stock and supplier lead times are not present in the public benchmark.']+result.get('assumptions',[]))
