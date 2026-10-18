'use client';
import { useEffect, useState } from 'react';
import {
  AreaChart,
  Area,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  BarChart,
  Bar,
} from 'recharts';
import {
  ArrowUpRight,
  ChartNoAxesCombined,
  Lightbulb,
  RefreshCw,
  ShieldAlert,
  Sparkles,
} from 'lucide-react';
import {
  usePos,
  inr,
  rate,
  Field,
  Action,
  Panel,
  Empty,
  Metric,
  DataTable,
  ExportButton,
  Loading,
} from './shared';
export default function Analytics() {
  const p = usePos();
  const today = p.ctx.business_date;
  const [end, setEnd] = useState(today),
    [start, setStart] = useState(() => {
      const d = new Date(today + 'T12:00:00');
      d.setDate(d.getDate() - 29);
      return d.toISOString().slice(0, 10);
    }),
    [tab, setTab] = useState('Overview'),
    [data, setData] = useState<any>(null),
    [radar, setRadar] = useState<any>(null),
    [radarLoading, setRadarLoading] = useState(false),
    [error, setError] = useState(''),
    [refresh, setRefresh] = useState(0);
  const query = new URLSearchParams({ start, end }).toString();
  useEffect(() => {
    let live = true;
    setError('');
    p.request('/analytics?' + query)
      .then((r) => {
        if (live) setData(r);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    setRadarLoading(true);
    p.request('/fraud-radar', { start, end })
      .then((r) => {
        if (live) setRadar(r);
      })
      .catch(() => {})
      .finally(() => {
        if (live) setRadarLoading(false);
      });
    return () => {
      live = false;
    };
  }, [query, refresh, p.request, start, end]);
  const period = (days: number) => {
    setEnd(today);
    const d = new Date(today + 'T12:00:00');
    d.setDate(d.getDate() - days + 1);
    setStart(d.toISOString().slice(0, 10));
  };
  return (
    <div className="os-stack">
      <div className="os-toolbar">
        <div className="os-categories">
          {[7, 30, 90].map((d) => (
            <button key={d} onClick={() => period(d)}>
              {d} days
            </button>
          ))}
        </div>
        <div className="os-spacer" />
        <Field label="From">
          <input
            type="date"
            value={start}
            max={end}
            onChange={(e) => setStart(e.target.value)}
          />
        </Field>
        <Field label="Through">
          <input
            type="date"
            value={end}
            min={start}
            onChange={(e) => setEnd(e.target.value)}
          />
        </Field>
        <Action secondary onClick={() => setRefresh((n) => n + 1)}>
          <RefreshCw size={16} />
          Refresh
        </Action>
      </div>
      {error && <p className="os-inline-error">{error}</p>}
      {!data ? (
        <Loading />
      ) : (
        <>
          <div className="os-metrics">
            <Metric
              accent
              label="Net sales"
              value={inr(data.kpis.net_sales_paise)}
              detail="After refunds · excludes GST"
            />
            <Metric
              label="Contribution"
              value={inr(data.kpis.contribution_paise)}
              detail={
                rate(data.kpis.margin_rate) + ' margin · before overheads'
              }
            />
            <Metric
              label="Transactions"
              value={data.kpis.transactions.toLocaleString()}
              detail={data.kpis.units_sold.toLocaleString() + ' units sold'}
            />
            <Metric
              label="Average basket"
              value={inr(data.kpis.average_basket_paise)}
              detail={
                data.kpis.units_per_basket.toFixed(1) + ' units per basket'
              }
            />
          </div>
          <nav className="os-tabs" aria-label="Analytics sections">
            {[
              'Overview',
              'Products & inventory',
              'Customers',
              'GST & payments',
              'Returns & registers',
              'AI Fraud & Margin Radar',
            ].map((t) => (
              <button
                aria-current={t === tab ? 'page' : undefined}
                key={t}
                onClick={() => setTab(t)}
              >
                {t}
              </button>
            ))}
          </nav>
          {tab === 'Overview' && (
            <>
              <div className="os-analytics-main">
                <Panel
                  title="Sales & contribution"
                  aside={
                    <ExportButton
                      path={'/export/daily?' + query}
                      name="aislemint-onesales-daily.csv"
                    />
                  }
                >
                  <div className="os-chart">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart
                        data={data.daily.map((d: any) => ({
                          ...d,
                          revenue: d.net_sales_paise / 100,
                          contribution: d.contribution_paise / 100,
                        }))}
                      >
                        <defs>
                          <linearGradient
                            id="os-revenue-fill"
                            x1="0"
                            y1="0"
                            x2="0"
                            y2="1"
                          >
                            <stop
                              offset="0%"
                              stopColor="#217860"
                              stopOpacity={0.25}
                            />
                            <stop
                              offset="100%"
                              stopColor="#217860"
                              stopOpacity={0}
                            />
                          </linearGradient>
                        </defs>
                        <CartesianGrid
                          strokeDasharray="3 5"
                          vertical={false}
                          stroke="#e4ebe7"
                        />
                        <XAxis
                          dataKey="date"
                          tickFormatter={(v) => v.slice(5)}
                          axisLine={false}
                          tickLine={false}
                        />
                        <YAxis
                          tickFormatter={(v) =>
                            '₹' + (v >= 1000 ? (v / 1000).toFixed(0) + 'k' : v)
                          }
                          axisLine={false}
                          tickLine={false}
                          width={65}
                        />
                        <Tooltip formatter={(v: any) => inr(Number(v) * 100)} />
                        <Area
                          name="Net sales"
                          dataKey="revenue"
                          stroke="#217860"
                          strokeWidth={2}
                          fill="url(#os-revenue-fill)"
                        />
                        <Area
                          name="Contribution"
                          dataKey="contribution"
                          stroke="#c89240"
                          strokeWidth={2}
                          fill="transparent"
                        />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                  <div className="os-legend">
                    <span>
                      <i />
                      Net sales
                    </span>
                    <span>
                      <i style={{ background: '#c89240' }} />
                      Contribution
                    </span>
                  </div>
                </Panel>
                <Panel
                  title="What needs attention"
                  aside={<Lightbulb size={20} />}
                >
                  <div className="os-insights">
                    {data.insights.length ? (
                      data.insights.map((s: string) => (
                        <p className="os-insight" key={s}>
                          <ArrowUpRight size={17} />
                          {s}
                        </p>
                      ))
                    ) : (
                      <p>No rule-based exceptions found in this period.</p>
                    )}
                  </div>
                  <div className="os-summary-row">
                    <span>Inventory at cost</span>
                    <b>{inr(data.kpis.inventory_cost_paise)}</b>
                  </div>
                  <div className="os-summary-row">
                    <span>At / below reorder level</span>
                    <b>{data.kpis.low_stock_products} products</b>
                  </div>
                </Panel>
              </div>
              <div className="os-two">
                <Panel title="Category performance">
                  <DataTable
                    headers={['Category', 'Net sales', 'Contribution']}
                  >
                    {data.categories.map((c: any) => (
                      <tr key={c.category}>
                        <td>{c.category}</td>
                        <td>{inr(c.net_sales_paise)}</td>
                        <td>{inr(c.contribution_paise)}</td>
                      </tr>
                    ))}
                  </DataTable>
                </Panel>
                <Panel title="When customers shop">
                  <div className="os-chart">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={data.hourly}>
                        <CartesianGrid vertical={false} strokeDasharray="3 5" />
                        <XAxis
                          dataKey="hour"
                          tickFormatter={(v) => `${v}:00`}
                          tickLine={false}
                          axisLine={false}
                        />
                        <YAxis
                          allowDecimals={false}
                          tickLine={false}
                          axisLine={false}
                        />
                        <Tooltip labelFormatter={(v) => `${v}:00 local time`} />
                        <Bar
                          name="Transactions"
                          dataKey="transactions"
                          fill="#76a593"
                          radius={[4, 4, 0, 0]}
                        />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </Panel>
              </div>
              <Panel title="Promotion execution">
                <DataTable
                  headers={[
                    'Offer mechanic',
                    'Associated transactions',
                    'Discount given',
                  ]}
                >
                  {data.promotions.map((o: any) => (
                    <tr key={o.kind}>
                      <td>{o.kind.toUpperCase()}</td>
                      <td>{o.transactions}</td>
                      <td>{inr(o.saving_paise)}</td>
                    </tr>
                  ))}
                </DataTable>
                {!data.promotions.length && (
                  <p className="os-help">
                    No executed offer discounts in this period.
                  </p>
                )}
                <p className="os-help">
                  These are recorded discounts, not incremental ROI. Promotion
                  review compares observed outcomes with a learned no-promotion
                  baseline.
                </p>
              </Panel>
            </>
          )}
          {tab === 'Products & inventory' && (
            <>
              <div className="os-three">
                <Metric
                  label="Stock value at landed cost"
                  value={inr(data.kpis.inventory_cost_paise)}
                />
                <Metric
                  label="Stock writeoffs"
                  value={inr(data.kpis.stock_writeoff_paise)}
                />
                <Metric
                  label="Low-stock products"
                  value={data.kpis.low_stock_products}
                />
              </div>
              <Panel
                title="Product economics & availability"
                aside={
                  <ExportButton
                    path={'/export/products?' + query}
                    name="aislemint-onesales-product-performance.csv"
                  />
                }
              >
                <DataTable
                  headers={[
                    'Product',
                    'Sold / returned',
                    'Net sales',
                    'Contribution',
                    'Margin',
                    'On hand',
                    'Days of stock',
                  ]}
                >
                  {data.products.map((x: any) => (
                    <tr key={x.product_id}>
                      <td>
                        <strong>{x.name}</strong>
                        <small>
                          {x.category} · {x.product_id}
                        </small>
                      </td>
                      <td>
                        {x.units_sold} / {x.return_units}
                      </td>
                      <td>{inr(x.net_sales_paise)}</td>
                      <td
                        className={
                          x.contribution_paise < 0 ? 'os-negative' : ''
                        }
                      >
                        {inr(x.contribution_paise)}
                      </td>
                      <td>{rate(x.margin_rate)}</td>
                      <td>
                        <span
                          className={
                            'os-pill ' +
                            (x.on_hand <= x.reorder_level ? 'os-amber' : '')
                          }
                        >
                          {x.on_hand}
                        </span>
                      </td>
                      <td>{x.days_of_stock?.toFixed(1) || '—'}</td>
                    </tr>
                  ))}
                </DataTable>
                <p className="os-help">
                  Days of stock divides current units by this period’s average
                  sales rate. Use Demand forecast and Inventory planner for
                  seasonal forecasts and replenishment decisions.
                </p>
              </Panel>
            </>
          )}
          {tab === 'Customers' && (
            <>
              <div className="os-three">
                <Metric
                  label="Identified customers"
                  value={data.kpis.identified_customers}
                />
                <Metric
                  label="Average basket incl. GST"
                  value={inr(data.kpis.average_basket_paise)}
                />
                <Metric
                  label="Units per transaction"
                  value={data.kpis.units_per_basket.toFixed(1)}
                />
              </div>
              <Panel title="Customers by observed spend">
                <DataTable
                  headers={[
                    'Customer',
                    'Contact',
                    'Visits',
                    'Gross paid',
                    'Refunds',
                    'Net paid',
                  ]}
                >
                  {data.customers.map((c: any) => (
                    <tr key={c.id}>
                      <td>{c.name}</td>
                      <td>{c.phone || '—'}</td>
                      <td>{c.visits}</td>
                      <td>{inr(c.spent_paise)}</td>
                      <td>{inr(c.refund_paise)}</td>
                      <td>{inr(c.net_spent_paise)}</td>
                    </tr>
                  ))}
                </DataTable>
                {!data.customers.length && (
                  <Empty title="No identified customer purchases">
                    Attach a customer at checkout to build purchase history.
                    Walk-in sales still count in store totals.
                  </Empty>
                )}
              </Panel>
            </>
          )}
          {tab === 'GST & payments' && (
            <>
              <div className="os-three">
                <Metric
                  label="GST after credit notes"
                  value={inr(data.kpis.gst_paise)}
                />
                <Metric
                  label="Net collections incl. GST"
                  value={inr(data.kpis.net_collected_paise)}
                />
                <Metric
                  label="Discounts given incl. GST"
                  value={inr(data.kpis.discount_paise)}
                />
              </div>
              <Panel
                title="GST reconciliation"
                aside={
                  <ExportButton
                    path={'/export/taxes?' + query}
                    name="aislemint-onesales-gst.csv"
                  />
                }
              >
                <DataTable
                  headers={[
                    'GST rate',
                    'Net taxable value',
                    'CGST',
                    'SGST',
                    'IGST',
                  ]}
                >
                  {data.taxes.map((t: any) => (
                    <tr key={t.gst_bps}>
                      <td>{t.gst_bps / 100}%</td>
                      <td>{inr(t.taxable_paise)}</td>
                      <td>{inr(t.cgst_paise)}</td>
                      <td>{inr(t.sgst_paise)}</td>
                      <td>{inr(t.igst_paise)}</td>
                    </tr>
                  ))}
                </DataTable>
                <p className="os-help">
                  Amounts are from invoices and credit notes on their respective
                  document dates. This reconciliation export does not submit a
                  GST return.
                </p>
              </Panel>
              <Panel
                title="Payment methods"
                aside={
                  <ExportButton
                    path={'/export/payments?' + query}
                    name="aislemint-onesales-payments.csv"
                  />
                }
              >
                <DataTable
                  headers={[
                    'Method',
                    'Collected after cash change',
                    'Refunded',
                    'Net',
                  ]}
                >
                  {data.payments.map((t: any) => (
                    <tr key={t.method}>
                      <td>{t.method.replaceAll('_', ' ')}</td>
                      <td>{inr(t.collected_paise)}</td>
                      <td>{inr(t.refunded_paise)}</td>
                      <td>{inr(t.net_paise)}</td>
                    </tr>
                  ))}
                </DataTable>
                <p className="os-help">
                  External payments are cashier-recorded. Reconcile their
                  references with your terminal or bank; settlement is not
                  connected.
                </p>
              </Panel>
            </>
          )}
          {tab === 'Returns & registers' && (
            <>
              <div className="os-metrics">
                <Metric
                  label="Returned units"
                  value={data.kpis.units_returned}
                />
                <Metric
                  label="Refund value ex. GST"
                  value={inr(data.kpis.refunds_ex_gst_paise)}
                />
                <Metric
                  label="Return value / sales"
                  value={rate(data.kpis.return_value_rate)}
                />
                <Metric
                  label="Discarded return cost"
                  value={inr(data.kpis.discarded_returns_cost_paise)}
                />
              </div>
              <Panel
                title="Cash register reconciliation"
                aside={
                  <ExportButton
                    path={'/export/returns?' + query}
                    name="aislemint-onesales-returns.csv"
                    label="Export returns"
                  />
                }
              >
                <DataTable
                  headers={[
                    'Register / date',
                    'Cashier',
                    'Status',
                    'Expected cash',
                    'Counted',
                    'Variance',
                  ]}
                >
                  {data.shifts.map((s: any) => (
                    <tr key={s.id}>
                      <td>
                        {s.register_id}
                        <small>{s.business_date}</small>
                      </td>
                      <td>{s.cashier}</td>
                      <td>{s.closed ? 'Closed' : 'Open'}</td>
                      <td>{inr(s.expected_paise)}</td>
                      <td>{inr(s.counted_paise)}</td>
                      <td className={s.variance_paise ? 'os-negative' : ''}>
                        {inr(s.variance_paise)}
                      </td>
                    </tr>
                  ))}
                </DataTable>
              </Panel>
            </>
          )}
          {tab === 'AI Fraud & Margin Radar' && (
            <>
              {radarLoading || !radar ? (
                <Loading />
              ) : (
                <>
                  <div className="os-metrics">
                    <Metric
                      accent
                      label="Forensic Risk Score"
                      value={`${radar.risk_score} / 100`}
                      detail={`Posture: ${radar.risk_level}`}
                    />
                    <Metric
                      label="Margin Exposure Monitored"
                      value={inr(radar.margin_at_risk_paise)}
                      detail="Discounts, variances, discards & writeoffs"
                    />
                    <Metric
                      label="Flagged Signals"
                      value={String(radar.anomalies.length)}
                      detail={`${radar.start} through ${radar.end}`}
                    />
                    <Metric
                      label="Audit Engine"
                      value={radar.ai_powered ? 'Gemini AI' : 'Rule Guard'}
                      detail={radar.model}
                    />
                  </div>
                  <div className="os-two">
                    <Panel
                      title="Executive Forensic Audit Brief"
                      aside={<ShieldAlert size={20} />}
                    >
                      <p style={{ fontSize: 14.5, lineHeight: 1.6 }}>
                        {radar.executive_summary}
                      </p>
                      <div
                        style={{
                          marginTop: 12,
                          padding: '10px 12px',
                          background: '#f2f8f5',
                          borderRadius: 8,
                          border: '1px solid #cce3d8',
                          fontSize: 13,
                        }}
                      >
                        <strong>
                          <Sparkles size={14} style={{ marginRight: 5 }} />
                          Neuro-Symbolic Return Clawback Active:
                        </strong>{' '}
                        OneSales deterministically recalculates BXGY and BXGCY
                        bundle eligibility on every credit note quote,
                        preventing free-reward retention fraud.
                      </div>
                    </Panel>
                    <Panel
                      title="Recommended Operational Guardrails"
                      aside={<Lightbulb size={20} />}
                    >
                      <div className="os-insights">
                        {radar.recommended_controls.map((c: string) => (
                          <p className="os-insight" key={c}>
                            <ArrowUpRight size={17} />
                            {c}
                          </p>
                        ))}
                      </div>
                    </Panel>
                  </div>
                  <Panel title="Detected Margin Leakage & Return/Register Anomalies">
                    {radar.anomalies.length ? (
                      <DataTable
                        headers={[
                          'Severity',
                          'Category',
                          'Signal & Forensic Evidence',
                          'Reference',
                          'Monitored Impact',
                        ]}
                      >
                        {radar.anomalies.map((a: any) => (
                          <tr key={a.id}>
                            <td>
                              <span
                                className={
                                  'os-pill ' +
                                  (a.severity === 'high'
                                    ? 'os-amber'
                                    : 'os-good')
                                }
                              >
                                {a.severity.toUpperCase()}
                              </span>
                            </td>
                            <td>{a.category}</td>
                            <td>
                              <strong>{a.title}</strong>
                              <small>{a.detail}</small>
                            </td>
                            <td>{a.reference}</td>
                            <td className="os-negative">
                              {inr(a.impact_paise)}
                            </td>
                          </tr>
                        ))}
                      </DataTable>
                    ) : (
                      <Empty title="Zero fraud or margin-leakage anomalies detected">
                        All closed shifts reconciled cleanly, product
                        contributions are positive, and promotional return
                        clawbacks are in compliance.
                      </Empty>
                    )}
                  </Panel>
                </>
              )}
            </>
          )}
          <details className="os-definitions">
            <summary>How these metrics are calculated</summary>
            {data.definitions.map((d: string) => (
              <p key={d}>{d}</p>
            ))}
            <p>
              All exports use integer paise for money columns. One rupee equals
              100 paise.
            </p>
          </details>
        </>
      )}
    </div>
  );
}
