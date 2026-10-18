from __future__ import annotations
from .storage import AssetPath,managed,read_frame,write_frame,load_model,dump_model
from functools import cached_property
import json
from pathlib import Path
import numpy as np
import pandas as pd
import joblib
from pydantic import BaseModel, Field, ConfigDict
from typing import Literal
from scipy.optimize import milp, Bounds, LinearConstraint
from .data import DATA, ARTIFACTS
from .model import exposures, clean_baseline_mask, metrics
from .offer_mechanics import OfferTerms,ConditionalOffer,terms_dict,apply_schedule,apply_rewards

CALCULATION_VERSION='stacked-offers-v3'

def demand_effects(delta,active,targeted):
    # Split each SKU/store/week BEFORE aggregation: netting can hide forward-buy dips.
    spill=~targeted;tail=targeted & ~active
    return dict(direct=float(delta[active].sum()),
        cannibalization=float(np.minimum(delta[spill],0).sum()),
        halo=float(np.maximum(delta[spill],0).sum()),
        pantry_loading=float(np.minimum(delta[tail],0).sum()),
        post_promo_uplift=float(np.maximum(delta[tail],0).sum()))

class Promotion(OfferTerms):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    product_id: str
    discount: float=Field(default=.2,ge=0,le=.5)
    duration: int=Field(default=2,ge=1,le=4)
    feature: bool=False
    display: bool=False
    fixed_cost: float=Field(default=0,ge=0,le=1e8)
    funding_per_unit: float=Field(default=0,ge=0,le=1e4)

