"""Reproducible public-data demonstration, confined to a marked demo retailer.

Invoked only by the trusted operator CLI. No registration endpoint, new login,
customer membership changes, external writeback or model activation is created.
Public responses and simulation manifests live in MongoDB, never local files.
"""
from datetime import date, datetime, timedelta, timezone
import json
import math
import time
from uuid import NAMESPACE_URL, uuid5

from ..pg import database, connection
from ..retail import auth
from ..retail.repository import now, audit
from . import api, pipeline
from .connectors import request_data
from .repository import digest, object_id, object_type
from .schema import ObjectType, SourceInput, map_record, prop

VERSION = 'connected-retail-demo-v1'
RETAILER = uuid5(NAMESPACE_URL, 'hindsight/demo/connected/retailer').hex
WORKSPACE = uuid5(NAMESPACE_URL, 'hindsight/demo/connected/bengaluru').hex
STORE_CODE = 'DEMO-BLR-01'
ACTOR = 'system:connected-demo'
PUBLIC = {
    'open_food_facts': {
        'name': 'Open Food Facts',
        'url': 'https://world.openfoodfacts.org/api/v2/search?countries_tags=en:india&page_size=18&fields=code,product_name,brands,categories',
        'reference': 'https://world.openfoodfacts.org',
        'license': 'Open Database License (ODbL); individual contents: DbCL',
        'license_url': 'https://opendatacommons.org/licenses/odbl/1-0/',
        'kind': 'Public community product labels; not verified shop stock or prices',
    },
    'dummyjson': {
        'name': 'DummyJSON',
        'url': 'https://dummyjson.com/products/category/groceries?limit=24&select=id,title,category,sku',
        'reference': 'https://dummyjson.com/docs/products',
        'license': 'Public fictional API test data; see provider documentation',
        'license_url': 'https://dummyjson.com/docs/products',
        'kind': 'Public fictional grocery catalogue',
    },
    'nasa_power': {
        'name': 'NASA POWER',
        'reference': 'https://power.larc.nasa.gov/',
        'license': 'NASA POWER open meteorological data; attribution retained',
        'license_url': 'https://power.larc.nasa.gov/docs/referencing/',
        'kind': 'Historical gridded meteorological estimates; not a delivery forecast',
    },
}


def fetch_public(as_of):
    """Small, fixed HTTPS requests; callers freeze results before publication."""
    packets = {}
    for key, metadata in PUBLIC.items():
        metadata = dict(metadata)
        if key == 'nasa_power':
            end = as_of - timedelta(days=7)
            start = end - timedelta(days=6)
            metadata['url'] = ('https://power.larc.nasa.gov/api/temporal/daily/point?parameters=T2M,PRECTOTCORR'
                '&community=RE&longitude=77.5946&latitude=12.9716'
                f'&start={start:%Y%m%d}&end={end:%Y%m%d}&format=JSON&time-standard=UTC')
        raw = request_data(metadata['url'], {'User-Agent': 'AisleMint-Connected-Demo/1.0 (public retail data evaluation)'})
        payload = json.loads(raw)
        packets[key] = {**metadata, 'fetched_at': now().isoformat(), 'payload': payload, 'sha256': digest(payload)}
    return packets


def text(value, fallback='', maximum=180):
    return ' '.join(str(value or fallback).split())[:maximum]


def definitions():
    return [
        ObjectType(name='Warehouse', description='Fictional receiving locations in the demo network.', properties=[
            prop('name', required=True), prop('city'), prop('latitude', 'number'), prop('longitude', 'number'), prop('data_kind')]),
        ObjectType(name='CatalogReference', description='Public catalogue facts linked to simulated shop SKUs; provider provenance is retained.', properties=[
            prop('name', required=True), prop('product_id', reference='Product', required=True), prop('provider'),
            prop('brand'), prop('source_url'), prop('data_kind'), prop('license'), prop('fetched_at', 'datetime')]),
        ObjectType(name='WeatherObservation', description='NASA POWER daily estimates at the demo location; context only, no inferred sales or delivery causation.', properties=[
            prop('name', required=True), prop('warehouse_id', reference='Warehouse'), prop('date', 'date', True),
            prop('temperature_c', 'number'), prop('precipitation_mm', 'number'), prop('provider'), prop('source_url'), prop('data_kind')]),
        ObjectType(name='DeliveryRoute', description='Synthetic links between shipments, receiving locations and historical weather context.', properties=[
            prop('name', required=True), prop('shipment_id', reference='Shipment'), prop('warehouse_id', reference='Warehouse'),
            prop('weather_id', reference='WeatherObservation'), prop('distance_km', 'number'), prop('data_kind')]),
    ]


