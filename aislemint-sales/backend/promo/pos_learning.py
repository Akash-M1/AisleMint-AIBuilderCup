from .storage import AssetPath,managed,read_frame,write_frame,load_model,dump_model
"""Observed trading-day snapshots, transparent drift checks and isolated model candidates."""
from datetime import date,timedelta,datetime,timezone
from zoneinfo import ZoneInfo
import json,threading,logging,os
from contextlib import nullcontext
import numpy as np
import pandas as pd
from fastapi import APIRouter,Header,HTTPException
from pydantic import Field
from .pos_api import operation,service
from .pos_core import StrictModel,uid,now,local_day,audit_event
from .pos_reports import csv_response
from .data import validate_panel
from .demand import validate_demand_panel

from .pg import MongoRoute
router=APIRouter(route_class=MongoRoute,prefix='/api/pos',tags=['OneSales model data'])
FIELDS=['date','store_id','product_id','name','category','units','price','base_price','feature','display','holiday','stockout','unit_cost','SPEND','free_promotion','promotion_available','returned_units','refund_ex_gst','currency','country']

def daily_snapshot(c,s,day):
    products=c.run('pos_learning_daily_snapshot_aggregate_pos_products', (s['id'], day)).fetchall()
    holiday=bool(c.run('pos_learning_daily_snapshot_aggregate_pos_holidays', (s['id'], day)).fetchone())
    start=datetime.fromisoformat(day).replace(tzinfo=ZoneInfo(s['timezone']));end=(start+timedelta(days=1)).astimezone(timezone.utc).isoformat()
    promo_day=s['promo_reference_date'] if s['demo'] and s['promo_reference_date'] else day
    available={}
    if s['forecast_workspace']:
        for event in c.run('pos_learning_daily_snapshot_aggregate_promotion_events', (s['forecast_workspace'], s['forecast_store_id'], promo_day, (date.fromisoformat(promo_day) + timedelta(days=6)).isoformat(), end, start.astimezone(timezone.utc).isoformat())):
            offer=next(p for p in json.loads(event['request'])['promotions'] if p['product_id']==event['product_id'])
            available.setdefault(event['product_id'],[]).append(offer)
    result=[]
    for p in products:
        p=dict(p)
        version=c.run('pos_learning_daily_snapshot_aggregate_pos_events', (s['id'], p['product_id'], end)).fetchone()
        if version:p.update(json.loads(version['payload'])['product'])
        sold=c.run('pos_learning_daily_snapshot_aggregate_pos_sale_lines', (s['id'], p['product_id'], day)).fetchall()
        movement=c.run('pos_learning_daily_snapshot_aggregate_pos_inventory_movements', (s['id'], p['product_id'], day)).fetchone()
        if not sold and not movement:continue  # Product was not stocked/tracked; do not invent zero demand.
        if not sold and not p['active']:continue
        units=sum(r['quantity'] for r in sold);net=sum(r['net_paise'] for r in sold)/100
        regular=sum(r['quantity']*next(l['unit_price_paise']*10000/(10000+l['gst_bps']) for l in json.loads(r['snapshot'])['lines'] if l['product_id']==p['product_id']) for r in sold)/100
        returns=c.run('pos_learning_daily_snapshot_aggregate_pos_return_lines', (s['id'], p['product_id'], day)).fetchone()
        offers=available.get(p['product_id'],[])
        feature=max([int(o.get('feature',0)) for o in offers],default=0);display=max([int(o.get('display',0)) for o in offers],default=0)
        for r in sold:
            for e in json.loads(r['snapshot'])['promotion_terms']:
                if e['terms']['product_id']==p['product_id']:feature=max(feature,e['terms']['feature']);display=max(display,e['terms']['display'])
        at_zero=bool(c.run('pos_learning_daily_snapshot_aggregate_pos_inventory_movements_2', (s['id'], p['product_id'], day)).fetchone()) or not movement or movement['balance']==0
        base=regular/units if units else p['price_paise']*100/(10000+p['gst_bps'])
        row=dict(date=day,store_id=s['forecast_store_id'] or s['id'],product_id=p['product_id'],name=p['name'],category=p['category'],units=units,price=net/units if units else base,base_price=base,feature=feature,display=display,holiday=int(holiday),stockout=int(at_zero),unit_cost=sum(r['quantity']*r['cost_paise'] for r in sold)/100/units if units else p['cost_paise']/100,SPEND=net,free_promotion=int(units>0 and net==0),returned_units=returns['units'],refund_ex_gst=returns['net']/100,currency='INR',country='IN')
        row['promotion_available']=int(bool(offers))
        if not units:row['price']=base*(1-max([o.get('discount',0) for o in offers],default=0))
        c.run('pos_learning_daily_snapshot_insert_pos_daily', (s['id'], p['product_id'], day, json.dumps(row)));result.append(row)
    return result

