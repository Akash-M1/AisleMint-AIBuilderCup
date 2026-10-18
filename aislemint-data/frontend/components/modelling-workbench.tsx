'use client';
import { useEffect, useRef, useState } from 'react';
import {
  Database,
  ShieldCheck,
  Play,
  RefreshCw,
  ArrowUpRight,
  CheckCircle2,
  Clock3,
  Activity,
  Download,
  Store,
} from 'lucide-react';
import {
  retail,
  useRetail,
  useAccess,
  RetailField,
} from '@/components/retail-platform';
import { number, percent } from '@/lib/api';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
function exportJson(data: unknown, name: string) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
  );
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}
function Badge({ value }: { value: string }) {
  return (
    <span
      className={
        'retail-badge ' +
        (value === 'active' || value === 'ready'
          ? 'good'
          : value === 'failed' || value === 'needs_data'
            ? 'attention'
            : '')
      }
    >
      {value.replaceAll('_', ' ')}
    </span>
  );
}
export default function ModellingWorkbench({
  workspace,
  onActivated,
}: {
  workspace: string;
  onActivated: () => void;
}) {
  const ctx = useRetail();
  const can = useAccess();
  const [data, setData] = useState<any>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(''),
    [tab, setTab] = useState('Models'),
    [profile, setProfile] = useState('comparison'),
    [selected, setSelected] = useState(''),
    [detail, setDetail] = useState(''),
    [reason, setReason] = useState(''),
    [activation, setActivation] = useState(''),
    [notice, setNotice] = useState('');
  const [preview, setPreview] = useState<any>(null);
  const dialogTrigger = useRef<HTMLButtonElement>(null);
  const activationReason = useRef<HTMLTextAreaElement>(null);
  const base = '/shops/' + workspace;
  const refresh = async () => {
    const r = await retail(base + '/workbench');
    setData(r);
  };
  useEffect(() => {
    setDetail('');
    setActivation('');
    setReason('');
    setPreview(null);
    setError('');
    setNotice('');
    let alive = true;
    const load = () =>
      retail(base + '/workbench')
        .then((r) => {
          if (alive) setData(r);
        })
        .catch((e) => {
          if (alive) setError(e.message);
        });
    void load();
    const id = setInterval(load, 10000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [workspace]);
  const act = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    setError('');
    setNotice('');
    try {
      await fn();
      await refresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy('');
    }
  };
  if (!data)
    return (
      <div className="retail-note">
        {error || 'Loading this shop’s modelling workbench…'}
      </div>
    );
  const owner =
    !!ctx?.session.user.admin ||
    ctx?.session.user.memberships[data.shop.retailer_id] === 'owner';
  const write = can('data','editor');
  const ready = data.snapshots.filter((s: any) => s.status === 'ready');
  const latest = data.snapshots[0];
  const running = data.runs.filter((r: any) =>
    ['queued', 'running'].includes(r.status),
  );
  const active = (kind: string) =>
    data.runs.find((r: any) => r.id === data.shop.active[kind]);
  const candidate = data.runs.find((r: any) => r.id === detail);
  const activating = data.runs.find((r: any) => r.id === activation);
  return (
    <div className="retail-workbench">
      {error && !detail && !activation && (
        <p className="retail-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="retail-note" role="status">
          {notice}
        </p>
      )}
      <section className="retail-workbench-hero">
        <div>
          <p className="retail-eyebrow">
            {data.shop.name} · {data.shop.currency} · OWNED MODEL LIFECYCLE
          </p>
          <h2>From your history to a model you can trust.</h2>
          <p>
            Validate a snapshot, compare models on later periods, then decide
            what serves this shop.
          </p>
          <div className="retail-inline">
            <span>
              <ShieldCheck size={15} /> Shop-isolated training
            </span>
            <span>
              <Database size={15} /> MongoDB + GridFS
            </span>
            <span>
              <Activity size={15} /> Versioned deployment
            </span>
          </div>
        </div>
        <button disabled={!!busy} onClick={() => void act('refresh', refresh)}>
          <RefreshCw size={16} />
          Refresh
        </button>
      </section>
      <div className="retail-summary-grid">
        <article>
          <span>Observed history</span>
          <strong>{latest ? number(latest.rows) : 'Awaiting data'}</strong>
          <small>
            {latest ? latest.name : 'Connect your ERP or OneSales history'}
          </small>
        </article>
        {['demand', 'promotion'].map((kind) => (
          <article key={kind}>
            <span>
              {kind === 'demand' ? 'Demand model' : 'Promotion model'}
            </span>
            <strong>{active(kind) ? 'Active' : 'Not activated'}</strong>
            <small>
              {active(kind)?.card.family ||
                'No demo weights are used for this shop'}
            </small>
            {active(kind) && (
              <small>Data through {active(kind).card.data_end}</small>
            )}
          </article>
        ))}
        <article>
          <span>Training queue</span>
          <strong>
            {running.length
              ? `${running.length} job${running.length > 1 ? 's' : ''}`
              : 'Available'}
          </strong>
          <small>One fit at a time across workers</small>
        </article>
      </div>
      {data.freshness?.some((f: any) => f.stale) && (
        <div className="retail-note" role="status">
          <strong>Refresh history before live planning.</strong>
          <p>
            {data.freshness
              .filter((f: any) => f.stale)
              .map(
                (f: any) =>
                  `${f.kind}: data through ${f.data_end} (${f.age_days} days ago)`,
              )
              .join(' · ')}
            . Historical analysis remains available; submit current data and
            review a new candidate.
          </p>
        </div>
      )}
      {!!data.ledger_jobs?.length && (
        <section className="retail-card">
          <h3>OneSales snapshot requests</h3>
          {data.ledger_jobs.map((j: any) => (
            <p key={j.id}>
              {j.kind} · <Badge value={j.status} /> ·{' '}
              {j.error ||
                'Waiting for committed ledger history to be validated.'}
            </p>
          ))}
        </section>
      )}
      <nav className="retail-tabs" aria-label="Workbench sections">
        {['Models', 'Data readiness', 'Connections', 'Activity'].map((t) => (
          <button key={t} aria-pressed={tab === t} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </nav>
      {tab === 'Models' && (
        <>
          <div className="retail-two">
            <section className="retail-card">
              <p className="retail-eyebrow">TRAIN A CANDIDATE</p>
              <h3>A fresh version. A clear comparison.</h3>
              <p>
                Both model families learn seasonality, market holidays and
                promotion exposures from this shop. Fitted weights are never
                shared across retailers.
              </p>
              <fieldset disabled={!!busy || !write}>
                <RetailField label="Validated data snapshot">
                  <select
                    value={selected}
                    onChange={(e) => setSelected(e.target.value)}
                  >
                    <option value="">Choose a ready snapshot</option>
                    {ready.map((s: any) => (
                      <option value={s.id} key={s.id}>
                        {s.kind} · {s.name} · {number(s.rows)} rows
                      </option>
                    ))}
                  </select>
                </RetailField>
                <RetailField label="Training profile">
                  <select
                    value={profile}
                    onChange={(e) => setProfile(e.target.value)}
                  >
                    <option value="comparison">
                      Model comparison · recommended
                    </option>
                    <option value="efficient">
                      Efficient · histogram gradient boosting
                    </option>
                  </select>
                </RetailField>
                <p className="retail-small">
                  Comparison: promotion baseline/response candidates include
                  HGB, XGBoost, CatBoost and a Poisson GLM; demand compares HGB,
                  XGBoost and statistical forecasts. Supported ensembles are
                  selected on calibration data. Missing optional libraries are
                  reported.
                </p>
                <button
                  className="retail-primary"
                  disabled={!selected}
                  onClick={() =>
                    void act('train', async () => {
                      await retail(base + '/train', {
                        snapshot_id: selected,
                        profile,
                        idempotency_key: crypto.randomUUID(),
                      });
                      setNotice(
                        'Candidate queued. You can keep using the app while training runs.',
                      );
                    })
                  }
                >
                  <Play size={16} />
                  {busy === 'train' ? 'Queueing…' : 'Train candidate'}
                </button>
              </fieldset>
              {!ready.length && (
                <p className="retail-note">
                  No ready snapshots yet. Open Data readiness to see the
                  required history and connection steps.
                </p>
              )}
            </section>
            <section className="retail-card">
              <p className="retail-eyebrow">ACTIVATION POLICY</p>
              <h3>Better evidence before a model change.</h3>
              <ul>
                <li>
                  Chronological training, calibration and holdout periods.
                </li>
                <li>
                  Demand error compared with recent and seasonal benchmarks.
                </li>
                <li>
                  Absolute holdout bias at most 15%; 90% interval coverage at
                  least 80%.
                </li>
                <li>
                  Clean non-promotion baseline evidence for promotion models.
                </li>
                <li>
                  Owner review, immutable checksums and an atomic version
                  switch.
                </li>
              </ul>
              <p className="retail-small">
                Passing these checks enables activation. It does not establish
                causal lift or guarantee margins and stock availability. An
                earlier passing version can be reactivated to roll back.
              </p>
            </section>
          </div>
          <section className="retail-card">
            <div className="retail-section-heading">
              <h3>Model registry</h3>
              <span>{data.runs.length} versions</span>
            </div>
            {!owner && (
              <p className="retail-note">
                You can review models. Activation requires a retailer owner or
                platform administrator.
              </p>
            )}
            {!data.runs.length ? (
              <div className="retail-placeholder">
                <Activity />
                <h3>Your first model starts here.</h3>
                <p>
                  Training results, holdout metrics and serving versions will
                  appear after you train a validated shop snapshot.
                </p>
              </div>
            ) : (
              <div className="retail-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Model version</th>
                      <th>Status</th>
                      <th>Holdout WAPE</th>
                      <th>90% coverage</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.runs.map((r: any) => {
                      const metrics =
                        r.kind === 'demand'
                          ? r.card?.test
                          : r.card?.folds?.at(-1)?.model;
                      const coverage =
                        r.kind === 'demand'
                          ? r.card?.interval_90_coverage
                          : r.card?.folds?.at(-1)?.interval_coverage;
                      return (
                        <tr key={r.id}>
                          <td>
                            <strong>
                              {r.kind === 'demand' ? 'Demand' : 'Promotion'} ·{' '}
                              {r.id.slice(0, 8)}
                            </strong>
                            <small>
                              {new Date(r.created).toLocaleString()} ·{' '}
                              {r.profile}
                            </small>
                            <small>{r.stage}</small>
                            {r.status === 'running' && (
                              <small>
                                {r.worker_alive
                                  ? 'Worker heartbeat active'
                                  : 'Worker heartbeat expired; awaiting recovery'}
                              </small>
                            )}
                            {r.error && (
                              <small className="retail-error">{r.error}</small>
                            )}
                          </td>
                          <td>
                            <Badge
                              value={
                                data.shop.active[r.kind] === r.id
                                  ? 'active'
                                  : r.status
                              }
                            />
                            {r.gate && !r.gate.passed && (
                              <small>Activation checks failed</small>
                            )}
                          </td>
                          <td>{percent(metrics?.wape)}</td>
                          <td>{percent(coverage)}</td>
                          <td>
                            {r.card && (
                              <button
                                aria-label={`Review ${r.kind} model ${r.id.slice(0, 8)}`}
                                onClick={(event) => {
                                  dialogTrigger.current = event.currentTarget;
                                  setError('');
                                  setPreview(null);
                                  setActivation('');
                                  setDetail(r.id);
                                }}
                              >
                                Review <ArrowUpRight size={14} />
                              </button>
                            )}
                            {r.status === 'queued' && (
                              <button
                                disabled={!!busy || !write}
                                onClick={() =>
                                  void act('cancel', async () => {
                                    await retail(
                                      base + '/runs/' + r.id + '/cancel',
                                      {},
                                    );
                                  })
                                }
                              >
                                Cancel
                              </button>
                            )}
                            {r.gate?.passed &&
                              data.shop.active[r.kind] !== r.id && (
                                <button
                                  disabled={!!busy || !owner}
                                  aria-label={`Activate ${r.kind} model ${r.id.slice(0, 8)}`}
                                  onClick={(event) => {
                                    dialogTrigger.current = event.currentTarget;
                                    setError('');
                                    setDetail('');
                                    setActivation(r.id);
                                    setReason('');
                                  }}
                                >
                                  Activate
                                </button>
                              )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            <Dialog
              open={!!candidate}
              onOpenChange={(open) => {
                if (!open) {
                  setDetail('');
                  setPreview(null);
                  setError('');
                }
              }}
            >
              {candidate && (
                <DialogContent
                  className="retail-dialog retail-card"
                  finalFocus={dialogTrigger}
                >
                  <DialogHeader className="pr-10">
                    <DialogTitle>
                      Review {candidate.kind} model · {candidate.id.slice(0, 8)}
                    </DialogTitle>
                    <DialogDescription>
                      {candidate.card.family}
                    </DialogDescription>
                  </DialogHeader>
                  {error && (
                    <p className="retail-error" role="alert">
                      {error}
                    </p>
                  )}
                  <div className="retail-section-heading">
                    <button
                      onClick={() =>
                        exportJson(
                          candidate.card,
                          `model-card-${candidate.id}.json`,
                        )
                      }
                    >
                      <Download size={15} />
                      Model card
                    </button>
                  </div>
                  <div className="retail-checks">
                    {candidate.gate.checks.map((c: any) => (
                      <div key={c.name}>
                        <span>
                          {c.passed ? (
                            <CheckCircle2 size={17} />
                          ) : (
                            <Clock3 size={17} />
                          )}
                        </span>
                        {c.name}
                        <Badge value={c.passed ? 'ready' : 'needs_data'} />
                      </div>
                    ))}
                  </div>
                  <p>
                    Snapshot <code>{candidate.snapshot_id}</code>
                  </p>
                  {candidate.card.provenance?.data_release_id && (
                    <p className="retail-small">
                      Shared data release{' '}
                      <code>{candidate.card.provenance.data_release_id}</code> ·{' '}
                      {candidate.card.provenance.source_ids?.length || 0} source
                      connections
                    </p>
                  )}
                  <p className="retail-small">
                    Dataset checksum <code>{candidate.panel_sha256}</code>
                  </p>
                  <p className="retail-small">
                    Code version{' '}
                    <code>{candidate.card.provenance?.code_sha256}</code>
                  </p>
                  <details>
                    <summary>
                      Features, evaluation windows & full evidence
                    </summary>
                    <pre>{JSON.stringify(candidate.card, null, 2)}</pre>
                  </details>
                  <button
                    disabled={!!busy}
                    onClick={() =>
                      void act('preview', async () => {
                        setPreview(
                          await retail(
                            base + '/runs/' + candidate.id + '/preview',
                          ),
                        );
                      })
                    }
                  >
                    <Play size={15} /> Preview candidate forecast
                  </button>
                  {preview?.run_id === candidate.id && (
                    <section className="retail-note">
                      <h3>Candidate forecast preview</h3>
                      <p>{preview.warning}</p>
                      <p>{preview.assumption}</p>
                      {!preview.gate.passed && (
                        <p className="retail-error">
                          Activation checks have not passed. These intervals are
                          not validated for operational decisions.
                        </p>
                      )}
                      <div className="retail-table-wrap">
                        <table>
                          <thead>
                            <tr>
                              <th>Period</th>
                              {candidate.kind === 'promotion' && (
                                <th>ML baseline</th>
                              )}
                              <th>Expected units</th>
                              <th>Lower estimate</th>
                              <th>Upper estimate</th>
                            </tr>
                          </thead>
                          <tbody>
                            {preview.points.map((p: any) => (
                              <tr key={p.date}>
                                <td>{p.date}</td>
                                {candidate.kind === 'promotion' && (
                                  <td>{number(p.baseline)}</td>
                                )}
                                <td>{number(p.forecast)}</td>
                                <td>{number(p.lower)}</td>
                                <td>{number(p.upper)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </section>
                  )}
                </DialogContent>
              )}
            </Dialog>
          </section>
          <Dialog
            open={!!activation}
            onOpenChange={(open) => {
              if (!open && !busy) {
                setActivation('');
                setReason('');
                setError('');
              }
            }}
          >
            {activation && (
              <DialogContent
                className="retail-dialog retail-card retail-activation"
                initialFocus={activationReason}
                finalFocus={dialogTrigger}
                showCloseButton={!busy}
              >
                <DialogHeader className="pr-10">
                  <DialogTitle>
                    Activate {activating?.kind} model · {activation.slice(0, 8)}
                  </DialogTitle>
                  <DialogDescription>
                    New forecasts will use this version. Saved plans retain
                    their original model evidence; confirmed promotion calendars
                    stay attached to the shop.
                  </DialogDescription>
                </DialogHeader>
                {error && (
                  <p className="retail-error" role="alert">
                    {error}
                  </p>
                )}
                <RetailField label="Review / rollback reason">
                  <textarea
                    ref={activationReason}
                    disabled={!!busy}
                    minLength={8}
                    maxLength={500}
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    placeholder="Why is this version appropriate for the shop?"
                  />
                </RetailField>
                <button
                  className="retail-primary disabled:opacity-50 disabled:cursor-not-allowed"
                  disabled={!!busy || !owner || reason.trim().length < 8}
                  onClick={() =>
                    void act('activate', async () => {
                      await retail(base + '/runs/' + activation + '/activate', {
                        revision: data.shop.revision,
                        reason,
                      });
                      setActivation('');
                      await ctx?.refresh();
                      onActivated();
                      setNotice(
                        'Model activated for this shop. New forecasts resolve the selected version.',
                      );
                    })
                  }
                >
                  {busy === 'activate' ? 'Activating…' : 'Confirm activation'}
                </button>
                <button
                  disabled={!!busy}
                  onClick={() => {
                    setActivation('');
                    setReason('');
                    setError('');
                  }}
                >
                  Cancel
                </button>
              </DialogContent>
            )}
          </Dialog>
        </>
      )}
      {tab === 'Data readiness' && (
        <>
          <section className="retail-card">
            <div className="retail-section-heading">
              <div>
                <p className="retail-eyebrow">{data.contract.version}</p>
                <h3>A shared format. Separate retailer data.</h3>
              </div>
              <button
                onClick={() =>
                  exportJson(data.contract, 'retail-sales-contract.json')
                }
              >
                <Download size={16} />
                Download JSON contract
              </button>
            </div>
            <div className="retail-two">
              <div>
                <h4>Required sales history</h4>
                <p>
                  Daily demand: at least 180 days. Weekly demand and promotion:
                  at least 78 weeks. Two or more years is preferable for annual
                  seasonality and holidays.
                </p>
                <p>
                  Each snapshot belongs to one shop and one model family. Demand
                  uncertainty also needs at least 20 complete calibration paths;
                  readiness checks explain any shortfall.
                </p>
              </div>
              <div>
                <h4>Business semantics matter</h4>
                <p>
                  Use gross units sold and realized prices excluding tax. Keep
                  returns separate. Include all tracked products, promotional
                  availability, stockouts and regular prices. Missing
                  observations remain missing.
                </p>
              </div>
            </div>
            <details>
              <summary>Example record and exact field specification</summary>
              <pre>
                {JSON.stringify(
                  {
                    ...data.contract.example,
                    store_id: data.shop.store_code,
                    country: data.shop.country,
                    currency: data.shop.currency,
                  },
                  null,
                  2,
                )}
              </pre>
              <pre>{JSON.stringify(data.contract.row_schema, null, 2)}</pre>
            </details>
          </section>
          <section className="retail-card">
            <h3>Data snapshots</h3>
            {!data.snapshots.length && (
              <p>
                No history received. Open Connections for the authenticated
                ingestion API or create a snapshot from OneSales.
              </p>
            )}
            {data.snapshots.map((s: any) => (
              <article className="retail-snapshot" key={s.id}>
                <div className="retail-section-heading">
                  <div>
                    <strong>{s.name}</strong>
                    <p>
                      {s.kind} · {s.frequency} · {number(s.rows)} rows ·{' '}
                      {s.id.slice(0, 8)}
                    </p>
                  </div>
                  <Badge value={s.status} />
                </div>
                {s.report && (
                  <>
                    <p>
                      {number(s.report.quality.products)} products ·{' '}
                      {number(
                        s.report.quality.periods || s.report.quality.weeks,
                      )}{' '}
                      periods
                    </p>
                    {s.report.issues.map((issue: string) => (
                      <p className="retail-error" key={issue}>
                        {issue}
                      </p>
                    ))}
                    {s.report.warnings.map((warning: string) => (
                      <p className="retail-small" key={warning}>
                        {warning}
                      </p>
                    ))}
                  </>
                )}
                {['receiving', 'validating'].includes(s.status) && (
                  <button
                    disabled={!!busy || !write}
                    onClick={() =>
                      void act('validate', async () => {
                        await retail(
                          base + '/snapshots/' + s.id + '/finalize',
                          {},
                        );
                      })
                    }
                  >
                    Seal & validate snapshot
                  </button>
                )}
                {s.status === 'ready' && (
                  <button
                    onClick={() => {
                      setSelected(s.id);
                      setTab('Models');
                    }}
                  >
                    Use for training <ArrowUpRight size={14} />
                  </button>
                )}
                {s.status === 'needs_data' && (
                  <small>
                    This version is sealed. Submit corrected or extended history
                    as a new snapshot.
                  </small>
                )}
              </article>
            ))}
          </section>
        </>
      )}
      {tab === 'Connections' && (
        <>
          <section className="retail-card">
            <h3>Connect an ERP or your data pipeline</h3>
            <p>
              Create an ingestion key in Manage retailers → Integration
              credentials. Your pipeline writes JSON directly to MongoDB through
              these authenticated endpoints.
            </p>
            <p>
              Workspace <code>{workspace}</code> · Store code{' '}
              <code>{data.shop.store_code}</code>
            </p>
            <ol>
              <li>
                Create a snapshot with its model family, cadence and a unique
                idempotency key.
              </li>
              <li>
                Send batches of at most 2,000 sales observations using stable
                batch keys.
              </li>
              <li>Finalize the snapshot; inspect its readiness report here.</li>
            </ol>
            <pre>{`POST /api/retail/shops/${workspace}/snapshots\n{ "name": "Observed history", "kind": "demand", "frequency": "daily", "idempotency_key": "erp-demand-2026-01" }\n\nPOST /api/retail/shops/${workspace}/snapshots/SNAPSHOT_ID/batches\n{ "batch_key": "0001", "records": [ /* contract observations */ ] }\n\nPOST /api/retail/shops/${workspace}/snapshots/SNAPSHOT_ID/finalize\n\nAuthorization: Bearer YOUR_RETAILER_INGESTION_KEY`}</pre>
            <p className="retail-small">
              Current worker envelope: {number(data.contract.max_rows)}{' '}
              observations and 250 products per snapshot. This is an explicit
              capacity limit; larger assortments need a partitioned modelling
              strategy before onboarding.
            </p>
          </section>
          <section className="retail-card">
            <h3>
              <Store size={19} /> OneSales connection
            </h3>
            {data.shop.pos_store_id ? (
              <>
                <p>
                  Checkout, confirmed promotions and observed trading days are
                  connected to this shop.
                </p>
                <code>{data.shop.pos_store_id}</code>
                <div className="retail-inline">
                  {['demand', 'promotion'].map((kind) => (
                    <button
                      key={kind}
                      disabled={!!busy || !write}
                      onClick={() =>
                        void act('ledger', async () => {
                          await retail(base + '/ledger-snapshot', {
                            kind,
                            idempotency_key: crypto.randomUUID(),
                          });
                          setTab('Data readiness');
                        })
                      }
                    >
                      Build {kind} history snapshot
                    </button>
                  ))}
                </div>
              </>
            ) : owner ? (
              <ConnectPOS
                workspace={workspace}
                country={data.shop.country}
                shopName={data.shop.name}
                done={async () => {
                  await refresh();
                  await ctx?.refresh();
                }}
              />
            ) : (
              <p>A retailer owner can connect this shop’s OneSales register.</p>
            )}
          </section>
        </>
      )}
      {tab === 'Activity' && (
        <section className="retail-card">
          <h3>Shop audit trail</h3>
          <p>
            Data versions, training requests and serving changes retain their
            actor and time.
          </p>
          {data.audit.map((e: any) => (
            <div key={e.id} className="retail-audit">
              <CheckCircle2 size={16} />
              <div>
                <strong>{e.event.replaceAll('_', ' ')}</strong>
                <small>
                  {new Date(e.created).toLocaleString()} · {e.actor}
                </small>
                {e.details.reason && <p>{e.details.reason}</p>}
                <code>{e.details.run_id || e.details.snapshot_id || ''}</code>
              </div>
            </div>
          ))}
          {!data.audit.length && <p>No events yet.</p>}
        </section>
      )}
    </div>
  );
}
function ConnectPOS({
  workspace,
  country,
  shopName,
  done,
}: {
  workspace: string;
  country: string;
  shopName: string;
  done: () => Promise<void>;
}) {
  const ctx = useRetail();
  const can = useAccess();
  const [address, setAddress] = useState(''),
    [state, setState] = useState(''),
    [registered, setRegistered] = useState(false),
    [gstin, setGstin] = useState(''),
    [name, setName] = useState(''),
    [pin, setPin] = useState(''),
    [existing, setExisting] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  if (country !== 'IN')
    return (
      <p>
        Planning supports this market. OneSales checkout currently supports
        India / INR.
      </p>
    );
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError('');
        try {
          await retail(
            '/shops/' + workspace + '/pos',
            existing
              ? { existing_store_id: existing }
              : {
                  new_store: {
                    name: shopName,
                    address,
                    state_code: state,
                    gst_registered: registered,
                    gstin,
                    owner_name: name,
                    pin,
                  },
                },
          );
          setPin('');
          await done();
        } catch (e: any) {
          setError(e.message);
        } finally {
          setBusy(false);
        }
      }}
    >
      {error && (
        <p className="retail-error" role="alert">
          {error}
        </p>
      )}
      <p>
        Create the store’s register here to connect checkout to this permanent
        planning workspace.
      </p>
      {ctx?.session.user.admin && (
        <RetailField label="Existing unassigned OneSales store ID (optional)">
          <input
            value={existing}
            onChange={(e) => setExisting(e.target.value)}
          />
        </RetailField>
      )}
      {!existing && (
        <fieldset disabled={busy}>
          <RetailField label="Store address">
            <input
              required
              minLength={5}
              value={address}
              onChange={(e) => setAddress(e.target.value)}
            />
          </RetailField>
          <div className="retail-two">
            <RetailField label="GST state code (e.g. 29)">
              <input
                required
                pattern="(0[1-9]|[12][0-9]|3[0-8])"
                value={state}
                onChange={(e) => setState(e.target.value)}
              />
            </RetailField>
            <RetailField label="Manager name">
              <input
                required
                minLength={2}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </RetailField>
          </div>
          <label className="retail-inline">
            <input
              type="checkbox"
              checked={registered}
              onChange={(e) => {
                setRegistered(e.target.checked);
                if (!e.target.checked) setGstin('');
              }}
            />
            GST registered
          </label>
          {registered && (
            <RetailField label="GSTIN">
              <input
                required
                maxLength={15}
                value={gstin}
                onChange={(e) => setGstin(e.target.value.toUpperCase())}
              />
            </RetailField>
          )}
          <RetailField label="Manager register PIN (6–12 digits)">
            <input
              type="password"
              required
              pattern="[0-9]{6,12}"
              autoComplete="new-password"
              value={pin}
              onChange={(e) => setPin(e.target.value)}
            />
          </RetailField>
        </fieldset>
      )}
      <button className="retail-primary" disabled={busy}>
        {busy ? 'Connecting…' : 'Connect OneSales'}
      </button>
    </form>
  );
}
