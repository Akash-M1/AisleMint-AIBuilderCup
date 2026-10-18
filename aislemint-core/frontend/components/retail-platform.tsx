'use client';
import { Brand, BrandMark } from '@/components/brand';
import { api } from '@/lib/api';
import { ensureDemoSession } from '../lib/demo-session';
import { BRAND } from '@/lib/brand';
import { Context, type Session, type Shop } from '@/lib/retail-context';
import { ArrowRight, LogOut, ShieldCheck, Store } from 'lucide-react';
import Link from 'next/link';
import { useContext, useEffect, useState, type ReactNode } from 'react';
import './retail-platform.css';
import { DemoBanner } from '../../../aislemint-platform/frontend/components/demo-mission';
export type { Session, Shop } from '@/lib/retail-context';
export const useRetail = () => useContext(Context);
export async function retail<T = any>(
  path: string,
  body?: unknown,
): Promise<T> {
  return api('/retail' + path, '', body);
}
export function RetailField({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <label className="retail-field">
      <span>{label}</span>
      {children}
    </label>
  );
}
export default function RetailPlatform({
  children,
}: {
  children: (shop: Shop | null) => ReactNode;
}) {
  const [session, setSession] = useState<Session | null>(null),
    [configured, setConfigured] = useState(true),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [email, setEmail] = useState(''),
    [password, setPassword] = useState(''),
    [selected, setSelected] = useState(''),
    [initialPassword, setInitialPassword] = useState('');
  useEffect(() => {
    const expired = () => {
      setSession(null);
      setSelected('');
    };
    window.addEventListener(BRAND.sessionExpiredEvent, expired);
    return () => window.removeEventListener(BRAND.sessionExpiredEvent, expired);
  }, []);
  const refresh = async () => {
    const next = await retail<Session>('/me');
    setSession(next);
  };
  useEffect(() => {
    ensureDemoSession().then(refresh).catch((e) => setError(e.message)).finally(() => setLoading(false));
  }, []);
  const all =
    session?.retailers.flatMap((r) =>
      r.shops.map((s) => ({ ...s, retailerName: r.name })),
    ) || [];
  const chosen = all.find((s) => s.id === selected) || null;
  useEffect(() => {
    if (!session) return;
    if (!selected && all.length) setSelected(all[0].id);
    if (
      selected &&
      selected !== 'benchmark' &&
      !all.some((s) => s.id === selected)
    )
      setSelected(all[0]?.id || '');
  }, [session, selected]);
  if (loading)
    return (
      <div className="retail-login retail-loading" role="status">
        <Brand />
        <h1>Connecting to AisleMint</h1>
        <p>Preparing your private synthetic sandbox…</p>
      </div>
    );
  if (!session) return <main className="retail-login"><div className="retail-login-card"><Brand/><h1>Preparing your demo workspace</h1><p>{error || 'Loading the synthetic retail simulation…'}</p><button onClick={() => window.location.reload()}>Retry</button></div></main>;
  if (session.user.must_change_password)
    return <FirstPassword email={session.user.email} initialPassword={initialPassword}
      complete={async () => { setInitialPassword(''); await refresh(); }} />;
  return (
    <Context.Provider
      value={{ shop: chosen, session, refresh, select: setSelected }}
    >
      <div className="retail-platform"><DemoBanner/>
        <header className="retail-accountbar">
          <span>
            <Store size={18} />
            <strong>{chosen?.retailerName || 'AisleMint platform'}</strong>
          </span>
          <select
            aria-label="Shop workspace"
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
          >
            <option value="" disabled>
              Select a shop
            </option>
            {all.map((s) => (
              <option key={s.id} value={s.id}>
                {s.retailerName} · {s.name}
              </option>
            ))}
            {session.user.admin && (
              <option value="benchmark">
                Demo sandbox · Historical benchmark
              </option>
            )}
          </select>
          <span className="retail-accountname">{session.user.name}</span>

        </header>
        {error && (
          <p className="retail-error" role="alert">
            {error}
          </p>
        )}
        {children(chosen)}
      </div>
    </Context.Provider>
  );
}

export function useAccess() {
  const context = useRetail();
  const levels: Record<string, number> = {
    none: 0,
    viewer: 1,
    user: 2,
    editor: 3,
  };
  const roles =
    context?.session.retailers.find((r) => r.id === context.shop?.retailer_id)
      ?.roles || {};
  return (area: string, level = 'viewer') =>
    Boolean(
      context?.session.user.admin ||
      (levels[roles[area]] || 0) >= (levels[level] || 1),
    );
}

function FirstPassword({ email, initialPassword, complete }: {
  email: string; initialPassword: string; complete: () => Promise<void>;
}) {
  const [current, setCurrent] = useState(initialPassword), [next, setNext] = useState(''),
    [confirm, setConfirm] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  return <main className="retail-login"><div className="retail-login-card">
    <Brand /><p className="retail-eyebrow">MAKE YOURSELF AT HOME</p>
    <h1>Welcome to your workspace.</h1>
    <p>Create a password of your own to finish setting up {email}.</p>
    <form onSubmit={async e => {
      e.preventDefault(); setError('');
      if (next !== confirm) { setError('The new passwords do not match.'); return; }
      setBusy(true);
      try {
        await retail('/auth/password', { current_password: current, new_password: next });
        await retail('/auth/login', { email, password: next });
        setCurrent(''); setNext(''); setConfirm(''); await complete();
      } catch (err: any) { setError(err.message); } finally { setBusy(false); }
    }}>
      <RetailField label="Temporary password from your welcome email"><input type="password"
        autoComplete="current-password" required maxLength={128} value={current} onChange={e => setCurrent(e.target.value)} /></RetailField>
      <RetailField label="Your new password · 12–128 characters"><input type="password"
        autoComplete="new-password" required minLength={12} maxLength={128} value={next} onChange={e => setNext(e.target.value)} /></RetailField>
      <RetailField label="Confirm your new password"><input type="password" autoComplete="new-password"
        required minLength={12} maxLength={128} value={confirm} onChange={e => setConfirm(e.target.value)} /></RetailField>
      {error && <p role="alert" className="retail-error">{error}</p>}
      <button className="retail-primary" disabled={busy}>{busy ? 'Preparing your workspace…' : 'Set password & enter workspace'}<ArrowRight size={16} /></button>
    </form>
    <small>Need help? <a href="mailto:hello@aislemint.com">hello@aislemint.com</a></small>
  </div><aside><BrandMark className="retail-login-mark" /><p className="retail-eyebrow">YOUR NEXT CHAPTER</p>
    <h2>Your plan. Your business. Ready to grow.</h2><p>Your purchased tools and a private workspace are ready. Connect your own data to start planning.</p>
    <div className="retail-login-trust"><ShieldCheck size={20}/><span>This replaces the temporary password and signs out earlier sessions.</span></div>
  </aside></main>;
}
