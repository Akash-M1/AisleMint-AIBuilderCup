'use client';
import { useEffect, useId, useRef, useState } from 'react';
import {
  Activity,
  ArrowRight,
  CalendarDays,
  Download,
  Info,
  LoaderCircle,
  Package,
  Play,
  Plus,
  Save,
  Truck,
  X,
} from 'lucide-react';
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  api,
  download,
  money as formatMoney,
  number,
  percent,
  shortDate,
} from '@/lib/api';
import OfferEditor, {
  OfferEvidence,
  emptyOfferConfig,
  type OfferConfig,
} from '@/components/offer-editor';

type Offer = Partial<OfferConfig> & {
  product_id: string;
  start_date: string;
  duration: number;
  discount: number;
  feature: boolean;
  display: boolean;
};
import type { DemandHandoff } from '@/lib/workspace-contracts';
export type { DemandHandoff } from '@/lib/workspace-contracts';
type PlanningCalendar = {
  revision: number;
  campaigns: {
    id: string;
    name: string;
    status: string;
    request: { store_id: string; start_date: string; promotions: Offer[] };
  }[];
  events_in_horizon: {
    campaign_id: string;
    product_id: string;
    store_id: string;
    period_end: string;
    terms?: Offer;
  }[];
  period_ends: string[];
  holidays: {
    date: string;
    name: string;
    kind: string;
    origin: string;
    period_end: string;
  }[];
  holiday_metadata: {
    version: string;
    supplied_rows: number;
    supplied_end: string;
    corrected_metadata_rows: number;
  } | null;
};
type Receipt = {
  quantity: number;
  due_date: string;
  expiry_date?: string | null;
};
type Position = {
  store_id: string;
  product_id: string;
  on_hand: number | null;
  unit_cost: number | null;
  reserved: number;
  backlog: number;
  lead_time_days: number;
  review_period_days: number;
  case_pack: number;
  minimum_order: number;
  max_stock: number | null;
  shelf_life_days: number | null;
  existing_expiry_date: string | null;
  lead_time_delay_days: number;
  receipts: Receipt[];
};
type Item = {
  product_id: string;
  name: string;
  category: string;
  average_units: number;
  price: number;
};
type Metric = { wape: number; bias: number };
type Model = {
  model_id: string;
  family: string;
  max_horizon: number;
  test: Metric;
  interval_90_coverage: number | null;
  protection_95_coverage: number | null;
  training_seconds: number;
  uncertainty_protocol?: string;
  candidates: {
    name: string;
    selection?: Metric;
    calibration: Metric;
    test: Metric;
  }[];
  limitations: string[];
};
type Source = {
  id: string;
  name: string;
  status: string;
  error?: string | null;
};
type Summary = {
  quality: {
    currency?: string;
    frequency: string;
    step_days: number;
    start: string;
    end: string;
    rows: number;
    annual_history_limited: boolean;
  };
  model: Model | null;
  stores: string[];
};
type Point = {
  date: string;
  forecast: number;
  lower: number;
  upper: number;
  price: number;
};
type Forecast = {
  model_id: string;
  as_of: string;
  frequency: string;
  step_days: number;
  horizon: number;
  total_units: number;
  series: {
    store_id: string;
    product_id: string;
    name: string;
    category: string;
    total_units: number;
    average_recent: number;
    rows: Point[];
    history: { date: string; actual: number }[];
  }[];
  assumptions: string[];
};
type Projection = {
  date: string;
  demand: number;
  ending_stock: number;
  lower_stock: number;
  upper_stock: number;
  expected_lost_sales: number;
  stockout_risk: number;
  in_protection_period: boolean;
};
type PlanRow = {
  store_id: string;
  product_id: string;
  name: string;
  action: string;
  order_quantity: number;
  purchase_cost: number;
  safety_stock: number;
  reorder_point: number;
  inventory_position: number;
  arrival_date: string;
  next_review_date: string;
  fill_rate: number;
  post_arrival_service: number;
  estimated_cycle_service: number;
  expected_shortfall: number;
  incremental_contribution: number;
  holding_cost: number;
  expected_writeoff: number;
  first_risk_date: string | null;
  lead_time_shortfall: number;
  notes: string[];
  projection: Projection[];
};
type Plan = {
  name: string;
  model_id: string;
  as_of: string;
  frequency: string;
  service_target: number;
  inventory_basis: string;
  orders: number;
  budget_used: number;
  budget_remaining: number | null;
  expected_shortfall: number;
  incremental_contribution: number;
  stock_risk_positions: number;
  rows: PlanRow[];
  assumptions: string[];
  forecast: Forecast;
};
type Saved = { id: string; name: string; created: string; result: Plan };

function Field({
  label,
  value,
  onChange,
  min = 0,
  max,
  step = 1,
  type = 'number',
  hint,
}: {
  label: string;
  value: string | number | null;
  onChange: (value: string) => void;
  min?: number;
  max?: number;
  step?: number;
  type?: string;
  hint?: string;
}) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <Input
        id={id}
        type={type}
        value={value ?? ''}
        min={min}
        max={max}
        step={step}
        onChange={(e) => onChange(e.target.value)}
      />
      {hint && <small>{hint}</small>}
    </div>
  );
}
function SelectField({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
}) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select
        id={id}
        className="demand-select"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}
