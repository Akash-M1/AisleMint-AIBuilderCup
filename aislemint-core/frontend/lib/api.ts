import { BRAND } from '@/lib/brand';
const API = '/api';
export async function api<T = any>(
  path: string,
  workspace = 'benchmark',
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(API + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: {
      'X-Workspace-ID': workspace,
      ...(body instanceof FormData
        ? {}
        : { 'Content-Type': 'application/json' }),
    },
    body:
      body === undefined
        ? undefined
        : body instanceof FormData
          ? body
          : JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    const error: any = await response.json().catch(() => ({
      detail: 'The local service could not complete this request.',
    }));
    if (
      response.status === 401 &&
      path !== '/retail/auth/login' &&
      typeof window !== 'undefined'
    )
      window.dispatchEvent(new Event(BRAND.sessionExpiredEvent));
    throw new Error(
      typeof error.detail === 'string'
        ? error.detail
        : JSON.stringify(error.detail),
    );
  }
  return response.json();
}
export async function download(
  path: string,
  filename: string,
  workspace = 'benchmark',
  body?: unknown,
) {
  const response = await fetch(API + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: {
      'X-Workspace-ID': workspace,
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new Error('Could not export this file.');
  const url = URL.createObjectURL(await response.blob());
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
export type Product = {
  product_id: string;
  name: string;
  category: string;
  base_price: number;
  avg_weekly_units: number;
  promo_observations: number;
  min_discount: number;
  max_discount: number;
};
export const number = (n: number | undefined | null, digits = 0) =>
  n == null
    ? '—'
    : new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(
        n,
      );
export const money = (n: number | undefined | null, currency = 'USD') =>
  n == null
    ? '—'
    : new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency,
        maximumFractionDigits: 0,
      }).format(n);
export const percent = (n: number | undefined | null, digits = 1) =>
  n == null ? '—' : `${(n * 100).toFixed(digits)}%`;
export const shortDate = (date: string) =>
  new Date(date + 'T00:00:00').toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
  });
