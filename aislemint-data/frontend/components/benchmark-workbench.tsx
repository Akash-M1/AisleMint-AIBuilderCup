'use client';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { api, number, percent } from '@/lib/api';
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  Database,
  FileCheck2,
  Layers,
  LoaderCircle,
  Play,
  RefreshCw,
} from 'lucide-react';
import { useEffect, useState } from 'react';

import { Empty, Metric, Picker, Warnings } from '@/components/planning-ui';
export default function BenchmarkWorkbench() {
  const [workspace, setWorkspace] = useState('benchmark');
  const [summary, setSummary] = useState<any>(null);
  const [datasets, setDatasets] = useState<any[]>([]);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const lastFold = summary?.model?.folds?.at(-1);
  const notify = (text: string) => setError(text);
  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label);
    setError('');
    try {
      await fn();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy('');
    }
  }
  useEffect(() => {
    const c = new AbortController();
    api('/summary', workspace, undefined, c.signal)
      .then(setSummary)
      .catch((e) => {
        if (e.name !== 'AbortError') setError(e.message);
      });
    return () => c.abort();
  }, [workspace, reload]);
  useEffect(() => {
    api<any[]>('/datasets')
      .then(setDatasets)
      .catch((e) => setError(e.message));
  }, [reload]);
  useEffect(() => {
    if (!datasets.some((d) => ['queued', 'training'].includes(d.status))) return;
    const t = setInterval(
      () =>
        api<any[]>('/datasets')
          .then(setDatasets)
          .catch((e) => setError(e.message)),
      4000,
    );
    return () => clearInterval(t);
  }, [datasets]);
  return (
    <section>
      <h1>Benchmark modelling workbench</h1>
      {error && <p role="status">{error}</p>}
      <fieldset disabled={!!busy} className="working-fieldset">
        <Tabs defaultValue="model">
          <TabsList className="data-tabs">
            <TabsTrigger value="model">Model performance</TabsTrigger>
            <TabsTrigger value="data">Data & connections</TabsTrigger>
            <TabsTrigger value="basket">Basket relationships</TabsTrigger>
          </TabsList>
          <TabsContent value="model">
            {summary?.model && (
              <>
                <div className="model-heading panel">
                  <div className="model-icon">
                    <Activity size={26} />
                  </div>
                  <div>
                    <span className="eyebrow">LOCAL DEMAND ENGINE</span>
                    <h2>{summary.model.family}</h2>
                    <p>
                      {summary.model.model_id} · trained in{' '}
                      {number(summary.model.training_seconds, 1)} seconds · 4
                      CPU threads
                    </p>
                  </div>
                  <span className="pill green">
                    Chronological holdout evaluated
                  </span>
                </div>
                <div className="metric-grid">
                  <Metric
                    label="Demand WAPE"
                    value={percent(lastFold.model.wape)}
                    sub="Final 13-week holdout · lower is better"
                  />
                  <Metric
                    label="Vs. historical mean"
                    value={percent(
                      1 - lastFold.model.wape / lastFold.historical_mean.wape,
                    )}
                    sub="Relative reduction in weighted error"
                    accent
                  />
                  <Metric
                    label="90% band coverage"
                    value={percent(lastFold.interval_coverage)}
                    sub="Observed coverage on held-out rows"
                  />
                  <Metric
                    label="Demand bias"
                    value={percent(lastFold.model.bias)}
                    sub="Positive means overforecasting"
                  />
                </div>
                {summary.model.baseline && (
                  <section className="panel baseline-model-card">
                    <div className="section-title">
                      <div>
                        <span className="eyebrow">
                          DEDICATED NO-PROMOTION LEARNER
                        </span>
                        <h2>
                          Baseline sales are predicted by machine learning.
                        </h2>
                      </div>
                      <span className="pill green">
                        No manually entered baseline
                      </span>
                    </div>
                    <div className="metric-grid three">
                      <Metric
                        label="Clean training observations"
                        value={number(summary.model.baseline.training_rows)}
                        sub="No promotion now or in the four prior weeks"
                      />
                      <Metric
                        label="Baseline WAPE"
                        value={percent(lastFold.baseline_model?.wape)}
                        sub={
                          number(lastFold.baseline_test_rows) +
                          ' unseen non-promotion observations'
                        }
                        accent
                      />
                      <Metric
                        label="Baseline learner"
                        value={
                          String(summary.model.baseline.iterations) + ' trees'
                        }
                        sub={summary.model.baseline.family}
                      />
                    </div>
                    <p className="micro">
                      The baseline model learns from actual non-promoted unit
                      sales. The response model learns how promotion exposure
                      changes demand. Promotion review subtracts the resulting
                      ML no-promotion prediction from actual sales; it does not
                      substitute forecast sales for observed sales.
                    </p>
                    <p className="micro">
                      {summary.model.baseline.selection} Baseline accuracy
                      during a live promotion cannot be directly observed.
                      Recent pre-window promotion carryover is retained through
                      the response model.
                    </p>
                  </section>
                )}
                <section className="panel">
                  <div className="section-title">
                    <h2>Performance through time</h2>
                    <span className="pill">2 temporal folds</span>
                  </div>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Training ends</TableHead>
                        <TableHead>Unseen test window</TableHead>
                        <TableHead className="numeric">Model WAPE</TableHead>
                        <TableHead className="numeric">
                          Response-only ML
                        </TableHead>
                        <TableHead className="numeric">
                          Historical mean
                        </TableHead>
                        <TableHead className="numeric">
                          Last-year naive
                        </TableHead>
                        <TableHead className="numeric">Coverage</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {summary.model.folds.map((f: any) => (
                        <TableRow key={f.train_end}>
                          <TableCell>{f.train_end}</TableCell>
                          <TableCell>
                            {f.test_start} → {f.test_end}
                          </TableCell>
                          <TableCell className="numeric positive">
                            {percent(f.model.wape)}
                          </TableCell>
                          <TableCell className="numeric">
                            {percent(f.response_only?.wape)}
                          </TableCell>
                          <TableCell className="numeric">
                            {percent(f.historical_mean.wape)}
                          </TableCell>
                          <TableCell className="numeric">
                            {percent(f.seasonal_naive.wape)}
                          </TableCell>
                          <TableCell className="numeric">
                            {percent(f.interval_coverage)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                  <p className="micro">
                    Each fold has a separate 13-week calibration window before
                    its test. The serving model retains the final fold’s
                    training cutoff, {summary.model.trained_until}, so held-out
                    weeks are never fitted.
                  </p>
                </section>
                {lastFold.baseline_selected && (
                  <section className="panel">
                    <div className="section-title">
                      <h2>Model comparison and seasonality</h2>
                      <span className="pill">Chosen on calibration data</span>
                    </div>
                    <p className="micro">
                      Baseline: {lastFold.baseline_selected.name}. Promotion
                      response: {lastFold.response_selected.name}. “hgb” uses
                      richer annual seasonality; “legacy_hgb” is the original
                      histogram model.
                    </p>
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Baseline candidate</TableHead>
                          <TableHead className="numeric">
                            Calibration WAPE
                          </TableHead>
                          <TableHead className="numeric">Test WAPE</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {lastFold.baseline_candidates.map(
                          (candidate: {
                            name: string;
                            calibration_wape: number;
                            test_wape: number;
                          }) => (
                            <TableRow key={candidate.name}>
                              <TableCell>{candidate.name}</TableCell>
                              <TableCell className="numeric">
                                {percent(candidate.calibration_wape)}
                              </TableCell>
                              <TableCell className="numeric">
                                {percent(candidate.test_wape)}
                              </TableCell>
                            </TableRow>
                          ),
                        )}
                      </TableBody>
                    </Table>
                    <p className="micro">
                      Annual Fourier orders 1–4, month and week describe the
                      calendar; the models learn their relationship with sales.
                      Ensemble weights use calibration data only. These
                      historical test periods have been reused during
                      development; they do not establish causal uplift or
                      performance for a new business.
                    </p>
                  </section>
                )}
                <div className="two-cols model-details">
                  <section className="panel">
                    <h2>What the model learns from</h2>
                    <div className="feature-tags">
                      {[
                        'Product & store',
                        'Regular price',
                        'Discount depth',
                        'Feature & display',
                        'Annual seasonality',
                        ...(summary.model.holiday_calendar
                          ? ['US occasions & federal observance']
                          : []),
                        'Non-promotion demand history',
                        'Peer promotions',
                        'Other-category exposure',
                        '4 weeks of lagged exposure',
                      ].map((s) => (
                        <span key={s}>{s}</span>
                      ))}
                    </div>
                    <p className="micro">
                      Poisson boosting with calibration-selected learners shown
                      above. Current discount, display and feature have
                      monotonic response constraints. No current or future sales
                      enter prediction features. Conditional offers use a
                      separate redemption estimator and effective-discount
                      proxy.
                    </p>
                  </section>
                  <section className="panel">
                    <h2>What the evidence supports</h2>
                    <Warnings items={summary.model.limitations} />
                  </section>
                </div>
                <div className="notice">
                  {summary.model.validation_scope} Enterprise readiness requires
                  client-specific validation, controlled promotion pilots,
                  security controls and operational monitoring.
                </div>
              </>
            )}
          </TabsContent>
          <TabsContent value="data">
            <div className="two-cols connection-grid">
              <section className="panel">
                <div className="section-title">
                  <h2>Active dataset</h2>
                  <span className="pill green">Validated</span>
                </div>
                <Picker
                  label="Workspace"
                  value={workspace}
                  options={[
                    { value: 'benchmark', label: 'dunnhumby benchmark' },
                    ...datasets
                      .filter((d) => d.status === 'ready')
                      .map((d) => ({ value: d.id, label: d.name })),
                  ]}
                  onChange={setWorkspace}
                />
                <h3 className="source-name">{summary?.quality?.source}</h3>
                <div className="data-stats">
                  <div>
                    <strong>{number(summary?.quality?.rows)}</strong>
                    <span>Weekly observations</span>
                  </div>
                  <div>
                    <strong>{summary?.quality?.products}</strong>
                    <span>Products</span>
                  </div>
                  <div>
                    <strong>{summary?.quality?.stores}</strong>
                    <span>Stores</span>
                  </div>
                  <div>
                    <strong>{summary?.quality?.weeks}</strong>
                    <span>Weeks</span>
                  </div>
                </div>
                <div className="financial-row">
                  <span>Coverage</span>
                  <strong>
                    {summary?.quality?.start} → {summary?.quality?.end}
                  </strong>
                </div>
                <div className="financial-row">
                  <span>Promoted observations</span>
                  <strong>{percent(summary?.quality?.promo_share)}</strong>
                </div>
                <div className="financial-row">
                  <span>Excluded invalid / sparse rows</span>
                  <strong>
                    {number(summary?.quality?.excluded_invalid_rows || 0)}
                  </strong>
                </div>
                <div className="financial-row">
                  <span>Absent panel observations</span>
                  <strong>
                    {number(summary?.quality?.missing_panel_rows)}
                  </strong>
                </div>
                <p className="micro">
                  Missing product/store weeks stay missing; they are not assumed
                  to be zero demand. Missing inventory prevents stockout
                  correction.
                </p>
                <a
                  className="source-link"
                  href="https://www.dunnhumby.com/source-files/"
                  target="_blank"
                  rel="noreferrer"
                >
                  Source data & documentation
                  <ArrowUpRight size={15} />
                </a>
              </section>
              <section className="panel">
                <div className="section-title">
                  <h2>Connected business data</h2>
                  <Database size={19} />
                </div>
                <p className="panel-subtitle">
                  OneSales records sales, returns and inventory directly in
                  MongoDB Atlas. Closed trading days become validated training
                  snapshots.
                </p>
                <div className="validation-success">
                  <FileCheck2 size={22} />
                  <div>
                    <strong>One source of business truth</strong>
                    <span>
                      Promotion plans, sales history and model versions stay
                      connected.
                    </span>
                  </div>
                </div>
                <p className="micro">
                  Use OneSales → Model data to monitor readiness and train
                  candidates. Existing ERP and commerce systems can send
                  validated records through the authenticated integration API.
                </p>
                <p className="micro">
                  Promotion training needs at least 78 weeks. Country, currency,
                  holiday and stockout signals are validated before a snapshot
                  can be trained.
                </p>
              </section>
            </div>
            {datasets.length > 0 && (
              <section className="panel dataset-list">
                <div className="section-title">
                  <h2>Connected workspaces</h2>
                  <Button
                    variant="ghost"
                    onClick={() =>
                      run('refresh', async () =>
                        setDatasets(await api('/datasets')),
                      )
                    }
                  >
                    <RefreshCw />
                    Refresh
                  </Button>
                </div>
                {datasets.map((d) => (
                  <div className="dataset-row" key={d.id}>
                    <Database size={19} />
                    <div>
                      <strong>{d.name}</strong>
                      <span>{d.error || d.status}</span>
                    </div>
                    {d.status === 'ready' ? (
                      <Button
                        onClick={() => {
                          setWorkspace(d.id);
                          setReload((x) => x + 1);
                          notify('Switched to the trained client workspace.');
                        }}
                      >
                        Use workspace
                        <ArrowRight />
                      </Button>
                    ) : ['queued', 'training'].includes(d.status) ? (
                      <span className="training-state">
                        <LoaderCircle className="spin" size={17} />
                        {d.status === 'queued' ? 'Queued for training' : 'Training model'}
                      </span>
                    ) : (
                      <Button
                        disabled={!!busy}
                        onClick={() =>
                          run('train', async () => {
                            await api(
                              '/datasets/' + d.id + '/train',
                              'benchmark',
                              {},
                            );
                            setDatasets(await api('/datasets'));
                            notify(
                              'Model training started. This page will update when it finishes.',
                            );
                          })
                        }
                      >
                        <Play />
                        Train model
                      </Button>
                    )}
                  </div>
                ))}
              </section>
            )}
            <div className="notice">
              Each connected workspace has its own dataset, model, saved plans
              and audit records. This release runs for one user on localhost;
              shared hosting requires authentication and authorization.
            </div>
          </TabsContent>
          <TabsContent value="basket">
            {summary?.baskets ? (
              <>
                <div className="metric-grid three">
                  <Metric
                    label="Transaction lines"
                    value={number(summary.baskets.rows)}
                    sub="The Complete Journey"
                  />
                  <Metric
                    label="Shopping baskets"
                    value={number(summary.baskets.baskets)}
                    sub="All transaction baskets"
                  />
                  <Metric
                    label="Households"
                    value={number(summary.baskets.households)}
                    sub="Historical household panel"
                  />
                </div>
                <div className="insight-strip">
                  <Layers size={22} />
                  <div>
                    <strong>Discover products that travel together.</strong>
                    <p>
                      {summary.baskets.interpretation} These relationships are
                      shown for analysis and are not injected into the demand
                      model as causal halo.
                    </p>
                  </div>
                </div>
                <section className="panel table-panel">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Category</TableHead>
                        <TableHead>Often bought with</TableHead>
                        <TableHead className="numeric">
                          Shared baskets
                        </TableHead>
                        <TableHead className="numeric">
                          Association lift
                        </TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {summary.baskets.relationships.map((r: any) => (
                        <TableRow key={r.category_a + r.category_b}>
                          <TableCell>{r.category_a}</TableCell>
                          <TableCell>{r.category_b}</TableCell>
                          <TableCell className="numeric">
                            {number(r.baskets)}
                          </TableCell>
                          <TableCell className="numeric positive">
                            {number(r.association_lift, 2)}×
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </section>
                <p className="micro">
                  Lift = P(A and B) ÷ (P(A) × P(B)). Relationships among the 35
                  most frequent categories, with at least 500 shared baskets.
                </p>
              </>
            ) : (
              <section className="panel">
                <Empty
                  title="Basket data is not connected"
                  text="This workspace contains weekly sales. Transaction-level basket IDs and a product mapping are needed to estimate shopping associations."
                />
              </section>
            )}
          </TabsContent>
        </Tabs>
      </fieldset>
    </section>
  );
}
