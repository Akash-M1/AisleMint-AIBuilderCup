"""Direct multi-horizon sales-demand forecasting, separate from causal promo baselines."""
from __future__ import annotations
from .storage import AssetPath,managed,read_frame,write_frame,load_model,dump_model
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits
from .model import metrics
from .holidays import us_calendar,calendar_for
from .offer_mechanics import OfferTerms,terms_dict,apply_schedule,apply_rewards

KEYS=['store_id','product_id']
REQUIRED=['date','store_id','product_id','category','name','units','price','base_price']


def validate_demand_panel(frame):
    missing=set(REQUIRED)-set(frame)
    if missing:raise ValueError('Missing demand columns: '+', '.join(sorted(missing)))
    d=frame.copy()
    dates=pd.to_datetime(d.date,errors='raise')
    if dates.dt.tz is not None:raise ValueError('Demand dates must use local calendar dates without timezones.')
    if (dates!=dates.dt.normalize()).any():raise ValueError('Use period-ending calendar dates without time-of-day values.')
    d['date']=dates.dt.normalize()
    for col in ['feature','display','holiday','stockout']:
        if col not in d:d[col]=0
        d[col]=pd.to_numeric(d[col],errors='raise')
        if not d[col].isin([0,1]).all():raise ValueError(f'{col} must be 0 or 1.')
    if d[REQUIRED].isna().any().any():raise ValueError('Demand required columns contain missing values.')
    for col in ['store_id','product_id','category','name']:
        d[col]=d[col].astype(str).str.strip()
        if d[col].eq('').any():raise ValueError(f'{col} must not be blank.')
    for col in ['units','price','base_price']:
        d[col]=pd.to_numeric(d[col],errors='raise')
        if not np.isfinite(d[col]).all():raise ValueError(f'{col} must be finite.')
    free=pd.to_numeric(d.get('free_promotion',pd.Series(0,index=d.index)),errors='raise')
    if not free.isin([0,1]).all():raise ValueError('free_promotion must be 0 or 1.')
    if (d.units<0).any() or ((d.price<=0)&~(d.price.eq(0)&free.eq(1)&d.units.gt(0))).any() or (d.base_price<=0).any():raise ValueError('Demand units must be nonnegative and prices positive, except explicitly recorded free reward units.')
    if (d.price>d.base_price*1.01).any():raise ValueError('Selling price exceeds regular price.')
    if d.duplicated(KEYS+['date']).any():raise ValueError('Duplicate demand product/store/date keys.')
    if d.groupby('product_id').category.nunique().max()>1:raise ValueError('A product must have one category.')
    unique=np.sort(d.date.unique());steps=np.diff(unique).astype('timedelta64[D]').astype(int)
    if len(steps)==0 or not np.isin(steps,[1]).all() and not np.isin(steps,[7]).all():
        raise ValueError('Use a complete daily or weekly calendar. Missing series rows remain missing, not zero demand.')
    step=int(steps[0]);minimum=180 if step==1 else 78
    if len(unique)<minimum:raise ValueError(f'At least {minimum} distinct {"days" if step==1 else "weeks"} are needed.')
    minimum_series=28 if step==1 else 26
    if (d.groupby(KEYS).size()<minimum_series).any():raise ValueError(f'Every product/store series needs at least {minimum_series} observed periods.')
    if d.product_id.nunique()>250 or d.store_id.nunique()>250:raise ValueError('Partition local demand workspaces to at most 250 products and 250 stores.')
    if len(d)>1000000:raise ValueError('Demand upload limit is one million observed rows.')
    if len(unique)*len(d[KEYS].drop_duplicates())>2000000:
        raise ValueError('Partition this history: its complete product/store calendar exceeds two million local state rows.')
    d['discount']=(1-d.price/d.base_price).clip(0,1)
    if 'promotion_available' in d:
        d['promotion_available']=pd.to_numeric(d.promotion_available,errors='raise')
        if not d.promotion_available.isin([0,1]).all():raise ValueError('promotion_available must be 0 or 1.')
    d=d.sort_values(KEYS+['date']).reset_index(drop=True)
    report=dict(rows=len(d),products=d.product_id.nunique(),stores=d.store_id.nunique(),periods=len(unique),
        step_days=step,frequency='daily' if step==1 else 'weekly',start=str(d.date.min().date()),end=str(d.date.max().date()),
        stockout_rows=int(d.stockout.sum()),holiday_rows=int(d.holiday.sum()),availability_supplied='stockout' in frame,holiday_supplied='holiday' in frame,
        annual_history_limited=(d.date.max()-d.date.min()).days<730,
        missing_periods=int(len(unique)*len(d[KEYS].drop_duplicates())-len(d)))
    from .data import market_metadata
    report.update(market_metadata(d))
    return d,report


