"""Performance changes must preserve atomic writes and current authorization."""
from datetime import timedelta
from unittest.mock import patch

import pytest
from psycopg.errors import UniqueViolation
from pymongo import ReplaceOne, UpdateOne

from promo import pg
from promo.retail import auth
from promo.retail.repository import now
from test_retail_platform import identity, tenant


def test_warm_transactions_do_not_recheck_startup_gate():
    pg.initialize()
    with patch.object(pg, 'assert_migration_ready', side_effect=AssertionError('Repeated startup query')):
        for _ in range(3):
            with pg.connection() as unit:
                assert unit.conn.execute('SELECT 1').fetchone() == (1,)


def test_importer_initialization_cannot_skip_readiness(monkeypatch):
    monkeypatch.setattr(pg, '_migration_checked', set())
    pg.initialize(allow_incomplete_migration=True)
    with patch.object(pg, 'assert_migration_ready', side_effect=RuntimeError('incomplete import')):
        with pytest.raises(RuntimeError, match='incomplete import'):
            pg.initialize()
    pg.initialize()


def test_single_statement_reads_return_idle_connection_and_share_explicit_transactions():
    db=pg.database()
    with pytest.raises(RuntimeError, match='abort'):
        with pg.connection() as unit:
            db.retailers.insert_one({'_id':'uncommitted','name':'Private'})
            assert db.retailers.find_one({'_id':'uncommitted'})['name']=='Private'
            assert not unit.conn.autocommit
            raise RuntimeError('abort')
    assert db.retailers.find_one({'_id':'uncommitted'}) is None
    with pg.operation_connection(read_only=True) as conn:
        assert conn.autocommit
        # A failed SELECT must not leave the pooled connection in a transaction.
        with pytest.raises(Exception):conn.execute('SELECT missing_performance_test_column')
    with pg.pool().connection() as conn:
        assert not conn.autocommit
        assert conn.execute('SELECT 1').fetchone()==(1,)


def test_document_projection_preserves_null_missing_and_id_semantics():
    db=pg.database();db.retailers.insert_one({'_id':'projected','name':'Retailer','nullable':None,'private':'x'*100000})
    assert db.retailers.find_one({}, {'_id':1})=={'_id':'projected'}
    assert db.retailers.find_one({}, {'name':1,'nullable':1,'missing':1,'_id':0})=={'name':'Retailer','nullable':None}
    assert list(db.retailers.find({}, {'private':0}))==[{'_id':'projected','name':'Retailer','nullable':None}]


def test_batched_inserts_and_mixed_bulk_writes_preserve_order_and_rollback():
    db=pg.database();collection=db.connect_objects
    rows=[{'_id':'first','value':0},{'_id':'second','value':1}]
    assert collection.insert_many(iter(rows)).inserted_ids==['first','second']
    assert rows[0]=={'_id':'first','value':0}
    collection.bulk_write([
        ReplaceOne({'_id':'first'},{'value':2,'nested':{'keep':True}},upsert=True),
        UpdateOne({'_id':'first'},{'$set':{'value':3}}),
        UpdateOne({'_id':'first'},{'$set':{'nested.changed':True}}),
        ReplaceOne({'_id':'third'},{'_id':'third','value':4},upsert=True),
    ])
    assert collection.find_one({'_id':'first'})=={'_id':'first','value':3,'nested':{'keep':True,'changed':True}}
    with pytest.raises(UniqueViolation):
        collection.insert_many([{'_id':'not-committed'},{'_id':'first'}])
    assert collection.find_one({'_id':'not-committed'}) is None
    with pytest.raises(RuntimeError,match='abort'):
        with pg.connection():
            collection.bulk_write([ReplaceOne({'_id':'first'},{'value':99},upsert=True)])
            raise RuntimeError('abort')
    assert collection.find_one({'_id':'first'})['value']==3


def test_joined_authentication_observes_expiry_disable_and_revocation(monkeypatch):
    monkeypatch.setenv('AISLEMINT_TEST_AUTH_BYPASS','0')
    retailer,_=tenant();client,user=identity(retailer)
    db=pg.database()
    assert client.get('/api/retail/me').status_code==200
    db.retail_users.update_one({'_id':user},{'$set':{'disabled':True}})
    assert client.get('/api/retail/me').status_code==401
    db.retail_users.update_one({'_id':user},{'$set':{'disabled':False}})
    session={'_id':auth.digest(client.cookies.get(auth.COOKIE))}
    db.retail_sessions.update_one(session,{'$set':{'expires_at':now()-timedelta(seconds=1)}})
    assert client.get('/api/retail/me').status_code==401
    db.retail_sessions.update_one(session,{'$set':{'expires_at':now()+timedelta(minutes=1)}})
    assert client.get('/api/retail/me').status_code==200
    db.retail_sessions.delete_one(session)
    assert client.get('/api/retail/me').status_code==401
