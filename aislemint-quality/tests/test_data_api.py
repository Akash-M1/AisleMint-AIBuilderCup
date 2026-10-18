from promo.storage import AssetPath
import io
import json
import time
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from promo.data import validate_panel
import promo.api as service

@pytest.fixture
def panel(engine):
    # A real, sufficiently long series for exercising the external data contract.
    x=engine.panel[(engine.panel.store_id=='367')&(engine.panel.product_id=='3800031838')].copy()
    return x

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(service,'DATA',AssetPath('test/'+tmp_path.name))
    service.get_engine.cache_clear()
    yield TestClient(service.app)
    service.get_engine.cache_clear()

def test_import_contract_rejects_duplicate_keys(panel):
    with pytest.raises(ValueError,match='Duplicate'):validate_panel(pd.concat([panel,panel.iloc[:1]]))

def test_import_contract_rejects_future_missing_week_and_invalid_prices(panel):
    x=panel.copy();x.loc[x.index[0],'base_price']=0
    with pytest.raises(ValueError,match='prices must be positive'):validate_panel(x)
    x=panel[panel.date!=sorted(panel.date.unique())[50]]
    with pytest.raises(ValueError,match='missing calendar'):validate_panel(x)

def test_api_rejects_cross_origin_mutation(client):
    r=client.post('/api/forecast',headers={'Origin':'https://attacker.example'},json={})
    assert r.status_code==403

def test_api_rejects_unknown_workspace_and_invalid_payload(client):
    assert client.get('/api/summary',headers={'X-Workspace-ID':'../../etc'}).status_code==404
    assert client.post('/api/forecast',json={'promotions':[]}).status_code==422

def test_client_import_training_and_workspace_isolation(client,panel,tmp_path):
    r=client.post('/api/integrations/snapshots',headers={'Authorization':'Bearer test-integration-key'},json=dict(name='Client history',kind='promotion',idempotency_key='client-history-key',records=json.loads(panel.to_json(orient='records',date_format='iso'))))
    assert r.status_code==200,r.text
    id=r.json()['id'];headers={'X-Workspace-ID':id}
    assert client.get('/api/catalog',headers=headers).status_code==503
    r=client.post(f'/api/datasets/{id}/train',json={});assert r.status_code==200
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        status=client.get('/api/datasets').json()[0]['status']
        if status!='training':break
        time.sleep(.1)
    assert status=='ready'
    assert client.post(f'/api/datasets/{id}/train',json={}).status_code==409
    s=client.get('/api/summary',headers=headers);assert s.status_code==200
    assert s.json()['quality']['products']==1 and s.json()['baskets'] is None
    body={'name':'Client plan','store_id':'367','promotions':[{'product_id':'3800031838','discount':.2}]}
    r=client.post('/api/scenarios',headers=headers,json=body);assert r.status_code==200,r.text
    saved=r.json()['id']
    assert len(client.get('/api/scenarios',headers=headers).json())==1
    assert client.get('/api/scenarios').json()==[]
    assert client.get(f'/api/scenarios/{saved}/export').status_code==404
    exported=client.get(f'/api/scenarios/{saved}/export',headers=headers)
    assert exported.status_code==200 and 'baseline,forecast' in exported.text
    assert len(client.get('/api/audit',headers=headers).json())>=2

def test_import_rejects_malformed_and_non_csv(client):
    assert client.post('/api/datasets',files={'file':('bad.csv',b'a,b\n1,2')}).status_code==405
    assert client.post('/api/datasets',files={'file':('file.exe',b'hello')}).status_code==405

def test_legacy_snapshot_is_preserved_when_recalculated(client,engine,monkeypatch):
    body={'name':'Legacy portfolio','store_id':'367','start_date':'2012-01-11',
        'promotions':[{'product_id':'3800031838','discount':.2}]}
    legacy={'effects':{'carryover':19},'incremental_profit':100,'roi':2,'start_date':'2012-01-11'}
    with service.connection() as c:
        c.run('data_api_test_legacy_snapshot_is_preserved_when_recalculated_insert_scenarios', ('legacy', 'benchmark', body['name'], json.dumps(body), json.dumps(legacy), '2026-09-01'))
    with monkeypatch.context() as patch:
        patch.setattr(service,'get_engine',lambda workspace:engine)
        response=client.post('/api/scenarios',json=body)
    assert response.status_code==200,response.text
    snapshots={s['id']:s for s in client.get('/api/scenarios').json()}
    assert snapshots['legacy']['result']==legacy
    current=snapshots[response.json()['id']]['result']
    from promo.engine import CALCULATION_VERSION
    assert current['calculation_version']==CALCULATION_VERSION
    assert 'carryover' not in current['effects']
    assert current['effects']['pantry_loading']<=0
    assert current['planning_profit']<=current['incremental_profit']
    assert current['start_date']==legacy['start_date']
