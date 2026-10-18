from .storage import AssetPath,managed,read_frame,write_frame,load_model,dump_model
"""Reconciled business analytics, transaction exports and escaped printable receipts."""
from datetime import date,timedelta,datetime
from zoneinfo import ZoneInfo
import csv,io,json,html
from fastapi import APIRouter,Header,HTTPException
from fastapi.responses import Response
from .pos_api import operation
from .pos_core import local_day
from .pos_returns import sale_detail

from .pg import MongoRoute
router=APIRouter(route_class=MongoRoute,prefix='/api/pos',tags=['OneSales analytics'])

def period(store,start,end):
    end=date.fromisoformat(end or local_day(store));start=date.fromisoformat(start) if start else end-timedelta(days=29)
    if start>end or (end-start).days>365:raise ValueError('Choose an ordered date range of at most 366 days.')
    return start.isoformat(),end.isoformat()

def analytics(con,store,start='',end=''):
    start,end=period(store,start,end);args=(store['id'],start,end)
    sales=con.run('pos_reports_analytics_aggregate_pos_sales_3', args).fetchone()
    returns=con.run('pos_reports_analytics_aggregate_pos_returns_3', args).fetchone()
    losses=con.run('pos_reports_analytics_aggregate_pos_inventory_movements_3', args).fetchone()[0]
    sold=con.run('pos_reports_analytics_aggregate_pos_sale_lines_3', args).fetchone()[0]
    returned=con.run('pos_reports_analytics_aggregate_pos_return_lines_3', args).fetchone()[0]
    revenue=sales['revenue']-returns['net'];cogs=sales['cost']-returns['restock'];profit=revenue-cogs-losses
    daily={d:dict(date=d,sales_paise=0,refunds_paise=0,net_sales_paise=0,contribution_paise=0,transactions=0) for d in [(date.fromisoformat(start)+timedelta(days=i)).isoformat() for i in range((date.fromisoformat(end)-date.fromisoformat(start)).days+1)]}
    for r in con.run('pos_reports_analytics_aggregate_pos_sales', args):daily[r['business_date']].update(sales_paise=r['net'],transactions=r['n'],net_sales_paise=r['net'],contribution_paise=r['net']-r['cost'])
    for r in con.run('pos_reports_analytics_aggregate_pos_returns', args):
        row=daily[r['business_date']];row['refunds_paise']=r['net'];row['net_sales_paise']-=r['net'];row['contribution_paise']-=r['net']-r['cost']
    for r in con.run('pos_reports_analytics_aggregate_pos_inventory_movements', args):daily[r['business_date']]['contribution_paise']-=r['loss']
    products={r['product_id']:{**dict(r),'units_sold':0,'return_units':0,'sales_paise':0,'refund_paise':0,'cost_paise_total':0,'restock_cost_paise':0,'discount_paise':0} for r in con.run('pos_reports_analytics_aggregate_pos_products', (store['id'],))}
    for r in con.run('pos_reports_analytics_aggregate_pos_sale_lines', args):products[r['product_id']].update(units_sold=r['units'],sales_paise=r['net'],cost_paise_total=r['cost'],discount_paise=r['discounts'])
    for r in con.run('pos_reports_analytics_aggregate_pos_return_lines', args):products[r['product_id']].update(return_units=r['qty'],refund_paise=r['net'],restock_cost_paise=r['cost'])
    categories={}
    writeoffs={r['product_id']:r['loss'] for r in con.run('pos_reports_analytics_aggregate_pos_inventory_movements_2', args)}
    for p in products.values():
        p['net_sales_paise']=p['sales_paise']-p['refund_paise'];p['contribution_paise']=p['net_sales_paise']-p['cost_paise_total']+p['restock_cost_paise']
        p['stock_writeoff_paise']=writeoffs.get(p['product_id'],0);p['contribution_paise']-=p['stock_writeoff_paise']
        p['margin_rate']=p['contribution_paise']/p['net_sales_paise'] if p['net_sales_paise']>0 else None
        days=(date.fromisoformat(end)-date.fromisoformat(start)).days+1;p['days_of_stock']=p['on_hand']/(p['units_sold']/days) if p['units_sold'] else None
        group=categories.setdefault(p['category'],dict(category=p['category'],net_sales_paise=0,contribution_paise=0,units=0))
        group['net_sales_paise']+=p['net_sales_paise'];group['contribution_paise']+=p['contribution_paise'];group['units']+=p['units_sold']
    taxes=[dict(r) for r in con.run('pos_reports_analytics_aggregate_pos_sale_lines_2', args)]
    taxmap={r['gst_bps']:r for r in taxes}
    # Credit notes retain the original rate and component amounts.
    for r in con.run('pos_reports_analytics_aggregate_pos_return_lines_2', args):
        rate=next(l['gst_bps'] for l in json.loads(r['snapshot'])['lines'] if l['product_id']==r['product_id'])
        row=taxmap.setdefault(rate,dict(gst_bps=rate,taxable_paise=0,cgst_paise=0,sgst_paise=0,igst_paise=0));row['taxable_paise']-=r['net_paise']
        for k in ['cgst_paise','sgst_paise','igst_paise']:row[k]-=r[k]
    payments={k:dict(method=k,collected_paise=0,refunded_paise=0) for k in ['cash','external_card','external_upi']};hours={h:dict(hour=h,transactions=0,sales_paise=0) for h in range(24)}
    for r in con.run('pos_reports_analytics_aggregate_pos_sales_2', args):
        sale=json.loads(r['snapshot'])
        for t in sale['tenders']:payments[t['method']]['collected_paise']+=t['amount_paise']
        payments['cash']['collected_paise']-=sale['change_paise'];h=datetime.fromisoformat(r['created']).astimezone(ZoneInfo(store['timezone'])).hour;hours[h]['transactions']+=1;hours[h]['sales_paise']+=sale['subtotal_paise']
    for r in con.run('pos_reports_analytics_aggregate_pos_returns_2', args):
        for t in json.loads(r['snapshot'])['tenders']:payments[t['method']]['refunded_paise']+=t['amount_paise']
    for p in payments.values():p['net_paise']=p['collected_paise']-p['refunded_paise']
    promotions=[dict(r) for r in con.run('pos_reports_analytics_aggregate_pos_sale_offers', args)]
    customer_rows=[dict(r) for r in con.run('pos_reports_analytics_aggregate_pos_sales_4', args)]
    for row in customer_rows:row['refund_paise']=con.run('pos_reports_analytics_aggregate_pos_returns_4', (*args, row['id'])).fetchone()[0];row['net_spent_paise']=row['spent_paise']-row['refund_paise']
    shifts=[dict(r) for r in con.run('pos_reports_analytics_aggregate_pos_shifts', args)]
    active=list(products.values());low=[p for p in active if p['on_hand']<=p['reorder_level']];best=sorted(active,key=lambda p:-p['net_sales_paise'])
    insights=[]
    if not sales['transactions']:insights.append('No completed sales in this period. Trading data will populate the dashboard as checkout is used.')
    if low:insights.append(f'{len(low)} products are at or below their reorder level. Review receipts and demand before buying more.')
    losses_count=sum(p['contribution_paise']<0 for p in active)
    if losses_count:insights.append(f'{losses_count} products have negative contribution in this period. Check prices, discounts, returns and landed costs.')
    if any(r['variance_paise'] for r in shifts if r['closed']):insights.append('Closed register shifts contain cash differences. Review cash movements and payment references.')
    return dict(currency='INR',demo=bool(store['demo']),start=start,end=end,kpis=dict(transactions=sales['transactions'],gross_sales_ex_gst_paise=sales['revenue'],refunds_ex_gst_paise=returns['net'],net_sales_paise=revenue,gst_paise=sales['gst']-returns['gst'],net_collected_paise=sales['collected']-returns['gross'],contribution_paise=profit,margin_rate=profit/revenue if revenue>0 else None,cogs_paise=cogs,discount_paise=sales['discounts'],units_sold=sold,units_returned=returned,return_documents=returns['documents'],return_value_rate=returns['net']/sales['revenue'] if sales['revenue'] else None,average_basket_paise=sales['collected']/sales['transactions'] if sales['transactions'] else 0,units_per_basket=sold/sales['transactions'] if sales['transactions'] else 0,identified_customers=sales['customers'],inventory_cost_paise=sum(p['on_hand']*p['cost_paise'] for p in active),low_stock_products=len(low),discarded_returns_cost_paise=returns['discard'],stock_writeoff_paise=losses),daily=list(daily.values()),products=best,categories=sorted(categories.values(),key=lambda r:-r['net_sales_paise']),payments=list(payments.values()),hourly=list(hours.values()),taxes=list(taxmap.values()),promotions=promotions,customers=customer_rows,shifts=shifts,insights=insights,definitions=['Net sales exclude GST and subtract refunds recorded during the selected period.','Contribution deducts sold-product cost, reverses restocked return cost and deducts stock writeoffs. It excludes rent, payroll, processing fees and other overheads.','Returned goods discarded retain their original cost expense. Returns can refer to sales before this period.','Offer discounts and associated transactions are observed execution; they are not causal ROI or incremental sales estimates.','Days of stock uses observed sales over this range, not the ML demand forecast.'])

