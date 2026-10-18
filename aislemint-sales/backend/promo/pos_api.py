"""OneSales local store operations and register API."""
from __future__ import annotations
from .storage import AssetPath,managed,read_frame,write_frame,load_model,dump_model
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta,date
from pathlib import Path
from typing import Literal
import csv,io,json,hmac,re,secrets
import pandas as pd
from fastapi import APIRouter,Header,HTTPException
from fastapi.responses import Response
from pydantic import Field
from .pos_core import *
from .pos_returns import ReturnInput,ReturnQuote,sale_detail,quote_return,complete_return

from .pg import MongoRoute
router=APIRouter(route_class=MongoRoute,prefix='/api/pos',tags=['OneSales'])
def service():
    from . import api
    return api
@contextmanager
def operation(store_id,authorization,manager=False,write=False):
    with service().connection() as con:
        con.begin()
        staff=authorize(con,store_id,authorization,manager);store=store_row(con,store_id)
        yield con,store,staff

class Login(StrictModel):
    staff_id:str
    pin:str=Field(pattern=r'^\d{6,12}$')
class StaffInput(StrictModel):
    name:str=Field(min_length=2,max_length=80)
    role:Literal['cashier','manager']='cashier'
    pin:str=Field(pattern=r'^\d{6,12}$')
class CustomerInput(StrictModel):
    id:str|None=None
    name:str=Field(min_length=2,max_length=100)
    phone:str=Field(default='',max_length=25,pattern=r'^[+\d ()-]*$')
    email:str=Field(default='',max_length=150)
    address:str=Field(default='',max_length=400)
    state_code:str=Field(default='',pattern=r'^(|0[1-9]|[12][0-9]|3[0-8])$')
    gstin:str=Field(default='',max_length=15)
    @model_validator(mode='after')
    def business_customer(self):
        self.phone=re.sub(r'[^+\d]','',self.phone);self.gstin=self.gstin.upper()
        if self.gstin and (not re.fullmatch(r'\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]',self.gstin) or not self.address or self.gstin[:2]!=self.state_code):raise ValueError('A business customer needs a GSTIN, address and matching registration state.')
        return self
class StockInput(StrictModel):
    product_id:str
    quantity:int=Field(ge=-1000000,le=1000000,strict=True)
    kind:Literal['receive','adjustment','discard']='receive'
    reason:str=Field(min_length=3,max_length=200)
    idempotency_key:str=Field(min_length=8,max_length=128,pattern=r'^[A-Za-z0-9_-]+$')
class OpenShift(StrictModel):
    register_id:str=Field(default='Register 1',min_length=1,max_length=30)
    opening_paise:int=Field(default=0,ge=0,le=100000000,strict=True)
class CloseShift(StrictModel):counted_paise:int=Field(ge=0,le=1000000000,strict=True)
class CashMovement(StrictModel):
    amount_paise:int=Field(ge=-100000000,le=100000000,strict=True)
    reason:str=Field(min_length=3,max_length=200)
class ParkCart(StrictModel):
    name:str=Field(min_length=1,max_length=80)
    cart:Cart
class ConnectionInput(StrictModel):
    workspace:str|None=None
    store_id:str|None=None
    demo_reference_date:str|None=None

@router.get('/stores')
def stores():
    with service().connection() as c:
        rows=[dict(r) for r in c.run('pos_api_stores_aggregate_pos_stores', ())]
        from .retail.auth import principal,allowed_retailers
        if principal.get():
            allowed=allowed_retailers()
            if allowed is not None:
                from .retail.permissions import allows
                allowed=[r for r in allowed if allows(principal.get(),r,'sales')]
                owned={r['id'] for r in c.db.pos_stores.find({'retailer_id':{'$in':allowed}},session=c.session)}
                rows=[r for r in rows if r['id'] in owned]
        for r in rows:r['staff']=[dict(s) for s in c.run('pos_api_stores_aggregate_pos_staff', (r['id'],))]
        return rows

