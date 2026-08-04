import { useMemo } from 'react';
import {
  Bar, BarChart, Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { Badge, Card, CardTitle, Empty, Reveal, Tip } from '@/components/ui';
import { hold, money, num, pct, tone } from '@/lib/format';
import { cn } from '@/lib/api';

const AXIS = { stroke: 'rgb(var(--muted))', fontSize: 11 };
const MIN_RELIABLE = 10;

function entries(groups, key) {
  return Object.entries(groups?.[key] || {}).map(([label, stats]) => ({ label, ...stats }));
}

function TipBox({ title, lines }) {
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-lift">
      <div className="mb-1 font-medium text-ink">{title}</div>
      {lines.filter(Boolean).map(([k, v, cls]) => (
        <div key={k} className="flex justify-between gap-4">
          <span className="text-muted">{k}</span><span className={cn('tabular', cls)}>{v}</span>
        </div>
      ))}
    </div>
  );
}

/** Session performance as cards rather than a table row per session. */
export function SessionCards({ groups }) {
  const rows = entries(groups, 'session').sort((a, b) => b.net - a.net);
  if (!rows.length) return <Card className="mb-5"><CardTitle>Sessions</CardTitle><Empty /></Card>;

  return (
    <Card className="mb-5">
      <CardTitle hint="Grouped by entry time in UTC — the session you opened the position in, not the one it happened to close in.">
        Sessions
      </CardTitle>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        {rows.map((row, i) => (
          <Reveal key={row.label} delay={i * 0.03}>
            <div className={cn('h-full rounded-xl border border-line bg-elevated p-3.5',
              row.trades < MIN_RELIABLE && 'opacity-70')}>
              <div className="flex items-center justify-between">
                <span className="text-[13px] font-medium capitalize text-ink">{row.label}</span>
                {row.trades < MIN_RELIABLE
                  ? <Badge tone="warn">thin</Badge>
                  : <Badge tone={row.net >= 0 ? 'up' : 'down'}>{pct(row.win_rate_pct)}</Badge>}
              </div>
              <div className={cn('tabular mt-2 text-xl font-semibold', tone(row.net))}>
                {money(row.net)}
              </div>
              <dl className="mt-2 space-y-1 text-[11px] text-muted">
                <div className="flex justify-between"><dt>Trades</dt><dd className="tabular">{row.trades}</dd></div>
                <div className="flex justify-between"><dt>Avg</dt>
                  <dd className={cn('tabular', tone(row.avg))}>{money(row.avg)}</dd></div>
                <div className="flex justify-between"><dt>Avg hold</dt>
                  <dd className="tabular">{hold(row.avg_duration_sec)}</dd></div>
                <div className="flex justify-between"><dt>Best / worst</dt>
                  <dd className="tabular">{money(row.best)} / {money(row.worst)}</dd></div>
              </dl>
            </div>
          </Reveal>
        ))}
      </div>
    </Card>
  );
}

