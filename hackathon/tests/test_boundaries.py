from pathlib import Path
import sys
import pytest
from datetime import date
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'aislemint-ops/backend')]
from promo.hackathon import assert_isolated,transform
from hackathon.catalog import history,products


def test_demo_requires_explicit_mode(monkeypatch):
 monkeypatch.delenv('AISLEMINT_HACKATHON',raising=False)
 with pytest.raises(RuntimeError):assert_isolated()

@pytest.mark.parametrize('name',['aislemint','postgres','customer_db',''])
def test_demo_rejects_other_databases(monkeypatch,name):
 monkeypatch.setenv('AISLEMINT_HACKATHON','1');monkeypatch.setenv('POSTGRES_DATABASE',name)
 with pytest.raises(RuntimeError):assert_isolated()

def test_database_url_cannot_override_isolation(monkeypatch):
 monkeypatch.setenv('AISLEMINT_HACKATHON','1');monkeypatch.setenv('POSTGRES_DATABASE','aislemint_hackathon')
 monkeypatch.setenv('DATABASE_URL','postgresql://localhost/aislemint')
 with pytest.raises(RuntimeError,match='Database URL'):assert_isolated()

def test_namespaced_ids_change_but_shared_immutable_paths_stay():
 old='a'*32;new='b'*32
 value={'id':old,'workspace':old,'references':[{'target':old}], 'path':'retail/'+old+'/model','model_id':'promotion-'+old}
 changed=transform(value,{old:new})
 assert changed['id']==new and changed['workspace']==new
 assert changed['references'][0]['target']==new
 assert changed['path']=='retail/'+old+'/model'
 assert changed['model_id']=='promotion-'+new

@pytest.mark.parametrize('kind,count',[('promotion',156*12),('demand',1092*12)])
def test_synthetic_history_is_reproducible_and_uses_same_catalog(kind,count):
 first=history(kind,date(2026,10,18));assert first==history(kind,date(2026,10,18))
 assert len(first)==count and {r['product_id'] for r in first}=={p['product_id'] for p in products()}
 assert all(r['currency']=='INR' and r['units']>=0 and r['price']>0 for r in first)


def test_flyer_conflict_is_a_review_issue_not_a_failed_scan(monkeypatch):
 from types import SimpleNamespace
 from unittest.mock import Mock
 from promo import promotion_ai,gemini
 spec=promotion_ai._FlyerVisionSchema(competitor_summary='Fixture flyer',counter_offers=[
  promotion_ai._ExtractedCompetitorOffer(competitor_item='Chips',competitor_mechanic='15% off',matched_product_id='MINT-CHIPS',recommended_discount_pct=15,recommended_duration_weeks=1,strategic_rationale='Review candidate')])
 monkeypatch.setattr(gemini,'generate_structured',lambda **kw:(spec,'fixture-model'))
 monkeypatch.setattr(promotion_ai,'calendar_snapshot',lambda *_:{})
 engine=SimpleNamespace(catalog=lambda _:[dict(product_id='MINT-CHIPS',name='Chips',base_price=40)],forecast=Mock(side_effect=ValueError('An offer overlaps an exclusive promotion')))
 result=promotion_ai.scan_competitor_flyer(engine,'tenant',promotion_ai.FlyerScanRequest(competitor_text='Chips 15% off'))
 assert result['ai_powered'] is True and result['counter_offers']==[]
 assert result['candidate_issues'][0]['product_id']=='MINT-CHIPS'
