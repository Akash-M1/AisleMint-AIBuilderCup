"""Account roles are enforced on API calls, independently of navigation and POS PINs."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from fastapi.testclient import TestClient
from promo.api import app
from promo.pg import database
from promo.retail import auth, permissions
from test_retail_platform import tenant, identity

@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv('AISLEMINT_TEST_AUTH_BYPASS','0')

def member(retailer, roles):
    client,id=identity(retailer,role='member')
    database().retail_users.update_one({'_id':id},{'$set':{'module_roles':{retailer:roles}}})
    return client,id

@pytest.mark.parametrize('area,path',[
    ('promotions','/api/summary'),('demand','/api/demand/summary'),
    ('data','/api/retail/shops/{shop}/workbench'),
    ('connect','/api/retail/shops/{shop}/connected/objects')])
def test_product_read_permissions_and_direct_navigation(area,path):
    retailer,shop=tenant();client,_=member(retailer,{area:'viewer'})
    path=path.format(shop=shop['id'])
    headers={'X-Workspace-ID':shop['id']};params={'source_id':shop['id']}
    allowed=client.get(path,headers=headers,params=params)
    assert allowed.status_code==200,allowed.text
    blocked,_=member(retailer,{})
    assert blocked.get(path,headers=headers,params=params).status_code==403

@pytest.mark.parametrize('roles,path,body,status',[
    ({'promotions':'viewer'},'/api/forecast',{},403),
    ({'promotions':'user'},'/api/forecast',{},422),
    ({'promotions':'user'},'/api/scenarios',{},403),
    ({'promotions':'editor'},'/api/scenarios',{},422),
    ({'promotions':'user'},'/api/promotions/confirm',{},403),
    ({'demand':'viewer'},'/api/demand/forecast',{'source_id':'{shop}'},403),
    ({'demand':'user'},'/api/demand/forecast',{'source_id':'{shop}'},409),
    ({'demand':'user'},'/api/demand/plans',{'forecast':{'source_id':'{shop}'}},403),
    ({'data':'user'},'/api/retail/shops/{shop}/train',{},403),
    ({'data':'editor'},'/api/retail/shops/{shop}/train',{},422),
    ({'connect':'viewer'},'/api/retail/shops/{shop}/connected/ask',{},422),
    ({'connect':'user'},'/api/retail/shops/{shop}/connected/ask',{},422),
])
def test_role_action_matrix(roles,path,body,status):
    import json
    r,s=tenant();client,_=member(r,roles)
    body=json.loads(json.dumps(body).replace('{shop}',s['id']))
    response=client.post(path.format(shop=s['id']),headers={'X-Workspace-ID':s['id']},json=body)
    assert response.status_code==status,response.text


def test_roles_are_authoritative_and_cannot_cross_tenant_or_escalate():
    r,s=tenant();other,other_shop=tenant('OTHER');owner,_=identity(r)
    client,id=member(r,{'promotions':'editor'})
    assert client.get('/api/retail/me').json()['retailers'][0]['roles']['data']=='none'
    assert client.get('/api/demand/sources').json()==[]
    assert client.get('/api/summary',headers={'X-Workspace-ID':other_shop['id']}).status_code==403
    assert client.post(f'/api/retail/retailers/{r}/accounts',json={'name':'Bad','email':'bad@example.test','password':'Strong-test-password-12','membership':'owner'}).status_code==403
    assert owner.post(f'/api/retail/retailers/{other}/accounts/{id}/access',json={'revision':0,'roles':{'data':'editor'}}).status_code==403
    assert client.get(f'/api/retail/shops/{s["id"]}/unregistered-operation').status_code==403


def test_owner_creates_least_privilege_named_accounts_and_grants_are_validated():
    r,_=tenant();owner,_=identity(r)
    body={'name':'Promo planner','email':'planner@example.test','password':'Strong-test-password-12'}
    result=owner.post(f'/api/retail/retailers/{r}/accounts',json=body)
    assert result.status_code==200,result.text
    assert set(result.json()['roles'].values())=={'none'}
    assert result.json()['membership']=='member'
    assert 'password' not in result.text and 'salt' not in result.text
    for extra in ({'admin':True},{'roles':{'platform':'editor'}}):
        assert owner.post(f'/api/retail/retailers/{r}/accounts',json={**body,**extra}).status_code==422
    assert database().retail_audit.count_documents({'workspace':r,'event':'account_created'})==1


def test_access_updates_revoke_sessions_and_reject_stale_changes():
    r,_=tenant();owner,_=identity(r);client,id=member(r,{'promotions':'viewer'})
    path=f'/api/retail/retailers/{r}/accounts/{id}/access'
    change={'revision':0,'membership':'member','roles':{'promotions':'editor','sales':'viewer'}}
    assert owner.post(path,json=change).status_code==200
    assert client.get('/api/retail/me').status_code==401
    assert owner.post(path,json=change).status_code==409
    users=owner.get(f'/api/retail/retailers/{r}/accounts').json()
    saved=next(u for u in users if u['id']==id)
    assert saved['roles']['sales']=='viewer' and saved['roles']['demand']=='none' and saved['revision']==1
    audit=owner.get(f'/api/retail/retailers/{r}/access-audit').json()
    assert audit[0]['details']['before']['roles']['promotions']=='viewer'
    assert owner.post(path,json={**change,'revision':1,'membership':None}).status_code==200
    assert r not in database().retail_users.find_one({'_id':id})['memberships']


def test_last_owner_protected_in_new_and_legacy_endpoints():
    r,_=tenant();owner,id=identity(r)
    assert owner.post(f'/api/retail/retailers/{r}/accounts/{id}/access',json={'revision':0,'membership':None}).status_code==409
    assert owner.post(f'/api/retail/retailers/{r}/users/{id}/access',json={'role':'viewer'}).status_code==409
    assert owner.get('/api/retail/me').status_code==200


def test_concurrent_owner_demotions_cannot_remove_both_owners():
    r,_=tenant();owners=[identity(r),identity(r)];barrier=Barrier(2)
    def remove(pair):
        client,id=pair;barrier.wait(timeout=5)
        return client.post(f'/api/retail/retailers/{r}/accounts/{id}/access',json={'revision':0,'membership':'member','roles':{}})
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(remove,owners))
    assert sum(x.status_code==200 for x in results)==1,[x.text for x in results]
    assert database().retail_users.count_documents({f'memberships.{r}':'owner','disabled':False})==1


def test_legacy_roles_keep_access_without_broadening_explicit_grants():
    for legacy,expected in [('owner','editor'),('planner','editor'),('viewer','viewer'),('member','none')]:
        assert set(permissions.roles({'memberships':{'r':legacy}},'r').values())=={expected}
    assert permissions.roles({'memberships':{'r':'planner'},'module_roles':{'r':{'sales':'user'}}},'r')['data']=='none'
    assert not permissions.allows({'memberships':{'elsewhere':'owner'}},'r','sales')


def test_sales_pin_cannot_bypass_account_module_roles():
    r,s=tenant();owner,_=identity(r)
    store_body={'name':'Role test','address':'Test store address','state_code':'29','gst_registered':False,'owner_name':'Manager','pin':'123456'}
    created=owner.post(f'/api/retail/shops/{s["id"]}/pos',json={'new_store':store_body})
    assert created.status_code==200,created.text
    store=created.json()['pos_store_id'];base=f'/api/pos/stores/{store}'
    no_sales,_=member(r,{'promotions':'editor'})
    assert no_sales.get('/api/pos/stores').json()==[]
    assert no_sales.post(base+'/login',json={}).status_code==403
    viewer,_=member(r,{'sales':'viewer'})
    assert viewer.post(base+'/checkout',json={}).status_code==403
    operator,_=member(r,{'sales':'user'})
    assert operator.post(base+'/products',json={}).status_code==403
    assert operator.post(base+'/checkout',json={}).status_code==422
    assert operator.post(base+'/learning/train',json={}).status_code==403
    assert operator.post(base+'/learning/settings',json={}).status_code==403
    assert operator.post(base+'/holidays',json={}).status_code==403