def panel(c,s,kind='demand'):
    rows=[json.loads(r[0]) for r in c.run('pos_learning_panel_aggregate_pos_daily', (s['id'],))]
    d=pd.DataFrame(rows,columns=FIELDS)
    if len(d):d['store_id']=s['forecast_store_id'] or s['id']
    d['promotion_available']=d.promotion_available.fillna(0)
    if kind=='promotion' and len(d):
        # Weekly product rows require seven explicitly closed, observed days.
        d['week']=pd.to_datetime(d.date).dt.to_period('W-SUN').dt.end_time.dt.normalize();weekly=[]
        for (store,pid,end),g in d.groupby(['store_id','product_id','week']):
            if g.date.nunique()!=7:continue
            units=int(g.units.sum());net=float(g.SPEND.sum());base=float((g.base_price*g.units).sum()/units) if units else float(g.base_price.mean())
            row=g.iloc[-1].drop('week').to_dict();row.update(date=str(end.date()),units=units,SPEND=net,price=net/units if units else base,base_price=base,feature=int(g.feature.max()),display=int(g.display.max()),holiday=int(g.holiday.max()),stockout=int(g.stockout.max()),unit_cost=float((g.unit_cost*g.units).sum()/units) if units else float(g.unit_cost.mean()),free_promotion=int(units>0 and net==0),returned_units=int(g.returned_units.sum()),refund_ex_gst=float(g.refund_ex_gst.sum()))
            row['promotion_available']=int(g.promotion_available.max())
            weekly.append(row)
        d=pd.DataFrame(weekly,columns=FIELDS)
    return d

def retail_panel(c,s,kind='demand',through=None):
    d=panel(c,s,kind)
    if len(d):d=d[d.date.lt(local_day(s)) & d.date.le(through or local_day(s))]
    return d

def readiness(c,s):
    result={}
    retail_shop=c.db.retail_shops.find_one({'pos_store_id':s['id']},session=c.session)
    if retail_shop:
        from .retail.contracts import readiness as check,normalize
        for kind in ('demand','promotion'):
            d=retail_panel(c,s,kind);records,errors=normalize(d.to_dict('records'),retail_shop)
            _,report=check(records or [],kind,'daily' if kind=='demand' else 'weekly')
            if errors:report['ready']=False;report['issues']=[e['message'] for e in errors]
            result[kind]=dict(ready=report['ready'],reason='; '.join(report['issues']) or None,rows=len(d),periods=int(d.date.nunique()) if len(d) else 0,quality=report['quality'])
        return result
    for kind,validator in [('demand',validate_demand_panel),('promotion',validate_panel)]:
        d=panel(c,s,kind);reason=None;quality=None
        try:
            if d.empty:raise ValueError('Close tracked trading days to create observed training data.')
            _,quality=validator(d)
        except ValueError as exc:reason=str(exc)
        result[kind]=dict(ready=reason is None,reason=reason,rows=len(d),periods=int(d.date.nunique()) if len(d) else 0,quality=quality)
    return result

