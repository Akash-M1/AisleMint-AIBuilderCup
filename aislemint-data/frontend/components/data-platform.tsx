'use client';
import ConnectedIntelligence from '@/components/connected-intelligence';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
Select,
SelectContent,
SelectItem,
SelectTrigger,
SelectValue,
} from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import {
Table,
TableBody,
TableCell,
TableHead,
TableHeader,
TableRow,
} from '@/components/ui/table';
import { Tabs,TabsContent,TabsList,TabsTrigger } from '@/components/ui/tabs';
import { api } from '@/lib/api';
import {
ArrowRight,
CheckCircle2,
Database,
Download,
GitBranch,
Play,
RefreshCw,
ShieldCheck,
} from 'lucide-react';
import { useEffect,useState } from 'react';
import './data-platform.css';

const empty = {
  revision: 0,
  sales: [] as string[],
  catalog: [] as string[],
  inventory: [] as string[],
  frequency: 'daily',
  sales_overlap: 'reject',
  auto_publish: true,
  auto_train: false,
  profile: 'efficient',
};
const fmt = (n: number | null | undefined) =>
  n == null
    ? 'Not supplied'
    : n.toLocaleString(undefined, { maximumFractionDigits: 1 });
const time = (s: string) => (s ? new Date(s).toLocaleString() : 'Not yet');
const label = (s: string) => (s || 'Awaiting data').replaceAll('_', ' ');
function Badge({ children }: { children: React.ReactNode }) {
  return <span className="dp-badge">{children}</span>;
}

