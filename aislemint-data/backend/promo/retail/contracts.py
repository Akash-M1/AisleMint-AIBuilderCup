"""Versioned JSON contract and readiness checks, independent of model fitting."""
from datetime import date,datetime
from zoneinfo import ZoneInfo
from typing import Literal
import json, hashlib
import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator
from ..data import validate_panel
from ..demand import validate_demand_panel

VERSION='retail-sales-v1'
MAX_ROWS=100000
class SalesObservation(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False,strict=True)
    date:str=Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    store_id:str=Field(min_length=1,max_length=64)
    product_id:str=Field(min_length=1,max_length=64)
    category:str=Field(min_length=1,max_length=120)
    name:str=Field(min_length=1,max_length=200)
    units:float=Field(ge=0)
    price:float=Field(ge=0)
    base_price:float=Field(gt=0)
    feature:Literal[0,1]
    display:Literal[0,1]
    promotion_available:Literal[0,1]
    country:Literal['IN','US']
    currency:Literal['INR','USD']
    stockout:Literal[0,1]|None=None
    holiday:Literal[0,1]|None=None
    unit_cost:float|None=Field(default=None,ge=0)
    SPEND:float|None=Field(default=None,ge=0)
    free_promotion:Literal[0,1]=0
    returned_units:float|None=Field(default=None,ge=0)
    refund_ex_gst:float|None=Field(default=None,ge=0)
    @model_validator(mode='after')
    def business_rules(self):
        day=date.fromisoformat(self.date)

        for field in ('store_id','product_id','category','name'):
            if getattr(self,field)!=getattr(self,field).strip(): raise ValueError(f'{field} must not contain surrounding whitespace.')
        if (self.country,self.currency) not in [('IN','INR'),('US','USD')]: raise ValueError('Country and currency must match.')
        if self.price>self.base_price*1.01: raise ValueError('Realized price exceeds regular price.')
        if self.price==0 and not(self.free_promotion and self.units>0 and self.promotion_available): raise ValueError('Zero price requires observed free promotional units.')
        if self.SPEND is not None and abs(self.SPEND-self.units*self.price)>max(.05,self.SPEND*.001): raise ValueError('SPEND must reconcile to gross units × realized price, excluding tax and refunds.')
        return self

def normalize(records,shop):
    result=[];errors=[]
    for i,row in enumerate(records):
        try:
            r=SalesObservation.model_validate(row).model_dump(exclude_none=True)
            if date.fromisoformat(r['date'])>=datetime.now(ZoneInfo(shop['timezone'])).date():raise ValueError('Supply completed historical periods before today in the shop timezone.')
            if r['store_id']!=shop['store_code']: raise ValueError('store_id does not match this shop’s registered store code.')
            if (r['country'],r['currency'])!=(shop['country'],shop['currency']): raise ValueError('Market does not match this shop.')
            result.append(r)
        except Exception as exc:
            errors.append({'row':i+1,'message':str(exc)[:800]})
            if len(errors)>=20: break
    if errors: return None,errors
    return result,[]