def load_demand_panel(path):
    d,quality=validate_demand_panel(read_frame(path))
    original=managed(path).parent/'quality.json'
    if original.exists():
        supplied=json.loads(original.read_text())
        for key in ['availability_supplied','holiday_supplied']:
            if key in supplied:quality[key]=supplied[key]
    return d,quality


def promotion_context(d):
    d=d.copy();d['dose']=d.discount+.1*d.display+.08*d.feature
    g=d.groupby(['store_id','date','category']).dose
    total=d.groupby(['store_id','date']).dose
    d['peer_dose']=(g.transform('sum')-d.dose)/(g.transform('count')-1).clip(lower=1)
    d['other_category_dose']=(total.transform('sum')-g.transform('sum'))/(total.transform('count')-g.transform('count')).clip(lower=1)
    return d


def schedule_features(rows,schedule,step):
    """Lag scheduled exposures, including offers planned after the forecast origin; never sales."""
    rows=rows.copy()
    known=promotion_context(schedule).set_index(KEYS+['date'])
    for lag in [1,2,3,4]:
        idx=pd.MultiIndex.from_arrays([rows.store_id,rows.product_id,rows.date-pd.Timedelta(days=lag*step)])
        prior=known.reindex(idx)
        rows[f'offer_lag_{lag}']=prior.dose.to_numpy()
        rows[f'peer_lag_{lag}']=prior.peer_dose.to_numpy()
    return rows


def history_states(panel,step):
    """Every feature at an origin uses observations no later than that origin."""
    dates=pd.DataFrame({'date':pd.date_range(panel.date.min(),panel.date.max(),freq=f'{step}D')})
    grid=panel[KEYS].drop_duplicates().merge(dates,how='cross').merge(panel,on=KEYS+['date'],how='left')
    grid=grid.sort_values(KEYS+['date']).reset_index(drop=True)
    # Known stockouts censor demand. Do not teach the lag features that those sales were true demand.
    grid['observed']=grid.units.notna() & grid.stockout.fillna(0).eq(0)
    grid['demand_observed']=grid.units.where(grid.observed)
    groups=grid.groupby(KEYS,sort=False)
    states=grid[KEYS+['date']].copy()
    states['last_units']=grid.demand_observed
    for lag in [1,2,3,6,12,27,51]:states[f'lag_{lag}']=groups.demand_observed.shift(lag)
    for window in [4,13,28]:
        rolling=groups.demand_observed.rolling(window,min_periods=1)
        states[f'mean_{window}']=rolling.mean().reset_index(level=KEYS,drop=True).sort_index()
    states['std_13']=groups.demand_observed.rolling(13,min_periods=2).std().reset_index(level=KEYS,drop=True).sort_index()
    grid['zero']=grid.demand_observed.eq(0).where(grid.observed)
    states['zero_share']=grid.groupby(KEYS).zero.rolling(28,min_periods=1).mean().reset_index(level=KEYS,drop=True).sort_index()
    grid['known_date']=grid.date.where(grid.observed)
    states['observation_age']=(grid.date-grid.groupby(KEYS).known_date.ffill()).dt.days
    states['recent_promo']=groups.discount.rolling(4,min_periods=1).mean().reset_index(level=KEYS,drop=True).sort_index()
    states['lag_year']=groups.demand_observed.shift(364 if step==1 else 51)
    states['origin_price']=groups.base_price.ffill()
    return states.set_index(KEYS+['date'])


def examples(panel,states,step,horizons):
    d=schedule_features(promotion_context(panel),panel,step);parts=[]
    for h in horizons:
        x=d.copy();x['origin']=x.date-pd.Timedelta(days=step*h)
        idx=pd.MultiIndex.from_arrays([x.store_id,x.product_id,x.origin])
        past=states.reindex(idx).reset_index(drop=True)
        x=x.reset_index(drop=True)
        for col in past:x[col]=past[col]
        x['horizon']=h
        parts.append(x[x.mean_13.notna() & x.units.notna() & x.stockout.eq(0)])
    return pd.concat(parts,ignore_index=True)


