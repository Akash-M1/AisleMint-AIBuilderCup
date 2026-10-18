"""Versioned cache isolation, expiry, failure fallback and current authorization."""
import json
import os
import pytest
import redis
from promo import cache, pg


@pytest.fixture
def caching(monkeypatch):
    monkeypatch.setenv('REDIS_HOST','127.0.0.1')
    monkeypatch.setenv('REDIS_PORT',os.environ.get('TEST_REDIS_PORT','56379'))
    monkeypatch.setenv('REDIS_DB','15')
    monkeypatch.setenv('REDIS_TLS','false')
    monkeypatch.delenv('REDIS_PASSWORD',raising=False)
    monkeypatch.setattr(cache,'_failed_until',0)
    cache.client.cache_clear()
    client=cache.client();client.flushdb()
    yield client
    client.flushdb();client.close();cache.client.cache_clear()


def test_cache_reuses_results_and_isolates_workspace_and_release(caching):
    def fail():raise AssertionError('Recomputed a cache hit')
    assert cache.remember('analytics','shop-a',['release-1'],lambda:{'sales':10})=={'sales':10}
    assert cache.remember('analytics','shop-a',['release-1'],fail)=={'sales':10}
    assert cache.remember('analytics','shop-a',['release-2'],lambda:{'sales':20})=={'sales':20}
    assert cache.remember('analytics','shop-b',['release-1'],lambda:{'sales':30})=={'sales':30}
    assert caching.dbsize()==3
    assert all(1<=caching.ttl(k)<=330 for k in caching.scan_iter())


def test_expired_corrupt_and_oversized_entries_are_recomputed(caching):
    cache.remember('summary','shop',1,lambda:{'value':1})
    key=next(caching.scan_iter())
    caching.set(key,b'invalid JSON',ex=30)
    assert cache.remember('summary','shop',1,lambda:{'value':2})=={'value':2}
    caching.expire(key,0)
    assert cache.remember('summary','shop',1,lambda:{'value':3})=={'value':3}
    caching.flushdb()
    big={'value':'x'*(cache.MAX_VALUE_BYTES+1)}
    assert cache.remember('summary','shop',1,lambda:big)==big
    assert caching.dbsize()==0


def test_redis_outage_falls_back_and_stops_repeated_connection_attempts(caching,monkeypatch):
    calls=[]
    def unavailable(*args):calls.append(1);raise redis.ConnectionError('Unavailable')
    monkeypatch.setattr(caching,'get',unavailable)
    assert cache.remember('summary','shop',1,lambda:{'value':1})=={'value':1}
    assert cache.remember('summary','shop',1,lambda:{'value':2})=={'value':2}
    assert len(calls)==1


def test_uncommitted_results_never_enter_redis(caching):
    with pytest.raises(RuntimeError,match='abort'):
        with pg.connection():
            assert cache.remember('summary','shop',1,lambda:{'value':'uncommitted'})=={'value':'uncommitted'}
            assert caching.dbsize()==0
            raise RuntimeError('abort')
    assert cache.remember('summary','shop',1,lambda:{'value':'committed'})=={'value':'committed'}


def test_catalogue_cache_does_not_bypass_tenant_or_disabled_account(caching,monkeypatch):
    from test_retail_platform import tenant,identity
    from promo.data_platform import service
    monkeypatch.setenv('AISLEMINT_TEST_AUTH_BYPASS','0')
    retailer,shop=tenant();owner,user=identity(retailer);other,_=identity(tenant('OTHER')[0])
    monkeypatch.setattr(service,'active',lambda workspace:{'id':'release','workspace':workspace})
    monkeypatch.setattr(service,'records',lambda *args:[{'id':'product','properties':{'name':'Test'}}])
    path=f'/api/retail/shops/{shop["id"]}/data/catalog'
    first=owner.get(path);assert first.status_code==200
    assert owner.get(path).json()==first.json() and caching.dbsize()==1
    assert other.get(path).status_code==403
    pg.database().retail_users.update_one({'_id':user},{'$set':{'disabled':True}})
    assert owner.get(path).status_code==401
