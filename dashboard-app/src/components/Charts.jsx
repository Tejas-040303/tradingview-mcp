import { useMemo, useState } from 'react';
import {
  Area, AreaChart, Bar, BarChart, Brush, CartesianGrid, Cell, ComposedChart, Line,
  ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis,
} from 'recharts';
import { Button, Card, CardTitle, Empty } from '@/components/ui';
import { compact, hold, money, num, pct, shortDate } from '@/lib/format';
import { cn, downloadFile, toCsv } from '@/lib/api';

const AXIS = { stroke: 'rgb(var(--muted))', fontSize: 11 };
const GRID = 'rgb(var(--line))';

function Panel({ children, label, rows, columns, filename, hint, right }) {
  return (
    <Card className="mb-5">
      <CardTitle hint={hint} right={
        <div className="flex items-center gap-1.5">
          {right}
          {rows?.length > 0 && (
            <Button variant="ghost" size="sm"
              onClick={() => downloadFile(filename, 'text/csv', toCsv(rows, columns))}>
              CSV
            </Button>
          )}
        </div>
      }>
        {label}
      </CardTitle>
      {children}
    </Card>
  );
}

function TipBox({ title, lines }) {
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-lift">
      <div className="mb-1 font-medium text-ink">{title}</div>
      {lines.filter(Boolean).map(([k, v, cls]) => (
        <div key={k} className="flex justify-between gap-4">
          <span className="text-muted">{k}</span>
          <span className={cn('tabular', cls)}>{v}</span>
        </div>
      ))}
    </div>
  );
}

/**
 * Equity curve with brush-zoom and a crosshair.
 *
 * Plots balance when a starting balance was supplied and cumulative P&L
 * otherwise — the same choice the API makes, so the axis label always matches
 * what the numbers mean.
 */
export function EquityChart({ curve, drawdown }) {
  const hasBalance = Boolean(curve?.length && curve[0].balance !== undefined);
  const key = hasBalance ? 'balance' : 'cumulative';

  const data = useMemo(() => (curve || []).map((p, i) => ({
    i, value: p[key], time: p.time, profit: p.profit, symbol: p.symbol,
  })), [curve, key]);

  if (data.length < 2) {
    return (
      <Panel label="Equity curve">
        <Empty>Not enough closed trades to plot a curve.</Empty>
      </Panel>
    );
  }

  const peak = drawdown?.peak_at;
  const trough = drawdown?.trough_at;
  const peakIndex = data.findIndex(d => d.time === peak);
  const troughIndex = data.findIndex(d => d.time === trough);
  const positive = data[data.length - 1].value >= (hasBalance ? data[0].value : 0);

  return (
    <Panel
      label="Equity curve"
      hint={`${hasBalance ? 'Account balance' : 'Cumulative realised P&L'} · ${data.length} trades · drag the strip below to zoom`}
      rows={data} columns={['time', 'value', 'profit', 'symbol']} filename="equity-curve.csv"
    >
      <div className="h-[320px] w-full">
        <ResponsiveContainer>
          <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
            <defs>
              <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={positive ? 'rgb(var(--up))' : 'rgb(var(--down))'}
                  stopOpacity={0.28} />
                <stop offset="100%" stopColor={positive ? 'rgb(var(--up))' : 'rgb(var(--down))'}
                  stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="time" tick={AXIS} tickLine={false} axisLine={false}
              tickFormatter={shortDate} minTickGap={48} />
            <YAxis tick={AXIS} tickLine={false} axisLine={false} width={64}
              tickFormatter={v => compact(v)} />
            {!hasBalance && <ReferenceLine y={0} stroke="rgb(var(--muted))" strokeDasharray="4 3" />}
            {peakIndex >= 0 && (
              <ReferenceLine x={data[peakIndex].time} stroke="rgb(var(--up))"
                strokeDasharray="3 3" label={{ value: 'peak', fill: 'rgb(var(--up))', fontSize: 10 }} />
            )}
            {troughIndex >= 0 && (
              <ReferenceLine x={data[troughIndex].time} stroke="rgb(var(--down))"
                strokeDasharray="3 3" label={{ value: 'trough', fill: 'rgb(var(--down))', fontSize: 10 }} />
            )}
            <Tooltip cursor={{ stroke: 'rgb(var(--muted))', strokeDasharray: '3 3' }}
              content={({ active, payload }) => active && payload?.[0] && (
                <TipBox title={shortDate(payload[0].payload.time)} lines={[
                  [hasBalance ? 'Balance' : 'Cumulative', money(payload[0].payload.value, { sign: false })],
                  ['This trade', money(payload[0].payload.profit),
                    payload[0].payload.profit >= 0 ? 'text-up' : 'text-down'],
                  ['Symbol', payload[0].payload.symbol],
                ]} />
              )} />
            <Area type="monotone" dataKey="value" strokeWidth={2} fill="url(#eq)"
              stroke={positive ? 'rgb(var(--up))' : 'rgb(var(--down))'}
              animationDuration={600} dot={false} />
            <Brush dataKey="time" height={22} travellerWidth={8} stroke={GRID}
              fill="rgb(var(--elevated))" tickFormatter={shortDate} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </Panel>
  );
}