def drift(c,s):
    d=panel(c,s);dates=sorted(d.date.unique()) if len(d) else []
    if len(dates)<35:return dict(status='collecting',closed_days=len(dates),required_days=35,signals=[],note='Needs 28 reference and 7 recent closed days. This detects distribution changes; forecast accuracy requires held-out predictions.')
    last=dates[-35:]
    if (date.fromisoformat(last[-1])-date.fromisoformat(last[0])).days!=34:return dict(status='incomplete',closed_days=len(dates),signals=[],note='The last 35 observed days are not consecutive. Close missing tracked days; missing history is not zero demand.')
    ref=d[d.date.isin(last[:28])];recent=d[d.date.isin(last[28:])]
    before=ref.groupby('date').units.sum().mean();after=recent.groupby('date').units.sum().mean();change=float((after-before)/before) if before>0 else None
    ids=sorted(set(ref.product_id)|set(recent.product_id));a=ref.groupby('product_id').units.sum().reindex(ids,fill_value=0).to_numpy(float)+.5;b=recent.groupby('product_id').units.sum().reindex(ids,fill_value=0).to_numpy(float)+.5;a/=a.sum();b/=b.sum();psi=float(np.sum((b-a)*np.log(b/a)))
    ref_discount=float((1-ref.price/ref.base_price).mean());new_discount=float((1-recent.price/recent.base_price).mean());stockout=float(recent.stockout.mean());signals=[]
    sufficient=ref.units.sum()>=100 and recent.units.sum()>=25
    if sufficient and change is not None and abs(change)>.3:signals.append('Average daily unit sales changed by more than 30%.')
    if sufficient and psi>.2:signals.append('Product sales mix shifted (PSI above 0.20).')
    if sufficient and abs(new_discount-ref_discount)>.1:signals.append('Average realized discount changed by more than 10 percentage points.')
    if stockout>.2:signals.append('Over 20% of recent product-days show possible stockout censoring.')
    return dict(status='drift_detected' if signals else 'stable' if sufficient else 'low_volume',closed_days=len(dates),reference_start=last[0],reference_end=last[27],recent_start=last[28],recent_end=last[-1],unit_change=change,product_mix_psi=psi,discount_change=new_discount-ref_discount,stockout_share=stockout,signals=signals,note='Distribution alerts can reflect seasonality, promotions or stockouts. They do not prove model error or causal change. Candidates still require out-of-time validation.')

def queue_job(c,s,kind):
    if kind not in ['demand','promotion']:raise ValueError('Choose demand or promotion training.')
    retail_shop=c.db.retail_shops.find_one({'pos_store_id':s['id']},session=c.session)
    if retail_shop:
        from .retail.repository import now as queued_at
        pending=c.db.retail_ledger_jobs.find_one({'workspace':retail_shop['id'],'kind':kind,'status':{'$in':['queued','processing']}},session=c.session)
        recent=c.db.retail_runs.find_one({'workspace':retail_shop['id'],'kind':kind,'status':{'$in':['queued','running']}},session=c.session)
        if recent or pending:
            r=recent or pending
            return dict(id=r['id'],status='queued',source_id=retail_shop['id'],kind=kind)
        ready=readiness(c,s)[kind]
        if not ready['ready']:raise ValueError(ready['reason'])
        id=uid()
        # Transactional outbox: no worker can read a snapshot until day-close commits.
        c.db.retail_ledger_jobs.insert_one(dict(_id=id,id=id,workspace=retail_shop['id'],pos_store_id=s['id'],kind=kind,status='queued',through=local_day(s),created=queued_at()),session=c.session)
        audit_event(c,s['id'],'model_candidate_requested',id,dict(kind=kind,workspace=retail_shop['id']))
        return dict(id=id,status='queued',source_id=retail_shop['id'],kind=kind)
    existing=c.run('pos_learning_queue_job_aggregate_pos_model_jobs', (s['id'], kind)).fetchone()
    if existing:return dict(existing)
    d=panel(c,s,kind);d,quality=(validate_demand_panel if kind=='demand' else validate_panel)(d)
    id=uid();source=('demand_' if kind=='demand' else '')+uid();p=service().DATA/('demand' if kind=='demand' else 'uploads')/source;p.mkdir(parents=True)
    quality.update(source='OneSales closed-day ledger',scope='Demo operations' if s['demo'] else 'Client observed sales',demo=bool(s['demo']),pos_store_id=s['id'],model_store_id=s['forecast_store_id'] or s['id'],currency='INR',country='IN')
    write_frame(d, p / 'panel.parquet', index=False);(p/'quality.json').write_text(json.dumps(quality,indent=2))
    if kind=='demand':c.run('pos_learning_queue_job_insert_demand_datasets', (source, s['name'] + ' · OneSales', json.dumps(quality), now()))
    else:c.run('pos_learning_queue_job_insert_datasets', (source, s['name'] + ' · OneSales', json.dumps(quality), 'validated', None, now()))
    c.run('pos_learning_queue_job_insert_pos_model_jobs', (id, s['id'], kind, 'queued', source, None, now(), now()))
    audit_event(c,s['id'],'model_candidate_queued',id,dict(kind=kind,source_id=source))
    return dict(id=id,status='queued',source_id=source,kind=kind)