class DirectDemandModel:
    def __init__(self,family='hgb'):self.family=family

    def encode(self,d):
        x=pd.DataFrame(index=d.index)
        for col in ['product_id','store_id','category']:
            x[col]=d[col].map(self.codes[col]).fillna(-1).astype(int)
        phase=2*np.pi*(d.date.dt.dayofyear-1)/365.25
        for k in range(1,5):
            x[f'annual_sin_{k}']=np.sin(k*phase);x[f'annual_cos_{k}']=np.cos(k*phase)
        x['day_of_week']=d.date.dt.dayofweek;x['month']=d.date.dt.month
        x['trend']=(d.date-self.origin).dt.days/365.25
        if getattr(self,'offer_availability',False):x['promotion_available']=d.get('promotion_available',pd.Series(0,index=d.index)).astype(float)
        for col in ['base_price','discount','feature','display','holiday','peer_dose','other_category_dose','horizon',
            'last_units','lag_1','lag_2','lag_3','lag_6','lag_12','lag_27','lag_51','mean_4','mean_13','mean_28',
            'std_13','zero_share','observation_age','recent_promo','lag_year','origin_price']:
            x[col]=d[col].astype(float)
        if getattr(self,'scheduled_lags',False):
            for lag in [1,2,3,4]:
                for kind in ['offer','peer']:x[f'{kind}_lag_{lag}']=d[f'{kind}_lag_{lag}'].astype(float)
        if getattr(self,'holiday_calendar',None) is not None:
            holiday=self.holiday_calendar.features(d.date,self.step_days)
            for col in holiday:x[col]=holiday[col].to_numpy()
            x['holiday']=np.maximum(x.holiday,holiday.us_holiday_any.to_numpy())
        if self.family=='xgboost':
            for col in ['product_id','store_id','category']:
                x[col]=pd.Categorical(x[col],categories=range(len(self.codes[col])))
        return x

    def fit(self,d):
        self.scheduled_lags=True
        self.offer_availability='promotion_available' in d
        self.holiday_calendar=calendar_for(d)
        self.step_days=int(((d.date-d.origin).dt.days/d.horizon).iloc[0])
        self.origin=d.date.min();self.codes={c:{k:i for i,k in enumerate(sorted(d[c].unique()))} for c in ['product_id','store_id','category']}
        # Annual lags may be entirely unavailable in a short daily history.
        x=self.encode(d).dropna(axis=1,how='all');self.features=list(x)
        if self.family=='hgb':
            self.estimator=HistGradientBoostingRegressor(loss='poisson',max_iter=220,max_leaf_nodes=31,
                learning_rate=.06,l2_regularization=20,min_samples_leaf=70,early_stopping=False,
                categorical_features=[c in ['product_id','store_id','category'] for c in x],random_state=42)
        else:
            from xgboost import XGBRegressor
            self.estimator=XGBRegressor(objective='count:poisson',tree_method='hist',enable_categorical=True,
                n_estimators=280,max_depth=6,learning_rate=.06,min_child_weight=40,reg_lambda=20,
                subsample=.9,n_jobs=4,random_state=42)
        with threadpool_limits(limits=4):self.estimator.fit(x,d.units)
        self.trained_until=str(d.date.max().date());return self

    def predict(self,d):
        with threadpool_limits(limits=4):return np.maximum(0,self.estimator.predict(self.encode(d).reindex(columns=self.features)))


class StatisticalDemandModel:
    def __init__(self,kind):self.kind=kind
    def predict(self,d):
        if self.kind=='recent_mean':return np.maximum(0,d.mean_4.fillna(d.mean_13).fillna(0).to_numpy())
        # Seasonal naive is known at the origin; long horizons fall back to a recent level.
        return np.maximum(0,d.seasonal_reference.fillna(d.mean_13).fillna(0).to_numpy())