class Scenario(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    store_id: str='367'
    start_date: str|None=None
    promotions: list[Promotion]=Field(min_length=1,max_length=12)
    margin_rate: float=Field(default=.35,ge=.01,le=.95)
    inventory_units: float|None=Field(default=None,gt=0,le=1e9)
    name: str=Field(default='Untitled scenario',min_length=1,max_length=120)

class OptimizeRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    store_id: str='367'
    product_ids: list[str]=Field(default_factory=list,max_length=12)
    budget: float=Field(default=500,ge=0,le=1e8)
    margin_rate: float=Field(default=.35,ge=.01,le=.95)
    min_margin: float=Field(default=.1,ge=0,le=.9)
    max_promotions: int=Field(default=3,ge=1,le=6)
    objective: Literal['profit','revenue','roi']='profit'
    inventory_units: float|None=Field(default=None,gt=0)
    fixed_cost: float=Field(default=0,ge=0,le=1e6)
    funding_per_unit: float=Field(default=0,ge=0,le=1e4)
    mechanics:list[ConditionalOffer]=Field(default_factory=list,max_length=6)
    normal_basket_units:float=Field(default=2,ge=1,le=100)
    redemption_rate:float|None=Field(default=None,ge=0,le=1)
    stacking_policy:Literal['exclusive','stack','best_offer']='exclusive'

class Engine:
    def __init__(self,panel_path=None,artifact_dir=None):
        self.artifacts=managed(artifact_dir or ARTIFACTS)
        self.panel=read_frame(panel_path or DATA / 'processed/panel.parquet')
        from .data import market_metadata
        self.currency=market_metadata(self.panel)['currency']
        self.model=load_model(self.artifacts / 'model.joblib')
        self.card=json.loads((self.artifacts/'model_card.json').read_text())
        self.last=self.panel.date.max()
        self.start=self.panel.date.min()

    def scope(self,store_id):
        if store_id=='all':return self.panel
        d=self.panel[self.panel.store_id==store_id]
        if d.empty:raise ValueError('Unknown store.')
        return d

    def catalog(self,store_id='all'):
        d=self.scope(store_id)
        recent=d[d.date>=self.last-pd.Timedelta(weeks=12)]
        items=[]
        for pid,g in recent.groupby('product_id'):
            full=d[d.product_id==pid]
            promo=full[full.promoted==1]
            items.append(dict(product_id=pid,name=str(g.name.iloc[0]),category=str(g.category.iloc[0]),
                base_price=float(g.sort_values('date').groupby('store_id').tail(1).base_price.mean()),
                avg_weekly_units=float(g.groupby('date').units.sum().mean()),
                promo_observations=len(promo),min_discount=float(promo.discount.min()) if len(promo) else 0,
                max_discount=float(promo.discount.max()) if len(promo) else 0))
        return sorted(items,key=lambda x:-x['avg_weekly_units'])

    def _future(self,req:Scenario,calendar=None):
        hist=self.scope(req.store_id)
        start=pd.Timestamp(req.start_date) if req.start_date else self.last+pd.Timedelta(weeks=1)
        if pd.isna(start) or start.tzinfo is not None:raise ValueError('Use a valid date without a timezone.')
        weeks_ahead=(start-self.last).days/7
        if weeks_ahead<1 or weeks_ahead>13 or not weeks_ahead.is_integer():
            raise ValueError('Start must be on the weekly cadence, between 1 and 13 weeks after the data end.')
        if len({p.product_id for p in req.promotions})!=len(req.promotions):raise ValueError('One promotion per product per scenario.')
        latest=hist.sort_values('date').groupby(['store_id','product_id']).tail(1).copy()
        latest=latest[latest.date>=self.last-pd.Timedelta(weeks=4)]
        unknown={p.product_id for p in req.promotions}-set(latest.product_id)
        if unknown:raise ValueError('Unknown or inactive products: '+', '.join(sorted(unknown)))
        horizon=max(p.duration for p in req.promotions)+4
        future=[]
        for dt in pd.date_range(self.last+pd.Timedelta(weeks=1),start+pd.Timedelta(weeks=horizon-1),freq='7D'):
            x=latest.copy();x['date']=dt;x['discount']=0.;x['feature']=0;x['display']=0;x['price']=x.base_price;x['promoted']=0
            x['holiday']=0
            if 'promotion_available' in x:x['promotion_available']=0
            for holiday in (calendar or {}).get('local_holidays',[]):
                if dt-pd.Timedelta(days=7)<pd.Timestamp(holiday['date'])<=dt:
                    x.loc[x.store_id.eq(holiday.get('store_id')) if holiday.get('store_id') else x.index==x.index,'holiday']=1
            future.append(x)
        base=pd.concat([hist[hist.date>self.last-pd.Timedelta(weeks=4)],*future],ignore_index=True)
        existing={}
        for event in (calendar or {}).get('events',[]):
            dt=pd.Timestamp(event['period_end'])
            if dt<=self.last or dt>base.date.max():continue
            key=(event['store_id'],event['product_id'],dt)
            existing.setdefault(key,[]).append(event.get('terms') or terms_dict(event))
        base,base_profiles=apply_schedule(base,existing)
        schedule={k:list(v) for k,v in existing.items()}
        for p in req.promotions:
            mask=(base.product_id==p.product_id)&(base.date>=start)&(base.date<start+pd.Timedelta(weeks=p.duration))
            for r in base[mask].itertuples():schedule.setdefault((str(r.store_id),p.product_id,r.date),[]).append(terms_dict(p))
        planned,profiles=apply_schedule(base,schedule)
        # Rebuild every product's exposure under each entire portfolio, including lag propagation.
        b=exposures(base);s=exposures(planned)
        select=b.date>=start
        b=b[select].reset_index(drop=True);s=s[select].reset_index(drop=True)
        b.attrs['offer_profiles']=base_profiles;s.attrs['offer_profiles']=profiles
        return b,s,start

    def forecast(self,req:Scenario,detail=True,calendar=None):
        b,s,start=self._future(req,calendar)
        base_profiles=b.attrs.get('offer_profiles',[]);profiles=s.attrs.get('offer_profiles',[])
        b['q']=self.model.predict(b);s['q']=self.model.predict(s)
        raw_bq=b.q.to_numpy().copy();raw_sq=s.q.to_numpy().copy()
        raw_bp=b.price.to_numpy().copy();raw_sp=s.price.to_numpy().copy()
        b,base_rewards=apply_rewards(b,base_profiles,'q');s,rewards=apply_rewards(s,profiles,'q')
        delta=s.q-b.q
        modeled_delta=pd.Series(raw_sq-raw_bq,index=s.index)
        targets={p.product_id for p in req.promotions}
        targeted=s.product_id.isin(targets)
        active=pd.Series(False,index=s.index)
        funding=np.zeros(len(s));fixed=sum(p.fixed_cost for p in req.promotions)
        warnings=[]
        if any(p.mechanics for p in req.promotions):warnings.append('Conditional redemption uses synthetic training or supplied assumptions. Effective discount is a proxy into models trained on observed price promotions; conditional demand and reward choice are not validated on client transactions. Prediction bands do not include all redemption and choice-share uncertainty.')
        for p in req.promotions:
            mask=(s.product_id==p.product_id)&(s.date<start+pd.Timedelta(weeks=p.duration))
            active|=mask
            funding[mask]=p.funding_per_unit*s.loc[mask,'q']
            train=self.panel[(self.panel.product_id==p.product_id)&(self.panel.date<=pd.Timestamp(self.model.trained_until))]
            if req.store_id!='all':train=train[train.store_id==req.store_id]
            discounts=[v['effective_discount'] for v in profiles if v['product_id']==p.product_id]
            effective=float(np.mean(discounts)) if discounts else p.discount
            matching=train[(abs(train.discount-effective)<=.05)&train.feature.eq(int(p.feature))&train.display.eq(int(p.display))]
            if len(matching)<8:warnings.append(f'{p.product_id}: only {len(matching)} comparable training observations; promotion response has limited support.')
            if max(discounts,default=p.discount)>float(train.discount.max())+.01:warnings.append(f'{p.product_id}: effective discount exceeds the observed training range.')
        costs=s.unit_cost.to_numpy() if 'unit_cost' in s else s.base_price.to_numpy()*(1-req.margin_rate)
        assumed='unit_cost' not in s
        row_ids={(str(r.store_id),str(r.product_id),str(r.date.date())):r.Index for r in s.itertuples()}
        existing_funding_rate=np.zeros(len(s))
        for event in (calendar or {}).get('events',[]):
            i=row_ids.get((event['store_id'],event['product_id'],event['period_end']))
            if i is not None:existing_funding_rate[i]+=event.get('funding_per_unit',0)
        base_funding=existing_funding_rate*b.q.to_numpy()
        funding+=existing_funding_rate*s.q.to_numpy()
        margins=[]
        for profile in profiles:
            i=row_ids.get((profile['store_id'],profile['product_id'],profile['date']))
            if i is None or not active.iloc[i]:continue
            reward_cost_per_unit=sum(r['units_per_qualifier']*costs[row_ids[(profile['store_id'],r['product_id'],profile['date'])]] for r in profile['rewards'])
            margins.append((raw_sp[i]-costs[i]-reward_cost_per_unit)/raw_sp[i] if raw_sp[i]>0 else -1.)
        profit=s.q.to_numpy()*(s.price.to_numpy()-costs)+funding
        base_profit=b.q.to_numpy()*(b.price.to_numpy()-costs)+base_funding
        profit_delta=profit-base_profit
        investment=float(np.maximum(s.q*(s.base_price-s.price)-b.q*(b.base_price-b.price),0).sum()+fixed)
        netprofit=float(profit_delta.sum()-fixed)
        promoted_sales=float(s.loc[active,'q'].sum())
        baseline_sales=float(b.loc[active,'q'].sum())
        lost=0 if req.inventory_units is None else max(0,promoted_sales-req.inventory_units)
        if lost>0:warnings.append('Expected promoted-item demand exceeds the supplied inventory. This scenario is infeasible; demand is not silently capped.')
        if assumed:warnings.append('Unit costs are estimated from the supplied margin; financial results are assumption-based.')
        # Positive/negative spillovers are distinguished; no unmeasured uplift percentage is injected.
        spill=~targeted
        tail=targeted & ~active
        effects=demand_effects(modeled_delta,active,targeted)
        if rewards or base_rewards:effects['choice_rewards']=float((delta-modeled_delta).sum())
        # A planning policy, not a sign constraint on the fitted ML response.
        # Retain every post-offer loss; do not fund decisions with unvalidated tail gains.
        excluded_profit=float(np.maximum(profit_delta[tail],0).sum())
        revenue_delta=s.q*s.price-b.q*b.price
        excluded_revenue=float(np.maximum(revenue_delta[tail],0).sum())
        planning_profit=netprofit-excluded_profit
        response_profit_delta=raw_sq*(raw_sp-costs)+funding-raw_bq*(raw_bp-costs)-base_funding
        effect_profit=dict(direct=float(response_profit_delta[active].sum()),
            cannibalization=float(response_profit_delta[spill & (modeled_delta<0)].sum()),
            halo=float(response_profit_delta[spill & (modeled_delta>=0)].sum()),
            pantry_loading=float(response_profit_delta[tail & (modeled_delta<0)].sum()),
            post_promo_uplift=float(response_profit_delta[tail & (modeled_delta>=0)].sum()),fixed_cost=-fixed)
        if rewards or base_rewards:effect_profit['choice_reward_cost']=float((profit_delta-response_profit_delta).sum())
        weekrows=[]
        for dt,ids in s.groupby('date').groups.items():
            ids=list(ids);mask=targeted.loc[ids];tids=list(mask[mask].index)
            width=float((np.array([self.model.q_product.get(k,self.model.q) for k in s.loc[tids,'product_id']])*np.sqrt(np.maximum(s.loc[tids,'q'],1))).sum())
            q=float(s.loc[tids,'q'].sum())
            weekrows.append(dict(date=str(dt.date()),baseline=float(b.loc[tids,'q'].sum()),forecast=q,lower=max(0,q-width),upper=q+width,
                phase='Promotion' if active.loc[ids].any() else 'Post-promotion',
                portfolio_incremental=float(delta.loc[ids].sum())))
        result=dict(name=req.name,model_id=self.model.model_id,calculation_version=CALCULATION_VERSION,store_id=req.store_id,start_date=str(start.date()),
            currency=self.currency,baseline_units=baseline_sales,total_units=promoted_sales,incremental_units=promoted_sales-baseline_sales,
            portfolio_net_units=float(delta.sum()),lift=(promoted_sales/baseline_sales-1) if baseline_sales else 0,
            total_revenue=float((s.loc[active,'q']*s.loc[active,'price']).sum()),
            incremental_revenue=float(revenue_delta.sum()),incremental_profit=netprofit,
            baseline_contribution=float(base_profit.sum()),planned_contribution=float(profit.sum()-fixed),
            investment=investment,roi=netprofit/investment if investment>1e-8 else None,
            supplier_funding=float(funding.sum()),fixed_cost=fixed,assumed_costs=assumed,
            baseline_supplier_funding=float(base_funding.sum()),incremental_supplier_funding=float(funding.sum()-base_funding.sum()),
            minimum_offer_margin=min(margins,default=req.margin_rate),
            feasible=lost==0,inventory_shortfall=lost,effects=effects,
            effect_profit=effect_profit,
            planning_effect_profit={**effect_profit,'uncredited_post_promo_gain':-excluded_profit},
            planning_profit=planning_profit,planning_roi=planning_profit/investment if investment>1e-8 else None,
            planning_revenue=float(revenue_delta.sum())-excluded_revenue,
            excluded_post_promo_profit=excluded_profit,excluded_post_promo_revenue=excluded_revenue,
            planning_policy='Retain post-offer losses; exclude positive post-offer profit and revenue contributions on promoted products. Raw model forecasts remain unchanged.',
            warnings=warnings,interval_note=self.card['intervals'],weeks=weekrows,
            offer_profiles=profiles,reward_allocations=rewards,
            recommendation_basis='Exploratory conditional-offer assumptions' if any(p.mechanics for p in req.promotions) else 'Observed price-promotion response',
            promotion_calendar=dict(revision=(calendar or {}).get('revision',0),campaign_ids=sorted({e['campaign_id'] for e in (calendar or {}).get('events',[])})),
            assumptions=dict(margin_rate=req.margin_rate,inventory_units=req.inventory_units,post_promo_weeks=4,other_future_promotions='Confirmed calendar retained in both comparisons' if calendar else 'None',prices='Latest observed base price held constant',effects='Conditional model estimates, not proven causal attribution',
                conditional_offers='TPR first; highest overlapping TPR. Same conditional group uses greatest expected benefit; distinct groups multiply remaining expected prices. Basket distribution and joint redemption are assumptions.',
                choice_rewards='One free reward per qualifying basket; choice shares default to equal. Reward units first replace paid reward purchases, then add any remaining physical units. Gifts do not recursively qualify for rewards.',
                pantry_loading='Negative post-offer differences are a forward-buy proxy, not identified household stockpiling. Dips may include cross-effects. Four weeks may miss later losses.'))
        if detail:
            changes=s[['product_id','name','category']].copy();changes['delta']=delta;changes['profit']=profit_delta
            result['affected_products']=[dict(product_id=k,name=str(g['name'].iloc[0]),category=str(g.category.iloc[0]),incremental_units=float(g.delta.sum()),incremental_profit=float(g.profit.sum()),promoted=k in targets)
                for k,g in changes.groupby('product_id') if abs(g.delta.sum())>.001]
            result['affected_products'].sort(key=lambda x:-abs(x['incremental_units']))
        return result

    def hindsight(self,product_id,store_id='367'):
        scope=self.scope(store_id)
        end=self.last;start=pd.Timestamp(self.card['folds'][-1]['test_start'])
        if product_id not in set(scope.product_id):raise ValueError('Unknown product in this store.')
        d=scope[scope.date>=start-pd.Timedelta(weeks=4)].copy()
        counter=d.copy();mask=(counter.product_id==product_id)&(counter.date>=start)
        counter.loc[mask,['discount','feature','display']]=0
        f=exposures(d);b=exposures(counter)
        test=f.date>=start;f=f[test].reset_index(drop=True);b=b[test].reset_index(drop=True)
        pred=self.model.predict(f);base=self.model.predict(b);target=f.product_id==product_id
        active=target & f.promoted.eq(1);spill=~target;delta=pred-base
        residual=float((f.loc[target,'units']-pred[target]).sum())
        clean_target=target & clean_baseline_mask(f)
        baseline_validation=dict(rows=int(clean_target.sum()),
            metrics=metrics(f.loc[clean_target,'units'],self.model.baseline_predict(f[clean_target])) if clean_target.any() and hasattr(self.model,'baseline_predict') else None,
            limited_support=int(clean_target.sum())<10,
            interpretation='Checks use actual non-promoted weeks only; promotion-period baseline remains unobservable.')
        effects=demand_effects(delta,active,target)
        own=f[target].copy()
        if own.empty:raise ValueError('No observations for this product in the evaluation window.')
        own['prediction']=pred[target];own['baseline']=base[target]
        own['actual_revenue']=own['SPEND'] if 'SPEND' in own else own.units*own.price
        own['baseline_revenue']=own.baseline*own.base_price
        own['incremental']=own.units-own.baseline
        own['incremental_revenue']=own.actual_revenue-own.baseline_revenue
        series=own.groupby('date').agg(actual=('units','sum'),baseline=('baseline','sum'),prediction=('prediction','sum'),discount=('discount','mean'),promoted=('promoted','max'),incremental=('incremental','sum'),
            actual_revenue=('actual_revenue','sum'),baseline_revenue=('baseline_revenue','sum'),incremental_revenue=('incremental_revenue','sum')).reset_index()
        series.date=series.date.dt.strftime('%Y-%m-%d')
        events=[];current=[]
        for row in series.to_dict('records'):
            if row['promoted']:current.append(row)
            elif current:events.append(current);current=[]
        if current:events.append(current)
        campaigns=[dict(start=e[0]['date'],end=e[-1]['date'],weeks=len(e),actual=sum(r['actual'] for r in e),baseline=sum(r['baseline'] for r in e),incremental=sum(r['actual']-r['baseline'] for r in e),discount=float(np.mean([r['discount'] for r in e])),actual_revenue=sum(r['actual_revenue'] for r in e),
            baseline_revenue=sum(r['baseline_revenue'] for r in e),incremental_revenue=sum(r['incremental_revenue'] for r in e)) for e in events]
        return dict(product_id=product_id,name=str(own['name'].iloc[0]),store_id=store_id,start=str(start.date()),end=str(end.date()),
            actual=float(own.units.sum()),baseline=float(own.baseline.sum()),incremental=float((own.units-own.baseline).sum()),
            actual_revenue=float(own.actual_revenue.sum()),baseline_revenue=float(own.baseline_revenue.sum()),incremental_revenue=float(own.incremental_revenue.sum()),
            promotion_actual=float(own.loc[own.promoted==1,'units'].sum()),promotion_baseline=float(own.loc[own.promoted==1,'baseline'].sum()),
            promotion_incremental=float(own.loc[own.promoted==1,'incremental'].sum()),
            baseline_validation=baseline_validation,
            baseline_source=self.card.get('baseline',{'family':'ML counterfactual demand model','human_entered':False}),
            calculation='actual_units - ml_baseline_units',
            modeled_incremental=float(delta[target].sum()),unexplained=residual,effects=effects,portfolio_net_units=float(delta.sum()),calculation_version=CALCULATION_VERSION,
            series=series.to_dict('records'),campaigns=campaigns,model_id=self.model.model_id,
            evidence='Out-of-time counterfactual estimate. Actual minus baseline includes unexplained demand variation; modeled effects are not causal proof.',
            boundary_note='Campaigns are truncated at the holdout boundary; four weeks of earlier exposure are retained. Missing product/store weeks are not filled as zero sales.')

    def optimize(self,req:OptimizeRequest,calendar=None):
        catalog=self.catalog(req.store_id);known={p['product_id']:p for p in catalog}
        ids=req.product_ids or [p['product_id'] for p in catalog[:8]]
        if len(set(ids))!=len(ids):raise ValueError('Duplicate product choices.')
        if set(ids)-known.keys():raise ValueError('Unknown product choices.')
        candidates=[];rejected=0
        for pid in ids:
            if any(pid in o.reward_choices for o in req.mechanics):continue
            for discount in ([0,.05,.10,.15,.20,.25,.30] if req.mechanics else [.10,.15,.20,.25,.30]):
                # Margin floor accounts for markdown before vendor funding.
                history=self.scope(req.store_id)
                latest=history[history.product_id==pid].sort_values('date').groupby('store_id').tail(1)
                costs=latest.unit_cost if 'unit_cost' in latest else latest.base_price*(1-req.margin_rate)
                realized_margin=(latest.base_price*(1-discount)-costs)/(latest.base_price*(1-discount))
                if (realized_margin<req.min_margin).any():continue
                for duration in [1,2]:
                    p=Promotion(product_id=pid,discount=discount,duration=duration,fixed_cost=req.fixed_cost,funding_per_unit=req.funding_per_unit,
                        mechanics=req.mechanics,normal_basket_units=req.normal_basket_units,redemption_rate=req.redemption_rate,stacking_policy=req.stacking_policy)
                    scenario=Scenario(store_id=req.store_id,promotions=[p],margin_rate=req.margin_rate,inventory_units=req.inventory_units)
                    try:r=self.forecast(scenario,detail=False,calendar=calendar)
                    except ValueError as exc:
                        if 'overlap' in str(exc):rejected+=1;continue
                        raise
                    # Include conditional markdown and choice reward cost in the offer margin floor.
                    if (req.mechanics or (calendar or {}).get('events')) and r['minimum_offer_margin']<req.min_margin:rejected+=1;continue
                    # Limited overlap is retained for exploration but excluded from recommended plans.
                    unsupported=any('comparable training' in w or 'exceeds the observed' in w for w in r['warnings'])
                    if r['planning_profit']<=0 or not r['feasible'] or unsupported:rejected+=1;continue
                    candidates.append(dict(promotion=p.model_dump(),name=known[pid]['name'],category=known[pid]['category'],**{k:r[k] for k in ['investment','incremental_profit','incremental_revenue','roi','total_units','planning_profit','planning_revenue','planning_roi']}))
        if not candidates:
            return dict(selected=[],candidates=[],forecast=None,reason='No supported, profitable promotion satisfies these constraints. Keep the no-promotion plan.',evaluated_products=len(ids),rejected=rejected,solver='No-promotion fallback')
        n=len(candidates);objective=np.array([x['planning_revenue'] if req.objective=='revenue' else (x['planning_roi'] or 0) if req.objective=='roi' else x['planning_profit'] for x in candidates])
        rows=[[x['investment'] for x in candidates],[1]*n];upper=[req.budget,req.max_promotions]
        for pid in ids:
            rows.append([int(x['promotion']['product_id']==pid) for x in candidates]);upper.append(1)
        if req.inventory_units is not None:rows.append([x['total_units'] for x in candidates]);upper.append(req.inventory_units)
        result=milp(c=-objective,integrality=np.ones(n),bounds=Bounds(np.zeros(n),np.ones(n)),constraints=LinearConstraint(np.array(rows),np.full(len(rows),-np.inf),upper),options={'time_limit':15})
        chosen=[candidates[i] for i in range(n) if result.x is not None and result.x[i]>.5]
        combined=None
        # Standalone scores are approximations. Always recompute joint cross-effects and repair.
        while chosen:
            scenario=Scenario(store_id=req.store_id,promotions=[Promotion(**x['promotion']) for x in chosen],margin_rate=req.margin_rate,inventory_units=req.inventory_units,name='Recommended portfolio')
            combined=self.forecast(scenario,calendar=calendar)
            if combined['investment']<=req.budget+1e-6 and combined['feasible'] and combined['planning_profit']>0:break
            chosen.remove(min(chosen,key=lambda x:x['planning_profit']/max(x['investment'],.01)));combined=None
        return dict(selected=chosen,candidates=sorted(candidates,key=lambda x:-x['planning_profit'])[:40],forecast=combined,
            scenario=scenario.model_dump() if chosen else None,evaluated_products=len(ids),rejected=rejected,
            reason='Joint portfolio checked against budget, stock and positive planning profit, excluding positive post-offer credit.' if chosen else 'No profitable joint portfolio remains. Keep the no-promotion plan.',
            solver='Mixed-integer candidate selection + joint simulation + feasibility repair',solver_status=int(result.status),
            optimality='Candidate-grid solution; cross-product interactions make this a heuristic for the full nonlinear portfolio. ROI mode ranks standalone ROI; portfolio ROI is recomputed.',
            objective=req.objective,budget=req.budget)
