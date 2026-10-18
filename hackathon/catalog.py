"""Invented products and deterministic trading history; no customer data."""
from datetime import date,timedelta
import math
import numpy as np

CATALOG = [
 ('MILK','Fresh Milk 1L','Dairy',60,0),('OATS','Honey Oats 500g','Breakfast',180,500),
 ('COFFEE','Filter Coffee 200g','Beverages',240,1800),('RICE','Everyday Rice 5kg','Staples',320,500),
 ('OIL','Sunflower Oil 1L','Staples',160,500),('BISCUIT','Butter Biscuits 200g','Snacks',50,1800),
 ('JUICE','Mango Juice 1L','Beverages',100,1200),('NUTS','Roasted Almonds 200g','Snacks',220,500),
 ('CHIPS','Masala Chips 100g','Snacks',40,1200),('SOAP','Herbal Soap 100g','Personal Care',45,1800),
 ('ATTA','Whole Wheat Atta 5kg','Staples',260,500),('CEREAL','Crunchy Cereal 500g','Breakfast',210,1800),
]
STORE_CODE='CUP-BLR-01'
FLYER='FICTIONAL COMPETITOR FLYER — Weekend Match Day: 25% OFF Honey Oats 500g; 20% OFF Mango Juice 1L; 15% OFF Masala Chips 100g. Offers valid this weekend. Simulated prices and offers for the AI Builder Cup demonstration.'

def products():
 return [dict(product_id='MINT-'+sku,name=name,category=cat,price_paise=price*100,gst_bps=gst,
              base_price=round(price/(1+gst/10000),4),cost_paise=round(price/(1+gst/10000)*.58*100),
              on_hand=8 if sku=='OATS' else (12 if sku=='JUICE' else 160),reorder_level=25)
         for sku,name,cat,price,gst in CATALOG]

def history(kind,as_of=None):
 as_of=as_of or date.today();rng=np.random.default_rng(20261018)
 end=as_of-timedelta(days=1)
 if kind=='promotion':
  end-=timedelta(days=(end.weekday()-6)%7);periods=156;step=7
 else:periods=1092;step=1
 dates=[end-timedelta(days=step*(periods-1-i)) for i in range(periods)];rows=[]
 for j,p in enumerate(products()):
  for i,day in enumerate(dates):
   week=i if step==7 else i//7
   disc=[.10,.15,.20,.25][(week+j)%4] if week%14 in (5,6,7) else 0
   feature=int(disc>0 and (week+j)%2==0);display=int(disc>0 and (week+j)%3==0)
   season=1+.05*math.sin(i*2*math.pi/(52 if step==7 else 365)+j*.23)
   trend=1+.0005*week
   weekday=1 if step==7 else (1.35 if day.weekday()>=5 else .9)
   base=(65+j*5) if step==7 else (12+j)
   units=max(1,round(base*season*trend*weekday*(1+disc*3.5+.2*feature+.12*display)+rng.normal(0,3.0)))
   price=round(p['base_price']*(1-disc),4)
   rows.append(dict(date=day.isoformat(),store_id=STORE_CODE,product_id=p['product_id'],name=p['name'],category=p['category'],
    units=units,price=price,base_price=p['base_price'],feature=feature,display=display,promotion_available=int(disc>0),
    country='IN',currency='INR',stockout=0,holiday=0,unit_cost=p['cost_paise']/100,SPEND=round(units*price,4)))
 return rows
