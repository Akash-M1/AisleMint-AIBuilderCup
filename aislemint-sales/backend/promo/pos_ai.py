"""Gemini-powered Conversational Shopper, Live Deal Nudge Engine, and Fraud & Margin Radar for OneSales POS."""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from . import gemini
from .pos_core import Cart, CartLine, StrictModel, executed_offers, local_day, quote


class ShopperAssistInput(StrictModel):
    prompt: str = Field(default='', max_length=1500)
    budget_rupees: float | None = Field(default=None, ge=0.01, le=500000)
    current_cart: dict[str, int] = Field(default_factory=dict)
    image_base64: str | None = Field(default=None, max_length=6_000_000)
    image_mime_type: str = Field(default='image/jpeg', max_length=60)


class FraudRadarInput(StrictModel):
    start: str = ''
    end: str = ''


class _AIItemSpec(BaseModel):
    product_id: str = Field(description='Exact product_id from the store catalog.')
    quantity: int = Field(ge=1, le=50, description='Recommended quantity to add to the basket.')
    reason: str = Field(description='Short shopper-friendly reason why this item fits the request or unlocks an offer.')


class _AIShopperSchema(BaseModel):
    occasion_tag: str = Field(description='Short 2-4 word title for the shopping mission or list.')
    assistant_message: str = Field(description='Warm, helpful 2-3 sentence explanation of how the basket was curated and how much is saved.')
    items: list[_AIItemSpec] = Field(default_factory=list, description='Selected catalog items for the basket.')
    deal_tips: list[str] = Field(default_factory=list, description='1-3 short tips on active promotions unlocked or near-miss deals.')


class _AIFraudSchema(BaseModel):
    executive_summary: str = Field(description='Concise 2-4 sentence forensic audit summary of margin leakage, return anomalies, and cash drawer health.')
    recommended_controls: list[str] = Field(default_factory=list, description='3-4 concrete operational controls for the store manager.')


