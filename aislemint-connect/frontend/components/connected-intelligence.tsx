'use client';
import { useEffect, useState } from 'react';
import {
  ArrowRight,
  Check,
  Database,
  Layers,
  Play,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  X,
} from 'lucide-react';
import { api } from '@/lib/api';
import {
  useAccess,
  useRetail,
  RetailField,
} from '@/components/retail-platform';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from '@/components/ui/dialog';
import './connected-intelligence.css';
import { connectedAreas, type ConnectedArea } from './navigation';

const key = () => crypto.randomUUID();
const time = (s: string) =>
  s ? new Date(s).toLocaleString() : 'Not synced yet';
const status = (s: string) => s?.replaceAll('_', ' ');
const sourceBody = (s: any) =>
  Object.fromEntries(
    [
      'name',
      'kind',
      'binding',
      'object_type',
      'id_field',
      'mapping',
      'visibility',
      'freshness_hours',
      'schedule_minutes',
      'enabled',
      'revision',
    ].map((k) => [k, s[k]]),
  );
export default function ConnectedIntelligence({
  workspace,
  openWorkbench,
  connectionsOnly = false,
  navigation,
}: {
  workspace: string;
  openWorkbench: () => void;
  connectionsOnly?: boolean;
  navigation?: { area: ConnectedArea; onChange: (area: ConnectedArea) => void };
}) {
  const retail = useRetail();
  const can = useAccess();
  const [localArea, setLocalArea] = useState<ConnectedArea>(
      connectionsOnly ? 'Connections' : 'Operating picture',
    ),
    [data, setData] = useState<any>(null),
    [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [busy, setBusy] = useState(false);
  const area = connectionsOnly
    ? 'Connections'
    : (navigation?.area ?? localArea);
  const setArea = (next: ConnectedArea) => {
    if (navigation) navigation.onChange(next);
    else setLocalArea(next);
  };
  const [selected, setSelected] = useState<any>(null),
    [search, setSearch] = useState(''),
    [type, setType] = useState(''),
    [objects, setObjects] = useState<any[]>([]),
    [next, setNext] = useState<string | null>(null);
  const [runs, setRuns] = useState<any[]>([]),
    [runNext, setRunNext] = useState<string | null>(null),
    [issues, setIssues] = useState<any[]>([]),
    [issueNext, setIssueNext] = useState<string | null>(null);
  const [question, setQuestion] = useState('Which products have low stock?'),
    [answer, setAnswer] = useState<any>(null),
    [mappingSource, setMappingSource] = useState<any>(null),
    [creatingType, setCreatingType] = useState<any>(null);
  const call = <T = any,>(path: string, body?: unknown) =>
    api<T>(`/retail/shops/${workspace}/connected${path}`, workspace, body);
  const refresh = async () => {
    const result = await call('/overview');
    setData(result);
  };
  const act = async (fn: () => Promise<void>, skipRefresh = false) => {
    if (busy) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await fn();
      if (!skipRefresh) await refresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const runAsk = (q: string) => {
    setQuestion(q);
    void act(async () => {
      setAnswer(await call('/ask', { question: q, use_gemini: true }));
    }, true);
  };
  useEffect(() => {
    let live = true;
    call('/overview')
      .then((r) => {
        if (live) setData(r);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    const timer = setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return;
      call('/overview')
        .then((r) => {
          if (live) setData(r);
        })
        .catch(() => {});
    }, 45000);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [workspace]);
  useEffect(() => {
    if (area !== 'Ontology') return;
    const controller = new AbortController();
    const t = setTimeout(() => {
      api<any>(
        `/retail/shops/${workspace}/connected/objects?type=${encodeURIComponent(type)}&q=${encodeURIComponent(search)}`,
        workspace,
        undefined,
        controller.signal,
      )
        .then((r) => {
          setObjects(r.items);
          setNext(r.next_cursor);
        })
        .catch((e) => {
          if (e.name !== 'AbortError') setError(e.message);
        });
    }, 200);
    return () => {
      clearTimeout(t);
      controller.abort();
    };
  }, [area, type, search, workspace]);
  useEffect(() => {
    if (area !== 'Pipelines') return;
    let live = true;
    Promise.all([call('/runs'), call('/quarantine')])
      .then(([r, q]) => {
        if (live) {
          setRuns(r.items);
          setRunNext(r.next_cursor);
          setIssues(q.items);
          setIssueNext(q.next_cursor);
        }
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [
    area,
    data?.runs?.map((r: any) => r.id + r.status + r.processed).join('|'),
  ]);
  const openObject = async (id: string) => {
    try {
      setSelected(await call('/objects/' + id));
    } catch (e: any) {
      setError(e.message);
    }
  };
  const writer = can(connectionsOnly ? 'data' : 'connect', 'editor');
  if (!data)
    return (
      <div className="ci-empty" role="status">
        {error || 'Loading connected intelligence…'}
      </div>
    );
  const objectCount = Object.values(data.object_counts).reduce(
    (a: number, b: any) => a + Number(b),
    0,
  );
  return (
    <section className="ci-app">
      <header className="ci-heading">
        <div>
          <span className="ci-eyebrow">AISLEMINT CONNECT</span>
          <h2>
            {connectionsOnly
              ? 'Manage your shop’s connections'
              : 'From scattered data to connected decisions.'}
          </h2>
          <p>
            {connectionsOnly
              ? 'These connections are shared with Data routing and Connected intelligence.'
              : `One operating picture for ${retail?.shop?.name || 'your shop'}.`}
          </p>
        </div>
        <button
          className="ci-button"
          disabled={busy}
          onClick={() => void act(refresh)}
        >
          <RefreshCw size={16} /> Refresh
        </button>
      </header>
      {data.demo && !connectionsOnly && (
        <aside
          className="ci-demo-banner"
          aria-label="Demonstration data notice"
        >
          <div>
            <span className="ci-eyebrow">
              DEMO WORKSPACE · {data.demo.as_of}
            </span>
            <strong>A connected grocery business, ready to explore.</strong>
            <p>{data.demo.note}</p>
          </div>
          <div className="ci-demo-credits">
            <span>Public reference sources</span>
            {data.demo.sources.map((source: any) => (
              <a
                key={source.name}
                href={source.reference}
                target="_blank"
                rel="noreferrer"
              >
                {source.name} <ArrowRight size={13} />
              </a>
            ))}
          </div>
        </aside>
      )}
      {!connectionsOnly && !navigation && (
        <nav className="ci-nav" aria-label="Connected intelligence areas">
          {connectedAreas.map(({ name, icon: Icon }) => (
            <button
              key={name}
              aria-current={area === name ? 'page' : undefined}
              onClick={() => setArea(name)}
            >
              <Icon size={17} />
              {name}
            </button>
          ))}
        </nav>
      )}
      {error && (
        <div className="ci-alert" role="alert">
          {error}
          <button onClick={() => setError('')} aria-label="Dismiss error">
            <X size={16} />
          </button>
        </div>
      )}
      {notice && (
        <div className="ci-notice" role="status">
          <Check size={16} />
          {notice}
        </div>
      )}
      {area === 'Operating picture' && (
        <>
          <div className="ci-metrics">
            {[
              [data.sources.length, 'Connected sources'],
              [objectCount, 'Business objects'],
              [data.risks.counts.low_stock, 'Low-stock records'],
              [data.risks.counts.overdue, 'Overdue records'],
            ].map(([value, label]) => (
              <div key={label}>
                <span>{label}</span>
                <strong>{Number(value).toLocaleString()}</strong>
              </div>
            ))}
          </div>
          {data.demo && (
            <section
              className="ci-demo-tour"
              aria-label="Client demonstration walkthrough"
            >
              <div className="ci-panel-title">
                <h3>Follow the evidence</h3>
                <span className="ci-badge">Guided walkthrough</span>
              </div>
              <div className="ci-demo-stories">
                {data.demo.stories.map((story: any, index: number) => (
                  <button
                    key={story.object_id}
                    onClick={() => void openObject(story.object_id)}
                  >
                    <span>{String(index + 1).padStart(2, '0')}</span>
                    <strong>{story.title}</strong>
                    <p>{story.detail}</p>
                    <ArrowRight size={17} />
                  </button>
                ))}
              </div>
              <div className="ci-demo-actions">
                <button
                  className="ci-button"
                  onClick={() => setArea('Pipelines')}
                >
                  Inspect the quality-check example
                </button>
                <button
                  className="ci-button"
                  onClick={() => setArea('Decisions')}
                >
                  Review demo proposals
                </button>
              </div>
            </section>
          )}
          <div className="ci-columns">
            <section className="ci-panel">
              <div className="ci-panel-title">
                <h3>What needs attention</h3>
                <span className="ci-badge">Observed evidence</span>
              </div>
              <p className="ci-muted">
                Stock thresholds and due dates from your connected systems.
                Demand predictions remain in Demand forecast.
              </p>
              {!data.risks.findings.length && (
                <Empty
                  title="No matching evidence yet"
                  text="Connect inventory and order data to surface low-stock and delivery risks. An empty list does not establish that there are no risks."
                  action={() => setArea('Connections')}
                  label="Connect data"
                />
              )}
              {data.risks.findings.map((r: any) => (
                <button
                  className="ci-finding"
                  key={r.kind + r.object_id}
                  onClick={() => void openObject(r.object_id)}
                >
                  <span className={'ci-dot ' + r.kind} />
                  <div>
                    <strong>{r.label}</strong>
                    <p>{r.detail}</p>
                    <small>Observed {time(r.observed_at)}</small>
                  </div>
                  <ArrowRight size={17} />
                </button>
              ))}
            </section>
            <section className="ci-panel">
              <h3>Data health</h3>
              <p className="ci-muted">
                Freshness is measured against each connection’s expected update
                interval.
              </p>
              {!data.sources.length && <p>No sources connected yet.</p>}
              {data.sources.map((s: any) => {
                const stale = data.risks.stale_sources.some(
                  (r: any) => r.source_id === s.id,
                );
                return (
                  <div className="ci-health" key={s.id}>
                    <span
                      className={'ci-dot ' + (stale ? 'overdue' : 'healthy')}
                    />
                    <div>
                      <strong>{s.name}</strong>
                      <small>{time(s.last_sync)}</small>
                    </div>
                    <span className="ci-badge">
                      {!s.enabled ? 'Paused' : stale ? 'Needs data' : 'Current'}
                    </span>
                  </div>
                );
              })}
              <div className="ci-callout">
                <ShieldCheck size={19} />
                <p>
                  Insights, linked objects, and assistant evidence follow your
                  account’s source permissions.
                </p>
              </div>
              <button className="ci-button" onClick={openWorkbench}>
                Open Modelling workbench <ArrowRight size={15} />
              </button>
            </section>
          </div>
        </>
      )}
      {area === 'Connections' && (
        <div className="ci-columns">
          <div>
            <section className="ci-panel">
              <h3>Your connections</h3>
              <p className="ci-muted">
                Each connection maps an authoritative source into a business
                object type.
              </p>
              {!data.sources.length && (
                <Empty
                  title="Start with the systems you already use"
                  text="Connect OneSales directly, or register an external adapter. No CSV uploads are needed."
                />
              )}
              {data.sources.map((s: any) => (
                <article className="ci-source" key={s.id}>
                  <div className="ci-panel-title">
                    <div>
                      <strong>{s.name}</strong>
                      <small>
                        {s.kind} → {s.object_type}
                      </small>
                    </div>
                    <span className="ci-badge">
                      {s.visibility === 'restricted'
                        ? 'Owner only'
                        : s.enabled
                          ? 'Team access'
                          : 'Paused'}
                    </span>
                  </div>
                  <p className="ci-muted">
                    {time(s.last_sync)} · Mapping v{s.revision}
                    {s.schedule_minutes
                      ? ` · Every ${s.schedule_minutes} min`
                      : ''}
                  </p>
                  <div className="ci-actions">
                    {s.kind !== 'push' && writer && (
                      <button
                        className="ci-button"
                        disabled={busy || !s.enabled}
                        onClick={() =>
                          void act(async () => {
                            await call('/sources/' + s.id + '/sync', {
                              request_key: key(),
                            });
                            setNotice(
                              'Sync queued. Follow its progress in Pipelines.',
                            );
                          })
                        }
                      >
                        <Play size={14} /> Sync now
                      </button>
                    )}
                    {data.can_manage && (
                      <>
                        <button
                          className="ci-button"
                          onClick={() => setMappingSource(s)}
                        >
                          Mapping & setup
                        </button>
                        <button
                          className="ci-button"
                          disabled={busy}
                          onClick={() =>
                            void act(async () => {
                              await call('/sources/' + s.id + '/configure', {
                                ...sourceBody(s),
                                enabled: !s.enabled,
                              });
                            })
                          }
                        >
                          {s.enabled ? 'Pause' : 'Resume'}
                        </button>
                      </>
                    )}
                    {s.object_type === 'SalesObservation' && writer && (
                      <>
                        <button
                          className="ci-button"
                          disabled={busy}
                          onClick={() =>
                            void act(async () => {
                              const r = await call(
                                '/sources/' + s.id + '/sales-snapshot',
                                {
                                  request_key: key(),
                                  kind: 'demand',
                                  frequency: 'daily',
                                },
                              );
                              setNotice(
                                `Demand snapshot: ${status(r.status)}. Review its data checks in Modelling workbench.`,
                              );
                            })
                          }
                        >
                          Daily demand snapshot
                        </button>
                        <button
                          className="ci-button"
                          disabled={busy}
                          onClick={() =>
                            void act(async () => {
                              const r = await call(
                                '/sources/' + s.id + '/sales-snapshot',
                                {
                                  request_key: key(),
                                  kind: 'promotion',
                                  frequency: 'weekly',
                                },
                              );
                              setNotice(
                                `Promotion snapshot: ${status(r.status)}. Review its data checks in Modelling workbench.`,
                              );
                            })
                          }
                        >
                          Weekly promotion snapshot
                        </button>
                      </>
                    )}
                  </div>
                </article>
              ))}
            </section>
            <section className="ci-panel ci-space">
              <h3>Connector coverage</h3>
              <div className="ci-catalog">
                {data.catalog.map((c: any) => (
                  <div key={c.kind}>
                    <strong>{c.name}</strong>
                    <p>{c.scope}</p>
                    <small>{c.setup}</small>
                  </div>
                ))}
              </div>
            </section>
          </div>
          <section className="ci-panel ci-sticky">
            {data.can_manage ? (
              <CreateSource
                data={data}
                busy={busy}
                onSave={(body: any) =>
                  void act(async () => {
                    await call('/sources', body);
                    setNotice(
                      'Connection created. Configure its mapping, then start a sync or send a batch.',
                    );
                  })
                }
              />
            ) : (
              <Empty
                title="Connections are managed by your retailer owner"
                text="You can explore sources shared with your team."
              />
            )}
          </section>
        </div>
      )}
      {area === 'Ontology' && (
        <>
          <div className="ci-panel ci-toolbar">
            <RetailField label="Object type">
              <select value={type} onChange={(e) => setType(e.target.value)}>
                <option value="">All object types</option>
                {data.types.map((t: any) => (
                  <option key={t.name}>{t.name}</option>
                ))}
              </select>
            </RetailField>
            <RetailField label="Search objects and document text">
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Product, supplier, order ID…"
              />
            </RetailField>
            {data.can_manage && (
              <button
                className="ci-button"
                onClick={() => setCreatingType(true)}
              >
                <Plus size={16} /> Define object type
              </button>
            )}
          </div>
          <div className="ci-columns">
            <section className="ci-panel">
              <h3>Object explorer</h3>
              <p className="ci-muted">
                Open an object to traverse relationships and inspect its source
                evidence.
              </p>
              {!objects.length && (
                <Empty
                  title="No objects matched"
                  text="Connect data or change your search. Types define the structure; objects appear when valid records arrive."
                />
              )}
              {objects.map((o: any) => (
                <button
                  className="ci-finding"
                  key={o.id}
                  onClick={() => void openObject(o.id)}
                >
                  <Layers size={20} />
                  <div>
                    <strong>{o.label}</strong>
                    <p>
                      {o.type} · {o.external_id}
                    </p>
                  </div>
                  <span className="ci-badge">v{o.revision}</span>
                  <ArrowRight size={16} />
                </button>
              ))}
              {next && (
                <button
                  className="ci-button"
                  disabled={busy}
                  onClick={() =>
                    void act(async () => {
                      const r = await call(
                        `/objects?after=${next}&type=${encodeURIComponent(type)}&q=${encodeURIComponent(search)}`,
                      );
                      setObjects([...objects, ...r.items]);
                      setNext(r.next_cursor);
                    })
                  }
                >
                  Load more objects
                </button>
              )}
            </section>
            <section className="ci-panel">
              <h3>Business vocabulary</h3>
              <p className="ci-muted">
                Typed properties, stable identities, and explicit relationships.
                Matching names alone never merges records.
              </p>
              {data.types
                .filter((t: any) => !type || t.name === type)
                .map((t: any) => (
                  <details className="ci-contract" key={t.name}>
                    <summary>
                      {t.name}
                      <span>v{t.revision}</span>
                    </summary>
                    <p>{t.description}</p>
                    {t.properties.map((p: any) => (
                      <div className="ci-property" key={p.name}>
                        <code>
                          {p.name}
                          {p.required ? ' *' : ''}
                        </code>
                        <span>{p.reference ? '→ ' + p.reference : p.type}</span>
                      </div>
                    ))}
                    {data.can_manage && !t.builtin && (
                      <button
                        className="ci-button"
                        onClick={() => setCreatingType(t)}
                      >
                        Edit contract
                      </button>
                    )}
                  </details>
                ))}
            </section>
          </div>
        </>
      )}
      {area === 'Pipelines' && (
        <>
          <div className="ci-flow">
            <span>
              <Database size={19} />
              Source
            </span>
            <ArrowRight />
            <span>Versioned mapping</span>
            <ArrowRight />
            <span>Quality checks</span>
            <ArrowRight />
            <span>Objects & evidence</span>
          </div>
          <section className="ci-panel">
            <h3>Pipeline runs</h3>
            <p className="ci-muted">
              Input pages and mappings are frozen per run. Failed runs retain
              completed page checkpoints.
            </p>
            <div className="ci-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Connection</th>
                    <th>Status</th>
                    <th>Accepted</th>
                    <th>Quarantined</th>
                    <th>Started</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r: any) => (
                    <tr key={r.id}>
                      <td>
                        {
                          data.sources.find((s: any) => s.id === r.source_id)
                            ?.name
                        }
                        <small>{r.id.slice(0, 12)}</small>
                        {r.error && <p className="ci-error-text">{r.error}</p>}
                      </td>
                      <td>
                        <span className="ci-badge">{status(r.status)}</span>
                      </td>
                      <td>{r.accepted.toLocaleString()}</td>
                      <td>{r.rejected.toLocaleString()}</td>
                      <td>{time(r.created)}</td>
                      <td>
                        {writer && r.status === 'failed' && (
                          <button
                            className="ci-button"
                            disabled={busy}
                            onClick={() =>
                              void act(async () => {
                                await call('/runs/' + r.id + '/retry', {});
                              })
                            }
                          >
                            Retry
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!runs.length && (
              <Empty
                title="No pipeline runs yet"
                text="Start a sync in Connections or send your first authenticated JSON batch."
              />
            )}
            {runNext && (
              <button
                className="ci-button"
                disabled={busy}
                onClick={() =>
                  void act(async () => {
                    const r = await call('/runs?before=' + runNext);
                    setRuns([...runs, ...r.items]);
                    setRunNext(r.next_cursor);
                  })
                }
              >
                Older runs
              </button>
            )}
          </section>
          <section className="ci-panel ci-space">
            <h3>Quarantine</h3>
            <p className="ci-muted">
              Rejected records never become objects. Correct the source or
              mapping and submit a new batch; raw inputs remain linked to the
              original run.
            </p>
            {issues.map((r: any) => (
              <div className="ci-health" key={r.id}>
                <span className="ci-dot overdue" />
                <div>
                  <strong>
                    {data.sources.find((s: any) => s.id === r.source_id)?.name}{' '}
                    · Row {r.row}
                  </strong>
                  <p>{r.reason}</p>
                  <small>Run {r.run_id.slice(0, 12)}</small>
                </div>
              </div>
            ))}
            {!issues.length && (
              <p>No quarantined records in your accessible sources.</p>
            )}
            {issueNext && (
              <button
                className="ci-button"
                onClick={() =>
                  void act(async () => {
                    const r = await call('/quarantine?before=' + issueNext);
                    setIssues([...issues, ...r.items]);
                    setIssueNext(r.next_cursor);
                  })
                }
              >
                Older issues
              </button>
            )}
          </section>
        </>
      )}
      {area === 'Decisions' && (
        <section className="ci-panel">
          <h3>Decisions with evidence</h3>
          <p className="ci-muted">
            Planners propose an investigation, replenishment review or supplier
            follow-up from an object. Owners review it against the current
            evidence. These tracked decisions do not send orders or change
            checkout inventory.
          </p>
          {!data.decisions.length && (
            <Empty
              title="Build an evidence trail for your next decision"
              text="Open an object in the ontology, review its source and create a proposal."
              action={() => setArea('Ontology')}
              label="Explore objects"
            />
          )}
          {data.decisions.map((d: any) => (
            <DecisionCard
              key={d.id}
              decision={d}
              owner={data.can_manage}
              busy={busy}
              open={() => void openObject(d.object_id)}
              save={(s: string, note: string) =>
                void act(async () => {
                  await call('/decisions/' + d.id + '/transition', {
                    revision: d.revision,
                    status: s,
                    note,
                  });
                })
              }
            />
          ))}
        </section>
      )}
      {area === 'Evidence assistant' && (
        <div className="ci-columns">
          <section className="ci-panel">
            <span className="ci-eyebrow">
              GEMINI AGENTIC MERCHANT COPILOT · NEURO-SYMBOLIC AI
            </span>
            <h3>Answers & MILP simulations you can trace.</h3>
            <p className="ci-muted">
              Powered by Google Gemini tool-calling over your permission-checked
              supply-chain ontology, GBDT demand engine, and SciPy HiGHS MILP
              promotion optimizer.
            </p>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                runAsk(question);
              }}
            >
              <RetailField label="Ask your Merchant Copilot">
                <textarea
                  required
                  minLength={3}
                  maxLength={1200}
                  rows={3}
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                />
              </RetailField>
              <div
                className="ci-actions"
                style={{ flexWrap: 'wrap', gap: 8, marginBottom: 8 }}
              >
                <button className="ci-button ci-primary" disabled={busy}>
                  <Search size={16} />
                  {busy
                    ? 'Running Agentic Tools…'
                    : 'Run Copilot & Trace Evidence'}
                </button>
                <button
                  className="ci-button"
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    runAsk(
                      'Which products have low stock, and what 2-week promotional portfolio maximizes net profit under a $4,000 budget?',
                    )
                  }
                >
                  ⚡ Low stock + MILP Profit Plan
                </button>
                <button
                  className="ci-button"
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    runAsk(
                      'Which deliveries are overdue and how should we adjust our promotional calendar to protect store margins?',
                    )
                  }
                >
                  ⚡ Overdue deliveries + Margin Guard
                </button>
              </div>
            </form>
            {answer && (
              <div className="ci-answer">
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    flexWrap: 'wrap',
                    marginBottom: 8,
                  }}
                >
                  <span className="ci-badge">{status(answer.mode)}</span>
                  {answer.model && (
                    <span className="ci-badge">Model: {answer.model}</span>
                  )}
                </div>
                <p style={{ whiteSpace: 'pre-line' }}>{answer.answer}</p>
                {answer.warning && (
                  <p className="ci-notice">{answer.warning}</p>
                )}
                {answer.tool_trace?.length > 0 && (
                  <div
                    style={{
                      margin: '12px 0',
                      padding: '10px 12px',
                      borderRadius: 8,
                      background: 'rgba(25, 105, 79, 0.06)',
                      border: '1px solid rgba(25, 105, 79, 0.2)',
                    }}
                  >
                    <strong
                      style={{
                        display: 'block',
                        fontSize: 12,
                        textTransform: 'uppercase',
                        letterSpacing: '0.04em',
                        marginBottom: 6,
                      }}
                    >
                      Agentic Tool Execution Trace ({answer.tool_trace.length}{' '}
                      step{answer.tool_trace.length > 1 ? 's' : ''})
                    </strong>
                    {answer.tool_trace.map((t: any, idx: number) => (
                      <div
                        key={idx}
                        style={{
                          fontSize: 12.5,
                          padding: '5px 0',
                          borderTop:
                            idx > 0
                              ? '1px dashed rgba(25, 105, 79, 0.2)'
                              : 'none',
                        }}
                      >
                        <code>{t.tool}</code>{' '}
                        <span className="ci-muted">
                          ({JSON.stringify(t.args)})
                        </span>
                      </div>
                    ))}
                  </div>
                )}
                {answer.evidence.map((e: any, i: number) => (
                  <button
                    key={e.object_id}
                    className="ci-finding"
                    onClick={() => void openObject(e.object_id)}
                  >
                    <span className="ci-citation">{i + 1}</span>
                    <div>
                      <strong>{e.label}</strong>
                      <small>
                        Object v{e.revision} · Mapping v
                        {e.provenance.mapping_revision}
                      </small>
                    </div>
                    <ArrowRight size={16} />
                  </button>
                ))}
              </div>
            )}
          </section>
          <section className="ci-panel">
            <h3>Evidence before action</h3>
            <div className="ci-callout">
              <ShieldCheck />
              <p>
                The Neuro-Symbolic Copilot invokes read-only ontology tools and
                deterministic GBDT + MILP solvers. It cannot approve decisions,
                activate models, place orders or alter a sale without human
                confirmation.
              </p>
            </div>
            <p>
              Answers are saved with the question, tool execution trace, object
              versions and source lineage. Sources may be incomplete or stale;
              absence of evidence is not a forecast.
            </p>
            <button className="ci-button" onClick={() => setArea('Decisions')}>
              Review decisions <ArrowRight size={16} />
            </button>
          </section>
        </div>
      )}
      {selected && (
        <ObjectDialog
          detail={selected}
          close={() => setSelected(null)}
          open={openObject}
          canPropose={can('connect', 'user')}
          busy={busy}
          propose={(body: any) =>
            void act(async () => {
              await call('/decisions', {
                ...body,
                object_id: selected.object.id,
                object_revision: selected.object.revision,
                request_key: key(),
              });
              setSelected(null);
              setArea('Decisions');
              setNotice('Proposal created for owner review.');
            })
          }
        />
      )}
      {mappingSource && (
        <MappingDialog
          source={mappingSource}
          types={data.types}
          close={() => setMappingSource(null)}
          busy={busy}
          save={(body: any) =>
            void act(async () => {
              await call('/sources/' + mappingSource.id + '/configure', body);
              setMappingSource(null);
              setNotice('Mapping version saved. New runs use this version.');
            })
          }
          preview={(records: any[]) =>
            call('/sources/' + mappingSource.id + '/preview', { records })
          }
          workspace={workspace}
        />
      )}
      {creatingType && (
        <TypeDialog
          initial={creatingType === true ? undefined : creatingType}
          types={data.types}
          close={() => setCreatingType(false)}
          busy={busy}
          save={(body: any) =>
            void act(async () => {
              await call('/types', body);
              setCreatingType(false);
              setNotice(
                'Object type defined. Map a connection to populate it.',
              );
            })
          }
        />
      )}
    </section>
  );
}

function Empty({
  title,
  text,
  action,
  label,
}: {
  title: string;
  text: string;
  action?: () => void;
  label?: string;
}) {
  return (
    <div className="ci-empty">
      <Layers size={27} />
      <h4>{title}</h4>
      <p>{text}</p>
      {action && (
        <button className="ci-button" onClick={action}>
          {label}
          <ArrowRight size={15} />
        </button>
      )}
    </div>
  );
}
function CreateSource({ data, busy, onSave }: any) {
  const [name, setName] = useState(''),
    [kind, setKind] = useState('onesales'),
    [target, setTarget] = useState('Product'),
    [idField, setIdField] = useState('id'),
    [binding, setBinding] = useState(''),
    [restricted, setRestricted] = useState(false),
    [schedule, setSchedule] = useState(0);
  const external = !['onesales', 'push'].includes(kind);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSave({
          name,
          kind,
          binding: external ? binding : '',
          object_type: target,
          id_field: ['push', 'rest', 'mongodb'].includes(kind) ? idField : 'id',
          visibility: restricted ? 'restricted' : 'team',
          schedule_minutes: kind === 'push' ? 0 : schedule,
        });
      }}
    >
      <h3>Add a connection</h3>
      <RetailField label="Connection name">
        <input
          required
          minLength={2}
          maxLength={120}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Main register catalog"
        />
      </RetailField>
      <RetailField label="Source system">
        <select
          value={kind}
          onChange={(e) => {
            setKind(e.target.value);
            setTarget('Product');
            setBinding('');
          }}
        >
          {data.catalog.map((c: any) => (
            <option value={c.kind} key={c.kind}>
              {c.name}
            </option>
          ))}
        </select>
      </RetailField>
      <RetailField label="Target object type">
        <select value={target} onChange={(e) => setTarget(e.target.value)}>
          {data.types
            .filter((t: any) =>
              ['onesales'].includes(kind)
                ? ['Product', 'InventoryPosition', 'SalesObservation'].includes(
                    t.name,
                  )
                : ['push', 'rest', 'mongodb'].includes(kind) ||
                  t.name === 'Product',
            )
            .map((t: any) => (
              <option key={t.name}>{t.name}</option>
            ))}
        </select>
      </RetailField>
      {external && (
        <>
          <RetailField label="Approved connection binding">
            <select
              required
              value={binding}
              onChange={(e) => setBinding(e.target.value)}
            >
              <option value="">Select a configured binding</option>
              {data.bindings
                .filter((b: any) => b.kind === kind)
                .map((b: any) => (
                  <option key={b.name}>{b.name}</option>
                ))}
            </select>
          </RetailField>
          <p className="ci-muted">
            Your deployment operator registers this shop’s endpoint and secret
            reference. Credentials are never entered in this form.
          </p>
        </>
      )}
      {['push', 'rest', 'mongodb'].includes(kind) && (
        <RetailField label="Stable source identifier field">
          <input
            required
            pattern="[A-Za-z0-9_.-]+"
            maxLength={200}
            value={idField}
            onChange={(e) => setIdField(e.target.value)}
            placeholder="id or _id or product.sku"
          />
        </RetailField>
      )}
      {kind !== 'push' && (
        <RetailField label="Sync schedule">
          <select
            value={schedule}
            onChange={(e) => setSchedule(Number(e.target.value))}
          >
            <option value={0}>Manual</option>
            <option value={15}>Every 15 minutes</option>
            <option value={60}>Hourly</option>
            <option value={1440}>Daily</option>
          </select>
        </RetailField>
      )}
      <label className="ci-checkbox">
        <input
          type="checkbox"
          checked={restricted}
          onChange={(e) => setRestricted(e.target.checked)}
        />{' '}
        Restrict source evidence to owners
      </label>
      <button className="ci-button ci-primary" disabled={busy}>
        <Plus size={16} />
        Create connection
      </button>
    </form>
  );
}
function ObjectDialog({ detail, close, open, canPropose, busy, propose }: any) {
  const o = detail.object,
    [title, setTitle] = useState(''),
    [reason, setReason] = useState(''),
    [kind, setKind] = useState('investigate');
  const nodes = [o, ...detail.neighbors];
  return (
    <Dialog
      open
      onOpenChange={(v) => {
        if (!v) close();
      }}
    >
      <DialogContent className="ci-dialog">
        <DialogTitle>{o.label}</DialogTitle>
        <DialogDescription>
          {o.type} · {o.external_id} · Object version {o.revision}
        </DialogDescription>
        <div className="ci-object-columns">
          <div>
            <h3>Properties</h3>
            {Object.entries(o.properties).map(([k, v]) => (
              <div className="ci-property" key={k}>
                <strong>{k}</strong>
                <span>{String(v)}</span>
              </div>
            ))}
            <h3>Relationships</h3>
            {!detail.neighbors.length && (
              <p>No accessible resolved relationships yet.</p>
            )}
            {detail.edges.length > 0 && (
              <div className="ci-graph" aria-label="Object relationships">
                {detail.edges.map((e: any, i: number) => (
                  <div key={i}>
                    <button onClick={() => open(e.source)}>
                      {nodes.find((n: any) => n.id === e.source)?.label}
                    </button>
                    <span>
                      {e.label}
                      <ArrowRight size={14} />
                    </span>
                    <button onClick={() => open(e.target)}>
                      {nodes.find((n: any) => n.id === e.target)?.label}
                    </button>
                  </div>
                ))}
              </div>
            )}
            <p className="ci-muted">{detail.note}</p>
          </div>
          <div>
            <h3>Where this came from</h3>
            <div className="ci-lineage">
              <div>
                <Database size={17} />
                <strong>{detail.lineage.source.name}</strong>
              </div>
              <span>
                Mapping v{detail.lineage.mapping_revision} · Contract v
                {detail.lineage.schema_revision}
              </span>
              <span>Run {detail.lineage.run_id}</span>
              <span>Input row {detail.lineage.row}</span>
              <code>SHA-256: {detail.lineage.batch_sha256}</code>
            </div>
            <h3>Change history</h3>
            {detail.versions.map((v: any) => (
              <details className="ci-contract" key={v.revision}>
                <summary>
                  Version {v.revision}
                  <span>{time(v.updated)}</span>
                </summary>
                <pre>{JSON.stringify(v.properties, null, 2)}</pre>
              </details>
            ))}
            {canPropose && (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  propose({ title, rationale: reason, kind });
                }}
              >
                <h3>Propose an action</h3>
                <RetailField label="Decision type">
                  <select
                    value={kind}
                    onChange={(e) => setKind(e.target.value)}
                  >
                    <option value="investigate">Investigate</option>
                    <option value="replenishment_review">
                      Replenishment review
                    </option>
                    <option value="supplier_followup">
                      Supplier follow-up
                    </option>
                  </select>
                </RetailField>
                <RetailField label="Title">
                  <input
                    required
                    minLength={4}
                    maxLength={160}
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                  />
                </RetailField>
                <RetailField label="Reason and intended next step">
                  <textarea
                    required
                    minLength={8}
                    maxLength={2000}
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                  />
                </RetailField>
                <button className="ci-button ci-primary" disabled={busy}>
                  Submit for owner review
                </button>
              </form>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
function DecisionCard({ decision: d, owner, busy, open, save }: any) {
  const [note, setNote] = useState('');
  return (
    <article className="ci-source">
      <div className="ci-panel-title">
        <strong>{d.title}</strong>
        <span className="ci-badge">{status(d.status)}</span>
      </div>
      <p>{d.rationale}</p>
      <button className="ci-link" onClick={open}>
        {d.evidence.label} · Evidence v{d.object_revision}{' '}
        <ArrowRight size={14} />
      </button>
      {d.history?.map((h: any, i: number) => (
        <p className="ci-muted" key={i}>
          {status(h.status)} · {h.note} · {time(h.created)}
        </p>
      ))}
      {owner && ['proposed', 'approved'].includes(d.status) && (
        <div className="ci-decision-review">
          <RetailField label="Review or completion note">
            <input
              minLength={8}
              maxLength={2000}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Record why this decision is appropriate…"
            />
          </RetailField>
          <div className="ci-actions">
            {(d.status === 'proposed'
              ? ['approved', 'rejected']
              : ['completed', 'rejected']
            ).map((s) => (
              <button
                className="ci-button"
                key={s}
                disabled={busy || note.length < 8}
                onClick={() => save(s, note)}
              >
                {s === 'approved'
                  ? 'Approve'
                  : s === 'completed'
                    ? 'Mark completed'
                    : 'Reject'}
              </button>
            ))}
          </div>
        </div>
      )}
    </article>
  );
}
function MappingDialog({
  source,
  types,
  close,
  busy,
  save,
  preview,
  workspace,
}: any) {
  const spec = types.find((t: any) => t.name === source.object_type);
  const [mapping, setMapping] = useState<any[]>(
      spec.properties.map(
        (p: any) =>
          source.mapping.find((m: any) => m.target === p.name) || {
            target: p.name,
            source: source.mapping.length ? '' : p.name,
            transform: 'identity',
            default: null,
          },
      ),
    ),
    [sample, setSample] = useState(
      '[\n  {"id": "example-1", "name": "Example product"}\n]',
    ),
    [result, setResult] = useState<any>(null),
    [error, setError] = useState('');
  return (
    <Dialog
      open
      onOpenChange={(v) => {
        if (!v) close();
      }}
    >
      <DialogContent className="ci-dialog">
        <DialogTitle>
          {source.name} · Mapping v{source.revision}
        </DialogTitle>
        <DialogDescription>
          Map source fields to {source.object_type}. Source identifier path:{' '}
          {source.id_field}. Saved revisions apply to new runs.
        </DialogDescription>
        {source.kind === 'push' && (
          <div className="ci-callout">
            <div>
              <strong>Authenticated JSON ingestion endpoint</strong>
              <code>
                /api/retail/shops/{workspace}/connected/sources/{source.id}
                /batches
              </code>
              <p>
                Use a retailer integration key from Manage retailers. Send
                request_key and records; at most 500 records per batch. Reuse
                the same key when retrying the same payload.
              </p>
            </div>
          </div>
        )}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save({
              ...sourceBody(source),
              mapping: mapping.filter((m) => m.source || m.default !== null),
            });
          }}
        >
          <div className="ci-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Target property</th>
                  <th>Source field path</th>
                  <th>Transform</th>
                </tr>
              </thead>
              <tbody>
                {mapping.map((m, i) => (
                  <tr key={m.target}>
                    <td>
                      {m.target}
                      {spec.properties.find((p: any) => p.name === m.target)
                        ?.required
                        ? ' *'
                        : ''}
                    </td>
                    <td>
                      <input
                        aria-label={'Source for ' + m.target}
                        value={m.source}
                        onChange={(e) =>
                          setMapping(
                            mapping.map((r, j) =>
                              i === j ? { ...r, source: e.target.value } : r,
                            ),
                          )
                        }
                      />
                    </td>
                    <td>
                      <select
                        aria-label={'Transform for ' + m.target}
                        value={m.transform}
                        onChange={(e) =>
                          setMapping(
                            mapping.map((r, j) =>
                              i === j ? { ...r, transform: e.target.value } : r,
                            ),
                          )
                        }
                      >
                        {[
                          'identity',
                          'string',
                          'number',
                          'integer',
                          'boolean',
                          'trim',
                          'lower',
                          'upper',
                          'minor_to_major',
                        ].map((v) => (
                          <option key={v}>{v}</option>
                        ))}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button className="ci-button ci-primary" disabled={busy}>
            Save new mapping version
          </button>
        </form>
        <details className="ci-contract">
          <summary>Test saved mapping with sample JSON</summary>
          <p className="ci-muted">
            This evaluates the saved mapping, not unsaved edits. It does not
            import or store these sample records.
          </p>
          <textarea
            aria-label="Sample JSON records"
            rows={5}
            value={sample}
            onChange={(e) => setSample(e.target.value)}
          />
          <button
            className="ci-button"
            onClick={async () => {
              try {
                setError('');
                setResult(await preview(JSON.parse(sample)));
              } catch (e: any) {
                setError(e.message);
              }
            }}
          >
            Preview saved mapping
          </button>
          {error && <p role="alert">{error}</p>}
          {result && <pre>{JSON.stringify(result.results, null, 2)}</pre>}
        </details>
      </DialogContent>
    </Dialog>
  );
}
function TypeDialog({ types, close, busy, save, initial }: any) {
  const [name, setName] = useState(initial?.name || ''),
    [description, setDescription] = useState(initial?.description || ''),
    [properties, setProperties] = useState<any[]>(
      initial?.properties || [
        { name: 'name', type: 'string', required: true, reference: null },
      ],
    );
  return (
    <Dialog
      open
      onOpenChange={(v) => {
        if (!v) close();
      }}
    >
      <DialogContent className="ci-dialog">
        <DialogTitle>
          {initial ? 'Update object contract' : 'Define a business object'}
        </DialogTitle>
        <DialogDescription>
          Create a typed contract for entities such as warehouses, equipment,
          campaigns or delivery routes.
        </DialogDescription>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save({
              name,
              description,
              properties,
              revision: initial?.revision || 0,
            });
          }}
        >
          <RetailField label="Type name">
            <input
              required
              pattern="[A-Za-z][A-Za-z0-9_]{0,63}"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Warehouse"
              disabled={Boolean(initial)}
            />
          </RetailField>
          <RetailField label="Business meaning">
            <input
              required
              minLength={4}
              maxLength={500}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </RetailField>
          <div className="ci-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Property</th>
                  <th>Type</th>
                  <th>Reference</th>
                  <th>Required</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {properties.map((p, i) => (
                  <tr key={i}>
                    <td>
                      <input
                        aria-label={'Property ' + (i + 1)}
                        required
                        pattern="[A-Za-z][A-Za-z0-9_]{0,63}"
                        value={p.name}
                        onChange={(e) =>
                          setProperties(
                            properties.map((r, j) =>
                              j === i ? { ...r, name: e.target.value } : r,
                            ),
                          )
                        }
                      />
                    </td>
                    <td>
                      <select
                        aria-label={'Type of property ' + (i + 1)}
                        value={p.type}
                        onChange={(e) =>
                          setProperties(
                            properties.map((r, j) =>
                              j === i
                                ? {
                                    ...r,
                                    type: e.target.value,
                                    reference: null,
                                  }
                                : r,
                            ),
                          )
                        }
                      >
                        {[
                          'string',
                          'number',
                          'integer',
                          'boolean',
                          'date',
                          'datetime',
                        ].map((t) => (
                          <option key={t}>{t}</option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <select
                        aria-label={'Reference for property ' + (i + 1)}
                        disabled={p.type !== 'string'}
                        value={p.reference || ''}
                        onChange={(e) =>
                          setProperties(
                            properties.map((r, j) =>
                              j === i
                                ? { ...r, reference: e.target.value || null }
                                : r,
                            ),
                          )
                        }
                      >
                        <option value="">None</option>
                        {types.map((t: any) => (
                          <option key={t.name}>{t.name}</option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={'Required property ' + (i + 1)}
                        checked={p.required}
                        onChange={(e) =>
                          setProperties(
                            properties.map((r, j) =>
                              j === i
                                ? { ...r, required: e.target.checked }
                                : r,
                            ),
                          )
                        }
                      />
                    </td>
                    <td>
                      <button
                        type="button"
                        aria-label={'Remove property ' + (i + 1)}
                        disabled={properties.length === 1}
                        onClick={() =>
                          setProperties(properties.filter((_, j) => j !== i))
                        }
                      >
                        <X size={14} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="ci-actions">
            <button
              type="button"
              className="ci-button"
              disabled={properties.length >= 60}
              onClick={() =>
                setProperties([
                  ...properties,
                  {
                    name: '',
                    type: 'string',
                    required: false,
                    reference: null,
                  },
                ])
              }
            >
              <Plus size={15} />
              Add property
            </button>
            <button className="ci-button ci-primary" disabled={busy}>
              {initial ? 'Save contract version' : 'Create object type'}
            </button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
