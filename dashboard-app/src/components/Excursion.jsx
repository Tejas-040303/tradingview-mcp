import {
  CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart,
  Tooltip, XAxis, YAxis,
} from 'recharts';
import { Activity, Loader2 } from 'lucide-react';
import { Badge, Button, Card, CardTitle, Empty, Reveal, Tip } from '@/components/ui';
import { hold, money, num, pct } from '@/lib/format';
import { cn } from '@/lib/api';

const AXIS = { stroke: 'rgb(var(--muted))', fontSize: 11 };
const GRID = 'rgb(var(--line))';

/**
 * Post-trade excursion.
 *
 * The panel exists for one number: the share of losing exits where price came
 * straight back to the entry. A high figure is evidence the stop sat inside
 * normal noise; a low one says the entry was simply wrong. The exit
 * distribution alone cannot separate those two, and every other panel on this
 * page has had to leave the question open.
 */
export default function Excursion({ data, enabled, onEnable, isFetching }) {
  if (!enabled) {
    return (
      <Card className="mb-5">
        <CardTitle hint="Reads bar data around every trade, so it is slower than the rest of this page and is not loaded by default.">
          Excursion analysis
        </CardTitle>
        <div className="flex flex-col items-start gap-3 py-4">
          <p className="max-w-2xl text-[13px] leading-relaxed text-muted">
            How far each trade went against you before it worked, how much of the
            available move you took, and — for losing exits — whether price
            returned to your entry within 5, 15 or 60 minutes. That last figure
            is the direct test of <em>“my stop sat inside normal noise”</em>
            {' '}against <em>“my entry was wrong”</em>.
          </p>
          <Button variant="accent" onClick={onEnable} disabled={isFetching}>
            {isFetching
              ? <><Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading…</>
              : <><Activity className="h-3.5 w-3.5" /> Run excursion analysis</>}
          </Button>
        </div>
      </Card>
    );
  }

  if (data?.success === false) {
    return (
      <Card className="mb-5">
        <CardTitle>Excursion analysis</CardTitle>
        <p className="text-xs text-down">{data.error}</p>
        <p className="mt-2 text-xs text-muted">
          This usually means the terminal has no minute-bar history downloaded for
          the period. Scroll back on an M1 chart for the symbol, then refresh.
        </p>
      </Card>
    );
  }

  const summary = data?.summary;
  if (!summary) {
    return (
      <Card className="mb-5">
        <CardTitle>Excursion analysis</CardTitle>
        <Empty>
          {data?.analysed === 0
            ? 'No trades could be matched to bar data in this window.'
            : 'Nothing to analyse in this selection.'}
        </Empty>
      </Card>
    );
  }

  return (
    <>
      <ShakeoutPanel summary={summary} analysed={data.analysed}
        unavailable={data.unavailable} />
      <HeatPanel rows={data.rows} summary={summary} />
    </>
  );
}

