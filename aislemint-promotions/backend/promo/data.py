from __future__ import annotations
from .storage import AssetPath,managed,read_frame,write_frame,load_model,dump_model
import hashlib
import io
import json
from pathlib import Path
import numpy as np
import pandas as pd
import openpyxl

from .runtime import ROOT
DATA = AssetPath('data')
ARTIFACTS = AssetPath('artifacts')
REQUIRED = ['date', 'store_id', 'product_id', 'units', 'price', 'base_price', 'feature', 'display', 'category', 'name']

def validate_panel(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    missing = sorted(set(REQUIRED) - set(df.columns))
    if missing: raise ValueError('Missing columns: ' + ', '.join(missing))
    df = df.copy()
    df['date'] = pd.to_datetime(df.date, errors='raise').dt.normalize()
    if df[REQUIRED].isna().any().any(): raise ValueError('Required fields contain empty values.')
    for c in ['store_id', 'product_id', 'category', 'name']:
        df[c] = df[c].astype(str).str.strip()
        if df[c].eq('').any(): raise ValueError(f'{c} contains blank values.')
    for c in ['units', 'price', 'base_price', 'feature', 'display']:
        df[c] = pd.to_numeric(df[c], errors='raise')
        if not np.isfinite(df[c]).all(): raise ValueError(f'{c} contains non-finite values.')
    free=pd.to_numeric(df.get('free_promotion',pd.Series(0,index=df.index)),errors='raise')
    if not free.isin([0,1]).all():raise ValueError('free_promotion must be 0 or 1.')
    if (df.units < 0).any() or ((df.price<=0)&~(df.price.eq(0)&free.eq(1)&df.units.gt(0))).any() or (df.base_price <= 0).any():
        raise ValueError('Units must be nonnegative; prices must be positive.')
    if not df.feature.isin([0, 1]).all() or not df.display.isin([0, 1]).all():
        raise ValueError('Feature and display must be 0 or 1.')
    if df.duplicated(['date', 'store_id', 'product_id']).any():
        raise ValueError('Duplicate product/store/week keys. Aggregate before importing.')
    if df.groupby('product_id').category.nunique().max() > 1:
        raise ValueError('A product must map to a single category.')
    if (df.price > df.base_price * 1.01).any():
        raise ValueError('Selling price exceeds base price by more than 1%; correct the base price.')
    dates = sorted(df.date.unique())
    if len(dates) < 78: raise ValueError('At least 78 distinct weeks are needed for training and time-based validation.')
    if not (np.diff(np.array(dates, dtype='datetime64[D]')).astype(int) == 7).all():
        raise ValueError('Dates must share a weekly cadence with no missing calendar weeks.')
    if df.product_id.nunique() > 250 or df.store_id.nunique() > 250:
        raise ValueError('Local model supports up to 250 products and 250 stores per workspace; partition larger imports.')
    if (df.groupby(['store_id','product_id']).size() < 26).any():
        raise ValueError('Each store/product series needs at least 26 observed weeks.')
    if 'SPEND' in df:
        df['SPEND']=pd.to_numeric(df['SPEND'],errors='raise')
        if not np.isfinite(df['SPEND']).all() or (df['SPEND']<0).any():raise ValueError('SPEND must contain nonnegative realized sales values.')
    df['discount'] = (1 - df.price / df.base_price).clip(0, 1)
    df['promoted'] = ((df.discount >= .02) | df.feature.eq(1) | df.display.eq(1)).astype(int)
    if 'promotion_available' in df:
        df['promotion_available']=pd.to_numeric(df.promotion_available,errors='raise')
        if not df.promotion_available.isin([0,1]).all():raise ValueError('promotion_available must be 0 or 1.')
        df['promoted']=np.maximum(df.promoted,df.promotion_available).astype(int)
    df['week'] = ((df.date - df.date.min()).dt.days // 7).astype(int)
    df = df.sort_values(['store_id', 'product_id', 'date']).reset_index(drop=True)
    if df.promoted.mean() < .01 or df.promoted.mean() > .95:
        raise ValueError('Insufficient mix of promoted and non-promoted observations.')
    report = dict(rows=len(df), products=df.product_id.nunique(), stores=df.store_id.nunique(),
                  weeks=len(dates), start=str(df.date.min().date()), end=str(df.date.max().date()),
                  promo_share=float(df.promoted.mean()), missing_panel_rows=int(len(dates)*df.groupby(['store_id','product_id']).ngroups-len(df)),
                  **market_metadata(df), missing_inventory='stockout' not in df.columns, missing_costs='unit_cost' not in df.columns)
    if 'unit_cost' in df:
        df['unit_cost'] = pd.to_numeric(df.unit_cost, errors='raise')
        if not np.isfinite(df.unit_cost).all() or (df.unit_cost < 0).any(): raise ValueError('Invalid unit costs.')
    return df, report

def market_metadata(df):
    result={}
    for field,default,allowed in [('currency','USD',{'USD','INR'}),('country','US',{'US','IN'})]:
        values=df[field].astype(str).str.upper().unique().tolist() if field in df else [default]
        if len(values)!=1 or values[0] not in allowed:raise ValueError(f'Use a single supported {field}: '+', '.join(sorted(allowed)))
        result[field]=values[0]
    if (result['currency'],result['country']) not in [('USD','US'),('INR','IN')]:raise ValueError('Supply matching country and currency columns (IN/INR or US/USD).')
    return result

def prepare_breakfast() -> dict:
    dest=DATA/'processed';dest.mkdir(parents=True,exist_ok=True)
    if (dest/'source_panel.parquet').exists():
        df=read_frame(dest / 'source_panel.parquet')
        stores=pd.DataFrame(json.loads((dest/'stores.json').read_text()))
    else:
        book = openpyxl.load_workbook(io.BytesIO((DATA/'raw/breakfast/dunnhumby - Breakfast at the Frat.xlsx').read_bytes()),read_only=True,data_only=True)
        def table(name, n):
            it=book[name].iter_rows(min_row=2,max_col=n,values_only=True)
            names=next(it)
            return pd.DataFrame((r for r in it if r[0] is not None),columns=names)
        products=table('dh Products Lookup',6)
        stores=table('dh Store Lookup',9)
        df=table('dh Transaction Data',12)
        book.close()
        df=df.merge(products,on='UPC',validate='many_to_one')
        write_frame(df, dest / 'source_panel.parquet', index=False)
        (dest/'stores.json').write_text(stores.to_json(orient='records'))
    df['DESCRIPTION']=df['DESCRIPTION'].str.strip()+' · '+df['PRODUCT_SIZE'].fillna('').astype(str).str.strip()
    df=df.rename(columns={'WEEK_END_DATE':'date','STORE_NUM':'store_id','UPC':'product_id','UNITS':'units','PRICE':'price','BASE_PRICE':'base_price','FEATURE':'feature','DISPLAY':'display','CATEGORY':'category','DESCRIPTION':'name'})
    # No zero-sales rows are fabricated for absent observations.
    invalid=df[REQUIRED].isna().any(axis=1)|(df.price<=0)|(df.base_price<=0)|(df.units<0)|(df.price>df.base_price*1.01)
    excluded=int(invalid.sum());df=df.loc[~invalid].copy()
    sparse=df.groupby(['store_id','product_id']).units.transform('size')<26
    excluded+=int(sparse.sum());df=df.loc[~sparse].copy()
    print('Null fields:',df[REQUIRED].isna().sum().to_dict(),flush=True)
    df,report=validate_panel(df)
    write_frame(df, dest / 'panel.parquet', index=False)
    (dest/'stores.json').write_text(stores.to_json(orient='records'))
    report.update(raw_rows=int(len(read_frame(dest / 'source_panel.parquet'))), source='dunnhumby — Breakfast at the Frat',source_url='https://www.dunnhumby.com/source-files/',excluded_invalid_rows=excluded,
        raw_sha256=hashlib.sha256((DATA/'raw/breakfast.zip').read_bytes()).hexdigest(),
        scope='Historical benchmark; public data is not a client production feed.',
        rights='Source Files benchmark. No commercial redistribution rights verified; obtain permission before redistribution.')
    (dest/'quality.json').write_text(json.dumps(report,indent=2))
    return report

def prepare_baskets() -> dict:
    import duckdb
    con=duckdb.connect();con.execute("SET memory_limit='1GB'");con.execute('SET threads=4');con.execute("SET temp_directory=''")
    from .storage import read_csv_reference
    transactions=read_csv_reference(DATA/'raw/journey/transaction_data.csv',usecols=['BASKET_ID','household_key','PRODUCT_ID','QUANTITY','SALES_VALUE'])
    products=read_csv_reference(DATA/'raw/journey/product.csv',usecols=['PRODUCT_ID','COMMODITY_DESC'])
    con.register('tx',transactions);con.register('products',products)
    totals=con.execute('SELECT count(*), count(distinct BASKET_ID), count(distinct household_key), count(distinct PRODUCT_ID) FROM tx').fetchone()
    con.execute('''CREATE TEMP TABLE basket_categories AS SELECT DISTINCT BASKET_ID, COMMODITY_DESC AS category
        FROM tx JOIN products USING(PRODUCT_ID) WHERE QUANTITY>0 AND SALES_VALUE>0''')
    con.execute('CREATE TEMP TABLE topcats AS SELECT category,count(*) n FROM basket_categories GROUP BY category ORDER BY n DESC LIMIT 35')
    basket_count=con.execute('SELECT count(distinct BASKET_ID) FROM basket_categories').fetchone()[0]
    pairs=con.execute('''SELECT a.category category_a,b.category category_b,count(*) baskets,
        count(*)*?::DOUBLE/any_value(ca.n)/any_value(cb.n) association_lift
        FROM basket_categories a JOIN basket_categories b USING(BASKET_ID)
        JOIN topcats ca ON a.category=ca.category JOIN topcats cb ON b.category=cb.category
        WHERE a.category<b.category GROUP BY a.category,b.category HAVING count(*)>=500
        ORDER BY association_lift DESC LIMIT 30''',[basket_count]).fetchdf()
    report=dict(source='dunnhumby — The Complete Journey',rows=totals[0],baskets=totals[1],households=totals[2],products=totals[3],
                relationships=pairs.to_dict('records'),interpretation='Basket association suggests complements; it does not measure causal halo. Product identifiers cannot be joined to Breakfast at the Frat.',
                raw_sha256=hashlib.sha256((DATA/'raw/journey.zip').read_bytes()).hexdigest())
    (DATA/'processed/baskets.json').write_text(json.dumps(report,indent=2));con.close();return report