def build_scenario(packets, as_of):
    """Pure, deterministic fixture generation. Missing public data never becomes fake data."""
    captured = datetime.combine(as_of, datetime.min.time(), tzinfo=timezone.utc).isoformat()
    products, reference_groups = [], {'open_food_facts': [], 'dummyjson': []}
    seen = set()
    for provider in reference_groups:
        packet = packets[provider]
        for raw in packet['payload'].get('products', []):
            source_id = str(raw.get('code') if provider == 'open_food_facts' else raw.get('id', ''))
            name = text(raw.get('product_name') if provider == 'open_food_facts' else raw.get('title'))
            if not name or not source_id or source_id == 'None':
                continue
            sku = ('OFF-' if provider == 'open_food_facts' else 'DJ-') + source_id
            if sku in seen:
                continue
            seen.add(sku)
            index = len(products)
            # INR values are invented demonstration assumptions, not FX conversions.
            products.append({'id': sku, 'master': {'label': name, 'sku': sku,
                'category': text(raw.get('categories'), 'Groceries', 120) if provider == 'open_food_facts' else 'Fresh & everyday groceries',
                'price_paise': (25 + (index * 17) % 375) * 100, 'supplier_code': f'SUP-{index % 6 + 1:02d}'}})
            reference_groups[provider].append(dict(id=sku, name=name, product_id=sku, provider=packet['name'],
                brand=text(raw.get('brands'), 'Not supplied'),
                source_url=f'https://world.openfoodfacts.org/product/{source_id}' if provider == 'open_food_facts' else f'https://dummyjson.com/products/{source_id}',
                data_kind=packet['kind'], license=packet['license'], fetched_at=packet['fetched_at'], original=raw))
        if not reference_groups[provider]:
            raise ValueError(f'No usable {provider} catalogue records; no substitute data was invented.')
    if len(products) < 12:
        raise ValueError('The demonstration requires at least twelve public catalogue items.')

    packet = packets['nasa_power']
    payload = packet['payload']
    units = payload.get('parameters', {})
    if units.get('T2M', {}).get('units') != 'C' or units.get('PRECTOTCORR', {}).get('units') != 'mm/day':
        raise ValueError('Unexpected NASA units; review the upstream schema before mapping.')
    parameters = payload.get('properties', {}).get('parameter', {})
    weather = []
    missing = payload.get('header', {}).get('fill_value', -999)
    for day, temperature in sorted(parameters.get('T2M', {}).items()):
        rain = parameters.get('PRECTOTCORR', {}).get(day)
        if not all(isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v) and v != missing for v in (temperature, rain)):
            continue
        day = datetime.strptime(day, '%Y%m%d').date().isoformat()
        weather.append(dict(id='NASA-BLR-' + day, name='Bengaluru weather · ' + day, warehouse_id='WH-BLR-01',
            date=day, temperature_c=temperature, precipitation_mm=rain, provider='NASA POWER Daily ' + text(payload.get('header', {}).get('api', {}).get('version')),
            source_url=packet['url'], data_kind='Historical gridded estimate (UTC day); context only'))
    if not weather:
        raise ValueError('NASA returned no usable days. Missing weather is not zero rainfall.')

    suppliers = [dict(id=f'SUP-{i+1:02d}', name='Demo · ' + name, status='active', lead_time_days=2 + i)
        for i, name in enumerate(['Bengaluru Pantry Distribution', 'Garden City Fresh Foods', 'Everyday Essentials Supply',
                                  'Southside Grocery Wholesale', 'Morning Basket Logistics', 'Neighbourhood Food Partners'])]
    inventory, earlier_inventory, orders, shipments, routes, documents = [], [], [], [], [], []
    for i, p in enumerate(products):
        sku, name = p['id'], p['master']['label']
        stock_id = text('Stock · ' + name + ' · ' + sku, maximum=195)
        quantity = [4, 0, 8, 6, 10, 12][i] if i < 6 else 60 + i * 3
        row = dict(id=stock_id, item={'sku': sku}, balance={'available': quantity, 'reorder': 24}, captured=captured)
        inventory.append(row)
        if i == 0:
            earlier_inventory.append({**row, 'balance': {'available': 31, 'reorder': 24}})
        if i < 18:
            po = f'PO-DEMO-{i+1:03d}'
            shipment = f'SHIP-DEMO-{i+1:03d}'
            late = i < 4
            delivered = i in (4, 5)
            due = as_of + timedelta(days=(-3 if late or delivered else i % 5 + 1))
            orders.append(dict(id=po, name=text(f'Demo replenishment · {name}'), supplier_id=p['master']['supplier_code'],
                product_id=sku, status='received' if delivered else 'in_transit' if late else 'confirmed', due_date=due.isoformat(), quantity=48 + i * 6))
            shipments.append(dict(id=shipment, name=text(f'Demo shipment · {name}'), order_id=po,
                status='delivered' if delivered else 'delayed' if late else 'in_transit', expected_date=due.isoformat()))
            routes.append(dict(id=f'ROUTE-{i+1:03d}', name=f'Demo receiving route {i+1:02d}', shipment_id=shipment,
                warehouse_id='WH-BLR-01', weather_id=weather[-1]['id'], distance_km=15 + i * 7,
                data_kind='Synthetic route; weather link provides context and does not explain this delay'))
        if i < 6:
            documents.append(dict(id=f'BRIEF-{i+1:03d}', title=text(f'Demo supplier brief · {name}'), product_id=sku,
                supplier_id=p['master']['supplier_code'], text=(f'SIMULATED OPERATIONS. {name} ({sku}) has {quantity} units against a reorder point of 24. '
                    f'This scenario uses invented inventory, supplier links and purchase commitments. Review PO-DEMO-{i+1:03d} '
                    'before ordering. The supplier, delivery narrative and INR values are fictional. Public catalogue labels do not establish real stock, availability or prices.')))
    warehouses = [dict(id='WH-BLR-01', name='Demo · Bengaluru receiving hub', city='Bengaluru', latitude=12.9716, longitude=77.5946,
        data_kind='Fictional facility at city-level coordinates; no real business address')]
    for provider, packet in sorted(packets.items()):
        documents.append(dict(id='CREDIT-' + provider, title='Source notes · ' + packet['name'],
            text=(f"Provider: {packet['name']}. {packet['kind']}. Source: {packet['url']}. Retrieved: {packet['fetched_at']}. "
                  f"License/reference: {packet['license']}. {packet['license_url']}. Response checksum: {packet['sha256']}. "
                  'Only selected scalar fields are mapped; originals are retained in the demo manifest. No images, reviews or customer records are imported. '
                  'Shop operations and all INR prices are synthetic. No provider endorses this demo.')))
    documents.append(dict(id='DEMO-GUIDE', title='Demo guide · Connected retail walkthrough', text=(
        'This is a simulated Bengaluru grocery retailer. Start with a low-stock record and traverse Product → Supplier → PurchaseOrder → Shipment. '
        'Show a proposed replenishment review, owner approval and its audit note. Show the intentionally invalid stock row in Pipelines. '
        'Browse CatalogReference for Open Food Facts and DummyJSON attribution. Browse WeatherObservation for NASA historical estimates. '
        'The weather link demonstrates a common ontology; it does not claim weather caused a delay or change sales. '
        'No sales ledger entries, real customers or trained model performance are created by this demonstration.')))

    mapping = lambda target, source, transform='identity', default=None: dict(target=target, source=source, transform=transform, default=default)
    groups = [
        ('suppliers', 'Supplier directory · Simulated', 'Supplier', suppliers, []),
        ('warehouse', 'Receiving locations · Simulated', 'Warehouse', warehouses, []),
        ('erp_products', 'ERP product master · Simulated INR prices', 'Product', products, [
            mapping('name', 'master.label', 'trim'), mapping('sku', 'master.sku'), mapping('category', 'master.category'),
            mapping('price', 'master.price_paise', 'minor_to_major'), mapping('currency', '', default='INR'), mapping('supplier_id', 'master.supplier_code')]),
        ('open_food_facts', 'Open Food Facts · Public label snapshot', 'CatalogReference', reference_groups['open_food_facts'], []),
        ('dummyjson', 'DummyJSON · Public fictional catalogue', 'CatalogReference', reference_groups['dummyjson'], []),
        ('stock', 'Warehouse stock feed · Simulated', 'InventoryPosition', inventory, [
            mapping('product_id', 'item.sku'), mapping('on_hand', 'balance.available', 'number'),
            mapping('reorder_point', 'balance.reorder', 'number'), mapping('captured_at', 'captured')]),
        ('orders', 'Procurement orders · Simulated', 'PurchaseOrder', orders, []),
        ('shipments', 'Carrier milestones · Simulated', 'Shipment', shipments, []),
        ('weather', 'NASA POWER · Public weather snapshot', 'WeatherObservation', weather, []),
        ('routes', 'Delivery network · Simulated', 'DeliveryRoute', routes, []),
        ('documents', 'Operations briefs & source attribution', 'Document', documents, []),
    ]
    return dict(groups=groups, earlier_inventory=earlier_inventory,
        invalid_stock=[dict(id='QUALITY-DEMO-MISSING-QUANTITY', item={'sku': products[0]['id']}, balance={'available': 'unknown', 'reorder': 24}, captured=captured)],
        stories=[dict(title='Trace a stock shortage', detail='Follow stock → product → supplier → replenishment order.', type='InventoryPosition', external_id=inventory[0]['id']),
                 dict(title='Investigate a delayed delivery', detail='Inspect the purchase order, route and receiving hub.', type='Shipment', external_id=shipments[0]['id']),
                 dict(title='Explore external weather context', detail='Inspect dated NASA estimates and the warehouse link; no causal claim.', type='WeatherObservation', external_id=weather[-1]['id'])])


