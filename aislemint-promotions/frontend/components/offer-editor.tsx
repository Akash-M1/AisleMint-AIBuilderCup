'use client';
import { useState, useEffect, useRef } from 'react';
import { Plus, Trash2, Layers } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { api, money, percent } from '@/lib/api';

export type Mechanic = {
  kind: 'bxgy' | 'bxgcy' | 'bulk' | 'tiered' | 'spend_threshold';
  buy_quantity?: number;
  reward_quantity?: number;
  reward_discount?: number;
  threshold_quantity?: number;
  discount?: number;
  threshold_spend?: number;
  amount_off?: number;
  tiers?: { quantity: number; discount: number }[];
  reward_choices?: string[];
  choice_weights?: number[];
  stack_group?: string;
};
export type OfferConfig = {
  mechanics: Mechanic[];
  normal_basket_units: number;
  redemption_rate: number | null;
  stacking_policy: 'exclusive' | 'stack' | 'best_offer';
};
export const emptyOfferConfig: OfferConfig = {
  mechanics: [],
  normal_basket_units: 2,
  redemption_rate: null,
  stacking_policy: 'exclusive',
};
type Product = {
  product_id: string;
  name: string;
  base_price?: number;
  price?: number;
};
const kinds = [
  ['bxgy', 'Buy X, get Y (same product)'],
  ['bxgcy', 'Buy X, choose one free product'],
  ['bulk', 'Buy in bulk'],
  ['tiered', 'Buy more, save more'],
  ['spend_threshold', 'Spend threshold'],
];
function defaults(kind: Mechanic['kind']): Mechanic {
  return {
    kind,
    stack_group: 'conditional',
    ...(kind === 'bxgy'
      ? { buy_quantity: 1, reward_quantity: 1, reward_discount: 1 }
      : kind === 'bxgcy'
        ? { buy_quantity: 2, reward_choices: [] }
        : kind === 'bulk'
          ? { threshold_quantity: 3, discount: 0.1 }
          : kind === 'tiered'
            ? {
                tiers: [
                  { quantity: 3, discount: 0.1 },
                  { quantity: 6, discount: 0.2 },
                ],
              }
            : { threshold_spend: 20, amount_off: 5 }),
  };
}
function NumberField({
  label,
  value,
  onChange,
  min = 0,
  max = 100000,
  step = 1,
}: {
  label: string;
  value: number | string;
  onChange: (n: number) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <label className="offer-field">
      <span>{label}</span>
      <Input
        aria-label={label}
        type="number"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}

export default function OfferEditor({
  value,
  onChange,
  products,
  productId,
  discount = 0,
}: {
  value: OfferConfig;
  onChange: (v: OfferConfig) => void;
  products: Product[];
  productId?: string;
  discount?: number;
}) {
  const [quantity, setQuantity] = useState(4),
    [receipt, setReceipt] = useState<any>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  const previewKey = JSON.stringify({
    value,
    productId,
    discount,
    quantity,
    products,
  });
  const latestPreview = useRef(previewKey);
  latestPreview.current = previewKey;
  useEffect(() => {
    setReceipt(null);
    setError('');
  }, [previewKey]);
  const update = (v: OfferConfig) => {
    onChange(v);
    setReceipt(null);
    setError('');
  };
  const change = (i: number, patch: Partial<Mechanic>) =>
    update({
      ...value,
      mechanics: value.mechanics.map((o, j) =>
        i === j ? { ...o, ...patch } : o,
      ),
    });
  async function preview() {
    if (!productId) return;
    const key = previewKey;
    setBusy(true);
    setError('');
    setReceipt(null);
    try {
      const cart: Record<string, number> = { [productId]: quantity },
        prices: Record<string, number> = {};
      const selected: Record<string, string> = {};
      for (const o of value.mechanics)
        if (o.kind === 'bxgcy' && o.reward_choices?.length) {
          const id = o.reward_choices[0];
          cart[id] =
            (cart[id] || 0) + Math.floor(quantity / (o.buy_quantity || 2));
          selected[o.stack_group || 'conditional'] = id;
        }
      for (const p of products)
        if (p.product_id in cart)
          prices[p.product_id] = p.base_price ?? p.price ?? 0;
      const response = await api('/offer-mechanics/checkout', 'benchmark', {
        product_id: productId,
        cart,
        regular_prices: prices,
        discount,
        mechanics: value.mechanics,
        selected_rewards: selected,
      });
      if (latestPreview.current === key) setReceipt(response);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Cannot price this basket.');
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="offer-editor">
      <div className="offer-title">
        <strong>
          <Layers size={16} /> Conditional offers
        </strong>
        <Button
          variant="outline"
          size="sm"
          disabled={value.mechanics.length >= 6}
          onClick={() =>
            update({
              ...value,
              mechanics: [...value.mechanics, defaults('bxgy')],
            })
          }
        >
          <Plus size={14} /> Add
        </Button>
      </div>
      <p className="micro">
        TPR applies first. Add a quantity or spend condition to stack with it.
      </p>
      {value.mechanics.map((o, i) => (
        <section className="offer-rule" key={i}>
          <div className="offer-rule-header">
            <select
              aria-label={'Offer mechanic ' + (i + 1)}
              value={o.kind}
              onChange={(e) =>
                update({
                  ...value,
                  mechanics: value.mechanics.map((m, j) =>
                    i === j
                      ? {
                          ...defaults(e.target.value as Mechanic['kind']),
                          stack_group: o.stack_group,
                        }
                      : m,
                  ),
                })
              }
            >
              {kinds.map(([v, n]) => (
                <option key={v} value={v}>
                  {n}
                </option>
              ))}
            </select>
            <Button
              variant="ghost"
              aria-label={'Remove conditional offer ' + (i + 1)}
              onClick={() =>
                update({
                  ...value,
                  mechanics: value.mechanics.filter((_, j) => j !== i),
                })
              }
            >
              <Trash2 size={15} />
            </Button>
          </div>
          <div className="offer-fields">
            {(o.kind === 'bxgy' || o.kind === 'bxgcy') && (
              <NumberField
                label="Buy units"
                value={o.buy_quantity ?? 1}
                min={1}
                max={100}
                onChange={(n) => change(i, { buy_quantity: n })}
              />
            )}
            {o.kind === 'bxgy' && (
              <>
                <NumberField
                  label="Reward units"
                  value={o.reward_quantity ?? 1}
                  min={1}
                  max={100}
                  onChange={(n) => change(i, { reward_quantity: n })}
                />
                <NumberField
                  label="Reward discount (%)"
                  value={(o.reward_discount ?? 1) * 100}
                  min={1}
                  max={100}
                  onChange={(n) => change(i, { reward_discount: n / 100 })}
                />
              </>
            )}
            {o.kind === 'bulk' && (
              <>
                <NumberField
                  label="Minimum units"
                  value={o.threshold_quantity ?? 3}
                  min={2}
                  max={100}
                  onChange={(n) => change(i, { threshold_quantity: n })}
                />
                <NumberField
                  label="Extra discount (%)"
                  value={(o.discount ?? 0.1) * 100}
                  min={1}
                  max={100}
                  onChange={(n) => change(i, { discount: n / 100 })}
                />
              </>
            )}
            {o.kind === 'spend_threshold' && (
              <>
                <NumberField
                  label="Spend after TPR (excl. tax)"
                  value={o.threshold_spend ?? 20}
                  min={0.01}
                  step={0.01}
                  onChange={(n) => change(i, { threshold_spend: n })}
                />
                <NumberField
                  label="Amount off (excl. tax)"
                  value={o.amount_off ?? 5}
                  min={0.01}
                  step={0.01}
                  onChange={(n) => change(i, { amount_off: n })}
                />
              </>
            )}
          </div>
          {o.kind === 'bxgcy' && (
            <>
              <p className="micro">
                One free reward for each qualifying basket. Choose up to six
                eligible products. Planning assumes equal choice shares.
              </p>
              <div className="offer-choices">
                {products
                  .filter((p) => p.product_id !== productId)
                  .map((p) => (
                    <label key={p.product_id}>
                      <input
                        type="checkbox"
                        checked={
                          o.reward_choices?.includes(p.product_id) || false
                        }
                        disabled={
                          !o.reward_choices?.includes(p.product_id) &&
                          (o.reward_choices?.length || 0) >= 6
                        }
                        onChange={(e) =>
                          change(i, {
                            reward_choices: e.target.checked
                              ? [...(o.reward_choices || []), p.product_id]
                              : (o.reward_choices || []).filter(
                                  (id) => id !== p.product_id,
                                ),
                            choice_weights: [],
                          })
                        }
                      />
                      {p.name}
                    </label>
                  ))}
              </div>
            </>
          )}
          {o.kind === 'tiered' && (
            <div>
              {(o.tiers || []).map((tier, j) => (
                <div className="offer-tier" key={j}>
                  <NumberField
                    label={'Tier ' + (j + 1) + ' units'}
                    value={tier.quantity}
                    min={2}
                    max={100}
                    onChange={(n) =>
                      change(i, {
                        tiers: o.tiers!.map((t, k) =>
                          j === k ? { ...t, quantity: n } : t,
                        ),
                      })
                    }
                  />
                  <NumberField
                    label={'Tier ' + (j + 1) + ' saving (%)'}
                    value={tier.discount * 100}
                    min={1}
                    max={100}
                    onChange={(n) =>
                      change(i, {
                        tiers: o.tiers!.map((t, k) =>
                          j === k ? { ...t, discount: n / 100 } : t,
                        ),
                      })
                    }
                  />
                  <Button
                    variant="ghost"
                    aria-label={'Remove tier ' + (j + 1)}
                    onClick={() =>
                      change(i, { tiers: o.tiers!.filter((_, k) => k !== j) })
                    }
                  >
                    <Trash2 size={14} />
                  </Button>
                </div>
              ))}
              <Button
                variant="outline"
                size="sm"
                disabled={(o.tiers?.length || 0) >= 6}
                onClick={() =>
                  change(i, {
                    tiers: [
                      ...(o.tiers || []),
                      {
                        quantity: Math.min(
                          100,
                          (o.tiers?.at(-1)?.quantity || 0) + 3,
                        ),
                        discount: Math.min(
                          1,
                          (o.tiers?.at(-1)?.discount || 0) + 0.05,
                        ),
                      },
                    ],
                  })
                }
              >
                Add tier
              </Button>
            </div>
          )}
          <label className="offer-field">
            <span>Conditional group</span>
            <Input
              aria-label={'Conditional group ' + (i + 1)}
              value={o.stack_group || 'conditional'}
              onChange={(e) => change(i, { stack_group: e.target.value })}
            />
          </label>
        </section>
      ))}
      {value.mechanics.length > 0 && (
        <>
          <p className="micro">
            Offers in the same group compete for the best benefit. Different
            groups stack in alphabetical order on the remaining price.
          </p>
          <div className="offer-fields">
            <NumberField
              label="Usual basket units"
              value={value.normal_basket_units}
              min={1}
              max={100}
              step={0.1}
              onChange={(n) => update({ ...value, normal_basket_units: n })}
            />
            <label className="offer-field">
              <span>Redeemed unit share (%)</span>
              <Input
                aria-label="Redeemed unit share (%)"
                type="number"
                min={0}
                max={100}
                value={
                  value.redemption_rate === null
                    ? ''
                    : value.redemption_rate * 100
                }
                placeholder="Synthetic ML estimate"
                onChange={(e) =>
                  update({
                    ...value,
                    redemption_rate:
                      e.target.value === ''
                        ? null
                        : Number(e.target.value) / 100,
                  })
                }
              />
            </label>
          </div>
          <p className="offer-disclosure">
            Conditional response is exploratory. Redemption uses a model trained
            on labeled synthetic baskets, or your supplied share. Replace it
            with transaction evidence before relying on these offers for
            production decisions.
          </p>
        </>
      )}
      <label className="offer-field">
        <span>When another campaign overlaps</span>
        <select
          aria-label="Promotion overlap policy"
          value={value.stacking_policy}
          onChange={(e) =>
            update({
              ...value,
              stacking_policy: e.target.value as OfferConfig['stacking_policy'],
            })
          }
        >
          <option value="exclusive">Block overlap</option>
          <option value="stack">Stack compatible campaigns</option>
          <option value="best_offer">Use highest expected discount</option>
        </select>
      </label>
      <p className="micro">
        Both campaigns must choose the same policy. Stacked campaigns use the
        highest TPR. Confirmation rechecks the calendar.
      </p>
      {!!value.mechanics.length && productId && (
        <details>
          <summary>Check an example basket</summary>
          <NumberField
            label="Qualifying product units in basket"
            value={quantity}
            min={1}
            max={1000}
            onChange={(n) => {
              setQuantity(n);
              setReceipt(null);
            }}
          />
          <p className="micro">
            Choice examples use the first eligible reward; forecasting allocates
            choices by their planning shares. This is exact checkout pricing for
            this basket, not average customer redemption.
          </p>
          <Button variant="outline" disabled={busy} onClick={preview}>
            Calculate basket price
          </Button>
          {error && <p role="alert">{error}</p>}
          {receipt && (
            <p className="offer-receipt">
              Basket total <strong>{money(receipt.total)}</strong> · saving{' '}
              {money(receipt.saving)} ({percent(receipt.effective_discount)})
            </p>
          )}
        </details>
      )}
    </div>
  );
}

export function OfferEvidence({ result }: { result: any }) {
  const profiles = (result?.offer_profiles || []).filter(
    (p: any) => p.groups?.length,
  );
  if (!profiles.length) return null;
  const average = (key: string) =>
    profiles.reduce((a: number, p: any) => a + p[key], 0) / profiles.length;
  return (
    <section className="panel offer-evidence">
      <div className="section-title">
        <h3>Conditional offer assumptions</h3>
        <span className="pill">
          {profiles.some((p: any) => p.data_basis === 'synthetic')
            ? 'Synthetic redemption model'
            : profiles.some((p: any) => p.data_basis === 'client_observations')
              ? 'Client redemption model'
              : 'Supplied redemption assumptions'}
        </span>
      </div>
      <div className="offer-evidence-grid">
        <span>
          Effective discount signal
          <strong>{percent(average('effective_discount'))}</strong>
        </span>
        <span>
          Qualifying-product markdown
          <strong>{percent(average('paid_discount'))}</strong>
        </span>
        <span>
          Redeemed unit share
          <strong>{percent(average('redemption_rate'))}</strong>
        </span>
        <span>
          Offer intensity<strong>{average('intensity').toFixed(2)}</strong>
        </span>
      </div>
      <p className="micro">
        Simple averages across active product/store periods. Effective discount
        includes the value of choice rewards; their cost and inventory are
        allocated to the reward products. Intensity combines savings, threshold
        and usual basket size.
      </p>
      {result.reward_allocations?.length > 0 && (
        <p className="micro">
          {result.reward_allocations
            .reduce((n: number, r: any) => n + r.reward_units, 0)
            .toFixed(1)}{' '}
          expected free reward units. Paid reward demand is displaced first; any
          excess becomes additional physical demand.
        </p>
      )}
    </section>
  );
}