def origin_frame(panel,states,step,origin,horizon,known_schedule=True):
    history=panel[panel.date<=origin]
    latest=history.sort_values('date').groupby(KEYS).tail(1)
    latest=latest[latest.date>=origin-pd.Timedelta(days=step*4)]
    rows=[]
    indexed=panel.set_index(KEYS+['date'])
    for h in range(1,horizon+1):
        x=latest.copy();x['date']=origin+pd.Timedelta(days=h*step);x['origin']=origin;x['horizon']=h
        keys=pd.MultiIndex.from_frame(x[KEYS+['date']]);future=indexed.reindex(keys)
        x['units']=future.units.to_numpy();x['stockout']=future.stockout.fillna(0).to_numpy()
        if known_schedule:
            if 'promotion_available' in panel:x['promotion_available']=future.promotion_available.fillna(0).to_numpy()
            for col in ['price','base_price','discount','feature','display','holiday']:
                x[col]=future[col].to_numpy()
            x['base_price']=x.base_price.fillna(latest.base_price)
            x['price']=x.price.fillna(x.base_price)
            for col in ['discount','feature','display','holiday']:x[col]=x[col].fillna(0)
        else:
            x['price']=x.base_price;x['discount']=0.;x[['feature','display','holiday']]=0
            if 'promotion_available' in x:x['promotion_available']=0
        key=pd.MultiIndex.from_arrays([x.store_id,x.product_id,np.repeat(origin,len(x))])
        past=states.reindex(key)
        for col in past:x[col]=past[col].to_numpy()
        seasonal_lag=7 if step==1 else 52
        # Repeat the last known seasonal cycle for all horizons; never look past the origin.
        reference=x.date-pd.to_timedelta(np.ceil(h/seasonal_lag)*seasonal_lag*step,unit='D')
        idx=pd.MultiIndex.from_arrays([x.store_id,x.product_id,reference])
        ref=indexed.reindex(idx)
        x['seasonal_reference']=ref.units.where(ref.stockout.eq(0)).to_numpy()
        rows.append(x)
    future=promotion_context(pd.concat(rows,ignore_index=True))
    cols=KEYS+['date','category','discount','feature','display']
    return schedule_features(future,pd.concat([history[cols],future[cols]],ignore_index=True),step)


def calibrate_residual_paths(blocks):
    """Symmetric, horizon-specific absolute-error calibration from complete paths.

    Use the finite-sample 90% order statistic, without interpolating away tails.
    Reflect whole paths (not individual weeks), retaining their temporal structure.
    Time and cross-product dependence mean this is empirical calibration, not an
    exchangeability-based coverage guarantee.
    """
    blocks=np.asarray(blocks,dtype=float)
    if blocks.ndim!=2 or len(blocks)<20 or not np.isfinite(blocks).all():
        raise ValueError('Need at least 20 complete finite calibration paths.')
    rank=min(len(blocks),int(np.ceil((len(blocks)+1)*.9)))
    radius=np.sort(np.abs(blocks),axis=0)[rank-1]
    symmetric=np.concatenate([blocks,-blocks])
    empirical=np.quantile(symmetric,.95,axis=0)
    scale=np.divide(radius,empirical,out=np.ones_like(radius),where=empirical>0)
    return symmetric*scale,radius


class DemandBundle:
    def __init__(self,models,weights,step):self.models=models;self.weights=weights;self.step=step
    def predict(self,d):return sum(w*self.models[k].predict(d) for k,w in self.weights.items())
    def paths(self,point,category,count=400,seed=42):
        bank=self.residuals.get(category,self.residuals['__pooled__'])
        rng=np.random.default_rng(seed);chosen=bank[rng.integers(0,len(bank),count)]
        # Full sampled paths preserve within-series error dependence through the calibrated horizon.
        return np.maximum(0,np.asarray(point)[None,:]+chosen[:,:len(point)]*np.sqrt(np.maximum(point,1))[None,:])

    def interval(self,point,category,seed=42):
        radii=getattr(self,'residual_radius_90',None)
        if radii is None: # Existing immutable models keep their original interval behaviour.
            return np.quantile(self.paths(point,category,seed=seed),[.05,.95],axis=0)
        point=np.asarray(point)
        radius=radii.get(category,radii['__pooled__'])[:len(point)]*np.sqrt(np.maximum(point,1))
        return np.maximum(0,point-radius),point+radius