/** The headline: did price come back after stopping you out? */
function ShakeoutPanel({ summary, analysed, unavailable }) {
  const buckets = Object.entries(summary.by_exit_reason || {});
  const horizons = summary.horizons || [];

  return (
    <Card className="mb-5">
      <CardTitle
        hint={`${analysed} trades matched to bar data. The shakeout rate is computed only over losing exits — for a winning exit the question is meaningless, since price is already past your entry.`}
        right={summary.overall?.reliable === false
          && <Badge tone="warn">thin sample</Badge>}>
        Did price come back?
      </CardTitle>

      {unavailable && (
        <p className="mb-3 text-xs text-warn">
          No bars for: {Object.keys(unavailable).join(', ')}
        </p>
      )}

      <div className="overflow-x-auto">
        <table className="tabular w-full text-[12px]">
          <thead className="text-muted">
            <tr>
              <th className="px-2 py-1.5 text-left font-medium">exit</th>
              <th className="px-2 py-1.5 text-right font-medium">n</th>
              <th className="px-2 py-1.5 text-right font-medium">avg MAE</th>
              <th className="px-2 py-1.5 text-right font-medium">avg MFE</th>
              <th className="px-2 py-1.5 text-right font-medium">capture</th>
              {horizons.map(h => (
                <th key={h} className="px-2 py-1.5 text-right font-medium">
                  back @ {h / 60}m
                </th>
              ))}
              <th className="px-2 py-1.5 text-right font-medium">median return</th>
            </tr>
          </thead>
          <tbody>
            {buckets.map(([name, b]) => (
              <tr key={name} className={cn('border-t border-line/60',
                !b.reliable && 'opacity-70')}>
                <td className="px-2 py-1.5 text-left">
                  {name}
                  {!b.reliable && <span className="ml-1.5 text-warn">thin</span>}
                </td>
                <td className="px-2 py-1.5 text-right">{b.trades}</td>
                <td className="px-2 py-1.5 text-right text-down">{num(b.avg_mae, 2)}</td>
                <td className="px-2 py-1.5 text-right text-up">{num(b.avg_mfe, 2)}</td>
                <td className="px-2 py-1.5 text-right">{num(b.avg_capture_ratio, 2)}</td>
                {horizons.map(h => {
                  const post = b[`post_${h}`];
                  const value = post?.reached_entry_pct;
                  return (
                    <td key={h} className="px-2 py-1.5 text-right">
                      {value === null || value === undefined ? (
                        <Tip content="Not applicable — this bucket has no losing exits, and for a winning exit price is already past the entry.">
                          <span className="cursor-help text-muted">n/a</span>
                        </Tip>
                      ) : (
                        <span className={value >= 50 ? 'text-warn' : ''}>{pct(value)}</span>
                      )}
                    </td>
                  );
                })}
                <td className="px-2 py-1.5 text-right">
                  {b.median_return_sec == null ? '—' : hold(b.median_return_sec)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Interpretation summary={summary} />
    </Card>
  );
}

/**
 * One sentence stating what the numbers support — and, just as often, what they
 * do not. Thresholds are deliberately wide; a marginal figure gets no verdict.
 */
function Interpretation({ summary }) {
  const stop = summary.by_exit_reason?.stop_loss;
  const short = summary.horizons?.[0];
  if (!stop || !short) return null;

  const post = stop[`post_${short}`];
  const rate = post?.reached_entry_pct;
  const n = post?.shakeout_samples ?? 0;
  const minutes = short / 60;

  if (rate === null || rate === undefined || n < (summary.min_reliable ?? 10)) {
    return (
      <p className="mt-3 text-xs text-muted">
        Not enough stopped-out trades with bar coverage to read anything into
        this yet — {n} so far.
      </p>
    );
  }

  const verdict = rate >= 60
    ? {
      tone: 'text-warn',
      text: `${pct(rate)} of your stop-outs saw price back at the entry within `
        + `${minutes} minutes. That is consistent with stops sitting inside normal `
        + `noise for this instrument — worth testing a wider stop against the same `
        + `entries before changing anything else.`,
    }
    : rate <= 30
      ? {
        tone: 'text-muted',
        text: `Only ${pct(rate)} of stop-outs saw price return within ${minutes} `
          + `minutes, so the stops are mostly being hit on real moves rather than `
          + `noise. Widening them would likely just increase the loss per trade.`,
      }
      : {
        tone: 'text-muted',
        text: `${pct(rate)} of stop-outs came back within ${minutes} minutes — `
          + `mixed. Neither "stops too tight" nor "entries wrong" is supported `
          + `on its own here.`,
      };

  return (
    <p className={cn('mt-3 max-w-3xl text-xs leading-relaxed', verdict.tone)}>
      {verdict.text} <span className="text-muted/70">(n={n})</span>
    </p>
  );
}

/** Heat taken against move offered, one point per trade. */
function HeatPanel({ rows, summary }) {
  if (!rows?.length) return null;

  const points = rows.map(r => ({
    mae: r.mae, mfe: r.mfe, net: r.net, symbol: r.symbol,
    exit_reason: r.exit_reason, capture: r.capture_ratio,
  }));
  // Rounded up to a quarter: an axis ending at 5.48037 clips its own label and
  // reads as precision the numbers do not have.
  const limit = Math.ceil(Math.max(...points.flatMap(p => [p.mae, p.mfe]), 1) * 4) / 4;

  return (
    <Card className="mb-5">
      <CardTitle hint="Each point is a trade. Above the diagonal, the trade offered more than it cost you; below it, you sat through more heat than the move was ever worth.">
        Heat taken vs move offered
      </CardTitle>
      <div className="h-[280px] w-full">
        <ResponsiveContainer>
          <ScatterChart margin={{ top: 8, right: 12, bottom: 0, left: -14 }}>
            <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
            <XAxis type="number" dataKey="mae" name="MAE" tick={AXIS}
              tickLine={false} axisLine={false} domain={[0, limit]}
              tickFormatter={v => num(v, 1)}
              label={{ value: 'went against you', position: 'insideBottom',
                offset: -2, fill: 'rgb(var(--muted))', fontSize: 11 }} />
            <YAxis type="number" dataKey="mfe" name="MFE" tick={AXIS}
              tickLine={false} axisLine={false} width={44} domain={[0, limit]}
              tickFormatter={v => num(v, 1)}
              label={{ value: 'offered', angle: -90, position: 'insideLeft',
                fill: 'rgb(var(--muted))', fontSize: 11 }} />
            {/* Break-even diagonal: heat equals the move on offer. */}
            <ReferenceLine segment={[{ x: 0, y: 0 }, { x: limit, y: limit }]}
              stroke="rgb(var(--muted))" strokeDasharray="4 3" />
            <Tooltip cursor={{ strokeDasharray: '3 3' }}
              content={({ active, payload }) => active && payload?.[0] && (
                <div className="rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-lift">
                  <div className="mb-1 font-medium">{payload[0].payload.symbol}</div>
                  <div className="tabular space-y-0.5">
                    <div>against you: {num(payload[0].payload.mae, 2)}</div>
                    <div>offered: {num(payload[0].payload.mfe, 2)}</div>
                    <div>captured: {num(payload[0].payload.capture, 2)}</div>
                    <div className={payload[0].payload.net >= 0 ? 'text-up' : 'text-down'}>
                      {money(payload[0].payload.net)} · {payload[0].payload.exit_reason}
                    </div>
                  </div>
                </div>
              )} />
            <Scatter data={points} animationDuration={500}>
              {points.map((p, i) => (
                <Cell key={i} fill={p.net >= 0 ? 'rgb(var(--up))' : 'rgb(var(--down))'}
                  fillOpacity={0.5} />
              ))}
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-2 grid gap-3 sm:grid-cols-3">
        {[
          ['Avg adverse', num(summary.overall?.avg_mae, 2), 'text-down'],
          ['Avg offered', num(summary.overall?.avg_mfe, 2), 'text-up'],
          ['Avg capture', num(summary.overall?.avg_capture_ratio, 2), ''],
        ].map(([label, value, tone], i) => (
          <Reveal key={label} delay={i * 0.04}>
            <div className="rounded-xl border border-line bg-elevated p-3">
              <div className="text-[11px] uppercase tracking-wider text-muted">{label}</div>
              <div className={cn('tabular mt-1 text-lg font-semibold', tone)}>{value}</div>
            </div>
          </Reveal>
        ))}
      </div>
    </Card>
  );
}
