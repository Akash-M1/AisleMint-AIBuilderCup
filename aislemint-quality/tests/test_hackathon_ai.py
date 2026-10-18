"""AI boundary regressions. Model calls are mocked; POS prices use real checkout rules."""
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest
from pydantic import BaseModel

from promo import api, gemini, pos_ai, promotion_ai
from promo.connected.insights import _build_copilot_tools
from test_pos import pos, install_offer


@pytest.fixture(autouse=True)
def offline_ai(monkeypatch):
    monkeypatch.setattr(gemini, 'get_api_key', lambda: '')
    gemini._memory_cache.clear()
    yield
    gemini._memory_cache.clear()


def shopper(pos, **body):
    return pos[2]('/shopper/assist', body)


def suggest(monkeypatch, product='milk', quantity=1):
    spec = pos_ai._AIShopperSchema(
        occasion_tag='Test', assistant_message='Unverified model amounts must not be displayed.',
        items=[pos_ai._AIItemSpec(product_id=product, quantity=quantity, reason='Fixture')])
    monkeypatch.setattr(gemini, 'generate_structured', lambda **kw: (spec, 'fixture-model'))


def test_single_item_cannot_exceed_budget(pos):
    result = shopper(pos, budget_rupees=100)
    assert result['cart'] == {} and result['quote_summary'] is None
    assert result['status'] == 'needs_revision'


def test_single_sku_quantity_reduced_using_real_quote(pos, monkeypatch):
    suggest(monkeypatch, quantity=50)
    result = shopper(pos, budget_rupees=236)
    assert result['cart'] == {'milk': 2}
    assert result['quote_summary']['total_paise'] == 23600
    assert 'Unverified' not in result['assistant_message']
    assert result['model'] == 'fixture-model' and result['ai_powered'] is True


def test_exact_budget_includes_gst(pos):
    result = shopper(pos, budget_rupees=118)
    assert result['quote_summary']['total_paise'] == 11800
    assert result['quote_summary']['tax_paise'] == 1800
    assert result['ai_powered'] is False


def test_model_cannot_add_unknown_sku(pos, monkeypatch):
    suggest(monkeypatch, product='invented')
    result = shopper(pos, budget_rupees=118)
    assert result['cart'] == {'milk': 1}


def test_model_quantity_clamped_to_stock(pos, monkeypatch):
    suggest(monkeypatch, quantity=50)
    assert shopper(pos)['cart'] == {'milk': 20}


def test_budget_rechecked_after_promotion_pricing(pos, monkeypatch):
    install_offer(pos, discount=.1)
    suggest(monkeypatch, quantity=3)
    result = shopper(pos, budget_rupees=220)
    assert result['cart'] == {'milk': 2}
    assert result['quote_summary']['total_paise'] == 21240


def test_empty_inventory_has_no_ai_claim(pos):
    pos[2]('/stock', dict(product_id='milk', quantity=-20, kind='adjustment',
                         reason='Empty shelf', idempotency_key='empty-shelf'))
    result = shopper(pos)
    assert result['cart'] == {} and result['ai_powered'] is False


def test_no_cross_workspace_optimizer_fallback(monkeypatch):
    get_engine = Mock(side_effect=RuntimeError('private model path'))
    monkeypatch.setattr(api, 'get_engine', get_engine)
    optimizer = _build_copilot_tools('tenant-without-model', {}, [])[-1]
    result = optimizer()
    assert result['status'] == 'unavailable'
    get_engine.assert_called_once_with('tenant-without-model')
    assert 'private model path' not in result['reason']


@pytest.mark.parametrize('budget', [-1, 0, float('nan'), 50001])
def test_invalid_optimizer_budget_never_calls_engine(monkeypatch, budget):
    get_engine = Mock()
    monkeypatch.setattr(api, 'get_engine', get_engine)
    result = _build_copilot_tools('tenant', {}, [])[-1](budget=budget)
    assert result['status'] == 'unavailable'
    get_engine.assert_not_called()


def test_optimizer_respects_small_budget(monkeypatch):
    engine = SimpleNamespace(panel=pd.DataFrame({'store_id': ['store']}), optimize=Mock(return_value={}))
    monkeypatch.setattr(api, 'get_engine', lambda _: engine)
    _build_copilot_tools('tenant', {}, [])[-1](budget=100)
    assert engine.optimize.call_args.args[0].budget == 100


class Payload(BaseModel):
    value: str


