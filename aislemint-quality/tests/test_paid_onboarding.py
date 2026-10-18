"""Real PostgreSQL transactions and HTTP boundaries for paid customer access."""
import hashlib
import hmac
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from promo.api import app
from promo.pg import database
from promo.retail import auth, permissions
from promo.retail.provisioning import Purchase, provision
from promo.retail.repository import now
from test_retail_platform import identity, tenant, ORIGIN

SECRET = 'isolated-provisioning-secret-at-least-32-bytes'
INITIAL = 'Private-initial-password-12345'
NEW = 'My-own-secure-password-12345'


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv('AISLEMINT_TEST_AUTH_BYPASS', '0')
    monkeypatch.setenv('BILLING_PROVISIONING_SECRET', SECRET)


def purchase(**overrides):
    return dict(order_id='AM-' + str(uuid4()), payment_id=str(uuid4()),
                pricing_version='annual-2026-10', plan_id='basic', amount_paise=1500000,
                currency='INR', name='Paid customer', email=str(uuid4()) + '@example.test',
                business='Customer business', temporary_password=INITIAL, **overrides)


def signed(client, body, timestamp=None, secret=SECRET, path='orders'):
    raw = json.dumps(body, separators=(',', ':'))
    stamp = str(timestamp if timestamp is not None else int(time.time()))
    signature = hmac.new(secret.encode(), (stamp + '.' + raw).encode(), hashlib.sha256).hexdigest()
    return client.post('/api/retail/provisioning/' + path, content=raw,
                       headers={'Content-Type': 'application/json', 'X-AisleMint-Timestamp': stamp,
                                'X-AisleMint-Signature': signature})


def customer(body, replace=True):
    result = provision(Purchase(**body)); client = TestClient(app,base_url='https://testserver'); client.headers.update(ORIGIN)
    assert client.post('/api/retail/auth/login', json={'email': body['email'], 'password': INITIAL}).status_code == 200
    if replace:
        response = client.post('/api/retail/auth/password', json={'current_password': INITIAL, 'new_password': NEW})
        assert response.status_code == 200, response.text
        assert client.post('/api/retail/auth/login', json={'email': body['email'], 'password': NEW}).status_code == 200
    return client, result


def test_provisioning_requires_signed_recent_request_and_exact_catalog():
    client = TestClient(app); body = purchase()
    assert client.post('/api/retail/provisioning/orders', json=body).status_code == 401
    assert signed(client, body, secret='wrong').status_code == 401
    assert signed(client, body, timestamp=int(time.time()) - 601).status_code == 401
    assert signed(client, {**body, 'amount_paise': 1000}).status_code == 409
    assert signed(client, {**body, 'pricing_version': 'test-2026-10'}).status_code == 409
    assert signed(client, {}).status_code == 422
    assert database().retailers.count_documents({}) == 0
    assert signed(client, {}, path='status').json() == {'ready': True}


def test_provision_is_atomic_idempotent_and_does_not_echo_password():
    client = TestClient(app); body = purchase()
    first = signed(client, body); second = signed(client, body)
    assert first.status_code == 200, first.text
    assert first.json() == second.json()
    assert INITIAL not in first.text and 'password_hash' not in first.text
    assert database().retailers.count_documents({}) == 1
    assert database().retail_shops.count_documents({}) == 1
    assert database().retail_users.count_documents({}) == 1
    assert signed(client, {**body, 'plan_id': 'ultra', 'amount_paise': 5000000}).status_code == 409
    row = database().retail_users.find_one({'email': body['email']})
    assert row['must_change_password'] is True and row['password_hash'] != INITIAL
    assert row['admin'] is False


def test_simultaneous_provision_retries_create_one_workspace():
    body = Purchase(**purchase())
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda _: provision(body), range(2)))
    assert responses[0] == responses[1]
    assert database().retailers.count_documents({}) == 1