def run_job(id,lease_held=False):
    s=service()
    with (nullcontext() if lease_held else s.training_lock):
        with s.connection() as c:
            row=c.run('pos_learning_run_job_aggregate_pos_model_jobs', (id,)).fetchone()
            if not row or row['status']!='queued':return
            job=dict(row);c.run('pos_learning_run_job_update_pos_model_jobs', (now(), id))
        try:
            if job['kind']=='demand':
                from .demand import train_demand
                from .demand_api import paths,engine
                p,a=paths(job['source_id']);train_demand(p,a)
                with s.connection() as c:c.run('pos_learning_run_job_insert_demand_jobs', (job['source_id'], 'ready', None, now()))
                engine.cache_clear()
            else:
                from .model import train
                p,a=s.workspace_path(job['source_id']);train(p/'panel.parquet',a)
                with s.connection() as c:c.run('pos_learning_run_job_update_datasets', (job['source_id'],))
                s.get_engine.cache_clear()
            with s.connection() as c:c.run('pos_learning_run_job_update_pos_model_jobs_2', (now(), id))
        except Exception as exc:
            logging.exception('OneSales candidate training failed')
            with s.connection() as c:c.run('pos_learning_run_job_update_pos_model_jobs_3', (str(exc)[:500], now(), id))

def launch(id):
    if os.environ.get('LEGACY_TRAINING_WORKER')=='external':return
    from .pg import database
    if not database().retail_runs.find_one({'_id':id}) and not database().retail_ledger_jobs.find_one({'_id':id}):threading.Thread(target=run_job,args=(id,),daemon=True).start()
def recover():
    s=service()
    from .pg import database
    db=database()
    if not any(db[name].find_one(query,{'_id':1}) for name,query in [
        ('datasets',{'status':'training'}),('demand_jobs',{'status':'training'}),('pos_model_jobs',{'status':{'$in':['training','queued']}})]):return
    # A second API process must never mark another worker's running fit failed.
    if not s.training_lock.acquire(blocking=False):return
    try:
        with s.connection() as c:
            c.begin()
            c.db.datasets.update_many({'status':'training'},{'$set':{'status':'failed','error':'The previous worker lease expired; retry this snapshot.'}},session=c.session)
            c.db.demand_jobs.update_many({'status':'training'},{'$set':{'status':'failed','error':'The previous worker lease expired; retry this snapshot.'}},session=c.session)
            c.db.pos_model_jobs.update_many({'status':'training'},{'$set':{'status':'failed','error':'The previous worker lease expired; queue a fresh candidate.','updated':now()}},session=c.session)
            queued=[r['id'] for r in c.db.pos_model_jobs.find({'status':'queued'},session=c.session)]
    finally:s.training_lock.release()
    for id in queued:launch(id)

class CloseDay(StrictModel):
    business_date:str|None=None
class LearningSettings(StrictModel):auto_retrain:bool=False
class TrainInput(StrictModel):kind:str='demand'
class HolidayInput(StrictModel):
    date:str
    name:str=Field(min_length=2,max_length=100)

@router.get('/stores/{store_id}/learning')
def status(store_id:str,authorization:str=Header(default='')):
    with operation(store_id,authorization,True) as (c,s,staff):
        setting=c.run('pos_learning_status_aggregate_pos_learning_settings', (store_id,)).fetchone()
        retail_shop=c.db.retail_shops.find_one({'pos_store_id':store_id},session=c.session)
        retail_jobs=[dict(id=r['id'],kind=r['kind'],created=r['created'],source_id=retail_shop['id'],status='training' if r['status']=='running' else 'ready' if r['status']=='candidate' else r['status'],error=r.get('error')) for r in c.db.retail_runs.find({'workspace':retail_shop['id']},session=c.session).sort('created',-1).limit(50)] if retail_shop else []
        if retail_shop:
            retail_jobs+=[dict(id=r['id'],kind=r['kind'],created=r['created'],source_id=retail_shop['id'],status='queued' if r['status']=='queued' else 'training' if r['status']=='processing' else 'failed',error=r.get('error')) for r in c.db.retail_ledger_jobs.find({'workspace':retail_shop['id'],'status':{'$ne':'complete'}},session=c.session).sort('created',-1).limit(50)]
        return dict(readiness=readiness(c,s),drift=drift(c,s),auto_retrain=bool(setting and setting[0]),jobs=retail_jobs+[dict(r) for r in c.run('pos_learning_status_aggregate_pos_model_jobs', (store_id,))],holidays=[dict(r) for r in c.run('pos_learning_status_aggregate_pos_holidays', (store_id,))],days=[dict(r) for r in c.run('pos_learning_status_aggregate_pos_days', (store_id,))])

