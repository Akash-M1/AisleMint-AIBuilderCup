"""Copy enumerated public fixture blobs from a read-only source to local test PostgreSQL.
Cloud SQL is the CI source; Atlas remains an explicit migration-era fallback.
No source writes, operational customer rows, or local dataset files are used.
"""
from pathlib import Path
import sys,os,hashlib,gzip
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'aislemint-ops/backend'))
from dotenv import dotenv_values
from pymongo import MongoClient
from gridfs import GridFSBucket
import certifi
import psycopg
from psycopg.conninfo import conninfo_to_dict
KEYS=['data/processed/panel.parquet','data/processed/quality.json','data/processed/baskets.json','data/processed/stores.json','data/reference/us_holidays_2004_2021.csv']
KEYS += [f'artifacts/{prefix}{name}' for prefix in ['','demand/','offer-mechanics/'] for name in ['model.joblib','model_card.json']]
KEYS += ['artifacts/evaluation.parquet','artifacts/demand/evaluation.parquet','artifacts/offer-mechanics/training_data.parquet']

def main():
    target=os.environ['TEST_DATABASE_URL'];info=conninfo_to_dict(target)
    if info.get('host') not in ('127.0.0.1','localhost') or info.get('dbname')!='aislemint_test':
        raise SystemExit('Fixture target must be the loopback aislemint_test database.')
    fixture=dotenv_values(Path(__file__).resolve().parents[2]/'aislemint-ops/.env.fixture')
    pg_source=os.environ.get('FIXTURE_DATABASE_URL')
    if pg_source:
        from psycopg.rows import dict_row
        source=psycopg.connect(pg_source,row_factory=dict_row,connect_timeout=15,options='-c default_transaction_read_only=on')
    else:
        uri=os.environ.get('MONGODB_URI') or fixture.get('MONGODB_URI');name=os.environ.get('MONGODB_DATABASE') or fixture.get('MONGODB_DATABASE')
        if not uri or not name:raise SystemExit('Configure a read-only FIXTURE_DATABASE_URL or the transitional Atlas fixture credential.')
        source=MongoClient(uri,tlsCAFile=certifi.where(),serverSelectionTimeoutMS=8000)[name]
    os.environ['DATABASE_URL']=target
    from promo.pg import initialize,pool
    from promo.storage import publish
    initialize();bucket=None if pg_source else GridFSBucket(source)
    for key in KEYS:
        meta=source.execute("SELECT * FROM assets WHERE key=%s AND kind='file'",(key,)).fetchone() if pg_source else source.assets.find_one({'key':key,'kind':'file'})
        if not meta:raise RuntimeError('Missing fixture: '+key)
        with psycopg.connect(target) as conn:
            if conn.execute('SELECT 1 FROM assets WHERE key=%s AND sha256=%s',(key,meta['sha256'])).fetchone():continue
        if pg_source:blob=bytes(source.execute('SELECT data FROM asset_blobs WHERE blob_id=%s',(meta['blob_id'],)).fetchone()['data'])
        else:
            with bucket.open_download_stream(meta['blob_id']) as stream:blob=stream.read()
        data=gzip.decompress(blob) if meta.get('compression')=='gzip' else blob
        if hashlib.sha256(data).hexdigest()!=meta['sha256']:raise RuntimeError('Fixture checksum mismatch')
        import io
        publish(key,io.BytesIO(data));print('Verified fixture:',key,flush=True)
    pool().close()
    if pg_source:source.close()
    else:source.client.close()
if __name__=='__main__':main()