@router.get('/stores/{store_id}/analytics')
def dashboard(store_id:str,start:str='',end:str='',authorization:str=Header(default='')):
    with operation(store_id,authorization,True) as (c,s,staff):return analytics(c,s,start,end)

def csv_response(rows,fields,filename):
    buf=io.StringIO();writer=csv.DictWriter(buf,fieldnames=fields,extrasaction='ignore');writer.writeheader()
    for row in rows:writer.writerow({k:("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v) for k,v in row.items()})
    return Response(buf.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="{filename}"'})

@router.get('/stores/{store_id}/export/{kind}')
def export(store_id:str,kind:str,start:str='',end:str='',authorization:str=Header(default='')):
    with operation(store_id,authorization,True) as (c,s,staff):
        start,end=period(s,start,end)
        if kind in ['products','daily','taxes','payments']:
            report=analytics(c,s,start,end);rows=report[kind];fields=list(rows[0]) if rows else ['date'];return csv_response(rows,fields,f'aislemint-onesales-{kind}.csv')
        if kind=='transactions':
            rows=[dict(r) for r in c.run('pos_reports_export_aggregate_pos_sales', (store_id, start, end))]
            return csv_response(rows,['number','business_date','created','subtotal_paise','tax_paise','total_paise','discount_paise','cash_net_paise'],'aislemint-onesales-transactions.csv')
        if kind=='returns':
            rows=[dict(r) for r in c.run('pos_reports_export_aggregate_pos_returns', (store_id, start, end))]
            return csv_response(rows,['number','original_invoice','business_date','net_paise','tax_paise','total_paise','restock_cost_paise','discard_cost_paise'],'aislemint-onesales-returns.csv')
        raise HTTPException(404,'Unknown export.')

def receipt_html(record,credit=False):
    e=lambda value:html.escape(str(value or ''));money=lambda n:f'₹{n/100:,.2f}'
    store=record['store'];customer=record.get('customer');demo=bool(store['demo'])
    title='Credit note' if credit else 'Tax invoice' if store['gst_registered'] else 'Sales receipt'
    rows=''.join(f'<tr><td>{e(l["name"])}<small>{e(l["product_id"])} · HSN {e(l["hsn"])}<br>Net {money(l["net_paise"])} · GST {l["gst_bps"]/100:g}%: {money(l["tax_paise"])}</small></td><td>{l["quantity"]} {e(l.get("unit","NOS"))}</td><td>{money(l["paid_paise"])}</td></tr>' for l in record['lines'])
    taxes={k:sum(l[k] for l in record['lines']) for k in ['cgst_paise','sgst_paise','igst_paise']}
    taxrows=''.join(f'<div><span>{k.split("_")[0].upper()}</span><b>{money(v)}</b></div>' for k,v in taxes.items() if v)
    offers='' if credit else ''.join(f'<li>{e(o["kind"].upper())}: {money(o["saving_paise"])}</li>' for o in record['applied_promotions'])
    payments=''.join(f'<div><span>{e(t["method"].replace("external_","External ").replace("_"," "))}</span><b>{money(t["amount_paise"])}</b></div>'+ (f'<small>Reference {e(t["reference"])}</small>' if t['reference'] else '') for t in record['tenders'])
    recipient='' if not customer else f'<p>Customer: {e(customer["name"])}<br>{e(customer.get("phone"))}<br>{e(customer.get("address"))}'+(f'<br>GSTIN: {e(customer["gstin"])}' if customer.get('gstin') else '')+'</p>'
    original=f'<p>Original invoice: {e(record["original_number"])}<br>Invoice date: {e(record["original_date"])}</p>' if credit else ''
    footer='Refund recorded; external-terminal refunds are performed outside OneSales.' if credit else record['return_policy']
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{e(record['number'])} · AisleMint OneSales</title><style>@page{{size:80mm auto;margin:5mm}}*{{box-sizing:border-box}}body{{max-width:72mm;margin:15px auto;font:12px/1.45 system-ui;color:#111}}header{{text-align:center}}h1{{font-size:20px;margin:4px}}h2{{font-size:14px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:7px 2px;text-align:left;border-bottom:1px dashed #bbb;vertical-align:top}}td:last-child,th:last-child{{text-align:right;white-space:nowrap}}small{{display:block;font-size:10px;color:#444}}.totals>div{{display:flex;justify-content:space-between;padding:3px 0}}.grand{{font-size:18px;border-top:1px solid;padding-top:8px!important}}.demo{{border:2px solid;padding:8px;font-weight:800}}footer{{border-top:1px dashed;padding-top:10px;margin-top:16px;text-align:center}}button{{padding:10px;width:100%;margin-bottom:14px}}@media print{{body{{margin:0}}button{{display:none}}}}</style></head><body><button onclick="window.print()">Print receipt / Save PDF</button><header>{'<p class="demo">DEMO · NOT A TAX DOCUMENT</p>' if demo else ''}<h1>{e(store['name'])}</h1><p>{e(store['address'])}<br>State {e(store['state_code'])}</p>{'<p>GSTIN '+e(store['gstin'])+'</p>' if store['gst_registered'] else ''}<h2>{title} · {e(record['number'])}</h2><small>{e(datetime.fromisoformat(record['created']).astimezone(ZoneInfo(store['timezone'])).strftime('%d %b %Y, %H:%M %Z'))}</small></header>{original}{recipient}<p>Place of supply: {e(record['place_of_supply'])}<br>Cashier: {e(record['cashier']['name'])}</p><table><thead><tr><th>Item</th><th>Qty</th><th>Amount</th></tr></thead><tbody>{rows}</tbody></table>{'<ul>'+offers+'</ul>' if offers else ''}<section class="totals"><div><span>Taxable / net value</span><b>{money(record['subtotal_paise'])}</b></div>{taxrows}<div class="grand"><span>{'Refund' if credit else 'Total'}</span><b>{money(record['total_paise'])}</b></div>{payments}{'<div><span>Change</span><b>'+money(record.get('change_paise',0))+'</b></div>' if not credit else ''}</section><footer>{e(footer)}<p>Reverse charge: No</p><p>Authorised representative: __________________</p><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 38" width="22" height="24" aria-hidden="true"><path fill="currentColor" d="M4 30 14 6h8L12 30z m15-10 5-12 9 22H23z M2 33h31v3H2z"/></svg><small>Powered by AisleMint OneSales · Prices include configured GST · INR</small></footer></body></html>'''

@router.get('/stores/{store_id}/sales/{sale_id}/receipt')
def print_sale(store_id:str,sale_id:str,authorization:str=Header(default='')):
    with operation(store_id,authorization) as (c,s,staff):return Response(receipt_html(sale_detail(c,store_id,sale_id)),media_type='text/html')

@router.get('/stores/{store_id}/returns/{return_id}/receipt')
def print_return(store_id:str,return_id:str,authorization:str=Header(default='')):
    with operation(store_id,authorization) as (c,s,staff):
        row=c.run('pos_reports_print_return_aggregate_pos_returns', (return_id, store_id)).fetchone()
        if not row:raise HTTPException(404,'Credit note not found.')
        return Response(receipt_html(json.loads(row['snapshot']),True),media_type='text/html')
