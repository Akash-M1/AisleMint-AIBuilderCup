from __future__ import annotations
from .storage import AssetPath,managed,read_frame,write_frame,load_model,dump_model
import json
import time
import hashlib
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits
from .data import DATA, ARTIFACTS
from .holidays import us_calendar,calendar_for


def exposures(frame: pd.DataFrame) -> pd.DataFrame:
    """Only known prices/promotion exposure; never contemporaneous sales."""
    d=frame.copy()
    d['dose']=d.discount+.10*d.display+.08*d.feature
    idx=pd.MultiIndex.from_frame(d[['store_id','product_id','date']])
    dose=pd.Series(d.dose.to_numpy(),index=idx)
    for lag in range(1,5):
        past=pd.MultiIndex.from_arrays([d.store_id,d.product_id,d.date-pd.Timedelta(weeks=lag)])
        d[f'lag_dose_{lag}']=dose.reindex(past).to_numpy()
        if 'promotion_available' in d:d[f'lag_available_{lag}']=pd.Series(d.promotion_available.to_numpy(),index=idx).reindex(past).to_numpy()
    g=d.groupby(['store_id','date','category']).dose
    d['peer_dose']=(g.transform('sum')-d.dose)/(g.transform('count')-1).clip(lower=1)
    category=d.groupby(['store_id','date','category']).dose.mean().unstack('category')
    for c in sorted(d.category.unique()):
        key=pd.MultiIndex.from_frame(d[['store_id','date']])
        d['category_dose_'+c]=np.where(d.category.eq(c),0,category[c].reindex(key).to_numpy())
    return d

class DemandModel:
    def __init__(self,iterations=140):
        self.iterations=iterations

    def _raw_encode(self,df):
        x=pd.DataFrame(index=df.index)
        for c in ['product_id','store_id','category']:
            x[c]=df[c].map(self.codes[c]).fillna(-1).astype(int)
        iso=df.date.dt.isocalendar().week.astype(float)
        x['season_sin']=np.sin(2*np.pi*iso/52.18)
        x['season_cos']=np.cos(2*np.pi*iso/52.18)
        x['trend']=(df.date-self.origin).dt.days/365.25
        x['base_price']=df.base_price
        key=pd.MultiIndex.from_frame(df[['store_id','product_id']])
        x['clean_rate']=self.rates.reindex(key).to_numpy()
        x['clean_rate']=x.clean_rate.fillna(df.product_id.map(self.product_rate)).fillna(self.global_rate)
        for c in self.treatments:
            x[c]=df[c] if c in df else 0.
        if getattr(self,'holiday_calendar',None) is not None:
            holiday=self.holiday_calendar.features(df.date,7)
            for col in holiday:x[col]=holiday[col].to_numpy()
            if 'client_holiday_any' in holiday:x['client_holiday_any']=np.maximum(x.client_holiday_any,df.get('holiday',pd.Series(0,index=df.index)).fillna(0))
        return x

    def _encode(self,df):
        x=self._raw_encode(df)
        return x[self.features] if hasattr(self,'features') else x

    def _fit_encoder(self,df):
        self.holiday_calendar=calendar_for(df)
        self.origin=df.date.min()
        self.codes={c:{v:i for i,v in enumerate(sorted(df[c].unique()))} for c in ['product_id','store_id','category']}
        clean=df[(df.promoted==0)&(df[[f'lag_dose_{i}' for i in range(1,5)]].fillna(0).max(axis=1)<.02)]
        if len(clean)<100: clean=df[df.promoted==0]
        self.global_rate=float(clean.units.mean())
        self.product_rate=clean.groupby('product_id').units.mean()
        stats=clean.groupby(['store_id','product_id']).units.agg(['sum','count'])
        prior=stats.index.get_level_values('product_id').map(self.product_rate).to_numpy()
        self.rates=(stats['sum']+8*prior)/(stats['count']+8)
        self.treatments=['discount','feature','display','peer_dose']+[f'lag_dose_{i}' for i in range(1,5)]+sorted(c for c in df if c.startswith('category_dose_'))
        if 'promotion_available' in df:self.treatments+=['promotion_available']+[f'lag_available_{i}' for i in range(1,5)]
        x=self._encode(df);self.features=list(x.columns)
        return x

    def fit(self,df):
        x=self._fit_encoder(df)
        mono=[1 if c in ['discount','feature','display'] else 0 for c in self.features]
        self.estimator=HistGradientBoostingRegressor(loss='poisson',max_iter=self.iterations,max_leaf_nodes=23,
            l2_regularization=12,min_samples_leaf=50,learning_rate=.07,early_stopping=False,
            categorical_features=[c in ['product_id','store_id','category'] for c in self.features],
            monotonic_cst=mono,random_state=42)
        with threadpool_limits(limits=4): self.estimator.fit(x,df.units)
        self.trained_until=str(df.date.max().date());return self

    def predict(self,df):
        with threadpool_limits(limits=4): return np.maximum(0,self.estimator.predict(self._encode(df)))


