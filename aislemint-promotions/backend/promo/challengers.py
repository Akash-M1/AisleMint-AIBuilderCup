"""Reproducible local model comparison; never changes the serving artifact by itself."""
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
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import PoissonRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from threadpoolctl import threadpool_limits

from .data import DATA
from .model import DemandModel, PromotionDemandModel, clean_baseline_mask, exposures, metrics
from .holidays import us_calendar,calendar_for

CATEGORICAL=['product_id','store_id','category']
OWN_TREATMENT=['discount','feature','display',*[f'lag_dose_{i}' for i in range(1,5)]]


class SeasonalDemandModel(DemandModel):
    """Common information set for HGB, XGBoost, CatBoost and baseline-only GLM."""
    def __init__(self,family='hgb',baseline=False,iterations=400):
        super().__init__(iterations)
        self.family=family
        self.baseline=baseline

    def _encode(self,df):
        x=self._raw_encode(df)
        phase=2*np.pi*df.date.dt.isocalendar().week.astype(float)/52.18
        for harmonic in range(2,5):
            x[f'season_sin_{harmonic}']=np.sin(harmonic*phase)
            x[f'season_cos_{harmonic}']=np.cos(harmonic*phase)
        x['month']=df.date.dt.month.astype(float)
        x['week_of_year']=df.date.dt.isocalendar().week.astype(float)
        if self.family=='glm':
            # Let the GLM learn different annual shapes per category, just as trees can.
            seasonal=[c for c in x if c.startswith('season_')]
            for category,code in self.codes['category'].items():
                for col in seasonal:
                    x[f'category_{code}_{col}']=x[col]*df.category.eq(category)
        if self.baseline:x=x.drop(columns=OWN_TREATMENT+['promotion_available',*[f'lag_available_{i}' for i in range(1,5)]],errors='ignore')
        return x[self.features] if hasattr(self,'features') else x

    def _native_frame(self,x):
        x=x.copy()
        if self.family=='xgboost':
            for col in CATEGORICAL:
                x[col]=pd.Categorical(x[col],categories=range(len(self.codes[col])))
        elif self.family=='catboost':
            for col in CATEGORICAL:x[col]=x[col].astype(str)
        return x

    def fit(self,df):
        x=self._fit_encoder(df)
        monotonic=[int(c in ['discount','feature','display']) for c in x]
        if self.family=='hgb':
            self.estimator=HistGradientBoostingRegressor(loss='poisson',max_iter=self.iterations,
                max_leaf_nodes=31,l2_regularization=12,min_samples_leaf=50,learning_rate=.05,
                early_stopping=False,categorical_features=[c in CATEGORICAL for c in x],
                monotonic_cst=monotonic,random_state=42)
        elif self.family=='xgboost':
            from xgboost import XGBRegressor
            self.estimator=XGBRegressor(objective='count:poisson',tree_method='hist',device='cpu',
                n_estimators=self.iterations,max_depth=6,min_child_weight=30,learning_rate=.05,
                reg_lambda=12,subsample=.9,colsample_bytree=1,enable_categorical=True,
                monotone_constraints=tuple(monotonic),n_jobs=4,random_state=42)
        elif self.family=='catboost':
            from catboost import CatBoostRegressor
            self.estimator=CatBoostRegressor(loss_function='Poisson',iterations=self.iterations,
                depth=6,learning_rate=.05,l2_leaf_reg=12,cat_features=CATEGORICAL,
                monotone_constraints=monotonic,thread_count=4,random_seed=42,
                verbose=False,allow_writing_files=False)
        elif self.family=='glm':
            if not self.baseline:raise ValueError('GLM challenger is baseline-only; response requires monotonic treatment handling.')
            numeric=[c for c in x if c not in CATEGORICAL]
            encoder=ColumnTransformer([
                ('category',OneHotEncoder(handle_unknown='ignore'),CATEGORICAL),
                ('numeric',make_pipeline(SimpleImputer(strategy='median'),StandardScaler()),numeric)])
            self.estimator=make_pipeline(encoder,PoissonRegressor(alpha=.05,max_iter=self.iterations,tol=1e-5))
        else:raise ValueError(f'Unknown model family: {self.family}')
        with threadpool_limits(limits=4):self.estimator.fit(self._native_frame(x),df.units)
        self.trained_until=str(df.date.max().date())
        return self

    def predict(self,df):
        x=self._native_frame(self._encode(df))
        with threadpool_limits(limits=4):
            p=self.estimator.predict(x,prediction_type='Exponent') if self.family=='catboost' else self.estimator.predict(x)
        return np.maximum(0,np.asarray(p))


