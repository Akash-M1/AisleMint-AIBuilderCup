"""Explicit checkout rules and an isolated, labeled redemption model for conditional offers."""
from __future__ import annotations
from .storage import AssetPath,managed,read_frame,write_frame,load_model,dump_model
from decimal import Decimal,ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path
from typing import Literal
import hashlib
import json
import math
import joblib
import numpy as np
import pandas as pd
from pydantic import BaseModel,ConfigDict,Field,model_validator
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits

from .runtime import ROOT
ARTIFACTS=AssetPath('artifacts/offer-mechanics')
FEATURES=['threshold_units','normal_basket_units','conditional_saving','tpr','threshold_spend','price','intensity']


class DiscountTier(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    quantity:int=Field(ge=2,le=100)
    discount:float=Field(gt=0,le=1)


class ConditionalOffer(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    kind:Literal['bxgy','bxgcy','bulk','tiered','spend_threshold']
    buy_quantity:int=Field(default=1,ge=1,le=100)
    reward_quantity:int=Field(default=1,ge=1,le=100)
    reward_discount:float=Field(default=1,gt=0,le=1)
    threshold_quantity:int=Field(default=3,ge=2,le=100)
    discount:float=Field(default=.1,gt=0,le=1)
    threshold_spend:float=Field(default=20,gt=0,le=100000)
    amount_off:float=Field(default=5,gt=0,le=100000)
    tiers:list[DiscountTier]=Field(default_factory=list,max_length=6)
    reward_choices:list[str]=Field(default_factory=list,max_length=6)
    choice_weights:list[float]=Field(default_factory=list,max_length=6)
    stack_group:str=Field(default='conditional',min_length=1,max_length=40,pattern=r'^[a-zA-Z0-9_-]+$')
    redemption_limit:int|None=Field(default=None,ge=1,le=100)

    @model_validator(mode='after')
    def valid(self):
        if self.kind=='tiered':
            if not self.tiers:raise ValueError('Buy more/save more requires at least one tier.')
            ordered=sorted(self.tiers,key=lambda t:t.quantity)
            if len({t.quantity for t in ordered})!=len(ordered) or any(a.discount>b.discount for a,b in zip(ordered,ordered[1:])):raise ValueError('Tier thresholds must be unique and savings non-decreasing.')
        if self.kind=='bxgcy' and not self.reward_choices:raise ValueError('Choice offers require eligible reward products.')
        if self.kind=='bxgcy' and (self.reward_quantity!=1 or self.reward_discount!=1):raise ValueError('BXGCY awards one free product from the eligible choice list.')
        if len(set(self.reward_choices))!=len(self.reward_choices):raise ValueError('Duplicate reward choices.')
        if self.choice_weights and (len(self.choice_weights)!=len(self.reward_choices) or not all(math.isfinite(w) and w>=0 for w in self.choice_weights) or not math.isclose(sum(self.choice_weights),1)):
            raise ValueError('Reward choice weights must match products, be nonnegative and sum to one.')
        return self


def cents(value):return int((Decimal(str(value))*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
def discounted(value,rate):return int((Decimal(value)*(1-Decimal(str(rate)))).quantize(Decimal('1'),rounding=ROUND_HALF_UP))


def threshold(offer,price,tpr):
    if offer.kind=='bxgy':return offer.buy_quantity+offer.reward_quantity
    if offer.kind=='bxgcy':return offer.buy_quantity
    if offer.kind=='bulk':return offer.threshold_quantity
    if offer.kind=='tiered':return min(t.quantity for t in offer.tiers)
    return max(1,math.ceil(cents(offer.threshold_spend)/max(1,discounted(cents(price),tpr))))


def apply_conditional(cart,prices,pid,offer,selected_reward=None):
    """Integer cents; reward units must exist in the cart and do not create another qualification."""
    result={key:list(value) for key,value in prices.items()};quantity=cart.get(pid,0)
    if not quantity:return result
    if offer.kind=='bxgy':
        times=quantity//(offer.buy_quantity+offer.reward_quantity)
        if offer.redemption_limit is not None:times=min(times,offer.redemption_limit)
        count=min(quantity,times*offer.reward_quantity)
        indices=sorted(range(quantity),key=lambda i:result[pid][i])[:count]
        for i in indices:result[pid][i]=discounted(result[pid][i],offer.reward_discount)
    elif offer.kind=='bxgcy':
        times=quantity//offer.buy_quantity
        if offer.redemption_limit is not None:times=min(times,offer.redemption_limit)
        eligible=[key for key in offer.reward_choices if cart.get(key,0)>0 and key!=pid]
        if selected_reward is not None:
            if selected_reward not in eligible:raise ValueError('Selected reward must be eligible and present in the basket.')
            eligible=[selected_reward]
        if eligible and times:
            # When no choice is specified, allocate the lowest-priced eligible reward units.
            units=sorted((price,key,i) for key in eligible for i,price in enumerate(result[key]))
            for _,key,i in units[:times*offer.reward_quantity]:result[key][i]=discounted(result[key][i],offer.reward_discount)
    elif offer.kind=='bulk' and quantity>=offer.threshold_quantity:
        result[pid]=[discounted(p,offer.discount) for p in result[pid]]
    elif offer.kind=='tiered':
        rate=max([t.discount for t in offer.tiers if quantity>=t.quantity],default=0)
        result[pid]=[discounted(p,rate) for p in result[pid]]
    elif offer.kind=='spend_threshold' and sum(result.get(pid,[]))>=cents(offer.threshold_spend):
        remaining=min(cents(offer.amount_off),sum(result[pid]))
        for i in reversed(range(quantity)):
            benefit=min(remaining,result[pid][i]);result[pid][i]-=benefit;remaining-=benefit
    return result


def checkout(cart,regular_prices,pid,tpr=0,offers=None,selected_rewards=None):
    if any(not isinstance(q,int) or isinstance(q,bool) or q<0 or q>10000 for q in cart.values()):raise ValueError('Basket quantities must be whole units between 0 and 10,000.')
    if set(cart)-set(regular_prices):raise ValueError('Every basket product needs a regular price.')
    if not 0<=tpr<=.5:raise ValueError('TPR must be between 0 and 50%.')
    if any(not math.isfinite(p) or p<=0 for p in regular_prices.values()):raise ValueError('Basket prices must be finite and positive.')
    prices={key:[discounted(cents(regular_prices[key]),tpr if key==pid else 0)]*q for key,q in cart.items()}
    groups={}
    for offer in offers or []:groups.setdefault(offer.stack_group,[]).append(offer)
    applied=[]
    for group,options in sorted(groups.items()):
        candidates=[(apply_conditional(cart,prices,pid,o,(selected_rewards or {}).get(group)),o) for o in options]
        candidate,offer=min(candidates,key=lambda item:sum(map(sum,item[0].values())))
        saving=sum(map(sum,prices.values()))-sum(map(sum,candidate.values()))
        if saving>0:applied.append(dict(group=group,kind=offer.kind,saving=saving/100))
        prices=candidate
    total=sum(map(sum,prices.values()));regular=sum(cents(regular_prices[key])*q for key,q in cart.items())
    return dict(total=total/100,regular_total=regular/100,saving=(regular-total)/100,
        effective_discount=1-total/regular if regular else 0,applied=applied,
        lines=[dict(product_id=key,quantity=cart[key],paid=sum(values)/100) for key,values in prices.items()])


def train_redemption(output_dir=ARTIFACTS,observations=None):
    """Synthetic training is an explicit behavioral assumption, never retail effectiveness evidence."""
    out=managed(output_dir);out.mkdir(parents=True,exist_ok=True)
    if (out/'model_card.json').exists():raise ValueError('Train into a new mechanics artifact directory.')
    rng=np.random.default_rng(42)
    if observations is None:
        n=50000
        d=pd.DataFrame(dict(threshold_units=rng.integers(2,25,n),normal_basket_units=rng.uniform(1.2,6,n),
            conditional_saving=rng.uniform(.03,.85,n),tpr=rng.uniform(0,.5,n),price=rng.uniform(.5,40,n)))
        d['threshold_spend']=d.threshold_units*d.price*(1-d.tpr)
        d['intensity']=d.conditional_saving*d.threshold_units/d.normal_basket_units
        tail=1-1/d.normal_basket_units
        natural=(tail**(d.threshold_units-1))*(1+(d.threshold_units-1)*(1-tail))
        adoption=1/(1+np.exp(-(5*d.conditional_saving+.8*d.tpr-.45*(d.threshold_units-d.normal_basket_units))))
        d['redeemed_unit_share']=np.clip(natural+(1-natural)*adoption+rng.normal(0,.025,n),0,1)
        basis='synthetic';split=int(.8*n)
    else:
        d=read_frame(observations)
        needed=FEATURES+['date','redeemed_unit_share']
        if set(needed)-set(d):raise ValueError('Production mechanics CSV needs '+', '.join(needed))
        d['date']=pd.to_datetime(d.date,errors='raise');d=d.sort_values('date')
        if d.date.isna().any() or d.date.dt.tz is not None:raise ValueError('Use complete local observation dates without timezones.')
        for col in FEATURES+['redeemed_unit_share']:d[col]=pd.to_numeric(d[col],errors='raise')
        if len(d)<200 or not d.redeemed_unit_share.between(0,1).all():raise ValueError('Supply at least 200 records with valid redeemed-unit shares.')
        dates=sorted(d.date.unique())
        if len(dates)<10:raise ValueError('Production mechanics fitting needs at least ten dated observation periods.')
        split=int((d.date<dates[int(len(dates)*.8)]).sum());basis='client_observations'
    if not np.isfinite(d[FEATURES+['redeemed_unit_share']].to_numpy()).all():raise ValueError('Mechanics inputs must be finite.')
    if not (d.threshold_units.between(1,10000).all() and d.normal_basket_units.between(1,100).all() and d.conditional_saving.between(0,1).all() and d.tpr.between(0,.5).all() and d.threshold_spend.gt(0).all() and d.price.gt(0).all() and d.intensity.ge(0).all()):raise ValueError('Mechanics features contain out-of-range basket, price or discount values.')
    model=HistGradientBoostingRegressor(max_iter=180,max_leaf_nodes=23,l2_regularization=10,learning_rate=.06,early_stopping=False,random_state=42)
    with threadpool_limits(limits=4):model.fit(d[FEATURES].iloc[:split],d.redeemed_unit_share.iloc[:split]);pred=model.predict(d[FEATURES].iloc[split:])
    card=dict(model_id='redemption-'+basis+'-'+hashlib.sha256(pd.util.hash_pandas_object(d,index=False).values.tobytes()).hexdigest()[:12],
        data_basis=basis,rows=len(d),training_rows=split,test_rows=len(d)-split,features=FEATURES,
        test_mae=float(np.abs(pred-d.redeemed_unit_share.iloc[split:]).mean()),
        evaluation_scope='Synthetic generator approximation only; not customer redemption or demand accuracy.' if basis=='synthetic' else 'Chronological client redeemed-unit-share holdout; not causal promotion lift.',
        production_validated=False,target='Share of qualifying-product units sold in redeemed transactions',
        assumptions='Geometric synthetic basket quantities and a specified incentive/threshold adoption function. No real consumer responses are claimed.')
    dump_model(dict(model=model, card=card), out / 'model.joblib');write_frame(d, out / 'training_data.parquet', index=False)
    (out/'model_card.json').write_text(json.dumps(card,indent=2));return card


@lru_cache(maxsize=1)
def redemption_model():
    if not (ARTIFACTS/'model.joblib').exists():raise ValueError('Train the local conditional-offer redemption model before using conditional mechanics.')
    return load_model(ARTIFACTS / 'model.joblib')


def offer_profile(price,tpr,offers,normal_basket_units=2,redemption_override=None,reward_prices=None):
    if not math.isfinite(price) or price<=0 or not 0<=tpr<=.5 or not 1<=normal_basket_units<=100:raise ValueError('Invalid price, TPR or normal basket size.')
    if redemption_override is not None and not 0<=redemption_override<=1:raise ValueError('Redemption share must be between zero and one.')
    if not offers:return dict(effective_discount=tpr,paid_discount=tpr,redemption_rate=0,intensity=0,conditional_saving=0,threshold_units=1,rewards=[],data_basis='observed_price_response',model_id=None)
    grouped={}
    for offer in offers:grouped.setdefault(offer.stack_group,[]).append(offer)
    profiles=[]
    for group,options in sorted(grouped.items()):
        options_profiles=[]
        for offer in options:
            n=threshold(offer,price,tpr)
            if n>10000:raise ValueError('The conditional threshold exceeds the supported basket size.')
            rewards=[]
            if offer.kind=='bxgcy':
                if not reward_prices or set(offer.reward_choices)-set(reward_prices):raise ValueError('Every reward choice must be available in the same store.')
                weights=offer.choice_weights or [1/len(offer.reward_choices)]*len(offer.reward_choices)
                value=sum(w*reward_prices[k] for k,w in zip(offer.reward_choices,weights))*offer.reward_quantity*offer.reward_discount
                saving=value/(n*price*(1-tpr)+value)
                rewards=[dict(product_id=k,units_per_qualifier=offer.reward_quantity/n*w,discount=offer.reward_discount) for k,w in zip(offer.reward_choices,weights)]
                paid=0.
            else:
                # Conditional geometric basket distribution: evaluate higher tiers and
                # incomplete bundles too. This distribution is an explicit assumption.
                quantities=np.arange(n,min(10000,n+max(80,int(normal_basket_units*12)))+1)
                tail=max(1e-9,1-1/normal_basket_units)
                weights=quantities*np.power(tail,quantities-n);weights=weights/weights.sum()
                savings=[]
                for quantity in quantities:
                    receipt=checkout({'qualifier':int(quantity)},{'qualifier':price},'qualifier',tpr,[offer])
                    savings.append(1-receipt['total']/max(.01,quantity*price*(1-tpr)))
                saving=float(np.dot(weights,savings));paid=saving
            features=dict(threshold_units=n,normal_basket_units=normal_basket_units,conditional_saving=saving,tpr=tpr,
                          threshold_spend=n*price*(1-tpr),price=price,intensity=saving*n/normal_basket_units)
            if redemption_override is None:
                trained=redemption_model()
                with threadpool_limits(limits=4):rate=float(np.clip(trained['model'].predict(pd.DataFrame([features])[FEATURES])[0],0,1))
                basis=trained['card']['data_basis'];model_id=trained['card']['model_id']
            else:rate=redemption_override;basis='user_assumption';model_id=None
            options_profiles.append(dict(**features,redemption_rate=rate,paid_saving=paid,rewards=rewards,data_basis=basis,model_id=model_id,kind=offer.kind,group=group))
        profiles.append(max(options_profiles,key=lambda p:p['conditional_saving']*p['redemption_rate']))
    effective=1-(1-tpr)*math.prod(1-p['conditional_saving']*p['redemption_rate'] for p in profiles)
    paid=1-(1-tpr)*math.prod(1-p['paid_saving']*p['redemption_rate'] for p in profiles)
    rewards=[{**r,'units_per_qualifier':r['units_per_qualifier']*p['redemption_rate']} for p in profiles for r in p['rewards']]
    return dict(effective_discount=min(.95,effective),paid_discount=min(1,paid),redemption_rate=max(p['redemption_rate'] for p in profiles),
        intensity=sum(p['intensity']*p['redemption_rate'] for p in profiles),conditional_saving=1-math.prod(1-p['conditional_saving'] for p in profiles),
        threshold_units=max(p['threshold_units'] for p in profiles),rewards=rewards,groups=profiles,
        data_basis='synthetic' if any(p['data_basis']=='synthetic' for p in profiles) else profiles[0]['data_basis'],model_id=profiles[0]['model_id'])


class OfferTerms(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    product_id:str
    discount:float=Field(default=.2,ge=0,le=.5)
    feature:bool=False
    display:bool=False
    mechanics:list[ConditionalOffer]=Field(default_factory=list,max_length=6)
    normal_basket_units:float=Field(default=2,ge=1,le=100)
    redemption_rate:float|None=Field(default=None,ge=0,le=1)
    stacking_policy:Literal['exclusive','stack','best_offer']='exclusive'

    @model_validator(mode='after')
    def reward_assortment(self):
        if any(self.product_id in o.reward_choices for o in self.mechanics):raise ValueError('Choice rewards must be different products; use BXGY for same-product rewards.')
        return self


def compatible(terms):
    if len(terms)<2:return
    policies={t.stacking_policy for t in terms}
    if len(policies)!=1 or 'exclusive' in policies:raise ValueError('An offer overlaps an exclusive or incompatible calendar promotion. Both offers must select the same overlap policy: stack or best offer.')
    if next(iter(policies))=='stack' and len({(t.normal_basket_units,t.redemption_rate) for t in terms if t.mechanics})>1:
        raise ValueError('Overlapping stacked conditional offers must share basket-size and redemption assumptions.')


@lru_cache(maxsize=4096)
def cached_profile(price,serialized,reward_prices):
    terms=[OfferTerms(**t) for t in json.loads(serialized)];compatible(terms)
    def profile(t):return offer_profile(price,t.discount,t.mechanics,t.normal_basket_units,t.redemption_rate,dict(reward_prices))
    if len(terms)>1 and terms[0].stacking_policy=='best_offer':
        winner=max(terms,key=lambda t:profile(t)['effective_discount']);result=profile(winner)
        return {**result,'feature':int(winner.feature),'display':int(winner.display),'overlap_policy':'best_offer'}
    conditional=[t for t in terms if t.mechanics];assumptions=(conditional or terms)[0]
    result=offer_profile(price,max(t.discount for t in terms),[o for t in terms for o in t.mechanics],
        assumptions.normal_basket_units,assumptions.redemption_rate,dict(reward_prices))
    return {**result,'feature':int(any(t.feature for t in terms)),'display':int(any(t.display for t in terms)),
        'overlap_policy':terms[0].stacking_policy,'tpr':max(t.discount for t in terms)}


def terms_dict(value):
    data=value.model_dump() if isinstance(value,BaseModel) else value
    return OfferTerms(**{k:v for k,v in data.items() if k in OfferTerms.model_fields}).model_dump()


def apply_schedule(frame,schedules):
    """One resolved exposure per product/store/period, shared by both ML engines."""
    result=frame.copy();profiles=[]
    if not schedules:return result,profiles
    indexed={(str(r.store_id),str(r.product_id),r.date):r.Index for r in result.itertuples()}
    for key,terms in schedules.items():
        if key not in indexed:continue
        i=indexed[key];store,pid,date=key
        choices={p for t in terms for o in t.get('mechanics',[]) for p in o.get('reward_choices',[])}
        reward_prices={p:float(result.at[indexed[(store,p,date)],'base_price']) for p in choices if (store,p,date) in indexed}
        profile=cached_profile(float(result.at[i,'base_price']),json.dumps([terms_dict(t) for t in terms],sort_keys=True),tuple(sorted(reward_prices.items())))
        result.loc[i,['discount','feature','display']]=[profile['effective_discount'],profile['feature'],profile['display']]
        if 'promotion_available' in result:result.at[i,'promotion_available']=int(any(t.get('discount',0) or t.get('feature',0) or t.get('display',0) or t.get('mechanics') for t in terms))
        result.at[i,'price']=result.at[i,'base_price']*(1-profile['paid_discount'])
        profiles.append(dict(store_id=store,product_id=pid,date=str(date.date()),**profile))
    return result,profiles


def apply_rewards(frame,profiles,quantity_column):
    """Allocate free choice units before paid reward sales; never count them as halo.

    Gifts first displace forecast paid purchases of the reward SKU. Only obligations
    above its existing demand add physical units. No recursive gift qualification.
    """
    if not any(p['rewards'] for p in profiles):return frame,[]
    frame=frame.copy();index={(str(r.store_id),str(r.product_id),str(r.date.date())):r.Index for r in frame.itertuples()}
    obligations={};quantity=frame[quantity_column].copy()
    for p in profiles:
        key=(p['store_id'],p['product_id'],p['date'])
        if key not in index:continue
        for reward in p['rewards']:
            target=(p['store_id'],reward['product_id'],p['date'])
            units=float(quantity.at[index[key]])*reward['units_per_qualifier']
            obligations[target]=obligations.get(target,0)+units
    allocations=[]
    for key,units in obligations.items():
        if key not in index:raise ValueError('Reward product has no demand forecast in this store.')
        i=index[key];before=float(quantity.at[i]);total=max(before,units);paid=max(0,before-units)
        original_price=float(frame.at[i,'price'])
        frame.at[i,quantity_column]=total
        frame.at[i,'price']=paid*original_price/total if total else original_price
        allocations.append(dict(store_id=key[0],product_id=key[1],date=key[2],reward_units=units,
            displaced_paid_units=min(before,units),additional_physical_units=total-before,paid_units=paid))
    return frame,allocations
