'use client';
import { useEffect, useRef, useState } from 'react';
import {
  Search,
  ScanBarcode,
  ShoppingBasket,
  Plus,
  Minus,
  Trash2,
  Pause,
  Tag,
  CheckCircle2,
  Wallet,
  ArrowRight,
  Gift,
  Sparkles,
  Camera,
} from 'lucide-react';
import {
  usePos,
  inr,
  toPaise,
  Field,
  StateSelect,
  Action,
  Panel,
  Empty,
  Modal,
  PrintButton,
  saveRetry,
  clearRetry,
  definitiveFailure,
} from './shared';
export default function Register({
  onAddCustomer,
}: {
  onAddCustomer: () => void;
}) {
  const p = usePos(),
    { ctx, products, customers } = p;
  const [q, setQ] = useState(''),
    [category, setCategory] = useState('All'),
    [cart, setCart] = useState<any>({}),
    [customer, setCustomer] = useState(''),
    [place, setPlace] = useState(ctx.store.state_code),
    [rewards, setRewards] = useState<any>({}),
    [quote, setQuote] = useState<any>(null),
    [quoteError, setQuoteError] = useState(''),
    [refreshQuote, setRefreshQuote] = useState(0),
    [dialog, setDialog] = useState(''),
    [sale, setSale] = useState<any>(null),
    [parked, setParked] = useState<any[]>([]),
    [shiftId, setShiftId] = useState(''),
    [nudges, setNudges] = useState<any[]>([]);
  const [searchCustomers, setSearchCustomers] = useState<any[]>(customers),
    [customerQ, setCustomerQ] = useState('');
  const signature = JSON.stringify({
    lines: Object.entries(cart).map(([product_id, quantity]) => ({
      product_id,
      quantity,
    })),
    customer_id: customer || null,
    place_of_supply: place,
    selected_rewards: rewards,
  });
  const current = useRef(signature);
  const scanner = useRef<HTMLInputElement>(null);
  current.current = signature;
  const epoch = useRef(0);
  const shifts = ctx.shifts.filter(
      (s: any) => s.staff_id === ctx.staff.id || ctx.staff.role === 'manager',
    ),
    shift = shifts.find((s: any) => s.id === shiftId) || shifts[0];
  useEffect(() => {
    let alive = true;
    const i = ++epoch.current;
    setQuote(null);
    setQuoteError('');
    if (!Object.keys(cart).length) return;
    const timer = setTimeout(
      () =>
        p
          .request('/quote', JSON.parse(signature))
          .then((r) => {
            if (alive && i === epoch.current && current.current === signature)
              setQuote({ ...r, signature });
          })
          .catch((e) => {
            if (alive) setQuoteError(e.message);
          }),
      180,
    );
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [signature, refreshQuote, p.request, p.products, ctx.calendar_revision]);
  useEffect(() => {
    let alive = true;
    const timer = setTimeout(() => {
      p.request('/shopper/nudges', { cart })
        .then((r) => {
          if (alive) setNudges(r?.nudges || []);
        })
        .catch(() => {
          if (alive) setNudges([]);
        });
    }, 220);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [cart, p.request, ctx.calendar_revision]);
  useEffect(() => {
    let alive = true;
    const t = setTimeout(
      () =>
        p
          .request('/customers?q=' + encodeURIComponent(customerQ))
          .then((r) => {
            if (alive) setSearchCustomers(r);
          })
          .catch(() => {}),
      200,
    );
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [customerQ, p.request, p.customers]);
  const add = (id: string, qty = 1) => {
    const next = Math.min(500, Math.max(0, (cart[id] || 0) + qty));
    setCart((c: any) => {
      const n = { ...c };
      if (next) n[id] = next;
      else delete n[id];
      return n;
    });
    requestAnimationFrame(() => scanner.current?.focus());
  };
  const reset = () => {
    setCart({});
    setCustomer('');
    setRewards({});
    setSale(null);
    setQuote(null);
    setDialog('');
    requestAnimationFrame(() => scanner.current?.focus());
  };
  const loadParked = () =>
    p.act(async () => {
      setParked(await p.request('/carts'));
      setDialog('parked');
    });
  const activeIds = new Set(
    ctx.active_offers.map((e: any) => e.terms.product_id),
  );
  const visible = products.filter(
    (x) =>
      x.active &&
      (category === 'All' || x.category === category) &&
      [x.name, x.barcode, x.product_id].some((v) =>
        v.toLowerCase().includes(q.toLowerCase()),
      ),
  );
  return (
    <>
      <div className="os-register-tools">
        <span className={'os-pill ' + (shift ? 'os-good' : 'os-amber')}>
          {shift ? 'Register open' : 'Register closed'}
        </span>
        {shifts.length > 1 && (
          <select
            aria-label="Active register"
            value={shift?.id || ''}
            onChange={(e) => setShiftId(e.target.value)}
          >
            {shifts.map((s: any) => (
              <option key={s.id} value={s.id}>
                {s.register_id}
              </option>
            ))}
          </select>
        )}
        <span className="os-help">
          {ctx.active_offers.length} active offer rules · checked again at
          payment
        </span>
        <div className="os-spacer" />
        <Action onClick={() => setDialog('ai-shopper')}>
          <Sparkles size={16} />
          AI Shopper & Deal Concierge
        </Action>
        <Action secondary onClick={loadParked}>
          <Pause size={16} />
          Held baskets
        </Action>
        <Action secondary onClick={() => setDialog('shift')}>
          <Wallet size={16} />
          {shift ? 'Manage register' : 'Open register'}
        </Action>
      </div>
      <div className="os-register">
        <section className="os-catalog">
          <form
            className="os-search"
            onSubmit={(e) => {
              e.preventDefault();
              const item = products.find(
                (x) => x.active && (x.barcode === q || x.product_id === q),
              );
              if (item) {
                add(item.product_id);
                setQ('');
              }
            }}
          >
            <Search size={19} />
            <input
              ref={scanner}
              autoFocus
              aria-label="Scan barcode or search products"
              placeholder="Scan barcode or search products…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <ScanBarcode size={22} />
          </form>
          <div className="os-categories">
            {[
              'All',
              ...new Set(
                products.filter((p) => p.active).map((p) => p.category),
              ),
            ].map((c) => (
              <button
                key={c}
                className={c === category ? 'selected' : ''}
                onClick={() => setCategory(c)}
              >
                {c}
              </button>
            ))}
          </div>
          <div className="os-catalog-heading">
            <h3>{category === 'All' ? 'Your shelves' : category}</h3>
            <span>{visible.length} products</span>
          </div>
          <div className="os-product-grid">
            {visible.map((x) => (
              <button
                key={x.product_id}
                className="os-product"
                disabled={!x.on_hand || cart[x.product_id] >= x.on_hand}
                onClick={() => add(x.product_id)}
              >
                <div className="os-product-top">
                  <span className="os-product-symbol">
                    {x.category.slice(0, 2).toUpperCase()}
                  </span>
                  {activeIds.has(x.product_id) && (
                    <span className="os-offer">
                      <Tag size={13} />
                      Offer
                    </span>
                  )}
                </div>
                <span className="os-product-category">{x.category}</span>
                <strong>{x.name}</strong>
                <small>{x.barcode}</small>
                <div className="os-product-bottom">
                  <b>{inr(x.price_paise)}</b>
                  <span className="os-product-add">
                    <Plus size={18} />
                  </span>
                </div>
                <span
                  className={
                    x.on_hand <= x.reorder_level ? 'os-lowstock' : 'os-stock'
                  }
                >
                  {x.on_hand} on shelf{' '}
                  {cart[x.product_id]
                    ? `· ${cart[x.product_id]} in basket`
                    : ''}
                </span>
              </button>
            ))}
          </div>
          {!visible.length && (
            <Empty
              title={
                products.length
                  ? 'No products match'
                  : 'Your shelves are ready to fill'
              }
            >
              {products.length
                ? 'Try a name, SKU or barcode.'
                : 'Open Catalog & stock to add products and receive opening stock.'}
            </Empty>
          )}
        </section>
        <aside className="os-basket">
          <div className="os-basket-heading">
            <div>
              <span className="os-eyebrow">CURRENT SALE</span>
              <h2>Shopping basket</h2>
            </div>
            <ShoppingBasket size={25} />
          </div>
          <div className="os-basket-customer">
            <Field label="Customer">
              <input
                placeholder="Search name or phone…"
                aria-label="Search customer"
                value={customerQ}
                onChange={(e) => {
                  setCustomerQ(e.target.value);
                  setCustomer('');
                }}
              />
              <select
                value={customer}
                onChange={(e) => setCustomer(e.target.value)}
              >
                <option value="">Walk-in customer</option>
                {searchCustomers.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} {c.phone}
                  </option>
                ))}
              </select>
            </Field>
            <button className="os-link" onClick={onAddCustomer}>
              <Plus size={14} />
              Add or edit customer
            </button>
            <Field label="Declared place of supply">
              <StateSelect value={place} onChange={setPlace} />
            </Field>
          </div>
          <div className="os-basket-lines">
            {!Object.keys(cart).length ? (
              <Empty title="Start with a product">
                Scan a barcode or tap a product to add it.
              </Empty>
            ) : (
              Object.entries(cart).map(([id, qty]: any) => {
                const x = products.find((x) => x.product_id === id),
                  line = quote?.lines.find((l: any) => l.product_id === id);
                return (
                  <div key={id} className="os-basket-line">
                    <div className="os-basket-line-title">
                      <strong>{x?.name || id}</strong>
                      <button
                        className="os-icon"
                        aria-label={'Remove ' + x?.name}
                        onClick={() =>
                          setCart((c: any) => {
                            const n = { ...c };
                            delete n[id];
                            return n;
                          })
                        }
                      >
                        <Trash2 size={15} />
                      </button>
                    </div>
                    <div className="os-basket-line-controls">
                      <div className="os-quantity">
                        <button
                          aria-label={'Decrease ' + x?.name}
                          onClick={() => add(id, -1)}
                        >
                          <Minus size={14} />
                        </button>
                        <input
                          aria-label={'Quantity for ' + x?.name}
                          type="number"
                          min="1"
                          max={Math.min(500, x?.on_hand || 500)}
                          value={qty}
                          onChange={(e) =>
                            setCart((c: any) => ({
                              ...c,
                              [id]: Math.max(
                                1,
                                Math.min(500, Number(e.target.value) || 1),
                              ),
                            }))
                          }
                        />
                        <button
                          aria-label={'Increase ' + x?.name}
                          onClick={() => add(id)}
                        >
                          <Plus size={14} />
                        </button>
                      </div>
                      <div>
                        {line?.discount_paise > 0 && (
                          <del>{inr(x?.price_paise * qty)}</del>
                        )}
                        <b>{inr(line?.paid_paise ?? x?.price_paise * qty)}</b>
                      </div>
                    </div>
                    {line?.free_units > 0 && (
                      <small className="os-green">
                        {line.free_units} free reward unit(s) included
                      </small>
                    )}
                  </div>
                );
              })
            )}
          </div>
          {quote?.reward_opportunities.length > 0 && (
            <div className="os-rewards">
              {quote.reward_opportunities.map((o: any, i: number) => (
                <Field key={i} label={'Choose reward · buy ' + o.buy_quantity}>
                  <select
                    value={rewards[o.product_id + ':' + o.group] || ''}
                    onChange={(e) => {
                      const id = e.target.value;
                      if (!id) return;
                      setRewards((r: any) => ({
                        ...r,
                        [o.product_id + ':' + o.group]: id,
                      }));
                      if (!cart[id]) add(id);
                    }}
                  >
                    <option value="">Select a reward to add to basket</option>
                    {o.reward_choices.map((id: string) => (
                      <option
                        key={id}
                        value={id}
                        disabled={
                          !products.find((x) => x.product_id === id)?.on_hand
                        }
                      >
                        {products.find((x) => x.product_id === id)?.name || id}
                      </option>
                    ))}
                  </select>
                </Field>
              ))}
            </div>
          )}
          {nudges.length > 0 && (
            <div
              className="os-rewards"
              style={{
                background: '#f2f8f5',
                border: '1px solid #cce3d8',
                borderRadius: 8,
                padding: '10px 12px',
                marginBottom: 8,
              }}
            >
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  fontSize: 12,
                  fontWeight: 700,
                  color: '#19694f',
                  textTransform: 'uppercase',
                  letterSpacing: '0.04em',
                  marginBottom: 6,
                }}
              >
                <Sparkles size={14} /> Smart Deal Nudges
              </div>
              {nudges.slice(0, 2).map((n: any) => (
                <div
                  key={n.id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 8,
                    padding: '6px 0',
                    borderTop: '1px dashed #d5e6dd',
                  }}
                >
                  <div style={{ fontSize: 12.5, lineHeight: 1.35 }}>
                    <strong style={{ display: 'block', color: '#173e32' }}>
                      {n.title}
                    </strong>
                    <small style={{ color: '#567064' }}>{n.description}</small>
                  </div>
                  <button
                    type="button"
                    className="os-button"
                    style={{
                      minHeight: 30,
                      padding: '4px 10px',
                      fontSize: 12,
                    }}
                    onClick={() => {
                      if (n.action_product_id && n.action_add_qty) {
                        add(n.action_product_id, n.action_add_qty);
                      }
                      if (n.reward_group && n.reward_product_id) {
                        setRewards((r: any) => ({
                          ...r,
                          [n.reward_group]: n.reward_product_id,
                        }));
                      }
                    }}
                  >
                    <Gift size={13} /> Claim
                  </button>
                </div>
              ))}
            </div>
          )}
          {quoteError && (
            <p className="os-inline-error" role="alert">
              {quoteError}
            </p>
          )}
          <div className="os-basket-totals">
            {quote?.applied_promotions.length > 0 && (
              <div className="os-applied">
                <Tag size={16} />
                {quote.applied_promotions.length} offer benefits applied
                automatically
              </div>
            )}
            <div>
              <span>Before GST</span>
              <b>{inr(quote?.subtotal_paise)}</b>
            </div>
            <div>
              <span>{quote?.interstate ? 'IGST' : 'CGST + SGST'}</span>
              <b>{inr(quote?.tax_paise)}</b>
            </div>
            <div className="os-green">
              <span>You save</span>
              <b>{inr(quote?.discount_paise)}</b>
            </div>
            <div className="os-grand">
              <span>Total</span>
              <b>{inr(quote?.total_paise)}</b>
            </div>
            <Action
              disabled={
                !quote ||
                quote.signature !== signature ||
                !shift ||
                shift.business_date !== ctx.business_date ||
                ctx.day_closed
              }
              onClick={() => setDialog('payment')}
            >
              Take payment <ArrowRight size={19} />
            </Action>
            <Action
              secondary
              disabled={!Object.keys(cart).length}
              onClick={() => setDialog('park')}
            >
              <Pause size={15} />
              Hold this basket
            </Action>
          </div>
        </aside>
      </div>
      {dialog === 'ai-shopper' && (
        <AIShopperModal
          currentCart={cart}
          onClose={() => setDialog('')}
          onLoadBasket={(nextCart, nextRewards) => {
            setCart(nextCart);
            setRewards(nextRewards || {});
            setDialog('');
            p.notify(
              'AI-curated promotional basket loaded into live register.',
            );
          }}
          onHoldBasket={(name, nextCart, nextRewards) =>
            p.act(async () => {
              await p.request('/carts', {
                name,
                cart: {
                  lines: Object.entries(nextCart).map(
                    ([product_id, quantity]) => ({ product_id, quantity }),
                  ),
                  customer_id: customer || null,
                  place_of_supply: place,
                  selected_rewards: nextRewards || {},
                },
              });
              setDialog('');
              p.notify('AI Shopper basket saved to Held Baskets for pickup.');
            })
          }
        />
      )}
      {dialog === 'payment' && quote && (
        <Payment
          quote={quote}
          cart={JSON.parse(signature)}
          shift={shift}
          onClose={() => {
            setDialog('');
            setRefreshQuote((n) => n + 1);
          }}
          onSuccess={async (s) => {
            setSale(s);
            setCart({});
            setRewards({});
            setCustomer('');
            setDialog('sale');
            await p.refresh();
          }}
        />
      )}
      {dialog === 'sale' && sale && (
        <Modal title="Sale complete" onClose={reset}>
          <div className="os-sale-success">
            <CheckCircle2 size={50} />
            <h2>{inr(sale.total_paise)}</h2>
            <p>{sale.number}</p>
            <p>
              Change to give: <strong>{inr(sale.change_paise)}</strong>
            </p>
            <PrintButton id={sale.id} />
            <Action onClick={reset}>
              Next customer <ArrowRight size={17} />
            </Action>
          </div>
        </Modal>
      )}
      {dialog === 'shift' && (
        <ShiftDialog shift={shift} onClose={() => setDialog('')} />
      )}
      {dialog === 'park' && (
        <Modal title="Hold this basket" onClose={() => setDialog('')}>
          <form
            className="os-form"
            onSubmit={(e) => {
              e.preventDefault();
              const name = new FormData(e.currentTarget).get('name');
              p.act(async () => {
                await p.request('/carts', {
                  name,
                  cart: JSON.parse(signature),
                });
                reset();
                p.notify(
                  'Basket held. Stock is reserved only when checkout completes.',
                );
              });
            }}
          >
            <Field label="Customer name or basket note">
              <input name="name" required maxLength={80} />
            </Field>
            <Action type="submit">Hold basket</Action>
          </form>
        </Modal>
      )}
      {dialog === 'parked' && (
        <Modal title="Held baskets" onClose={() => setDialog('')}>
          {Object.keys(cart).length > 0 && (
            <p className="os-help">
              Hold or complete the current basket before resuming another.
            </p>
          )}
          {parked.length ? (
            parked.map((c) => (
              <div className="os-held" key={c.id}>
                <div>
                  <strong>{c.name}</strong>
                  <small>
                    {c.body.lines.length} products ·{' '}
                    {new Date(c.created).toLocaleString()}
                  </small>
                </div>
                <Action
                  secondary
                  disabled={Object.keys(cart).length > 0}
                  onClick={() =>
                    p.act(async () => {
                      setCart(
                        Object.fromEntries(
                          c.body.lines.map((l: any) => [
                            l.product_id,
                            l.quantity,
                          ]),
                        ),
                      );
                      setCustomer(c.body.customer_id || '');
                      setPlace(c.body.place_of_supply || ctx.store.state_code);
                      setRewards(c.body.selected_rewards || {});
                      await p.request('/carts/' + c.id + '/remove', {});
                      setDialog('');
                    })
                  }
                >
                  Resume
                </Action>
              </div>
            ))
          ) : (
            <Empty title="No held baskets" />
          )}
        </Modal>
      )}
    </>
  );
}
function Payment({
  quote,
  cart,
  shift,
  onClose,
  onSuccess,
}: {
  quote: any;
  cart: any;
  shift: any;
  onClose: () => void;
  onSuccess: (s: any) => Promise<void>;
}) {
  const p = usePos();
  const [method, setMethod] = useState('cash'),
    [cash, setCash] = useState(String(quote.total_paise / 100)),
    [external, setExternal] = useState(String(quote.total_paise / 100)),
    [reference, setReference] = useState(''),
    [error, setError] = useState(''),
    [saving, setSaving] = useState(false);
  const pending = useRef<any>(null),
    locked = useRef(false);
  const externalAmount =
      method === 'cash'
        ? 0
        : method === 'split'
          ? toPaise(external)
          : quote.total_paise,
    cashAmount = method === 'cash' || method === 'split' ? toPaise(cash) : 0;
  return (
    <Modal
      title="Take payment"
      onClose={() => {
        if (!saving && !pending.current) onClose();
      }}
    >
      <div className="os-payment-total">
        <span>Amount due</span>
        <strong>{inr(quote.total_paise)}</strong>
      </div>
      <form
        className="os-form"
        onSubmit={async (e) => {
          e.preventDefault();
          if (locked.current) return;
          locked.current = true;
          setSaving(true);
          setError('');
          try {
            if (!pending.current) {
              const tenders = [];
              if (cashAmount > 0)
                tenders.push({ method: 'cash', amount_paise: cashAmount });
              if (externalAmount > 0)
                tenders.push({
                  method: method === 'split' ? 'external_card' : method,
                  amount_paise: externalAmount,
                  reference,
                });
              pending.current = {
                ...cart,
                shift_id: shift.id,
                quote_token: quote.quote_token,
                idempotency_key: crypto.randomUUID(),
                tenders,
              };
            }
            await saveRetry(p.session, '/checkout', pending.current);
            const s = await p.request('/checkout', pending.current);
            await clearRetry(p.session);
            pending.current = null;
            await onSuccess(s);
          } catch (e: any) {
            setError(
              e.message +
                (e.status
                  ? ''
                  : ' Retry this same payment recording to safely recover the result.'),
            );
            if (definitiveFailure(e)) {
              pending.current = null;
              if (!e.preserveRetry) await clearRetry(p.session);
            }
          } finally {
            locked.current = false;
            setSaving(false);
          }
        }}
      >
        <fieldset
          disabled={saving || !!pending.current}
          className="os-fieldset"
        >
          <div className="os-payment-methods">
            {[
              ['cash', 'Cash'],
              ['external_card', 'External card'],
              ['external_upi', 'External UPI'],
              ['split', 'Cash + card'],
            ].map(([v, label]) => (
              <button
                key={v}
                type="button"
                className={method === v ? 'selected' : ''}
                onClick={() => setMethod(v)}
              >
                {label}
              </button>
            ))}
          </div>
          {(method === 'cash' || method === 'split') && (
            <Field label="Cash tendered (₹)">
              <input
                type="number"
                step="0.01"
                min="0"
                value={cash}
                onChange={(e) => setCash(e.target.value)}
                required
              />
            </Field>
          )}
          {method === 'split' && (
            <Field label="Card amount already paid (₹)">
              <input
                type="number"
                min="0"
                max={quote.total_paise / 100}
                step="0.01"
                value={external}
                onChange={(e) => setExternal(e.target.value)}
                required
              />
            </Field>
          )}
          {method !== 'cash' && (
            <>
              <Field label="Successful terminal / UPI reference">
                <input
                  required
                  value={reference}
                  maxLength={100}
                  onChange={(e) => setReference(e.target.value)}
                  placeholder="Record the payment reference"
                />
              </Field>
              <p className="os-help">
                Complete the payment on your external terminal first. OneSales
                records it; it does not charge a card or initiate UPI.
              </p>
            </>
          )}
          <div className="os-summary-row">
            <span>
              {cashAmount + externalAmount >= quote.total_paise
                ? 'Change to give'
                : 'Amount still due'}
            </span>
            <b>
              {inr(Math.abs(cashAmount + externalAmount - quote.total_paise))}
            </b>
          </div>
        </fieldset>
        {error && (
          <p className="os-inline-error" role="alert">
            {error}
          </p>
        )}
        <Action
          type="submit"
          disabled={
            saving ||
            externalAmount > quote.total_paise ||
            cashAmount + externalAmount < quote.total_paise
          }
        >
          {saving
            ? 'Recording…'
            : pending.current
              ? 'Retry same payment recording'
              : 'Complete sale'}
        </Action>
        {!pending.current && (
          <Action secondary onClick={onClose} disabled={saving}>
            Back to basket
          </Action>
        )}
      </form>
    </Modal>
  );
}
function ShiftDialog({ shift, onClose }: { shift: any; onClose: () => void }) {
  const p = usePos();
  const [task, setTask] = useState('close');
  return (
    <Modal
      title={shift ? 'Manage ' + shift.register_id : 'Open register'}
      onClose={onClose}
    >
      <form
        className="os-form"
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          p.act(async () => {
            if (!shift) {
              await p.request('/shifts', {
                register_id: f.get('register'),
                opening_paise: toPaise(f.get('amount')),
              });
              p.notify('Register opened.');
            } else if (task === 'close') {
              const r = await p.request('/shifts/' + shift.id + '/close', {
                counted_paise: toPaise(f.get('amount')),
              });
              p.notify(
                'Register closed. Cash variance: ' + inr(r.variance_paise),
              );
            } else {
              await p.request('/shifts/' + shift.id + '/cash', {
                amount_paise:
                  toPaise(f.get('amount')) * (task === 'out' ? -1 : 1),
                reason: f.get('reason'),
              });
              p.notify('Cash movement recorded.');
            }
            await p.refresh();
            onClose();
          });
        }}
      >
        {shift ? (
          <>
            <div className="os-summary-row">
              <span>Expected drawer cash</span>
              <b>{inr(shift.expected_paise)}</b>
            </div>
            <Field label="Register action">
              <select value={task} onChange={(e) => setTask(e.target.value)}>
                <option value="close">Count cash and close shift</option>
                {p.ctx.staff.role === 'manager' && (
                  <>
                    <option value="in">Paid in</option>
                    <option value="out">Paid out</option>
                  </>
                )}
              </select>
            </Field>
          </>
        ) : (
          <Field label="Register name">
            <input
              required
              name="register"
              defaultValue="Register 1"
              maxLength={30}
            />
          </Field>
        )}
        <Field
          label={
            shift
              ? task === 'close'
                ? 'Cash counted (₹)'
                : 'Movement amount (₹)'
              : 'Opening cash float (₹)'
          }
        >
          <input
            name="amount"
            type="number"
            required
            min="0"
            step="0.01"
            defaultValue="0"
          />
        </Field>
        {shift && task !== 'close' && (
          <Field label="Reason">
            <input required minLength={3} name="reason" />
          </Field>
        )}
        <Action type="submit">
          {shift
            ? task === 'close'
              ? 'Close and reconcile'
              : 'Record cash movement'
            : 'Open register'}
        </Action>
      </form>
    </Modal>
  );
}