def ensure_workspace():
    """Fail closed on namespace collisions; never reuse an unmarked customer shop."""
    db = database()
    with connection() as c:
        c.begin()
        retailer = db.retailers.find_one({'_id': RETAILER}, session=c.session)
        shop = db.retail_shops.find_one({'_id': WORKSPACE}, session=c.session)
        if retailer and retailer.get('demo_package') != VERSION:
            raise ValueError('Reserved demo retailer ID is occupied by another record. Nothing was changed.')
        if shop and (shop.get('demo_package') != VERSION or shop.get('retailer_id') != RETAILER or shop.get('demo') is not True):
            raise ValueError('Reserved demo workspace ID is occupied by another record. Nothing was changed.')
        if not retailer:
            db.retailers.insert_one(dict(_id=RETAILER, id=RETAILER, name='AisleMint Demo · Simulated Retail', demo_package=VERSION, created=now()), session=c.session)
        if not shop:
            db.retail_shops.insert_one(dict(_id=WORKSPACE, id=WORKSPACE, retailer_id=RETAILER, name='DEMO · Bengaluru Connected Grocery',
                store_code=STORE_CODE, country='IN', currency='INR', timezone='Asia/Kolkata', created=now(), revision=0, active={},
                demo=True, demo_package=VERSION), session=c.session)
            audit('connected_demo_workspace_created', WORKSPACE, ACTOR, {'synthetic': True}, session=c.session)


