"""Recovery uses email possession; it never changes passwords just by requesting."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from promo.api import app
from promo.pg import database
from promo.retail import auth
from promo.retail.recovery import ResetRequest, ResetComplete, prepare, complete
from promo.retail.repository import now
from test_paid_onboarding import signed, SECRET
from test_retail_platform import identity, tenant, ORIGIN

PASSWORD = 'A-fresh-customer-password-123'


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv('AISLEMINT_TEST_AUTH_BYPASS', '0')
    monkeypatch.setenv('BILLING_PROVISIONING_SECRET', SECRET)


def issue(uid, requester='a' * 64):
    user = database().retail_users.find_one({'_id': uid})
    return prepare(ResetRequest(email=user['email'], requester_hash=requester))['recovery']


def test_private_recovery_rejects_unsigned_stale_and_malformed_requests():
    client = TestClient(app)
    body = {'email': 'owner@example.test', 'requester_hash': 'a' * 64}
    path = '/api/retail/provisioning/recovery/request'
    assert client.post(path, json=body).status_code == 401
    assert signed(client, body, path='recovery/request', secret='wrong').status_code == 401
    assert signed(client, {}, path='recovery/request').status_code == 422
    malformed = signed(client, {'token': 'private-invalid-token', 'password': 'short'}, path='recovery/complete')
    assert malformed.status_code == 422
    assert 'private-invalid-token' not in malformed.text and 'short' not in malformed.text
    assert client.post('/api/retail/provisioning/recovery/unreviewed', json=body).status_code == 401


def test_signed_http_recovery_changes_only_the_proven_account():
    _, uid = identity(); db = database(); before = db.retail_users.find_one({'_id': uid})
    _, other = identity(); untouched = db.retail_users.find_one({'_id': other})
    client = TestClient(app)
    response = signed(client, {'email': before['email'], 'requester_hash': 'a' * 64}, path='recovery/request')
    assert response.status_code == 200
    token = response.json()['recovery']['token']
    public_user = TestClient(app).get('/api/retail/me')
    assert public_user.status_code == 401
    changed = signed(client, {'token': token, 'password': PASSWORD}, path='recovery/complete')
    assert changed.status_code == 200 and token not in changed.text and PASSWORD not in changed.text
    assert db.retail_users.find_one({'_id': other}) == untouched
    assert signed(client, {'token': token, 'password': PASSWORD}, path='recovery/complete').status_code == 400


def test_unknown_disabled_and_admin_accounts_receive_no_token():
    db = database()
    assert prepare(ResetRequest(email='unknown@example.test', requester_hash='a' * 64)) == {'recovery': None}
    for admin, disabled in [(True, False), (False, True)]:
        _, uid = identity(admin=admin)
        if disabled: db.retail_users.update_one({'_id': uid}, {'$set': {'disabled': True}})
        assert issue(uid) is None
        assert 'password_reset' not in db.retail_users.find_one({'_id': uid})


def test_recovery_keeps_password_until_complete_then_revokes_sessions_and_preserves_workspaces():
    rid, shop = tenant()
    old_client, uid = identity(rid)
    db = database(); before = db.retail_users.find_one({'_id': uid})
    recovery = issue(uid)
    stored = db.retail_users.find_one({'_id': uid})
    assert stored['password_hash'] == before['password_hash']
    assert recovery['token'] not in json.dumps(stored, default=str)
    assert old_client.get('/api/retail/me').status_code == 200
    result = complete(ResetComplete(token=recovery['token'], password=PASSWORD))
    assert result == {'status': 'password_changed'}
    after = db.retail_users.find_one({'_id': uid})
    assert after['memberships'] == before['memberships']
    assert db.retail_shops.find_one({'_id': shop['id']})
    assert after['password_hash'] != before['password_hash'] and 'password_reset' not in after
    assert old_client.get('/api/retail/me').status_code == 401
    client = TestClient(app, base_url='https://testserver'); client.headers.update(ORIGIN)
    assert client.post('/api/retail/auth/login', json={'email': before['email'], 'password': PASSWORD}).status_code == 200
    assert client.get('/api/retail/me').json()['retailers'][0]['id'] == rid
    with pytest.raises(HTTPException) as exc: complete(ResetComplete(token=recovery['token'], password='Another-fresh-password-123'))
    assert exc.value.status_code == 400


def test_expired_tampered_superseded_or_disabled_links_cannot_change_password():
    db = database(); _, uid = identity(); first = issue(uid)
    with pytest.raises(HTTPException): complete(ResetComplete(token=first['token'][:-1] + ('a' if first['token'][-1] != 'a' else 'b'), password=PASSWORD))
    db.retail_users.update_one({'_id': uid}, {'$set': {'password_reset.issued_at': now() - timedelta(minutes=2)}})
    second = issue(uid)
    assert first['token'] != second['token']
    with pytest.raises(HTTPException): complete(ResetComplete(token=first['token'], password=PASSWORD))
    db.retail_users.update_one({'_id': uid}, {'$set': {'password_reset.expires_at': now() - timedelta(seconds=1)}})
    with pytest.raises(HTTPException): complete(ResetComplete(token=second['token'], password=PASSWORD))
    db.retail_users.update_one({'_id': uid}, {'$set': {'password_reset.expires_at': now() + timedelta(minutes=20), 'disabled': True}})
    with pytest.raises(HTTPException): complete(ResetComplete(token=second['token'], password=PASSWORD))


def test_password_change_invalidates_link_and_expired_initial_invitation_can_recover():
    db = database(); client, uid = identity(); token = issue(uid)['token']
    response = client.post('/api/retail/auth/password', json={'current_password': 'Strong-test-password-12', 'new_password': PASSWORD})
    assert response.status_code == 200
    with pytest.raises(HTTPException): complete(ResetComplete(token=token, password='Another-fresh-password-123'))
    db.retail_users.update_one({'_id': uid}, {'$set': {'must_change_password': True, 'temporary_password_expires_at': now() - timedelta(hours=1)}})
    token = issue(uid)['token']
    complete(ResetComplete(token=token, password='Another-fresh-password-123'))
    row = db.retail_users.find_one({'_id': uid})
    assert row['must_change_password'] is False and 'temporary_password_expires_at' not in row


def test_persistent_email_cooldown_and_ip_limits_bound_mail_requests():
    db = database(); _, uid = identity()
    assert issue(uid) and issue(uid) is None
    for _ in range(2):
        db.retail_users.update_one({'_id': uid}, {'$set': {'password_reset.issued_at': now() - timedelta(minutes=2)}})
        issue(uid)
    assert issue(uid) is None
    for i in range(21): prepare(ResetRequest(email=f'unknown{i}@example.test', requester_hash='b' * 64))
    _, other = identity()
    assert issue(other, 'b' * 64) is None


def test_simultaneous_redemptions_only_one_can_change_password():
    _, uid = identity(); token = issue(uid)['token']
    def redeem(i):
        try: return complete(ResetComplete(token=token, password=PASSWORD + str(i)))['status']
        except HTTPException as exc: return exc.status_code
    with ThreadPoolExecutor(2) as executor: outcomes = list(executor.map(redeem, range(2)))
    assert sorted(outcomes, key=str) == [400, 'password_changed']
