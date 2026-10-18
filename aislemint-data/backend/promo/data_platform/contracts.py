"""Consumer contracts and deterministic fan-in. No model or connector dependency."""
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from ..retail.contracts import contract as sales_contract, normalize, MAX_ROWS

VERSION = 'retail-data-product-v1'
DOMAINS = {'sales': 'SalesObservation', 'catalog': 'Product', 'inventory': 'InventoryPosition'}

class Subscription(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(default=0, ge=0)
    sales: list[str] = Field(default_factory=list, max_length=20)
    catalog: list[str] = Field(default_factory=list, max_length=20)
    inventory: list[str] = Field(default_factory=list, max_length=20)
    frequency: Literal['daily', 'weekly'] = 'daily'
    sales_overlap: Literal['reject', 'disjoint_channels'] = 'reject'
    auto_publish: bool = True
    auto_train: bool = False
    profile: Literal['efficient', 'comparison'] = 'efficient'
    @model_validator(mode='after')
    def unique_sources(self):
        ids = self.sales + self.catalog + self.inventory
        if not ids: raise ValueError('Select at least one data source.')
        if len(set(ids)) != len(ids): raise ValueError('Select each source once.')
        if self.sales_overlap == 'disjoint_channels' and len(self.sales) < 2:
            raise ValueError('Channel aggregation requires at least two sales sources.')
        return self

def registry():
    return dict(version=VERSION, sales=sales_contract(), domains=[
        dict(id='sales', object_type='SalesObservation', grain=['store_id','product_id','date'], consumers=['Business analytics','Demand forecasting','Promotion forecasting','Promotion review','Recommendations'],
             semantics='Completed periods, gross units, ex-tax prices, refunds separate. IDs must match across sources. Missing observations are never zeros.'),
        dict(id='catalog', object_type='Product', grain=['external_id'], consumers=['Product catalogue','Business analytics','Ontology'],
             semantics='One authoritative product identity per shop. Historical sale prices and categories remain as observed; catalogue edits do not rewrite history.'),
        dict(id='inventory', object_type='InventoryPosition', grain=['product_id','captured_at'], consumers=['Inventory planner','Business analytics','Ontology'],
             semantics='End-of-day stock with a timezone, nonnegative on_hand and positive unit_cost in shop currency. Forecast-origin dates must match. Optional lead_time_days, review_period_days, case_pack, reserved and backlog.'),
    ], policies=[
        'Default: overlapping product/date sales keys fail publication, even when values match.',
        'Disjoint channels is an explicit assertion that sources contain different sales. Every channel must cover the same product/date keys, including observed zero-sale periods.',
        'All selected sources must finish without quarantined rows. Publication does not fill missing records or delete upstream objects.',
        'Daily promotion snapshots aggregate only complete Monday–Sunday weeks. Partial weeks are excluded and counted.',
        'Shared sources are published to all authorized shop members. Owner-only sources cannot power shared models or analytics.',
        'Automatic training queues candidates; activation requires the existing quality gates and shop-owner review.',
    ], max_rows=MAX_ROWS)

def grain(r): return (r['store_id'], r['product_id'], r['date'])

def merge_sales(objects, config, shop):
    """Never infer whether two POS/ERP records describe the same underlying sale."""
    by_source = {s: {} for s in config['sales']}
    for obj in objects:
        row = obj['properties']; key = grain(row); rows = by_source[obj['source_id']]
        if key in rows: raise ValueError(f'Duplicate sales key within one source: {key}. Correct its stable record IDs.')
        rows[key] = row
    if any(not rows for rows in by_source.values()): raise ValueError('Every selected sales source must contain observed history.')
    all_rows = []
    if config['sales_overlap'] == 'reject':
        seen = set()
        for rows in by_source.values():
            if seen.intersection(rows): raise ValueError('Overlapping product/date sales keys across sources. Select one authority or explicitly declare disjoint sales channels.')
            seen.update(rows); all_rows.extend(rows.values())
    else:
        keysets = [set(rows) for rows in by_source.values()]
        if any(keys != keysets[0] for keys in keysets[1:]):
            raise ValueError('Disjoint channels need identical product/date coverage. Supply observed zero-sale periods; missing channel data is not zero.')
        for key in sorted(keysets[0]):
            rows = [source[key] for source in by_source.values()]
            for field in ('name','category','base_price','country','currency'):
                if any(r[field] != rows[0][field] for r in rows[1:]): raise ValueError(f'Channel reference data disagrees for {field} at {key}. Reconcile before combining.')
            result = dict(rows[0]); units = sum(r['units'] for r in rows)
            revenue = sum(r['units'] * r['price'] for r in rows)
            result.update(units=units, price=revenue/units if units else min(r['price'] for r in rows))
            for field in ('feature','display','promotion_available','free_promotion'):
                result[field] = max(r.get(field,0) for r in rows)
            for field in ('stockout','holiday','unit_cost','SPEND','returned_units','refund_ex_gst'):
                present = [field in r for r in rows]
                if any(present) and not all(present): raise ValueError(f'{field} must be consistently supplied across channels at {key}.')
                if not all(present): continue
                if field in ('stockout','holiday'): result[field] = max(r[field] for r in rows)
                elif field == 'unit_cost': result[field] = sum(r[field]*r['units'] for r in rows)/units if units else max(r[field] for r in rows)
                else: result[field] = sum(r[field] for r in rows)
            all_rows.append(result)
    if len(all_rows) > MAX_ROWS: raise ValueError(f'Partition history to at most {MAX_ROWS:,} canonical observations for this worker.')
    normalized, errors = normalize(all_rows, shop)
    if errors: raise ValueError(errors[0]['message'])
    return sorted(normalized, key=grain)

def weekly_sales(records):
    groups = defaultdict(list)
    for row in records:
        d = date.fromisoformat(row['date']); end = d + timedelta(days=6-d.weekday())
        groups[(row['store_id'],row['product_id'],end)].append(row)
    weekly = []; omitted = 0
    for (_, _, end), rows in sorted(groups.items()):
        expected = {(end-timedelta(days=i)).isoformat() for i in range(7)}
        if len(rows)!=7 or {r['date'] for r in rows} != expected:
            omitted += len(rows); continue
        rows.sort(key=lambda r:r['date']); result=dict(rows[-1]); units=sum(r['units'] for r in rows)
        result.update(date=end.isoformat(), units=units,
            price=sum(r['units']*r['price'] for r in rows)/units if units else sum(r['price'] for r in rows)/7,
            base_price=sum(r['units']*r['base_price'] for r in rows)/units if units else sum(r['base_price'] for r in rows)/7)
        for field in ('feature','display','promotion_available','free_promotion','stockout','holiday'):
            if any(field in r for r in rows):
                if not all(field in r for r in rows): raise ValueError(f'{field} is incomplete within a week.')
                result[field]=max(r[field] for r in rows)
        for field in ('unit_cost','SPEND','returned_units','refund_ex_gst'):
            if not any(field in r for r in rows): continue
            if not all(field in r for r in rows): raise ValueError(f'{field} is incomplete within a week.')
            result[field]=(sum(r[field]*r['units'] for r in rows)/units if units else sum(r[field] for r in rows)/7) if field=='unit_cost' else sum(r[field] for r in rows)
        weekly.append(result)
    return weekly, omitted

def analytics(records, catalog, inventory, currency):
    daily=defaultdict(list); products=defaultdict(list); categories=defaultdict(list)
    for r in records:
        daily[r['date']].append(r); products[r['product_id']].append(r); categories[r['category']].append(r)
    def totals(rows):
        gross=sum(r['units']*r['price'] for r in rows)
        refunds_known=all('refund_ex_gst' in r for r in rows)
        costs_known=bool(rows) and all('unit_cost' in r for r in rows)
        returns_known=all('returned_units' in r for r in rows)
        refunds=sum(r.get('refund_ex_gst',0) for r in rows) if refunds_known else None
        return dict(observations=len(rows),gross_units=sum(r['units'] for r in rows), gross_revenue=gross,
            refunds=refunds,net_revenue=gross-refunds if refunds is not None else None,
            returned_units=sum(r['returned_units'] for r in rows) if returns_known else None,
            gross_cogs=sum(r['units']*r['unit_cost'] for r in rows) if costs_known else None,
            promotional_revenue=sum(r['units']*r['price'] for r in rows if r['promotion_available']),
            stockout_observations=sum(r.get('stockout',0) for r in rows),
            stockout_coverage=sum('stockout' in r for r in rows))
    latest={}
    for row in inventory:
        p=row['properties']; previous=latest.get(p['product_id'])
        if not previous or datetime.fromisoformat(p['captured_at'].replace('Z','+00:00'))>datetime.fromisoformat(previous['captured_at'].replace('Z','+00:00')): latest[p['product_id']]=p
    return dict(currency=currency, totals=totals(records),
        date_start=min(daily) if daily else None, date_end=max(daily) if daily else None,
        products=len(products), catalogue_products=len(catalog),
        daily=[dict(date=d,**totals(rows)) for d,rows in sorted(daily.items())],
        product_metrics=[dict(product_id=p,name=rows[-1]['name'],category=rows[-1]['category'],**totals(rows)) for p,rows in sorted(products.items())],
        categories=[dict(category=p,**totals(rows)) for p,rows in sorted(categories.items())],
        inventory=dict(products=len(latest),on_hand=sum(r['on_hand'] for r in latest.values()),
            known_value=sum(r['on_hand']*r['unit_cost'] for r in latest.values() if r.get('unit_cost') is not None),
            cost_coverage=sum(r.get('unit_cost') is not None for r in latest.values()),
            low_stock=sum(r['on_hand']<=r['reorder_point'] for r in latest.values() if r.get('reorder_point') is not None)),
        limitations=['Sales aggregates do not provide transaction counts, payment methods, GST, customer identities or return disposition. Open OneSales ledger analytics for these.',
            'Gross COGS is cost of sold units before returns. Net profit needs actual return/disposal costs and operating expenses.',
            'Zero stockout flags do not prove unconstrained demand; lost sales require availability evidence.'])
