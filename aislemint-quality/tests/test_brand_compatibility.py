"""The public rebrand must preserve connections and shop identity boundaries."""
import json
import pytest
from promo.brand import setting, retailer_name
from promo.connected.connectors import bindings, secret


def test_new_configuration_wins_and_empty_disables_legacy_model(monkeypatch):
    monkeypatch.setenv('HINDSIGHT_SOURCE_BINDINGS', json.dumps({'old': {'workspace': 'shop-a'}}))
    monkeypatch.delenv('AISLEMINT_SOURCE_BINDINGS', raising=False)
    assert set(bindings('shop-a')) == {'old'}
    monkeypatch.setenv('AISLEMINT_SOURCE_BINDINGS', json.dumps({'new': {'workspace': 'shop-b'}}))
    assert bindings('shop-a') == {}
    assert set(bindings('shop-b')) == {'new'}
    monkeypatch.setenv('HINDSIGHT_LOCAL_AI_MODEL', 'legacy-model')
    monkeypatch.setenv('AISLEMINT_LOCAL_AI_MODEL', '')
    assert setting('LOCAL_AI_MODEL') == ''


def test_connector_secret_names_keep_both_prefixes_without_widening_access(monkeypatch):
    for prefix in ['AISLEMINT_CONNECT_', 'HINDSIGHT_CONNECT_']:
        monkeypatch.setenv(prefix + 'TEST_TOKEN', 'test-only')
        assert secret({'token_env': prefix + 'TEST_TOKEN'}, 'token') == 'test-only'
    monkeypatch.setenv('UNRELATED_SECRET', 'must-not-read')
    with pytest.raises(ValueError, match='Connector secrets'):
        secret({'token_env': 'UNRELATED_SECRET'}, 'token')


def test_demo_label_does_not_rename_customer_records_or_change_ids():
    legacy = dict(id='dbec888923f752659a34caf284055d60', demo_package='connected-retail-demo-v1', name='Hindsight Demo · Simulated Retail')
    original = dict(legacy)
    assert retailer_name(legacy) == 'AisleMint Demo · Simulated Retail'
    assert legacy == original
    assert retailer_name({**legacy, 'id': 'customer-retailer'}) == legacy['name']
    assert retailer_name({**legacy, 'demo_package': None}) == legacy['name']
    assert retailer_name({**legacy, 'name': 'Owner-customized demo'}) == 'Owner-customized demo'