class WeightedDemandModel:
    def __init__(self,models,weights,names):
        self.models=models;self.weights=np.asarray(weights,dtype=float);self.names=names
        if len(models)!=len(weights) or np.any(self.weights<0) or not np.isclose(self.weights.sum(),1):
            raise ValueError('Ensemble weights must be nonnegative and sum to one.')
        self.features=sorted(set().union(*(m.features for m in models)))
        self.trained_until=max(m.trained_until for m in models)
        self.iterations=sum(getattr(m,'iterations',0) for m in models)

    def predict(self,df):
        return sum(w*m.predict(df) for w,m in zip(self.weights,self.models))


def select_blend(models,predictions,y):
    """Select a single model or a convex blend of the best two; calibration only."""
    rows=[dict(name=name,weights={name:1.},calibration_wape=metrics(y,p)['wape']) for name,p in predictions.items()]
    best=sorted(rows,key=lambda r:r['calibration_wape'])[:2]
    if len(best)==2:
        a,b=[r['name'] for r in best]
        for weight in [.25,.5,.75]:
            rows.append(dict(name=f'{a} {weight:g} + {b} {1-weight:g}',weights={a:weight,b:1-weight},
                calibration_wape=metrics(y,weight*predictions[a]+(1-weight)*predictions[b])['wape']))
    selected=min(rows,key=lambda r:r['calibration_wape'])
    names=list(selected['weights'])
    model=models[names[0]] if len(names)==1 else WeightedDemandModel(
        [models[name] for name in names],list(selected['weights'].values()),names)
    return model,selected,rows


