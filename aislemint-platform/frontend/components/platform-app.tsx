'use client';
import { Brand, BrandMark } from '@/components/brand';
import RetailPlatform, {
  retail,
  RetailField,
  useAccess,
  useRetail,
} from '@/components/retail-platform';
import type { DemandHandoff, ProductArea } from '@/lib/workspace-contracts';
import { connectedAreas } from '@aislemint/connect/navigation';
import {
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  Boxes,
  ChevronRight,
  Database,
  LayoutGrid,
  LockKeyhole,
  Network,
  ShieldCheck,
  Sparkles,
  Store,
  Tag,
} from 'lucide-react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import {
  Component,
  lazy,
  Suspense,
  useEffect,
  useState,
  type ReactNode,
} from 'react';
import './platform.css';
import DemoMission from './demo-mission';
const Promotions = lazy(() => import('@/components/planning-workspace'));
const Demand = lazy(() => import('@/components/demand-planner'));
const Sales = lazy(() => import('@/components/onesales'));
const Workbench = lazy(() => import('@/components/modelling-workbench'));
const Benchmark = lazy(() => import('@/components/benchmark-workbench'));
const Data = lazy(() => import('@/components/data-platform'));
const Analytics = lazy(() => import('@/components/business-analytics'));
const Connect = lazy(() => import('@/components/connected-intelligence'));
const Admin = lazy(() => import('@/components/admin-portal'));
const products = [
  {
    id: 'promotions' as ProductArea,
    name: 'Promotions',
    eyebrow: 'MAKE EVERY OFFER COUNT',
    description:
      'Understand what worked. Plan what comes next. Turn your next promotion into a better decision.',
    icon: Tag,
    color: 'mint',
    tabs: [
      ['Promotion review', '/promotions/review'],
      ['Scenario studio', '/promotions/scenarios'],
      ['Recommendations', '/promotions/recommendations'],
      ['Saved plans', '/promotions/plans'],
    ],
  },
  {
    id: 'demand' as ProductArea,
    name: 'Demand & inventory',
    eyebrow: 'THE RIGHT STOCK, AT THE RIGHT TIME',
    description:
      'Look ahead with demand forecasts and bring availability, replenishment and working capital into balance.',
    icon: Boxes,
    color: 'sand',
    tabs: [
      ['Demand forecast', '/demand/forecast'],
      ['Inventory planner', '/demand/inventory'],
    ],
  },
  {
    id: 'sales' as ProductArea,
    name: 'Sales',
    eyebrow: 'EVERY TRANSACTION, CONNECTED',
    description:
      'A smooth checkout. Simple returns. A clear view of how your business is performing.',
    icon: Store,
    color: 'blue',
    tabs: [
      ['OneSales', '/sales/pos'],
      ['Sales & returns', '/sales/returns'],
      ['Business analytics', '/sales/analytics'],
    ],
  },
  {
    id: 'data' as ProductArea,
    name: 'Data & data science',
    eyebrow: 'BUILD ON TRUSTED EVIDENCE',
    description:
      'Connect your systems, validate your data and manage models built for each of your shops.',
    icon: Database,
    color: 'lilac',
    tabs: [
      ['Modelling workbench', '/data/models'],
      ['Data connections', '/data/connections'],
    ],
  },
  {
    id: 'connect' as ProductArea,
    name: 'Connected intelligence',
    eyebrow: 'SEE THE WHOLE PICTURE',
    description:
      'Connect products, suppliers, stock and decisions. Follow the evidence behind every business question.',
    icon: Network,
    color: 'forest',
    tabs: connectedAreas.map(({ name, path }) => [name, path]),
  },
];
const allTabs = products.flatMap((p) => p.tabs);
class AreaBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <section className="platform-empty" role="alert">
        <h2>This area could not load.</h2>
        <p>Your saved business data is retained. Refresh to reconnect.</p>
        <button onClick={() => window.location.reload()}>
          Reload workspace
        </button>
      </section>
    ) : (
      this.props.children
    );
  }
}
export default function PlatformApp() {
  return (
    <RetailPlatform>
      {(shop) => <Workspace key={shop?.id || 'benchmark'} />}
    </RetailPlatform>
  );
}
function Workspace() {
  const context = useRetail()!,
    can = useAccess(),
    router = useRouter(),
    pathname = usePathname() || '/';
  const subscription = context.session.retailers.find(r => r.id === context.shop?.retailer_id)?.subscription;
  const shop = context.shop,
    workspace = shop?.id || 'benchmark';
  const [handoff, setHandoff] = useState<DemandHandoff | null>(null),
    [salesOpened, setSalesOpened] = useState(false),
    [analyticsMode, setAnalyticsMode] = useState('connected'),
    [aiStatus, setAiStatus] = useState<any>(null),
    [aiModal, setAiModal] = useState(false),
    [apiKeyInput, setApiKeyInput] = useState(''),
    [aiSaving, setAiSaving] = useState(false),
    [aiMsg, setAiMsg] = useState('');
  useEffect(() => {
    retail('/ai/status')
      .then(setAiStatus)
      .catch(() => {});
  }, []);
  const product = products.find((p) =>
    p.tabs.some(([, path]) => path === pathname),
  );
  const activeTab =
    product?.tabs.find(([, path]) => path === pathname)?.[0] || '';
  const connectedArea = connectedAreas.find(({ path }) => path === pathname);
  const isAdmin = pathname === '/admin',
    isAccount = pathname === '/account';
  const manager =
    context.session.user.admin ||
    Object.values(context.session.user.memberships).includes('owner');
  const usable = Boolean(shop || context.session.user.admin);
  useEffect(() => {
    if (pathname === '/' && window.location.hostname === 'admin.aislemint.com')
      router.replace('/admin');
  }, [pathname, router]);
  useEffect(() => {
    if (product?.id === 'sales' && can('sales')) setSalesOpened(true);
  }, [product?.id, can('sales')]);
  function navigate(label: string) {
    const target = allTabs.find(([name]) => name === label);
    if (target) router.push(target[1]);
  }
  if (pathname === '/' || pathname === '/demo') return <DemoMission/>;
  const salesVisible =
    product?.id === 'sales' &&
    (pathname !== '/sales/analytics' || analyticsMode === 'ledger' || !shop);
  return (
    <div className="platform-app">
      <header className="platform-header">
        <Link href="/" aria-label="AisleMint platform home">
          <Brand />
        </Link>
        <div className="platform-header-label">
          {isAdmin ? 'ADMIN CONSOLE' : 'RETAIL OPERATING PLATFORM'}
        </div>
        <span className="platform-live"><Sparkles size={13}/> Gemini · server managed</span>
        <span className="platform-live">
          <i />
          {shop?.name || 'Your workspace'}
        </span>
      </header>
      {aiModal && (
        <div
          role="dialog"
          aria-label="Configure Google Gemini API Key"
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(15, 32, 26, 0.48)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 9999,
            padding: 16,
          }}
        >
          <div
            style={{
              background: '#fff',
              borderRadius: 14,
              maxWidth: 480,
              width: '100%',
              padding: 22,
              border: '1px solid #d7e5dd',
              boxShadow: '0 20px 50px rgba(0,0,0,0.18)',
            }}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: 8,
              }}
            >
              <strong style={{ fontSize: 17, color: '#173e32' }}>
                <Sparkles
                  size={16}
                  style={{ display: 'inline', marginRight: 6 }}
                />
                Google Gemini AI Configuration
              </strong>
              <button
                type="button"
                onClick={() => setAiModal(false)}
                style={{
                  border: 0,
                  background: 'transparent',
                  cursor: 'pointer',
                  fontSize: 18,
                }}
              >
                ×
              </button>
            </div>
            <p style={{ fontSize: 13.5, color: '#567064', lineHeight: 1.5 }}>
              Paste your Google AI Studio <code>GEMINI_API_KEY</code> to unlock
              live multimodal vision (competitor flyers & handwritten lists),
              the Agentic Merchant Copilot, and conversational OneSales basket
              building.
            </p>
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                setAiSaving(true);
                setAiMsg('');
                try {
                  const res = await retail('/ai/configure', {
                    api_key: apiKeyInput.trim(),
                  });
                  setAiStatus(res);
                  setApiKeyInput('');
                  setAiMsg(
                    'Saved! Gemini 3.8 Flash & 3.5 Flash-Lite are now active across all workspaces.',
                  );
                } catch (err: any) {
                  setAiMsg(err.message || 'Could not configure key.');
                } finally {
                  setAiSaving(false);
                }
              }}
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: 10,
                marginTop: 12,
              }}
            >
              <input
                type="password"
                required
                minLength={10}
                value={apiKeyInput}
                onChange={(e) => setApiKeyInput(e.target.value)}
                placeholder={
                  aiStatus?.configured
                    ? `Current key: ${aiStatus.key_preview} (paste new key to replace)`
                    : 'AIzaSy...'
                }
                style={{
                  padding: '10px 12px',
                  borderRadius: 8,
                  border: '1px solid #c9dcd2',
                  fontSize: 14,
                }}
              />
              {aiMsg && (
                <small style={{ color: '#19694f', fontWeight: 600 }}>
                  {aiMsg}
                </small>
              )}
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'flex-end',
                  gap: 8,
                  marginTop: 4,
                }}
              >
                <button
                  type="button"
                  onClick={() => setAiModal(false)}
                  style={{
                    padding: '8px 14px',
                    borderRadius: 8,
                    border: '1px solid #d5e2dc',
                    background: '#fff',
                  }}
                >
                  Close
                </button>
                <button
                  type="submit"
                  disabled={aiSaving}
                  style={{
                    padding: '8px 16px',
                    borderRadius: 8,
                    border: '1px solid #19694f',
                    background: '#19694f',
                    color: '#fff',
                    fontWeight: 600,
                  }}
                >
                  {aiSaving ? 'Saving…' : 'Save & Activate Gemini'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
      {pathname === '/' ? (
        <main className="platform-home">
          <section className="platform-welcome">
            <div>
              <p className="platform-eyebrow">
                YOUR BUSINESS. WORKING TOGETHER.
              </p>
              <h1>
                A clearer view.
                <br />
                <span>A smarter next move.</span>
              </h1>
              <p>
                From your first sale to your next big decision.
                <br className="desktop-break" /> Choose a workspace and get
                straight to what matters.
              </p>
            </div>
            <div className="platform-emblem" aria-hidden="true">
              <div className="emblem-orbit orbit-one" />
              <div className="emblem-orbit orbit-two" />
              <BrandMark />
              <span className="orbit-node node-one">
                <BarChart3 size={20} />
              </span>
              <span className="orbit-node node-two">
                <Boxes size={20} />
              </span>
              <span className="orbit-node node-three">
                <Sparkles size={20} />
              </span>
            </div>
          </section>
          {subscription && usable && <section className="platform-onboarding">
            <div><p className="platform-eyebrow">YOUR {subscription.plan_id.toUpperCase()} ANNUAL PLAN</p>
              <h2>{new Date(subscription.expires_at) <= new Date() ? 'Your annual access has ended.' : 'Your workspace is ready. Bring your data.'}</h2>
              <p>Access through {new Date(subscription.expires_at).toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' })}. No automatic renewal.
                Start with your products, sales history and inventory; your forecasts build on the data you connect.</p>
            </div>
            {can('data') ? <Link href="/data/connections">Connect your business <ArrowRight size={18}/></Link> : <a href="mailto:hello@aislemint.com">Contact us about access <ArrowRight size={18}/></a>}
          </section>}
          {!usable && (
            <section className="platform-onboarding">
              <div>
                <h2>Welcome to your retail platform.</h2>
                <p>
                  {manager
                    ? 'Create your first shop in Administration to connect its data and start planning.'
                    : 'Your retailer owner will add your shop workspace and assign your product roles.'}
                </p>
              </div>
              {manager && (
                <Link href="/admin">
                  Set up a shop <ArrowRight size={18} />
                </Link>
              )}
            </section>
          )}
          <div className="platform-section-title">
            <h2>Your workspaces</h2>
            <span>One business. Five connected perspectives.</span>
          </div>
          <div className="product-grid">
            {products.map(({ icon: Icon, ...p }, index) => {
              const allowed = can(p.id) && usable;
              return (
                <article
                  className={
                    'product-card tone-' +
                    p.color +
                    (!allowed ? ' product-locked' : '')
                  }
                  key={p.id}
                >
                  <div className="product-card-top">
                    <span className="product-icon">
                      <Icon size={24} />
                    </span>
                    <span className="product-index">0{index + 1}</span>
                  </div>
                  <p className="product-eyebrow">{p.eyebrow}</p>
                  <h2>{p.name}</h2>
                  <p className="product-description">{p.description}</p>
                  <div className="product-features">
                    {p.tabs.map(([label]) => (
                      <span key={label}>{label}</span>
                    ))}
                  </div>
                  {allowed ? (
                    <Link
                      className="product-open"
                      href={p.tabs[0][1]}
                      aria-label={'Open ' + p.name}
                    >
                      Open workspace <ArrowUpRight size={20} />
                    </Link>
                  ) : (
                    <div className="product-access">
                      <LockKeyhole size={15} />
                      {usable
                        ? 'Ask your owner for access'
                        : 'A shop workspace is required'}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
          <div className="platform-trust">
            <ShieldCheck size={18} />
            <p>
              Your shops, data and models stay connected within your retailer
              account.
            </p>
            {manager && (
              <Link href="/admin">
                Manage people & access <ArrowRight size={15} />
              </Link>
            )}
          </div>
        </main>
      ) : isAccount ? (
        <AccountPage />
      ) : isAdmin && !manager ? (
        <section className="platform-empty">
          <h1>Retailer owner access required</h1>
          <p>Your retailer owner manages shops and account roles.</p>
          <Link href="/">Return to your workspaces</Link>
        </section>
      ) : isAdmin ? (
        <AreaBoundary key="admin">
          <Suspense fallback={<Loading />}>
            <Admin
              session={context.session}
              refresh={context.refresh}
              close={() => router.push('/')}
              select={(id) => {
                context.select(id);
                router.push('/');
              }}
            />
          </Suspense>
        </AreaBoundary>
      ) : !product ? (
        <section className="platform-empty">
          <h1>Workspace not found</h1>
          <Link href="/">Return to all workspaces</Link>
        </section>
      ) : !can(product.id) || !usable ? (
        <section className="platform-empty">
          <LockKeyhole size={32} />
          <h1>
            {usable
              ? 'This workspace needs access.'
              : 'Select a shop to get started.'}
          </h1>
          <p>
            {usable
              ? 'Ask your retailer owner to assign a role for ' +
                product.name +
                '.'
              : 'Open Administration to set up your first shop.'}
          </p>
          <Link href="/">Back to the platform</Link>
        </section>
      ) : (
        <>
          <section className="product-navigation">
            <div className="product-breadcrumb">
              <Link href="/">
                <LayoutGrid size={16} /> All workspaces
              </Link>
              <ChevronRight size={14} />
              <strong>{product.name}</strong>
              <span className="product-role">
                {context.session.user.admin
                  ? 'Platform administrator'
                  : context.session.user.memberships[
                        shop?.retailer_id || ''
                      ] === 'owner'
                    ? 'Retailer owner'
                    : context.session.retailers.find(
                        (r) => r.id === shop?.retailer_id,
                      )?.roles[product.id] || 'viewer'}
              </span>
            </div>
            <nav aria-label={product.name + ' navigation'}>
              {product.tabs.map(([label, path]) => {
                const Icon =
                  product.id === 'connect'
                    ? connectedAreas.find((item) => item.path === path)?.icon
                    : undefined;
                return (
                  <Link
                    key={path}
                    href={path}
                    aria-current={pathname === path ? 'page' : undefined}
                  >
                    {Icon && <Icon size={16} aria-hidden="true" />}
                    {label}
                  </Link>
                );
              })}
            </nav>
          </section>
          {!can(product.id, 'user') && (
            <p className="platform-role-notice">
              <LockKeyhole size={14} /> Viewer access: you can inspect existing
              results. Running analysis or changing records requires a higher
              role.
            </p>
          )}
          <AreaBoundary key={product.id}>
            <Suspense fallback={<Loading />}>
              {product.id === 'promotions' && (
                <Promotions
                  retailShop={shop}
                  view={activeTab}
                  setView={navigate}
                  onDemandHandoff={setHandoff}
                />
              )}
              {product.id === 'demand' && (
                <section className="platform-feature">
                  <Demand
                    workspace={workspace}
                    shopScoped={!!shop}
                    mode={
                      pathname === '/demand/inventory' ? 'inventory' : 'demand'
                    }
                    handoff={handoff}
                    openWorkbench={() => router.push('/data/models')}
                  />
                </section>
              )}
              {product.id === 'data' && (
                <section className="platform-feature">
                  {pathname === '/data/models' ? (
                    shop ? (
                      <Workbench
                        workspace={workspace}
                        onActivated={() => void context.refresh()}
                      />
                    ) : (
                      <Benchmark />
                    )
                  ) : shop ? (
                    <Data
                      workspace={workspace}
                      openWorkbench={() => router.push('/data/models')}
                      openAnalytics={() => router.push('/sales/analytics')}
                    />
                  ) : (
                    <ShopNeeded />
                  )}
                </section>
              )}
              {product.id === 'connect' && (
                <section className="platform-feature">
                  {shop ? (
                    <Connect
                      workspace={workspace}
                      openWorkbench={() => router.push('/data/models')}
                      navigation={{
                        area: connectedArea?.name ?? 'Operating picture',
                        onChange: (area) => {
                          const target = connectedAreas.find(
                            (item) => item.name === area,
                          );
                          if (target) router.push(target.path);
                        },
                      }}
                    />
                  ) : (
                    <ShopNeeded />
                  )}
                </section>
              )}
              {product.id === 'sales' &&
                pathname === '/sales/analytics' &&
                shop && (
                  <section className="platform-feature">
                    <div
                      className="sales-source-tabs"
                      role="group"
                      aria-label="Analytics source"
                    >
                      <button
                        className={
                          analyticsMode === 'connected' ? 'active' : ''
                        }
                        onClick={() => setAnalyticsMode('connected')}
                      >
                        All connected sources
                      </button>
                      <button
                        className={analyticsMode === 'ledger' ? 'active' : ''}
                        onClick={() => setAnalyticsMode('ledger')}
                      >
                        OneSales ledger
                      </button>
                    </div>
                    {analyticsMode === 'connected' && (
                      <Analytics
                        workspace={workspace}
                        openConnections={() => router.push('/data/connections')}
                      />
                    )}
                  </section>
                )}
            </Suspense>
          </AreaBoundary>
        </>
      )}
      {salesOpened && can('sales') && (
        <div hidden={!salesVisible} className="platform-feature">
          <AreaBoundary key={'sales-' + workspace}>
            <Suspense fallback={<Loading />}>
              <Sales
                visible={salesVisible}
                mode={
                  pathname === '/sales/returns'
                    ? 'sales'
                    : pathname === '/sales/analytics'
                      ? 'analytics'
                      : 'register'
                }
              />
            </Suspense>
          </AreaBoundary>
        </div>
      )}
      <footer className="platform-footer">
        <span>AisleMint</span>
        <span>Good retail instinct. Even better decisions.</span>
      </footer>
    </div>
  );
}
function Loading() {
  return (
    <div className="platform-empty" role="status">
      Loading your workspace…
    </div>
  );
}
function ShopNeeded() {
  return (
    <div className="platform-empty">
      <h2>Select a retailer shop</h2>
      <p>
        This workspace connects to your shop’s own sources. Use the shop
        selector above to begin.
      </p>
    </div>
  );
}
function AccountPage() {
  const [current, setCurrent] = useState(''),
    [password, setPassword] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  return (
    <section className="platform-account">
      <Link href="/">← All workspaces</Link>
      <h1>My account</h1>
      <h2>Change your password</h2>
      <p>Changing your password signs out your existing sessions.</p>
      {error && <p role="alert">{error}</p>}
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await retail('/auth/password', {
              current_password: current,
              new_password: password,
            });
            window.location.reload();
          } catch (e: any) {
            setError(e.message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <RetailField label="Current password">
          <input
            type="password"
            autoComplete="current-password"
            required
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </RetailField>
        <RetailField label="New password · 12–128 characters">
          <input
            type="password"
            autoComplete="new-password"
            minLength={12}
            maxLength={128}
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </RetailField>
        <button className="retail-primary" disabled={busy}>
          {busy ? 'Updating…' : 'Update password & sign out'}
        </button>
      </form>
    </section>
  );
}