/** Per-trade P&L histogram with mean and median marked. */
export function DistributionChart({ distribution, trades }) {
  const stats = useMemo(() => {
    const nets = (trades || []).filter(t => !t.open).map(t => t.net).sort((a, b) => a - b);
    if (!nets.length) return null;
    const mean = nets.reduce((a, b) => a + b, 0) / nets.length;
    const mid = Math.floor(nets.length / 2);
    const median = nets.length % 2 ? nets[mid] : (nets[mid - 1] + nets[mid]) / 2;
    return { mean, median };
  }, [trades]);

  if (!distribution?.length) {
    return <Panel label="P&L distribution"><Empty /></Panel>;
  }

  const data = distribution.map(b => ({ ...b, label: num(b.from, 1) }));

  return (
    <Panel label="P&L distribution"
      hint="Per-trade net, bucketed. Bucket edges are split at zero, so no bar mixes winners with losers."
      rows={distribution} columns={['from', 'to', 'count', 'net']} filename="distribution.csv">
      <div className="h-[240px] w-full">
        <ResponsiveContainer>
          <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
            <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="label" tick={AXIS} tickLine={false} axisLine={false} />
            <YAxis tick={AXIS} tickLine={false} axisLine={false} allowDecimals={false} width={40} />
            {stats && (
              <ReferenceLine x={num(stats.mean, 1)} stroke="rgb(var(--accent))" strokeDasharray="4 3"
                label={{ value: 'mean', fill: 'rgb(var(--accent))', fontSize: 10, position: 'top' }} />
            )}
            <Tooltip cursor={{ fill: 'rgb(var(--elevated))' }}
              content={({ active, payload }) => active && payload?.[0] && (
                <TipBox title={`${payload[0].payload.from} to ${payload[0].payload.to}`} lines={[
                  ['Trades', payload[0].payload.count],
                  ['Net', money(payload[0].payload.net),
                    payload[0].payload.net >= 0 ? 'text-up' : 'text-down'],
                ]} />
              )} />
            <Bar dataKey="count" radius={[4, 4, 0, 0]} animationDuration={500}>
              {data.map((b, i) => (
                <Cell key={i} fill={b.to <= 0 ? 'rgb(var(--down))' : 'rgb(var(--up))'} fillOpacity={0.85} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      {stats && (
        <p className="tabular mt-2 text-xs text-muted">
          mean {money(stats.mean)} · median {money(stats.median)}
        </p>
      )}
    </Panel>
  );
}

/**
 * Holding time against outcome.
 *
 * Two views of one question. The bars answer "does holding longer pay", the
 * scatter shows the spread the bars average away.
 */
export function HoldingAnalysis({ holding }) {
  const [view, setView] = useState('buckets');
  const buckets = holding?.buckets || [];
  const scatter = holding?.scatter || [];

  if (!buckets.length) return <Panel label="Holding time"><Empty /></Panel>;

  const bars = buckets.map(b => ({ ...b, label: hold(b.from_sec) }));
  const points = scatter.map(p => ({ ...p, minutes: p.duration_sec / 60 }));

  return (
    <Panel label="Holding time" hint="Buckets are geometric — durations span seconds to hours."
      rows={buckets} columns={['from_sec', 'to_sec', 'trades', 'net', 'win_rate_pct']}
      filename="holding-time.csv"
      right={
        <div className="flex rounded-lg border border-line p-0.5">
          {['buckets', 'scatter'].map(v => (
            <button key={v} onClick={() => setView(v)}
              className={cn('rounded-md px-2 py-0.5 text-[11px] capitalize transition-colors',
                view === v ? 'bg-elevated text-ink' : 'text-muted hover:text-ink')}>
              {v}
            </button>
          ))}
        </div>
      }>
      <div className="h-[240px] w-full">
        <ResponsiveContainer>
          {view === 'buckets' ? (
            <ComposedChart data={bars} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
              <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="label" tick={AXIS} tickLine={false} axisLine={false} />
              <YAxis yAxisId="net" tick={AXIS} tickLine={false} axisLine={false} width={48}
                tickFormatter={compact} />
              <YAxis yAxisId="wr" orientation="right" tick={AXIS} tickLine={false}
                axisLine={false} width={40} domain={[0, 100]} />
              <ReferenceLine yAxisId="net" y={0} stroke="rgb(var(--muted))" strokeDasharray="4 3" />
              <Tooltip cursor={{ fill: 'rgb(var(--elevated))' }}
                content={({ active, payload }) => active && payload?.[0] && (
                  <TipBox title={`${hold(payload[0].payload.from_sec)} – ${hold(payload[0].payload.to_sec)}`}
                    lines={[
                      ['Trades', payload[0].payload.trades],
                      ['Net', money(payload[0].payload.net),
                        payload[0].payload.net >= 0 ? 'text-up' : 'text-down'],
                      ['Win rate', pct(payload[0].payload.win_rate_pct)],
                      !payload[0].payload.reliable && ['Note', 'thin sample', 'text-warn'],
                    ]} />
                )} />
              <Bar yAxisId="net" dataKey="net" radius={[4, 4, 0, 0]} animationDuration={500}>
                {bars.map((b, i) => (
                  <Cell key={i} fill={b.net >= 0 ? 'rgb(var(--up))' : 'rgb(var(--down))'}
                    fillOpacity={b.reliable ? 0.85 : 0.32} />
                ))}
              </Bar>
              <Line yAxisId="wr" type="monotone" dataKey="win_rate_pct" dot={false}
                stroke="rgb(var(--accent))" strokeWidth={2} strokeDasharray="4 3" />
            </ComposedChart>
          ) : (
            <ScatterChart margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
              <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
              <XAxis type="number" dataKey="minutes" name="Minutes held" tick={AXIS}
                tickLine={false} axisLine={false} tickFormatter={v => `${v.toFixed(0)}m`} />
              <YAxis type="number" dataKey="net" name="Net" tick={AXIS} tickLine={false}
                axisLine={false} width={48} tickFormatter={compact} />
              <ReferenceLine y={0} stroke="rgb(var(--muted))" strokeDasharray="4 3" />
              <Tooltip cursor={{ strokeDasharray: '3 3' }}
                content={({ active, payload }) => active && payload?.[0] && (
                  <TipBox title={payload[0].payload.symbol} lines={[
                    ['Held', hold(payload[0].payload.duration_sec)],
                    ['Net', money(payload[0].payload.net),
                      payload[0].payload.net >= 0 ? 'text-up' : 'text-down'],
                    ['Side', payload[0].payload.direction],
                  ]} />
                )} />
              <Scatter data={points} animationDuration={500}>
                {points.map((p, i) => (
                  <Cell key={i} fill={p.net >= 0 ? 'rgb(var(--up))' : 'rgb(var(--down))'}
                    fillOpacity={0.55} />
                ))}
              </Scatter>
            </ScatterChart>
          )}
        </ResponsiveContainer>
      </div>
      <p className="mt-2 text-xs text-muted">
        Faded bars are below the reliability threshold ({holding?.min_reliable ?? 10} trades).
      </p>
    </Panel>
  );
}

/** Position size against outcome — do bigger positions do worse? */
export function SizeAnalysis({ sizes }) {
  if (!sizes?.length) return <Panel label="Position size"><Empty /></Panel>;

  const data = sizes.map(s => ({ ...s, z: s.trades }));
  return (
    <Panel label="Position size" hint="Bubble area is the number of trades at that size."
      rows={sizes} columns={['volume', 'trades', 'net', 'avg', 'win_rate_pct']}
      filename="position-size.csv">
      <div className="h-[240px] w-full">
        <ResponsiveContainer>
          <ScatterChart margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
            <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
            <XAxis type="number" dataKey="volume" name="Lots" tick={AXIS} tickLine={false}
              axisLine={false} />
            <YAxis type="number" dataKey="net" name="Net" tick={AXIS} tickLine={false}
              axisLine={false} width={48} tickFormatter={compact} />
            <ZAxis type="number" dataKey="z" range={[60, 520]} />
            <ReferenceLine y={0} stroke="rgb(var(--muted))" strokeDasharray="4 3" />
            <Tooltip cursor={{ strokeDasharray: '3 3' }}
              content={({ active, payload }) => active && payload?.[0] && (
                <TipBox title={`${payload[0].payload.volume} lots`} lines={[
                  ['Trades', payload[0].payload.trades],
                  ['Net', money(payload[0].payload.net),
                    payload[0].payload.net >= 0 ? 'text-up' : 'text-down'],
                  ['Avg', money(payload[0].payload.avg)],
                  ['Win rate', pct(payload[0].payload.win_rate_pct)],
                  !payload[0].payload.reliable && ['Note', 'thin sample', 'text-warn'],
                ]} />
              )} />
            <Scatter data={data} animationDuration={500}>
              {data.map((s, i) => (
                <Cell key={i} fill={s.net >= 0 ? 'rgb(var(--up))' : 'rgb(var(--down))'}
                  fillOpacity={s.reliable ? 0.6 : 0.25} />
              ))}
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </Panel>
  );
}

/** The chart chunk's entry point, so App only needs one lazy import. */
export function ChartsSection({ data }) {
  return (
    <>
      <EquityChart curve={data.equity_curve} drawdown={data.drawdown} />
      <div className="mb-5 grid gap-4 xl:grid-cols-2">
        <HoldingAnalysis holding={data.holding} />
        <SizeAnalysis sizes={data.sizes} />
      </div>
      <DistributionChart distribution={data.distribution} trades={data.trades} />
    </>
  );
}