function Stat({
  label,
  value,
  detail,
  accent = false,
}: {
  label: string;
  value: string;
  detail: string;
  accent?: boolean;
}) {
  return (
    <div className={'metric ' + (accent ? 'accent' : '')}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}
function emptyPosition(
  product_id: string,
  store_id: string,
  step: number,
): Position {
  return {
    product_id,
    store_id,
    on_hand: null,
    unit_cost: null,
    reserved: 0,
    backlog: 0,
    lead_time_days: step * 2,
    review_period_days: step,
    case_pack: 1,
    minimum_order: 0,
    max_stock: null,
    shelf_life_days: null,
    existing_expiry_date: null,
    lead_time_delay_days: 0,
    receipts: [],
  };
}

export default function DemandPlanner({
  mode,
  workspace,
  handoff,
  shopScoped = false,
  openWorkbench,
}: {
  mode: 'demand' | 'inventory';
  workspace: string;
  handoff?: DemandHandoff | null;
  shopScoped?: boolean;
  openWorkbench?: () => void;
}) {
  const [source, setSource] = useState(workspace),
    [sources, setSources] = useState<Source[]>([]),
    [summary, setSummary] = useState<Summary | null>(null);
  const [store, setStore] = useState('367'),
    [catalog, setCatalog] = useState<Item[]>([]),
    [selected, setSelected] = useState<string[]>([]),
    [horizon, setHorizon] = useState(8);
  const [offers, setOffers] = useState<Offer[]>([]),
    [holidayDates, setHolidayDates] = useState(''),
    [forecast, setForecast] = useState<Forecast | null>(null),
    [plan, setPlan] = useState<Plan | null>(null);
  const [positions, setPositions] = useState<Position[]>([]),
    [basis, setBasis] = useState<'user_supplied' | 'illustrative'>(
      'user_supplied',
    );
  const [service, setService] = useState(95),
    [budget, setBudget] = useState<number | null>(null),
    [holding, setHolding] = useState(20),
    [fee, setFee] = useState(0),
    [objective, setObjective] = useState('service');
  const [active, setActive] = useState(''),
    [busy, setBusy] = useState(''),
    [error, setError] = useState(''),
    [message, setMessage] = useState(''),
    [reload, setReload] = useState(0),
    [saved, setSaved] = useState<Saved[]>([]),
    [savedId, setSavedId] = useState('');
  const [name, setName] = useState('Inventory replenishment plan'),
    [stockDate, setStockDate] = useState('');
  const [promoPlans, setPromoPlans] = useState<
    {
      id: string;
      name: string;
      request: {
        store_id: string;
        start_date: string | null;
        promotions: Omit<Offer, 'start_date'>[];
      };
    }[]
  >([]);
  const epoch = useRef(0);
  const autoHandoff = useRef('');
  const [calendar, setCalendar] = useState<PlanningCalendar | null>(null),
    [calendarReload, setCalendarReload] = useState(0),
    [includeConfirmed, setIncludeConfirmed] = useState(true);
  const money = (value: number | null | undefined) =>
    formatMoney(value, summary?.quality.currency || 'USD');
  const step = summary?.quality.step_days || 7,
    unit = step === 1 ? 'days' : 'weeks';
  const asOf = summary?.quality.end || '';
  const nextDate = asOf
    ? new Date(new Date(asOf + 'T00:00:00Z').getTime() + step * 86400000)
        .toISOString()
        .slice(0, 10)
    : '';
  const forecastRequest = {
    source_id: source,
    store_id: store,
    product_ids: selected,
    horizon,
    promotions: offers,
    holidays: holidayDates
      .split(',')
      .map((v) => v.trim())
      .filter(Boolean),
    use_confirmed_promotions: includeConfirmed,
    expected_calendar_revision: includeConfirmed
      ? (calendar?.revision ?? null)
      : null,
  };
  // Reward products need their own stock positions, including when this screen
  // is opened directly instead of through the confirmation handoff.
  const rewardSelectionKey = JSON.stringify(
    [
      ...offers,
      ...(includeConfirmed
        ? calendar?.events_in_horizon.map((e) => e.terms).filter(Boolean) || []
        : []),
    ].flatMap((o) =>
      o && selected.includes(o.product_id)
        ? (o.mechanics || []).flatMap((m) => m.reward_choices || [])
        : [],
    ),
  );
  useEffect(() => {
    const extra = (JSON.parse(rewardSelectionKey) as string[]).filter(
      (id) => !selected.includes(id),
    );
    if (extra.length) {
      const combined = Array.from(new Set([...selected, ...extra]));
      if (combined.length <= 12) setSelected(combined);
      else
        setError(
          'Reward choices take this plan above 12 products. Reduce the assortment or split the plan.',
        );
    }
  }, [rewardSelectionKey]);
  const inventoryRequest = {
    forecast: forecastRequest,
    stock_as_of: stockDate,
    positions,
    service_level: service / 100,
    budget,
    annual_holding_rate: holding / 100,
    order_fee: fee,
    objective,
    name,
    inventory_basis: basis,
  };
  const selectedSeries =
    forecast?.series.find((s) => s.product_id === active) ||
    forecast?.series[0];
  const selectedPlan =
    plan?.rows.find((s) => s.product_id === active) || plan?.rows[0];
  const position =
    positions.find((p) => p.product_id === active) || positions[0];
  const job = sources.find((s) => s.id === source);

  useEffect(() => {
    setSource(workspace);
  }, [workspace]);
  useEffect(() => {
    if (handoff?.source_id === workspace) {
      setSource(workspace);
      setIncludeConfirmed(true);
      setCalendarReload((v) => v + 1);
    }
  }, [handoff?.nonce, workspace]);
  useEffect(() => {
    setStockDate(asOf);
  }, [asOf]);
  useEffect(() => {
    api<Source[]>('/demand/sources')
      .then((rows) =>
        setSources(shopScoped ? rows.filter((r) => r.id === workspace) : rows),
      )
      .catch((e) => setError(e.message));
  }, [reload]);
  useEffect(() => {
    const c = new AbortController();
    epoch.current++;
    setSummary(null);
    setForecast(null);
    setPlan(null);
    setPositions([]);
    setOffers([]);
    setHolidayDates('');
    setCatalog([]);
    setSelected([]);
    setBasis('user_supplied');
    setSavedId('');
    setError('');
    setCalendar(null);
    api<Summary>(
      '/demand/summary?source_id=' + source,
      source,
      undefined,
      c.signal,
    )
      .then((s) => {
        setSummary(s);
        setHorizon(
          Math.min(
            handoff?.source_id === source ? handoff.horizon : 8,
            s.model?.max_horizon || 13,
          ),
        );
        setStore((old) =>
          handoff?.source_id === source && s.stores.includes(handoff.store_id)
            ? handoff.store_id
            : s.stores.includes(old)
              ? old
              : s.stores[0] || '',
        );
      })
      .catch((e) => {
        if (e.name !== 'AbortError') setError(e.message);
      });
    api<Saved[]>(
      '/demand/plans?source_id=' + source,
      source,
      undefined,
      c.signal,
    )
      .then(setSaved)
      .catch(() => setSaved([]));
    if (!source.startsWith('demand_'))
      api<typeof promoPlans>('/scenarios', source, undefined, c.signal)
        .then(setPromoPlans)
        .catch(() => setPromoPlans([]));
    else setPromoPlans([]);
    return () => {
      c.abort();
      epoch.current++;
    };
  }, [source, reload]);
  useEffect(() => {
    if (!summary?.model || !store) return;
    const c = new AbortController();
    setCatalog([]);
    setPositions([]);
    setOffers([]);
    api<Item[]>(
      '/demand/catalog?source_id=' + source + '&store_id=' + store,
      source,
      undefined,
      c.signal,
    )
      .then((items) => {
        setCatalog(items);
        const handed =
          handoff?.source_id === source
            ? handoff.product_ids.filter((id) =>
                items.some((p) => p.product_id === id),
              )
            : [];
        setSelected(
          handed.length ? handed : items.slice(0, 6).map((p) => p.product_id),
        );
        setActive(items[0]?.product_id || '');
      })
      .catch((e) => {
        if (e.name !== 'AbortError') setError(e.message);
      });
    return () => c.abort();
  }, [source, store, summary?.model?.model_id]);
  useEffect(() => {
    if (!summary?.model || !store) return;
    const controller = new AbortController();
    setCalendar(null);
    const refresh = () =>
      api<PlanningCalendar>(
        '/demand/calendar?source_id=' +
          source +
          '&store_id=' +
          store +
          '&horizon=' +
          horizon,
        'benchmark',
        undefined,
        controller.signal,
      )
        .then(setCalendar)
        .catch((e) => {
          if (e.name !== 'AbortError') setError(e.message);
        });
    void refresh();
    window.addEventListener('focus', refresh);
    return () => {
      controller.abort();
      window.removeEventListener('focus', refresh);
    };
  }, [source, store, horizon, summary?.model?.model_id, calendarReload]);
  useEffect(() => {
    setPositions((old) =>
      selected.map(
        (pid) =>
          old.find((p) => p.product_id === pid && p.store_id === store) ||
          emptyPosition(pid, store, step),
      ),
    );
  }, [selected, store, step]);
  useEffect(() => {
    epoch.current++;
    setForecast(null);
    setPlan(null);
    setSavedId('');
  }, [
    source,
    store,
    selected,
    horizon,
    offers,
    holidayDates,
    includeConfirmed,
    calendar?.revision,
  ]);
  useEffect(() => {
    if (
      mode !== 'demand' ||
      !handoff ||
      handoff.source_id !== source ||
      autoHandoff.current === handoff.nonce ||
      !summary?.model ||
      !calendar ||
      !selected.length
    )
      return;
    if (handoff.store_id !== 'all' && store !== handoff.store_id) return;
    if (!selected.some((id) => handoff.product_ids.includes(id))) return;
    const timer = setTimeout(() => {
      autoHandoff.current = handoff.nonce;
      void run('forecast', async () => {
        const e = epoch.current;
        const response = await api<Forecast>(
          '/demand/forecast',
          'benchmark',
          forecastRequest,
        );
        if (e === epoch.current) {
          setForecast(response);
          setActive(response.series[0]?.product_id || '');
          setMessage(
            'Demand updated from the confirmed promotion calendar.' +
              (handoff.store_id === 'all'
                ? ' The promotion applies across stores; inspect demand one location at a time.'
                : ''),
          );
        }
      });
    }, 200);
    return () => clearTimeout(timer);
  }, [
    mode,
    handoff?.nonce,
    source,
    store,
    selected,
    calendar?.revision,
    summary?.model?.model_id,
  ]);
  useEffect(() => {
    epoch.current++;
    setPlan(null);
    setSavedId('');
  }, [
    positions,
    service,
    budget,
    holding,
    fee,
    objective,
    basis,
    name,
    stockDate,
  ]);
  useEffect(() => {
    if (!['queued', 'training'].includes(sources.find((s) => s.id === source)?.status || '')) return;
    const timer = setInterval(
      () =>
        api<Source[]>('/demand/sources')
          .then((rows) => {
            setSources(rows);
            if (!['queued', 'training'].includes(rows.find((r) => r.id === source)?.status || ''))
              setReload((v) => v + 1);
          })
          .catch((e) => setError(e.message)),
      5000,
    );
    return () => clearInterval(timer);
  }, [sources, source]);

  async function run(label: string, action: () => Promise<void>) {
    setBusy(label);
    setError('');
    setMessage('');
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request failed');
    } finally {
      setBusy('');
    }
  }
  function updatePosition(pid: string, values: Partial<Position>) {
    setPositions((old) =>
      old.map((p) => (p.product_id === pid ? { ...p, ...values } : p)),
    );
  }
  function requireStock() {
    if (positions.some((p) => p.on_hand === null || p.unit_cost === null))
      throw new Error(
        'Enter on-hand stock and unit cost, import a snapshot, or load clearly labeled illustrative inputs.',
      );
  }
  function loadExample() {
    setPositions((old) =>
      old.map((p) => {
        const item = catalog.find((c) => c.product_id === p.product_id)!;
        return {
          ...p,
          on_hand: Math.ceil(item.average_units * 1.5),
          unit_cost: Math.round(item.price * 0.65 * 100) / 100,
        };
      }),
    );
    setBasis('illustrative');
    setMessage(
      'Illustrative stock and costs loaded for exploration. These are not observed inventory or supplier costs.',
    );
  }
  function loadPromo(id: string) {
    const p = promoPlans.find((p) => p.id === id);
    if (!p) return;
    const schedule = p.request.promotions.map((o) => ({
      product_id: o.product_id,
      discount: o.discount,
      duration: o.duration,
      feature: o.feature,
      display: o.display,
      mechanics: o.mechanics || [],
      normal_basket_units: o.normal_basket_units ?? 2,
      redemption_rate: o.redemption_rate ?? null,
      stacking_policy: o.stacking_policy || 'exclusive',
      start_date: p.request.start_date || nextDate,
    }));
    setOffers(schedule);
    setIncludeConfirmed(false);
    setMessage(
      'Saved draft loaded as a what-if comparison. Confirmed promotions are excluded for this comparison.',
    );
  }

  return (
    <div className="demand-workspace">
      {error && (
        <div className="error-banner" role="alert">
          <Info size={18} />
          <span>{error}</span>
          <button
            onClick={() => setError('')}
            aria-label="Dismiss demand error"
          >
            <X size={16} />
          </button>
        </div>
      )}
      {message && (
        <div className="notice demand-message" role="status">
          {message}
        </div>
      )}
      <fieldset disabled={!!busy} className="working-fieldset">
        <section className="panel demand-scope">
          <SelectField
            label="Sales history"
            value={source}
            onChange={setSource}
            options={sources.map((s) => ({ value: s.id, label: s.name }))}
          />
          <SelectField
            label="Store / fulfillment location"
            value={store}
            onChange={setStore}
            options={(summary?.stores || []).map((s) => ({
              value: s,
              label: 'Store ' + s,
            }))}
          />
          <Field
            label={'Forecast horizon (' + unit + ')'}
            value={horizon}
            min={1}
            max={summary?.model?.max_horizon || 13}
            onChange={(v) => setHorizon(Number(v))}
          />
          <div className="demand-date">
            <CalendarDays size={19} />
            <span>
              Planning from<strong>{asOf || 'Loading history…'}</strong>
              <small>
                {step === 7
                  ? 'Weekly observations · no daily disaggregation'
                  : 'Daily observations'}
              </small>
            </span>
          </div>
        </section>

        <section className="panel demand-import">
          <div className="demand-import-body">
            <h3>Connected demand history</h3>
            <p>
              OneSales supplies observed sales, prices, promotions, holidays and
              stockout signals directly from MongoDB. Manage data readiness,
              training and model activation in Modelling workbench.
            </p>
            <p className="micro">
              External business systems use the authenticated integration API.
              Minimum history: 180 days or 78 weeks; annual seasonality needs
              longer history to validate.
            </p>
          </div>
        </section>

        {summary && !summary.model ? (
          <section className="panel demand-onboarding">
            <div className="demand-symbol">
              <Activity size={30} />
            </div>
            <h2>Train this history for demand planning</h2>
            <p>
              A dedicated model will compare sales-history and seasonal
              forecasting candidates on future periods, then estimate demand
              uncertainty for inventory decisions.
            </p>
            <div className="metric-grid three">
              <Stat
                label="Observed sales rows"
                value={number(summary.quality.rows)}
                detail="Missing sales are not filled with zeros"
              />
              <Stat
                label="Time scale"
                value={summary.quality.frequency}
                detail={summary.quality.start + ' to ' + asOf}
              />
              <Stat
                label="Model status"
                value={job?.status || 'Untrained'}
                detail="Candidate versions are isolated by shop"
              />
            </div>
            {job?.error && <p className="negative">{job.error}</p>}
            {shopScoped ? (
              <Button onClick={openWorkbench}>Open Modelling workbench</Button>
            ) : (
              <Button
                disabled={['queued', 'training'].includes(job?.status || '')}
                onClick={() =>
                  run('train', async () => {
                    await api('/demand/train', 'benchmark', {
                      source_id: source,
                    });
                    setReload((v) => v + 1);
                  })
                }
              >
                {['queued', 'training'].includes(job?.status || '') ? (
                  <LoaderCircle className="spin" />
                ) : (
                  <Play />
                )}
                {job?.status === 'queued'
                  ? 'Queued for training…'
                  : job?.status === 'training'
                  ? 'Training demand models…'
                  : 'Train demand models'}
              </Button>
            )}
          </section>
        ) : (
          summary?.model && (
            <>
              <section className="panel demand-assortment">
                <div className="section-title">
                  <div>
                    <h2>Plan an assortment</h2>
                    <p className="panel-subtitle">
                      Choose up to 12 products. Inventory stays separate for
                      every product and store.
                    </p>
                  </div>
                  <span className="pill">{selected.length} selected</span>
                </div>
                <div className="demand-product-grid">
                  {catalog.map((p) => (
                    <label
                      key={p.product_id}
                      className={
                        'demand-product ' +
                        (selected.includes(p.product_id) ? 'selected' : '')
                      }
                    >
                      <input
                        type="checkbox"
                        checked={selected.includes(p.product_id)}
                        disabled={
                          !selected.includes(p.product_id) &&
                          selected.length >= 12
                        }
                        onChange={(e) =>
                          setSelected((old) =>
                            e.target.checked
                              ? [...old, p.product_id]
                              : old.filter((id) => id !== p.product_id),
                          )
                        }
                      />
                      <span>
                        <strong>{p.name}</strong>
                        <small>
                          {p.category} · {number(p.average_units)} units/
                          {step === 1 ? 'day' : 'week'}
                        </small>
                      </span>
                    </label>
                  ))}
                </div>
              </section>

              <details className="panel demand-calendar">
                <summary>
                  <CalendarDays size={17} /> Confirmed promotions and US
                  holidays{' '}
                  <span className="pill">
                    {
                      new Set(
                        calendar?.events_in_horizon.map((e) => e.campaign_id),
                      ).size
                    }{' '}
                    confirmed · {offers.length} what-if
                  </span>
                </summary>
                <div className="demand-import-body">
                  <div className="demand-support">
                    <label>
                      <input
                        type="checkbox"
                        checked={includeConfirmed}
                        onChange={(e) => setIncludeConfirmed(e.target.checked)}
                      />{' '}
                      Include confirmed promotions automatically
                    </label>
                    <Button
                      variant="outline"
                      onClick={() => setCalendarReload((v) => v + 1)}
                    >
                      Refresh calendar
                    </Button>
                  </div>
                  {!includeConfirmed && (
                    <p className="demand-note">
                      What-if comparison: confirmed promotions are excluded from
                      this forecast.
                    </p>
                  )}
                  {calendar?.campaigns
                    .filter((p) => p.status === 'confirmed')
                    .map((p) => (
                      <div className="confirmed-promotion-row" key={p.id}>
                        <div>
                          <strong>{p.name}</strong>
                          <small>
                            {p.request.start_date} ·{' '}
                            {p.request.promotions.length} product(s) · Store{' '}
                            {p.request.store_id}
                          </small>
                        </div>
                        <span className="pill green">Confirmed</span>
                        <Button
                          variant="ghost"
                          onClick={() =>
                            run('cancel-promotion', async () => {
                              await api(
                                '/promotions/' + p.id + '/cancel',
                                source,
                                {},
                              );
                              setCalendarReload((v) => v + 1);
                              setMessage(
                                'Promotion cancelled. It will no longer feed new demand or inventory forecasts; saved decisions remain unchanged.',
                              );
                            })
                          }
                        >
                          Cancel promotion
                        </Button>
                      </div>
                    ))}
                  {calendar &&
                    !calendar.campaigns.some(
                      (p) => p.status === 'confirmed',
                    ) && (
                      <p>
                        No confirmed promotions for this location. Confirm a
                        reviewed scenario to connect it to demand and inventory.
                      </p>
                    )}
                  {calendar?.holiday_metadata ? (
                    <div className="holiday-calendar">
                      <strong>
                        {summary?.quality.currency === 'INR'
                          ? 'Client local holiday calendar'
                          : 'US holiday calendar is included automatically'}
                      </strong>
                      <p className="micro">
                        {summary?.quality.currency === 'INR' ? (
                          'India uses supplied local holiday flags. Future regional occasions need your planned dates.'
                        ) : (
                          <>
                            {calendar.holiday_metadata.supplied_rows} supplied
                            dates through{' '}
                            {calendar.holiday_metadata.supplied_end}. Later
                            dates follow calendar rules. Occasions and federal
                            observed dates are shown separately.
                          </>
                        )}
                      </p>
                      <div className="holiday-event-grid">
                        {calendar.holidays.map((h, i) => (
                          <div key={i}>
                            <strong>{h.name}</strong>
                            <span>
                              {h.date} ·{' '}
                              {h.kind === 'federal_observed'
                                ? 'Federal observed'
                                : 'Retail occasion'}
                            </span>
                            <small>Forecast period ending {h.period_end}</small>
                          </div>
                        ))}
                      </div>
                      {!calendar.holidays.length && (
                        <p className="micro">
                          No listed holidays inside the selected horizon.
                        </p>
                      )}
                    </div>
                  ) : (
                    <p className="micro">
                      This model predates automatic US holiday features. Train a
                      new source version to enable them.
                    </p>
                  )}
                  <p>
                    Confirmed offers feed the forecast across the assortment,
                    including later exposure effects. Add draft offers below for
                    what-if planning. Overlapping offers are rejected.
                  </p>
                  <div className="save-row">
                    <Button
                      variant="outline"
                      disabled={!selected.length || !calendar}
                      onClick={() =>
                        setOffers((old) => [
                          ...old,
                          {
                            product_id: selected[0],
                            start_date: nextDate,
                            duration: 1,
                            discount: 0.2,
                            feature: false,
                            display: false,
                          },
                        ])
                      }
                    >
                      <Plus /> Add offer
                    </Button>
                    {promoPlans.filter(
                      (p) =>
                        p.request.store_id === store ||
                        p.request.store_id === 'all',
                    ).length > 0 && (
                      <SelectField
                        label="Use a saved promotion calendar"
                        value=""
                        onChange={loadPromo}
                        options={[
                          { value: '', label: 'Choose a saved plan' },
                          ...promoPlans
                            .filter(
                              (p) =>
                                p.request.store_id === store ||
                                p.request.store_id === 'all',
                            )
                            .map((p) => ({ value: p.id, label: p.name })),
                        ]}
                      />
                    )}
                  </div>
                  {offers.map((offer, i) => (
                    <div className="demand-offer" key={i}>
                      <SelectField
                        label="Promoted product"
                        value={offer.product_id}
                        onChange={(v) =>
                          setOffers((old) =>
                            old.map((o, j) =>
                              i === j ? { ...o, product_id: v } : o,
                            ),
                          )
                        }
                        options={catalog.map((p) => ({
                          value: p.product_id,
                          label: p.name,
                        }))}
                      />
                      <Field
                        label="First forecast period"
                        type="date"
                        value={offer.start_date}
                        onChange={(v) =>
                          setOffers((old) =>
                            old.map((o, j) =>
                              i === j ? { ...o, start_date: v } : o,
                            ),
                          )
                        }
                      />
                      <Field
                        label="Discount (%)"
                        value={offer.discount * 100}
                        max={50}
                        onChange={(v) =>
                          setOffers((old) =>
                            old.map((o, j) =>
                              i === j ? { ...o, discount: Number(v) / 100 } : o,
                            ),
                          )
                        }
                      />
                      <Field
                        label={'Duration (' + unit + ')'}
                        value={offer.duration}
                        min={1}
                        max={horizon}
                        onChange={(v) =>
                          setOffers((old) =>
                            old.map((o, j) =>
                              i === j ? { ...o, duration: Number(v) } : o,
                            ),
                          )
                        }
                      />
                      <Button
                        variant="ghost"
                        aria-label="Remove offer"
                        onClick={() =>
                          setOffers((old) => old.filter((_, j) => j !== i))
                        }
                      >
                        <X />
                      </Button>
                      <div className="demand-support">
                        {(['feature', 'display'] as const).map((flag) => (
                          <label key={flag}>
                            <input
                              type="checkbox"
                              checked={offer[flag]}
                              onChange={(e) =>
                                setOffers((old) =>
                                  old.map((o, j) =>
                                    i === j
                                      ? { ...o, [flag]: e.target.checked }
                                      : o,
                                  ),
                                )
                              }
                            />
                            {flag === 'feature'
                              ? 'Feature / mailer'
                              : 'In-store display'}
                          </label>
                        ))}
                      </div>
                      <div className="demand-offer-mechanics">
                        <OfferEditor
                          value={{ ...emptyOfferConfig, ...offer }}
                          onChange={(v) =>
                            setOffers((old) =>
                              old.map((o, j) => (i === j ? { ...o, ...v } : o)),
                            )
                          }
                          products={catalog}
                          productId={offer.product_id}
                          discount={offer.discount}
                        />
                      </div>
                    </div>
                  ))}
                  <Field
                    label="Additional custom holiday periods (optional, comma separated)"
                    type="text"
                    value={holidayDates}
                    onChange={setHolidayDates}
                    hint="Standard US dates are automatic. Use aligned period-ending dates for additional local occasions; client flags are needed to learn their distinct effects."
                  />
                </div>
              </details>

              {mode === 'demand' && (
                <>
                  <div className="demand-action-row">
                    <div>
                      <h2>Forecast the demand ahead</h2>
                      <p>
                        Recent demand, seasonality, promotion calendar and
                        forecast uncertainty.
                      </p>
                    </div>
                    <Button
                      disabled={!selected.length || !calendar}
                      onClick={() =>
                        run('forecast', async () => {
                          const e = epoch.current;
                          const r = await api<Forecast>(
                            '/demand/forecast',
                            'benchmark',
                            forecastRequest,
                          );
                          if (e === epoch.current) {
                            setForecast(r);
                            setActive(r.series[0]?.product_id || '');
                          }
                        })
                      }
                    >
                      <Play /> Generate demand forecast
                    </Button>
                  </div>
                  {forecast && (
                    <>
                      <OfferEvidence result={forecast} />
                      <div className="save-row">
                        <Button
                          variant="outline"
                          onClick={() =>
                            run('export', () =>
                              download(
                                '/demand/forecast-export',
                                'aislemint-demand-forecast.csv',
                                'benchmark',
                                forecastRequest,
                              ),
                            )
                          }
                        >
                          <Download /> Export demand forecast
                        </Button>
                      </div>
                      <div className="metric-grid three">
                        <Stat
                          label="Forecast demand"
                          value={number(forecast.total_units)}
                          detail={
                            forecast.horizon +
                            ' ' +
                            forecast.frequency +
                            ' periods · selected assortment'
                          }
                          accent
                        />
                        <Stat
                          label="Historical test WAPE"
                          value={percent(summary.model.test.wape)}
                          detail="Multi-step forecast error · lower is better"
                        />
                        <Stat
                          label="90% band coverage"
                          value={percent(summary.model.interval_90_coverage)}
                          detail="Measured on historical test forecasts"
                        />
                      </div>
                      <section className="panel">
                        <div className="section-title">
                          <div>
                            <span className="eyebrow">DEMAND OUTLOOK</span>
                            <h2>{selectedSeries?.name}</h2>
                          </div>
                          <SelectField
                            label="Inspect product"
                            value={selectedSeries?.product_id || ''}
                            onChange={setActive}
                            options={forecast.series.map((s) => ({
                              value: s.product_id,
                              label: s.name,
                            }))}
                          />
                        </div>
                        <div className="demand-chart">
                          <ResponsiveContainer width="100%" height="100%">
                            <ComposedChart
                              data={selectedSeries?.rows}
                              margin={{
                                top: 15,
                                right: 20,
                                left: 0,
                                bottom: 0,
                              }}
                            >
                              <CartesianGrid
                                strokeDasharray="3 5"
                                vertical={false}
                                stroke="#e4ebec"
                              />
                              <XAxis
                                dataKey="date"
                                tickFormatter={shortDate}
                                tick={{ fontSize: 11 }}
                              />
                              <YAxis tick={{ fontSize: 11 }} />
                              <Tooltip
                                labelFormatter={(v) => shortDate(String(v))}
                              />
                              <Area
                                dataKey={(r: Point) => [r.lower, r.upper]}
                                name="90% empirical range"
                                fill="#dcebe5"
                                stroke="none"
                              />
                              <Line
                                dataKey="forecast"
                                name="Forecast units"
                                stroke="#217860"
                                strokeWidth={3}
                                dot={false}
                              />
                            </ComposedChart>
                          </ResponsiveContainer>
                        </div>
                        <p className="micro">
                          Forecasts begin after {forecast.as_of}. Ranges come
                          from historical forecast-error paths; they are not
                          guarantees and can miss stockout-driven lost demand.
                        </p>
                      </section>
                      <section className="panel table-panel">
                        <table className="demand-table">
                          <thead>
                            <tr>
                              <th>Product</th>
                              <th>Recent units / period</th>
                              <th>Forecast units</th>
                              <th>Average forecast / period</th>
                            </tr>
                          </thead>
                          <tbody>
                            {forecast.series.map((s) => (
                              <tr key={s.product_id}>
                                <td>{s.name}</td>
                                <td>{number(s.average_recent)}</td>
                                <td>{number(s.total_units)}</td>
                                <td>
                                  {number(s.total_units / forecast.horizon)}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </section>
                      <details className="panel demand-import">
                        <summary>
                          <Info size={17} /> Forecast assumptions
                        </summary>
                        <div className="demand-import-body">
                          {forecast.assumptions.map((a, i) => (
                            <p className="micro" key={i}>
                              {a}
                            </p>
                          ))}
                        </div>
                      </details>
                    </>
                  )}
                </>
              )}

              {mode === 'inventory' && (
                <>
                  <section className="panel">
                    <div className="section-title">
                      <div>
                        <span className="eyebrow">INVENTORY POSITION</span>
                        <h2>Stock available as of {asOf}</h2>
                        <p className="panel-subtitle">
                          {shopScoped
                            ? 'Enter observed stock and unit costs, or load a matching published inventory / OneSales position.'
                            : 'Enter actual stock and unit costs, or use illustrative inputs to explore the workflow.'}
                        </p>
                      </div>
                      <span
                        className={
                          'pill ' + (basis === 'illustrative' ? 'amber' : '')
                        }
                      >
                        {basis === 'illustrative'
                          ? 'Illustrative inventory'
                          : 'User-supplied inventory'}
                      </span>
                    </div>
                    <div className="save-row">
                      {shopScoped ? (
                        <Button
                          variant="outline"
                          onClick={() =>
                            run('stock', async () => {
                              const data = await api(
                                '/retail/shops/' +
                                  workspace +
                                  '/inventory-position',
                                workspace,
                              );
                              setPositions((old) =>
                                old.map((p) => ({
                                  ...p,
                                  ...(data.positions.find(
                                    (x: any) => x.product_id === p.product_id,
                                  ) || {}),
                                })),
                              );
                              setStockDate(data.as_of);
                              setBasis('user_supplied');
                              setMessage(
                                'Observed stock loaded at the forecast origin. Review missing products, supplier lead times and order constraints.',
                              );
                            })
                          }
                        >
                          Load connected stock
                        </Button>
                      ) : (
                        <Button variant="outline" onClick={loadExample}>
                          Load illustrative stock
                        </Button>
                      )}
                    </div>
                    <Field
                      label="Stock snapshot date"
                      type="date"
                      value={stockDate}
                      onChange={setStockDate}
                      hint={
                        'Must match the latest sales date, ' +
                        asOf +
                        '. Sync updated sales history to plan from a more recent stock snapshot.'
                      }
                    />
                    <div className="demand-table-scroll">
                      <table className="demand-table stock-input-table">
                        <thead>
                          <tr>
                            <th>Product</th>
                            <th>On hand</th>
                            <th>Unit cost (local currency)</th>
                            <th>Lead time (days)</th>
                            <th>Review every (days)</th>
                            <th>Case pack</th>
                            <th>Settings</th>
                          </tr>
                        </thead>
                        <tbody>
                          {positions.map((p) => (
                            <tr key={p.product_id}>
                              <td>
                                <strong>
                                  {
                                    catalog.find(
                                      (c) => c.product_id === p.product_id,
                                    )?.name
                                  }
                                </strong>
                                <small>{p.product_id}</small>
                              </td>
                              {(
                                [
                                  'on_hand',
                                  'unit_cost',
                                  'lead_time_days',
                                  'review_period_days',
                                  'case_pack',
                                ] as const
                              ).map((key) => (
                                <td key={key}>
                                  <Input
                                    aria-label={key + ' for ' + p.product_id}
                                    type="number"
                                    min={
                                      key === 'unit_cost'
                                        ? 0.01
                                        : key === 'case_pack' ||
                                            key === 'review_period_days'
                                          ? 1
                                          : 0
                                    }
                                    step={key === 'unit_cost' ? 0.01 : 1}
                                    value={p[key] ?? ''}
                                    onChange={(e) =>
                                      updatePosition(p.product_id, {
                                        [key]:
                                          e.target.value === ''
                                            ? null
                                            : Number(e.target.value),
                                      })
                                    }
                                  />
                                </td>
                              ))}
                              <td>
                                <Button
                                  variant="ghost"
                                  onClick={() => setActive(p.product_id)}
                                  aria-label={
                                    'More settings for ' + p.product_id
                                  }
                                >
                                  <ArrowRight />
                                </Button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    {position && (
                      <details className="stock-advanced">
                        <summary>
                          Inbound, commitments and shelf life ·{' '}
                          {
                            catalog.find(
                              (c) => c.product_id === position.product_id,
                            )?.name
                          }
                        </summary>
                        <div className="demand-settings-grid">
                          <Field
                            label="Reserved units"
                            value={position.reserved}
                            onChange={(v) =>
                              updatePosition(position.product_id, {
                                reserved: Number(v),
                              })
                            }
                          />
                          <Field
                            label="Backlog units"
                            value={position.backlog}
                            onChange={(v) =>
                              updatePosition(position.product_id, {
                                backlog: Number(v),
                              })
                            }
                          />
                          <Field
                            label="Minimum order (units)"
                            value={position.minimum_order}
                            onChange={(v) =>
                              updatePosition(position.product_id, {
                                minimum_order: Number(v),
                              })
                            }
                          />
                          <Field
                            label="Available-stock limit (units)"
                            value={position.max_stock}
                            onChange={(v) =>
                              updatePosition(position.product_id, {
                                max_stock: v === '' ? null : Number(v),
                              })
                            }
                          />
                          <Field
                            label="New stock shelf life (days)"
                            value={position.shelf_life_days}
                            onChange={(v) =>
                              updatePosition(position.product_id, {
                                shelf_life_days: v === '' ? null : Number(v),
                              })
                            }
                          />
                          <Field
                            label="Existing stock expires"
                            type="date"
                            value={position.existing_expiry_date}
                            onChange={(v) =>
                              updatePosition(position.product_id, {
                                existing_expiry_date: v || null,
                              })
                            }
                          />
                          <Field
                            label="Supplier delay stress (days)"
                            value={position.lead_time_delay_days}
                            onChange={(v) =>
                              updatePosition(position.product_id, {
                                lead_time_delay_days: Number(v),
                              })
                            }
                          />
                        </div>
                        <div className="demand-receipts">
                          <Button
                            variant="outline"
                            disabled={position.receipts.length >= 30}
                            onClick={() =>
                              updatePosition(position.product_id, {
                                receipts: [
                                  ...position.receipts,
                                  {
                                    quantity: 1,
                                    due_date: nextDate,
                                    expiry_date: null,
                                  },
                                ],
                              })
                            }
                          >
                            <Plus /> Add inbound delivery
                          </Button>
                          {position.receipts.map((receipt, i) => (
                            <div className="demand-receipt" key={i}>
                              <Field
                                label={'Delivery ' + (i + 1) + ' quantity'}
                                value={receipt.quantity}
                                min={1}
                                onChange={(v) =>
                                  updatePosition(position.product_id, {
                                    receipts: position.receipts.map((r, j) =>
                                      i === j
                                        ? { ...r, quantity: Number(v) }
                                        : r,
                                    ),
                                  })
                                }
                              />
                              <Field
                                label="Due date"
                                type="date"
                                value={receipt.due_date}
                                onChange={(v) =>
                                  updatePosition(position.product_id, {
                                    receipts: position.receipts.map((r, j) =>
                                      i === j ? { ...r, due_date: v } : r,
                                    ),
                                  })
                                }
                              />
                              <Field
                                label="Expiry date (optional)"
                                type="date"
                                value={receipt.expiry_date || ''}
                                onChange={(v) =>
                                  updatePosition(position.product_id, {
                                    receipts: position.receipts.map((r, j) =>
                                      i === j
                                        ? { ...r, expiry_date: v || null }
                                        : r,
                                    ),
                                  })
                                }
                              />
                              <Button
                                variant="ghost"
                                aria-label={'Remove delivery ' + (i + 1)}
                                onClick={() =>
                                  updatePosition(position.product_id, {
                                    receipts: position.receipts.filter(
                                      (_, j) => i !== j,
                                    ),
                                  })
                                }
                              >
                                <X />
                              </Button>
                            </div>
                          ))}
                        </div>
                      </details>
                    )}
                  </section>

                  <section className="panel demand-settings">
                    <div className="section-title">
                      <div>
                        <h2>Choose the service and investment tradeoff</h2>
                        <p className="panel-subtitle">
                          Higher service targets generally require more safety
                          stock. Budget or shelf-life limits can leave the
                          target unmet.
                        </p>
                      </div>
                    </div>
                    <div className="demand-settings-grid">
                      <Field
                        label="Post-arrival cycle service target (%)"
                        value={service}
                        min={50}
                        max={99.5}
                        step={0.5}
                        onChange={(v) => setService(Number(v))}
                      />
                      <Field
                        label="Purchase budget ($, blank = uncapped)"
                        value={budget}
                        onChange={(v) => setBudget(v === '' ? null : Number(v))}
                      />
                      <Field
                        label="Annual holding cost (%)"
                        value={holding}
                        max={200}
                        onChange={(v) => setHolding(Number(v))}
                      />
                      <Field
                        label="Fixed fee per order (local currency)"
                        value={fee}
                        onChange={(v) => setFee(Number(v))}
                      />
                      <SelectField
                        label="Planning objective"
                        value={objective}
                        onChange={setObjective}
                        options={[
                          {
                            value: 'service',
                            label: 'Protect service within budget',
                          },
                          {
                            value: 'profit',
                            label: 'Maximize expected contribution',
                          },
                        ]}
                      />
                      <Field
                        label="Plan name"
                        type="text"
                        value={name}
                        onChange={setName}
                      />
                    </div>
                  </section>
                  <div className="demand-action-row">
                    <div>
                      <h2>Build the replenishment plan</h2>
                      <p>
                        Recommendations account for timed receipts, demand
                        uncertainty and your constraints.
                      </p>
                    </div>
                    <Button
                      disabled={!selected.length}
                      onClick={() =>
                        run('inventory', async () => {
                          requireStock();
                          const e = epoch.current;
                          const r = await api<Plan>(
                            '/demand/inventory',
                            'benchmark',
                            inventoryRequest,
                          );
                          if (e === epoch.current) {
                            setPlan(r);
                            setForecast(r.forecast);
                            setActive(r.rows[0]?.product_id || '');
                          }
                        })
                      }
                    >
                      <Package /> Calculate replenishment
                    </Button>
                  </div>
                  {plan && (
                    <>
                      <div className="metric-grid">
                        <Stat
                          label="Purchase commitment"
                          value={money(plan.budget_used)}
                          detail={
                            plan.orders + ' order suggestions, including fees'
                          }
                          accent
                        />
                        <Stat
                          label="Expected contribution change"
                          value={money(plan.incremental_contribution)}
                          detail="Versus no new order · protection periods"
                        />
                        <Stat
                          label="Expected lost units"
                          value={number(plan.expected_shortfall)}
                          detail="Through lead time plus review period"
                        />
                        <Stat
                          label="Positions at risk"
                          value={number(plan.stock_risk_positions)}
                          detail="May require expediting or a revised constraint"
                        />
                      </div>
                      <section className="panel">
                        <div className="section-title">
                          <div>
                            <span className="eyebrow">
                              DRAFT ORDER SUGGESTIONS
                            </span>
                            <h2>{plan.name}</h2>
                            <p className="panel-subtitle">
                              Snapshot {plan.as_of} ·{' '}
                              {plan.inventory_basis === 'illustrative'
                                ? 'Illustrative stock assumptions'
                                : 'User-supplied stock'}{' '}
                              · {percent(plan.service_target)} service target
                            </p>
                          </div>
                          <div className="save-row">
                            {!savedId && (
                              <Button
                                variant="outline"
                                onClick={() =>
                                  run('save', async () => {
                                    requireStock();
                                    const r = await api<{
                                      id: string;
                                      result: Plan;
                                    }>(
                                      '/demand/plans',
                                      'benchmark',
                                      inventoryRequest,
                                    );
                                    setSavedId(r.id);
                                    setPlan(r.result);
                                    setSaved(
                                      await api<Saved[]>(
                                        '/demand/plans?source_id=' + source,
                                      ),
                                    );
                                    setMessage(
                                      'Inventory plan saved. No purchase orders were sent.',
                                    );
                                  })
                                }
                              >
                                <Save /> Save plan
                              </Button>
                            )}
                            {savedId && (
                              <Button
                                variant="ghost"
                                onClick={() =>
                                  run('export', () =>
                                    download(
                                      '/demand/plans/' +
                                        savedId +
                                        '/export?source_id=' +
                                        source,
                                      'aislemint-inventory-order-suggestions.csv',
                                    ),
                                  )
                                }
                              >
                                <Download /> Export orders
                              </Button>
                            )}
                          </div>
                        </div>
                        <div className="demand-table-scroll">
                          <table className="demand-table">
                            <thead>
                              <tr>
                                <th>Product / action</th>
                                <th>Order units</th>
                                <th>Purchase cost</th>
                                <th>Safety stock</th>
                                <th>Expected fill rate</th>
                                <th>Post-arrival service</th>
                                <th>Inspect</th>
                              </tr>
                            </thead>
                            <tbody>
                              {plan.rows.map((r) => (
                                <tr key={r.product_id}>
                                  <td>
                                    <strong>{r.name}</strong>
                                    <small
                                      className={
                                        r.first_risk_date ? 'negative' : ''
                                      }
                                    >
                                      {r.action}
                                    </small>
                                  </td>
                                  <td className="order-quantity">
                                    {number(r.order_quantity)}
                                  </td>
                                  <td>{money(r.purchase_cost)}</td>
                                  <td>{number(r.safety_stock)}</td>
                                  <td>{percent(r.fill_rate)}</td>
                                  <td
                                    className={
                                      r.post_arrival_service <
                                      plan.service_target
                                        ? 'negative'
                                        : ''
                                    }
                                  >
                                    {percent(r.post_arrival_service)}
                                  </td>
                                  <td>
                                    <Button
                                      variant="ghost"
                                      onClick={() => setActive(r.product_id)}
                                      aria-label={'Inspect ' + r.name}
                                    >
                                      <ArrowRight />
                                    </Button>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </section>
                      {selectedPlan && (
                        <section className="panel">
                          <div className="section-title">
                            <div>
                              <h2>{selectedPlan.name}</h2>
                              <p className="panel-subtitle">
                                Order arrival boundary{' '}
                                {selectedPlan.arrival_date} · review again{' '}
                                {selectedPlan.next_review_date}
                              </p>
                            </div>
                            <span
                              className={
                                'pill ' +
                                (selectedPlan.first_risk_date
                                  ? 'amber'
                                  : 'green')
                              }
                            >
                              {selectedPlan.action}
                            </span>
                          </div>
                          <div className="demand-chart">
                            <ResponsiveContainer width="100%" height="100%">
                              <ComposedChart
                                data={selectedPlan.projection}
                                margin={{
                                  top: 15,
                                  right: 20,
                                  left: 0,
                                  bottom: 0,
                                }}
                              >
                                <CartesianGrid
                                  strokeDasharray="3 5"
                                  vertical={false}
                                />
                                <XAxis
                                  dataKey="date"
                                  tickFormatter={shortDate}
                                  tick={{ fontSize: 11 }}
                                />
                                <YAxis tick={{ fontSize: 11 }} />
                                <Tooltip
                                  labelFormatter={(v) => shortDate(String(v))}
                                />
                                <Area
                                  dataKey={(r: Projection) => [
                                    r.lower_stock,
                                    r.upper_stock,
                                  ]}
                                  name="Stock range"
                                  fill="#dcebe5"
                                  stroke="none"
                                />
                                <Line
                                  dataKey="ending_stock"
                                  name="Expected ending stock"
                                  stroke="#217860"
                                  strokeWidth={3}
                                  dot={false}
                                />
                                <Line
                                  dataKey="demand"
                                  name="Forecast demand"
                                  stroke="#879cb1"
                                  strokeDasharray="5 5"
                                  dot={false}
                                />
                                <ReferenceLine y={0} stroke="#d78264" />
                              </ComposedChart>
                            </ResponsiveContainer>
                          </div>
                          <p className="micro">
                            Projection includes this order and your listed
                            inbound only. Later review orders are not assumed.
                            Inventory risk is estimated from sampled historical
                            forecast-error paths.
                          </p>
                          <div className="demand-risk-grid">
                            <div>
                              <span>Expected lost units before arrival</span>
                              <strong>
                                {number(selectedPlan.lead_time_shortfall)}
                              </strong>
                            </div>
                            <div>
                              <span>Expected holding cost</span>
                              <strong>
                                {money(selectedPlan.holding_cost)}
                              </strong>
                            </div>
                            <div>
                              <span>Expected expiry writeoff</span>
                              <strong>
                                {money(selectedPlan.expected_writeoff)}
                              </strong>
                            </div>
                          </div>
                          {selectedPlan.notes.map((note, i) => (
                            <p className="demand-note" key={i}>
                              <Info size={16} />
                              {note}
                            </p>
                          ))}
                        </section>
                      )}
                      <details className="panel demand-import">
                        <summary>
                          <Info size={17} /> Planning assumptions
                        </summary>
                        <div className="demand-import-body">
                          {plan.assumptions.map((a, i) => (
                            <p className="micro" key={i}>
                              {a}
                            </p>
                          ))}
                        </div>
                      </details>
                    </>
                  )}
                  {saved.length > 0 && (
                    <section className="panel">
                      <div className="section-title">
                        <h2>Saved inventory decisions</h2>
                        <span className="pill">{saved.length} snapshots</span>
                      </div>
                      <div className="inventory-saved-grid">
                        {saved.map((s) => (
                          <div className="inventory-saved" key={s.id}>
                            <strong>{s.name}</strong>
                            <small>
                              {s.result.as_of} ·{' '}
                              {s.result.inventory_basis === 'illustrative'
                                ? 'Illustrative'
                                : 'User supplied'}
                            </small>
                            <span>
                              {money(s.result.budget_used)} · {s.result.orders}{' '}
                              orders
                            </span>
                            <Button
                              variant="outline"
                              onClick={() => {
                                setPlan(s.result);
                                setForecast(null);
                                setSavedId(s.id);
                                setActive(s.result.rows[0]?.product_id || '');
                                setMessage(
                                  'Viewing the saved snapshot. Change inputs and recalculate to create a new decision.',
                                );
                              }}
                            >
                              View snapshot
                            </Button>
                            <Button
                              variant="ghost"
                              onClick={() =>
                                run('export', () =>
                                  download(
                                    '/demand/plans/' +
                                      s.id +
                                      '/export?source_id=' +
                                      source,
                                    'aislemint-inventory-order-suggestions.csv',
                                  ),
                                )
                              }
                            >
                              <Download /> Export
                            </Button>
                          </div>
                        ))}
                      </div>
                    </section>
                  )}
                </>
              )}

              <details className="panel demand-evidence">
                <summary>
                  <Activity size={17} /> Forecast evidence and model selection
                </summary>
                <div className="demand-import-body">
                  <p>
                    <strong>{summary.model.family}</strong> · trained in{' '}
                    {number(summary.model.training_seconds, 1)} seconds.
                  </p>
                  <p className="micro">
                    {summary.model.uncertainty_protocol ===
                    'separate-selection-absolute-paths-v2'
                      ? 'Candidates are selected before two separate interval calibration windows. '
                      : 'Candidates are selected on two earlier forecast origins. '}
                    The later test predicts multiple periods without updating lag
                    features with future sales. Backtesting treats historical
                    future promotions/prices as a known calendar.
                  </p>
                  <table className="demand-table">
                    <thead>
                      <tr>
                        <th>Candidate</th>
                        {summary.model.uncertainty_protocol ===
                          'separate-selection-absolute-paths-v2' && (
                          <th>Selection WAPE</th>
                        )}
                        <th>Calibration WAPE</th>
                        <th>Test WAPE</th>
                      </tr>
                    </thead>
                    <tbody>
                      {summary.model.candidates.map((c) => (
                        <tr key={c.name}>
                          <td>{c.name}</td>
                          {summary.model?.uncertainty_protocol ===
                            'separate-selection-absolute-paths-v2' && (
                            <td>{percent(c.selection?.wape)}</td>
                          )}
                          <td>{percent(c.calibration.wape)}</td>
                          <td>{percent(c.test.wape)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="micro">
                    Historical 95% cumulative-demand coverage:{' '}
                    {percent(summary.model.protection_95_coverage)}. A selected
                    service target is not a guarantee of achieved service.
                  </p>
                  {summary.model.limitations.map((l, i) => (
                    <p className="micro" key={i}>
                      {l}
                    </p>
                  ))}
                </div>
              </details>
            </>
          )
        )}
      </fieldset>
      {busy && (
        <div className="demand-busy" role="status">
          <LoaderCircle className="spin" size={22} />
          <span>
            {busy === 'inventory'
              ? 'Simulating inventory paths and comparing feasible orders…'
              : busy === 'forecast'
                ? 'Forecasting demand across the selected products…'
                : 'Working…'}
          </span>
        </div>
      )}
    </div>
  );
}