/** Weekday performance as a horizontal bar chart. */
export function WeekdayBars({ groups }) {
  const ORDER = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];
  const rows = entries(groups, 'weekday')
    .sort((a, b) => ORDER.indexOf(a.label) - ORDER.indexOf(b.label));
  if (!rows.length) return <Card><CardTitle>Weekdays</CardTitle><Empty /></Card>;

  return (
    <Card>
      <CardTitle hint="Net by day of week, entry time.">Weekdays</CardTitle>
      <div className="h-[260px] w-full">
        <ResponsiveContainer>
          <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 12, bottom: 0, left: 18 }}>
            <XAxis type="number" tick={AXIS} tickLine={false} axisLine={false} />
            <YAxis type="category" dataKey="label" tick={AXIS} tickLine={false} axisLine={false}
              width={72} tickFormatter={v => v.slice(0, 3)} />
            <Tooltip cursor={{ fill: 'rgb(var(--elevated))' }}
              content={({ active, payload }) => active && payload?.[0] && (
                <TipBox title={payload[0].payload.label} lines={[
                  ['Trades', payload[0].payload.trades],
                  ['Win rate', pct(payload[0].payload.win_rate_pct)],
                  ['Net', money(payload[0].payload.net),
                    payload[0].payload.net >= 0 ? 'text-up' : 'text-down'],
                  ['Avg hold', hold(payload[0].payload.avg_duration_sec)],
                  payload[0].payload.trades < MIN_RELIABLE && ['Note', 'thin sample', 'text-warn'],
                ]} />
              )} />
            <Bar dataKey="net" radius={[0, 4, 4, 0]} animationDuration={500}>
              {rows.map((r, i) => (
                <Cell key={i} fill={r.net >= 0 ? 'rgb(var(--up))' : 'rgb(var(--down))'}
                  fillOpacity={r.trades >= MIN_RELIABLE ? 0.85 : 0.32} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}

/** How trades end, as a donut plus the numbers behind it. */
export function ExitAnalysis({ groups }) {
  const rows = entries(groups, 'exit_reason').sort((a, b) => b.trades - a.trades);
  if (!rows.length) return <Card><CardTitle>Exits</CardTitle><Empty /></Card>;

  const total = rows.reduce((sum, r) => sum + r.trades, 0);
  const palette = ['rgb(var(--down))', 'rgb(var(--up))', 'rgb(var(--info))',
    'rgb(var(--warn))', 'rgb(var(--accent))'];

  return (
    <Card>
      <CardTitle hint="Share of trades by how they closed.">Exits</CardTitle>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="h-[190px] w-full sm:w-1/2">
          <ResponsiveContainer>
            <PieChart>
              <Pie data={rows} dataKey="trades" nameKey="label" innerRadius="55%" outerRadius="85%"
                paddingAngle={2} animationDuration={500} stroke="none">
                {rows.map((r, i) => <Cell key={i} fill={palette[i % palette.length]} fillOpacity={0.85} />)}
              </Pie>
              <Tooltip content={({ active, payload }) => active && payload?.[0] && (
                <TipBox title={payload[0].payload.label} lines={[
                  ['Trades', `${payload[0].payload.trades} (${pct(payload[0].payload.trades / total * 100)})`],
                  ['Win rate', pct(payload[0].payload.win_rate_pct)],
                  ['Net', money(payload[0].payload.net),
                    payload[0].payload.net >= 0 ? 'text-up' : 'text-down'],
                ]} />
              )} />
              <Legend verticalAlign="bottom" height={24}
                formatter={v => <span className="text-[11px] text-muted">{v}</span>} />
            </PieChart>
          </ResponsiveContainer>
        </div>
        <div className="w-full sm:w-1/2">
          <table className="w-full text-[11px]">
            <thead className="text-muted">
              <tr><th className="text-left font-medium">exit</th>
                <th className="text-right font-medium">n</th>
                <th className="text-right font-medium">win%</th>
                <th className="text-right font-medium">net</th></tr>
            </thead>
            <tbody className="tabular">
              {rows.map((r, i) => (
                <tr key={r.label} className="border-t border-line/60">
                  <td className="py-1.5 text-left">
                    <span className="mr-1.5 inline-block h-2 w-2 rounded-full align-middle"
                      style={{ background: palette[i % palette.length] }} />
                    {r.label}
                  </td>
                  <td className="text-right">{r.trades}</td>
                  <td className="text-right">{pct(r.win_rate_pct)}</td>
                  <td className={cn('text-right', tone(r.net))}>{money(r.net)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Card>
  );
}

/** Per-symbol cards with best/worst badges. */
export function SymbolCards({ groups }) {
  const rows = entries(groups, 'symbol').sort((a, b) => b.net - a.net);
  if (!rows.length) return null;
  const best = rows[0];
  const worst = rows[rows.length - 1];

  return (
    <Card className="mb-5">
      <CardTitle hint="Net by instrument.">Symbols</CardTitle>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {rows.map((row, i) => (
          <Reveal key={row.label} delay={i * 0.03}>
            <div className="h-full rounded-xl border border-line bg-elevated p-3.5">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-[13px] font-medium text-ink">{row.label}</span>
                {rows.length > 1 && row.label === best.label && <Badge tone="up">best</Badge>}
                {rows.length > 1 && row.label === worst.label && <Badge tone="down">worst</Badge>}
              </div>
              <div className={cn('tabular mt-2 text-xl font-semibold', tone(row.net))}>
                {money(row.net)}
              </div>
              <dl className="mt-2 space-y-1 text-[11px] text-muted">
                <div className="flex justify-between"><dt>Trades</dt><dd className="tabular">{row.trades}</dd></div>
                <div className="flex justify-between"><dt>Win rate</dt><dd className="tabular">{pct(row.win_rate_pct)}</dd></div>
                <div className="flex justify-between"><dt>Avg hold</dt><dd className="tabular">{hold(row.avg_duration_sec)}</dd></div>
                <div className="flex justify-between"><dt>Volume</dt><dd className="tabular">{num(row.volume)}</dd></div>
              </dl>
            </div>
          </Reveal>
        ))}
      </div>
    </Card>
  );
}

/** Month-by-month summary, derived from the daily series. */
export function MonthlyReport({ daily }) {
  const months = useMemo(() => {
    const map = new Map();
    for (const day of daily || []) {
      const key = day.date.slice(0, 7);
      const bucket = map.get(key) || { month: key, net: 0, trades: 0, wins: 0, days: 0 };
      bucket.net += day.net;
      bucket.trades += day.trades;
      bucket.wins += day.wins;
      bucket.days += 1;
      map.set(key, bucket);
    }
    return [...map.values()].map(m => ({
      ...m, net: Number(m.net.toFixed(2)),
      win_rate_pct: m.trades ? (m.wins / m.trades) * 100 : null,
    }));
  }, [daily]);

  if (!months.length) return null;

  return (
    <Card className="mb-5">
      <CardTitle hint="Aggregated from the daily series, so it always matches the calendar above.">
        Monthly
      </CardTitle>
      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {months.map((m, i) => (
          <Reveal key={m.month} delay={i * 0.02}>
            <Tip content={
              <div className="tabular space-y-0.5">
                <div>{m.trades} trades over {m.days} trading days</div>
                <div>{pct(m.win_rate_pct)} win rate</div>
              </div>
            }>
              <div className="rounded-xl border border-line bg-elevated p-3">
                <div className="text-[11px] uppercase tracking-wider text-muted">{m.month}</div>
                <div className={cn('tabular mt-1 text-lg font-semibold', tone(m.net))}>
                  {money(m.net)}
                </div>
                <div className="tabular text-[11px] text-muted">
                  {m.trades} trades · {pct(m.win_rate_pct)}
                </div>
              </div>
            </Tip>
          </Reveal>
        ))}
      </div>
    </Card>
  );
}

/** Measurable behaviour only — the unmeasurable traits are named, not scored. */
export function BehaviourPanel({ behaviour }) {
  if (!behaviour) return null;
  const { reentry_timing: timing, hold_asymmetry: asym, size_escalation: size } = behaviour;

  const rows = [
    { label: 'Re-entry after a loss', value: hold(timing?.after_loss_sec),
      compare: `vs ${hold(timing?.after_win_sec)} after a win`,
      n: Math.min(timing?.samples_after_loss ?? 0, timing?.samples_after_win ?? 0) },
    { label: 'Average hold, winners', value: hold(asym?.avg_win_sec),
      compare: `vs ${hold(asym?.avg_loss_sec)} for losers`, n: asym?.samples ?? 0 },
    size && { label: 'Size after a loss', value: `${num(size.size_ratio_after_loss)}×`,
      compare: `vs ${num(size.size_ratio_after_win)}× after a win`, n: size.samples },
  ].filter(Boolean);

  return (
    <Card className="mb-5">
      <CardTitle hint="Only behaviours that leave a trace in timestamps and volumes.">
        Measured behaviour
      </CardTitle>
      <div className="grid gap-3 sm:grid-cols-3">
        {rows.map(row => (
          <div key={row.label} className="rounded-xl border border-line bg-elevated p-3.5">
            <div className="text-[11px] uppercase tracking-wider text-muted">{row.label}</div>
            <div className="tabular mt-1 text-lg font-semibold text-ink">{row.value}</div>
            <div className="text-[11px] text-muted">{row.compare}</div>
            <div className="tabular mt-1 text-[11px] text-muted/70">n={row.n}</div>
          </div>
        ))}
      </div>
      <p className="mt-3 text-xs text-muted">
        Not shown: {behaviour.not_measurable?.join(', ')} — these leave no trace in fill data.
        Scoring them would mean inventing the numbers.
      </p>
    </Card>
  );
}