def clean_baseline_mask(df):
    lags=df[[f'lag_dose_{i}' for i in range(1,5)]]
    clean=(df.discount.le(.001) & df.feature.eq(0) & df.display.eq(0)
            & lags.notna().all(axis=1) & lags.max(axis=1).le(.001)
            & df.get('stockout',pd.Series(0,index=df.index)).eq(0))
    if 'promotion_available' in df:clean &= df.promotion_available.eq(0)&df[[f'lag_available_{i}' for i in range(1,5)]].fillna(1).max(axis=1).eq(0)
    return clean

class BaselineModel(DemandModel):
    """A separate supervised learner fitted only to clean non-promotion sales."""
    def _encode(self,df):
        x=super()._encode(df)
        return x.drop(columns=['discount','feature','display','promotion_available',*[f'lag_dose_{i}' for i in range(1,5)],*[f'lag_available_{i}' for i in range(1,5)]],errors='ignore')

class PromotionDemandModel:
    """ML baseline + learned promotion response, calibrated and tested jointly."""
    def fit(self,df,calibration_df=None):
        df=df[df.get('stockout',pd.Series(0,index=df.index)).eq(0)]
        clean=df[clean_baseline_mask(df)]
        if len(clean)<20 or clean.units.sum()<=0:
            raise ValueError('Baseline training needs at least 20 non-promoted observations with four prior promotion-free weeks and positive sales.')
        self.response_model=DemandModel().fit(df)
        self.baseline_candidates=[]
        clean_cal=calibration_df[clean_baseline_mask(calibration_df)] if calibration_df is not None else clean.iloc[:0]
        models=[]
        for iterations in [160,320,500]:
            candidate=BaselineModel(iterations=iterations).fit(clean)
            score=metrics(clean_cal.units,candidate.predict(clean_cal))['wape'] if len(clean_cal) else 0
            self.baseline_candidates.append(dict(iterations=iterations,calibration_rows=len(clean_cal),calibration_wape=score))
            models.append(candidate)
        self.baseline_model=models[int(np.argmin([x['calibration_wape'] for x in self.baseline_candidates]))]
        self.baseline_training_rows=len(clean)
        self.baseline_training_products=clean.product_id.nunique()
        self.baseline_training_stores=clean.store_id.nunique()
        self.trained_until=self.response_model.trained_until
        self.features=self.response_model.features
        return self

    def baseline_predict(self,df):
        # The baseline learner has no own-discount/display/feature/lag inputs.
        return self.baseline_model.predict(df)

    def predict(self,df):
        neutral=df.copy()
        neutral[['discount','feature','display',*[f'lag_dose_{i}' for i in range(1,5)]]]=0
        if 'promotion_available' in neutral:neutral[['promotion_available',*[f'lag_available_{i}' for i in range(1,5)]]]=0
        response=self.response_model.predict(df)-self.response_model.predict(neutral)
        return np.maximum(0,self.baseline_predict(df)+response)


def metrics(y,p):
    y=np.asarray(y);p=np.asarray(p)
    return dict(wape=float(np.abs(y-p).sum()/max(y.sum(),1)),bias=float((p-y).sum()/max(y.sum(),1)),
        mae=float(np.abs(y-p).mean()),rmse=float(np.sqrt(((y-p)**2).mean())))


