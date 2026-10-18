"""Bounded read-only adapters. Deployment-owned bindings scope secrets and endpoints to a shop.

Browser users cannot supply URLs, arbitrary queries, headers or secret names. External
systems are never modified. Scheduled syncs scan current records, not change-data capture.
"""
import base64
import json
import os
import ssl
import certifi
from urllib.parse import urlsplit, urlencode, urlunsplit, parse_qsl
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler
from xml.etree import ElementTree
from bson import ObjectId
from datetime import datetime
from zoneinfo import ZoneInfo
from pymongo import MongoClient
from ..pg import database
from ..brand import setting
from ..retail.repository import required_shop, now
from .schema import value_at

PAGE_SIZE = 100
MAX_RESPONSE = 4 * 1024 * 1024
CATALOG = [
    dict(kind='onesales', name='AisleMint OneSales', scope='Product catalogue, observed inventory and closed-day sales history', setup='Connect the shop register in Modelling workbench. Sales refresh after day close and at the next local day.'),
    dict(kind='push', name='REST ingestion', scope='Any declared object type, including sales history and document text', setup='Send authenticated JSON batches with a retailer integration key.'),
    dict(kind='rest', name='REST source', scope='Paginated JSON lists', setup='An operator registers a shop-scoped HTTPS binding.'),
    dict(kind='mongodb', name='MongoDB source', scope='One collection with a fixed filter and projection', setup='An operator registers a read-only database binding.'),
    dict(kind='shopify', name='Shopify', scope='Variant catalog; aggregate inventory only for a single-location shop', setup='Admin GraphQL token, API version and shop endpoint.'),
    dict(kind='woocommerce', name='WooCommerce', scope='Product catalog; variations require a separate mapped REST binding', setup='Read-only consumer key and secret.'),
    dict(kind='zoho', name='Zoho Inventory', scope='Item catalog; aggregate inventory only for a single-location shop', setup='Organization ID and OAuth access token; refresh managed by your secret provider.'),
    dict(kind='tally', name='TallyPrime', scope='Stock-item master names and categories', setup='Read-only XML export via a company-scoped local Tally gateway.'),
]

def bindings(workspace):
    try: values = json.loads(setting('SOURCE_BINDINGS', '{}'))
    except json.JSONDecodeError: raise ValueError('Source bindings configuration is invalid; contact your operator.') from None
    if not isinstance(values, dict): raise ValueError('Source bindings must be an object.')
    return {name: config for name, config in values.items() if isinstance(config, dict) and config.get('workspace') == workspace}

def binding(source):
    config = bindings(source['workspace']).get(source['binding'])
    if not config or config.get('kind') != source['kind']: raise ValueError('This connection needs an operator-configured binding for this shop and connector.')
    return config

def secret(config, name, optional=False):
    variable = config.get(name + '_env')
    if variable and not variable.startswith(('AISLEMINT_CONNECT_', 'HINDSIGHT_CONNECT_')): raise ValueError('Connector secrets must use an AISLEMINT_CONNECT_ environment variable (legacy HINDSIGHT_CONNECT_ is also supported).')
    value = os.environ.get(variable, '') if variable else ''
    if not value and not optional: raise ValueError('Connector credentials are not configured.')
    return value

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None

def request_data(url, headers=None, body=None, local=False):
    parsed = urlsplit(url)
    if parsed.username or parsed.password or parsed.fragment: raise ValueError('Endpoint credentials and fragments are not allowed.')
    if parsed.scheme != 'https' and not (local and parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1', '::1')):
        raise ValueError('External connections require HTTPS; local Tally and local AI may use loopback HTTP.')
    req = Request(url, headers=headers or {}, data=body)
    # No redirects: never forward a source credential to a second origin.
    context = ssl.create_default_context(cafile=certifi.where())
    with build_opener(NoRedirect, HTTPSHandler(context=context)).open(req, timeout=20) as response:
        raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE: raise ValueError('Source response exceeds 4 MiB; reduce its page size.')
        return raw

def json_request(url, headers=None, payload=None):
    return json.loads(request_data(url, headers, None if payload is None else json.dumps(payload).encode()))

def with_query(url, **params):
    parsed = urlsplit(url); query = dict(parse_qsl(parsed.query)); query.update({k: str(v) for k, v in params.items()})
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ''))

