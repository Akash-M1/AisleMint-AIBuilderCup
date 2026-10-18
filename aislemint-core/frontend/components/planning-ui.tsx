'use client';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { money as formatMoney, number, shortDate } from '@/lib/api';
import { ChartNoAxesCombined, Info, LoaderCircle } from 'lucide-react';
import { type ReactNode } from 'react';
import {
  Area,
  CartesianGrid,
  Tooltip as ChartTooltip,
  ComposedChart,
  Line,
  ReferenceArea,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from 'recharts';

const money = formatMoney;
export const colors = {
  baseline: '#8d9eaa',
  actual: '#217860',
  direct: '#217860',
  cannibalization: '#d78264',
  halo: '#799cdb',
  carryover: '#c99b43',
  pantry_loading: '#df795f',
  post_promo_uplift: '#8295b8',
};
export function Picker({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
}) {
  return (
    <div className="field">
      <label>{label}</label>
      <Select
        value={value}
        onValueChange={(v) => v !== null && onChange(v)}
        items={options}
      >
        <SelectTrigger aria-label={label}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
export function Numeric({
  label,
  value,
  onChange,
  min = 0,
  max,
  step = 1,
  help,
}: {
  label: string;
  value: number | string;
  onChange: (v: string) => void;
  min?: number;
  max?: number;
  step?: number;
  help?: string;
}) {
  return (
    <div className="field">
      <label>{label}</label>
      <Input
        aria-label={label}
        type="number"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      {help && <small>{help}</small>}
    </div>
  );
}
export function Metric({
  label,
  value,
  sub,
  accent = false,
  icon,
}: {
  label: string;
  value: string;
  sub: string;
  accent?: boolean;
  icon?: ReactNode;
}) {
  return (
    <div className={'metric ' + (accent ? 'metric-accent' : '')}>
      <div className="metric-label">
        {label}
        {icon}
      </div>
      <strong>{value}</strong>
      <span>{sub}</span>
    </div>
  );
}
export function Busy({ text = 'Calculating…' }: { text?: string }) {
  return (
    <div className="busy" role="status">
      <LoaderCircle className="spin" size={22} />
      <span>{text}</span>
    </div>
  );
}
export function Empty({
  title,
  text,
  children,
}: {
  title: string;
  text: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <ChartNoAxesCombined size={31} />
      <h2>{title}</h2>
      <p>{text}</p>
      {children}
    </div>
  );
}
export function Effects({
  effects,
  profit = false,
  currency = 'USD',
}: {
  effects: any;
  profit?: boolean;
  currency?: string;
}) {
  const max = Math.max(
    1,
    ...Object.values(effects).map((x) => Math.abs(Number(x))),
  );
  const names: Record<string, string> = {
    direct: 'Direct promotion lift',
    cannibalization: 'Cannibalization',
    halo: 'Halo / positive spillover',
    carryover: 'Net post-promotion effect (legacy)',
    pantry_loading: 'Pantry loading / forward-buy dip (proxy)',
    post_promo_uplift: 'Post-promotion uplift (unvalidated)',
    uncredited_post_promo_gain: 'Positive post-offer credit excluded',
    fixed_cost: 'Execution cost',
    choice_rewards: 'Additional physical choice reward units',
    choice_reward_cost: 'Choice rewards: cost and displaced paid sales',
  };
  return (
    <div className="effects-list">
      {Object.entries(effects).map(([k, v]) => (
        <div className="effect-row" key={k}>
          <span
            className="effect-dot"
            style={{
              background: colors[k as keyof typeof colors] || '#7e8c96',
            }}
          />
          <div className="effect-content">
            <div>
              <span>{names[k] || k}</span>
              <strong className={Number(v) < 0 ? 'negative' : 'positive'}>
                {Number(v) > 0 ? '+' : ''}
                {profit ? money(Number(v), currency) : number(Number(v))}
              </strong>
            </div>
            <div className="effect-track">
              <i
                style={{
                  width: Math.max(1, (Math.abs(Number(v)) / max) * 100) + '%',
                  background: colors[k as keyof typeof colors] || '#7e8c96',
                }}
              />
            </div>
          </div>
        </div>
      ))}
      <p className="micro">
        {'carryover' in effects
          ? 'This older snapshot stores only the net post-promotion effect. A positive net value can hide dips and is not pantry loading. Recalculate to see the separate components.'
          : 'Pantry loading shifts purchases into the offer and reduces later demand. The dip shown is a model proxy, not proof of stockpiling. Positive post-offer effects are shown separately; planning profit and ROI exclude positive post-offer credit. The model follows effects for four weeks and may miss later losses.'}
      </p>
    </div>
  );
}
export function PlanningNote({
  result,
}: {
  result: {
    planning_profit?: number;
    incremental_profit: number;
    excluded_post_promo_profit?: number;
    currency?: string;
  };
}) {
  if (result.planning_profit === undefined)
    return (
      <p className="micro negative">
        Legacy economics include net post-promotion credit. Recalculate this
        plan before using its profit or ROI for a decision.
      </p>
    );
  return (
    <p className="micro">
      Raw model profit: {money(result.incremental_profit, result.currency)}.
      Positive post-offer credit excluded:{' '}
      {money(result.excluded_post_promo_profit ?? 0, result.currency)}. Planning
      profit retains predicted losses. Demand charts show the raw ML forecast;
      this policy adjustment does not establish causal accuracy.
    </p>
  );
}
export function DemandChart({
  data,
  forecast = false,
}: {
  data: any[];
  forecast?: boolean;
}) {
  return (
    <div
      className="chart-box"
      role="img"
      aria-label={
        forecast
          ? 'Weekly baseline, forecast and prediction range'
          : 'Weekly actual sales and estimated no-promotion baseline'
      }
    >
      <ResponsiveContainer
        width="100%"
        height="100%"
        minWidth={0}
        initialDimension={{ width: 600, height: 263 }}
      >
        <ComposedChart
          data={data}
          margin={{ top: 18, right: 16, left: -13, bottom: 5 }}
        >
          <defs>
            <linearGradient id="salesFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#278767" stopOpacity={0.17} />
              <stop offset="100%" stopColor="#278767" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid
            vertical={false}
            stroke="#e9edf1"
            strokeDasharray="3 4"
          />
          <XAxis
            dataKey="date"
            tickFormatter={shortDate}
            tick={{ fontSize: 12, fill: '#7b8795' }}
            axisLine={false}
            tickLine={false}
            minTickGap={30}
            dy={8}
          />
          <YAxis
            tick={{ fontSize: 12, fill: '#7b8795' }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(n) => number(n)}
          />
          <ChartTooltip
            labelFormatter={(label) => shortDate(String(label))}
            formatter={(v: any, name: any) => [
              Array.isArray(v)
                ? v.map((n: number) => number(n)).join('–')
                : number(Number(v), 1),
              name,
            ]}
            contentStyle={{
              border: '1px solid #dfe8e3',
              borderRadius: 10,
              fontSize: 13,
            }}
          />
          {!forecast &&
            data.map((d, i) =>
              d.promoted ? (
                <ReferenceArea
                  key={d.date}
                  x1={d.date}
                  x2={data[Math.min(i + 1, data.length - 1)].date}
                  fill="#f0dbaa"
                  fillOpacity={0.26}
                  strokeOpacity={0}
                />
              ) : null,
            )}
          {forecast && (
            <Area
              name="Prediction range"
              dataKey={(d: any) => [d.lower, d.upper]}
              stroke="none"
              fill="#dae9e3"
              fillOpacity={0.65}
            />
          )}
          <Area
            type="monotone"
            name={forecast ? 'Forecast units' : 'Actual units'}
            dataKey={forecast ? 'forecast' : 'actual'}
            stroke="#217860"
            strokeWidth={2.6}
            fill="url(#salesFill)"
          />
          <Line
            type="monotone"
            name="No-promotion baseline"
            dataKey="baseline"
            stroke="#8d9eaa"
            strokeWidth={2}
            strokeDasharray="5 5"
            dot={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
export function Warnings({ items }: { items: string[] }) {
  return (
    <div className="warnings">
      {items.map((s, i) => (
        <p key={i}>
          <Info size={15} />
          {s}
        </p>
      ))}
    </div>
  );
}
