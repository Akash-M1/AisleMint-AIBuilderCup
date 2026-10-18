'use client';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
Table,
TableBody,
TableCell,
TableHead,
TableHeader,
TableRow,
} from '@/components/ui/table';
import { api } from '@/lib/api';
import '@/styles/data-platform.css';
import { ArrowRight,RefreshCw } from 'lucide-react';
import { useEffect,useState } from 'react';
import {
CartesianGrid,
Line,
LineChart,
ResponsiveContainer,
Tooltip,
XAxis,
YAxis,
} from 'recharts';

const fmt = (n: number | null | undefined) =>
  n == null
    ? 'Not supplied'
    : n.toLocaleString(undefined, { maximumFractionDigits: 1 });
const time = (s: string) => (s ? new Date(s).toLocaleString() : 'Not yet');
const label = (s: string) => (s || 'Awaiting data').replaceAll('_', ' ');
function Badge({ children }: { children: React.ReactNode }) {
  return <span className="dp-badge">{children}</span>;
}

export default function ConnectedAnalytics({
  workspace,
  openConnections,
}: {
  workspace: string;
  openConnections: () => void;
}) {
  const [data, setData] = useState<any>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [start, setStart] = useState(''),
    [end, setEnd] = useState('');
  const load = async () => {
    setBusy(true);
    setError('');
    try {
      const query = new URLSearchParams();
      if (start) query.set('start', start);
      if (end) query.set('end', end);
      setData(
        await api(
          `/retail/shops/${workspace}/data/analytics?${query}`,
          workspace,
        ),
      );
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    let live = true;
    setData(null);
    api(`/retail/shops/${workspace}/data/analytics`, workspace)
      .then((r) => {
        if (live) setData(r);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [workspace]);
  const money = (n: number | null | undefined) =>
    n == null
      ? 'Not supplied'
      : new Intl.NumberFormat(undefined, {
          style: 'currency',
          currency: data?.currency || 'INR',
          maximumFractionDigits: 0,
        }).format(n);
  return (
    <div className="dp-app">
      <section className="panel">
        <div className="dp-heading">
          <div>
            <h2>Business performance across your sources</h2>
            <p>
              {data
                ? `${data.date_start || 'No sales'} → ${data.date_end || 'No sales'} · published release ${data.release.id.slice(0, 8)}`
                : 'Connect sales sources to see the combined business.'}
            </p>
          </div>
          <Button variant="outline" onClick={openConnections}>
            Data connections
            <ArrowRight size={16} />
          </Button>
        </div>
        <div className="dp-settings">
          <label>
            From
            <Input
              type="date"
              value={start}
              onChange={(e) => setStart(e.target.value)}
            />
          </label>
          <label>
            Through
            <Input
              type="date"
              value={end}
              onChange={(e) => setEnd(e.target.value)}
            />
          </label>
          <Button variant="outline" disabled={busy} onClick={() => void load()}>
            <RefreshCw size={16} />
            Apply & refresh
          </Button>
        </div>
        {error && (
          <p className="retail-error" role="alert">
            {error}
          </p>
        )}
        {data?.release.demo && (
          <p className="dp-demo">
            Simulated trading history for demonstrating the workflow. These are
            not actual retailer results.
          </p>
        )}
        {data && (
          <>
            <div className="dp-kpis">
              {[
                [money(data.totals.net_revenue), 'Net sales excluding tax'],
                [
                  money(data.totals.gross_revenue),
                  'Gross sales before refunds',
                ],
                [fmt(data.totals.gross_units), 'Gross units sold'],
                [money(data.totals.refunds), 'Refunds'],
                [
                  money(data.totals.gross_cogs),
                  'Cost of sold units before returns',
                ],
                [fmt(data.products), 'Products with sales'],
              ].map(([v, l]) => (
                <article key={l}>
                  <span>{l}</span>
                  <strong>{v}</strong>
                </article>
              ))}
            </div>
            <h3>Sales over time</h3>
            <div className="dp-chart">
              <ResponsiveContainer width="100%" height={280}>
                <LineChart data={data.daily}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="date" minTickGap={70} />
                  <YAxis width={70} tickFormatter={(v) => fmt(v)} />
                  <Tooltip formatter={(v: any) => money(v)} />
                  <Line
                    dataKey="gross_revenue"
                    name="Gross sales"
                    stroke="#217860"
                    dot={false}
                    strokeWidth={2}
                  />
                  <Line
                    dataKey="net_revenue"
                    name="Net sales"
                    stroke="#4e79b8"
                    dot={false}
                    strokeWidth={2}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <div className="dp-kpis">
              <article>
                <span>Stock units across latest product snapshots</span>
                <strong>{fmt(data.inventory.on_hand)}</strong>
              </article>
              <article>
                <span>Stock value with supplied costs</span>
                <strong>{money(data.inventory.known_value)}</strong>
                <small>
                  {data.inventory.cost_coverage} / {data.inventory.products}{' '}
                  products have costs
                </small>
              </article>
              <article>
                <span>Products at or below reorder point</span>
                <strong>{fmt(data.inventory.low_stock)}</strong>
              </article>
            </div>
            <h3>Category performance</h3>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Category</TableHead>
                  <TableHead>Units</TableHead>
                  <TableHead>Gross sales</TableHead>
                  <TableHead>Net sales</TableHead>
                  <TableHead>Promotional sales</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.categories.map((r: any) => (
                  <TableRow key={r.category}>
                    <TableCell>{r.category}</TableCell>
                    <TableCell>{fmt(r.gross_units)}</TableCell>
                    <TableCell>{money(r.gross_revenue)}</TableCell>
                    <TableCell>{money(r.net_revenue)}</TableCell>
                    <TableCell>{money(r.promotional_revenue)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <h3>Product performance</h3>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Product</TableHead>
                  <TableHead>Units</TableHead>
                  <TableHead>Gross sales</TableHead>
                  <TableHead>Net sales</TableHead>
                  <TableHead>Returns</TableHead>
                  <TableHead>Stockout observations</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {[...data.product_metrics]
                  .sort((a: any, b: any) => b.gross_revenue - a.gross_revenue)
                  .slice(0, 100)
                  .map((r: any) => (
                    <TableRow key={r.product_id}>
                      <TableCell>
                        {r.name}
                        <small className="dp-block">{r.product_id}</small>
                      </TableCell>
                      <TableCell>{fmt(r.gross_units)}</TableCell>
                      <TableCell>{money(r.gross_revenue)}</TableCell>
                      <TableCell>{money(r.net_revenue)}</TableCell>
                      <TableCell>{fmt(r.returned_units)}</TableCell>
                      <TableCell>
                        {r.stockout_coverage
                          ? `${r.stockout_observations} / ${r.stockout_coverage}`
                          : 'Not supplied'}
                      </TableCell>
                    </TableRow>
                  ))}
              </TableBody>
            </Table>
            <small>
              Top 100 products by gross sales in the selected period.
            </small>
            <details className="dp-contract">
              <summary>Coverage and interpretation</summary>
              {data.limitations.map((s: string) => (
                <p key={s}>{s}</p>
              ))}
              <p>
                Inventory uses the latest supplied snapshot for each product and
                is independent of the sales date filter. Verify stock dates in
                Data connections before ordering.
              </p>
            </details>
          </>
        )}
      </section>
    </div>
  );
}