def _native(source, cursor):
    shop = required_shop(source['workspace']); store = shop.get('pos_store_id')
    if not store: raise ValueError('Connect this shop to a OneSales register first.')
    if source['object_type']=='SalesObservation':
        today=datetime.now(ZoneInfo(shop['timezone'])).date().isoformat()
        query={'store_id':store,'business_date':{'$lt':today}}
        if cursor: query['$or']=[{'business_date':{'$gt':cursor['date']}},{'business_date':cursor['date'],'product_id':{'$gt':cursor['product']}}]
        rows=list(database().pos_daily.find(query).sort([('business_date',1),('product_id',1)]).limit(PAGE_SIZE))
        result=[dict(r['body'],store_id=shop['store_code'],id='onesales:'+r['business_date']+':'+r['product_id']) for r in rows]
        return result,dict(date=rows[-1]['business_date'],product=rows[-1]['product_id']) if len(rows)==PAGE_SIZE else None
    if source['object_type'] not in ('Product', 'InventoryPosition'): raise ValueError('OneSales supports Product, InventoryPosition and SalesObservation.')
    query = {'store_id': store, **({'product_id': {'$gt': cursor}} if cursor else {})}
    rows = list(database().pos_products.find(query).sort('product_id', 1).limit(PAGE_SIZE))
    result = []
    for r in rows:
        if source['object_type'] == 'Product':
            result.append(dict(id=r['product_id'], name=r['name'], sku=r['product_id'], category=r['category'], price=r['price_paise']/100,
                currency=shop['currency'], on_hand=r['on_hand'], reorder_point=r['reorder_level']))
        else:
            result.append(dict(id=r['product_id'], product_id=r['product_id'], on_hand=r['on_hand'], reorder_point=r['reorder_level'], captured_at=now().isoformat(),unit_cost=r['cost_paise']/100,currency=shop['currency']))
    return result, rows[-1]['product_id'] if len(rows) == PAGE_SIZE else None

