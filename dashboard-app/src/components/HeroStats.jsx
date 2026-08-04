import { AnimatedNumber, Card, Reveal, Skeleton, Tip } from '@/components/ui';
import { hold, money, num, pct, tone } from '@/lib/format';
import { cn } from '@/lib/api';

/**
 * A tile's sparkline. Deliberately not Recharts — one polyline needs no
 * charting library, and keeping it out of this bundle means the hero row
 * paints before the chart chunk has loaded.
 */
function Spark({ points, positive }) {
  if (!points || points.length < 2) return null;
  const w = 100;
  const h = 24;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const d = points
    .map((v, i) => `${i ? 'L' : 'M'}${(i / (points.length - 1)) * w},${h - ((v - min) / span) * h}`)
    .join('');
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="mt-2 h-6 w-full" preserveAspectRatio="none"
      aria-hidden="true">
      <path d={d} fill="none" strokeWidth="1.75" vectorEffect="non-scaling-stroke"
        className={positive ? 'stroke-up' : 'stroke-down'} />
    </svg>
  );
}

function Tile({ label, value, note, valueClass, spark, sparkPositive, help, delay }) {
  const body = (
    <Card className="group h-full transition-shadow hover:shadow-lift">
      <div className="text-[11px] font-medium uppercase tracking-wider text-muted">{label}</div>
      <div className={cn('tabular mt-1 text-2xl font-semibold tracking-tight', valueClass)}>
        {value}
      </div>
      {note && <div className="mt-0.5 text-xs text-muted">{note}</div>}
      <Spark points={spark} positive={sparkPositive} />
    </Card>
  );
  return (
    <Reveal delay={delay}>
      {help ? <Tip content={help}>{body}</Tip> : body}
    </Reveal>
  );
}

export function HeroSkeleton() {
  return (
    <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-5">
      {Array.from({ length: 10 }, (_, i) => (
        <Card key={i}><Skeleton className="h-3 w-20" /><Skeleton className="mt-3 h-7 w-24" />
          <Skeleton className="mt-3 h-6 w-full" /></Card>
      ))}
    </div>
  );
}

export default function HeroStats({ data }) {
  const h = data?.headline;
  if (!h) return null;

  const dd = data.drawdown || {};
  const risk = data.risk || {};
  const mc = data.monte_carlo || {};
  const streaks = data.streaks || {};
  const curve = (data.equity_curve || []).map(p => p.balance ?? p.cumulative);
  const daily = (data.daily || []).map(d => d.net);

  const tiles = [
    { label: 'Net P&L', value: <AnimatedNumber value={h.net} format={v => money(v)} />,
      valueClass: tone(h.net), note: `${h.trades} closed trades`,
      spark: curve, sparkPositive: h.net >= 0 },
    { label: 'Win rate', value: <AnimatedNumber value={h.win_rate_pct} format={v => pct(v)} />,
      note: `${h.wins}W / ${h.losses}L`, spark: daily, sparkPositive: h.net >= 0 },
    { label: 'Expectancy', value: <AnimatedNumber value={h.expectancy} format={v => money(v)} />,
      valueClass: tone(h.expectancy), note: 'per trade',
      help: 'Average money made or lost on every trade taken. Automation scales this number, whichever sign it has.' },
    { label: 'Profit factor', value: num(h.profit_factor),
      valueClass: h.profit_factor >= 1 ? 'text-up' : 'text-down', note: 'gross win / gross loss',
      help: 'Below 1.0 means gross losses exceed gross wins, whatever the win rate says.' },
    { label: 'Payoff', value: num(h.payoff_ratio), note: 'avg win / avg loss',
      help: h.payoff_ratio ? `Needs a win rate above ${(100 / (1 + h.payoff_ratio)).toFixed(0)}% just to break even.` : undefined },
    { label: 'Max drawdown', value: money(-Math.abs(dd.max_drawdown ?? 0)),
      valueClass: 'text-down',
      note: dd.max_drawdown_pct == null
        ? 'set a starting balance for %'
        : `${num(dd.max_drawdown_pct, 1)}%${dd.still_in_drawdown ? ' · not recovered' : ''}` },
    { label: 'Sharpe', value: num(risk.sharpe),
      valueClass: risk.sharpe >= 0 ? 'text-up' : 'text-down',
      note: `${risk.trading_days ?? 0} trading days`,
      help: 'Annualised from daily P&L. Computed on P&L rather than percentage returns, so it needs no account balance — the ratio is unchanged by position size.' },
    { label: 'Sortino', value: num(risk.sortino),
      valueClass: risk.sortino >= 0 ? 'text-up' : 'text-down', note: 'downside risk only',
      help: 'Like Sharpe but only penalises losing days, so a few outsized winners do not count against you.' },
    { label: 'Recovery factor', value: num(risk.recovery_factor),
      note: 'net / max drawdown' },
    { label: 'Avg hold', value: hold(h.avg_duration_sec),
      note: `win ${hold(h.avg_win_duration_sec)} · loss ${hold(h.avg_loss_duration_sec)}`,
      help: 'The comparison raw fills cannot produce. Winners and losers held for similar times rules out "cutting winners early" as an explanation.' },
    { label: 'Best / worst', value: `${money(h.best)} / ${money(h.worst)}` },
    { label: 'Longest streak',
      value: `${streaks.longest_win_streak ?? 0}W / ${streaks.longest_loss_streak ?? 0}L`,
      note: streaks.current_streak_kind
        ? `now ${streaks.current_streak} ${streaks.current_streak_kind}` : undefined },
    { label: 'Kelly', value: risk.kelly_fraction == null ? '—' : pct(risk.kelly_fraction * 100),
      valueClass: risk.kelly_fraction > 0 ? 'text-up' : 'text-down', note: 'of capital per trade',
      help: 'Reported unclamped. A negative Kelly is the honest output for a losing edge and means the correct stake is zero.' },
    { label: 'Drawdown p95', value: money(-Math.abs(mc.drawdown_p95 ?? 0), { sign: false }),
      valueClass: 'text-down', note: mc.runs ? `${mc.runs} simulations` : 'needs more trades',
      help: 'One in twenty reorderings of these same trades draws down at least this far. Size to survive it.' },
    { label: 'P(loss)', value: mc.probability_of_loss_pct == null ? '—' : pct(mc.probability_of_loss_pct),
      valueClass: (mc.probability_of_loss_pct ?? 0) > 50 ? 'text-down' : 'text-up',
      note: 'shuffled orderings',
      help: 'Share of reshuffled trade sequences that still end negative. Near 100% means the result was the edge, not bad luck.' },
  ];

  return (
    <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-5">
      {tiles.map((t, i) => <Tile key={t.label} {...t} delay={i * 0.025} />)}
    </div>
  );
}