def create_store(con,req,demo=False):
    id=uid();created=now()
    con.run('pos_api_create_store_insert_pos_stores', (id, req.name, req.address, req.state_code, req.gstin, int(req.gst_registered), int(demo), created))
    staff_id=uid();salt=secrets.token_hex(16)
    con.run('pos_api_create_store_insert_pos_staff', (staff_id, id, req.owner_name, 'manager', salt, pin_hash(req.pin, salt), created))
    audit_event(con,id,'store_created',id,dict(demo=demo))
    return dict(store=store_row(con,id),staff=dict(id=staff_id,name=req.owner_name,role='manager'),token=session_for(con,staff_id))

@router.post('/stores')
def new_store(req:StoreInput):
    with service().connection() as c:
        c.begin();return create_store(c,req)

@router.post('/demo')
def demo_store():
    with service().connection() as c:
        c.begin();old=c.run('pos_api_demo_store_aggregate_pos_stores', ()).fetchone()
        if old:
            staff=dict(c.run('pos_api_demo_store_aggregate_pos_staff', (old['id'],)).fetchone())
            return dict(store=dict(old),staff=staff,token=session_for(c,staff['id']))
        req=StoreInput(name='OneSales · Demo Market',address='Illustrative Indian grocery store',state_code='29',gst_registered=True,gstin='29ABCDE1234F1Z5',owner_name='Demo manager',pin='902617')
        result=create_store(c,req,True);store=result['store']
        catalog=service().get_engine('benchmark').catalog('367')[:18]
        # Explicit demo inventory/prices: no exchange-rate conversion or real balances implied.
        for i,p in enumerate(catalog):
            price=[9900,14900,24900,7900,18900,5900][i%6];cost=price*60//100;gst=500 if i%3 else 1800
            c.run('pos_api_demo_store_insert_pos_products', (store['id'], p['product_id'], p['product_id'], p['name'], p['category'], '19059090', 'NOS', price, cost, gst, 0, 10, 1, now()))
            stock_move(c,store,p['product_id'],60,'demo_opening',store['id'],'Illustrative stock, not a physical count',result['staff']['id'],cost)
        c.run('pos_api_demo_store_update_pos_stores', ('benchmark', '367', '2012-01-11', store['id']))
        result['store']=store_row(c,store['id']);return result

