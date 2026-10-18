'use client';
import {
  createContext,
  useContext,
  useEffect,
  useRef,
  type ReactNode,
} from 'react';
import { X, LoaderCircle, Inbox, Download, Printer } from 'lucide-react';
export const inr = (n: number | null | undefined) =>
  n == null
    ? '—'
    : new Intl.NumberFormat('en-IN', {
        style: 'currency',
        currency: 'INR',
      }).format(n / 100);
export const rate = (n: number | null | undefined) =>
  n == null ? '—' : `${(n * 100).toFixed(1)}%`;
export const toPaise = (value: FormDataEntryValue | null | string) =>
  Math.round(Number(value || 0) * 100);
export const states: Record<string, string> = {
  '01': 'Jammu & Kashmir',
  '02': 'Himachal Pradesh',
  '03': 'Punjab',
  '04': 'Chandigarh',
  '05': 'Uttarakhand',
  '06': 'Haryana',
  '07': 'Delhi',
  '08': 'Rajasthan',
  '09': 'Uttar Pradesh',
  '10': 'Bihar',
  '11': 'Sikkim',
  '12': 'Arunachal Pradesh',
  '13': 'Nagaland',
  '14': 'Manipur',
  '15': 'Mizoram',
  '16': 'Tripura',
  '17': 'Meghalaya',
  '18': 'Assam',
  '19': 'West Bengal',
  '20': 'Jharkhand',
  '21': 'Odisha',
  '22': 'Chhattisgarh',
  '23': 'Madhya Pradesh',
  '24': 'Gujarat',
  '26': 'Dadra & Nagar Haveli and Daman & Diu',
  '27': 'Maharashtra',
  '29': 'Karnataka',
  '30': 'Goa',
  '31': 'Lakshadweep',
  '32': 'Kerala',
  '33': 'Tamil Nadu',
  '34': 'Puducherry',
  '35': 'Andaman & Nicobar Islands',
  '36': 'Telangana',
  '37': 'Andhra Pradesh',
  '38': 'Ladakh',
};
export type PosContextType = {
  error: string;
  session: any;
  ctx: any;
  products: any[];
  customers: any[];
  request: (path: string, body?: unknown) => Promise<any>;
  refresh: () => Promise<void>;
  act: (fn: () => Promise<void>) => Promise<void>;
  busy: boolean;
  notify: (message: string) => void;
  download: (path: string, name: string, print?: boolean) => Promise<void>;
};
export const PosContext = createContext<PosContextType>(null!);
export const usePos = () => useContext(PosContext);
const pendingRecordings = new Map<string, any>();
const retryKey = (s: any) => s.store.id + ':' + s.staff.id;
async function recordingRequest(session: any, suffix = '', body?: any) {
  const r = await fetch('/api/pos/stores/' + session.store.id + '/recording' + suffix, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { Authorization: 'Bearer ' + session.token, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    const data: any = await r.json();
    throw Object.assign(new Error(data.detail || 'Could not preserve this recording.'), {status:r.status, preserveRetry:true});
  }
  return r.json();
}
export async function readRetry(session: any) {
  if (!session) return null;
  const value = await recordingRequest(session);
  pendingRecordings.set(retryKey(session), value);
  return value;
}
export async function saveRetry(session: any, path: string, body: any) {
  const value = await recordingRequest(session, '', {path,body});
  pendingRecordings.set(retryKey(session), value);
  window.dispatchEvent(new Event('onesales-retry'));
}
export async function clearRetry(session: any) {
  const old = pendingRecordings.get(retryKey(session));
  if (old) await recordingRequest(session, '/clear', {idempotency_key:old.body.idempotency_key});
  pendingRecordings.delete(retryKey(session));
  window.dispatchEvent(new Event('onesales-retry'));
}
export const definitiveFailure = (e: any) =>
  e.status >= 400 && e.status < 500 && ![401, 408, 429].includes(e.status);
export function Field({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <label className="os-field">
      <span>{label}</span>
      {children}
    </label>
  );
}
export function StateSelect({
  name = 'state_code',
  value,
  onChange,
  defaultValue = '29',
}: {
  name?: string;
  value?: string;
  onChange?: (v: string) => void;
  defaultValue?: string;
}) {
  return (
    <select
      name={name}
      value={value}
      onChange={onChange ? (e) => onChange(e.target.value) : undefined}
      defaultValue={value === undefined ? defaultValue : undefined}
    >
      {Object.entries(states).map(([k, v]) => (
        <option key={k} value={k}>
          {v} · {k}
        </option>
      ))}
    </select>
  );
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="os-empty">
      <Inbox size={32} />
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
export function Metric({
  label,
  value,
  detail,
  accent = false,
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  accent?: boolean;
}) {
  return (
    <div className={'os-metric ' + (accent ? 'os-accent' : '')}>
      <span>{label}</span>
      <strong>{value}</strong>
      {detail && <small>{detail}</small>}
    </div>
  );
}
export function Panel({
  title,
  aside,
  children,
}: {
  title: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="os-panel">
      <div className="os-panel-title">
        <h3>{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}
export function Modal({
  title,
  onClose,
  children,
  wide = false,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  const context = usePos();
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
    return () => ref.current?.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className={'os-modal ' + (wide ? 'os-wide' : '')}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <div className="os-modal-heading">
        <h2>{title}</h2>
        <button className="os-icon" aria-label="Close dialog" onClick={onClose}>
          <X size={20} />
        </button>
      </div>
      {context?.error && (
        <p className="os-inline-error" role="alert">
          {context.error}
        </p>
      )}
      {children}
    </dialog>
  );
}
export function Action({
  children,
  onClick,
  disabled = false,
  secondary = false,
  type = 'button',
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  secondary?: boolean;
  type?: 'button' | 'submit';
}) {
  return (
    <button
      type={type}
      className={'os-button ' + (secondary ? 'os-secondary' : '')}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
export function ExportButton({
  path,
  name,
  label = 'Export CSV',
}: {
  path: string;
  name: string;
  label?: string;
}) {
  const p = usePos();
  return (
    <Action secondary onClick={() => p.act(() => p.download(path, name))}>
      <Download size={16} />
      {label}
    </Action>
  );
}
export function PrintButton({
  id,
  credit = false,
}: {
  id: string;
  credit?: boolean;
}) {
  const p = usePos();
  return (
    <Action
      secondary
      onClick={() =>
        p.act(() =>
          p.download(
            `/${credit ? 'returns' : 'sales'}/${id}/receipt`,
            'receipt.html',
            true,
          ),
        )
      }
    >
      <Printer size={17} />
      {credit ? 'Print credit note' : 'Print receipt'}
    </Action>
  );
}
export function Loading() {
  return (
    <div className="os-empty">
      <LoaderCircle className="animate-spin" />
      Loading store records…
    </div>
  );
}
export function DataTable({
  headers,
  children,
}: {
  headers: string[];
  children: ReactNode;
}) {
  return (
    <div className="os-table-wrap">
      <table className="os-table">
        <thead>
          <tr>
            {headers.map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
