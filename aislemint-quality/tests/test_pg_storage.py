"""PostgreSQL integration tests: durability, isolation, migration and failure boundaries."""
import io,json,sqlite3,importlib.util
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from psycopg.errors import CheckViolation
from promo import api
from promo.pg import database,connection,pool
from promo.storage import AssetPath,publish
from promo.jobs import TrainingLease
from test_pos import pos

def test_postgres_versions_checksum_and_no_disk_fallback(tmp_path):
    p=AssetPath('tests/large-asset.bin');content=b'no local cache\n'*100000
    record=publish(p.key,io.BytesIO(content))
    assert record['compression']=='gzip' and p.read_bytes()==content
    with pool().connection() as conn:
        assert conn.execute('SELECT octet_length(data) FROM asset_blobs WHERE blob_id=%s',(record['blob_id'],)).fetchone()[0]<len(content)//10
    p.write_bytes(b'new version');assert p.read_bytes()==b'new version'
    assert database().asset_versions.count_documents({'key':p.key})==2
    database().assets.update_one({'key':p.key},{'$set':{'sha256':'0'*64}})
    with pytest.raises(ValueError,match='checksum'):p.read_bytes()
    with pytest.raises(FileNotFoundError):AssetPath('no-local-fallback').read_bytes()

def test_transaction_rolls_back_all_writes():
    with pytest.raises(RuntimeError,match='abort'):
        with connection() as c:
            c.insert('scenarios',dict(id='rollback',workspace='benchmark',name='Abort',request='{}',result='{}',created='2026-09-06'))
            raise RuntimeError('abort')
    assert database().scenarios.count_documents({'id':'rollback'})==0

def test_database_validator_enforces_stock_and_integer_money(pos):
    _,_,_,_,session=pos;store=session['store']['id']
    with pytest.raises(CheckViolation):database().pos_products.update_one({'store_id':store},{'$set':{'on_hand':-1}})
    with pytest.raises(ValueError,match='Integer'):database().pos_products.update_one({'store_id':store},{'$set':{'price_paise':12.5}})

def test_recording_recovery_is_durable_and_staff_scoped(pos):
    client,base,post,shift,session=pos;cart={'lines':[{'product_id':'milk','quantity':1}]};q=post('/quote',cart)
    body={**cart,'shift_id':shift['id'],'quote_token':q['quote_token'],'idempotency_key':'mongo-recovery-key','tenders':[{'method':'cash','amount_paise':q['total_paise']}]}
    post('/recording',{'path':'/checkout','body':body})
    replacement=TestClient(api.app);replacement.headers['Authorization']='Bearer '+session['token']
    assert replacement.get(base+'/recording').json()['body']==body
    assert replacement.get('/api/pos/stores/another/recording').status_code==401
    changed={**body,'idempotency_key':'another-sale-key'}
    assert client.post(base+'/recording',json={'path':'/checkout','body':changed}).status_code==409
    post('/recording/clear',{'idempotency_key':'wrong-key-123'})
    assert replacement.get(base+'/recording').json()
    post('/recording/clear',{'idempotency_key':body['idempotency_key']})
    assert replacement.get(base+'/recording').json() is None

def test_csv_upload_endpoints_removed_and_integration_protected():
    client=TestClient(api.app)
    for path in ['/api/datasets','/api/demand/datasets','/api/demand/inventory-import']:
        assert client.post(path,files={'file':('data.csv',b'a,b\n1,2')}).status_code in (404,405)
    assert client.get('/api/integrations/status').status_code==401
    r=client.get('/api/integrations/status',headers={'Authorization':'Bearer test-integration-key'})
    assert r.status_code==200 and r.json()['file_uploads'] is False

def test_training_lease_excludes_second_worker():
    a=TrainingLease('test-worker');b=TrainingLease('test-worker')
    assert a.acquire(False)
    try:
        assert not b.acquire(False)
        a.assert_owned()
    finally:a.release()
    assert b.acquire(False);b.release()