def train(panel_path=None,output_dir=None):
    start=time.monotonic();out=managed(output_dir or ARTIFACTS);out.mkdir(parents=True,exist_ok=True)
    raw=read_frame(panel_path or DATA / 'processed/panel.parquet')
    d=exposures(raw);weeks=sorted(d.week.unique());folds=[]
    d=d[d.get('stockout',pd.Series(0,index=d.index)).eq(0)]
    # Two chronological folds, independent 13-week calibration and test windows.
    cutoffs=[len(weeks)-53,len(weeks)-27]
    best=None
    for cut in cutoffs:
        tr=d[d.week<=cut];cal=d[(d.week>cut)&(d.week<=cut+13)];test=d[(d.week>cut+13)&(d.week<=cut+26)]
        if len(tr)<20 or cal.empty or test.empty:raise ValueError('Too few uncensored observations in a chronological training, calibration or test window.')
        print('Training through',tr.date.max(),'rows',len(tr),flush=True)
        m=PromotionDemandModel().fit(tr,cal)
        cp=m.predict(cal);pred=m.predict(test)
        score=np.abs(cal.units.to_numpy()-cp)/np.sqrt(np.maximum(cp,1))
        q=float(np.quantile(score,min(1,np.ceil((len(score)+1)*.9)/len(score)),method='higher'))
        byproduct={k:float(np.quantile(v,.9)) for k,v in pd.Series(score,index=cal.product_id).groupby(level=0) if len(v)>100}
        naive_lookup=tr.groupby(['store_id','product_id']).units.mean()
        index=pd.MultiIndex.from_frame(test[['store_id','product_id']])
        naive=naive_lookup.reindex(index).fillna(tr.units.mean()).to_numpy()
        seasonal=d.set_index(['store_id','product_id','date']).units
        old=pd.MultiIndex.from_arrays([test.store_id,test.product_id,test.date-pd.Timedelta(weeks=52)])
        sp=seasonal.reindex(old).to_numpy();sp=np.where(np.isfinite(sp),sp,naive)
        width=np.array([byproduct.get(k,q) for k in test.product_id])*np.sqrt(np.maximum(pred,1))
        clean_test=test[clean_baseline_mask(test)]
        baseline_prediction=m.baseline_predict(clean_test) if len(clean_test) else np.array([])
        report=dict(baseline_selected_iterations=m.baseline_model.iterations,baseline_candidates=m.baseline_candidates,baseline_train_rows=m.baseline_training_rows,baseline_test_rows=len(clean_test),
            baseline_model=metrics(clean_test.units,baseline_prediction) if len(clean_test) else None,
            response_only=metrics(test.units,m.response_model.predict(test)),
            train_start=str(tr.date.min().date()),train_end=str(tr.date.max().date()),calibration_start=str(cal.date.min().date()),
            calibration_end=str(cal.date.max().date()),test_start=str(test.date.min().date()),test_end=str(test.date.max().date()),train_rows=len(tr),test_rows=len(test),
            model=metrics(test.units,pred),historical_mean=metrics(test.units,naive),seasonal_naive=metrics(test.units,sp),
            interval_coverage=float(((test.units>=np.maximum(0,pred-width))&(test.units<=pred+width)).mean()),
            non_promo=metrics(test.loc[test.promoted==0,'units'],pred[test.promoted==0]),promo=metrics(test.loc[test.promoted==1,'units'],pred[test.promoted==1]))
        folds.append(report);print(json.dumps(report),flush=True)
        best=m;best.q=q;best.q_product=byproduct
    model_id='hgb-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    best.model_id=model_id
    # Keep the evaluated model intact: held-out weeks never enter fitting.
    dump_model(best, out / 'model.joblib')
    test=d[(d.week>cutoffs[-1]+13)]
    test=test.copy();test['prediction']=best.predict(test);test['baseline_prediction']=best.baseline_predict(test)
    write_frame(test, out / 'evaluation.parquet', index=False)
    artifact_hash=hashlib.sha256((out/'model.joblib').read_bytes()).hexdigest()
    report=dict(model_id=model_id,family='Two-stage ML · baseline + promotion response',features=best.features,
        holiday_calendar=best.baseline_model.holiday_calendar.metadata,
        baseline=dict(family='Poisson histogram gradient boosting',iterations=best.baseline_model.iterations,
            selection='Lowest WAPE on clean calibration weeks, among 160/320/500-tree candidates. The test window is not used for model selection.',
            candidates=best.baseline_candidates,
            training_rows=best.baseline_training_rows,training_products=best.baseline_training_products,training_stores=best.baseline_training_stores,
            trained_until=best.baseline_model.trained_until,features=best.baseline_model.features,
            target='Observed non-promoted unit sales after four promotion-free weeks',human_entered=False,
            validation='Evaluated only on unseen clean non-promotion rows. True baseline during a promotion remains unobserved.'),
        training_seconds=round(time.monotonic()-start,2),folds=folds,trained_until=best.trained_until,
        data_end=str(d.date.max().date()),rows=len(d),artifact_sha256=artifact_hash,
        panel_sha256=hashlib.sha256(managed(panel_path or DATA / 'processed/panel.parquet').read_bytes()).hexdigest(),
        production_ready=False,validation_scope='Weekly conditional demand, observed promotion schedules. Not validation of causal lift or multi-week intervention outcomes.',
        intervals='90% marginal prediction bands calibrated on subsequent 13 weeks. Portfolio sums use conservative full-correlation widths; causal-effect intervals are not identified.',
        limitations=['Observational model; unmeasured confounding remains.','Costs, inventory, competitor prices and promotion execution are absent from the benchmark.',
        'Cross-category exposure is a proxy, not identified halo. Basket relationships are association only.',
        'Four weeks of lagged promotion exposure approximate carryover; longer pantry loading is unmeasured.',
        'Evaluation holds out time, not unseen businesses. Retrain and calibrate for each client.',
        'Past weeks in the evaluation period update exposure inputs only; they do not refit model parameters.'],
        recommendation_gate=folds[-1]['model']['wape'] < min(folds[-1]['historical_mean']['wape'],folds[-1]['seasonal_naive']['wape']))
    (out/'model_card.json').write_text(json.dumps(report,indent=2));return report
