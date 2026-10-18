"""Exercise the real importer on disposable databases, including historical blobs."""
import gzip
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import uuid
from datetime import datetime, timezone
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pymongo import MongoClient
from gridfs import GridFSBucket


def test_import_preserves_history_unknown_records_and_detects_same_size_corruption(tmp_path):
    root=Path(__file__).resolve().parents[2]
    info=conninfo_to_dict(os.environ['DATABASE_URL'])
    assert info['host'] in ('localhost','127.0.0.1') and info['dbname']=='aislemint_test'
    name='aislemint_migration_test_'+uuid.uuid4().hex[:12]
    target=make_conninfo(**{**info,'dbname':name})
    # Neither fixture has access to the production database or Atlas.
    mongo=MongoClient('mongodb://127.0.0.1:27029/?directConnection=true',tz_aware=True)
    source=mongo[name]
    with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    try:
        bucket=GridFSBucket(source);now=datetime.now(timezone.utc)
        for content in (b'historical model',b'current model'):
            packed=gzip.compress(content)
            blob=bucket.upload_from_stream('tests/model',packed)
            meta=dict(key='tests/model',kind='file',blob_id=blob,sha256=hashlib.sha256(content).hexdigest(),size=len(content),compression='gzip',created=now)
            source.asset_versions.insert_one(dict(meta))
        source.assets.insert_one(meta)
        orphan=bucket.upload_from_stream('unreferenced archive',b'ABCD')
        source.retail_users.insert_one({'_id':'test-user','email':'migration@example.test','memberships':{'123-shop':'owner'},'expires_at':now})
        source.legacy_extra.insert_one({'_id':'old-record','value':2**60,'nested':{'keep':True}})
        fixture=tmp_path/'source.env'
        fixture.write_text('MONGODB_URI=mongodb://127.0.0.1:27029/?directConnection=true\nMONGODB_DATABASE='+name+'\n')
        env={**os.environ,'DATABASE_URL':target}
        command=[sys.executable,str(root/'aislemint-ops/scripts/migrate_mongo_to_pg.py'),'--source-env',str(fixture),'--apply']
        staged=subprocess.run([*command,'--blobs-only'],env=env,capture_output=True,text=True)
        assert staged.returncode==0,staged.stdout+staged.stderr
        with psycopg.connect(target) as conn:
            assert conn.execute('SELECT status,verified FROM mongo_import').fetchone()==('blobs_staged',False)
            assert conn.execute('SELECT count(*) FROM assets').fetchone()==(0,)
        for _ in range(2):
            result=subprocess.run(command,env=env,capture_output=True,text=True)
            assert result.returncode==0,result.stdout+result.stderr
        with psycopg.connect(target) as conn:
            assert conn.execute('SELECT verified FROM mongo_import').fetchone()==(True,)
            assert conn.execute('SELECT count(*) FROM asset_versions').fetchone()==(2,)
            assert conn.execute('SELECT count(*) FROM asset_blobs').fetchone()==(3,)
            assert conn.execute("SELECT document->'value'->>'$numberLong' FROM mongo_archive WHERE collection='legacy_extra'").fetchone()==(str(2**60),)
            old=conn.execute('SELECT data FROM asset_blobs WHERE blob_id=%s',(str(source.asset_versions.find_one()['blob_id']),)).fetchone()[0]
            assert gzip.decompress(bytes(old))==b'historical model'
            conn.execute('UPDATE asset_blobs SET data=%s WHERE blob_id=%s',(b'WXYZ',str(orphan)))
        rejected=subprocess.run(command,env=env,capture_output=True,text=True)
        assert rejected.returncode!=0 and 'Migration stopped' in rejected.stderr
        with psycopg.connect(target) as conn:
            assert conn.execute('SELECT verified FROM mongo_import').fetchone()==(False,)
        assert source.assets.count_documents({})==1 and source.asset_versions.count_documents({})==2
    finally:
        mongo.drop_database(name);mongo.close()
        with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