def test_initial_password_can_only_change_password_and_never_access_products():
    body = purchase(); client, result = customer(body, replace=False)
    me = client.get('/api/retail/me').json()
    assert me['user']['must_change_password'] is True and me['retailers'] == []
    for path in ['/api/summary', '/api/demand/sources', '/api/retail/contract',
                 f"/api/retail/shops/{result['shop_id']}/workbench"]:
        assert client.get(path).status_code == 403
    assert client.post('/api/retail/retailers', json={'name': 'Escalation'}).status_code == 403
    assert client.post('/api/retail/auth/password', json={'current_password': 'wrong', 'new_password': NEW}).status_code == 401
    assert client.post('/api/retail/auth/password', json={'current_password': INITIAL, 'new_password': INITIAL}).status_code == 422
    assert client.post('/api/retail/auth/password', json={'current_password': INITIAL, 'new_password': NEW}).status_code == 200
    assert client.get('/api/retail/me').status_code == 401
    assert client.post('/api/retail/auth/login', json={'email': body['email'], 'password': INITIAL}).status_code == 401
    assert client.post('/api/retail/auth/login', json={'email': body['email'], 'password': NEW}).status_code == 200
    assert len(client.get('/api/retail/me').json()['retailers']) == 1


@pytest.mark.parametrize('plan,amount,sales,connect', [
    ('basic', 1500000, False, False), ('premium', 2500000, True, False), ('ultra', 5000000, True, True)])
def test_plan_ceiling_applies_to_owner_staff_and_direct_api(plan, amount, sales, connect):
    body = purchase(); body.update(plan_id=plan, amount_paise=amount)
    client, result = customer(body); retailer, shop = result['retailer_id'], result['shop_id']
    account = client.get('/api/retail/me').json()
    roles = account['retailers'][0]['roles']
    assert roles['data'] == roles['demand'] == roles['promotions'] == 'editor'
    assert (roles['sales'] == 'editor') is sales
    assert (roles['connect'] == 'editor') is connect
    for path, allowed in [(f'/api/retail/shops/{shop}/connected/objects', connect), (f'/api/retail/shops/{shop}/data/analytics', sales)]:
        response = client.get(path)
        assert (response.status_code != 403) is allowed, response.text
    staff, staff_id = identity(retailer, role='planner')
    assert staff.get('/api/retail/me').json()['retailers'][0]['roles']['sales'] == ('editor' if sales else 'none')
    assert permissions.roles({'memberships': {retailer: 'owner'}, 'module_roles': {retailer: {'connect': 'editor'}}}, retailer)['connect'] == ('editor' if connect else 'none')


def test_expired_annual_access_revokes_owner_and_integration_product_access():
    body = purchase(); client, result = customer(body); r, s = result['retailer_id'], result['shop_id']
    key = client.post(f'/api/retail/retailers/{r}/keys', json={'name': 'Customer ingestion'}).json()['token']
    database().retailers.update_one({'_id': r}, {'$set': {'subscription.expires_at': now() - timedelta(seconds=1)}})
    roles = client.get('/api/retail/me').json()['retailers'][0]['roles']
    assert set(roles.values()) == {'none'}
    assert client.get(f'/api/retail/shops/{s}/workbench').status_code == 403
    integration = TestClient(app)
    assert integration.get('/api/retail/contract', headers={'Authorization': 'Bearer ' + key}).status_code == 403


def test_existing_account_keeps_password_and_other_memberships():
    r, _ = tenant(); old, user_id = identity(r)
    original = database().retail_users.find_one({'_id': user_id})
    body = purchase(); body['email'] = original['email']
    result = provision(Purchase(**body))
    user = database().retail_users.find_one({'_id': user_id})
    assert result['new_account'] is False
    assert user['password_hash'] == original['password_hash'] and user['memberships'][r] == 'owner'
    assert result['retailer_id'] in user['memberships']
    assert len(old.get('/api/retail/me').json()['retailers']) == 2


def test_expired_initial_password_and_administrator_email_fail_closed():
    body = purchase(); provision(Purchase(**body))
    database().retail_users.update_one({'email': body['email']}, {'$set': {'temporary_password_expires_at': now() - timedelta(seconds=1)}})
    client = TestClient(app)
    assert client.post('/api/retail/auth/login', json={'email': body['email'], 'password': INITIAL}).status_code == 401
    _, admin_id = identity(admin=True)
    admin = database().retail_users.find_one({'_id': admin_id})
    second = purchase(); second['email'] = admin['email']
    assert signed(client, second).status_code == 409
    assert database().retailers.count_documents({}) == 1
