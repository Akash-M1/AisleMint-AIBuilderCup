'use client';
import { BRAND } from '@/lib/brand';
import { useCallback, useEffect, useState, useRef } from 'react';
import {
  Store,
  LockKeyhole,
  ShieldCheck,
  ArrowRight,
  RefreshCw,
  CheckCircle2,
  X,
} from 'lucide-react';
import {
  PosContext,
  Field,
  StateSelect,
  Action,
  Loading,
  Modal,
  readRetry,
  clearRetry,
  definitiveFailure,
  PrintButton,
  inr,
} from './pos/shared';
import Register from './pos/register';
import Operations from './pos/operations';
import Sales from './pos/sales';
import Analytics from './pos/analytics';
import { Brand } from '@/components/brand';
import './pos/onesales.css';
import { useRetail } from '@/components/retail-platform';
const API = '/api/pos';
async function raw(path: string, body?: unknown, token?: string) {
  const r = await fetch(API + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: {
      ...(token ? { Authorization: 'Bearer ' + token } : {}),
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
  });
  if (!r.ok) {
    const e: any = await r
      .json()
      .catch(() => ({ detail: 'The service could not be reached.' }));
    if (e.detail === 'Sign in to your retailer account.')
      window.dispatchEvent(new Event(BRAND.sessionExpiredEvent));
    throw Object.assign(
      new Error(
        typeof e.detail === 'string' ? e.detail : JSON.stringify(e.detail),
      ),
      { status: r.status },
    );
  }
  return r.json() as Promise<any>;
}
export default function OneSales({
  mode,
  visible = true,
}: {
  mode: 'register' | 'sales' | 'analytics';
  visible?: boolean;
}) {
  const [session, setSession] = useState<any>(null),
    [loaded, setLoaded] = useState(false),
    [ctx, setCtx] = useState<any>(null),
    [products, setProducts] = useState<any[]>([]),
    [customers, setCustomers] = useState<any[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [tab, setTab] = useState('Register');
  const latch = useRef(false);
  const [retry, setRetry] = useState<any>(null),
    [recovered, setRecovered] = useState<any>(null);
  useEffect(() => {
    const update = () => {
      void readRetry(session)
        .then(setRetry)
        .catch(() => setRetry(null));
    };
    update();
    window.addEventListener('onesales-retry', update);
    return () => window.removeEventListener('onesales-retry', update);
  }, [session]);
  useEffect(() => {
    fetch('/api/demo/pos-session', {method:'POST', headers:{'Content-Type':'application/json'},body:'{}'})
      .then(async (r) => {if (!r.ok) throw new Error('Could not prepare the demo register.'); return r.json();})
      .then((next) => {setSession(next);setCtx(null);})
      .catch((e) => setError(e.message)).finally(() => setLoaded(true));
  }, []);
  const login = (s: any) => {
    setSession(s);
    setCtx(null);
    setError('');
  };
  const request = useCallback(
    async (path: string, body?: unknown) => {
      try {
        return await raw(
          '/stores/' + session.store.id + path,
          body,
          session.token,
        );
      } catch (e: any) {
        if (e.status === 401) {
          setSession(null);
          setCtx(null);
        }
        throw e;
      }
    },
    [session],
  );
  const refresh = useCallback(async () => {
    if (!session) return;
    const r = await Promise.allSettled([
      request('/context'),
      request('/products'),
      request('/customers'),
    ]);
    for (const x of r) if (x.status === 'rejected') throw x.reason;
    setCtx((r[0] as any).value);
    setProducts((r[1] as any).value);
    setCustomers((r[2] as any).value);
  }, [request, session]);
  useEffect(() => {
    if (visible) refresh().catch((e) => setError(e.message));
  }, [refresh, visible]);
  const act = async (fn: () => Promise<void>) => {
    if (latch.current) return;
    latch.current = true;
    setBusy(true);
    setError('');
    try {
      await fn();
    } catch (e: any) {
      setError(e.message || 'The request could not be completed.');
    } finally {
      setBusy(false);
      latch.current = false;
    }
  };
  const download = async (path: string, name: string, print = false) => {
    const w = print ? window.open('about:blank', '_blank') : null;
    try {
      if (print && !w)
        throw new Error('Allow a new tab to open for the printable receipt.');
      const r = await fetch(API + '/stores/' + session.store.id + path, {
        headers: { Authorization: 'Bearer ' + session.token },
      });
      if (!r.ok)
        throw new Error(
          'Could not export this record. Unlock the store and retry.',
        );
      const u = URL.createObjectURL(await r.blob());
      if (w) {
        w.opener = null;
        w.location.href = u;
        setTimeout(() => URL.revokeObjectURL(u), 300000);
      } else {
        const a = document.createElement('a');
        a.href = u;
        a.download = name;
        a.click();
        setTimeout(() => URL.revokeObjectURL(u), 1000);
      }
    } catch (e) {
      w?.close();
      throw e;
    }
  };
  if (!loaded) return <Loading />;
  if (!session) return <div className="onesales"><p role="alert">{error || 'Preparing your synthetic register…'}</p><button onClick={() => window.location.reload()}>Retry demo register</button></div>;
  return (
    <div className="onesales">
      <PosContext.Provider
        value={{
          session,
          ctx,
          products,
          customers,
          request,
          refresh,
          act,
          busy,
          notify: setNotice,
          download,
          error,
        }}
      >
        {error && (
          <div className="os-message os-error" role="alert">
            {error}
            <button
              className="os-icon"
              aria-label="Dismiss error"
              onClick={() => setError('')}
            >
              <X size={17} />
            </button>
          </div>
        )}
        {notice && (
          <div className="os-message" role="status">
            <CheckCircle2 size={18} />
            {notice}
            <button
              className="os-icon"
              aria-label="Dismiss notice"
              onClick={() => setNotice('')}
            >
              <X size={17} />
            </button>
          </div>
        )}
        {!session ? (
          <Welcome login={login} act={act} busy={busy} />
        ) : !ctx ? (
          <>
            <Loading />
            <Action secondary onClick={() => act(refresh)}>
              Retry connection
            </Action>
          </>
        ) : (
          <>
            <div className="os-storebar">
              <div className="os-storemark">
                <Store size={24} />
              </div>
              <div>
                <strong>{ctx.store.name}</strong>
                <span>
                  {ctx.business_date} · INR ·{' '}
                  {ctx.store.gst_registered ? 'GST registered' : 'Unregistered'}
                </span>
              </div>
              <span className="os-pill">
                {ctx.store.demo ? 'Demo store' : 'Live store'}
              </span>
              <div className="os-spacer" />
              <span className="os-cashier">
                <ShieldCheck size={16} />
                {ctx.staff.name} · {ctx.staff.role}
              </span>
              <button
                className="os-icon"
                aria-label="Refresh store"
                onClick={() => act(refresh)}
              >
                <RefreshCw size={18} />
              </button>
              <Action
                secondary
                onClick={() =>
                  act(async () => {
                    await request('/logout', {});
                    setSession(null);
                    setCtx(null);
                  })
                }
              >
                <LockKeyhole size={16} />
                Lock
              </Action>
            </div>
            {ctx.store.demo && (
              <div className="os-demo">
                Demo inventory, prices and GST rates are illustrative. Offers
                follow the fixed simulation planning date{' '}
                {ctx.store.promo_reference_date}. Every receipt is marked as a
                demo.
              </div>
            )}
            {mode === 'register' && (
              <nav className="os-tabs" aria-label="AisleMint OneSales areas">
                {[
                  'Register',
                  'Catalog & stock',
                  'Customers',
                  'Store settings',
                  'Model data',
                ]
                  .filter(
                    (t) =>
                      ctx.staff.role === 'manager' ||
                      ['Register', 'Customers'].includes(t),
                  )
                  .map((t) => (
                    <button
                      key={t}
                      aria-current={tab === t ? 'page' : undefined}
                      onClick={() => setTab(t)}
                    >
                      {t}
                    </button>
                  ))}
              </nav>
            )}
            {retry && (
              <div className="os-message os-demo" role="status">
                <span>
                  A sale/refund recording is awaiting confirmation. Its retry
                  details are preserved in MongoDB. Unlock this store after a
                  refresh to recover it.
                </span>
                <Action
                  secondary
                  onClick={() =>
                    act(async () => {
                      try {
                        const r = await request(retry.path, retry.body);
                        await clearRetry(session);
                        setRecovered(r);
                        await refresh();
                      } catch (e) {
                        if (definitiveFailure(e)) await clearRetry(session);
                        throw e;
                      }
                    })
                  }
                >
                  Recover recording
                </Action>
              </div>
            )}
            {recovered && (
              <Modal
                title="Recording recovered"
                onClose={() => setRecovered(null)}
              >
                <p>
                  {recovered.number} · {inr(recovered.total_paise)}
                </p>
                <PrintButton id={recovered.id} credit={!!recovered.sale_id} />
                <p className="os-help">
                  This is the original recording, recovered using its retry key.
                </p>
              </Modal>
            )}
            <fieldset disabled={busy} className="os-fieldset">
              <div hidden={mode !== 'register' || tab !== 'Register'}>
                <Register
                  key={session.store.id}
                  onAddCustomer={() => setTab('Customers')}
                />
              </div>
              {mode === 'register' && tab !== 'Register' && (
                <Operations tab={tab} />
              )}
              {mode === 'sales' ? (
                <Sales />
              ) : (
                mode === 'analytics' &&
                visible &&
                (ctx.staff.role === 'manager' ? (
                  <Analytics />
                ) : (
                  <p className="os-message">
                    Business analytics is available to a manager. Lock this
                    session and sign in with a manager PIN.
                  </p>
                ))
              )}
            </fieldset>
            {busy && (
              <div className="os-working" role="status">
                Saving / loading…
              </div>
            )}
          </>
        )}
      </PosContext.Provider>
    </div>
  );
}
function Welcome({
  login,
  act,
  busy,
}: {
  login: (s: any) => void;
  act: (f: () => Promise<void>) => void;
  busy: boolean;
}) {
  const retailContext = useRetail();
  const scopedShop = retailContext?.shop;
  const [stores, setStores] = useState<any[]>([]),
    [store, setStore] = useState(''),
    [staff, setStaff] = useState(''),
    [setup, setSetup] = useState(false),
    [registered, setRegistered] = useState(true);
  useEffect(() => {
    raw('/stores')
      .then((rows) =>
        setStores(
          scopedShop
            ? rows.filter((r: any) => r.id === scopedShop.pos_store_id)
            : rows,
        ),
      )
      .catch(() => {});
  }, []);
  return (
    <div className="os-welcome">
      <div className="os-welcome-copy">
        <div className="os-wordmark">
          <Brand />
          <span className="os-product-name">OneSales</span>
        </div>
        <h2>
          Great service.
          <br />
          Every basket, connected.
        </h2>
        <p>
          Checkout, promotions, inventory and your next planning decision —
          working from the same sales record.
        </p>
        <div className="os-welcome-points">
          <span>01 · Offers apply as the basket qualifies</span>
          <span>02 · Every sale and return stays traceable</span>
          <span>03 · Real trading data feeds your models</span>
        </div>
        <small>Connected retail operations · India / INR</small>
      </div>
      <div className="os-welcome-form">
        <span className="os-eyebrow">YOUR STORE WORKSPACE</span>
        <h2>Ready for the next customer?</h2>
        <p>Unlock your register or set up your first store.</p>
        {stores.filter((s) => !s.demo).length > 0 && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              act(async () =>
                login(
                  await raw('/stores/' + store + '/login', {
                    staff_id: staff,
                    pin: f.get('pin'),
                  }),
                ),
              );
            }}
          >
            <Field label="Store">
              <select
                required
                value={store}
                onChange={(e) => {
                  setStore(e.target.value);
                  setStaff('');
                }}
              >
                <option value="">Choose your store</option>
                {stores
                  .filter((s) => !s.demo)
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
              </select>
            </Field>
            <Field label="Staff member">
              <select
                required
                value={staff}
                onChange={(e) => setStaff(e.target.value)}
              >
                <option value="">Choose staff member</option>
                {stores
                  .find((s) => s.id === store)
                  ?.staff.map((s: any) => (
                    <option key={s.id} value={s.id}>
                      {s.name} · {s.role}
                    </option>
                  ))}
              </select>
            </Field>
            <Field label="PIN">
              <input
                name="pin"
                type="password"
                inputMode="numeric"
                autoComplete="current-password"
                required
                pattern="[0-9]{6,12}"
                placeholder="6–12 digits"
              />
            </Field>
            <Action type="submit" disabled={busy}>
              Unlock register <ArrowRight size={17} />
            </Action>
          </form>
        )}
        {scopedShop ? (
          <p>
            Manage this shop’s register connection in Modelling workbench →
            Connections.
          </p>
        ) : (
          <>
            <Action onClick={() => setSetup(true)} disabled={busy}>
              Set up an Indian store <ArrowRight size={17} />
            </Action>
            <Action
              secondary
              disabled={busy}
              onClick={() => act(async () => login(await raw('/demo', {})))}
            >
              Explore the demo store
            </Action>
            <small>
              The demo starts with sample stock and an empty sales ledger.
            </small>
          </>
        )}
      </div>
      {setup && (
        <Modal title="Set up your store" onClose={() => setSetup(false)}>
          <form
            className="os-form"
            onSubmit={(e) => {
              e.preventDefault();
              const f = new FormData(e.currentTarget);
              act(async () => {
                login(
                  await raw('/stores', {
                    name: f.get('name'),
                    address: f.get('address'),
                    state_code: f.get('state_code'),
                    gst_registered: registered,
                    gstin: registered ? f.get('gstin') : '',
                    owner_name: f.get('owner_name'),
                    pin: f.get('pin'),
                  }),
                );
                setSetup(false);
              });
            }}
          >
            <Field label="Store name">
              <input name="name" required minLength={2} />
            </Field>
            <Field label="Full business address">
              <textarea name="address" required minLength={5} />
            </Field>
            <Field label="Store state">
              <StateSelect />
            </Field>
            <label className="os-check">
              <input
                type="checkbox"
                checked={registered}
                onChange={(e) => setRegistered(e.target.checked)}
              />
              Registered for regular GST
            </label>
            {registered && (
              <Field label="GSTIN">
                <input
                  name="gstin"
                  required
                  minLength={15}
                  maxLength={15}
                  placeholder="15-character business GSTIN"
                />
              </Field>
            )}
            <div className="os-two">
              <Field label="Owner / manager name">
                <input name="owner_name" required minLength={2} />
              </Field>
              <Field label="Your manager PIN">
                <input
                  name="pin"
                  type="password"
                  required
                  pattern="[0-9]{6,12}"
                  inputMode="numeric"
                  autoComplete="new-password"
                  placeholder="6–12 digits"
                />
              </Field>
            </div>
            <p className="os-help">
              Product prices include GST. You supply HSN, applicable GST rates
              and landed cost. Cash and successful external-terminal payments
              are supported. E-invoice submission, cess and GST filing are not
              connected.
            </p>
            <Action type="submit" disabled={busy}>
              Create store
            </Action>
          </form>
        </Modal>
      )}
    </div>
  );
}