export default function DataPlatform({
  workspace,
  openWorkbench,
  openAnalytics,
}: {
  workspace: string;
  openWorkbench: () => void;
  openAnalytics: () => void;
}) {
  const [data, setData] = useState<any>(null),
    [form, setForm] = useState<any>(empty),
    [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [notice, setNotice] = useState('');
  const call = (path: string, body?: unknown) =>
    api<any>(`/retail/shops/${workspace}/data${path}`, workspace, body);
  const refresh = async () => {
    const r = await call('/overview');
    setData(r);
    return r;
  };
  useEffect(() => {
    let live = true;
    const load = () =>
      call('/overview')
        .then((r) => {
          if (live) setData(r);
        })
        .catch((e) => {
          if (live) setError(e.message);
        });
    void load();
    const timer = setInterval(load, 6000);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [workspace]);
  useEffect(() => {
    if (data && !editing) setForm(data.subscription?.config || empty);
  }, [data, editing]);
  const act = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await fn();
      await refresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const change = (key: string, value: any) => {
    setEditing(true);
    setForm((f: any) => ({ ...f, [key]: value }));
  };
  if (!data)
    return (
      <section className="panel" role="status">
        {error || 'Loading your shared data layer…'}
      </section>
    );
  const release = data.release,
    pending = data.jobs.some((j: any) =>
      ['queued', 'running'].includes(j.status),
    );
  const selected = ['sales', 'catalog', 'inventory'].flatMap((d) => form[d]);
  return (
    <div className="dp-app">
      {error && (
        <div className="retail-error" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="retail-note" role="status">
          {notice}
        </div>
      )}
      {data.demo && (
        <div className="dp-demo">
          <ShieldCheck size={18} /> Demonstration workspace. Sales and inventory
          are simulated; model results on these records do not establish
          accuracy for a real retailer.
        </div>
      )}
      <section className="dp-overview">
        <div className="dp-heading">
          <div>
            <span className="eyebrow">SHARED DATA FOUNDATION</span>
            <h2>Connect once. Power your shop.</h2>
            <p>
              Choose the sources that your shop’s analytics and planning will
              share.
            </p>
          </div>
          <Button
            variant="outline"
            disabled={busy}
            onClick={() =>
              void act(async () => {
                await refresh();
              })
            }
          >
            <RefreshCw size={16} />
            Refresh
          </Button>
        </div>
        <div className="dp-flow" aria-label="Data flow">
          <div>
            <Database />
            <strong>{selected.length} sources</strong>
            <span>Mapped to shop contracts</span>
          </div>
          <ArrowRight />
          <div>
            <ShieldCheck />
            <strong>
              {release ? 'Published release' : 'Awaiting publication'}
            </strong>
            <span>
              {release
                ? `${fmt(release.counts.canonical_sales)} sales observations`
                : 'Validate and seal your data'}
            </span>
          </div>
          <ArrowRight />
          <div>
            <GitBranch />
            <strong>Shared consumers</strong>
            <span>Analytics · models · inventory · ontology</span>
          </div>
        </div>
        <div className="dp-actions">
          <Badge>
            {pending
              ? 'Delivery in progress'
              : data.subscription?.dirty
                ? 'New data awaiting publication'
                : data.subscription
                  ? 'Up to date'
                  : 'Not configured'}
          </Badge>
          <span>
            {release
              ? `History through ${release.date_end || 'No sales selected'} · ${release.id.slice(0, 8)}`
              : 'The first release will appear here.'}
          </span>
          <Button
            disabled={
              busy ||
              pending ||
              !data.subscription ||
              !data.can_write ||
              editing
            }
            onClick={() =>
              void act(async () => {
                await call('/publish', {});
                setNotice(
                  'Publication queued. This page updates automatically.',
                );
              })
            }
          >
            <Play size={16} />
            Validate & publish
          </Button>
        </div>
        {data.subscription?.error && (
          <p className="retail-error" role="alert">
            Latest delivery: {data.subscription.error}{' '}
            {release && 'The last published release remains available.'}
          </p>
        )}
      </section>
      <Tabs defaultValue="routing">
        <TabsList className="dp-tabs">
          <TabsTrigger value="routing">Data routing</TabsTrigger>
          <TabsTrigger value="connections">Connections</TabsTrigger>
          <TabsTrigger value="contracts">Contracts</TabsTrigger>
          <TabsTrigger value="releases">Delivery & lineage</TabsTrigger>
        </TabsList>
        <TabsContent value="routing">
          <section className="panel">
            <div className="dp-heading">
              <div>
                <h2>Sources for this shop</h2>
                <p>
                  Select compatible, team-visible feeds. Every selected feed
                  must complete validation before publication.
                </p>
              </div>
              <Badge>Contract {data.contracts.version}</Badge>
            </div>
            <div className="dp-domains">
              {data.contracts.domains.map((domain: any) => (
                <article key={domain.id}>
                  <h3>
                    {domain.id === 'sales'
                      ? 'Sales history'
                      : domain.id === 'catalog'
                        ? 'Product catalogue'
                        : 'Observed inventory'}
                  </h3>
                  <p>{domain.object_type}</p>
                  <div className="dp-source-list">
                    {data.sources
                      .filter((s: any) => s.object_type === domain.object_type)
                      .map((s: any) => (
                        <label className="dp-source" key={s.id}>
                          <Checkbox
                            aria-label={s.name}
                            checked={form[domain.id].includes(s.id)}
                            disabled={
                              !data.can_manage ||
                              busy ||
                              s.visibility !== 'team'
                            }
                            onCheckedChange={(checked) =>
                              change(
                                domain.id,
                                checked
                                  ? [...form[domain.id], s.id]
                                  : form[domain.id].filter(
                                      (id: string) => id !== s.id,
                                    ),
                              )
                            }
                          />
                          <span>
                            <strong>{s.name}</strong>
                            <small>
                              {s.visibility === 'restricted'
                                ? 'Restricted · cannot share'
                                : label(s.last_status)}{' '}
                              · {time(s.last_sync)}
                              {data.stale_source_ids?.includes(s.id) &&
                                ' · Refresh due'}
                            </small>
                          </span>
                        </label>
                      ))}
                    {!data.sources.some(
                      (s: any) => s.object_type === domain.object_type,
                    ) && (
                      <p className="dp-muted">
                        Create a {domain.object_type} connection in Connections
                        to use this data.
                      </p>
                    )}
                  </div>
                  <small>{domain.consumers.join(' · ')}</small>
                </article>
              ))}
            </div>
            <div className="dp-settings">
              <label>
                Sales cadence
                <Select
                  value={form.frequency}
                  disabled={!data.can_manage || busy}
                  onValueChange={(v) => change('frequency', v)}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="daily">
                      Daily · derive complete promotion weeks
                    </SelectItem>
                    <SelectItem value="weekly">
                      Weekly · period-end observations
                    </SelectItem>
                  </SelectContent>
                </Select>
              </label>
              <label>
                Overlapping sales keys
                <Select
                  value={form.sales_overlap}
                  disabled={!data.can_manage || busy}
                  onValueChange={(v) => change('sales_overlap', v)}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="reject">
                      Reject duplicates (recommended)
                    </SelectItem>
                    <SelectItem value="disjoint_channels">
                      Sum separate sales channels
                    </SelectItem>
                  </SelectContent>
                </Select>
              </label>
              <label>
                Training profile
                <Select
                  value={form.profile}
                  disabled={!data.can_manage || busy}
                  onValueChange={(v) => change('profile', v)}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="efficient">
                      Efficient · local training
                    </SelectItem>
                    <SelectItem value="comparison">
                      Compare model families
                    </SelectItem>
                  </SelectContent>
                </Select>
              </label>
            </div>
            {form.sales_overlap === 'disjoint_channels' && (
              <p className="retail-note">
                You are declaring that each selected source contains different
                sales, such as store and online channels. Every channel must
                cover the same product/date keys with consistent regular prices.
                Connecting an ERP copy of POS sales would double-count revenue.
              </p>
            )}
            <div className="dp-switches">
              <label>
                <Switch
                  checked={form.auto_publish}
                  disabled={!data.can_manage || busy}
                  onCheckedChange={(v) => change('auto_publish', v)}
                />
                <span>Publish automatically after clean source syncs</span>
              </label>
              <label>
                <Switch
                  checked={form.auto_train}
                  disabled={!data.can_manage || busy}
                  onCheckedChange={(v) => change('auto_train', v)}
                />
                <span>
                  Train candidates when history passes readiness checks
                </span>
              </label>
            </div>
            <p className="dp-muted">
              Models remain isolated to this shop. A shop owner reviews
              validation results and activates a passing candidate in Modelling
              workbench.
            </p>
            {data.can_manage && (
              <Button
                disabled={busy || !selected.length}
                onClick={() =>
                  void act(async () => {
                    await call('/subscription', form);
                    setEditing(false);
                    setNotice(
                      'Data routing saved. Automatic publication follows the policy you selected.',
                    );
                  })
                }
              >
                Save data routing
              </Button>
            )}
          </section>
          <section className="panel">
            <div className="dp-heading">
              <h2>Where this data is used</h2>
              <Button variant="outline" onClick={openWorkbench}>
                Modelling workbench
                <ArrowRight size={16} />
              </Button>
            </div>
            <div className="dp-consumers">
              <article>
                <CheckCircle2 />
                <h3>Business analytics</h3>
                <Badge>{release ? 'Available' : 'Awaiting release'}</Badge>
                <p>
                  Sales, refunds, products, categories and stock from all
                  selected sources.
                </p>
                <Button variant="outline" onClick={openAnalytics}>
                  Open analytics
                </Button>
              </article>
              {['demand', 'promotion'].map((kind) => {
                const c = release?.consumers?.[kind];
                return (
                  <article key={kind}>
                    <GitBranch />
                    <h3>
                      {kind === 'demand'
                        ? 'Demand & inventory'
                        : 'Promotion planning'}
                    </h3>
                    <Badge>{label(c?.status)}</Badge>
                    <p>
                      {c
                        ? `${fmt(c.report.quality.rows)} observations · ${fmt(c.report.quality.products)} products`
                        : 'History readiness is checked on each release.'}
                    </p>
                    {c?.run_id && (
                      <small>
                        Training {label(c.run_status || 'queued')}:{' '}
                        {c.run_id.slice(0, 8)}
                        {c.gate_passed === false
                          ? ' · review failed checks'
                          : ''}
                      </small>
                    )}
                    {c?.excluded_partial_week_rows > 0 && (
                      <small>
                        {fmt(c.excluded_partial_week_rows)} daily observations
                        in incomplete weeks excluded.
                      </small>
                    )}
                    {c?.report.issues?.slice(0, 3).map((s: string) => (
                      <p key={s} className="dp-issue">
                        {s}
                      </p>
                    ))}
                    <small>
                      {data.active_models[kind]
                        ? `Serving model ${data.active_models[kind].slice(0, 8)}`
                        : 'No model activated'}
                    </small>
                  </article>
                );
              })}
              <article>
                <Database />
                <h3>Inventory & ontology</h3>
                <Badge>
                  {release?.counts.inventory
                    ? 'Stock published'
                    : 'Stock not selected'}
                </Badge>
                <p>
                  Use “Load connected stock” in Inventory planner. Stock dates
                  must match the demand model’s forecast origin.
                </p>
                <small>
                  Relationships and source evidence remain available in
                  Connected intelligence.
                </small>
              </article>
            </div>
          </section>
        </TabsContent>
        <TabsContent value="connections">
          <ConnectedIntelligence
            workspace={workspace}
            openWorkbench={openWorkbench}
            connectionsOnly
          />
        </TabsContent>
        <TabsContent value="contracts">
          <section className="panel">
            <div className="dp-heading">
              <h2>Explicit contracts, reusable data</h2>
              <Button
                variant="outline"
                onClick={() => {
                  const url = URL.createObjectURL(
                    new Blob([JSON.stringify(data.contracts, null, 2)], {
                      type: 'application/json',
                    }),
                  );
                  const a = document.createElement('a');
                  a.href = url;
                  a.download = 'retail-data-contracts.json';
                  a.click();
                  URL.revokeObjectURL(url);
                }}
              >
                <Download size={16} />
                Download JSON contract
              </Button>
            </div>
            {data.contracts.domains.map((d: any) => (
              <article className="dp-contract" key={d.id}>
                <h3>{d.object_type}</h3>
                <p>{d.semantics}</p>
                <small>Unique key: {d.grain.join(' + ')}</small>
              </article>
            ))}
            <h3>Required sales fields</h3>
            <div className="dp-field-tags">
              {data.contracts.sales.row_schema.required.map((f: string) => (
                <code key={f}>{f}</code>
              ))}
            </div>
            <h3>Example observation</h3>
            <pre className="dp-json">
              {JSON.stringify(data.contracts.sales.example, null, 2)}
            </pre>
            <h3>Publication rules</h3>
            <ul className="dp-rules">
              {data.contracts.policies.map((p: string) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          </section>
        </TabsContent>
        <TabsContent value="releases">
          <section className="panel">
            <h2>Durable deliveries</h2>
            <p>
              Retries reuse sealed data. A changed source creates a new
              generation; it cannot overwrite a newer publication.
            </p>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Requested</TableHead>
                  <TableHead>Generation</TableHead>
                  <TableHead>Delivery</TableHead>
                  <TableHead>Result</TableHead>
                  <TableHead>Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.jobs.map((j: any) => (
                  <TableRow key={j.id}>
                    <TableCell>{time(j.created)}</TableCell>
                    <TableCell>{j.generation}</TableCell>
                    <TableCell>
                      <Badge>{label(j.status)}</Badge>
                    </TableCell>
                    <TableCell>
                      {j.error ||
                        j.release_id?.slice(0, 8) ||
                        'Waiting for worker'}
                    </TableCell>
                    <TableCell>
                      {j.status === 'failed' &&
                        data.can_write &&
                        j.generation === data.subscription?.generation && (
                          <Button
                            variant="outline"
                            disabled={busy}
                            onClick={() =>
                              void act(async () => {
                                await call(`/jobs/${j.id}/retry`, {});
                              })
                            }
                          >
                            Retry delivery
                          </Button>
                        )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {!data.jobs.length && <p>No publication requested yet.</p>}
          </section>
          <section className="panel">
            <h2>Release history</h2>
            {data.releases.map((r: any) => (
              <details className="dp-contract" key={r.id}>
                <summary>
                  <strong>{r.id.slice(0, 8)}</strong> ·{' '}
                  {fmt(r.counts.canonical_sales)} sales observations ·{' '}
                  {time(r.created)} {r.id === release?.id ? '· Current' : ''}
                </summary>
                <p>
                  SHA-256: <code className="dp-hash">{r.content_sha256}</code>
                </p>
                {r.lineage.map((s: any) => (
                  <p key={s.source_id}>
                    {s.name} · mapping v{s.mapping_revision} · sync{' '}
                    {s.run_id.slice(0, 8)}
                  </p>
                ))}
                <small>
                  Snapshots:{' '}
                  {Object.values(r.consumers || {})
                    .map((c: any) => c.snapshot_id.slice(0, 8))
                    .join(', ') || 'No sales snapshots'}
                </small>
              </details>
            ))}
          </section>
        </TabsContent>
      </Tabs>
    </div>
  );
}