def ensure_source(key, name, kind, mappings):
    db = database()
    identifier = uuid5(NAMESPACE_URL, VERSION + '/source/' + key).hex
    old = db.connect_sources.find_one({'_id': identifier})
    values = SourceInput(name=name, object_type=kind, mapping=mappings, freshness_hours=168).model_dump()
    if old:
        if old.get('workspace') != WORKSPACE or old.get('demo_package') != VERSION:
            raise ValueError('Demo source identity collision.')
        expected = {**values, 'revision': old['revision']}
        if any(old.get(k) != v for k, v in expected.items()):
            raise ValueError(f'Demo source {key} was edited. Preserve that edit; use a new demo package to rebuild it.')
        return old
    row = dict(_id=identifier, id=identifier, workspace=WORKSPACE, **values, created=now(), demo_package=VERSION)
    row['revision'] = 1
    with connection() as c:
        c.begin()
        db.connect_sources.insert_one(row, session=c.session)
        audit('connection_created', WORKSPACE, ACTOR, {'source_id': identifier, 'kind': 'push', 'demo': True}, session=c.session)
    return row


def publish(source, records, stage, progress):
    """Run the real mapping/quality/lineage pipeline, scoped to this demo only."""
    for offset in range(0, len(records), 40):
        rows = records[offset:offset + 40]
        key = 'demo-' + stage + '-' + digest(rows)[:40]
        queued = pipeline.queue(source, key, ACTOR, rows)
        deadline = time.monotonic() + 240
        while True:
            run = database().connect_runs.find_one({'_id': queued['id'], 'workspace': WORKSPACE})
            if run['status'] in ('complete', 'complete_with_issues'):
                if stage != 'quality' and run['rejected']:
                    raise ValueError(f"Unexpected quarantined rows in {source['name']}; inspect the pipeline before proceeding.")
                break
            if run['status'] == 'failed':
                raise ValueError(f"Demo sync failed: {source['name']}. Inspect Pipelines and retry the failed run.")
            if time.monotonic() > deadline:
                raise TimeoutError('Demo sync is still running. Rerun the script to resume without duplicate data.')
            if not pipeline.process_one(WORKSPACE):
                time.sleep(.5)
        progress(f"{source['name']}: {offset + len(rows)}/{len(records)} rows processed")


