from .storage import AssetPath,managed,read_frame,write_frame,load_model,dump_model
"""Append-only returns and bounded original-payment refunds."""
import json
from typing import Literal
from pydantic import Field
from fastapi import HTTPException
from .pos_core import StrictModel,Tender,digest,uid,now,local_day,require_shift,price_basket,next_number,stock_move,audit_event,expected_cash

class ReturnLine(StrictModel):
    product_id:str
    quantity:int=Field(ge=1,le=500,strict=True)
    disposition:Literal['restock','discard']='restock'
    reason:str=Field(min_length=3,max_length=200)
class ReturnQuote(StrictModel):
    lines:list[ReturnLine]=Field(min_length=1,max_length=60)
class ReturnInput(ReturnQuote):
    shift_id:str
    quote_token:str=Field(pattern=r'^[a-f0-9]{64}$')
    idempotency_key:str=Field(min_length=8,max_length=128,pattern=r'^[A-Za-z0-9_-]+$')
    tenders:list[Tender]=Field(default_factory=list,max_length=6)

def sale_detail(con,store_id,sale_id):
    row=con.run('pos_returns_sale_detail_aggregate_pos_sales', (sale_id, store_id)).fetchone()
    if not row:raise HTTPException(404,'Transaction not found in this store.')
    result=json.loads(row['snapshot']);returned={r['product_id']:r['returned'] for r in con.run('pos_returns_sale_detail_aggregate_pos_sale_lines', (sale_id,))}
    result['lines']=[{**l,'returned':returned[l['product_id']],'returnable':l['quantity']-returned[l['product_id']]} for l in result['lines']]
    result['returns']=[json.loads(r['snapshot']) for r in con.run('pos_returns_sale_detail_aggregate_pos_returns', (sale_id,))]
    result['refunded_paise']=sum(r['total_paise'] for r in result['returns'])
    result['status']='returned' if all(l['returnable']==0 for l in result['lines']) else 'partially_returned' if result['returns'] else 'completed'
    return result