function AIShopperModal({
  currentCart,
  onClose,
  onLoadBasket,
  onHoldBasket,
}: {
  currentCart: Record<string, number>;
  onClose: () => void;
  onLoadBasket: (
    cart: Record<string, number>,
    rewards: Record<string, string>,
  ) => void;
  onHoldBasket: (
    name: string,
    cart: Record<string, number>,
    rewards: Record<string, string>,
  ) => void;
}) {
  const p = usePos();
  const [prompt, setPrompt] = useState(
    'Weekend cricket watch party snacks & beverages for 8 friends, prioritizing active TPR and bundle offers',
  );
  const [budget, setBudget] = useState('1200');
  const [imageBase64, setImageBase64] = useState<string | null>(null);
  const [imageMime, setImageMime] = useState('image/jpeg');
  const [imageName, setImageName] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [err, setErr] = useState('');

  const runCurate = async (customPrompt?: string, customBudget?: string) => {
    setLoading(true);
    setErr('');
    try {
      const bVal = Number(customBudget ?? budget);
      const res = await p.request('/shopper/assist', {
        prompt: customPrompt ?? prompt,
        budget_rupees: bVal > 0 ? bVal : null,
        current_cart: currentCart,
        image_base64: imageBase64,
        image_mime_type: imageMime,
      });
      setResult(res);
    } catch (e: any) {
      setErr(e.message || 'Could not curate basket.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      title="AisleMint AI Shopper & Multimodal Deal Concierge"
      onClose={onClose}
    >
      <div className="os-form" style={{ gap: 12 }}>
        <p className="os-help" style={{ margin: 0 }}>
          Powered by <strong>Google Gemini</strong> + deterministic{' '}
          <strong>OneSales GST & Offer Engine</strong>. Describe a shopping
          mission or upload a handwritten list to assemble an in-stock,
          promotion-optimized basket.
        </p>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {[
            [
              'Cricket Party (₹1,200)',
              'Weekend cricket watch party snacks & drinks for 8 friends with maximum bundle savings',
              '1200',
            ],
            [
              'Family Breakfast & Staples (₹800)',
              'Weekly family breakfast, beverages, and pantry essentials with active discounts',
              '800',
            ],
            [
              'Max Promo Hunter (₹2,000)',
              'Build the highest-saving basket unlocking active TPR, Buy-X-Get-Y, and choice rewards',
              '2000',
            ],
          ].map(([label, text, b]) => (
            <button
              key={label}
              type="button"
              className="os-pill"
              disabled={loading}
              style={{
                cursor: 'pointer',
                border: '1px solid #b8d8c9',
                background: '#f2f8f5',
                color: '#173e32',
              }}
              onClick={() => {
                setPrompt(text);
                setBudget(b);
                void runCurate(text, b);
              }}
            >
              <Sparkles size={12} /> {label}
            </button>
          ))}
          <button
            type="button"
            className="os-pill"
            disabled={loading}
            style={{
              cursor: 'pointer',
              border: '1px solid #9ac8b8',
              background: '#eaf5f0',
              color: '#19694f',
              fontWeight: 600,
            }}
            onClick={() => {
              const listPrompt =
                'Match my handwritten weekend party grocery list (milk, paneer, snacks, cereal, dry fruits, cooking oil, tea) under ₹1,500';
              setPrompt(listPrompt);
              setBudget('1500');
              setImageName('handwritten_grocery_list.jpg');
              void runCurate(listPrompt, '1500');
            }}
          >
            <Camera size={12} /> Scan Sample Handwritten List
          </button>
        </div>

        <Field label="Shopping goal, occasion, or dietary preferences">
          <textarea
            rows={2}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="e.g., Hosting 8 friends for a match under ₹1,200, or match my handwritten grocery list…"
          />
        </Field>

        <div className="os-two">
          <Field label="Target budget (₹ INR, optional)">
            <input
              type="number"
              min="50"
              step="50"
              value={budget}
              onChange={(e) => setBudget(e.target.value)}
              placeholder="1200"
            />
          </Field>
          <Field label="Handwritten list / pantry photo (Gemini Vision)">
            <label
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 8,
                padding: '9px 12px',
                border: '1px dashed #9ac8b8',
                borderRadius: 7,
                background: '#f9fcfa',
                cursor: 'pointer',
                fontSize: 13,
                color: '#19694f',
                fontWeight: 500,
              }}
            >
              <Camera size={16} />
              {imageName ? imageName : 'Upload list photo…'}
              <input
                type="file"
                accept="image/*"
                style={{ display: 'none' }}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  setImageName(file.name);
                  setImageMime(file.type || 'image/jpeg');
                  const reader = new FileReader();
                  reader.onload = () => {
                    const dataUrl = String(reader.result || '');
                    const b64 = dataUrl.includes(',')
                      ? dataUrl.split(',')[1]
                      : dataUrl;
                    setImageBase64(b64);
                  };
                  reader.readAsDataURL(file);
                }}
              />
            </label>
          </Field>
        </div>

        <Action onClick={() => runCurate()} disabled={loading}>
          <Sparkles size={16} />
          {loading
            ? 'Curating & verifying GST/promo rules…'
            : 'Curate Smart Promotional Basket'}
        </Action>

        {err && <p className="os-inline-error">{err}</p>}

        {result && (
          <div
            style={{
              border: '1px solid #cce3d8',
              borderRadius: 9,
              padding: 13,
              background: '#f7fbf9',
            }}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                gap: 8,
                marginBottom: 6,
              }}
            >
              <strong>{result.occasion_tag}</strong>
              <span className="os-pill os-good">
                {result.ai_powered
                  ? `Gemini (${result.model})`
                  : 'Deterministic Promo Engine'}
              </span>
            </div>
            <p style={{ fontSize: 13.5, margin: '4px 0 10px' }}>
              {result.assistant_message}
            </p>

            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: 6,
                maxHeight: 210,
                overflowY: 'auto',
                marginBottom: 10,
              }}
            >
              {result.items.map((item: any) => (
                <div
                  key={item.product_id}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    background: '#fff',
                    padding: '7px 10px',
                    borderRadius: 6,
                    border: '1px solid #e1ece6',
                  }}
                >
                  <div>
                    <strong style={{ fontSize: 13.5 }}>
                      {item.name} × {item.quantity}
                    </strong>
                    <small style={{ display: 'block', color: '#567064' }}>
                      {item.reason}
                    </small>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    {item.discount_paise > 0 && (
                      <del
                        style={{
                          fontSize: 12,
                          color: '#889990',
                          marginRight: 6,
                        }}
                      >
                        {inr(item.regular_paise)}
                      </del>
                    )}
                    <b>{inr(item.paid_paise)}</b>
                  </div>
                </div>
              ))}
            </div>

            {result.quote_summary && (
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  padding: '8px 10px',
                  background: '#e7f4ee',
                  borderRadius: 7,
                  marginBottom: 10,
                  fontSize: 13.5,
                }}
              >
                <span>
                  Promo Savings:{' '}
                  <strong style={{ color: '#19694f' }}>
                    {inr(result.quote_summary.discount_paise)}
                  </strong>{' '}
                  · Incl. GST: {inr(result.quote_summary.tax_paise)}
                </span>
                <strong style={{ fontSize: 16 }}>
                  Total: {inr(result.quote_summary.total_paise)}
                </strong>
              </div>
            )}

            <div className="os-two">
              <Action
                onClick={() =>
                  onLoadBasket(result.cart, result.selected_rewards)
                }
                disabled={!Object.keys(result.cart || {}).length}
              >
                Load into Live Register <ArrowRight size={16} />
              </Action>
              <Action
                secondary
                onClick={() =>
                  onHoldBasket(
                    `AI Shopper · ${result.occasion_tag}`,
                    result.cart,
                    result.selected_rewards,
                  )
                }
                disabled={!Object.keys(result.cart || {}).length}
              >
                <Pause size={15} /> Hold for Customer Pickup
              </Action>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}

