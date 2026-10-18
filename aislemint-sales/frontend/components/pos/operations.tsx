'use client';
import { useEffect, useState } from 'react';
import {
  Plus,
  Search,
  Package,
  Link2,
  Users,
  Database,
  RefreshCw,
  Download,
  ChevronRight,
} from 'lucide-react';
import {
  usePos,
  inr,
  toPaise,
  rate,
  Field,
  StateSelect,
  Action,
  Panel,
  Empty,
  Modal,
  Metric,
  DataTable,
  ExportButton,
  Loading,
} from './shared';
export default function Operations({ tab }: { tab: string }) {
  return tab === 'Catalog & stock' ? (
    <Catalog />
  ) : tab === 'Customers' ? (
    <Customers />
  ) : tab === 'Store settings' ? (
    <Settings />
  ) : (
    <Learning />
  );
}
function Catalog() {
  const p = usePos();
  const [q, setQ] = useState(''),
    [editing, setEditing] = useState<any>(null),
    [stock, setStock] = useState<any>(null),
    [ledger, setLedger] = useState<any[] | null>(null),
    [kind, setKind] = useState('receive');
  return (
    <>
      <div className="os-toolbar">
        <div className="os-search">
          <Search size={18} />
          <input
            aria-label="Search catalog"
            value={q}
            placeholder="Search name, SKU or barcode"
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <Action onClick={() => setEditing({})}>
          <Plus size={17} />
          Add product
        </Action>
      </div>
      <p className="os-help">
        Prices include GST; landed unit cost excludes recoverable input GST.
        Catalog changes are saved to MongoDB. Receive stock separately to keep
        an auditable quantity ledger.
      </p>
      <Panel title="Catalog & stock">
        <DataTable
          headers={[
            'Product',
            'Category / HSN',
            'Price / GST',
            'Landed cost',
            'On hand',
            'Actions',
          ]}
        >
          {p.products
            .filter((x) =>
              [x.name, x.barcode, x.product_id].some((v) =>
                v.toLowerCase().includes(q.toLowerCase()),
              ),
            )
            .map((x) => (
              <tr key={x.product_id}>
                <td>
                  <strong>{x.name}</strong>
                  <small>
                    {x.product_id} · {x.barcode} {!x.active ? '· Inactive' : ''}
                  </small>
                </td>
                <td>
                  {x.category}
                  <small>HSN {x.hsn || '—'}</small>
                </td>
                <td>
                  {inr(x.price_paise)}
                  <small>{x.gst_bps / 100}% GST</small>
                </td>
                <td>{inr(x.cost_paise)}</td>
                <td>
                  <span
                    className={
                      'os-pill ' +
                      (x.on_hand <= x.reorder_level ? 'os-amber' : '')
                    }
                  >
                    {x.on_hand} {x.unit}
                  </span>
                  <small>Reorder at {x.reorder_level}</small>
                </td>
                <td>
                  <div className="os-inline-actions">
                    <button onClick={() => setEditing(x)}>Edit</button>
                    <button
                      onClick={() => {
                        setStock(x);
                        setKind('receive');
                      }}
                    >
                      Stock
                    </button>
                    <button
                      onClick={() =>
                        p.act(async () =>
                          setLedger(
                            await p.request(
                              '/stock/movements?product_id=' +
                                encodeURIComponent(x.product_id),
                            ),
                          ),
                        )
                      }
                    >
                      History
                    </button>
                  </div>
                </td>
              </tr>
            ))}
        </DataTable>
        {!p.products.length && (
          <Empty title="Add your first product">
            Add products here or connect your catalog through the integration
            API.
          </Empty>
        )}
      </Panel>
      {editing && (
        <Modal
          title={editing.product_id ? 'Edit product' : 'Add product'}
          onClose={() => setEditing(null)}
        >
          <form
            className="os-form"
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              p.act(async () => {
                await p.request('/products', {
                  product_id: f.get('product_id'),
                  barcode: f.get('barcode'),
                  name: f.get('name'),
                  category: f.get('category'),
                  hsn: f.get('hsn'),
                  unit: f.get('unit'),
                  price_paise: toPaise(f.get('price')),
                  cost_paise: toPaise(f.get('cost')),
                  gst_bps: Math.round(Number(f.get('gst')) * 100),
                  reorder_level: Number(f.get('reorder')),
                  active: f.get('active') === 'on',
                });
                await p.refresh();
                setEditing(null);
                p.notify('Product saved.');
              });
            }}
          >
            <div className="os-two">
              <Field label="Stable SKU / product ID">
                <input
                  name="product_id"
                  required
                  pattern="[A-Za-z0-9_-]+"
                  defaultValue={editing.product_id}
                  readOnly={!!editing.product_id}
                />
              </Field>
              <Field label="Barcode">
                <input name="barcode" required defaultValue={editing.barcode} />
              </Field>
            </div>
            <Field label="Product name">
              <input
                name="name"
                required
                minLength={2}
                defaultValue={editing.name}
              />
            </Field>
            <div className="os-two">
              <Field label="Category">
                <input
                  name="category"
                  required
                  defaultValue={editing.category}
                />
              </Field>
              <Field label="HSN code">
                <input
                  name="hsn"
                  required={!!p.ctx.store.gst_registered}
                  defaultValue={editing.hsn}
                  pattern="[0-9]{4}|[0-9]{6}|[0-9]{8}"
                />
              </Field>
            </div>
            <div className="os-two">
              <Field label="Selling price incl. GST (₹)">
                <input
                  name="price"
                  type="number"
                  step="0.01"
                  min="0.01"
                  required
                  defaultValue={
                    editing.price_paise ? editing.price_paise / 100 : ''
                  }
                />
              </Field>
              <Field label="Landed unit cost (₹)">
                <input
                  name="cost"
                  type="number"
                  step="0.01"
                  min="0"
                  required
                  defaultValue={
                    editing.cost_paise ? editing.cost_paise / 100 : 0
                  }
                />
              </Field>
              <Field label="GST rate (%)">
                <input
                  name="gst"
                  type="number"
                  step="0.01"
                  min="0"
                  max="100"
                  required
                  defaultValue={(editing.gst_bps || 0) / 100}
                />
              </Field>
              <Field label="Reorder level (units)">
                <input
                  name="reorder"
                  type="number"
                  step="1"
                  min="0"
                  required
                  defaultValue={editing.reorder_level ?? 10}
                />
              </Field>
              <Field label="Unit of quantity">
                <input
                  name="unit"
                  required
                  defaultValue={editing.unit || 'NOS'}
                  maxLength={10}
                />
              </Field>
            </div>
            <label className="os-check">
              <input
                type="checkbox"
                name="active"
                defaultChecked={editing.active !== 0}
              />
              Available for sale
            </label>
            <Action type="submit">Save product</Action>
          </form>
        </Modal>
      )}
      {stock && (
        <Modal
          title={'Update stock · ' + stock.name}
          onClose={() => setStock(null)}
        >
          <form
            className="os-form"
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              p.act(async () => {
                await p.request('/stock', {
                  product_id: stock.product_id,
                  kind,
                  quantity:
                    Number(f.get('quantity')) * (kind === 'discard' ? -1 : 1),
                  reason: f.get('reason'),
                  idempotency_key: crypto.randomUUID(),
                });
                await p.refresh();
                setStock(null);
                p.notify('Stock movement recorded.');
              });
            }}
          >
            <p>
              Current balance:{' '}
              <strong>
                {stock.on_hand} {stock.unit}
              </strong>
            </p>
            <Field label="Movement">
              <select value={kind} onChange={(e) => setKind(e.target.value)}>
                <option value="receive">Receive stock</option>
                <option value="discard">Discard damaged / expired stock</option>
                <option value="adjustment">Adjust by signed quantity</option>
              </select>
            </Field>
            <Field
              label={
                kind === 'adjustment'
                  ? 'Quantity change (+ or − units)'
                  : 'Quantity (units)'
              }
            >
              <input
                name="quantity"
                type="number"
                required
                min={kind === 'adjustment' ? -1000000 : 1}
                step="1"
              />
            </Field>
            <Field label="Reason / supplier receipt reference">
              <input name="reason" required minLength={3} maxLength={200} />
            </Field>
            <Action type="submit">Record movement</Action>
          </form>
        </Modal>
      )}
      {ledger && (
        <Modal
          title="Inventory movement history"
          wide
          onClose={() => setLedger(null)}
        >
          <DataTable
            headers={[
              'Date',
              'Movement',
              'Quantity',
              'Balance',
              'Reason / reference',
            ]}
          >
            {ledger.map((l) => (
              <tr key={l.id}>
                <td>{new Date(l.created).toLocaleString()}</td>
                <td>{l.kind}</td>
                <td>
                  {l.quantity > 0 ? '+' : ''}
                  {l.quantity}
                </td>
                <td>{l.balance}</td>
                <td>
                  {l.reason}
                  <small>{l.reference_id}</small>
                </td>
              </tr>
            ))}
          </DataTable>
        </Modal>
      )}
    </>
  );
}
function Customers() {
  const p = usePos();
  const [q, setQ] = useState(''),
    [rows, setRows] = useState<any[]>(p.customers),
    [editing, setEditing] = useState<any>(null),
    [history, setHistory] = useState<any>(null);
  useEffect(() => {
    let live = true;
    const t = setTimeout(
      () =>
        p
          .request('/customers?q=' + encodeURIComponent(q))
          .then((r) => {
            if (live) setRows(r);
          })
          .catch(() => {}),
      200,
    );
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [q, p.customers, p.request]);
  return (
    <>
      <div className="os-toolbar">
        <div className="os-search">
          <Search size={18} />
          <input
            aria-label="Find customer"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Find by name, phone or email"
          />
        </div>
        <Action onClick={() => setEditing({})}>
          <Plus size={17} />
          Add customer
        </Action>
      </div>
      <Panel title="Customer directory">
        <DataTable
          headers={['Customer', 'Contact', 'GST registration', 'Actions']}
        >
          {rows.map((c) => (
            <tr key={c.id}>
              <td>
                <strong>{c.name}</strong>
                <small>{c.address}</small>
              </td>
              <td>
                {c.phone || '—'}
                <small>{c.email}</small>
              </td>
              <td>{c.gstin || 'Consumer'}</td>
              <td>
                <div className="os-inline-actions">
                  <button onClick={() => setEditing(c)}>Edit</button>
                  <button
                    onClick={() =>
                      p.act(async () =>
                        setHistory(await p.request('/customers/' + c.id)),
                      )
                    }
                  >
                    Purchase history
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </DataTable>
        {!rows.length && (
          <Empty title="Build lasting customer relationships">
            Add a customer at your store, then attach them to checkout for a
            searchable purchase history.
          </Empty>
        )}
      </Panel>
      {editing && (
        <Modal
          title={editing.id ? 'Edit customer' : 'Add customer'}
          onClose={() => setEditing(null)}
        >
          <form
            className="os-form"
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              p.act(async () => {
                await p.request('/customers', {
                  id: editing.id || null,
                  name: f.get('name'),
                  phone: f.get('phone'),
                  email: f.get('email'),
                  address: f.get('address'),
                  state_code: f.get('state_code'),
                  gstin: f.get('gstin'),
                });
                await p.refresh();
                setEditing(null);
                p.notify('Customer saved.');
              });
            }}
          >
            <Field label="Full name / business name">
              <input
                name="name"
                required
                minLength={2}
                defaultValue={editing.name}
              />
            </Field>
            <div className="os-two">
              <Field label="Phone">
                <input name="phone" type="tel" defaultValue={editing.phone} />
              </Field>
              <Field label="Email">
                <input name="email" type="email" defaultValue={editing.email} />
              </Field>
            </div>
            <Field label="Address">
              <textarea name="address" defaultValue={editing.address} />
            </Field>
            <Field label="Customer state">
              <StateSelect
                defaultValue={editing.state_code || p.ctx.store.state_code}
              />
            </Field>
            <Field label="Business GSTIN (optional)">
              <input name="gstin" defaultValue={editing.gstin} maxLength={15} />
            </Field>
            <p className="os-help">
              Business invoices and invoices of ₹50,000 or more need complete
              recipient details.
            </p>
            <Action type="submit">Save customer</Action>
          </form>
        </Modal>
      )}
      {history && (
        <Modal
          title={history.customer?.name || 'Customer history'}
          wide
          onClose={() => setHistory(null)}
        >
          <div className="os-metrics">
            <Metric label="Visits" value={history.visits} />
            <Metric
              label="Net amount paid"
              value={inr(history.net_spent_paise)}
            />
          </div>
          <DataTable headers={['Invoice', 'Date', 'Total']}>
            {(history.sales || []).map((s: any) => (
              <tr key={s.id}>
                <td>{s.number}</td>
                <td>{s.business_date}</td>
                <td>{inr(s.total_paise)}</td>
              </tr>
            ))}
          </DataTable>
          <p className="os-help">
            Search an invoice number in Sales & returns to view its products,
            payments, receipt and refund history.
          </p>
        </Modal>
      )}
    </>
  );
}
function Settings() {
  const p = usePos();
  const [staffDialog, setStaffDialog] = useState(false),
    [audit, setAudit] = useState<any[] | null>(null),
    [sources, setSources] = useState<any[]>([]);
  useEffect(() => {
    fetch('/api/datasets')
      .then((r) => r.json() as Promise<any[]>)
      .then(setSources)
      .catch(() => {});
  }, []);
  return (
    <div className="os-two os-top">
      <Panel title="Promotion connection" aside={<Link2 size={19} />}>
        <p>
          Confirmed offers from the linked planning store apply automatically to
          matching product IDs and dates at checkout. A forecast preview alone
          does not create an offer.
        </p>
        <form
          className="os-form"
          onSubmit={(e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            p.act(async () => {
              await p.request('/planning-connection', {
                workspace: f.get('workspace') || null,
                store_id: f.get('store_id') || null,
                demo_reference_date: p.ctx.store.demo
                  ? f.get('date') || null
                  : null,
              });
              await p.refresh();
              p.notify(
                'Promotion connection saved. Checkout will recheck the current calendar.',
              );
            });
          }}
        >
          <Field label="Planning workspace">
            <select
              name="workspace"
              defaultValue={p.ctx.store.forecast_workspace || ''}
            >
              <option value="">Not connected</option>
              {p.ctx.store.demo && (
                <option value="benchmark">US benchmark · demo only</option>
              )}
              {sources
                .filter((s) => {
                  try {
                    return JSON.parse(s.quality).currency === 'INR';
                  } catch {
                    return false;
                  }
                })
                .map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} · {s.status}
                  </option>
                ))}
            </select>
          </Field>
          <Field label="Store ID in planning data">
            <input
              name="store_id"
              defaultValue={p.ctx.store.forecast_store_id || ''}
            />
          </Field>
          {p.ctx.store.demo && (
            <Field label="Historical demo promotion date">
              <input
                name="date"
                type="date"
                defaultValue={p.ctx.store.promo_reference_date || '2012-01-11'}
              />
            </Field>
          )}
          <p className="os-help">
            Live stores require an INR planning workspace. Import client history
            with country=IN and currency=INR, or train a OneSales promotion
            snapshot when sufficient history exists.
          </p>
          <Action type="submit">Save connection</Action>
        </form>
      </Panel>
      <div className="os-stack">
        <Panel title="Store & access">
          <p>
            <strong>{p.ctx.store.name}</strong>
            <br />
            {p.ctx.store.address}
          </p>
          <p>
            GSTIN: {p.ctx.store.gstin || 'Unregistered'}
            <br />
            Currency: INR · Timezone: Asia/Kolkata
          </p>
          <Action onClick={() => setStaffDialog(true)}>
            <Users size={17} />
            Add staff member
          </Action>
          <Action
            secondary
            onClick={() =>
              p.act(async () => setAudit(await p.request('/audit')))
            }
          >
            View store audit trail
          </Action>
          <p className="os-help">
            Manager access controls catalog changes, returns, reporting and
            training. Cashiers operate sales and their registers. Sessions
            expire after eight hours.
          </p>
        </Panel>
        <Panel title="Operating scope">
          <p>
            Whole-unit grocery checkout, tax-inclusive prices, regular GST, cash
            and recorded external payments.
          </p>
          <p className="os-help">
            This local release does not submit e-invoices or GST returns,
            process payments, handle cess/reverse-charge/composition schemes, or
            synchronize offline terminals. Configure your actual product tax
            rates before using a live store.
          </p>
        </Panel>
      </div>
      {staffDialog && (
        <Modal title="Add staff member" onClose={() => setStaffDialog(false)}>
          <form
            className="os-form"
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              p.act(async () => {
                await p.request('/staff', {
                  name: f.get('name'),
                  role: f.get('role'),
                  pin: f.get('pin'),
                });
                setStaffDialog(false);
                p.notify(
                  'Staff member created. They can sign in from the store selector.',
                );
              });
            }}
          >
            <Field label="Name">
              <input name="name" required minLength={2} />
            </Field>
            <Field label="Role">
              <select name="role">
                <option value="cashier">Cashier</option>
                <option value="manager">Manager</option>
              </select>
            </Field>
            <Field label="PIN">
              <input
                name="pin"
                type="password"
                required
                pattern="[0-9]{6,12}"
                autoComplete="new-password"
                placeholder="6–12 digits"
              />
            </Field>
            <Action type="submit">Create staff access</Action>
          </form>
        </Modal>
      )}
      {audit && (
        <Modal title="Store audit trail" wide onClose={() => setAudit(null)}>
          <DataTable headers={['Sequence / time', 'Event', 'Details']}>
            {audit.map((a) => (
              <tr key={a.sequence}>
                <td>
                  #{a.sequence}
                  <small>{new Date(a.created).toLocaleString()}</small>
                </td>
                <td>{a.kind}</td>
                <td>
                  <code className="os-wrap">{a.payload}</code>
                </td>
              </tr>
            ))}
          </DataTable>
        </Modal>
      )}
    </div>
  );
}
function Learning() {
  const p = usePos();
  const [data, setData] = useState<any>(null),
    [close, setClose] = useState(false),
    [holiday, setHoliday] = useState(false);
  const load = async () => setData(await p.request('/learning'));
  useEffect(() => {
    load().catch((e) => p.notify(e.message));
  }, [p.request]);
  useEffect(() => {
    if (!data?.jobs.some((j: any) => ['queued', 'training'].includes(j.status)))
      return;
    const t = setInterval(() => load().catch(() => {}), 10000);
    return () => clearInterval(t);
  }, [data]);
  if (!data) return <Loading />;
  return (
    <div className="os-stack">
      <div className="os-toolbar">
        <div>
          <h2>Every trading day improves the evidence</h2>
          <p className="os-help">
            Immutable daily observations → drift checks → validated model
            candidates
          </p>
        </div>
        <div className="os-spacer" />
        <Action secondary onClick={() => p.act(load)}>
          <RefreshCw size={16} />
          Refresh
        </Action>
        <Action onClick={() => setClose(true)}>Close trading day</Action>
      </div>
      <div className="os-two">
        {Object.entries(data.readiness).map(([kind, r]: any) => (
          <Panel
            key={kind}
            title={
              kind === 'demand' ? 'Demand model data' : 'Promotion model data'
            }
            aside={<Database size={19} />}
          >
            <strong className="os-big-number">{r.periods}</strong>
            <span>
              {' '}
              closed {kind === 'demand' ? 'days' : 'complete weeks'} ·{' '}
              {r.rows.toLocaleString()} rows
            </span>
            <p className={'os-pill ' + (r.ready ? 'os-good' : 'os-amber')}>
              {r.ready ? 'Ready to train' : 'Collecting history'}
            </p>
            <p className="os-help">
              {r.reason ||
                'The snapshot passes the training data contract. Model quality will be measured on later, unseen periods.'}
            </p>
            <div className="os-inline-actions">
              <ExportButton
                path={'/learning/export/' + kind}
                name={'aislemint-onesales-' + kind + '.csv'}
              />
              <Action
                disabled={!r.ready}
                onClick={() =>
                  p.act(async () => {
                    await p.request('/learning/train', { kind });
                    await load();
                    p.notify('A separate model candidate has been queued.');
                  })
                }
              >
                Train candidate
              </Action>
            </div>
          </Panel>
        ))}
      </div>
      <div className="os-two">
        <Panel title="Data drift">
          <span
            className={
              'os-pill ' +
              (data.drift.status === 'drift_detected' ? 'os-amber' : '')
            }
          >
            {data.drift.status.replaceAll('_', ' ')}
          </span>
          {data.drift.reference_start && (
            <p>
              {data.drift.reference_start}–{data.drift.reference_end} versus{' '}
              {data.drift.recent_start}–{data.drift.recent_end}
            </p>
          )}
          <div className="os-three">
            <Metric
              label="Unit volume change"
              value={rate(data.drift.unit_change)}
            />
            <Metric
              label="Product mix PSI"
              value={data.drift.product_mix_psi?.toFixed(3) || '—'}
            />
            <Metric
              label="Possible stockouts"
              value={rate(data.drift.stockout_share)}
            />
          </div>
          {data.drift.signals.map((s: string) => (
            <p key={s} className="os-insight">
              {s}
            </p>
          ))}
          <p className="os-help">{data.drift.note}</p>
          <label className="os-check">
            <input
              type="checkbox"
              checked={data.auto_retrain}
              onChange={(e) => {
                const v = e.target.checked;
                p.act(async () => {
                  await p.request('/learning/settings', { auto_retrain: v });
                  await load();
                });
              }}
            />
            Train a candidate when day-close detects drift
          </label>
          <p className="os-help">
            Requires sufficient history; at most one candidate per model per
            seven days. Your current model and confirmed promotions remain in
            place.
          </p>
        </Panel>
        <Panel
          title="Local holidays & data quality"
          aside={
            <Action secondary onClick={() => setHoliday(true)}>
              <Plus size={15} />
              Add date
            </Action>
          }
        >
          <p>
            India datasets use your local holiday flags and learned seasonality.
            The US holiday calendar remains specific to US datasets.
          </p>
          {data.holidays.slice(-10).map((h: any) => (
            <div className="os-summary-row" key={h.date}>
              <span>{h.name}</span>
              <b>{h.date}</b>
            </div>
          ))}
          <p className="os-help">
            Demand includes all sold units, including free rewards. Refunds are
            separate fields. Only explicitly closed, stocked product-days become
            zero-sales observations. Days that reach zero stock are marked as
            potentially censored.
          </p>
          <p className="os-help">
            For a new store, connect historical sales through the Modelling
            workbench integration API to train sooner. Exported prices and
            revenue exclude GST; no currency conversion is applied.
          </p>
        </Panel>
      </div>
      <Panel title="Training candidates">
        <DataTable
          headers={['Created', 'Model / status', 'Source version', 'Result']}
        >
          {data.jobs.map((j: any) => (
            <tr key={j.id}>
              <td>{new Date(j.created).toLocaleString()}</td>
              <td>
                {j.kind}
                <small>{j.status}</small>
              </td>
              <td>
                <code className="os-wrap">{j.source_id}</code>
              </td>
              <td>
                {j.error ||
                  (j.status === 'ready'
                    ? 'Review this candidate in Modelling workbench before activating it.'
                    : 'Training worker queue')}
              </td>
            </tr>
          ))}
        </DataTable>
        {!data.jobs.length && (
          <Empty title="No training runs yet">
            Candidates appear here when enough history is available and training
            is requested.
          </Empty>
        )}
      </Panel>
      {close && (
        <Modal title="Close a trading day" onClose={() => setClose(false)}>
          <form
            className="os-form"
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              p.act(async () => {
                const r = await p.request('/days/close', {
                  business_date: f.get('date'),
                });
                await load();
                await p.refresh();
                setClose(false);
                p.notify('Trading day closed: ' + r.business_date);
              });
            }}
          >
            <p>
              Reconcile and close every register shift first. Closing today
              prevents further sales or refunds today and creates the model-data
              snapshot.
            </p>
            <Field label="Observed trading date">
              <input
                name="date"
                type="date"
                required
                max={p.ctx.business_date}
                min={p.ctx.store.created.slice(0, 10)}
                defaultValue={p.ctx.business_date}
              />
            </Field>
            <Action type="submit">Close day & check drift</Action>
          </form>
        </Modal>
      )}
      {holiday && (
        <Modal title="Record a local holiday" onClose={() => setHoliday(false)}>
          <form
            className="os-form"
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              p.act(async () => {
                await p.request('/holidays', {
                  date: f.get('date'),
                  name: f.get('name'),
                });
                await load();
                setHoliday(false);
              });
            }}
          >
            <Field label="Date">
              <input name="date" type="date" required />
            </Field>
            <Field label="Holiday / local occasion">
              <input name="name" minLength={2} required />
            </Field>
            <p className="os-help">
              Historical dates flag training observations. Supply future dates
              in demand planning as well; adding a date does not claim an
              accurate uplift without historical examples.
            </p>
            <Action type="submit">Save holiday</Action>
          </form>
        </Modal>
      )}
    </div>
  );
}