@router.post('/stores/{store_id}/login')
def login(store_id:str,req:Login):
    failure=None;result=None
    with service().connection() as c:
        c.begin();store_row(c,store_id)
        row=c.run('pos_api_login_aggregate_pos_staff', (req.staff_id, store_id)).fetchone()
        if row and row['locked_until'] and row['locked_until']>now():failure='Too many attempts. Try again after the lock expires.'
        elif not row or not hmac.compare_digest(pin_hash(req.pin,row['salt']),row['pin_hash']):
            failure='Incorrect staff PIN.'
            if row:
                attempts=row['failed_attempts']+1;until=(datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat() if attempts>=5 else None
                c.run('pos_api_login_update_pos_staff_2', (attempts, until, row['id']))
        else:
            c.run('pos_api_login_update_pos_staff', (row['id'],))
            result=dict(store=store_row(c,store_id),staff=dict(id=row['id'],name=row['name'],role=row['role']),token=session_for(c,row['id']))
    if failure:raise HTTPException(401,failure)
    return result

@router.post('/stores/{store_id}/logout')
def logout(store_id:str,authorization:str=Header(default='')):
    with operation(store_id,authorization,write=True) as (c,s,staff):c.run('pos_api_logout_delete_pos_sessions', (hashlib.sha256(authorization.removeprefix('Bearer ').encode()).hexdigest(),))
    return dict(status='locked')

@router.get('/stores/{store_id}/context')
def context(store_id:str,authorization:str=Header(default='')):
    with operation(store_id,authorization) as (c,s,staff):
        shifts=[{**dict(r),'expected_paise':expected_cash(c,dict(r))} for r in c.run('pos_api_context_aggregate_pos_shifts', (store_id,))]
        offers,rev=executed_offers(c,s)
        return dict(store=s,staff=staff,shifts=shifts,business_date=local_day(s),day_closed=bool(c.run('pos_api_context_aggregate_pos_days', (store_id, local_day(s))).fetchone()),active_offers=offers,calendar_revision=rev)

@router.post('/stores/{store_id}/staff')
def add_staff(store_id:str,req:StaffInput,authorization:str=Header(default='')):
    with operation(store_id,authorization,True,True) as (c,s,staff):
        id=uid();salt=secrets.token_hex(16)
        c.run('pos_api_create_store_insert_pos_staff', (id, store_id, req.name, req.role, salt, pin_hash(req.pin, salt), now()))
        audit_event(c,store_id,'staff_created',id,dict(name=req.name,role=req.role,actor=staff['id']))
        return dict(id=id,name=req.name,role=req.role)

@router.get('/stores/{store_id}/products')
def products(store_id:str,q:str='',authorization:str=Header(default='')):
    with operation(store_id,authorization) as (c,s,staff):
        return [dict(r) for r in c.run('pos_api_products_aggregate_pos_products', (store_id, *['%' + q[:100] + '%'] * 3))]

def put_product(c,s,staff,p):
    if s['gst_registered'] and not s['demo'] and len(p.hsn) not in [4,6,8]:raise ValueError('Provide the product HSN (4, 6 or 8 digits) for a GST-registered store.')
    if not s['gst_registered'] and p.gst_bps:raise ValueError('An unregistered store cannot collect GST.')
    old=c.run('pos_api_put_product_aggregate_pos_products', (s['id'], p.product_id)).fetchone()
    if old and old['category']!=p.category and c.run('pos_api_put_product_aggregate_pos_sale_lines', (s['id'], p.product_id)).fetchone():raise ValueError('A traded product must keep a stable category for model history. Create a new product version for a reclassification.')
    try:c.run('pos_api_put_product_upsert_pos_products', (s['id'], p.product_id, p.barcode, p.name, p.category, p.hsn, p.unit, p.price_paise, p.cost_paise, p.gst_bps, 0, p.reorder_level, int(p.active), now()))
    except Exception as exc:
        if getattr(exc, 'sqlstate', '') == '23505' or 'duplicate' in str(exc).lower(): raise ValueError('Barcode must be unique within this store.') from exc
        raise
    audit_event(c,s['id'],'product_updated',p.product_id,dict(actor=staff['id'],product=p.model_dump()))

@router.post('/stores/{store_id}/products')
def save_product(store_id:str,req:ProductInput,authorization:str=Header(default='')):
    with operation(store_id,authorization,True,True) as (c,s,staff):
        put_product(c,s,staff,req);c.run('pos_api_save_product_update_pos_stores', (store_id,));return req.model_dump()



@router.post('/stores/{store_id}/stock')
def adjust_stock(store_id:str,req:StockInput,authorization:str=Header(default='')):
    with operation(store_id,authorization,True,True) as (c,s,staff):
        old=c.run('pos_api_adjust_stock_aggregate_pos_inventory_movements', (store_id, req.idempotency_key)).fetchone()
        if old:
            if (old['product_id'],old['quantity'],old['kind'],old['reason'])!=(req.product_id,req.quantity,req.kind,req.reason):raise HTTPException(409,'Stock retry key has different inputs.')
            return dict(old)
        if req.quantity==0 or req.kind=='receive' and req.quantity<0 or req.kind=='discard' and req.quantity>0:raise ValueError('Receive a positive quantity; discard a negative quantity. Adjustment must be nonzero.')
        p=c.run('pos_api_adjust_stock_aggregate_pos_products', (store_id, req.product_id)).fetchone()
        if not p:raise ValueError('Unknown stock product.')
        stock_move(c,s,req.product_id,req.quantity,req.kind,uid(),req.reason,staff['id'],p['cost_paise'],req.idempotency_key)
        audit_event(c,store_id,'stock_'+req.kind,req.product_id,req.model_dump());return dict(status='recorded')

@router.get('/stores/{store_id}/stock/movements')
def stock_history(store_id:str,product_id:str='',authorization:str=Header(default='')):
    with operation(store_id,authorization) as (c,s,staff):return [dict(r) for r in c.run('pos_api_stock_history_aggregate_pos_inventory_movements', (store_id, product_id, product_id))]

@router.get('/stores/{store_id}/customers')
def customers(store_id:str,q:str='',authorization:str=Header(default='')):
    with operation(store_id,authorization) as (c,s,staff):return [dict(r) for r in c.run('pos_api_customers_aggregate_pos_customers', (store_id, *['%' + q[:100] + '%'] * 3))]

@router.post('/stores/{store_id}/customers')
def customer(store_id:str,req:CustomerInput,authorization:str=Header(default='')):
    with operation(store_id,authorization,write=True) as (c,s,staff):
        id=req.id or uid()
        if req.id and not c.run('pos_api_customer_aggregate_pos_customers', (id, store_id)).fetchone():raise HTTPException(404,'Customer not found.')
        c.run('pos_api_customer_upsert_pos_customers', (id, store_id, req.name, req.phone, req.email, req.address, req.state_code, req.gstin, now()))
        audit_event(c,store_id,'customer_updated',id,dict(staff_id=staff['id']));return dict(id=id,**req.model_dump(exclude={'id'}))

@router.get('/stores/{store_id}/customers/{customer_id}')
def customer_history(store_id:str,customer_id:str,authorization:str=Header(default='')):
    with operation(store_id,authorization) as (c,s,staff):
        row=c.run('pos_api_customer_history_aggregate_pos_customers', (customer_id, store_id)).fetchone()
        if not row:raise HTTPException(404,'Customer not found.')
        sales=[dict(r) for r in c.run('pos_api_customer_history_aggregate_pos_sales', (store_id, customer_id))]
        stats=c.run('pos_api_customer_history_aggregate_pos_sales_2', (store_id, customer_id)).fetchone()
        refunded=c.run('pos_api_customer_history_aggregate_pos_returns', (store_id, customer_id)).fetchone()[0]
        return dict(customer=dict(row),sales=sales,visits=stats['visits'],net_spent_paise=stats['spent']-refunded)

@router.post('/stores/{store_id}/shifts')
def open_shift(store_id:str,req:OpenShift,authorization:str=Header(default='')):
    with operation(store_id,authorization,write=True) as (c,s,staff):
        if c.run('pos_api_context_aggregate_pos_days', (store_id, local_day(s))).fetchone():raise HTTPException(409,'This trading day is already closed.')
        old=c.run('pos_api_open_shift_aggregate_pos_shifts', (store_id, req.register_id)).fetchone()
        if old:raise HTTPException(409,'This register already has an open shift.')
        id=uid();c.run('pos_api_open_shift_insert_pos_shifts', (id, store_id, req.register_id, staff['id'], local_day(s), req.opening_paise, now()))
        audit_event(c,store_id,'shift_opened',id,dict(staff_id=staff['id'],opening_paise=req.opening_paise));return dict(id=id)

@router.post('/stores/{store_id}/shifts/{shift_id}/close')
def close_shift(store_id:str,shift_id:str,req:CloseShift,authorization:str=Header(default='')):
    with operation(store_id,authorization,write=True) as (c,s,staff):
        row=c.run('pos_api_close_shift_aggregate_pos_shifts', (shift_id, store_id)).fetchone()
        if not row:raise HTTPException(404,'Shift not found.')
        if row['staff_id']!=staff['id'] and staff['role']!='manager':raise HTTPException(403,'Only the cashier or manager can close this shift.')
        if row['closed']:return dict(row)
        expected=expected_cash(c,dict(row));variance=req.counted_paise-expected
        c.run('pos_api_close_shift_update_pos_shifts', (now(), req.counted_paise, expected, variance, shift_id))
        audit_event(c,store_id,'shift_closed',shift_id,dict(expected_paise=expected,counted_paise=req.counted_paise,variance_paise=variance,staff_id=staff['id']));return dict(expected_paise=expected,counted_paise=req.counted_paise,variance_paise=variance)

@router.post('/stores/{store_id}/shifts/{shift_id}/cash')
def cash_movement(store_id:str,shift_id:str,req:CashMovement,authorization:str=Header(default='')):
    with operation(store_id,authorization,True,True) as (c,s,staff):
        shift=require_shift(c,s,shift_id,staff)
        if req.amount_paise==0:raise ValueError('Cash movement must be nonzero.')
        if expected_cash(c,shift)+req.amount_paise<0:raise ValueError('Paid-out cash exceeds the recorded drawer balance.')
        id=uid();c.run('pos_api_cash_movement_insert_pos_cash_movements', (id, store_id, shift_id, req.amount_paise, req.reason, staff['id'], now()))
        audit_event(c,store_id,'cash_movement',id,req.model_dump());return dict(id=id)

@router.post('/stores/{store_id}/quote')
def cart_quote(store_id:str,req:Cart,authorization:str=Header(default='')):
    with operation(store_id,authorization) as (c,s,staff):return quote(c,s,req)

@router.post('/stores/{store_id}/checkout')
def checkout_sale(store_id:str,req:CheckoutInput,authorization:str=Header(default='')):
    with operation(store_id,authorization,write=True) as (c,s,staff):return complete_sale(c,s,staff,req)

@router.get('/stores/{store_id}/sales')
def sales(store_id:str,q:str='',start:str='',end:str='',offset:int=0,limit:int=50,authorization:str=Header(default='')):
    if not 0<=offset<=10000000 or not 1<=limit<=100:raise ValueError('Invalid transaction page.')
    for d in [start,end]:
        if d:date.fromisoformat(d)
    with operation(store_id,authorization) as (c,s,staff):
        params=(store_id,start,end,q[:100])
        count=c.run('pos_api_search_count',params).fetchone()['total']
        rows=[dict(r) for r in c.run('pos_api_search_sales',(*params,limit,offset))]

        return dict(rows=rows,total=count,offset=offset,limit=limit)

@router.get('/stores/{store_id}/sales/{sale_id}')
def transaction(store_id:str,sale_id:str,authorization:str=Header(default='')):
    with operation(store_id,authorization) as (c,s,staff):return sale_detail(c,store_id,sale_id)

@router.post('/stores/{store_id}/sales/{sale_id}/return-quote')
def return_quote(store_id:str,sale_id:str,req:ReturnQuote,authorization:str=Header(default='')):
    with operation(store_id,authorization,True) as (c,s,staff):return quote_return(c,s,sale_id,req)

@router.post('/stores/{store_id}/sales/{sale_id}/return')
def return_sale(store_id:str,sale_id:str,req:ReturnInput,authorization:str=Header(default='')):
    with operation(store_id,authorization,True,True) as (c,s,staff):return complete_return(c,s,staff,sale_id,req)

@router.get('/stores/{store_id}/carts')
def parked(store_id:str,authorization:str=Header(default='')):
    with operation(store_id,authorization) as (c,s,staff):return [{**dict(r),'body':json.loads(r['body'])} for r in c.run('pos_api_parked_aggregate_pos_carts', (store_id,))]

@router.post('/stores/{store_id}/carts')
def park(store_id:str,req:ParkCart,authorization:str=Header(default='')):
    with operation(store_id,authorization,write=True) as (c,s,staff):
        id=uid();c.run('pos_api_park_insert_pos_carts', (id, store_id, req.name, staff['id'], req.cart.model_dump_json(), now()));return dict(id=id)

@router.post('/stores/{store_id}/carts/{id}/remove')
def remove_cart(store_id:str,id:str,authorization:str=Header(default='')):
    with operation(store_id,authorization,write=True) as (c,s,staff):c.run('pos_api_remove_cart_delete_pos_carts', (id, store_id))
    return dict(status='removed')

@router.post('/stores/{store_id}/planning-connection')
def planning_connection(store_id:str,req:ConnectionInput,authorization:str=Header(default='')):
    with operation(store_id,authorization,True,True) as (c,s,staff):
        retailer_id=c.db.pos_stores.find_one({'id':store_id},session=c.session).get('retailer_id')
        if retailer_id:
            from .retail.repository import shop
            target=shop(req.workspace) if req.workspace else None
            if not target or target.get('pos_store_id')!=store_id or target['retailer_id']!=retailer_id or req.store_id!=target['store_code']:
                raise ValueError('Keep this checkout connected to its registered shop workspace.')
            return s
        if req.workspace:
            path,_=service().workspace_path(req.workspace)
            if not req.store_id:raise ValueError('Select the corresponding store in the planning workspace.')
            panel=read_frame(path / 'panel.parquet', columns=['store_id', 'product_id'])
            if req.store_id not in set(panel.store_id):raise ValueError('Store does not exist in this planning workspace.')
            quality=json.loads((path/'quality.json').read_text())
            if not s['demo'] and (req.workspace=='benchmark' or quality.get('currency','USD')!='INR' or quality.get('demo')):raise ValueError('A real INR store needs an observed client planning workspace in INR. Benchmark and demo-derived connections are demo-only.')
        if req.demo_reference_date and not s['demo']:raise ValueError('Real checkouts always use the current local business date.')
        if req.demo_reference_date:date.fromisoformat(req.demo_reference_date)
        c.run('pos_api_planning_connection_update_pos_stores', (req.workspace, req.store_id, req.demo_reference_date, store_id))
        audit_event(c,store_id,'planning_connection',store_id,req.model_dump());return store_row(c,store_id)

@router.get('/stores/{store_id}/audit')
def event_history(store_id:str,authorization:str=Header(default='')):
    with operation(store_id,authorization,True) as (c,s,staff):return [dict(r) for r in c.run('pos_api_event_history_aggregate_pos_events', (store_id,))]


class ProductBatch(StrictModel):
    products:list[ProductInput]=Field(min_length=1,max_length=1000)

@router.post('/stores/{store_id}/products/batch')
def product_batch(store_id:str,req:ProductBatch,authorization:str=Header(default='')):
    if len({p.product_id for p in req.products})!=len(req.products):raise ValueError('Duplicate product IDs in batch.')
    with operation(store_id,authorization,True,True) as (c,s,staff):
        for product in req.products:put_product(c,s,staff,product)
        c.db.pos_stores.update_one({'id':store_id},{'$inc':{'revision':1}},session=c.session)
        audit_event(c,store_id,'catalog_synchronized',store_id,dict(products=len(req.products)))
    return dict(products=len(req.products))

class RecordingInput(StrictModel):
    path:str=Field(pattern=r'^(/checkout|/sales/[A-Za-z0-9_-]+/return)$')
    body:dict
class RecordingClear(StrictModel):
    idempotency_key:str=Field(min_length=8,max_length=128)

@router.get('/stores/{store_id}/recording')
def pending_recording(store_id:str,authorization:str=Header(default='')):
    with operation(store_id,authorization) as (c,s,staff):
        row=c.db.operation_recovery.find_one({'store_id':store_id,'staff_id':staff['id']},session=c.session)
        return {'path':row['path'],'body':row['body']} if row else None

@router.post('/stores/{store_id}/recording')
def preserve_recording(store_id:str,req:RecordingInput,authorization:str=Header(default='')):
    (CheckoutInput if req.path=='/checkout' else ReturnInput)(**req.body)
    with operation(store_id,authorization,manager=req.path!='/checkout',write=True) as (c,s,staff):
        key={'store_id':store_id,'staff_id':staff['id']};old=c.db.operation_recovery.find_one(key,session=c.session)
        if old and (old['body']!=req.body or old['path']!=req.path):raise HTTPException(409,'Resolve the pending recording before starting another.')
        c.db.operation_recovery.update_one(key,{'$set':{'path':req.path,'body':req.body,'updated':now()}},upsert=True,session=c.session)
        return req.model_dump()

@router.post('/stores/{store_id}/recording/clear')
def clear_recording(store_id:str,req:RecordingClear,authorization:str=Header(default='')):
    with operation(store_id,authorization,write=True) as (c,s,staff):
        c.db.operation_recovery.delete_one({'store_id':store_id,'staff_id':staff['id'],'body.idempotency_key':req.idempotency_key},session=c.session)
        return {'cleared':True}

from .pos_ai import ShopperAssistInput,FraudRadarInput,assist_shopper,compute_upsell_nudges,run_fraud_radar

class NudgeInput(StrictModel):
    cart:dict[str,int]=Field(default_factory=dict)

@router.post('/stores/{store_id}/shopper/assist')
def shopper_assist(store_id:str,req:ShopperAssistInput,authorization:str=Header(default='')):
    with operation(store_id,authorization) as (c,s,staff):return assist_shopper(c,s,req)

@router.post('/stores/{store_id}/shopper/nudges')
def shopper_nudges(store_id:str,req:NudgeInput,authorization:str=Header(default='')):
    with operation(store_id,authorization) as (c,s,staff):
        products={r['product_id']:dict(r) for r in c.run('pos_core_quote_aggregate_pos_products', (store_id,))}
        offers,_=executed_offers(c,s)
        return {'nudges':compute_upsell_nudges(req.cart,products,offers)}

@router.post('/stores/{store_id}/fraud-radar')
def fraud_radar(store_id:str,req:FraudRadarInput,authorization:str=Header(default='')):
    with operation(store_id,authorization,True) as (c,s,staff):return run_fraud_radar(c,s,req)


