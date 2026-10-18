from pathlib import Path
import sys,os,json,time
from dotenv import load_dotenv
root=Path(__file__).resolve().parents[1]
if len(sys.argv)>1:load_dotenv(sys.argv[1],override=True)
sys.path[:0]=[str(root),str(root/'aislemint-ops/backend')]
from hackathon.server import app
from fastapi.testclient import TestClient
from promo.pg import database
origin=os.environ.get('PUBLIC_ORIGIN','http://127.0.0.1:8108')
clients=[TestClient(app,base_url=origin),TestClient(app,base_url=origin)];reports=[]
for i,c in enumerate(clients):
 c.headers['Origin']=origin
 t=time.perf_counter();r=c.post('/api/demo/session',json={});assert r.status_code==200,(r.status_code,r.text)
 me=c.get('/api/retail/me');assert me.status_code==200,me.text
 shop=me.json()['retailers'][0]['shops'][0];ws=shop['id'];headers={'X-Workspace-ID':ws}
 for path in ('/api/summary','/api/catalog'):
  r=c.get(path,headers=headers);assert r.status_code==200,(path,r.text)
 for path in ('workbench','connected/overview','data/analytics'):
  r=c.get('/api/retail/shops/'+ws+'/'+path);assert r.status_code==200,(path,r.text)
 pos=c.post('/api/demo/pos-session',json={});assert pos.status_code==200,pos.text
 p=pos.json();auth={'Authorization':'Bearer '+p['token']}
 for path in ('/context','/products','/analytics'):
  r=c.get('/api/pos/stores/'+p['store']['id']+path,headers=auth);assert r.status_code==200,(path,r.text)
 quote=c.post('/api/pos/stores/'+p['store']['id']+'/quote',headers=auth,json={'lines':[{'product_id':'MINT-OATS','quantity':1}]});assert quote.status_code==200,quote.text
 reports.append(dict(workspace=ws,store=p['store']['id'],prepare_seconds=round(time.perf_counter()-t,3),quote_total_paise=quote.json()['total_paise']))
assert reports[0]['workspace']!=reports[1]['workspace']
assert clients[0].get('/api/summary',headers={'X-Workspace-ID':reports[1]['workspace']}).status_code==403
assert clients[0].post('/api/retail/ai/configure',json={'api_key':'never'}).status_code==403
assert clients[0].get('/api/retail/shops/'+reports[1]['workspace']+'/connected/overview').status_code==403
print(json.dumps({'guests':reports,'cross_guest_access':'blocked','key_changes':'blocked'},indent=2))
(root/'.release').mkdir(exist_ok=True)
(root/'.release/verification.json').write_text(json.dumps(reports,indent=2))
# Exercise the actual reviewed-promotion -> executed POS price bridge.
c=clients[0];ws=reports[0]['workspace'];headers={'X-Workspace-ID':ws}
scenario={'store_id':'CUP-BLR-01','promotions':[{'product_id':'MINT-OATS','discount':.15,'duration':1}], 'name':'Judge smoke oats offer'}
f=c.post('/api/forecast',headers=headers,json=scenario);assert f.status_code==200,f.text
result=f.json()
confirm=c.post('/api/promotions/confirm',headers=headers,json={'scenario':scenario,'review_token':result['confirmation_token'],'idempotency_key':'smoke-confirm-oats'});assert confirm.status_code==200,confirm.text
p=c.post('/api/demo/pos-session',json={}).json();auth={'Authorization':'Bearer '+p['token']}
q=c.post('/api/pos/stores/'+p['store']['id']+'/quote',headers=auth,json={'lines':[{'product_id':'MINT-OATS','quantity':1}]});assert q.status_code==200,q.text
assert q.json()['total_paise']==15300,q.text
print('Confirmed offer reaches the same POS SKU: ₹180 -> ₹153 including GST.')
# Model origin must have a compatible observed inventory snapshot.
d=c.get('/api/demand/summary',params={'source_id':ws});assert d.status_code==200,d.text
from promo.data_platform.service import stock_position
from promo.retail import auth as auth_module
user=database().retail_users.find_one({'demo_workspace':ws})
token=auth_module.principal.set(dict(id=user['id'],admin=False,memberships=user['memberships']))
try:assert stock_position(ws,d.json()['quality']['end'])['positions']
finally:auth_module.principal.reset(token)
assert c.post('/api/demo/reset',json={}).status_code==200
new=c.get('/api/retail/me').json()['retailers'][0]['shops'][0]['id'];assert new!=ws
assert c.get('/api/summary',headers={'X-Workspace-ID':ws}).status_code==403
print('Reset creates a fresh isolated guest; the old workspace cannot be accessed by the new session.')
