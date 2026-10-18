'use client';
import { useEffect, useRef, useState } from 'react';
import {
  Search,
  ArrowUpRight,
  RotateCcw,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Tag,
} from 'lucide-react';
import {
  usePos,
  inr,
  Field,
  Action,
  Panel,
  Empty,
  Modal,
  Metric,
  DataTable,
  ExportButton,
  PrintButton,
  Loading,
  saveRetry,
  clearRetry,
  definitiveFailure,
} from './shared';
export default function Sales() {
  const p = usePos();
  const [q, setQ] = useState(''),
    [start, setStart] = useState(''),
    [end, setEnd] = useState(''),
    [offset, setOffset] = useState(0),
    [data, setData] = useState<any>(null),
    [sale, setSale] = useState<any>(null),
    [refresh, setRefresh] = useState(0),
    [returning, setReturning] = useState(false);
  useEffect(() => {
    let live = true;
    const t = setTimeout(
      () =>
        p
          .request(
            '/sales?' +
              new URLSearchParams({ q, start, end, offset: String(offset) }),
          )
          .then((r) => {
            if (live) setData(r);
          })
          .catch((e) => p.notify(e.message)),
      180,
    );
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [q, start, end, offset, refresh, p.request]);
  return (
    <>
      <div className="os-toolbar">
        <div className="os-search">
          <Search size={18} />
          <input
            aria-label="Search transactions"
            placeholder="Invoice, transaction ID, customer or phone…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setOffset(0);
            }}
          />
        </div>
        <Field label="From">
          <input
            type="date"
            value={start}
            onChange={(e) => {
              setStart(e.target.value);
              setOffset(0);
            }}
          />
        </Field>
        <Field label="Through">
          <input
            type="date"
            value={end}
            onChange={(e) => {
              setEnd(e.target.value);
              setOffset(0);
            }}
          />
        </Field>
        {p.ctx.staff.role === 'manager' && (
          <ExportButton
            path={'/export/transactions?' + new URLSearchParams({ start, end })}
            name="aislemint-onesales-transactions.csv"
          />
        )}
      </div>
      <Panel
        title="Every sale, traceable"
        aside={
          <span className="os-help">{data?.total ?? '…'} transactions</span>
        }
      >
        {!data ? (
          <Loading />
        ) : !data.rows.length ? (
          <Empty title="No transactions found">
            Completed checkouts appear here with their payments, receipt and
            return history.
          </Empty>
        ) : (
          <DataTable
            headers={[
              'Invoice / time',
              'Customer',
              'Cashier',
              'Amount incl. GST',
              'Refunded',
              'Status',
              '',
            ]}
          >
            {data.rows.map((s: any) => (
              <tr key={s.id}>
                <td>
                  <strong>{s.number}</strong>
                  <small>{new Date(s.created).toLocaleString()}</small>
                </td>
                <td>{s.customer_name || 'Walk-in customer'}</td>
                <td>{s.cashier}</td>
                <td>{inr(s.total_paise)}</td>
                <td>{s.refunded_paise ? inr(s.refunded_paise) : '—'}</td>
                <td>
                  <span
                    className={
                      'os-pill ' + (s.refunded_paise ? 'os-amber' : 'os-good')
                    }
                  >
                    {s.refunded_paise
                      ? s.refunded_paise === s.total_paise
                        ? 'Refunded'
                        : 'Partial refund'
                      : 'Completed'}
                  </span>
                </td>
                <td>
                  <button
                    className="os-link"
                    onClick={() =>
                      p.act(async () => {
                        setSale(await p.request('/sales/' + s.id));
                        setReturning(false);
                      })
                    }
                  >
                    View <ArrowUpRight size={15} />
                  </button>
                </td>
              </tr>
            ))}
          </DataTable>
        )}
        <div className="os-pagination">
          <Action
            secondary
            disabled={!offset}
            onClick={() => setOffset((n) => Math.max(0, n - 50))}
          >
            <ChevronLeft size={16} />
            Previous
          </Action>
          <span>
            {data?.total
              ? `${offset + 1}–${Math.min(offset + 50, data.total)} of ${data.total}`
              : '0 records'}
          </span>
          <Action
            secondary
            disabled={!data || offset + 50 >= data.total}
            onClick={() => setOffset((n) => n + 50)}
          >
            Next
            <ChevronRight size={16} />
          </Action>
        </div>
      </Panel>
      {sale && !returning && (
        <Modal title={sale.number} wide onClose={() => setSale(null)}>
          <div className="os-detail-summary">
            <div>
              <span className="os-pill">
                {sale.status.replaceAll('_', ' ')}
              </span>
              <p>
                {new Date(sale.created).toLocaleString()}
                <br />
                {sale.cashier.name} ·{' '}
                {sale.customer?.name || 'Walk-in customer'}
              </p>
              <small className="os-help">Transaction ID: {sale.id}</small>
            </div>
            <div className="os-inline-actions">
              <PrintButton id={sale.id} />
              {p.ctx.staff.role === 'manager' && (
                <Action
                  disabled={!sale.lines.some((l: any) => l.returnable > 0)}
                  onClick={() => setReturning(true)}
                >
                  <RotateCcw size={16} />
                  Return products
                </Action>
              )}
            </div>
          </div>
          <div className="os-three">
            <Metric label="Original amount" value={inr(sale.total_paise)} />
            <Metric label="Refunds recorded" value={inr(sale.refunded_paise)} />
            <Metric
              label="Retained amount"
              value={inr(sale.total_paise - sale.refunded_paise)}
            />
          </div>
          <DataTable
            headers={[
              'Product / HSN',
              'Sold',
              'Returned',
              'Net value',
              'GST',
              'Paid',
            ]}
          >
            {sale.lines.map((l: any) => (
              <tr key={l.product_id}>
                <td>
                  <strong>{l.name}</strong>
                  <small>
                    {l.product_id} · HSN {l.hsn} · {l.gst_bps / 100}%
                  </small>
                  {l.free_units > 0 && (
                    <small className="os-green">
                      {l.free_units} free units
                    </small>
                  )}
                </td>
                <td>{l.quantity}</td>
                <td>{l.returned}</td>
                <td>{inr(l.net_paise)}</td>
                <td>{inr(l.tax_paise)}</td>
                <td>{inr(l.paid_paise)}</td>
              </tr>
            ))}
          </DataTable>
          <div className="os-two os-top">
            <Panel title="Payments & customer">
              {sale.tenders.map((t: any, i: number) => (
                <div className="os-summary-row" key={i}>
                  <span>
                    {t.method.replaceAll('_', ' ')}
                    <small>{t.reference || 'Cash tendered'}</small>
                  </span>
                  <b>{inr(t.amount_paise)}</b>
                </div>
              ))}
              <div className="os-summary-row">
                <span>Cash change</span>
                <b>{inr(sale.change_paise)}</b>
              </div>
              {sale.customer && (
                <p>
                  {sale.customer.name}
                  <br />
                  {sale.customer.phone}
                  <br />
                  {sale.customer.address}
                  <br />
                  {sale.customer.gstin}
                </p>
              )}
              <p className="os-help">
                Place of supply: {sale.place_of_supply} ·{' '}
                {sale.interstate ? 'IGST' : 'CGST + SGST'}
              </p>
            </Panel>
            <Panel title="Executed offers">
              {sale.applied_promotions.length ? (
                sale.applied_promotions.map((o: any, i: number) => (
                  <div key={i} className="os-summary-row">
                    <span>
                      <Tag size={14} /> {o.kind.toUpperCase()} · {o.product_id}
                      <small>Campaign {o.campaign_ids.join(', ')}</small>
                    </span>
                    <b>{inr(o.saving_paise)}</b>
                  </div>
                ))
              ) : (
                <p>No offer discount on this basket.</p>
              )}
              <p className="os-help">
                Original prices and offer terms are preserved for accurate
                returns, even if a campaign changes later.
              </p>
            </Panel>
          </div>
          <Panel title="Return history">
            {sale.returns.length ? (
              sale.returns.map((r: any) => (
                <div className="os-return-history" key={r.id}>
                  <div>
                    <strong>
                      {r.number} · {inr(r.total_paise)}
                    </strong>
                    <small>{new Date(r.created).toLocaleString()}</small>
                    {r.lines.map((l: any, i: number) => (
                      <p key={i}>
                        {l.quantity} × {l.name} · {l.disposition} · {l.reason}
                      </p>
                    ))}
                  </div>
                  <PrintButton id={r.id} credit />
                </div>
              ))
            ) : (
              <p>No products have been returned.</p>
            )}
          </Panel>
        </Modal>
      )}
      {sale && returning && (
        <ReturnDialog
          sale={sale}
          onClose={() => setReturning(false)}
          onSuccess={async () => {
            setSale(await p.request('/sales/' + sale.id));
            setRefresh((n) => n + 1);
            await p.refresh();
            setReturning(false);
          }}
        />
      )}
    </>
  );
}
function ReturnDialog({
  sale,
  onClose,
  onSuccess,
}: {
  sale: any;
  onClose: () => void;
  onSuccess: () => Promise<void>;
}) {
  const p = usePos();
  const [selected, setSelected] = useState<any>({}),
    [quote, setQuote] = useState<any>(null),
    [error, setError] = useState(''),
    [saving, setSaving] = useState(false),
    [reference, setReference] = useState(''),
    [credit, setCredit] = useState<any>(null),
    [shiftId, setShiftId] = useState('');
  const pending = useRef<any>(null),
    locked = useRef(false);
  const shifts = p.ctx.shifts.filter(
      (s: any) => s.business_date === p.ctx.business_date,
    ),
    shift = shifts.find((s: any) => s.id === shiftId) || shifts[0];
  const edit = (id: string, key: string, value: any) => {
    setSelected((s: any) => ({
      ...s,
      [id]: {
        quantity: 0,
        disposition: 'restock',
        reason: '',
        ...s[id],
        [key]: value,
      },
    }));
    setQuote(null);
    setError('');
  };
  const lines = Object.entries(selected)
    .filter(([_, l]: any) => l.quantity > 0)
    .map(([product_id, l]: any) => ({ product_id, ...l }));
  const tenderAllocation = () => {
    let left = quote.total_paise;
    const tenders = [];
    for (const [method, cap] of Object.entries(quote.refundable_methods)) {
      const amount = Math.min(left, Number(cap));
      if (amount > 0)
        tenders.push({
          method,
          amount_paise: amount,
          reference: method === 'cash' ? '' : reference,
        });
      left -= amount;
    }
    return tenders;
  };
  return (
    <Modal
      title={credit ? 'Return completed' : 'Return products · ' + sale.number}
      wide
      onClose={() => {
        if (!saving && !pending.current) {
          if (credit) void onSuccess();
          else onClose();
        }
      }}
    >
      {credit ? (
        <div className="os-sale-success">
          <CheckCircle2 size={45} />
          <h2>{inr(credit.total_paise)} refunded</h2>
          <p>
            {credit.restock_units} units restocked · {credit.discard_units}{' '}
            discarded
          </p>
          <PrintButton id={credit.id} credit />
          <Action onClick={() => void onSuccess()}>Back to transaction</Action>
        </div>
      ) : (
        <>
          <p className="os-help">
            Refunds use the original paid amount. A return that breaks an offer
            condition must include the linked reward or remaining bundle. Choose
            whether each returned product goes back on the shelf.
          </p>
          <fieldset
            className="os-fieldset"
            disabled={saving || !!pending.current}
          >
            <Action
              secondary
              onClick={() => {
                setSelected(
                  Object.fromEntries(
                    sale.lines
                      .filter((l: any) => l.returnable > 0)
                      .map((l: any) => [
                        l.product_id,
                        {
                          quantity: l.returnable,
                          disposition: 'restock',
                          reason: 'Customer return',
                        },
                      ]),
                  ),
                );
                setQuote(null);
              }}
            >
              Select all returnable items
            </Action>
            <DataTable
              headers={['Product', 'Return qty', 'Disposition', 'Reason']}
            >
              {sale.lines
                .filter((l: any) => l.returnable > 0)
                .map((l: any) => (
                  <tr key={l.product_id}>
                    <td>
                      {l.name}
                      <small>
                        {l.returnable} available · originally paid{' '}
                        {inr(l.paid_paise)}
                      </small>
                    </td>
                    <td>
                      <input
                        aria-label={'Return quantity ' + l.name}
                        type="number"
                        min="0"
                        max={l.returnable}
                        step="1"
                        value={selected[l.product_id]?.quantity || 0}
                        onChange={(e) =>
                          edit(l.product_id, 'quantity', Number(e.target.value))
                        }
                      />
                    </td>
                    <td>
                      <select
                        aria-label={'Disposition ' + l.name}
                        value={selected[l.product_id]?.disposition || 'restock'}
                        onChange={(e) =>
                          edit(l.product_id, 'disposition', e.target.value)
                        }
                      >
                        <option value="restock">Add back to inventory</option>
                        <option value="discard">Discard / damaged</option>
                      </select>
                    </td>
                    <td>
                      <input
                        aria-label={'Return reason ' + l.name}
                        placeholder="Reason (required)"
                        value={selected[l.product_id]?.reason || ''}
                        onChange={(e) =>
                          edit(l.product_id, 'reason', e.target.value)
                        }
                      />
                    </td>
                  </tr>
                ))}
            </DataTable>
            <Action
              secondary
              disabled={!lines.length || lines.some((l) => l.reason.length < 3)}
              onClick={async () => {
                try {
                  setError('');
                  setQuote(
                    await p.request('/sales/' + sale.id + '/return-quote', {
                      lines,
                    }),
                  );
                } catch (e: any) {
                  setError(e.message);
                }
              }}
            >
              Review refund
            </Action>
          </fieldset>
          {error && (
            <p className="os-inline-error" role="alert">
              {error}
            </p>
          )}
          {quote && (
            <form
              className="os-form"
              onSubmit={async (e) => {
                e.preventDefault();
                if (locked.current) return;
                locked.current = true;
                setSaving(true);
                setError('');
                try {
                  if (!pending.current)
                    pending.current = {
                      lines,
                      quote_token: quote.quote_token,
                      shift_id: shift.id,
                      idempotency_key: crypto.randomUUID(),
                      tenders: tenderAllocation(),
                    };
                  await saveRetry(
                    p.session,
                    '/sales/' + sale.id + '/return',
                    pending.current,
                  );
                  const r = await p.request(
                    '/sales/' + sale.id + '/return',
                    pending.current,
                  );
                  pending.current = null;
                  await clearRetry(p.session);
                  setCredit(r);
                } catch (e: any) {
                  setError(
                    e.message +
                      (e.status
                        ? ''
                        : ' Retry the same refund recording to recover safely.'),
                  );
                  if (definitiveFailure(e)) {
                    pending.current = null;
                    if (!e.preserveRetry) await clearRetry(p.session);
                  }
                } finally {
                  setSaving(false);
                  locked.current = false;
                }
              }}
            >
              <fieldset
                className="os-fieldset"
                disabled={saving || !!pending.current}
              >
                <div className="os-payment-total">
                  <span>Reviewed refund</span>
                  <strong>{inr(quote.total_paise)}</strong>
                </div>
                <Field label="Refund register">
                  <select
                    required
                    value={shift?.id || ''}
                    onChange={(e) => setShiftId(e.target.value)}
                  >
                    {!shift && (
                      <option value="">
                        Open a register in OneSales first
                      </option>
                    )}
                    {shifts.map((s: any) => (
                      <option key={s.id} value={s.id}>
                        {s.register_id}
                      </option>
                    ))}
                  </select>
                </Field>
                {tenderAllocation().map((t: any) => (
                  <div className="os-summary-row" key={t.method}>
                    <span>{t.method.replaceAll('_', ' ')}</span>
                    <b>{inr(t.amount_paise)}</b>
                  </div>
                ))}
                {tenderAllocation().some((t: any) => t.method !== 'cash') && (
                  <>
                    <Field label="Successful external refund reference(s)">
                      <input
                        required
                        value={reference}
                        onChange={(e) => setReference(e.target.value)}
                        maxLength={100}
                      />
                    </Field>
                    <p className="os-help">
                      Perform the shown external refunds on the original payment
                      terminal before recording them here. For multiple external
                      methods, include each reference.
                    </p>
                  </>
                )}
              </fieldset>
              <Action type="submit" disabled={!shift || saving}>
                {saving
                  ? 'Recording refund…'
                  : pending.current
                    ? 'Retry same refund recording'
                    : 'Record refund & update stock'}
              </Action>
            </form>
          )}
        </>
      )}
    </Modal>
  );
}
