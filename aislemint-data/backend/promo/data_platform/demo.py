"""Reproducible, labelled multi-channel history for the existing reserved demo shop.

Public product labels come from the connected demo's frozen source snapshots.
Trading figures are entirely synthetic and never enter the OneSales ledger.
"""
from datetime import date, datetime, timedelta
import math
import time
from zoneinfo import ZoneInfo
from ..pg import database
from ..retail import auth, modelling
from ..retail.repository import required_shop, now
from ..connected import demo as connected_demo, pipeline
from ..connected.repository import digest
from . import service
from .contracts import Subscription, weekly_sales

VERSION='shared-data-demo-v1'

def history(products, end, channel):
    rows=[]; start=end-timedelta(days=799)
    for j,p in enumerate(products):
        for i in range(800):
            day=start+timedelta(days=i); week=(day.toordinal()//7); annual=math.sin(2*math.pi*day.timetuple().tm_yday/365.25)
            promo=int(j%2==0 and week%12 in (7,8)); holiday=int((day.month,day.day) in ((1,26),(8,15),(10,2),(12,25)))
            noise=(int(digest([p['external_id'],str(day),channel])[:8],16)%1000/1000-.5)*5
            base=float(p['properties'].get('price') or 100)
            demand=(35+j*3)*(1+.18*annual)*(1.25 if day.weekday()>=5 else 1)*(1+.5*promo+.25*holiday)+noise
            units=float(max(1,round(demand*(.7 if channel=='store' else .3))))
            price=round(base*(.85 if promo else 1),2); cost=round(base*.58,2)
            returned=float(1 if i%41==j else 0)
            rows.append(dict(id=f'{channel}:{p["external_id"]}:{day}',date=str(day),store_id=connected_demo.STORE_CODE,
                product_id=p['external_id'],name=p['properties']['name'],category=p['properties'].get('category','Grocery'),
                units=units,price=price,base_price=base,feature=0,display=promo,promotion_available=promo,stockout=0,holiday=holiday,
                unit_cost=cost,SPEND=round(units*price,2),returned_units=returned,refund_ex_gst=round(returned*price,2),free_promotion=0,country='IN',currency='INR'))
    return rows

def ingest(source,rows,progress):
    # Batch writes exercise the real connector contracts, checksums and provenance.
    for offset in range(0,len(rows),500):
        batch=rows[offset:offset+500];run=pipeline.queue(source,'shared-demo-'+digest(batch)[:40],connected_demo.ACTOR,batch)
        deadline=time.monotonic()+240
        while True:
            current=database().connect_runs.find_one({'_id':run['id'],'workspace':connected_demo.WORKSPACE})
            if current['status']=='complete':break
            if current['status'] in ('failed','complete_with_issues'):raise ValueError(f'{source["name"]}: inspect the failed or quarantined demo batch in Pipelines.')
            if time.monotonic()>deadline:raise TimeoutError('Demo ingestion is still running. Rerun to resume.')
            if not pipeline.process_one(connected_demo.WORKSPACE):time.sleep(.2)
        progress(f'{source["name"]}: {offset+len(batch):,}/{len(rows):,} observations')

def provision(progress=print,compact=False):
    from ..retail.repository import initialize
    initialize();db=database();connected_demo.ensure_workspace();shop=required_shop(connected_demo.WORKSPACE)
    catalog=db.connect_sources.find_one({'workspace':shop['id'],'demo_package':connected_demo.VERSION,'object_type':'Product'})
    if not catalog:raise ValueError('Run scripts/demo_connected.py first to populate the attributed public product catalogue.')
    all_products=list(db.connect_objects.find({'workspace':shop['id'],'source_id':catalog['id']}).sort('external_id',1))
    count=5 if compact else 6
    products=[r for r in all_products if r['external_id'].startswith('DJ-')][:count]+[r for r in all_products if r['external_id'].startswith('OFF-')][:count]
    if len(products)!=count*2:raise ValueError('This demo needs product labels from both public catalogues.')
    frequency='weekly' if compact else 'daily'
    if shop.get('data_demo') and shop['data_demo'].get('frequency','daily')!=frequency:raise ValueError('This demo already uses a different cadence. Keep its original cadence to preserve source identities.')
    end=date.fromisoformat(shop['data_demo']['through']) if shop.get('data_demo') else now().astimezone(ZoneInfo('Asia/Kolkata')).date()-timedelta(days=1)
    if compact:end-=timedelta(days=(end.weekday()+1)%7)
    token=auth.principal.set({'id':connected_demo.ACTOR,'admin':False,'memberships':{connected_demo.RETAILER:'owner'}})
    try:
        db.retail_shops.update_one({'_id':shop['id'],'demo_package':connected_demo.VERSION},{'$set':{'data_demo':dict(version=VERSION,through=str(end),frequency=frequency,products=len(products),channels=2,synthetic=True)}})
        feeds=[]
        for channel,name in [('store',f'Store POS channel · Simulated {frequency} sales'),('online',f'Online channel · Simulated {frequency} sales')]:
            source=connected_demo.ensure_source(VERSION+'-'+channel+('-weekly' if compact else ''),name,'SalesObservation',[])
            rows=history(products,end,channel)
            if compact:
                rows,_=weekly_sales([r for r in rows if r['date']>=(end-timedelta(days=629)).isoformat()])
                rows=[{**r,'id':f'{channel}-weekly:{r["product_id"]}:{r["date"]}'} for r in rows]
            ingest(source,rows,progress);feeds.append(source['id'])
        stock=connected_demo.ensure_source(VERSION+'-stock','Planning stock · Simulated end-of-day inventory','InventoryPosition',[])
        # Retain two dated stock snapshots so both daily demand and complete-week
        # origins can be demonstrated without inventing a stock-date conversion.
        dates={end,end-timedelta(days=(end.weekday()+1)%7)}
        inventory=[dict(id=f'planning:{p["external_id"]}:{day}',product_id=p['external_id'],on_hand=float(24+j*18),reorder_point=50.,
            captured_at=datetime.combine(day,datetime.min.time()).replace(hour=23,minute=59,tzinfo=ZoneInfo(shop['timezone'])).isoformat(),
            unit_cost=round(float(p['properties'].get('price') or 100)*.58,2),currency='INR',lead_time_days=4,review_period_days=7,case_pack=6)
            for day in sorted(dates) for j,p in enumerate(products)]
        ingest(stock,inventory,progress)
        config=Subscription(sales=feeds,catalog=[catalog['id']],inventory=[stock['id']],frequency=frequency,sales_overlap='disjoint_channels',auto_publish=True,auto_train=True,profile='efficient')
        old=db.data_products.find_one({'_id':shop['id']})
        if old:
            expected={**config.model_dump(),'revision':old['revision']}
            if old['config']!=expected:raise ValueError('Demo data routing was edited. Preserve it; review routing in the app instead of overwriting it.')
        else:service.configure(shop['id'],config,connected_demo.ACTOR)
        job=service.request_publish(shop['id'],connected_demo.ACTOR)
        deadline=time.monotonic()+240
        while True:
            current=db.data_jobs.find_one({'_id':job['id']})
            if current['status']=='complete':break
            if current['status'] in ('failed','superseded'):raise ValueError(current.get('error','A newer source generation superseded the demo publication. Rerun to publish it.'))
            if time.monotonic()>deadline:raise TimeoutError('Demo publication is running. Rerun to resume.')
            if not service.process_one(shop['id']):time.sleep(.2)
        release=service.active(shop['id'])
        return dict(workspace=shop['id'],release_id=release['id'],history_through=str(end),observations=release['counts']['canonical_sales'],
            consumers={k:dict(snapshot_id=v['snapshot_id'],status=v['status'],run_id=v.get('run_id'),issues=v['report']['issues']) for k,v in release['consumers'].items()})
    finally:auth.principal.reset(token)