def readiness(records,kind,frequency):
    d=pd.DataFrame(records);issues=[];warnings=[]
    report=dict(rows=len(d),products=int(d.product_id.nunique()) if 'product_id' in d else 0,
        periods=int(d.date.nunique()) if 'date' in d else 0,frequency=frequency)
    if not len(d):return None,dict(ready=False,quality=report,issues=['No observed history received.'],warnings=[])
    if kind=='promotion' and frequency!='weekly':issues.append('Promotion modelling requires a weekly snapshot; submit daily data separately for demand.')
    dates=pd.to_datetime(d.date)
    gaps=np.diff(np.sort(dates.unique())).astype('timedelta64[D]').astype(int)
    step=1 if frequency=='daily' else 7
    if len(gaps) and not (gaps==step).all():issues.append('Dates must have the declared cadence with no missing global periods.')
    for col in ('stockout','unit_cost','holiday'):
        if col not in d:warnings.append(f'{col} is absent. '+{'stockout':'Observed sales cannot reveal sales lost to unavailable stock.','unit_cost':'Promotion profit requires explicitly supplied margin assumptions.','holiday':'Only the market calendar can be used; local holiday effects need historical flags.'}[col])
        elif d[col].isna().any():issues.append(f'{col} must be supplied for every row or omitted entirely.')
    for col in ('SPEND','returned_units','refund_ex_gst'):
        if col in d and d[col].isna().any():issues.append(f'{col} must be complete or omitted entirely.')
    if (dates.max()-dates.min()).days<730:warnings.append('Less than two years of history: annual seasonality and holidays have limited repeat evidence.')
    if report['products']<2:warnings.append('Cross-product cannibalization and halo cannot be assessed from one product.')
    try:
        validated,q=(validate_panel if kind=='promotion' else validate_demand_panel)(d)
        report.update(q)
        if kind=='demand':
            # Match the fixed-origin uncertainty protocol before spending time fitting models.
            horizon=28 if frequency=='daily' else 13
            end=validated.date.max();step_days=1 if frequency=='daily' else 7
            complete=0
            for i in (3,2):
                origin=end-pd.Timedelta(days=i*horizon*step_days)
                future=validated[(validated.date>origin)&(validated.date<=origin+pd.Timedelta(days=horizon*step_days))]
                for _,g in future.groupby(['store_id','product_id']):
                    if len(g)==horizon and not g.stockout.any():complete+=1
            report['complete_calibration_paths']=complete
            if complete<20:issues.append(f'Demand uncertainty needs 20 complete, uncensored calibration paths; found {complete}. Supply more tracked products with complete history across the two calibration windows.')
        if kind=='promotion':
            from ..model import exposures,clean_baseline_mask
            e=exposures(validated);e=e[e.get('stockout',pd.Series(0,index=e.index)).eq(0)]
            clean=clean_baseline_mask(e);weeks=sorted(validated.week.unique())
            for cut in (len(weeks)-53,len(weeks)-27):
                for label,mask in [('training',e.week<=cut),('calibration',(e.week>cut)&(e.week<=cut+13)),('test',(e.week>cut+13)&(e.week<=cut+26))]:
                    if int((clean&mask).sum())<20: issues.append(f'Too few clean non-promotion {label} observations in a time fold (20 required after four promotion-free weeks).')
    except (ValueError,TypeError) as exc:validated=None;issues.append(str(exc))
    return validated,dict(ready=not issues,quality=report,issues=list(dict.fromkeys(issues)),warnings=warnings)

def checksum(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def contract():
    return dict(version=VERSION,row_schema=SalesObservation.model_json_schema(),max_rows=MAX_ROWS,batch_size=2000,
        keys=['store_id','product_id','date'],promotion_frequency='weekly',demand_frequencies=['daily','weekly'],
        minimum_history={'daily_demand':180,'weekly_demand':78,'weekly_promotion':78},
        recommended_history='At least two years, with promotion and non-promotion coverage.',
        example=dict(date='2025-01-05',store_id='SHOP-001',product_id='SKU-001',category='Breakfast',name='Rolled oats 500 g',units=24,price=95,base_price=100,feature=0,display=1,promotion_available=1,country='IN',currency='INR',stockout=0,holiday=0,unit_cost=68,SPEND=2280),
        semantics=['One observed product/shop/period row; never fill missing observations with zero.',
            'Weekly dates are period ends. Keep product identifiers identical to OneSales.',
            'units are gross sold units, including free rewards; track returns separately.',
            'Prices, revenue and costs are major currency units excluding tax. SPEND is before refunds.',
            'Set promotion_available even for conditional offers with no redemption or no sales.',
            'Baseline preserves seasonality and holidays while removing promotion exposure.',
            'Mechanic-level causal effects require executed offer and redemption evidence beyond aggregate sales.'])