def quote_return(con,store,sale_id,request):
    sale=sale_detail(con,store['id'],sale_id);original={l['product_id']:l for l in sale['lines']};quantities={};lines=[]
    for line in request.lines:quantities[line.product_id]=quantities.get(line.product_id,0)+line.quantity
    if any(p not in original or q>original[p]['returnable'] for p,q in quantities.items()):raise HTTPException(409,'Return quantity exceeds the unreturned units on this invoice.')
    allocated={p:original[p]['returned'] for p in quantities}
    for request_line in request.lines:
        p=request_line.product_id;l=original[p];before=allocated[p];after=before+request_line.quantity;allocated[p]=after
        amounts={key:l[key]*after//l['quantity']-l[key]*before//l['quantity'] for key in ['net_paise','cgst_paise','sgst_paise','igst_paise']}
        amounts['tax_paise']=sum(amounts[k] for k in ['cgst_paise','sgst_paise','igst_paise']);amounts['paid_paise']=amounts['net_paise']+amounts['tax_paise']
        lines.append(dict(**request_line.model_dump(),name=l['name'],hsn=l['hsn'],unit=l.get('unit','NOS'),gst_bps=l['gst_bps'],cost_paise=l['cost_paise'],**amounts))
    total=sum(l['paid_paise'] for l in lines)
    remaining={p:l['returnable']-quantities.get(p,0) for p,l in original.items() if l['returnable']-quantities.get(p,0)>0}
    products={p:dict(price_paise=l['unit_price_paise'],gst_bps=l['gst_bps']) for p,l in original.items()}
    prices,_,_=price_basket(remaining,products,sale['promotion_terms'],sale.get('selected_rewards',{}))
    retained_paid=sale['total_paise']-sale['refunded_paise']-total
    if sum(map(sum,prices.values()))>retained_paid+len(original):raise HTTPException(409,'This partial return breaks the original promotion condition. Include the linked free reward or the remaining bundle items in this return.')
    if total>sale['total_paise']-sale['refunded_paise']:raise HTTPException(409,'Refund would exceed the remaining amount paid.')
    methods={}
    for t in sale['tenders']:methods[t['method']]=methods.get(t['method'],0)+t['amount_paise']
    methods['cash']=methods.get('cash',0)-sale['change_paise']
    for r in sale['returns']:
        for t in r['tenders']:methods[t['method']]-=t['amount_paise']
    result=dict(sale_id=sale_id,original_number=sale['number'],original_date=sale['business_date'],customer=sale['customer'],store=sale['store'],place_of_supply=sale['place_of_supply'],lines=lines,total_paise=total,subtotal_paise=sum(l['net_paise'] for l in lines),tax_paise=sum(l['tax_paise'] for l in lines),refundable_methods={k:v for k,v in methods.items() if v>0},already_refunded_paise=sale['refunded_paise'],currency='INR',restock_units=sum(l['quantity'] for l in lines if l['disposition']=='restock'),discard_units=sum(l['quantity'] for l in lines if l['disposition']=='discard'))
    result['quote_token']=digest(result);return result

def complete_return(con,store,staff,sale_id,request):
    request_hash=digest(dict(sale_id=sale_id,**request.model_dump()))
    old=con.run('pos_returns_complete_return_aggregate_pos_returns', (store['id'], request.idempotency_key)).fetchone()
    if old:
        if old['request_hash']!=request_hash:raise HTTPException(409,'Return retry key was used with different inputs.')
        return {**json.loads(old['snapshot']),'replayed':True}
    shift=require_shift(con,store,request.shift_id,staff);review=quote_return(con,store,sale_id,request)
    if request.quote_token!=review['quote_token']:raise HTTPException(409,'Return availability changed. Review the refund again.')
    amounts={}
    for tender in request.tenders:amounts[tender.method]=amounts.get(tender.method,0)+tender.amount_paise
    if sum(amounts.values())!=review['total_paise']:raise ValueError('Refund payments must equal the reviewed refund total.')
    if any(v>review['refundable_methods'].get(k,0) for k,v in amounts.items()):raise ValueError('Refund to the original payment methods within their remaining paid amounts.')
    if amounts.get('cash',0)>expected_cash(con,shift):raise ValueError('This register has insufficient recorded cash for the refund. Record a manager-authorized cash paid-in first.')
    id=uid();day=local_day(store);number=next_number(con,store['id'],day,'CN');created=now()
    restock=sum(l['cost_paise']*l['quantity'] for l in review['lines'] if l['disposition']=='restock');discard=sum(l['cost_paise']*l['quantity'] for l in review['lines'] if l['disposition']=='discard')
    result={**review,'id':id,'number':number,'created':created,'business_date':day,'cashier':dict(id=staff['id'],name=staff['name']),'tenders':[t.model_dump() for t in request.tenders],'replayed':False}
    con.run('pos_returns_complete_return_insert_pos_returns', (id, store['id'], sale_id, number, request.shift_id, staff['id'], day, created, review['subtotal_paise'], review['tax_paise'], review['total_paise'], amounts.get('cash', 0), restock, discard, request_hash, request.idempotency_key, json.dumps(result)))
    for index,line in enumerate(review['lines']):
        con.run('pos_returns_complete_return_insert_pos_return_lines', (id, index, store['id'], line['product_id'], line['quantity'], line['disposition'], line['net_paise'], line['tax_paise'], line['cgst_paise'], line['sgst_paise'], line['igst_paise'], line['cost_paise']))
        con.run('pos_returns_complete_return_update_pos_sale_lines', (line['quantity'], sale_id, line['product_id']))
        stock_move(con,store,line['product_id'],line['quantity'] if line['disposition']=='restock' else 0,'return_'+line['disposition'],id,line['reason'],staff['id'],line['cost_paise'])
    audit_event(con,store['id'],'return_completed',id,dict(sale_id=sale_id,refund_paise=review['total_paise'],restock_units=review['restock_units'],discard_units=review['discard_units'],staff_id=staff['id']))
    return result
