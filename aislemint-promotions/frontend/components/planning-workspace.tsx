'use client';
import { BrandMark } from '@/components/brand';
import OfferEditor, {
  emptyOfferConfig,
  OfferEvidence,
  type OfferConfig,
} from '@/components/offer-editor';
import { useAccess, type Shop } from '@/components/retail-platform';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Slider } from '@/components/ui/slider';
import { Switch } from '@/components/ui/switch';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  api,
  download,
  money as formatMoney,
  number,
  percent,
  shortDate,
  type Product,
} from '@/lib/api';
import type { DemandHandoff } from '@/lib/workspace-contracts';
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  CalendarDays,
  ChartNoAxesCombined,
  CheckCircle2,
  ChevronRight,
  Download,
  History,
  Info,
  Layers,
  LoaderCircle,
  Play,
  Plus,
  Save,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  TrendingUp,
  X,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import {
  Busy,
  colors,
  DemandChart,
  Effects,
  Empty,
  Metric,
  Numeric,
  Picker,
  PlanningNote,
  Warnings,
} from '@/components/planning-ui';
export default function PlanningWorkspace({
  retailShop,
  view,
  setView,
  onDemandHandoff,
}: {
  retailShop: Shop | null;
  view: string;
  setView: (view: string) => void;
  onDemandHandoff: (handoff: DemandHandoff) => void;
}) {
  const can = useAccess();
  const canRun = can('promotions', 'user'),
    canSave = can('promotions', 'editor');
  const [workspace, setWorkspace] = useState(retailShop?.id || 'benchmark');
  const [summary, setSummary] = useState<any>(null);
  const money = (value: number | null | undefined) =>
    formatMoney(value, summary?.quality?.currency || 'USD');
  const [catalog, setCatalog] = useState<Product[]>([]);
  const [store, setStore] = useState(retailShop?.store_code || '367');
  const [pid, setPid] = useState('');
  const [history, setHistory] = useState<any>(null);
  const [result, setResult] = useState<any>(null);
  const [optimization, setOptimization] = useState<any>(null);
  const [saved, setSaved] = useState<any[]>([]);
  const [reviewedPlan, setReviewedPlan] = useState<any>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [toast, setToast] = useState('');
  const [reload, setReload] = useState(0);
  const [discount, setDiscount] = useState(20);
  const [duration, setDuration] = useState('2');
  const [feature, setFeature] = useState(false);
  const [display, setDisplay] = useState(false);
  const [margin, setMargin] = useState(35);
  const [cost, setCost] = useState(0);
  const [funding, setFunding] = useState(0);
  const [inventory, setInventory] = useState('');
  const [scenarioName, setScenarioName] = useState('New promotion scenario');
  const [startDate, setStartDate] = useState('');
  const [offerConfig, setOfferConfig] = useState<OfferConfig>(emptyOfferConfig);
  const [budget, setBudget] = useState(500);
  const [objective, setObjective] = useState('profit');
  const [maxPromos, setMaxPromos] = useState('3');
  const [minMargin, setMinMargin] = useState(10);
  const [selectedProducts, setSelectedProducts] = useState<string[]>([]);
  const [savedId, setSavedId] = useState('');
  const [historyLoading, setHistoryLoading] = useState(false);
  const [confirmationKeys, setConfirmationKeys] = useState<
    Record<string, string>
  >({});
  const [flyerText, setFlyerText] = useState('');
  const [flyerImageBase64, setFlyerImageBase64] = useState<string | null>(null);
  const [flyerImageMime, setFlyerImageMime] = useState('image/jpeg');
  const [flyerImageName, setFlyerImageName] = useState('');
  const [flyerScan, setFlyerScan] = useState<any>(null);
  const [campaignKit, setCampaignKit] = useState<any>(null);
  const [kitLang, setKitLang] = useState<'english' | 'hindi' | 'kannada'>(
    'english',
  );
  const chosen = catalog.find((p) => p.product_id === pid);
  const lastFold = summary?.model?.folds?.at(-1);
  const request = {
    store_id: store,
    start_date: startDate || null,
    promotions: [
      {
        product_id: pid,
        discount: discount / 100,
        duration: Number(duration),
        feature,
        display,
        fixed_cost: cost,
        funding_per_unit: funding,
        ...offerConfig,
      },
    ],
    margin_rate: margin / 100,
    inventory_units: inventory ? Number(inventory) : null,
    name: scenarioName,
  };
  function notify(s: string) {
    setToast(s);
    setTimeout(() => setToast(''), 5000);
  }
  function confirmPromotion(scenario: any, forecast: any) {
    return run('confirm-promotion', async () => {
      if (!forecast?.confirmation_token)
        throw new Error(
          'Run this scenario again to review it before confirmation.',
        );
      const key =
        confirmationKeys[forecast.confirmation_token] || crypto.randomUUID();
      setConfirmationKeys((old) => ({
        ...old,
        [forecast.confirmation_token]: key,
      }));
      const confirmed = await api('/promotions/confirm', workspace, {
        scenario: { ...scenario, start_date: forecast.start_date },
        review_token: forecast.confirmation_token,
        idempotency_key: key,
      });
      const offset = Math.round(
        (new Date(forecast.start_date).getTime() -
          new Date(summary.quality.end).getTime()) /
          604800000,
      );
      onDemandHandoff({
        source_id: workspace,
        store_id: scenario.store_id,
        product_ids: Array.from(
          new Set<string>(
            scenario.promotions.flatMap((p: any) => [
              p.product_id,
              ...(p.mechanics || []).flatMap(
                (o: any) => o.reward_choices || [],
              ),
            ]),
          ),
        ),
        horizon: Math.min(
          13,
          Math.max(
            8,
            offset +
              Math.max(...scenario.promotions.map((p: any) => p.duration)) +
              3,
          ),
        ),
        nonce: confirmed.promotion.id + '-' + Date.now(),
      });
      setView('Demand forecast');
      notify(
        'Promotion confirmed. Its calendar now feeds demand, inventory planning and checkout at linked OneSales stores.',
      );
    });
  }
  async function run(label: string, fn: () => Promise<void>) {
    const required = ['save', 'confirm-promotion'].includes(label)
      ? 'editor'
      : 'user';
    if (label !== 'export' && !can('promotions', required)) {
      setError(
        'Ask your retailer owner for Promotions ' + required + ' access.',
      );
      return;
    }
    setBusy(label);
    setError('');
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request failed.');
    } finally {
      setBusy('');
    }
  }
  useEffect(() => {
    const c = new AbortController();
    setReviewedPlan(null);
    setSummary(null);
    setHistory(null);
    setResult(null);
    setOptimization(null);
    setError('');
    api('/summary', workspace, undefined, c.signal)
      .then((s) => {
        setSummary(s);
        if (s.stores.length && !s.stores.some((x: any) => x.store_id === store))
          setStore(s.stores[0].store_id);
      })
      .catch((e) => {
        if (e.name !== 'AbortError')
          setError('Cannot load the workspace. ' + e.message);
      });
    return () => c.abort();
  }, [workspace, reload]);
  useEffect(() => {
    const c = new AbortController();
    setCatalog([]);
    setHistory(null);
    setResult(null);
    setOptimization(null);
    if (!summary?.model) return () => c.abort();
    api<Product[]>('/catalog?store_id=' + store, workspace, undefined, c.signal)
      .then((items) => {
        setCatalog(items);
        setPid(
          items.some((p) => p.product_id === pid)
            ? pid
            : items[0]?.product_id || '',
        );
        setSelectedProducts(items.slice(0, 8).map((p) => p.product_id));
      })
      .catch((e) => {
        if (e.name !== 'AbortError') setError(e.message);
      });
    return () => c.abort();
  }, [workspace, store, reload, summary?.model?.model_id]);
  useEffect(() => {
    if (!pid || view !== 'Promotion review') return;
    const c = new AbortController();
    setHistoryLoading(true);
    setHistory(null);
    api(
      '/hindsight?product_id=' + pid + '&store_id=' + store,
      workspace,
      undefined,
      c.signal,
    )
      .then(setHistory)
      .catch((e) => {
        if (e.name !== 'AbortError') setError(e.message);
      })
      .finally(() => {
        if (!c.signal.aborted) setHistoryLoading(false);
      });
    return () => c.abort();
  }, [pid, store, workspace, view, reload]);
  const skipResetRef = useRef(false);
  useEffect(() => {
    if (skipResetRef.current) {
      skipResetRef.current = false;
      return;
    }
    setResult(null);
    setSavedId('');
  }, [
    pid,
    discount,
    duration,
    feature,
    display,
    margin,
    cost,
    funding,
    inventory,
    startDate,
    offerConfig,
  ]);
  useEffect(() => {
    setOptimization(null);
  }, [
    budget,
    objective,
    maxPromos,
    minMargin,
    selectedProducts,
    margin,
    cost,
    funding,
    inventory,
    offerConfig,
  ]);
  useEffect(() => {
    if (view === 'Saved plans')
      api<any[]>('/scenarios', workspace)
        .then(setSaved)
        .catch((e) => setError(e.message));
  }, [view, workspace, reload]);
  function loadScenario(s: any) {
    const p = s.request.promotions[0];
    if (s.request.promotions.length > 1) {
      setReviewedPlan(s);
      return;
    }
    setStore(s.request.store_id);
    setPid(p.product_id);
    setDiscount(p.discount * 100);
    setDuration(String(p.duration));
    setFeature(p.feature);
    setDisplay(p.display);
    setOfferConfig({
      mechanics: p.mechanics || [],
      normal_basket_units: p.normal_basket_units ?? 2,
      redemption_rate: p.redemption_rate ?? null,
      stacking_policy: p.stacking_policy || 'exclusive',
    });
    setMargin(s.request.margin_rate * 100);
    setCost(p.fixed_cost);
    setFunding(p.funding_per_unit);
    setInventory(
      s.request.inventory_units ? String(s.request.inventory_units) : '',
    );
    setStartDate(s.request.start_date || '');
    setScenarioName(s.name);
    setView('Scenario studio');
    notify('Plan loaded. Run the scenario to refresh its forecast.');
  }
  function refreshSavedPlan(s: {
    request: Record<string, unknown>;
    result: { start_date: string };
  }) {
    return run('save', async () => {
      const refreshed = await api<{ id: string }>('/scenarios', workspace, {
        ...s.request,
        start_date: s.result.start_date,
      });
      const plans = await api<typeof saved>('/scenarios', workspace);
      setSaved(plans);
      setReviewedPlan(plans.find((plan) => plan.id === refreshed.id) || null);
      notify(
        'Recalculated as a new snapshot using the current model. Original plan retained.',
      );
    });
  }
  const productPicker = (
    <Picker
      label="Product"
      value={pid}
      options={catalog.map((p) => ({ value: p.product_id, label: p.name }))}
      onChange={setPid}
    />
  );
  const subtitles: Record<string, string> = {
    'Promotion review': 'Understand the lift. Account for the ripple effects.',
    'Scenario studio':
      'Explore the demand and profit behind your next promotion.',
    Recommendations:
      'Find supported, profitable offers within your business constraints.',
    'Demand forecast':
      'Anticipate demand with sales history, seasonality and your promotion calendar.',
    'Inventory planner':
      'Balance availability, stock investment and replenishment constraints.',
    'Saved plans':
      'Your decisions, assumptions and model results in one place.',
    'Modelling workbench': 'Know the evidence behind every planning decision.',
    'Data connections':
      'Connect your sources once and publish trusted data across your shop.',
    'Connected intelligence':
      'Connect your systems, understand relationships, and act on evidence.',
    OneSales:
      'From the first scan to the next forecast. Your store, connected.',
    'Sales & returns':
      'Find every transaction. Resolve every return with confidence.',
    'Business analytics':
      'See what sells, what earns, and what needs your attention.',
  };
  return (
    <section className="promotion-workspace">
      <div className="page-content">
        <fieldset disabled={!!busy} className="working-fieldset">
          <div className="title-row">
            <div>
              <div className="eyebrow">
                {view === 'Promotion review'
                  ? 'THE BIGGER PICTURE'
                  : view === 'Scenario studio'
                    ? 'LOOK AHEAD'
                    : view === 'Recommendations'
                      ? 'PLAN WITH PURPOSE'
                      : 'YOUR WORKSPACE'}
              </div>
              <h1>{view}</h1>
              <p className="subtitle">{subtitles[view]}</p>
            </div>
            {view === 'Promotion review' && (
              <Button onClick={() => setView('Scenario studio')}>
                <Plus />
                New scenario
              </Button>
            )}
            {view === 'Saved plans' && (
              <Button onClick={() => setView('Scenario studio')}>
                <Plus />
                New scenario
              </Button>
            )}
          </div>
          {error && (
            <div className="error-banner" role="alert">
              <Info size={18} />
              <span>{error}</span>
              <button aria-label="Dismiss error" onClick={() => setError('')}>
                <X size={16} />
              </button>
              <button onClick={() => setReload((x) => x + 1)}>Retry</button>
            </div>
          )}
          {retailShop?.demo &&
            ![
              'Data connections',
              'Connected intelligence',
              'Business analytics',
              'OneSales',
              'Sales & returns',
            ].includes(view) && (
              <p className="dp-demo">
                Demo workspace: trading history and inventory are simulated.
                Model results demonstrate the workflow and do not establish
                accuracy or ROI for a real retailer.
              </p>
            )}
          {view !== 'Modelling workbench' &&
            view !== 'Connected intelligence' &&
            view !== 'Data connections' &&
            !['OneSales', 'Sales & returns', 'Business analytics'].includes(
              view,
            ) &&
            view !== 'Saved plans' &&
            view !== 'Demand forecast' &&
            view !== 'Inventory planner' && (
              <div className="filterbar">
                <div className="filter-icon">
                  <SlidersHorizontal size={17} />
                </div>
                <Picker
                  label="Store scope"
                  value={store}
                  options={[
                    { value: 'all', label: 'All stores' },
                    ...(summary?.stores || []).map((s: any) => ({
                      value: s.store_id,
                      label: s.name + ' · ' + s.store_id,
                    })),
                  ]}
                  onChange={setStore}
                />
                {view === 'Promotion review' && productPicker}
                <div className="period">
                  <CalendarDays size={16} />
                  <span>
                    {view === 'Promotion review' && lastFold
                      ? shortDate(lastFold.test_start) +
                        ' – ' +
                        shortDate(lastFold.test_end) +
                        ' · ' +
                        lastFold.test_end.slice(0, 4)
                      : 'Planning from ' +
                        (summary?.quality?.end || 'latest history')}
                  </span>
                </div>
              </div>
            )}
          {!summary &&
            !error &&
            view !== 'Connected intelligence' &&
            view !== 'Data connections' &&
            !['OneSales', 'Sales & returns', 'Business analytics'].includes(
              view,
            ) && <Busy text="Connecting to the model…" />}
          {retailShop &&
            summary &&
            !summary.model &&
            ['Promotion review', 'Scenario studio', 'Recommendations'].includes(
              view,
            ) && (
              <section className="panel">
                <h2>This shop needs an active promotion model</h2>
                <p>
                  Validate its sales history, train a candidate and review the
                  results in Modelling workbench.
                </p>
                <Button onClick={() => setView('Modelling workbench')}>
                  Open Modelling workbench
                </Button>
              </section>
            )}
          {view === 'Promotion review' && (
            <>
              {historyLoading && (
                <Busy text="Predicting the no-promotion baseline with ML…" />
              )}
              {history && (
                <>
                  <div className="metric-grid">
                    <Metric
                      label="Actual sales"
                      value={number(history.actual)}
                      sub="Units · full evaluation window"
                      icon={<Layers size={17} />}
                    />
                    <Metric
                      label="ML-predicted baseline"
                      value={number(history.baseline)}
                      sub="Predicted by the trained baseline model"
                      icon={<History size={17} />}
                    />
                    <Metric
                      label="Incremental units"
                      value={
                        (history.incremental > 0 ? '+' : '') +
                        number(history.incremental)
                      }
                      sub={
                        percent(history.incremental / history.baseline) +
                        ' vs. ML baseline'
                      }
                      accent
                      icon={<TrendingUp size={17} />}
                    />
                    <Metric
                      label="Incremental revenue"
                      value={money(history.incremental_revenue)}
                      sub="Actual sales value − ML baseline value"
                      icon={<TrendingUp size={17} />}
                    />
                  </div>
                  <section
                    className="baseline-equation"
                    aria-label="Incremental sales calculation"
                  >
                    <div>
                      <span>ACTUAL SOLD</span>
                      <strong>{number(history.actual)} units</strong>
                      <small>
                        {money(history.actual_revenue)} actual sales value
                      </small>
                    </div>
                    <span className="math-symbol">−</span>
                    <div>
                      <span>ML BASELINE</span>
                      <strong>{number(history.baseline)} units</strong>
                      <small>
                        {money(history.baseline_revenue)} at regular price
                      </small>
                    </div>
                    <span className="math-symbol">=</span>
                    <div>
                      <span>INCREMENTAL SALES</span>
                      <strong>{number(history.incremental)} units</strong>
                      <small>
                        {money(history.incremental_revenue)} revenue change
                      </small>
                    </div>
                    <span className="pill green">
                      <Activity size={13} />
                      Trained ML baseline
                    </span>
                  </section>
                  <div className="analysis-grid">
                    <section className="panel chart-panel">
                      <div className="section-title">
                        <div>
                          <h2>What happened to demand?</h2>
                          <p className="panel-subtitle">{history.name}</p>
                        </div>
                        <span className="pill">Weekly sales</span>
                      </div>
                      <div className="chart-legend">
                        <span>
                          <i style={{ background: colors.actual }} />
                          Actual sales
                        </span>
                        <span>
                          <i className="dashed" />
                          ML-predicted baseline
                        </span>
                        <span>
                          <i style={{ background: '#efdcac' }} />
                          Promotion week
                        </span>
                      </div>
                      <DemandChart data={history.series} />
                      <div className="chart-footnote">
                        <ShieldCheck size={15} />
                        These weeks were held out from model training.{' '}
                        {history.baseline_validation?.limited_support &&
                          'Only ' +
                            history.baseline_validation.rows +
                            ' clean non-promotion observations are available for this scope; product-level baseline validation is limited.'}
                      </div>
                    </section>
                    <section className="panel">
                      <div className="section-title">
                        <h2>Beyond the promoted product</h2>
                        <span className="unit-label">UNITS</span>
                      </div>
                      <Effects effects={history.effects} />
                      <div className="effect-total">
                        <span>Net portfolio effect</span>
                        <strong>
                          {history.portfolio_net_units > 0 ? '+' : ''}
                          {number(history.portfolio_net_units)}
                        </strong>
                      </div>
                    </section>
                  </div>
                  <div className="section-title below-title">
                    <div>
                      <h2>Promotion episodes</h2>
                      <p className="panel-subtitle">
                        Consecutive promoted weeks within the evaluation window
                      </p>
                    </div>
                    <span className="pill">
                      {history.campaigns.length} episodes
                    </span>
                  </div>
                  <div className="panel table-panel">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Promotion window</TableHead>
                          <TableHead>Duration</TableHead>
                          <TableHead>Avg. discount</TableHead>
                          <TableHead className="numeric">
                            Actual units
                          </TableHead>
                          <TableHead className="numeric">
                            Baseline units
                          </TableHead>
                          <TableHead className="numeric">
                            Incremental units
                          </TableHead>
                          <TableHead />
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {history.campaigns.map((c: any) => (
                          <TableRow key={c.start}>
                            <TableCell>
                              <div className="episode-cell">
                                <span className="episode-icon">
                                  <CalendarDays size={16} />
                                </span>
                                <strong>
                                  {shortDate(c.start)} – {shortDate(c.end)}
                                </strong>
                              </div>
                            </TableCell>
                            <TableCell>
                              {c.weeks} {c.weeks === 1 ? 'week' : 'weeks'}
                            </TableCell>
                            <TableCell>
                              <span className="discount-chip">
                                {percent(c.discount, 0)} off
                              </span>
                            </TableCell>
                            <TableCell className="numeric">
                              {number(c.actual)}
                            </TableCell>
                            <TableCell className="numeric muted">
                              {number(c.baseline)}
                            </TableCell>
                            <TableCell
                              className={
                                'numeric ' +
                                (c.incremental >= 0 ? 'positive' : 'negative')
                              }
                            >
                              {c.incremental > 0 ? '+' : ''}
                              {number(c.incremental)}
                            </TableCell>
                            <TableCell>
                              <Button
                                variant="ghost"
                                aria-label={'Plan a promotion like ' + c.start}
                                onClick={() => {
                                  setDiscount(Math.round(c.discount * 100));
                                  setDuration(String(Math.min(4, c.weeks)));
                                  setView('Scenario studio');
                                }}
                              >
                                <ArrowUpRight />
                              </Button>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                    {!history.campaigns.length && (
                      <Empty
                        title="No promotion episodes"
                        text="This product had no promoted weeks in the held-out window. Select another product to examine its promotions."
                      />
                    )}
                  </div>
                  <div className="insight-strip">
                    <Info size={18} />
                    <div>
                      <strong>Keep observed and modeled lift separate.</strong>
                      <p>
                        Actual sales minus baseline is{' '}
                        {number(history.incremental)} units. The model
                        attributes {number(history.modeled_incremental)} units
                        to this product’s promotions and carryover;{' '}
                        {number(history.unexplained)} units remain unexplained.{' '}
                        {history.evidence}
                      </p>
                    </div>
                  </div>
                </>
              )}
            </>
          )}
          {view === 'Scenario studio' && (!retailShop || summary?.model) && (
            <div className="studio-grid">
              <section className="panel scenario-controls">
                <div className="section-title">
                  <h2>Build your promotion</h2>
                  <span className="step-chip">01</span>
                </div>
                <details
                  className="assumptions"
                  style={{
                    marginBottom: 14,
                    border: '1px solid #cce3d8',
                    background: '#f7fbf9',
                    borderRadius: 10,
                    padding: '10px 12px',
                  }}
                >
                  <summary style={{ fontWeight: 650, color: '#19694f' }}>
                    <Sparkles size={14} /> Gemini Vision: Competitor Flyer &
                    Co-Funding Scanner
                  </summary>
                  <div
                    style={{
                      display: 'flex',
                      flexDirection: 'column',
                      gap: 8,
                      marginTop: 10,
                    }}
                  >
                    <p className="micro" style={{ margin: 0 }}>
                      Upload a rival circular photo or paste flyer claims.
                      Gemini maps threats to your catalog and computes the exact{' '}
                      <strong>break-even supplier co-funding</strong> needed to
                      defend volume profitably.
                    </p>
                    <Input
                      value={flyerText}
                      onChange={(e) => setFlyerText(e.target.value)}
                      placeholder="Competitor flyer claims or category discounts…"
                    />
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                      <span style={{ fontSize: 11, color: '#567064' }}>1-Click Demo Scan:</span>
                      <button
                        type="button"
                        disabled={!!busy}
                        style={{ fontSize: 11.5, background: '#fff', border: '1px solid #cce3d8', borderRadius: 6, padding: '4px 9px', cursor: 'pointer', color: '#19694f', fontWeight: 600 }}
                        onClick={() => {
                          const text = 'Metro Supermart Weekend Mega Blitz: 25% OFF National Brand Pretzels & Snacks, Buy 2 Get 1 Honey Oat Cereal, 20% OFF Chilled Dairy & Artisan Cheese';
                          setFlyerText(text);
                          setFlyerImageName('');
                          setFlyerImageBase64(null);
                          setFlyerScan(null);
                          void run('flyer-scan', async () => {
                            setFlyerScan(
                              await api('/promotions/flyer-scan', workspace, {
                                store_id: store,
                                competitor_text: text,
                                image_base64: null,
                                image_mime_type: 'image/jpeg',
                                margin_rate: margin / 100,
                              }),
                            );
                          });
                        }}
                      >
                        🏷️ Weekend Blitz Circular
                      </button>
                      <button
                        type="button"
                        disabled={!!busy}
                        style={{ fontSize: 11.5, background: '#fff', border: '1px solid #cce3d8', borderRadius: 6, padding: '4px 9px', cursor: 'pointer', color: '#19694f', fontWeight: 600 }}
                        onClick={() => {
                          const text = 'Festive Mega Savings Mela: Flat 30% OFF Premium Cooking Oils & Ghee, ₹45/L Toned Milk special rate, Buy 1 Get 1 on Roasted Dry Fruits & Namkeen';
                          setFlyerText(text);
                          setFlyerImageName('');
                          setFlyerImageBase64(null);
                          setFlyerScan(null);
                          void run('flyer-scan', async () => {
                            setFlyerScan(
                              await api('/promotions/flyer-scan', workspace, {
                                store_id: store,
                                competitor_text: text,
                                image_base64: null,
                                image_mime_type: 'image/jpeg',
                                margin_rate: margin / 100,
                              }),
                            );
                          });
                        }}
                      >
                        🎉 Festive Savings Mela
                      </button>
                    </div>
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                        flexWrap: 'wrap',
                      }}
                    >
                      <label
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 6,
                          padding: '6px 10px',
                          border: '1px dashed #9ac8b8',
                          borderRadius: 6,
                          background: '#fff',
                          fontSize: 12,
                          cursor: 'pointer',
                          color: '#19694f',
                          fontWeight: 600,
                        }}
                      >
                        {flyerImageName
                          ? `Flyer: ${flyerImageName}`
                          : 'Upload Flyer Image…'}
                        <input
                          type="file"
                          accept="image/*"
                          style={{ display: 'none' }}
                          onChange={(e) => {
                            const f = e.target.files?.[0];
                            if (!f) return;
                            setFlyerText('');
                            setFlyerImageBase64(null);
                            setFlyerScan(null);
                            setFlyerImageName(f.name);
                            setFlyerImageMime(f.type || 'image/jpeg');
                            const r = new FileReader();
                            r.onload = () => {
                              const s = String(r.result || '');
                              setFlyerImageBase64(
                                s.includes(',') ? s.split(',')[1] : s,
                              );
                            };
                            r.readAsDataURL(f);
                          }}
                        />
                      </label>
                      <Button
                        size="sm"
                        disabled={!!busy || !canRun}
                        onClick={() =>
                          run('flyer-scan', async () => {
                            setFlyerScan(
                              await api('/promotions/flyer-scan', workspace, {
                                store_id: store,
                                competitor_text: flyerText,
                                image_base64: flyerImageBase64,
                                image_mime_type: flyerImageMime,
                                margin_rate: margin / 100,
                              }),
                            );
                          })
                        }
                      >
                        {busy === 'flyer-scan' ? (
                          <LoaderCircle className="spin" />
                        ) : (
                          <Sparkles size={14} />
                        )}
                        Scan & Compute Counter-Offer
                      </Button>
                    </div>
                    {flyerScan && (
                      <div
                        style={{
                          display: 'flex',
                          flexDirection: 'column',
                          gap: 6,
                          marginTop: 4,
                        }}
                      >
                        <small style={{ color: '#567064' }}>
                          {flyerScan.ai_powered
                            ? `Extracted with ${flyerScan.model} · Review before approval`
                            : 'Extraction unavailable · No verified flyer offers'}
                        </small>
                        <small style={{ color: '#20352d' }}>
                          {flyerScan.competitor_summary}
                        </small>
                        {flyerScan.counter_offers?.length > 0 && (
                          <small style={{ color: '#567064' }}>
                            Forecasts are estimates. Co-funding is a proposed supplier
                            allowance and requires approval.
                          </small>
                        )}
                        {flyerScan.candidate_issues?.map((issue: any) => (
                          <small key={issue.product_id} style={{ color: '#855a21' }}>
                            {issue.product_id}: {issue.reason}
                          </small>
                        ))}
                        {flyerScan.counter_offers?.map((co: any) => (
                          <div
                            key={co.product_id}
                            style={{
                              background: '#fff',
                              border: '1px solid #dce8e2',
                              borderRadius: 8,
                              padding: '8px 10px',
                              fontSize: 12.5,
                            }}
                          >
                            <div
                              style={{
                                display: 'flex',
                                justifyContent: 'space-between',
                                gap: 6,
                              }}
                            >
                              <strong>
                                {co.product_name} ({co.recommended_discount_pct}
                                % TPR · {co.recommended_duration_weeks}w)
                              </strong>
                              <span className="pill green">
                                Co-fund {money(co.recommended_funding_per_unit)}
                                /u
                              </span>
                            </div>
                            <p
                              style={{
                                margin: '4px 0 8px',
                                fontSize: 12,
                                color: '#567064',
                              }}
                            >
                              {co.strategic_rationale} · Unfunded:{' '}
                              {money(co.unfunded_planning_profit)} → Co-Funded:{' '}
                              <strong>
                                {money(co.funded_planning_profit)}
                              </strong>
                            </p>
                            {co.forecast_warnings?.map((warning: string) => (
                              <p key={warning} style={{ margin: '4px 0', color: '#855a21' }}>
                                {warning}
                              </p>
                            ))}
                            <Button
                              size="sm"
                              disabled={!!busy}
                              onClick={() => {
                                const nextName = `Co-Funded Counter · ${co.product_name}`;
                                skipResetRef.current = true;
                                setPid(co.product_id);
                                setDiscount(co.recommended_discount_pct);
                                setDuration(
                                  String(co.recommended_duration_weeks),
                                );
                                setFeature(true);
                                setDisplay(true);
                                setFunding(co.recommended_funding_per_unit);
                                setScenarioName(nextName);
                                const simReq = {
                                  store_id: store,
                                  start_date: startDate || null,
                                  promotions: [
                                    {
                                      product_id: co.product_id,
                                      discount: co.recommended_discount_pct / 100,
                                      duration: Number(co.recommended_duration_weeks),
                                      feature: true,
                                      display: true,
                                      fixed_cost: cost,
                                      funding_per_unit: co.recommended_funding_per_unit,
                                      ...offerConfig,
                                    },
                                  ],
                                  margin_rate: margin / 100,
                                  inventory_units: inventory ? Number(inventory) : null,
                                  name: nextName,
                                };
                                void run('forecast', async () => {
                                  const fRes: any = await api('/forecast', workspace, simReq);
                                  setResult(fRes);
                                  setSavedId('');
                                  const kRes = await api('/promotions/campaign-kit', workspace, {
                                    store_id: store,
                                    product_id: co.product_id,
                                    product_name: co.product_name,
                                    category: co.category || '',
                                    currency: summary?.quality?.currency || 'USD',
                                    base_price: co.base_price || 0,
                                    discount_pct: co.recommended_discount_pct,
                                    duration_weeks: Number(co.recommended_duration_weeks),
                                    funding_per_unit: co.recommended_funding_per_unit,
                                    total_units: fRes.total_units || 0,
                                    baseline_units: fRes.baseline_units || 0,
                                    incremental_units: fRes.incremental_units || 0,
                                    planning_profit: fRes.planning_profit || 0,
                                    planning_roi: fRes.planning_roi,
                                    investment: fRes.investment || 0,
                                  });
                                  setCampaignKit(kRes);
                                  notify(
                                    'Simulated counter-offer and generated Supplier Pitch + Multilingual Campaign Kit!',
                                  );
                                });
                              }}
                            >
                              <Sparkles size={13} /> Apply, Simulate & Generate Campaign Kit
                            </Button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </details>
                {productPicker}
                {chosen && (
                  <div className="product-context">
                    <span>{chosen.category}</span>
                    <strong>
                      {money(chosen.base_price)}{' '}
                      <small>average regular price</small>
                    </strong>
                  </div>
                )}
                <div className="discount-control">
                  <div>
                    <label>Unconditional TPR</label>
                    <strong>{discount}%</strong>
                  </div>
                  <Slider
                    aria-label="Price discount"
                    min={0}
                    max={50}
                    step={1}
                    value={[discount]}
                    onValueChange={(v) =>
                      setDiscount(Array.isArray(v) ? v[0] : v)
                    }
                  />
                  <div className="range-labels">
                    <span>0%</span>
                    <span>50%</span>
                  </div>
                </div>
                <OfferEditor
                  value={offerConfig}
                  onChange={setOfferConfig}
                  products={catalog}
                  productId={pid}
                  discount={discount / 100}
                />
                <div className="two-cols">
                  <Picker
                    label="Duration"
                    value={duration}
                    options={[1, 2, 3, 4].map((v) => ({
                      value: String(v),
                      label: v + ' ' + (v === 1 ? 'week' : 'weeks'),
                    }))}
                    onChange={setDuration}
                  />
                  <div className="field">
                    <label>Start week</label>
                    <Input
                      aria-label="Start week"
                      type="date"
                      value={startDate}
                      placeholder="Next benchmark week"
                      onChange={(e) => setStartDate(e.target.value)}
                    />
                    <small>
                      {startDate
                        ? 'Must match the weekly cadence'
                        : 'Blank = first week after history'}
                    </small>
                  </div>
                </div>
                <div className="switch-row">
                  <div>
                    <strong>Featured offer</strong>
                    <span>Feature / mailer support</span>
                  </div>
                  <Switch
                    aria-label="Featured offer"
                    checked={feature}
                    onCheckedChange={setFeature}
                  />
                </div>
                <div className="switch-row">
                  <div>
                    <strong>In-store display</strong>
                    <span>Secondary display support</span>
                  </div>
                  <Switch
                    aria-label="In-store display"
                    checked={display}
                    onCheckedChange={setDisplay}
                  />
                </div>
                <details className="assumptions" open>
                  <summary>
                    Economics & availability <Info size={14} />
                  </summary>
                  <div className="two-cols">
                    <Numeric
                      label="Base margin (%)"
                      value={margin}
                      min={1}
                      max={95}
                      onChange={(v) => setMargin(Number(v))}
                    />
                    <Numeric
                      label="Fixed offer cost (workspace currency)"
                      value={cost}
                      onChange={(v) => setCost(Number(v))}
                    />
                    <Numeric
                      label="Funding ($ / unit)"
                      value={funding}
                      step={0.05}
                      onChange={(v) => setFunding(Number(v))}
                    />
                    <Numeric
                      label="Available offer units"
                      value={inventory}
                      min={1}
                      onChange={setInventory}
                      help="Blank = inventory not checked"
                    />
                  </div>
                  <p className="micro">
                    Margin supplies an estimated unit cost when actual costs are
                    absent. Funding applies to promoted units only.
                  </p>
                </details>
                <Button
                  className="wide-button"
                  disabled={!!busy || !pid || !canRun}
                  onClick={() =>
                    run('forecast', async () => {
                      setResult(await api('/forecast', workspace, request));
                      setSavedId('');
                    })
                  }
                >
                  {busy === 'forecast' ? (
                    <LoaderCircle className="spin" />
                  ) : (
                    <Play size={16} />
                  )}
                  Run scenario
                  <ArrowRight />
                </Button>
              </section>
              <div className="scenario-output">
                {busy === 'forecast' ? (
                  <div className="panel">
                    <Busy text="Simulating promotion, spillovers and four weeks of carryover…" />
                  </div>
                ) : result ? (
                  <>
                    <div className="metric-grid three">
                      <Metric
                        label="Forecast offer units"
                        value={number(result.total_units)}
                        sub={
                          percent(result.lift) +
                          ' vs. calendar without this proposal'
                        }
                      />
                      <Metric
                        label="Planning incremental profit"
                        value={money(result.planning_profit)}
                        sub="Post-offer losses retained; positive credit excluded"
                        accent
                      />
                      <Metric
                        label="Planning ROI"
                        value={
                          result.planning_roi === null
                            ? 'No spend'
                            : percent(result.planning_roi)
                        }
                        sub={
                          money(result.investment) +
                          ' gross promotion investment'
                        }
                      />
                    </div>
                    <PlanningNote result={result} />
                    <section
                      className="panel"
                      style={{
                        border: '1px solid #cce3d8',
                        background: 'linear-gradient(180deg, #f7fbf9, #ffffff)',
                      }}
                    >
                      <div className="section-title">
                        <div>
                          <h2>
                            <Sparkles
                              size={18}
                              style={{
                                display: 'inline',
                                marginRight: 6,
                                color: '#19694f',
                              }}
                            />
                            Gemini Omnichannel Campaign Studio & Supplier
                            Co-Funding Pitch
                          </h2>
                          <p className="panel-subtitle">
                            Data-backed vendor trade-allowance brief,
                            multilingual WhatsApp customer broadcasts (English,
                            Hindi, Kannada), and printable in-store shelf tags
                          </p>
                        </div>
                        <Button
                          disabled={!!busy || !canRun}
                          onClick={() =>
                            run('campaign-kit', async () => {
                              setCampaignKit(
                                await api(
                                  '/promotions/campaign-kit',
                                  workspace,
                                  {
                                    store_id: store,
                                    product_id: pid,
                                    product_name: chosen?.name || pid,
                                    category: chosen?.category || '',
                                    currency:
                                      summary?.quality?.currency || 'USD',
                                    base_price: chosen?.base_price || 0,
                                    discount_pct: discount,
                                    duration_weeks: Number(duration),
                                    funding_per_unit: funding,
                                    total_units: result.total_units || 0,
                                    baseline_units: result.baseline_units || 0,
                                    incremental_units:
                                      result.incremental_units || 0,
                                    planning_profit:
                                      result.planning_profit || 0,
                                    planning_roi: result.planning_roi,
                                    investment: result.investment || 0,
                                  },
                                ),
                              );
                            })
                          }
                        >
                          {busy === 'campaign-kit' ? (
                            <LoaderCircle className="spin" />
                          ) : (
                            <Sparkles size={16} />
                          )}
                          {campaignKit
                            ? 'Refresh Campaign Kit'
                            : 'Generate Campaign Kit & Vendor Pitch'}
                        </Button>
                      </div>
                      {campaignKit && (
                        <div
                          style={{
                            display: 'grid',
                            gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))',
                            gap: 12,
                            marginTop: 12,
                          }}
                        >
                          <div
                            style={{
                              padding: 12,
                              background: '#fff',
                              border: '1px solid #dce8e2',
                              borderRadius: 10,
                              display: 'flex',
                              flexDirection: 'column',
                              justifyContent: 'space-between',
                            }}
                          >
                            <div>
                              <div
                                style={{
                                  display: 'flex',
                                  justifyContent: 'space-between',
                                  alignItems: 'center',
                                  gap: 8,
                                  marginBottom: 6,
                                  flexWrap: 'wrap',
                                }}
                              >
                                <strong style={{ fontSize: 13.5 }}>
                                  {campaignKit.supplier_subject}
                                </strong>
                                <span className="pill green">
                                  Ask:{' '}
                                  {money(
                                    campaignKit.recommended_funding_per_unit,
                                  )}
                                  /unit (
                                  {money(campaignKit.estimated_total_allowance)}{' '}
                                  total)
                                </span>
                              </div>
                              <p
                                style={{
                                  fontSize: 13,
                                  lineHeight: 1.55,
                                  margin: '6px 0 12px',
                                  color: '#20352d',
                                }}
                              >
                                {campaignKit.supplier_pitch}
                              </p>
                            </div>
                            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => {
                                  const nextFund = Number(
                                    campaignKit.recommended_funding_per_unit || 0,
                                  );
                                  skipResetRef.current = true;
                                  setFunding(nextFund);
                                  void run('forecast', async () => {
                                    const updatedReq = {
                                      ...request,
                                      promotions: request.promotions.map(
                                        (pr: any) => ({
                                          ...pr,
                                          funding_per_unit: nextFund,
                                        }),
                                      ),
                                    };
                                    setResult(
                                      await api(
                                        '/forecast',
                                        workspace,
                                        updatedReq,
                                      ),
                                    );
                                    notify(
                                      'Applied supplier co-funding and refreshed forecast profit & ROI!',
                                    );
                                  });
                                }}
                              >
                                Apply Co-Funding (
                                {money(
                                  campaignKit.recommended_funding_per_unit,
                                )}
                                /u)
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => {
                                  navigator.clipboard?.writeText(
                                    `${campaignKit.supplier_subject}\n\n${campaignKit.supplier_pitch}`,
                                  );
                                  notify('Copied Supplier Negotiation Pitch to clipboard.');
                                }}
                              >
                                Copy Vendor Pitch
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => {
                                  const w = window.open(
                                    'about:blank',
                                    '_blank',
                                  );
                                  if (w && campaignKit.shelf_talker?.html) {
                                    w.document.write(
                                      campaignKit.shelf_talker.html,
                                    );
                                    w.document.close();
                                  }
                                }}
                              >
                                Print Shelf Tag
                              </Button>
                            </div>
                          </div>

                          <div
                            style={{
                              padding: 12,
                              background: '#fff',
                              border: '1px solid #dce8e2',
                              borderRadius: 10,
                              display: 'flex',
                              flexDirection: 'column',
                              justifyContent: 'space-between',
                            }}
                          >
                            <div>
                              <div
                                style={{
                                  display: 'flex',
                                  justifyContent: 'space-between',
                                  alignItems: 'center',
                                  gap: 8,
                                  marginBottom: 8,
                                  flexWrap: 'wrap',
                                }}
                              >
                                <strong style={{ fontSize: 13.5 }}>
                                  Multilingual WhatsApp Broadcast
                                </strong>
                                <div style={{ display: 'flex', gap: 5 }}>
                                  {(
                                    [
                                      ['english', 'English'],
                                      ['hindi', 'हिन्दी'],
                                      ['kannada', 'ಕನ್ನಡ'],
                                    ] as const
                                  ).map(([k, label]) => (
                                    <Button
                                      key={k}
                                      size="sm"
                                      variant={
                                        kitLang === k ? 'default' : 'outline'
                                      }
                                      onClick={() => setKitLang(k)}
                                    >
                                      {label}
                                    </Button>
                                  ))}
                                </div>
                              </div>
                              <p
                                style={{
                                  padding: 10,
                                  background: '#f2f8f5',
                                  borderRadius: 8,
                                  fontSize: 13,
                                  lineHeight: 1.55,
                                  margin: '0 0 10px',
                                }}
                              >
                                {campaignKit.whatsapp?.[kitLang]}
                              </p>
                            </div>
                            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => {
                                  navigator.clipboard?.writeText(
                                    campaignKit.whatsapp?.[kitLang] || '',
                                  );
                                  notify(`Copied ${kitLang.toUpperCase()} WhatsApp broadcast to clipboard.`);
                                }}
                              >
                                Copy WhatsApp Message
                              </Button>
                            </div>
                          </div>
                        </div>
                      )}
                    </section>
                    <OfferEvidence result={result} />
                    <section className="panel">
                      <div className="section-title">
                        <div>
                          <h2>Demand during and after the offer</h2>
                          <p className="panel-subtitle">
                            Promoted products · 4 weeks of post-offer carryover
                          </p>
                        </div>
                        <span
                          className={
                            'pill ' + (result.feasible ? 'green' : 'amber')
                          }
                        >
                          {result.feasible
                            ? 'Inventory check passed or not supplied'
                            : 'Inventory shortfall'}
                        </span>
                      </div>
                      <div className="chart-legend">
                        <span>
                          <i style={{ background: colors.actual }} />
                          Forecast
                        </span>
                        <span>
                          <i className="dashed" />
                          Baseline
                        </span>
                        <span>
                          <i style={{ background: '#dae9e3' }} />
                          Prediction range
                        </span>
                      </div>
                      <DemandChart data={result.weeks} forecast />
                      <p className="micro">
                        90% marginal prediction bands. Portfolio aggregation is
                        conservative; these are demand bands, not confidence
                        intervals for causal lift.
                      </p>
                    </section>
                    <div className="two-cols results-breakdown">
                      <section className="panel">
                        <div className="section-title">
                          <h2>Where profit changes</h2>
                          <span className="unit-label">
                            {summary?.quality?.currency || 'USD'}
                          </span>
                        </div>
                        <Effects
                          effects={result.planning_effect_profit}
                          currency={result.currency}
                          profit
                        />
                      </section>
                      <section className="panel">
                        <div className="section-title">
                          <h2>The commercial picture</h2>
                          <TrendingUp size={18} />
                        </div>
                        <div className="financial-row">
                          <span>Baseline offer units</span>
                          <strong>{number(result.baseline_units)}</strong>
                        </div>
                        <div className="financial-row">
                          <span>Incremental offer units</span>
                          <strong>{number(result.incremental_units)}</strong>
                        </div>
                        <div className="financial-row">
                          <span>Offer revenue</span>
                          <strong>{money(result.total_revenue)}</strong>
                        </div>
                        <div className="financial-row">
                          <span>Raw model revenue change</span>
                          <strong>{money(result.incremental_revenue)}</strong>
                        </div>
                        <div className="financial-row">
                          <span>Incremental supplier funding</span>
                          <strong>
                            {money(
                              result.incremental_supplier_funding ??
                                result.supplier_funding,
                            )}
                          </strong>
                        </div>
                        <div className="financial-row">
                          <span>Portfolio unit change</span>
                          <strong>{number(result.portfolio_net_units)}</strong>
                        </div>
                        <div className="micro formula">
                          ROI = incremental contribution profit ÷ (markdown
                          value + execution costs). Discounting is counted once
                          in profit.
                        </div>
                      </section>
                    </div>
                    <section className="panel">
                      <h2>Confirm or save this decision</h2>
                      <p className="panel-subtitle">
                        Confirm to schedule this offer and automatically include
                        it in demand and inventory forecasts. Saving keeps a
                        draft for review.
                      </p>
                      <div className="save-row">
                        <Input
                          aria-label="Scenario name"
                          value={scenarioName}
                          maxLength={120}
                          onChange={(e) => setScenarioName(e.target.value)}
                        />
                        <Button
                          disabled={!!busy || !scenarioName.trim() || !canSave}
                          onClick={() => confirmPromotion(request, result)}
                        >
                          <CheckCircle2 /> Confirm promotion
                        </Button>
                        <Button
                          disabled={!!busy || !scenarioName.trim() || !canSave}
                          onClick={() =>
                            run('save', async () => {
                              const s = await api(
                                '/scenarios',
                                workspace,
                                request,
                              );
                              setSavedId(s.id);
                              notify(
                                'Scenario saved with its assumptions and model version.',
                              );
                            })
                          }
                        >
                          <Save />
                          {busy === 'save' ? 'Saving…' : 'Save plan'}
                        </Button>
                        {savedId && (
                          <Button
                            variant="outline"
                            onClick={() =>
                              run('export', () =>
                                download(
                                  '/scenarios/' + savedId + '/export',
                                  'aislemint-forecast.csv',
                                  workspace,
                                ),
                              )
                            }
                          >
                            <Download />
                            Export
                          </Button>
                        )}
                      </div>
                    </section>
                    <Warnings items={result.warnings} />
                  </>
                ) : (
                  <section className="panel forecast-empty">
                    <div className="forecast-preview-icon">
                      <ChartNoAxesCombined size={38} />
                    </div>
                    <span className="eyebrow">
                      A CLEARER VIEW OF WHAT’S NEXT
                    </span>
                    <h2>Every offer has a bigger picture.</h2>
                    <p>
                      Set up a promotion to forecast sales, contribution profit
                      and the effects across your assortment.
                    </p>
                    <div className="forecast-capabilities">
                      <span>
                        <CheckCircle2 />
                        No-promotion baseline
                      </span>
                      <span>
                        <CheckCircle2 />
                        Cannibalization & halo
                      </span>
                      <span>
                        <CheckCircle2 />
                        Post-promotion demand
                      </span>
                      <span>
                        <CheckCircle2 />
                        Budget & inventory
                      </span>
                    </div>
                    <div className="forecast-note">
                      <Info size={16} />
                      The benchmark timeline ends {summary?.quality?.end}.
                      Scenario dates are relative to that history.
                    </div>
                  </section>
                )}
              </div>
            </div>
          )}
          {view === 'Recommendations' && (!retailShop || summary?.model) && (
            <>
              <div className="panel optimize-controls">
                <div className="section-title">
                  <h2>Define a good outcome</h2>
                  <span className="pill green">
                    No-promotion alternative included
                  </span>
                </div>
                <div className="optimization-fields">
                  <Picker
                    label="Planning objective"
                    value={objective}
                    options={[
                      {
                        value: 'profit',
                        label: 'Planning incremental profit',
                      },
                      {
                        value: 'revenue',
                        label: 'Planning revenue · positive profit only',
                      },
                      { value: 'roi', label: 'Planning ROI ranking' },
                    ]}
                    onChange={setObjective}
                  />
                  <Numeric
                    label="Promotion budget (workspace currency)"
                    value={budget}
                    onChange={(v) => setBudget(Number(v))}
                  />
                  <Picker
                    label="Max. concurrent offers"
                    value={maxPromos}
                    options={[1, 2, 3, 4, 5, 6].map((x) => ({
                      value: String(x),
                      label: String(x),
                    }))}
                    onChange={setMaxPromos}
                  />
                  <Numeric
                    label="Base margin (%)"
                    value={margin}
                    min={1}
                    max={95}
                    onChange={(v) => setMargin(Number(v))}
                  />
                  <Numeric
                    label="Minimum offer margin (%)"
                    value={minMargin}
                    max={90}
                    onChange={(v) => setMinMargin(Number(v))}
                  />
                  <Numeric
                    label="Available offer units"
                    value={inventory}
                    min={1}
                    onChange={setInventory}
                  />
                </div>
                <details className="product-selection">
                  <summary>
                    {selectedProducts.length} products in the search · choose up
                    to 12
                    <ChevronRight size={15} />
                  </summary>
                  <div className="checkbox-grid">
                    {catalog.map((p) => (
                      <label key={p.product_id}>
                        <Checkbox
                          checked={selectedProducts.includes(p.product_id)}
                          disabled={
                            !selectedProducts.includes(p.product_id) &&
                            selectedProducts.length >= 12
                          }
                          onCheckedChange={(v) =>
                            setSelectedProducts((s) =>
                              v
                                ? [...s, p.product_id]
                                : s.filter((x) => x !== p.product_id),
                            )
                          }
                        />
                        <span>{p.name}</span>
                      </label>
                    ))}
                  </div>
                </details>
                <details className="assumptions">
                  <summary>Conditional offer template & overlap policy</summary>
                  <p className="micro">
                    Apply this template to each qualifying product in the
                    search. Reward products are excluded as qualifiers. Results
                    using synthetic redemption remain exploratory.
                  </p>
                  <OfferEditor
                    value={offerConfig}
                    onChange={setOfferConfig}
                    products={catalog}
                  />
                </details>
                <div className="optimizer-footer">
                  <span>
                    <Info size={15} />
                    Searches{' '}
                    {offerConfig.mechanics.length
                      ? '0–30% TPR with your conditional template'
                      : '10–30% TPR'}
                    , 1–2 weeks, without display or feature.
                  </span>
                  <Button
                    disabled={!!busy || !selectedProducts.length || !canRun}
                    onClick={() =>
                      run('optimize', async () => {
                        setOptimization(
                          await api('/optimize', workspace, {
                            store_id: store,
                            product_ids: selectedProducts,
                            budget,
                            margin_rate: margin / 100,
                            min_margin: minMargin / 100,
                            max_promotions: Number(maxPromos),
                            objective,
                            ...offerConfig,
                            inventory_units: inventory
                              ? Number(inventory)
                              : null,
                            fixed_cost: cost,
                            funding_per_unit: funding,
                          }),
                        );
                      })
                    }
                  >
                    {busy === 'optimize' ? (
                      <LoaderCircle className="spin" />
                    ) : (
                      <Sparkles />
                    )}
                    {busy === 'optimize'
                      ? 'Evaluating offers…'
                      : 'Find the best plan'}
                    <ArrowRight />
                  </Button>
                </div>
                <details className="extra-economics">
                  <summary>Offer costs & supplier funding</summary>
                  <div className="two-cols">
                    <Numeric
                      label="Fixed cost per offer (workspace currency)"
                      value={cost}
                      onChange={(v) => setCost(Number(v))}
                    />
                    <Numeric
                      label="Supplier funding ($ / unit)"
                      value={funding}
                      step={0.05}
                      onChange={(v) => setFunding(Number(v))}
                    />
                  </div>
                </details>
              </div>
              {busy === 'optimize' ? (
                <Busy text="Evaluating candidates, then checking the combined portfolio…" />
              ) : optimization ? (
                <>
                  {optimization.forecast ? (
                    <>
                      <div className="recommendation-banner">
                        <div>
                          <span className="eyebrow">
                            YOUR RECOMMENDED PORTFOLIO
                          </span>
                          <h2>
                            {optimization.selected.length} offers.{' '}
                            {money(optimization.forecast.planning_profit)}{' '}
                            planning incremental profit.
                          </h2>
                          <p>{optimization.reason}</p>
                        </div>
                        <Button
                          variant="outline"
                          disabled={!!busy || !canSave}
                          onClick={() =>
                            run('save', async () => {
                              await api(
                                '/scenarios',
                                workspace,
                                optimization.scenario,
                              );
                              notify('Recommended portfolio saved.');
                              setView('Saved plans');
                            })
                          }
                        >
                          <Save />
                          Save portfolio
                        </Button>
                        <Button
                          disabled={!!busy || !canSave}
                          onClick={() =>
                            confirmPromotion(
                              optimization.scenario,
                              optimization.forecast,
                            )
                          }
                        >
                          <CheckCircle2 /> Confirm promotions
                        </Button>
                      </div>
                      <div className="metric-grid three">
                        <Metric
                          label="Planning incremental profit"
                          value={money(optimization.forecast.planning_profit)}
                          sub="Post-offer losses retained; positive credit excluded"
                          accent
                        />
                        <Metric
                          label="Budget committed"
                          value={money(optimization.forecast.investment)}
                          sub={
                            money(budget - optimization.forecast.investment) +
                            ' remaining'
                          }
                        />
                        <Metric
                          label="Planning portfolio ROI"
                          value={percent(optimization.forecast.planning_roi)}
                          sub="Incremental contribution ÷ investment"
                        />
                      </div>
                      <PlanningNote result={optimization.forecast} />
                      <OfferEvidence result={optimization.forecast} />
                      <div className="recommendation-cards">
                        {optimization.selected.map((c: any, i: number) => (
                          <section
                            className="panel recommendation-card"
                            key={c.promotion.product_id}
                          >
                            <span className="offer-number">0{i + 1}</span>
                            <div>
                              <span className="pill green">
                                {c.promotion.discount * 100}% off ·{' '}
                                {c.promotion.duration} weeks
                              </span>
                              <h2>{c.name}</h2>
                              <span className="muted">
                                Product {c.promotion.product_id}
                              </span>
                            </div>
                            <div className="offer-value">
                              <strong>{money(c.planning_profit)}</strong>
                              <span>Standalone planning profit</span>
                            </div>
                            <CheckCircle2 className="positive" size={22} />
                          </section>
                        ))}
                      </div>
                      <div className="analysis-grid">
                        <section className="panel">
                          <div className="section-title">
                            <h2>The combined demand outlook</h2>
                          </div>
                          <DemandChart
                            data={optimization.forecast.weeks}
                            forecast
                          />
                        </section>
                        <section className="panel">
                          <div className="section-title">
                            <h2>Portfolio effects</h2>
                            <span className="unit-label">UNITS</span>
                          </div>
                          <Effects effects={optimization.forecast.effects} />
                        </section>
                      </div>
                      <Warnings items={optimization.forecast.warnings} />
                    </>
                  ) : (
                    <div className="panel no-plan">
                      <ShieldCheck size={31} />
                      <h2>The best supported choice is to hold.</h2>
                      <p>{optimization.reason}</p>
                      <span className="pill">
                        {optimization.rejected} unprofitable, unsupported or
                        infeasible candidates excluded
                      </span>
                    </div>
                  )}
                  {optimization.candidates?.length > 0 && (
                    <details className="candidate-details">
                      <summary>
                        Compare other profitable candidates (
                        {optimization.candidates.length})
                      </summary>
                      <div className="panel table-panel">
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead>Product</TableHead>
                              <TableHead>Offer</TableHead>
                              <TableHead className="numeric">
                                Planning profit
                              </TableHead>
                              <TableHead className="numeric">
                                Investment
                              </TableHead>
                              <TableHead className="numeric">
                                Planning ROI
                              </TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {optimization.candidates.map(
                              (c: any, i: number) => (
                                <TableRow key={i}>
                                  <TableCell>{c.name}</TableCell>
                                  <TableCell>
                                    {c.promotion.discount * 100}% ·{' '}
                                    {c.promotion.duration} weeks
                                  </TableCell>
                                  <TableCell className="numeric positive">
                                    {money(c.planning_profit)}
                                  </TableCell>
                                  <TableCell className="numeric">
                                    {money(c.investment)}
                                  </TableCell>
                                  <TableCell className="numeric">
                                    {percent(c.planning_roi)}
                                  </TableCell>
                                </TableRow>
                              ),
                            )}
                          </TableBody>
                        </Table>
                      </div>
                    </details>
                  )}
                  <div className="notice">
                    {optimization.optimality ||
                      'Only supported and profitable candidates enter selection. Results remain model estimates.'}
                  </div>
                </>
              ) : (
                <div className="planning-principles">
                  {[
                    {
                      icon: TrendingUp,
                      title: 'Protect incremental profit',
                      text: 'An offer must improve contribution after discounting and execution costs.',
                    },
                    {
                      icon: Layers,
                      title: 'Consider the whole portfolio',
                      text: 'Selected offers are simulated together to account for competing demand.',
                    },
                    {
                      icon: ShieldCheck,
                      title: 'Stay within the evidence',
                      text: 'Unsupported discounts and offers with too little comparable history are excluded.',
                    },
                  ].map(({ icon: Icon, title, text }) => (
                    <div key={title}>
                      <Icon size={23} />
                      <h3>{title}</h3>
                      <p>{text}</p>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
          {view === 'Saved plans' && (
            <>
              {saved.length ? (
                <div className="saved-grid">
                  {saved.map((s) => (
                    <section className="panel saved-card" key={s.id}>
                      <div className="section-title">
                        <span className="pill">
                          {s.request.promotions.length}{' '}
                          {s.request.promotions.length === 1
                            ? 'offer'
                            : 'offers'}
                        </span>
                        <span className="micro">
                          {new Date(s.created).toLocaleDateString()}
                        </span>
                      </div>
                      <h2>{s.name}</h2>
                      <p className="panel-subtitle">
                        Store {s.request.store_id} · {s.result.start_date}
                      </p>
                      <div className="saved-metrics">
                        <div>
                          <span>
                            {s.result.planning_profit === undefined
                              ? 'Legacy model profit'
                              : 'Planning profit'}
                          </span>
                          <strong>
                            {money(
                              s.result.planning_profit ??
                                s.result.incremental_profit,
                            )}
                          </strong>
                        </div>
                        <div>
                          <span>
                            {s.result.planning_profit === undefined
                              ? 'Legacy ROI'
                              : 'Planning ROI'}
                          </span>
                          <strong>
                            {percent(
                              s.result.planning_profit === undefined
                                ? s.result.roi
                                : s.result.planning_roi,
                            )}
                          </strong>
                        </div>
                      </div>
                      <div className="model-ref">{s.result.model_id}</div>
                      <PlanningNote result={s.result} />
                      <div className="save-row">
                        <Button
                          variant="outline"
                          disabled={!!busy || !canSave}
                          onClick={() => refreshSavedPlan(s)}
                        >
                          Recalculate & save copy
                        </Button>
                        <Button
                          variant="outline"
                          onClick={() => loadScenario(s)}
                        >
                          Open plan
                          <ArrowRight />
                        </Button>
                        <Button
                          variant="ghost"
                          onClick={() =>
                            run('export', () =>
                              download(
                                '/scenarios/' + s.id + '/export',
                                'aislemint-forecast.csv',
                                workspace,
                              ),
                            )
                          }
                        >
                          <Download />
                          Export CSV
                        </Button>
                      </div>
                    </section>
                  ))}
                </div>
              ) : (
                <section className="panel">
                  <Empty
                    title="Your next decision starts here"
                    text="Run a scenario or generate a recommended portfolio, then save its forecast and assumptions for review."
                  >
                    <Button onClick={() => setView('Scenario studio')}>
                      <Plus />
                      Create a scenario
                    </Button>
                  </Empty>
                </section>
              )}
            </>
          )}
          {view === 'Saved plans' && reviewedPlan && (
            <section className="panel saved-review">
              <div className="section-title">
                <div>
                  <span className="eyebrow">SAVED PORTFOLIO SNAPSHOT</span>
                  <h2>{reviewedPlan.name}</h2>
                  <p className="panel-subtitle">
                    Store {reviewedPlan.request.store_id} ·{' '}
                    {reviewedPlan.result.start_date} ·{' '}
                    {reviewedPlan.result.model_id}
                  </p>
                </div>
                <Button
                  variant="ghost"
                  onClick={() => setReviewedPlan(null)}
                  aria-label="Close portfolio review"
                >
                  <X />
                </Button>
              </div>
              <div className="metric-grid three">
                <Metric
                  label={
                    reviewedPlan.result.planning_profit === undefined
                      ? 'Legacy model profit'
                      : 'Planning incremental profit'
                  }
                  value={money(
                    reviewedPlan.result.planning_profit ??
                      reviewedPlan.result.incremental_profit,
                  )}
                  sub="Saved model estimate"
                  accent
                />
                <Metric
                  label="Promotion investment"
                  value={money(reviewedPlan.result.investment)}
                  sub="All selected offers"
                />
                <Metric
                  label={
                    reviewedPlan.result.planning_profit === undefined
                      ? 'Legacy portfolio ROI'
                      : 'Planning portfolio ROI'
                  }
                  value={percent(
                    reviewedPlan.result.planning_profit === undefined
                      ? reviewedPlan.result.roi
                      : reviewedPlan.result.planning_roi,
                  )}
                  sub="Saved economics"
                />
              </div>
              <PlanningNote result={reviewedPlan.result} />
              <OfferEvidence result={reviewedPlan.result} />
              <Button
                variant="outline"
                disabled={!!busy || !canSave}
                onClick={() => refreshSavedPlan(reviewedPlan)}
              >
                Recalculate & save copy
              </Button>
              <div className="analysis-grid">
                <div>
                  <DemandChart data={reviewedPlan.result.weeks} forecast />
                </div>
                <Effects effects={reviewedPlan.result.effects} />
              </div>
              <Warnings items={reviewedPlan.result.warnings} />
            </section>
          )}
          <footer className="page-footer">
            <span>
              <BrandMark />
              AisleMint · Retail intelligence
            </span>
            <span>
              {workspace === 'benchmark'
                ? 'Historical data · USD'
                : 'Imported data · ' + (summary?.quality?.currency || 'USD')}
              <span className="footer-dot">·</span>Estimated effects, informed
              decisions
            </span>
          </footer>
        </fieldset>
      </div>
      {toast && (
        <div className="toast" role="status">
          <CheckCircle2 size={18} />
          {toast}
        </div>
      )}
    </section>
  );
}