def train_demand(panel_path,output_dir,include_xgboost=True):
    started=time.monotonic();d,quality=load_demand_panel(panel_path)
    out=managed(output_dir);out.mkdir(parents=True,exist_ok=True)
    if (out/'model.joblib').exists() and (out/'model_card.json').exists():raise ValueError('This demand model version is already trained. Create a new source version to retrain.')
    step=quality['step_days'];horizon=28 if step==1 else 13
    end=d.date.max();train_end=end-pd.Timedelta(days=4*horizon*step)
    states=history_states(d,step)
    hs=[1,2,7,14,28] if step==1 else [1,2,4,8,13]
    training=examples(d[d.date<=train_end],states,step,hs)
    if len(training)<100 or training.units.sum()<=0:raise ValueError('Insufficient uncensored demand history for training.')
    print(f'Demand training: {len(training):,} direct-horizon examples through {train_end.date()}',flush=True)
    models={'recent_mean':StatisticalDemandModel('recent_mean'),'seasonal_naive':StatisticalDemandModel('seasonal_naive')}
    failures=[]
    for family in ['hgb']+(['xgboost'] if include_xgboost else []):
        tick=time.monotonic()
        try:models[family]=DirectDemandModel(family).fit(training)
        except ImportError as exc:
            if family=='hgb':raise
            failures.append(f'{family} unavailable: {type(exc).__name__}')
        print(f'Demand candidate {family}: {time.monotonic()-tick:.1f}s',flush=True)
    # Freeze model/blend selection before either uncertainty-calibration window.
    frames=[origin_frame(d,states,step,train_end+pd.Timedelta(days=i*horizon*step),horizon) for i in range(4)]
    selection=frames[0];cal=pd.concat(frames[1:3],ignore_index=True);test=frames[3]
    valid_selection=selection.units.notna() & selection.stockout.eq(0)
    valid_cal=cal.units.notna() & cal.stockout.eq(0);valid_test=test.units.notna() & test.stockout.eq(0)
    if not valid_selection.any():raise ValueError('No uncensored demand observations in the model-selection window.')
    selection_predictions={k:m.predict(selection) for k,m in models.items()}
    candidates=[dict(name=k,weights={k:1.},selection=metrics(selection.loc[valid_selection,'units'],p[valid_selection])) for k,p in selection_predictions.items()]
    first,second=sorted(candidates,key=lambda c:c['selection']['wape'])[:2]
    for weight in [.25,.5,.75]:
        a,b=first['name'],second['name'];p=weight*selection_predictions[a]+(1-weight)*selection_predictions[b]
        candidates.append(dict(name=f'{a} {weight:g} + {b} {1-weight:g}',weights={a:weight,b:1-weight},selection=metrics(selection.loc[valid_selection,'units'],p[valid_selection])))
    winner=min(candidates,key=lambda c:c['selection']['wape'])
    bundle=DemandBundle({k:models[k] for k in winner['weights']},winner['weights'],step)
    bundle.holiday_calendar=calendar_for(d)
    cp=bundle.predict(cal);pred=bundle.predict(test)
    cal['error_scaled']=np.where(valid_cal,(cal.units-cp)/np.sqrt(np.maximum(cp,1)),np.nan)
    residuals={};pooled=[]
    for category,g in cal.groupby('category'):
        blocks=g.pivot(index=['origin',*KEYS],columns='horizon',values='error_scaled').reindex(columns=range(1,horizon+1))
        # Complete paths only: missing/stockout observations are never imputed as zero error.
        blocks=blocks.dropna().to_numpy()
        if len(blocks)>=20:residuals[category]=blocks
        if len(blocks):pooled.append(blocks)
    if not pooled or sum(len(b) for b in pooled)<20:raise ValueError('Need at least 20 complete uncensored calibration paths to estimate inventory uncertainty.')
    residuals['__pooled__']=np.concatenate(pooled)
    calibration_paths={k:len(v) for k,v in residuals.items()}
    calibrated={k:calibrate_residual_paths(v) for k,v in residuals.items()}
    bundle.residuals={k:v[0] for k,v in calibrated.items()}
    bundle.residual_radius_90={k:v[1] for k,v in calibrated.items()}
    bundle.horizon=horizon;bundle.model_id='demand-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    cal_predictions={k:m.predict(cal) for k,m in models.items()}
    test_predictions={k:m.predict(test) for k,m in models.items()}
    for c in candidates:
        p=sum(w*cal_predictions[k] for k,w in c['weights'].items())
        c['calibration']=metrics(cal.loc[valid_cal,'units'],p[valid_cal])
        p=sum(w*test_predictions[k] for k,w in c['weights'].items())
        c['test']=metrics(test.loc[valid_test,'units'],p[valid_test])
    horizons=[dict(horizon=h,**metrics(g.units,g.prediction)) for h,g in test.assign(prediction=pred).loc[valid_test].groupby('horizon')]
    # Out-of-time interval and cumulative-demand coverage, relevant to stock protection.
    covered=[];cumulative=[]
    for _,g in test.assign(prediction=pred).groupby(KEYS):
        g=g.sort_values('horizon')
        if len(g)!=horizon or g.units.isna().any() or g.stockout.any():continue
        paths=bundle.paths(g.prediction.to_numpy(),g.category.iloc[0])
        lo,hi=bundle.interval(g.prediction.to_numpy(),g.category.iloc[0])
        covered.extend(((g.units>=lo)&(g.units<=hi)).tolist())
        cumulative.append(float(g.units.sum()<=np.quantile(paths.sum(axis=1),.95)))
    card=dict(model_id=bundle.model_id,family='Direct multi-horizon demand · '+winner['name'],weights=winner['weights'],
        quality=quality,step_days=step,max_horizon=horizon,train_end=str(train_end.date()),data_end=str(end.date()),training_examples=len(training),
        selection_origins=[str(selection.origin.iloc[0].date())],
        calibration_origins=[str(f.origin.iloc[0].date()) for f in frames[1:3]],test_origin=str(test.origin.iloc[0].date()),
        selection=winner['selection'],calibration_paths=calibration_paths,uncertainty_protocol='separate-selection-absolute-paths-v2',
        calibration=winner['calibration'],test=metrics(test.loc[valid_test,'units'],pred[valid_test]),horizon_metrics=horizons,candidates=candidates,
        interval_90_coverage=float(np.mean(covered)) if covered else None,protection_95_coverage=float(np.mean(cumulative)) if cumulative else None,
        training_seconds=round(time.monotonic()-started,2),missing_candidates=failures,
        uncertainty='Model selection precedes two separate uncertainty-calibration origins. Horizon-specific normalized absolute errors use the finite-sample 90% order statistic; whole residual paths are reflected and scaled for centered simulation. Time and cross-product dependence can invalidate nominal coverage. Empirical risk estimates, not service guarantees.',
        evidence='Direct forecasts from fixed origins: no target-window sales update the lag features. Historical future offer schedules and prices are treated as known in backtesting.',
        features=['sales lags','rolling levels and volatility','zero-sales frequency','observation age','annual Fourier seasonality','weekday and month','Frozen market holiday calendar and client local flags','price and promotion calendar','four prior scheduled offer and peer exposures','peer exposure','forecast horizon'],
        holiday_calendar=bundle.holiday_calendar.metadata,
        limitations=['Observed sales can understate demand when availability is unknown.','Future prices and promotions must be supplied; otherwise regular price and no offers are assumed.',
            'Market-specific holidays use the frozen training calendar; custom events need historical examples and future flags.','Residual-path risk can drift across businesses and regimes.',
            'No daily precision is inferred from weekly data.','No causal-lift or achieved inventory-service guarantee is implied.'],
        development_note='Historical benchmark periods have been used during development. A new client time window and prospective inventory trial are required for independent validation.',
        production_ready=False,panel_sha256=hashlib.sha256(managed(panel_path).read_bytes()).hexdigest())
    bundle.card=card
    dump_model(bundle, out / 'model.joblib')
    card['artifact_sha256']=hashlib.sha256((out/'model.joblib').read_bytes()).hexdigest()
    write_frame(test.assign(prediction=pred), out / 'evaluation.parquet', index=False)
    # The manifest is the readiness marker. Publish it atomically after every artifact succeeds.
    manifest=out/'model_card.json.tmp'
    manifest.write_text(json.dumps(card,indent=2));manifest.replace(out/'model_card.json')
    print(json.dumps({'model':card['family'],'test':card['test'],'coverage':card['interval_90_coverage'],'seconds':card['training_seconds']}),flush=True)
    return card