def fetch_page(source, cursor=None):
    kind = source['kind']
    if kind == 'onesales': return _native(source, cursor)
    config = binding(source)
    if kind == 'mongodb':
        # A fixed operator-owned filter; browser requests cannot inject Mongo operators.
        query = config.get('filter', {})
        if cursor:
            key = ObjectId(cursor['value']) if cursor['type'] == 'objectid' else cursor['value']
            query = {'$and': [query, {'_id': {'$gt': key}}]}
        with MongoClient(secret(config, 'uri'), serverSelectionTimeoutMS=8000, socketTimeoutMS=20000) as client:
            projection = dict(config.get('projection', {}))
            if projection: projection['_id'] = 1
            rows = list(client[config['database']][config['collection']].find(query, projection or None).sort('_id', 1).limit(PAGE_SIZE).max_time_ms(15000))
        next_cursor = None
        if len(rows) == PAGE_SIZE:
            last = rows[-1]['_id']
            if not isinstance(last, (str, ObjectId, int)): raise ValueError('Mongo source IDs must be consistently strings, integers or ObjectIds.')
            next_cursor = {'type': 'objectid' if isinstance(last, ObjectId) else 'scalar', 'value': str(last) if isinstance(last, ObjectId) else last}
        return [json.loads(json.dumps(row, default=str)) for row in rows], next_cursor
    url = config['url']; page = int(cursor or 1) if kind not in ('shopify',) else None
    if kind == 'shopify':
        query = 'query($after:String){productVariants(first:100,after:$after){nodes{id title sku price inventoryQuantity product{title productType}} pageInfo{hasNextPage endCursor}}}'
        response = json_request(url, {'Content-Type':'application/json', 'X-Shopify-Access-Token':secret(config, 'token')}, {'query':query, 'variables':{'after':cursor}})
        if response.get('errors'): raise ValueError('Shopify rejected the query; verify token scopes and the configured API version.')
        data = response['data']['productVariants']; rows = []
        for r in data['nodes']:
            row = dict(id=r['id'], name=r['product']['title']+' · '+r['title'], sku=r.get('sku') or r['id'], category=r['product']['productType'] or 'Uncategorized', price=float(r['price']), currency=config['currency'])
            if config.get('single_location'): row['on_hand'] = r['inventoryQuantity']
            rows.append(row)
        return rows, data['pageInfo']['endCursor'] if data['pageInfo']['hasNextPage'] else None
    if kind == 'woocommerce':
        credentials = base64.b64encode((secret(config, 'key')+':'+secret(config, 'secret')).encode()).decode()
        rows = json_request(with_query(url, page=page, per_page=PAGE_SIZE), {'Authorization':'Basic '+credentials})
        if not isinstance(rows, list): raise ValueError('WooCommerce returned an invalid product response.')
        result = [dict(id=str(r['id']), name=r['name'], sku=r.get('sku') or str(r['id']), category=', '.join(c['name'] for c in r.get('categories', [])) or 'Uncategorized',
            **({'price':float(r['price'])} if r.get('price') else {}), currency=config['currency'],
            **({'on_hand':r['stock_quantity']} if r.get('manage_stock') and r.get('stock_quantity') is not None else {})) for r in rows]
        return result, page+1 if len(rows) == PAGE_SIZE else None
    if kind == 'zoho':
        data = json_request(with_query(url, page=page, per_page=PAGE_SIZE, organization_id=config['organization_id']), {'Authorization':'Zoho-oauthtoken '+secret(config, 'token')})
        if data.get('code') != 0: raise ValueError('Zoho rejected the request; verify organization and token scopes.')
        rows = [dict(id=r['item_id'], name=r['name'], sku=r.get('sku') or r['item_id'], price=float(r['rate']), currency=config['currency'],
            **({'on_hand':r['stock_on_hand']} if config.get('single_location') and r.get('stock_on_hand') is not None else {})) for r in data['items']]
        return rows, page+1 if data.get('page_context', {}).get('has_more_page') else None
    if kind == 'tally':
        from xml.sax.saxutils import escape
        company = escape(config['company'])
        xml = ('<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE><ID>AisleMintStockItems</ID></HEADER>'
            '<BODY><DESC><STATICVARIABLES><SVCURRENTCOMPANY>'+company+'</SVCURRENTCOMPANY><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES>'
            '<TDL><TDLMESSAGE><COLLECTION NAME="AisleMintStockItems"><TYPE>Stock Item</TYPE><FETCH>GUID,Name,Parent</FETCH></COLLECTION></TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>')
        raw = request_data(url, {'Content-Type':'text/xml'}, xml.encode(), local=True)
        if b'<!' in raw: raise ValueError('Tally XML declarations and external entities are not accepted.')
        root = ElementTree.fromstring(raw)
        if root.find('.//LINEERROR') is not None: raise ValueError('Tally could not export the selected company.')
        rows = [dict(id=r.findtext('GUID') or r.attrib['NAME'], name=r.attrib['NAME'], category=r.findtext('PARENT') or 'Uncategorized') for r in root.findall('.//STOCKITEM')]
        # Tally collection export is bounded, not cursor-paginated. No silent truncation.
        if len(rows) > 500: raise ValueError('Tally returned more than 500 stock items. Partition exports through the REST ingestion adapter.')
        return rows, None
    if kind == 'rest':
        token = secret(config, 'token', optional=True)
        data = json_request(with_query(url, **{config.get('page_parameter','page'):page, config.get('size_parameter','per_page'):PAGE_SIZE}), {'Authorization':'Bearer '+token} if token else {})
        rows = value_at(data, config['records_path']) if config.get('records_path') else data
        if not isinstance(rows, list) or len(rows) > 500 or any(not isinstance(r, dict) for r in rows): raise ValueError('REST source must return a bounded list of JSON objects.')
        more = value_at(data, config['has_more_path']) if config.get('has_more_path') else len(rows) == PAGE_SIZE
        return rows, page+1 if more else None
    raise ValueError('Unsupported connector.')