@router.post('/stores/{store_id}/learning/settings')
def settings(store_id:str,req:LearningSettings,authorization:str=Header(default='')):
    with operation(store_id,authorization,True,True) as (c,s,staff):
        c.run('pos_learning_settings_insert_pos_learning_settings', (store_id, int(req.auto_retrain)));audit_event(c,store_id,'learning_settings',store_id,req.model_dump());return req

@router.post('/stores/{store_id}/holidays')
def holiday(store_id:str,req:HolidayInput,authorization:str=Header(default='')):
    dt=date.fromisoformat(req.date).isoformat()
    with operation(store_id,authorization,True,True) as (c,s,staff):
        c.run('pos_learning_holiday_insert_pos_holidays', (store_id, dt, req.name))
        c.run('pos_learning_holiday_update_pos_stores', (store_id,))
        if s['forecast_workspace']:
            from .promotions import advance
            advance(c,s['forecast_workspace'])
        for row in c.run('pos_learning_holiday_aggregate_pos_daily', (store_id, dt)).fetchall():
            body=json.loads(row['body']);body['holiday']=1;c.run('pos_learning_holiday_update_pos_daily', (json.dumps(body), store_id, row['product_id'], dt))
        audit_event(c,store_id,'local_holiday',dt,req.model_dump());return req

@router.post('/stores/{store_id}/days/close')
def close_day(store_id:str,req:CloseDay,authorization:str=Header(default='')):
    jobs=[]
    with operation(store_id,authorization,True,True) as (c,s,staff):
        day=date.fromisoformat(req.business_date or local_day(s)).isoformat()
        if day>local_day(s) or day<s['created'][:10]:raise ValueError('Close an observed trading date between store setup and today.')
        if c.run('pos_learning_close_day_aggregate_pos_days', (store_id, day)).fetchone():return dict(business_date=day,replayed=True)
        if c.run('pos_learning_close_day_aggregate_pos_shifts', (store_id, day)).fetchone():raise HTTPException(409,'Close and reconcile all register shifts for this day first.')
        rows=daily_snapshot(c,s,day)
        if not rows:raise ValueError('No tracked product inventory exists for this day.')
        c.run('pos_learning_close_day_insert_pos_days', (store_id, day, now(), staff['id']));check=drift(c,s);seq=c.run('pos_learning_close_day_aggregate_pos_events', (store_id,)).fetchone()[0]
        c.run('pos_learning_close_day_insert_pos_drift_checks', (uid(), store_id, now(), seq, json.dumps(check)))
        # Committed day-close invalidates the shared OneSales adapter. The
        # connected worker reads the ledger after this transaction commits.
        if s.get('forecast_workspace'):
            c.db.connect_sources.update_many({'workspace':s['forecast_workspace'],'kind':'onesales','object_type':'SalesObservation'},
                {'$set':{'native_change':uid()}},session=c.session)
        setting=c.run('pos_learning_status_aggregate_pos_learning_settings', (store_id,)).fetchone()
        if setting and setting[0] and check['status']=='drift_detected':
            for kind,ready in readiness(c,s).items():
                recent=c.run('pos_learning_close_day_aggregate_pos_model_jobs', (store_id, kind, (date.fromisoformat(local_day(s)) - timedelta(days=7)).isoformat())).fetchone()
                retail_shop=c.db.retail_shops.find_one({'pos_store_id':store_id},session=c.session)
                if retail_shop:
                    recent=recent or c.db.retail_runs.find_one({'workspace':retail_shop['id'],'kind':kind,'created':{'$gte':datetime.now(timezone.utc)-timedelta(days=7)}},session=c.session)
                if ready['ready'] and not recent:jobs.append(queue_job(c,s,kind))
        audit_event(c,store_id,'trading_day_closed',day,dict(rows=len(rows),drift=check['status']))
    for job in jobs:launch(job['id'])
    return dict(business_date=day,rows=len(rows),drift=check,jobs=jobs,replayed=False)

@router.post('/stores/{store_id}/learning/train')
def train(store_id:str,req:TrainInput,authorization:str=Header(default='')):
    with operation(store_id,authorization,True,True) as (c,s,staff):job=queue_job(c,s,req.kind)
    launch(job['id']);return job

@router.get('/stores/{store_id}/learning/export/{kind}')
def export(store_id:str,kind:str,authorization:str=Header(default='')):
    if kind not in ['demand','promotion']:raise HTTPException(404,'Unknown training export.')
    with operation(store_id,authorization,True) as (c,s,staff):return csv_response(panel(c,s,kind).to_dict('records'),FIELDS,'aislemint-onesales-'+kind+'.csv')