class DemandOffer(OfferTerms):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    product_id:str
    start_date:str
    duration:int=Field(default=1,ge=1,le=28)
    discount:float=Field(default=.2,ge=0,le=.5)
    feature:bool=False
    display:bool=False


class DemandRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    source_id:str='benchmark'
    store_id:str='367'
    product_ids:list[str]=Field(default_factory=list,max_length=12)
    horizon:int=Field(default=8,ge=1,le=28)
    promotions:list[DemandOffer]=Field(default_factory=list,max_length=30)
    holidays:list[str]=Field(default_factory=list,max_length=60)
    use_confirmed_promotions:bool=True
    expected_calendar_revision:int|None=Field(default=None,ge=0)


class DemandEngine:
    def __init__(self,panel_path,artifact_dir,calendar_provider=None):
        self.calendar_provider=calendar_provider
        self.panel,self.quality=load_demand_panel(panel_path)
        self.card=json.loads((managed(artifact_dir)/'model_card.json').read_text())
        if hashlib.sha256(managed(panel_path).read_bytes()).hexdigest()!=self.card['panel_sha256']:
            raise ValueError('Demand history has changed since training. Import a new source version.')
        if hashlib.sha256((managed(artifact_dir)/'model.joblib').read_bytes()).hexdigest()!=self.card['artifact_sha256']:
            raise ValueError('Demand model integrity check failed.')
        self.bundle=load_model(managed(artifact_dir) / 'model.joblib')
        self.step=self.bundle.step;self.last=self.panel.date.max()
        self.states=history_states(self.panel,self.step)

    def catalog(self,store_id):
        d=self.panel if store_id=='all' else self.panel[self.panel.store_id==store_id]
        if d.empty:raise ValueError('Unknown demand store.')
        active=d[d.date>=self.last-pd.Timedelta(days=self.step*4)].product_id.unique()
        recent=d[d.date>=self.last-pd.Timedelta(days=self.step*13)]
        recent=recent[recent.product_id.isin(active)]
        rows=[]
        for pid,g in recent.groupby('product_id'):
            rows.append(dict(product_id=pid,name=g.name.iloc[0],category=g.category.iloc[0],
                average_units=float(g.groupby('date').units.sum().mean()),price=float(g.sort_values('date').base_price.iloc[-1])))
        return sorted(rows,key=lambda r:-r['average_units'])

    def forecast(self,req,include_paths=False):
        if req.horizon>self.bundle.horizon:raise ValueError(f'This model supports up to {self.bundle.horizon} {self.quality["frequency"]} periods.')
        if len(set(req.product_ids))!=len(req.product_ids):raise ValueError('Duplicate demand product choices.')
        catalog=self.catalog(req.store_id);known={p['product_id'] for p in catalog};ids=req.product_ids or [p['product_id'] for p in catalog[:6]]
        if set(ids)-known:raise ValueError('Unknown or inactive demand products.')
        scope=self.panel if req.store_id=='all' else self.panel[self.panel.store_id==req.store_id]
        f=origin_frame(scope,self.states,self.step,self.last,req.horizon,known_schedule=False)
        if f.empty:raise ValueError('No recent assortment available.')
        schedule={};warnings=[]
        calendar=self.calendar_provider(req.source_id,req.store_id) if self.calendar_provider else dict(revision=0,events=[],campaigns=[])
        if req.use_confirmed_promotions and req.expected_calendar_revision is not None and req.expected_calendar_revision!=calendar['revision']:
            raise ValueError('The confirmed promotion calendar changed. Refresh the calendar and recalculate before saving this decision.')
        applied=[]
        if req.use_confirmed_promotions:
            for event in calendar['events']:
                period_end=pd.Timestamp(event['period_end'])
                dates=pd.date_range(period_end-pd.Timedelta(days=6),period_end) if self.step==1 else [period_end]
                for date in dates:
                    if date<=self.last or date>f.date.max():continue
                    mask=f.store_id.eq(event['store_id'])&f.product_id.eq(event['product_id'])&f.date.eq(date)
                    if not mask.any():
                        warnings.append(f"Confirmed promotion {event['campaign_id']} has no active demand row for {event['product_id']} at {date.date()}.")
                        continue
                    schedule.setdefault((event['store_id'],event['product_id'],date),[]).append(event.get('terms') or terms_dict(event));applied.append({**event,'applied_date':str(date.date())})
        for holiday in calendar.get('local_holidays',[]):
            dt=pd.Timestamp(holiday['date']);mask=(f.date>=dt)&(f.date<dt+pd.Timedelta(days=self.step))
            if holiday.get('store_id'):mask &= f.store_id.eq(holiday['store_id'])
            f.loc[mask,'holiday']=1
        for offer in req.promotions:
            if offer.product_id not in set(f.product_id):raise ValueError('Promotion references an inactive product.')
            observed=scope[scope.product_id.eq(offer.product_id)].discount.max()
            if offer.discount>observed+.03:
                warnings.append(f'{offer.product_id}: planned discount exceeds observed history; its response is extrapolated.')
            start=pd.Timestamp(offer.start_date)
            if pd.isna(start) or start.tzinfo is not None or start not in set(f.date):raise ValueError('Promotion start must be a forecast period date.')
            for dt in pd.date_range(start,periods=offer.duration,freq=f'{self.step}D'):
                if dt>f.date.max():raise ValueError('Promotion extends beyond the forecast horizon.')
                keys={(store,offer.product_id,dt) for store in f.loc[f.product_id.eq(offer.product_id)&f.date.eq(dt),'store_id']}
                for key in keys:schedule.setdefault(key,[]).append(terms_dict(offer))
        f,profiles=apply_schedule(f,schedule)
        rewards={r['product_id'] for p in profiles if p['product_id'] in ids for r in p['rewards']}
        ids=list(dict.fromkeys([*ids,*sorted(rewards)]))
        if len(ids)>12:raise ValueError('This plan including reward choices exceeds 12 products. Split the plan into smaller assortments.')
        if any(p['groups'] for p in profiles if 'groups' in p):
            warnings.append('Conditional-offer redemption and basket distributions are synthetic or supplied assumptions; response uses an effective-discount proxy. Reward choice shares default to equal. Demand intervals do not include redemption or choice-share uncertainty.')
        for date in req.holidays:
            dt=pd.Timestamp(date)
            if dt not in set(f.date):raise ValueError('Holiday dates must align with a forecast period.')
            f.loc[f.date.eq(dt),'holiday']=1
        holiday_calendar=getattr(self.bundle,'holiday_calendar',None)
        if req.holidays and not self.quality['holiday_rows']:
            warnings.append('Custom holiday dates have no client historical flag examples; their uplift is unvalidated. The configured market holiday calendar remains separate.')
        if self.quality['annual_history_limited']:
            warnings.append('Fewer than two annual cycles are available; annual seasonal patterns have limited validation.')
        if (req.promotions or applied) and not any(k in ['hgb','xgboost'] for k in self.bundle.weights):
            warnings.append('This source selected a statistical comparator; it does not estimate a separate response to future offers.')
        cols=KEYS+['date','category','discount','feature','display']
        f=schedule_features(promotion_context(f),pd.concat([scope[cols],f[cols]],ignore_index=True),self.step)
        f['prediction']=self.bundle.predict(f)
        f,reward_allocations=apply_rewards(f,profiles,'prediction')
        f=f[f.product_id.isin(ids)]
        if set(ids)-set(f.product_id):raise ValueError('Selected products have no recent sales history at this origin.')
        series=[];raw_paths={}
        for (store,pid),g in f.groupby(KEYS):
            g=g.sort_values('date');point=g.prediction.to_numpy()
            seed=int(hashlib.sha256(f'{store}:{pid}'.encode()).hexdigest()[:8],16)
            paths=self.bundle.paths(point,g.category.iloc[0],seed=seed)
            lo,hi=self.bundle.interval(point,g.category.iloc[0],seed=seed)
            history=scope[scope.product_id.eq(pid)&scope.store_id.eq(store)].tail(13)
            rows=[dict(date=str(dt.date()),forecast=float(q),lower=float(a),upper=float(b),price=float(price),regular_price=float(regular))
                for dt,q,a,b,price,regular in zip(g.date,point,lo,hi,g.price,g.base_price)]
            series.append(dict(store_id=store,product_id=pid,name=g.name.iloc[0],category=g.category.iloc[0],
                total_units=float(point.sum()),rows=rows,history=[dict(date=str(r.date.date()),actual=float(r.units)) for r in history.itertuples()],
                average_recent=float(history.units.mean()),zero_share=float(history.units.eq(0).mean()),observation_age_days=int((self.last-history.date.max()).days)))
            if include_paths:raw_paths[(store,pid)]=paths
        aggregate=[dict(date=str(dt.date()),forecast=float(g.prediction.sum())) for dt,g in f.groupby('date')]
        result=dict(model_id=self.bundle.model_id,source_id=req.source_id,currency=self.quality['currency'],as_of=str(self.last.date()),frequency=self.quality['frequency'],step_days=self.step,
            horizon=req.horizon,total_units=float(f.prediction.sum()),series=series,aggregate=aggregate,
            offer_profiles=profiles,reward_allocations=reward_allocations,selected_product_ids=ids,
            promotion_calendar=dict(revision=calendar['revision'],included=req.use_confirmed_promotions,events=applied,campaign_ids=sorted({e['campaign_id'] for e in applied})),
            holiday_calendar=dict(metadata=holiday_calendar.metadata,events=holiday_calendar.calendar(f.date.unique(),self.step)) if holiday_calendar else None,
            assumptions=['Inventory is planned per product/store; stock is never pooled across stores.',
                'Future offers and holidays follow the supplied calendar; regular prices are otherwise held constant.',
                'Forecasts estimate sales-based demand. Missing availability can conceal stockout-driven lost sales.']+warnings,model_evidence=self.card)
        return (result,raw_paths) if include_paths else result