def provision(*, refresh=False, packets=None, as_of=None, progress=print):
    """Reuse a frozen manifest by default. Refresh explicitly creates a new dated one."""
    db = database()
    from zoneinfo import ZoneInfo
    as_of = as_of or datetime.now(ZoneInfo('Asia/Kolkata')).date()
    existing = db.connect_demo_manifests.find_one({'workspace': WORKSPACE, 'package': VERSION}, sort=[('created', -1)])
    if existing and not refresh:
        packets, as_of = existing['public_sources'], date.fromisoformat(existing['as_of'])
        progress('Reusing the frozen MongoDB demo manifest; no public API calls needed.')
    else:
        progress('Fetching bounded public catalogue and weather snapshots…')
        packets = packets if packets is not None else fetch_public(as_of)
    scenario = build_scenario(packets, as_of)
    # Validate every intended object before creating the demo retailer or publishing.
    specs = {s.name: {**s.model_dump(), 'revision': 1} for s in definitions()}
    from .schema import builtin_types
    specs.update({s['name']: s for s in builtin_types()})
    for _, _, kind, rows, mappings in scenario['groups']:
        for row in rows:
            map_record(row, {'id_field': 'id', 'mapping': mappings}, specs[kind])
    ensure_workspace()
    manifest_id = digest([VERSION, as_of.isoformat(), packets])
    db.connect_demo_manifests.update_one({'_id': manifest_id}, {'$setOnInsert': dict(workspace=WORKSPACE, package=VERSION,
        as_of=as_of.isoformat(), public_sources=packets, created=now())}, upsert=True)
    token = auth.principal.set({'id': ACTOR, 'admin': False, 'memberships': {RETAILER: 'owner'}})
    try:
        for spec in definitions():
            old = db.connect_types.find_one({'workspace': WORKSPACE, 'name': spec.name})
            if not old:
                api.define_type(WORKSPACE, spec)
            elif old['properties'] != [p.model_dump() for p in spec.properties]:
                raise ValueError(f'Demo contract {spec.name} was edited; it will not be overwritten.')
        # Resume safely: an earlier inventory version is only queued once, before current stock.
        for key, name, kind, rows, mappings in scenario['groups']:
            source = ensure_source(key, name, kind, mappings)
            if key == 'stock' and not db.connect_runs.find_one({'source_id': source['id']}):
                publish(source, scenario['earlier_inventory'], 'earlier-stock', progress)
            publish(source, rows, key, progress)
            if key == 'stock':
                publish(source, scenario['invalid_stock'], 'quality', progress)
        stories = [{**s, 'object_id': object_id(WORKSPACE, s['type'], s['external_id'])} for s in scenario['stories']]
        for i, story in enumerate(stories[:2]):
            request_key = 'demo-proposal-' + manifest_id[:32] + '-' + str(i)
            if not db.connect_decisions.find_one({'workspace': WORKSPACE, 'request_key': request_key}):
                obj = db.connect_objects.find_one({'_id': story['object_id'], 'workspace': WORKSPACE})
                api.propose(WORKSPACE, api.Decision(object_id=obj['id'], object_revision=obj['revision'],
                    title='Demo review · ' + story['title'], rationale='SIMULATED: ' + story['detail'] + ' Review the source evidence and record an owner decision. No external action is performed.',
                    kind='replenishment_review' if i == 0 else 'supplier_followup', request_key=request_key))
        info = dict(as_of=as_of.isoformat(), manifest_id=manifest_id, sources=[{k: p[k] for k in ('name', 'reference', 'kind', 'fetched_at', 'license_url')} for p in packets.values()],
            stories=stories, note='Public reference snapshots with simulated operations. INR prices, stock, suppliers, orders and delays are invented; no real customer transactions or ML performance.',
            populated_at=now().isoformat())
        db.retail_shops.update_one({'_id': WORKSPACE, 'demo_package': VERSION}, {'$set': {'demo_info': info}})
        db.connect_demo_manifests.update_one({'_id': manifest_id}, {'$set': {'completed_at': now()}})
        audit('connected_demo_populated', WORKSPACE, ACTOR, {'manifest_id': manifest_id, 'public_providers': list(packets)})
        return dict(workspace=WORKSPACE, retailer_id=RETAILER, shop='DEMO · Bengaluru Connected Grocery',
            sources=db.connect_sources.count_documents({'workspace': WORKSPACE}), objects=db.connect_objects.count_documents({'workspace': WORKSPACE}),
            quarantine=db.connect_quarantine.count_documents({'workspace': WORKSPACE}), as_of=as_of.isoformat())
    finally:
        auth.principal.reset(token)