def compute_upsell_nudges(
    cart: dict[str, int],
    products: dict[str, dict[str, Any]],
    offers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Inspect active executed offers against the current cart to surface 1-click deal nudges."""
    nudges: list[dict[str, Any]] = []
    seen: set[str] = set()

    for event in offers:
        terms = event.get('terms') or {}
        pid = terms.get('product_id')
        if not pid or pid not in products:
            continue
        prod = products[pid]
        if not prod.get('active') or prod.get('on_hand', 0) <= 0:
            continue

        qty = int(cart.get(pid, 0))
        price_paise = int(prod['price_paise'])
        discount = float(terms.get('discount') or 0.0)
        mechanics = terms.get('mechanics') or []

        # 1. Near-miss conditional mechanics (BXGY, BXGCY, Spend Threshold)
        for mech in mechanics:
            kind = mech.get('kind')
            buy_qty = int(mech.get('buy_quantity') or 0)
            free_qty = int(mech.get('free_quantity') or 1)
            key = f'{pid}:{kind}:{buy_qty}'
            if key in seen:
                continue

            if kind == 'bxgy' and buy_qty > 0:
                total_needed = buy_qty + free_qty
                if 0 < qty < total_needed and prod['on_hand'] >= total_needed:
                    add_qty = total_needed - qty
                    saving = free_qty * price_paise
                    seen.add(key)
                    nudges.append({
                        'id': key,
                        'kind': 'bxgy',
                        'title': f'Unlock Buy {buy_qty} Get {free_qty} Free on {prod["name"]}',
                        'description': f'Add {add_qty} more unit(s) of {prod["name"]} to get {free_qty} unit(s) free (save ₹{saving / 100:,.2f}).',
                        'action_product_id': pid,
                        'action_add_qty': add_qty,
                        'reward_product_id': None,
                        'reward_group': None,
                        'estimated_saving_paise': saving,
                    })
            elif kind == 'bxgcy' and buy_qty > 0:
                reward_choices = [
                    r for r in (mech.get('reward_choices') or [])
                    if r in products and products[r].get('active') and products[r].get('on_hand', 0) > 0
                ]
                if not reward_choices:
                    continue
                best_reward = max(reward_choices, key=lambda r: products[r]['price_paise'])
                reward_prod = products[best_reward]
                saving = int(reward_prod['price_paise']) * max(1, free_qty)
                if qty >= buy_qty and int(cart.get(best_reward, 0)) == 0:
                    seen.add(key)
                    nudges.append({
                        'id': key,
                        'kind': 'bxgcy_reward',
                        'title': f'Claim Free {reward_prod["name"]} Cross-Reward!',
                        'description': f'Your basket qualifies for a free {reward_prod["name"]} (worth ₹{saving / 100:,.2f}) with {prod["name"]}.',
                        'action_product_id': best_reward,
                        'action_add_qty': 1,
                        'reward_product_id': best_reward,
                        'reward_group': f'{pid}:{mech.get("stack_group", "g1")}',
                        'estimated_saving_paise': saving,
                    })
                elif 0 < qty < buy_qty and prod['on_hand'] >= buy_qty:
                    add_qty = buy_qty - qty
                    seen.add(key)
                    nudges.append({
                        'id': key,
                        'kind': 'bxgcy_qualify',
                        'title': f'Add {add_qty} more {prod["name"]} for a Free {reward_prod["name"]}',
                        'description': f'Buy {buy_qty} {prod["name"]} to unlock a free {reward_prod["name"]} worth ₹{saving / 100:,.2f}.',
                        'action_product_id': pid,
                        'action_add_qty': add_qty,
                        'reward_product_id': best_reward,
                        'reward_group': f'{pid}:{mech.get("stack_group", "g1")}',
                        'estimated_saving_paise': saving,
                    })
            elif kind == 'spend_threshold':
                factor = 1 + prod.get('gst_bps', 0) / 10000
                threshold_paise = int(round(float(mech.get('threshold_spend') or 0) * factor * 100))
                amount_off_paise = int(round(float(mech.get('amount_off') or 0) * factor * 100))
                current_spend = qty * price_paise
                if 0 < current_spend < threshold_paise and amount_off_paise > 0:
                    shortfall = threshold_paise - current_spend
                    add_qty = max(1, (shortfall + price_paise - 1) // price_paise)
                    if qty + add_qty <= prod['on_hand']:
                        seen.add(key)
                        nudges.append({
                            'id': key,
                            'kind': 'spend_threshold',
                            'title': f'Spend ₹{shortfall / 100:,.0f} more on {prod["name"]} to save ₹{amount_off_paise / 100:,.0f}',
                            'description': f'Add {add_qty} more unit(s) of {prod["name"]} to cross the ₹{threshold_paise / 100:,.0f} threshold.',
                            'action_product_id': pid,
                            'action_add_qty': add_qty,
                            'reward_product_id': None,
                            'reward_group': None,
                            'estimated_saving_paise': amount_off_paise,
                        })

        # 2. If item is not yet in cart but has a strong TPR or conditional offer, suggest as a featured deal
        if qty == 0 and (discount >= 0.05 or mechanics):
            key = f'{pid}:featured'
            if key not in seen:
                seen.add(key)
                saving = int(round(price_paise * discount)) if discount > 0 else int(price_paise * 0.15)
                label = f'{int(round(discount * 100))}% Instant TPR Off' if discount > 0 else 'Active Bundle Offer'
                nudges.append({
                    'id': key,
                    'kind': 'featured_offer',
                    'title': f'Hot Deal: {prod["name"]} ({label})',
                    'description': f'Campaign "{event.get("name", "Store Special")}" is active today on {prod["name"]} (₹{price_paise / 100:,.2f}).',
                    'action_product_id': pid,
                    'action_add_qty': 1,
                    'reward_product_id': None,
                    'reward_group': None,
                    'estimated_saving_paise': max(saving, 100),
                })

    nudges.sort(key=lambda x: (-int(x.get('estimated_saving_paise') or 0), x['id']))
    return nudges[:5]


def _fallback_shopper_curate(
    prompt: str,
    budget_paise: int | None,
    products: dict[str, dict[str, Any]],
    offers_by_pid: dict[str, list[dict[str, Any]]],
    current_cart: dict[str, int],
) -> _AIShopperSchema:
    tokens = set(re.findall(r'[a-z0-9]+', (prompt or '').lower()))
    scored: list[tuple[float, dict[str, Any]]] = []
    for pid, prod in products.items():
        if not prod.get('active') or prod.get('on_hand', 0) <= 0:
            continue
        text = f'{prod["name"]} {prod["category"]} {pid}'.lower()
        match_score = sum(3.0 for t in tokens if len(t) > 2 and t in text)
        offer_events = offers_by_pid.get(pid, [])
        promo_score = 2.5 if offer_events else 0.0
        if offer_events:
            max_disc = max(float(e['terms'].get('discount') or 0) for e in offer_events)
            promo_score += max_disc * 10.0
        # Slight preference for affordable everyday items when budget is tight
        affordability = max(0.1, 1.0 - (prod['price_paise'] / 100000))
        scored.append((match_score + promo_score + affordability, prod))

    scored.sort(key=lambda item: (-item[0], item[1]['price_paise']))
    chosen: list[_AIItemSpec] = []
    running_paise = 0
    max_budget = budget_paise or 150000  # default ₹1,500 target if unspecified

    for _, prod in scored:
        pid = prod['product_id']
        price = int(prod['price_paise'])
        offer_events = offers_by_pid.get(pid, [])
        rec_qty = max(1, int(current_cart.get(pid, 0)))
        reason = f'Popular {prod["category"]} essential in stock ({prod["on_hand"]} on shelf).'

        if offer_events:
            terms = offer_events[0]['terms']
            disc = float(terms.get('discount') or 0)
            mechs = terms.get('mechanics') or []
            if mechs and mechs[0].get('kind') in ('bxgy', 'bxgcy'):
                buy_q = int(mechs[0].get('buy_quantity') or 1)
                free_q = int(mechs[0].get('free_quantity') or 1)
                rec_qty = min(int(prod['on_hand']), buy_q + (free_q if mechs[0].get('kind') == 'bxgy' else 0))
                reason = f'Unlocks {mechs[0]["kind"].upper()} promotion ({offer_events[0]["name"]}).'
            elif disc > 0:
                reason = f'Includes {int(round(disc * 100))}% instant TPR savings from {offer_events[0]["name"]}.'

        rec_qty = min(rec_qty, int(prod['on_hand']), 10)
        if running_paise + price * rec_qty > max_budget:
            if running_paise + price <= max_budget:
                rec_qty = 1
            else:
                continue

        chosen.append(_AIItemSpec(product_id=pid, quantity=rec_qty, reason=reason))
        running_paise += price * rec_qty
        if len(chosen) >= 5:
            break

    occasion = 'Smart Promo Basket'
    if 'party' in (prompt or '').lower() or 'cricket' in (prompt or '').lower():
        occasion = 'Match-Day Party Kit'
    elif 'breakfast' in (prompt or '').lower() or 'morning' in (prompt or '').lower():
        occasion = 'Morning Essentials'
    elif budget_paise:
        occasion = f'Under ₹{budget_paise / 100:,.0f} Value Basket'

    return _AIShopperSchema(
        occasion_tag=occasion,
        assistant_message=(
            f'Curated {len(chosen)} in-stock products tailored to your request'
            + (f' for your ₹{budget_paise / 100:,.2f} budget' if budget_paise else '')
            + ', prioritizing active store promotions and exact GST-inclusive checkout pricing.'
        ),
        items=chosen,
        deal_tips=[
            'All active TPR and bundle offers are automatically verified by the OneSales pricing engine.',
            'Tap "Load into Register" to transfer this exact basket to the live checkout counter.',
        ],
    )


def assist_shopper(con: Any, store: dict[str, Any], req: ShopperAssistInput) -> dict[str, Any]:
    """Run Gemini-assisted conversational basket building + exact integer-paise OneSales quote."""
    products = {
        r['product_id']: dict(r)
        for r in con.run('pos_core_quote_aggregate_pos_products', (store['id'],))
        if r['active'] and r['on_hand'] > 0
    }
    offers, _ = executed_offers(con, store)
    offers_by_pid: dict[str, list[dict[str, Any]]] = {}
    for e in offers:
        pid = e.get('terms', {}).get('product_id')
        if pid:
            offers_by_pid.setdefault(pid, []).append(e)

    budget_paise = int(round(req.budget_rupees * 100)) if req.budget_rupees else None

    if not products:
        return {
            'ai_powered': False,
            'model': 'deterministic-fallback',
            'occasion_tag': 'Catalog Empty',
            'assistant_message': 'No active in-stock products are currently on the shelf in this store.',
            'cart': {},
            'selected_rewards': {},
            'items': [],
            'quote_summary': None,
            'upsell_nudges': [],
            'deal_tips': [],
        }

    # Build compact catalog digest for Gemini
    catalog_digest = []
    for pid, p in list(products.items())[:60]:
        prod_offers = offers_by_pid.get(pid, [])
        offer_desc = []
        for ev in prod_offers:
            t = ev['terms']
            if t.get('discount', 0) > 0:
                offer_desc.append(f"{int(round(t['discount']*100))}% TPR off")
            for m in t.get('mechanics', []):
                if m['kind'] == 'bxgy':
                    offer_desc.append(f"Buy {m['buy_quantity']} Get {m.get('free_quantity', 1)} Free")
                elif m['kind'] == 'bxgcy':
                    offer_desc.append(f"Buy {m['buy_quantity']} Get Choice Free ({','.join(m.get('reward_choices', []))})")
                elif m['kind'] == 'spend_threshold':
                    offer_desc.append(f"Spend ₹{m['threshold_spend']} Get ₹{m['amount_off']} Off")
        catalog_digest.append({
            'product_id': pid,
            'name': p['name'],
            'category': p['category'],
            'price_rupees': round(p['price_paise'] / 100, 2),
            'on_hand': p['on_hand'],
            'active_offers': offer_desc,
        })

    fallback_spec = _fallback_shopper_curate(
        req.prompt, budget_paise, products, offers_by_pid, req.current_cart
    )

    prompt_text = (
        f"Store: {store['name']} (Currency: INR, GST-inclusive pricing).\n"
        f"Shopper Request: {req.prompt or 'Recommend the best value promotional basket from active store deals.'}\n"
        f"Target Budget: {'₹' + str(req.budget_rupees) if req.budget_rupees else 'Flexible (optimize value & savings)'}\n"
        f"Current Basket: {json.dumps(req.current_cart)}\n"
        f"Available In-Stock Catalog & Active Promotions:\n{json.dumps(catalog_digest, ensure_ascii=False)}\n\n"
        "Instructions:\n"
        "1. If an image of a handwritten shopping list or pantry is attached, match the items on the list to the closest `product_id`s in the catalog.\n"
        "2. Prioritize items with `active_offers` (TPR discounts, BXGY, BXGCY, spend thresholds) so the shopper maximizes real savings.\n"
        "3. Respect the shopper's target budget if specified, and never exceed `on_hand` stock.\n"
        "4. Return only valid `product_id`s from the catalog above."
    )

    ai_spec, used_gemini = gemini.generate_structured(
        prompt=prompt_text,
        schema=_AIShopperSchema,
        system_instruction=(
            "You are AisleMint Shopper, a neuro-symbolic retail concierge for an Indian grocery & retail store. "
            "You curate high-value shopping baskets from real store inventory and active promotions while the deterministic OneSales engine computes exact paise & GST."
        ),
        model=gemini.DEFAULT_MODEL,
        image_base64=req.image_base64,
        image_mime_type=req.image_mime_type,
        fallback=fallback_spec,
        workspace=f"pos:{store['id']}",
    )

    # Sanitize & validate AI items against actual stock
    curated_cart: dict[str, int] = {}
    reasons_by_pid: dict[str, str] = {}
    for item in ai_spec.items[:12]:
        pid = item.product_id
        if pid in products:
            max_q = min(int(products[pid]['on_hand']), 50)
            q = max(1, min(int(item.quantity), max_q))
            curated_cart[pid] = q
            reasons_by_pid[pid] = item.reason

    if not curated_cart:
        for item in fallback_spec.items:
            if item.product_id in products:
                curated_cart[item.product_id] = min(item.quantity, int(products[item.product_id]['on_hand']))
                reasons_by_pid[item.product_id] = item.reason

    # Auto-attach eligible BXGCY choice rewards if a qualifying item is in the curated cart
    selected_rewards: dict[str, str] = {}
    for pid, q in list(curated_cart.items()):
        for ev in offers_by_pid.get(pid, []):
            for mech in ev['terms'].get('mechanics', []):
                if mech.get('kind') == 'bxgcy' and q >= int(mech.get('buy_quantity') or 1):
                    choices = [
                        r for r in (mech.get('reward_choices') or [])
                        if r in products and products[r]['on_hand'] > 0
                    ]
                    if choices:
                        reward_pid = choices[0]
                        group_key = f"{pid}:{mech.get('stack_group', 'g1')}"
                        selected_rewards[group_key] = reward_pid
                        if reward_pid not in curated_cart:
                            curated_cart[reward_pid] = 1
                            reasons_by_pid[reward_pid] = f"Free cross-reward unlocked by {products[pid]['name']}!"

    # Validate every candidate with the checkout engine, including a single SKU.
    # Promotion thresholds are non-linear: always re-quote after changing quantities.
    priced = None
    for _ in range(120):
        if not curated_cart:
            break
        cart_model = Cart(
            lines=[CartLine(product_id=k, quantity=v) for k, v in curated_cart.items()],
            selected_rewards=selected_rewards,
        )
        try:
            candidate = quote(con, store, cart_model, check_stock=True)
        except ValueError:
            # Never bypass a tax or promotion validation rule to manufacture a quote.
            break
        if budget_paise is None or candidate['total_paise'] <= budget_paise:
            priced = candidate
            break
        last_pid = next(reversed(curated_cart))
        quantity = curated_cart[last_pid]
        if quantity > 1:
            curated_cart[last_pid] = max(1, min(quantity - 1, int(
                quantity * budget_paise / max(1, candidate['total_paise']))))
        else:
            del curated_cart[last_pid]
            selected_rewards = {
                k: v for k, v in selected_rewards.items()
                if not k.startswith(last_pid + ':') and v in curated_cart
            }

    if priced is None:
        return {
            'ai_powered': bool(used_gemini),
            'model': used_gemini or 'deterministic-fallback',
            'status': 'needs_revision',
            'occasion_tag': 'Revise your basket',
            'assistant_message': (
                f"I couldn't build a verified basket within ₹{budget_paise / 100:,.2f}. Try a higher budget or fewer items."
                if budget_paise is not None else
                "I couldn't price this basket safely. Try fewer items or review the store's promotion and tax rules."
            ),
            'cart': {}, 'selected_rewards': {}, 'items': [], 'quote_summary': None,
            'upsell_nudges': [], 'deal_tips': [],
        }

    enriched_items = []
    for line in priced['lines']:
        pid = line['product_id']
        enriched_items.append({
            **line,
            'reason': reasons_by_pid.get(pid, 'Selected for your basket.'),
        })

    nudges = compute_upsell_nudges(curated_cart, products, offers)

    return {
        'ai_powered': bool(used_gemini),
        'model': used_gemini or 'deterministic-fallback',
        'occasion_tag': ai_spec.occasion_tag,
        'assistant_message': (f"Verified {len(curated_cart)} in-stock products. Checkout total: ₹{priced['total_paise'] / 100:,.2f} "
                              f"including GST; savings: ₹{priced['discount_paise'] / 100:,.2f}."),
        'cart': curated_cart,
        'selected_rewards': selected_rewards,
        'items': enriched_items,
        'quote_summary': {
            'subtotal_paise': priced['subtotal_paise'],
            'tax_paise': priced['tax_paise'],
            'discount_paise': priced['discount_paise'],
            'total_paise': priced['total_paise'],
            'applied_promotions': priced['applied_promotions'],
        },
        'upsell_nudges': nudges,
        'deal_tips': ai_spec.deal_tips,
    }


def run_fraud_radar(con: Any, store: dict[str, Any], req: FraudRadarInput) -> dict[str, Any]:
    """Analyze POS sales, credit notes, shifts, and stock writeoffs for fraud & margin leakage."""
    from .pos_reports import analytics, period
    start, end = period(store, req.start, req.end)
    report = analytics(con, store, start, end)
    kpis = report['kpis']

    anomalies: list[dict[str, Any]] = []
    margin_at_risk_paise = 0

    # 1. Negative contribution products (over-discounting or below-cost promotion stacking)
    neg_products = [p for p in report['products'] if p['contribution_paise'] < 0]
    for p in neg_products[:5]:
        loss = abs(int(p['contribution_paise']))
        margin_at_risk_paise += loss
        anomalies.append({
            'id': f"neg-margin-{p['product_id']}",
            'severity': 'high' if loss >= 50000 else 'medium',
            'category': 'Margin Leakage / Stacking',
            'title': f"Negative net contribution on {p['name']} ({p['product_id']})",
            'detail': (
                f"Sold {p['units_sold']} units with ₹{p['discount_paise']/100:,.2f} discounts and "
                f"₹{p['stock_writeoff_paise']/100:,.2f} writeoffs, resulting in -₹{loss/100:,.2f} net contribution."
            ),
            'impact_paise': loss,
            'reference': p['product_id'],
        })

    # 2. Cash register shift variances
    variance_shifts = [s for s in report['shifts'] if s.get('closed') and int(s.get('variance_paise') or 0) != 0]
    for s in variance_shifts[:5]:
        var_abs = abs(int(s['variance_paise']))
        margin_at_risk_paise += var_abs
        anomalies.append({
            'id': f"shift-var-{s['id']}",
            'severity': 'high' if var_abs >= 25000 else 'medium',
            'category': 'Cash Drawer Variance',
            'title': f"Register {s['register_id']} cash discrepancy ({s['business_date']})",
            'detail': (
                f"Cashier {s['cashier']} closed shift with expected ₹{s['expected_paise']/100:,.2f} "
                f"vs counted ₹{s['counted_paise']/100:,.2f} (variance ₹{s['variance_paise']/100:,.2f})."
            ),
            'impact_paise': var_abs,
            'reference': s['register_id'],
        })

    # 3. Return-to-sales abuse & discarded returns
    return_rate = float(kpis.get('return_value_rate') or 0.0)
    discard_cost = int(kpis.get('discarded_returns_cost_paise') or 0)
    if return_rate > 0.08 or discard_cost > 0:
        impact = discard_cost + int(kpis.get('refunds_ex_gst_paise') or 0) // 5
        margin_at_risk_paise += impact
        anomalies.append({
            'id': 'return-velocity-alert',
            'severity': 'high' if return_rate > 0.15 else 'medium',
            'category': 'Return & Credit Note Velocity',
            'title': f"Return value rate at {return_rate*100:.1f}% across {kpis['return_documents']} credit notes",
            'detail': (
                f"Recorded ₹{kpis['refunds_ex_gst_paise']/100:,.2f} in refunds (ex-GST) and "
                f"₹{discard_cost/100:,.2f} in non-restocked discarded return cost. Verify BXGY/BXGCY bundle return clawbacks."
            ),
            'impact_paise': max(impact, 1000),
            'reference': f"{kpis['return_documents']} credit notes",
        })

    # 4. Stock writeoffs / shrinkage
    writeoffs_paise = int(kpis.get('stock_writeoff_paise') or 0)
    if writeoffs_paise > 0:
        margin_at_risk_paise += writeoffs_paise
        anomalies.append({
            'id': 'inventory-shrinkage',
            'severity': 'medium',
            'category': 'Inventory Writeoff / Shrinkage',
            'title': f"Manual inventory discards & writeoffs of ₹{writeoffs_paise/100:,.2f}",
            'detail': 'Stock adjustments and discards reduced period contribution. Audit high-value SKU movement logs.',
            'impact_paise': writeoffs_paise,
            'reference': 'Inventory ledger',
        })

    # 5. Inspect individual high-discount transactions
    sales_rows = [dict(r) for r in con.run('pos_reports_export_aggregate_pos_sales', (store['id'], start, end))]
    heavy_discount_tx = [
        r for r in sales_rows
        if int(r.get('discount_paise') or 0) > 0
        and (int(r['discount_paise']) / max(1, int(r['total_paise']) + int(r['discount_paise']))) >= 0.30
    ]
    if heavy_discount_tx:
        top_tx = max(heavy_discount_tx, key=lambda r: int(r['discount_paise']))
        disc_sum = sum(int(r['discount_paise']) for r in heavy_discount_tx)
        anomalies.append({
            'id': 'heavy-discount-concentration',
            'severity': 'medium',
            'category': 'Promotion Concentration',
            'title': f"{len(heavy_discount_tx)} invoice(s) with ≥30% effective basket discount",
            'detail': (
                f"Highest discount on invoice {top_tx['number']} (₹{top_tx['discount_paise']/100:,.2f} saved on "
                f"₹{top_tx['total_paise']/100:,.2f} net bill). Confirm stacked campaign rules."
            ),
            'impact_paise': disc_sum,
            'reference': top_tx['number'],
        })

    # Compute overall risk score (0 - 100)
    base_score = min(95, len(anomalies) * 16 + (20 if neg_products else 0) + (20 if variance_shifts else 0))
    if not anomalies and int(kpis.get('transactions') or 0) > 0:
        base_score = 8
    elif int(kpis.get('transactions') or 0) == 0:
        base_score = 0

    risk_level = (
        'Critical' if base_score >= 70
        else 'Elevated' if base_score >= 40
        else 'Moderate' if base_score >= 18
        else 'Low'
    )

    fallback_audit = _AIFraudSchema(
        executive_summary=(
            f"Forensic scan from {start} to {end} across {kpis['transactions']} transactions "
            f"(₹{kpis['net_sales_paise']/100:,.2f} net sales) identified {len(anomalies)} risk signal(s) "
            f"with ₹{margin_at_risk_paise/100:,.2f} in monitored margin exposure. "
            + (
                "OneSales deterministic return-clawback rules are active to block BXGY/BXGCY free-item abuse."
                if not anomalies
                else "Review flagged negative-contribution SKUs, register variances, and high-discount invoices immediately."
            )
        ),
        recommended_controls=[
            'Enforce OneSales linked-bundle return quotes so partial returns on BXGY/BXGCY automatically claw back the free reward value.',
            'Switch overlapping SKU campaigns from "stack_all" to "best_offer" on products showing negative net contribution.',
            'Require manager PIN sign-off on register shift closures with cash variance exceeding ₹100 and on non-restocked return discards.',
        ],
    )

    prompt = (
        f"Store: {store['name']} | Period: {start} to {end}\n"
        f"KPIs: Transactions={kpis['transactions']}, NetSales=₹{kpis['net_sales_paise']/100:,.2f}, "
        f"Contribution=₹{kpis['contribution_paise']/100:,.2f}, Discounts=₹{kpis['discount_paise']/100:,.2f}, "
        f"Refunds=₹{kpis['refunds_ex_gst_paise']/100:,.2f}, DiscardedReturns=₹{discard_cost/100:,.2f}, "
        f"Writeoffs=₹{writeoffs_paise/100:,.2f}\n"
        f"Detected Anomalies ({len(anomalies)}):\n{json.dumps(anomalies, ensure_ascii=False)}\n\n"
        "Provide an executive forensic audit summary and 3-4 concrete operational controls for the retail manager."
    )

    ai_audit, used_gemini = gemini.generate_structured(
        prompt=prompt,
        schema=_AIFraudSchema,
        system_instruction=(
            "You are AisleMint Loss-Prevention & Margin Auditor. Analyze deterministic POS ledger signals "
            "and write a sharp, grounded audit brief citing exact rupee amounts and invoice/product IDs."
        ),
        model=gemini.FAST_MODEL,
        fallback=fallback_audit,
        workspace=f"pos:{store['id']}",
    )

    return {
        'ai_powered': bool(used_gemini),
        'model': used_gemini or 'deterministic-fallback',
        'start': start,
        'end': end,
        'risk_score': base_score,
        'risk_level': risk_level,
        'margin_at_risk_paise': margin_at_risk_paise,
        'executive_summary': ai_audit.executive_summary,
        'recommended_controls': ai_audit.recommended_controls,
        'anomalies': anomalies,
    }