def test_structured_cache_is_scoped_expires_and_reports_actual_model(monkeypatch):
    monkeypatch.setattr(gemini, 'get_api_key', lambda: 'fake')
    generate = Mock(return_value=SimpleNamespace(text='{"value":"ok"}'))
    monkeypatch.setattr(gemini, '_get_genai_client', lambda _: SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    clock = [100.0]
    monkeypatch.setattr(gemini, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    for tenant in ('one', 'one', 'two'):
        _, model = gemini.generate_structured('same', Payload, workspace=tenant, fallback=Payload(value='offline'))
        assert model == gemini.FAST_MODEL
    assert generate.call_count == 2
    assert generate.call_args.kwargs['config'].max_output_tokens == gemini.MAX_OUTPUT_TOKENS
    clock[0] += gemini.CACHE_TTL_SECONDS + 1
    gemini.generate_structured('same', Payload, workspace='one')
    assert generate.call_count == 3


def test_invalid_structured_response_has_bounded_attempts(monkeypatch):
    monkeypatch.setattr(gemini, 'get_api_key', lambda: 'fake')
    generate = Mock(return_value=SimpleNamespace(text='not valid JSON'))
    monkeypatch.setattr(gemini, '_get_genai_client', lambda _: SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    fallback = Payload(value='offline')
    assert gemini.generate_structured('same', Payload, fallback=fallback) == (fallback, False)
    assert generate.call_count == gemini.MAX_MODEL_ATTEMPTS
    assert not gemini._memory_cache


def test_text_cache_disabled_really_disables_cache(monkeypatch):
    generate = Mock(return_value={'output_text': 'ok', 'model': 'fixture'})
    monkeypatch.setattr(gemini, '_create_interaction', generate)
    for _ in range(2):
        gemini.generate_text('same', use_cache=False)
    assert generate.call_count == 2 and not gemini._memory_cache


def test_no_key_never_creates_sdk_client(monkeypatch):
    client = Mock()
    monkeypatch.setattr(gemini, '_get_genai_client', client)
    with pytest.raises(RuntimeError, match='not configured'):
        gemini.generate_structured('same', Payload)
    client.assert_not_called()


def test_tool_signature_resolves_postponed_annotations():
    optimizer = _build_copilot_tools('tenant', {}, [])[-1]
    declaration, _ = gemini._callable_to_tool_spec(optimizer)
    assert declaration['parameters']['properties']['budget']['type'] == 'number'


def test_agent_caps_parallel_tool_execution(monkeypatch):
    monkeypatch.setattr(gemini, 'get_api_key', lambda: 'fake')
    calls = [{'type': 'function_call', 'name': 'lookup', 'id': str(i), 'arguments': {}} for i in range(20)]
    monkeypatch.setattr(gemini, '_create_interaction', lambda **kw: {'model': 'fixture', 'steps': calls})
    handler = Mock(return_value={})
    result = gemini.run_agent_with_tools('request', system_instruction='test',
              tool_handlers={'lookup': handler}, fallback_text='Unavailable')
    assert handler.call_count == gemini.MAX_TOOL_CALLS
    assert result['ai_powered'] is False and result['answer'] == 'Unavailable'


def test_agent_caps_rounds_and_rejects_unfinished_answer(monkeypatch):
    monkeypatch.setattr(gemini, 'get_api_key', lambda: 'fake')
    create = Mock(return_value={'model': 'fixture', 'steps': [
        {'type': 'function_call', 'name': 'lookup', 'id': '1', 'arguments': {}}]})
    monkeypatch.setattr(gemini, '_create_interaction', create)
    result = gemini.run_agent_with_tools('request', system_instruction='test', max_rounds=100,
              tool_handlers={'lookup': lambda _: {}}, fallback_text='Unavailable')
    assert create.call_count == gemini.MAX_TOOL_ROUNDS + 1
    assert result['ai_powered'] is False


@pytest.fixture
def flyer_engine(monkeypatch):
    monkeypatch.setattr(promotion_ai, 'calendar_snapshot', lambda *_: [])
    return SimpleNamespace(
        catalog=Mock(return_value=[dict(product_id='milk', name='Milk', base_price=118, category='Dairy')]),
        forecast=Mock(return_value=dict(total_units=20, planning_profit=-10, investment=40, incremental_units=5)))


def test_flyer_missing_input_cannot_make_offers(flyer_engine):
    result = promotion_ai.scan_competitor_flyer(flyer_engine, 'tenant', promotion_ai.FlyerScanRequest())
    assert result['status'] == 'needs_input' and result['counter_offers'] == []
    flyer_engine.catalog.assert_not_called()


def test_flyer_generation_failure_does_not_invent_offers(flyer_engine):
    result = promotion_ai.scan_competitor_flyer(flyer_engine, 'tenant',
        promotion_ai.FlyerScanRequest(image_base64='unreadable-fixture'))
    assert not result['ai_powered'] and result['counter_offers'] == []
    flyer_engine.forecast.assert_not_called()


def test_flyer_empty_store_does_not_use_all_stores(flyer_engine):
    flyer_engine.catalog.return_value = []
    result = promotion_ai.scan_competitor_flyer(flyer_engine, 'tenant', promotion_ai.FlyerScanRequest(competitor_text='Milk 25% off'))
    assert result['status'] == 'no_catalog'
    flyer_engine.catalog.assert_called_once_with('367')


@pytest.mark.parametrize('product', ['invented', 'milk'])
def test_flyer_unknown_sku_rejected_valid_sku_forecast(flyer_engine, monkeypatch, product):
    spec = promotion_ai._FlyerVisionSchema(competitor_summary='Fixture flyer', counter_offers=[
        promotion_ai._ExtractedCompetitorOffer(competitor_item='Milk', competitor_mechanic='25% off',
            matched_product_id=product, recommended_discount_pct=20, recommended_duration_weeks=2,
            strategic_rationale='Review this candidate')])
    generate = Mock(return_value=(spec, 'actual-fixture-model'))
    monkeypatch.setattr(gemini, 'generate_structured', generate)
    result = promotion_ai.scan_competitor_flyer(flyer_engine, 'tenant', promotion_ai.FlyerScanRequest(competitor_text='Milk 25% off'))
    assert generate.call_args.kwargs['workspace'] == 'tenant'
    assert result['model'] == 'actual-fixture-model'
    assert len(result['counter_offers']) == (1 if product == 'milk' else 0)
    assert flyer_engine.forecast.call_count == (2 if product == 'milk' else 0)


def test_empty_model_extraction_stays_empty(flyer_engine, monkeypatch):
    spec = promotion_ai._FlyerVisionSchema(competitor_summary='No readable promotions')
    monkeypatch.setattr(gemini, 'generate_structured', lambda **kw: (spec, 'fixture'))
    result = promotion_ai.scan_competitor_flyer(flyer_engine, 'tenant', promotion_ai.FlyerScanRequest(competitor_text='unrelated image'))
    assert result['counter_offers'] == []
    flyer_engine.forecast.assert_not_called()


def test_risk_citation_tracks_existing_evidence_order():
    existing = {'id': 'first'}
    record = {'id': 'risk'}
    evidence = [existing, record]
    report = {'findings': [dict(kind='low_stock', label='Milk', detail='Stock 1', evidence=record)],
              'counts': {'low_stock': 1}, 'stale_sources': []}
    result = _build_copilot_tools('tenant', report, evidence)[0]()
    assert result['sample_findings'][0]['citation'] == 2
    assert len(evidence) == 2


def test_invalid_gemini_citation_falls_back_to_verified_evidence(monkeypatch):
    from promo.connected import insights
    record = dict(id='risk', revision=1, source_id='source', label='Milk', provenance={})
    report = {'findings': [dict(kind='low_stock', label='Milk', detail='Stock 1', evidence=record)],
              'counts': {'low_stock': 1}, 'stale_sources': []}
    monkeypatch.setattr(insights, 'risks', lambda _: report)
    monkeypatch.setattr(gemini, 'get_api_key', lambda: 'fake')
    monkeypatch.setattr(gemini, 'run_agent_with_tools', lambda **kw: dict(
        answer='Invented evidence [99]', model='fixture', ai_powered=True, tool_trace=[]))
    insert = Mock()
    monkeypatch.setattr(insights, 'database', lambda: SimpleNamespace(connect_questions=SimpleNamespace(insert_one=insert)))
    result = insights.answer('tenant', 'low stock', 'actor', use_gemini=True)
    assert result['mode'] == 'evidence_search'
    assert 'Invented' not in result['answer'] and '[1]' in result['answer']
    assert result['warning'] and result['model'] is None


def test_campaign_draft_cache_uses_requested_workspace(monkeypatch):
    generate = Mock(side_effect=lambda **kw: (kw['fallback'], False))
    monkeypatch.setattr(gemini, 'generate_structured', generate)
    promotion_ai.generate_campaign_kit(promotion_ai.CampaignKitRequest(product_id='milk'), workspace='tenant')
    assert generate.call_args.kwargs['workspace'] == 'tenant'
