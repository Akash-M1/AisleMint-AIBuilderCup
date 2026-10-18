"""Declarative, versioned object contracts. No user code or database queries execute here."""
from datetime import date, datetime
from typing import Literal
import math
import re
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class Property(Input):
    name: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
    type: Literal['string', 'number', 'integer', 'boolean', 'date', 'datetime'] = 'string'
    required: bool = False
    reference: str | None = Field(default=None, pattern=r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
    @model_validator(mode='after')
    def reference_string(self):
        if self.reference and self.type != 'string': raise ValueError('References must be string identifiers.')
        return self

class ObjectType(Input):
    name: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
    description: str = Field(min_length=4, max_length=500)
    properties: list[Property] = Field(min_length=1, max_length=60)
    revision: int = Field(default=0, ge=0)
    @model_validator(mode='after')
    def unique_properties(self):
        if len({p.name for p in self.properties}) != len(self.properties): raise ValueError('Property names must be unique.')
        return self

class Mapping(Input):
    target: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
    source: str = Field(max_length=200, pattern=r'^[A-Za-z0-9_.-]*$')
    transform: Literal['identity', 'string', 'number', 'integer', 'boolean', 'trim', 'lower', 'upper', 'minor_to_major'] = 'identity'
    default: str | float | bool | None = None

class SourceInput(Input):
    name: str = Field(min_length=2, max_length=120)
    kind: Literal['push', 'onesales', 'rest', 'mongodb', 'shopify', 'woocommerce', 'zoho', 'tally'] = 'push'
    binding: str = Field(default='', pattern=r'^[A-Za-z0-9_-]{0,64}$')
    object_type: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
    id_field: str = Field(default='id', min_length=1, max_length=200, pattern=r'^[A-Za-z0-9_.-]+$')
    mapping: list[Mapping] = Field(default_factory=list, max_length=60)
    visibility: Literal['team', 'restricted'] = 'team'
    freshness_hours: int = Field(default=24, ge=1, le=8760)
    schedule_minutes: int = Field(default=0, ge=0, le=10080)
    enabled: bool = True
    revision: int = Field(default=0, ge=0)
    @model_validator(mode='after')
    def unique_targets(self):
        if len({m.target for m in self.mapping}) != len(self.mapping): raise ValueError('Each target property must be mapped once.')
        if 0 < self.schedule_minutes < 15: raise ValueError('Scheduled syncs must be at least 15 minutes apart.')
        if self.kind == 'push' and self.schedule_minutes: raise ValueError('Push connections receive batches from a connector; they are not polled.')
        return self

def prop(name, kind='string', required=False, reference=None):
    return dict(name=name, type=kind, required=required, reference=reference)

BUILTINS = {
    'Product': ('Products sold by this shop.', [prop('name', required=True), prop('sku'), prop('category'), prop('price', 'number'), prop('currency'), prop('on_hand', 'number'), prop('reorder_point', 'number'), prop('supplier_id', reference='Supplier')]),
    'Supplier': ('Organizations supplying this shop.', [prop('name', required=True), prop('status'), prop('lead_time_days', 'integer')]),
    'InventoryPosition': ('Observed stock at a specified time.', [prop('product_id', required=True, reference='Product'), prop('on_hand', 'number', True), prop('reorder_point', 'number'), prop('captured_at', 'datetime', True), prop('unit_cost','number'), prop('currency'), *[prop(n,'number') for n in ('reserved','backlog','lead_time_days','review_period_days','case_pack')]]),
    'PurchaseOrder': ('Purchase commitments and expected receipts.', [prop('name', required=True), prop('supplier_id', reference='Supplier'), prop('product_id', reference='Product'), prop('status', required=True), prop('due_date', 'date', True), prop('quantity', 'number')]),
    'Shipment': ('Shipments associated with purchase orders.', [prop('name', required=True), prop('order_id', reference='PurchaseOrder'), prop('status', required=True), prop('expected_date', 'date', True)]),
    'Document': ('Searchable document text supplied by an authorized connector.', [prop('title', required=True), prop('text', required=True), prop('product_id', reference='Product'), prop('supplier_id', reference='Supplier')]),
}
BUILTINS['SalesObservation'] = ('Completed daily or weekly sales observations for the Modelling Workbench.', [
    prop('date', 'date', True), *[prop(n, required=True) for n in ('store_id', 'product_id', 'name', 'category', 'country', 'currency')],
    *[prop(n, 'number', True) for n in ('units', 'price', 'base_price')],
    *[prop(n, 'integer', True) for n in ('feature', 'display', 'promotion_available')],
    *[prop(n, 'integer') for n in ('stockout', 'holiday', 'free_promotion')],
    *[prop(n, 'number') for n in ('unit_cost', 'SPEND', 'returned_units', 'refund_ex_gst')],
])

def builtin_types():
    return [dict(name=name, description=desc, properties=fields, revision=1, builtin=True) for name, (desc, fields) in BUILTINS.items()]

def value_at(record, path):
    value = record
    for key in path.split('.'):
        if not isinstance(value, dict): return None
        value = value.get(key)
    return value

def convert(value, transform):
    if value is None: return None
    if transform == 'identity': return value
    if isinstance(value, (dict, list)): raise ValueError('A mapped value must be a scalar.')
    if transform in ('string', 'trim', 'lower', 'upper'):
        result = str(value)
        return result if transform == 'string' else getattr(result, transform)() if transform != 'trim' else result.strip()
    if transform == 'boolean':
        if value in (True, 1, '1', 'true', 'True'): return True
        if value in (False, 0, '0', 'false', 'False'): return False
        raise ValueError('Expected a boolean value.')
    if isinstance(value, bool): raise ValueError('Booleans are not quantities.')
    number = float(value)
    if not math.isfinite(number): raise ValueError('Number must be finite.')
    if transform == 'integer':
        if number != int(number): raise ValueError('Expected a whole number.')
        return int(number)
    return number / 100 if transform == 'minor_to_major' else number

def validate_properties(properties, spec):
    allowed = {p['name']: p for p in spec['properties']}
    if set(properties) - set(allowed): raise ValueError('Unrecognized target properties: ' + ', '.join(sorted(set(properties) - set(allowed))))
    for name, p in allowed.items():
        value = properties.get(name)
        if value is None:
            if p['required']: raise ValueError(f'{name} is required.')
            continue
        kind = p['type']
        if kind in ('string', 'date', 'datetime'):
            if not isinstance(value, str) or not value.strip() or len(value) > (16000 if name == 'text' else 500): raise ValueError(f'{name} must be a non-empty bounded string.')
            if kind == 'date': date.fromisoformat(value)
            if kind == 'datetime':
                if datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is None: raise ValueError(f'{name} requires a timezone.')
        elif kind == 'boolean':
            if not isinstance(value, bool): raise ValueError(f'{name} must be boolean.')
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value): raise ValueError(f'{name} must be finite numeric data.')
        elif kind == 'integer' and value != int(value): raise ValueError(f'{name} must be a whole number.')
        if p.get('reference') and len(value) > 200: raise ValueError(f'{name} reference is too long.')
    return {key: value for key, value in properties.items() if value is not None}

def map_record(record, source, spec):
    external_id = value_at(record, source['id_field'])
    if isinstance(external_id, bool) or not isinstance(external_id, (str, int)): raise ValueError('A stable source identifier is required.')
    external_id = str(external_id)
    if not external_id.strip() or len(external_id) > 200: raise ValueError('Source identifier must contain 1–200 characters.')
    fields = source['mapping'] or [dict(target=p['name'], source=p['name'], transform='identity', default=None) for p in spec['properties']]
    result = {}
    for field in fields:
        value = value_at(record, field['source']) if field['source'] else None
        result[field['target']] = convert(field.get('default') if value is None else value, field['transform'])
    return external_id, validate_properties(result, spec)
