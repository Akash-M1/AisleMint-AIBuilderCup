import { api } from '@/lib/api';

let startup: Promise<unknown> | undefined;

/** Share startup within a page and serialize first-time cookie creation across tabs. */
export function ensureDemoSession(): Promise<unknown> {
  if (!startup) {
    const connect = () => api('/demo/session', '', {});
    startup = (navigator.locks
      ? navigator.locks.request('aislemint-demo-session', connect)
      : connect()).finally(() => { startup = undefined; });
  }
  return startup;
}