def train_challengers(panel_path=None,output_dir=None):
    started=time.monotonic()
    model_id='model-lab-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    out=managed(output_dir or DATA.parent / 'artifacts/experiments' / model_id)
    out.mkdir(parents=True,exist_ok=False)
    panel_path=managed(panel_path or DATA / 'processed/panel.parquet')
    d=exposures(read_frame(panel_path))
    d=d[d.get('stockout',pd.Series(0,index=d.index)).eq(0)]
    weeks=sorted(d.week.unique());folds=[];latest=None
    configurations=[('hgb',400),('xgboost',500),('catboost',600)];missing_candidates=[]
    protocol=dict(selection='Clean calibration WAPE for baseline; full calibration WAPE for composed demand response. Convex blends of best two at weights 0.25/0.5/0.75.',
        configurations=configurations,glm=dict(role='baseline',alpha=.05,iterations=400),
        seasonal_features='Annual Fourier orders 1 through 4, month, ISO week and supplied US occasions/federal observance; category interactions in GLM.',
        threads=4,test_usage='Reported after selection. Existing historical test periods have been inspected in earlier development; these are retrospective benchmarks, not a fresh prospective audit.',
        promotion_policy='This experiment does not replace the serving model or validate causal effects.')
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2))
    for fold_index,cut in enumerate([len(weeks)-53,len(weeks)-27]):
        tr=d[d.week<=cut];cal=d[(d.week>cut)&(d.week<=cut+13)];test=d[(d.week>cut+13)&(d.week<=cut+26)]
        clean=tr[clean_baseline_mask(tr)];clean_cal=cal[clean_baseline_mask(cal)]
        if len(clean_cal)<20:raise ValueError('Model comparison needs at least 20 clean calibration observations.')
        print(f'Fold {fold_index+1}: {len(tr):,} training rows; train ends {tr.date.max().date()}',flush=True)
        incumbent=PromotionDemandModel().fit(tr,cal)
        baselines={'legacy_hgb':incumbent.baseline_model};responses={'legacy_hgb':incumbent.response_model}
        timings={}
        for family,iterations in configurations:
            tick=time.monotonic()
            try:
                base_candidate=SeasonalDemandModel(family,baseline=True,iterations=iterations).fit(clean)
                response_candidate=SeasonalDemandModel(family,iterations=iterations).fit(tr)
            except ImportError:
                if family=='hgb':raise
                missing_candidates.append(f'{family} unavailable: install the optional model-lab runtime.');continue
            baselines[family]=base_candidate;responses[family]=response_candidate
            timings[family]=round(time.monotonic()-tick,2)
            print(f'  Fitted {family} baseline and response in {timings[family]}s',flush=True)
        tick=time.monotonic()
        baselines['glm']=SeasonalDemandModel('glm',baseline=True,iterations=400).fit(clean)
        timings['glm']=round(time.monotonic()-tick,2)
        baseline,best_base,base_rows=select_blend(baselines,{k:m.predict(clean_cal) for k,m in baselines.items()},clean_cal.units)
        # Hold the ML baseline fixed while comparing each promotion response learner.
        neutral=cal.copy();neutral[OWN_TREATMENT]=0
        if 'promotion_available' in neutral:neutral[['promotion_available',*[f'lag_available_{i}' for i in range(1,5)]]]=0
        base_cal=baseline.predict(cal)
        response_deltas={k:m.predict(cal)-m.predict(neutral) for k,m in responses.items()}
        response_predictions={k:np.maximum(0,base_cal+v) for k,v in response_deltas.items()}
        # Blend signed response differences BEFORE nonnegative clipping, matching serving semantics.
        response_rows=[dict(name=k,weights={k:1.},calibration_wape=metrics(cal.units,p)['wape']) for k,p in response_predictions.items()]
        leaders=sorted(response_rows,key=lambda r:r['calibration_wape'])[:2]
        a,b=[r['name'] for r in leaders]
        for w in [.25,.5,.75]:
            p=np.maximum(0,base_cal+w*response_deltas[a]+(1-w)*response_deltas[b])
            response_rows.append(dict(name=f'{a} {w:g} + {b} {1-w:g}',weights={a:w,b:1-w},calibration_wape=metrics(cal.units,p)['wape']))
        best_response=min(response_rows,key=lambda r:r['calibration_wape'])
        names=list(best_response['weights'])
        response=responses[names[0]] if len(names)==1 else WeightedDemandModel(
            [responses[k] for k in names],list(best_response['weights'].values()),names)
        m=PromotionDemandModel();m.baseline_model=baseline;m.response_model=response
        m.features=response.features;m.trained_until=response.trained_until
        m.baseline_training_rows=len(clean);m.baseline_training_products=clean.product_id.nunique();m.baseline_training_stores=clean.store_id.nunique()
        cp=m.predict(cal);pred=m.predict(test)
        clean_test=test[clean_baseline_mask(test)]
        score=np.abs(cal.units.to_numpy()-cp)/np.sqrt(np.maximum(cp,1))
        m.q=float(np.quantile(score,min(1,np.ceil((len(score)+1)*.9)/len(score)),method='higher'))
        m.q_product={k:float(np.quantile(v,.9)) for k,v in pd.Series(score,index=cal.product_id).groupby(level=0) if len(v)>100}
        width=np.array([m.q_product.get(k,m.q) for k in test.product_id])*np.sqrt(np.maximum(pred,1))
        naive=tr.groupby(['store_id','product_id']).units.mean().reindex(pd.MultiIndex.from_frame(test[['store_id','product_id']])).fillna(tr.units.mean()).to_numpy()
        seasonal=d.set_index(['store_id','product_id','date']).units.reindex(pd.MultiIndex.from_arrays([
            test.store_id,test.product_id,test.date-pd.Timedelta(weeks=52)])).to_numpy()
        seasonal=np.where(np.isfinite(seasonal),seasonal,naive)
        report=dict(train_start=str(tr.date.min().date()),train_end=str(tr.date.max().date()),
            calibration_start=str(cal.date.min().date()),calibration_end=str(cal.date.max().date()),test_start=str(test.date.min().date()),test_end=str(test.date.max().date()),
            train_rows=len(tr),test_rows=len(test),baseline_train_rows=len(clean),baseline_test_rows=len(clean_test),
            baseline_selected=best_base,response_selected=best_response,baseline_candidates=base_rows,response_candidates=response_rows,
            model=metrics(test.units,pred),baseline_model=metrics(clean_test.units,baseline.predict(clean_test)),
            historical_mean=metrics(test.units,naive),seasonal_naive=metrics(test.units,seasonal),
            incumbent_model=metrics(test.units,incumbent.predict(test)),incumbent_baseline=metrics(clean_test.units,incumbent.baseline_predict(clean_test)),
            incumbent_calibration=metrics(cal.units,incumbent.predict(cal)),calibration=metrics(cal.units,cp),
            response_only=metrics(test.units,response.predict(test)),promo=metrics(test.loc[test.promoted==1,'units'],pred[test.promoted==1]),
            non_promo=metrics(test.loc[test.promoted==0,'units'],pred[test.promoted==0]),
            interval_coverage=float(((test.units>=np.maximum(0,pred-width))&(test.units<=pred+width)).mean()),training_seconds_by_family=timings)
        # Test columns are attached only after choices are frozen and cannot affect selection.
        for row in base_rows:
            bp=sum(w*baselines[name].predict(clean_test) for name,w in row['weights'].items())
            row['test_wape']=metrics(clean_test.units,bp)['wape']
        neutral_test=test.copy();neutral_test[OWN_TREATMENT]=0
        if 'promotion_available' in neutral_test:neutral_test[['promotion_available',*[f'lag_available_{i}' for i in range(1,5)]]]=0
        btest=baseline.predict(test)
        rd={name:r.predict(test)-r.predict(neutral_test) for name,r in responses.items()}
        for row in response_rows:
            rp=np.maximum(0,btest+sum(w*rd[name] for name,w in row['weights'].items()))
            row['test_wape']=metrics(test.units,rp)['wape']
        folds.append(report);latest=m
        (out/f'fold-{fold_index+1}.json').write_text(json.dumps(report,indent=2))
        print(json.dumps({'fold':fold_index+1,'baseline':best_base,'response':best_response,'test':report['model'],'incumbent_test':report['incumbent_model']},indent=2),flush=True)
    latest.model_id=model_id
    dump_model(latest, out / 'model.joblib')
    evaluation=test.copy();evaluation['prediction']=latest.predict(test);evaluation['baseline_prediction']=latest.baseline_predict(test)
    write_frame(evaluation, out / 'evaluation.parquet', index=False)
    family='ML baseline + promotion response: '+folds[-1]['baseline_selected']['name']+' / '+folds[-1]['response_selected']['name']
    card=dict(model_id=model_id,family=family,missing_candidates=sorted(set(missing_candidates)),features=latest.features,folds=folds,trained_until=latest.trained_until,
        training_seconds=round(time.monotonic()-started,2),data_end=str(d.date.max().date()),rows=len(d),protocol=protocol,
        baseline=dict(family=folds[-1]['baseline_selected']['name'],iterations=latest.baseline_model.iterations,
            selection=protocol['selection'],candidates=folds[-1]['baseline_candidates'],training_rows=latest.baseline_training_rows,
            training_products=latest.baseline_training_products,training_stores=latest.baseline_training_stores,
            trained_until=latest.baseline_model.trained_until,features=latest.baseline_model.features,human_entered=False,
            target='Observed non-promoted unit sales after four promotion-free weeks'),
        production_ready=False,validation_scope='Retrospective weekly conditional demand; no causal validation.',
        intervals='90% marginal demand bands from calibration residuals; not causal-effect confidence intervals.',
        holiday_calendar=calendar_for(d).metadata,
        limitations=['Observational effects remain unvalidated.','No stockout, competitor or local store-closure inputs in the current benchmark.',
            'Four-week promotion lags can miss longer purchase cycles.','Historical test periods have been reused during development.',
            'Demand validation under observed schedules does not establish scenario or recommendation profit accuracy.'],
        recommendation_gate=folds[-1]['model']['wape']<min(folds[-1]['historical_mean']['wape'],folds[-1]['seasonal_naive']['wape']),
        artifact_sha256=hashlib.sha256((out/'model.joblib').read_bytes()).hexdigest(),
        panel_sha256=hashlib.sha256(panel_path.read_bytes()).hexdigest())
    (out/'model_card.json').write_text(json.dumps(card,indent=2))
    print(f'Experiment saved: {out}',flush=True)
    return out,card
