"""
Advanced trade analytics: risk-adjusted returns, grids, and coaching rules.

Sits beside analytics.py rather than inside it — that module answers "what
happened", this one answers "is it significant, and what does it mean". The
split matters because everything here is an *inference*, and inferences need
guard rails that descriptive statistics do not.

The guard rail is sample size. A win rate over four trades is not a finding,
but rendered as a coloured cell it is indistinguishable from one. Every grid
cell and every coaching claim here carries the count it was computed from, and
anything below MIN_CLAIM refuses to make a claim at all.

Pure functions over paired trades (analytics.pair_trades output) — no
MetaTrader5, no filesystem, no network.
"""
import math
import random
from datetime import datetime, timezone

from analytics import session_of, weekday_of

# Below this, a bucket is shown but explicitly marked unreliable.
MIN_CELL = 10
# Below this, no coaching claim is made at all, however tempting the number.
MIN_CLAIM = 20

TRADING_DAYS = 252


def _closed(trades):
    return [t for t in (trades or []) if not t.get('open')]


def _day_key(ts):
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime('%Y-%m-%d')


def daily_pnl(trades):
    """
    Net P&L per UTC calendar day, oldest first.

    The unit the calendar heatmap and every risk-adjusted ratio are built on.
    Days with no trades are absent rather than zero — a day you did not trade
    is not a flat day, and padding them would drag volatility down.
    """
    days = {}
    for trade in _closed(trades):
        ts = trade.get('closed_utc')
        if ts is None:
            continue
        key = _day_key(ts)
        bucket = days.setdefault(key, {'date': key, 'net': 0.0, 'trades': 0,
                                       'wins': 0, 'losses': 0})
        bucket['net'] += trade['net']
        bucket['trades'] += 1
        if trade['net'] > 0:
            bucket['wins'] += 1
        elif trade['net'] < 0:
            bucket['losses'] += 1

    out = []
    for bucket in sorted(days.values(), key=lambda d: d['date']):
        bucket['net'] = round(bucket['net'], 2)
        bucket['win_rate_pct'] = (round(bucket['wins'] / bucket['trades'] * 100, 1)
                                  if bucket['trades'] else None)
        out.append(bucket)
    return out


def _mean(values):
    return sum(values) / len(values) if values else 0.0


def _stdev(values):
    """Sample standard deviation; None below two points, where it is undefined."""
    if len(values) < 2:
        return None
    avg = _mean(values)
    return math.sqrt(sum((v - avg) ** 2 for v in values) / (len(values) - 1))


def risk_adjusted(trades):
    """
    Sharpe, Sortino and recovery factor over the daily P&L series.

    Computed on P&L rather than percentage returns, which needs no account
    balance: both the mean and the deviation scale linearly with position size,
    so the ratio is unchanged. Annualised at 252 trading days.

    Sortino divides by downside deviation only — it does not punish a strategy
    for its good days, which is the whole objection to Sharpe on a skewed P&L
    distribution like a stop-heavy one.
    """
    days = daily_pnl(trades)
    series = [d['net'] for d in days]
    if len(series) < 2:
        return {'sharpe': None, 'sortino': None, 'trading_days': len(series),
                'avg_daily': round(_mean(series), 2) if series else None,
                'daily_stdev': None,
                'note': 'needs at least two trading days'}

    avg = _mean(series)
    sd = _stdev(series)
    downside = [v for v in series if v < 0]
    # Downside deviation is measured against zero, not the mean: the target is
    # "not losing money", not "doing better than average".
    dd = math.sqrt(sum(v ** 2 for v in downside) / len(series)) if downside else 0.0
    annual = math.sqrt(TRADING_DAYS)

    return {
        'sharpe': round(avg / sd * annual, 2) if sd else None,
        'sortino': round(avg / dd * annual, 2) if dd else None,
        'trading_days': len(series),
        'avg_daily': round(avg, 2),
        'daily_stdev': round(sd, 2) if sd is not None else None,
        'best_day': round(max(series), 2),
        'worst_day': round(min(series), 2),
        'note': None,
    }


def recovery_factor(net, max_dd):
    """Net profit per unit of worst drawdown. None when nothing was lost."""
    if not max_dd:
        return None
    return round(net / abs(max_dd), 2)


def kelly_fraction(win_rate_pct, payoff_ratio):
    """
    Kelly stake as a fraction of capital.

    Reported raw and unclamped, including negative values — a negative Kelly is
    the honest output for a losing edge and means "do not take this trade",
    which is more useful than a floor at zero that hides it.
    """
    if win_rate_pct is None or not payoff_ratio:
        return None
    w = win_rate_pct / 100
    return round(w - (1 - w) / payoff_ratio, 4)


def monte_carlo(trades, runs=1000, seed=7):
    """
    Bootstrap the trade sequence to see how much of the result was order.

    Resamples the same trades with replacement, so every path has the identical
    edge and differs only in sequencing. The spread answers "how much of my
    equity curve was luck", and the drawdown percentiles answer the question
    that actually matters before automating: how bad can this get.
    """
    nets = [t['net'] for t in _closed(trades)]
    if len(nets) < MIN_CELL:
        return {'runs': 0, 'trades': len(nets),
                'note': f'needs at least {MIN_CELL} closed trades'}

    rng = random.Random(seed)
    finals, drawdowns = [], []
    for _ in range(runs):
        equity = peak = worst = 0.0
        for _ in range(len(nets)):
            equity += nets[rng.randrange(len(nets))]
            peak = max(peak, equity)
            worst = max(worst, peak - equity)
        finals.append(equity)
        drawdowns.append(worst)

    def pct(values, p):
        ordered = sorted(values)
        return round(ordered[min(len(ordered) - 1, int(p / 100 * len(ordered)))], 2)

    return {
        'runs': runs,
        'trades': len(nets),
        'final_p5': pct(finals, 5),
        'final_p50': pct(finals, 50),
        'final_p95': pct(finals, 95),
        'drawdown_p50': pct(drawdowns, 50),
        'drawdown_p95': pct(drawdowns, 95),
        # Share of shuffled paths that still end negative. Near 100% means the
        # loss was the edge, not the ordering.
        'probability_of_loss_pct': round(
            len([f for f in finals if f < 0]) / runs * 100, 1),
        'note': None,
    }


HEATMAP_KEYS = ('weekday', 'session', 'hour', 'symbol', 'direction', 'month')


def _axis_value(trade, key):
    when = trade.get('opened_utc') or trade.get('closed_utc')
    if key == 'weekday':
        return weekday_of(when)
    if key == 'session':
        return session_of(when)
    if key == 'hour':
        return (datetime.fromtimestamp(int(when), tz=timezone.utc).hour
                if when is not None else None)
    if key == 'month':
        return (datetime.fromtimestamp(int(when), tz=timezone.utc).strftime('%Y-%m')
                if when is not None else None)
    return trade.get(key)


def heatmap(trades, rows='weekday', cols='session'):
    """
    Two-dimensional performance grid.

    Every cell reports its trade count and a `reliable` flag, because the whole
    failure mode of a heatmap is that a deep green cell built on two trades
    looks exactly like one built on two hundred.
    """
    for key in (rows, cols):
        if key not in HEATMAP_KEYS:
            raise ValueError(
                f'Unknown heatmap key {key!r}. Supported: {", ".join(HEATMAP_KEYS)}')

    cells = {}
    for trade in _closed(trades):
        r = _axis_value(trade, rows)
        c = _axis_value(trade, cols)
        r = 'unknown' if r is None else str(r)
        c = 'unknown' if c is None else str(c)
        cell = cells.setdefault((r, c), {'net': 0.0, 'trades': 0, 'wins': 0})
        cell['net'] += trade['net']
        cell['trades'] += 1
        if trade['net'] > 0:
            cell['wins'] += 1

    def order(key, labels):
        if key == 'weekday':
            from analytics import WEEKDAYS
            return [d for d in WEEKDAYS if d in labels] + sorted(labels - set(WEEKDAYS))
        if key in ('hour', 'month'):
            return sorted(labels, key=lambda v: (len(v), v) if key == 'hour' else v)
        return sorted(labels)

    row_labels = order(rows, {r for r, _ in cells})
    col_labels = order(cols, {c for _, c in cells})

    grid = []
    for r in row_labels:
        line = []
        for c in col_labels:
            cell = cells.get((r, c))
            if not cell:
                line.append(None)
                continue
            line.append({
                'net': round(cell['net'], 2),
                'trades': cell['trades'],
                'win_rate_pct': round(cell['wins'] / cell['trades'] * 100, 1),
                'avg': round(cell['net'] / cell['trades'], 2),
                'reliable': cell['trades'] >= MIN_CELL,
            })
        grid.append(line)

    return {'rows': rows, 'cols': cols, 'row_labels': row_labels,
            'col_labels': col_labels, 'grid': grid, 'min_reliable': MIN_CELL}


def holding_time_analysis(trades, buckets=8):
    """
    Does holding longer help or hurt?

    Buckets are geometric rather than linear: trade durations span seconds to
    hours, and equal-width bins would put almost everything in the first one.
    """
    rows = [t for t in _closed(trades) if t.get('duration_sec')]
    if not rows:
        return {'buckets': [], 'scatter': []}

    low = max(1, min(t['duration_sec'] for t in rows))
    high = max(t['duration_sec'] for t in rows)
    if high <= low:
        edges = [low, low + 1]
    else:
        step = (math.log(high) - math.log(low)) / buckets
        edges = [math.exp(math.log(low) + step * i) for i in range(buckets + 1)]
        # exp(log(x)) does not round-trip exactly: the outer edges come back as
        # 30.000000000000004 and 7199.999999999999, which puts the fastest and
        # slowest trades outside their own buckets. Anchor them to real values.
        edges[0], edges[-1] = low, high

    out = []
    for i in range(len(edges) - 1):
        start, end = edges[i], edges[i + 1]
        last = i == len(edges) - 2
        # Half-open everywhere except the final bucket, which must own its
        # upper edge or the longest trade belongs to no bucket at all.
        inside = [t for t in rows
                  if ((start <= t['duration_sec'] <= end) if last
                      else (start <= t['duration_sec'] < end))]
        wins = [t for t in inside if t['net'] > 0]
        out.append({
            'from_sec': round(start),
            'to_sec': round(end),
            'trades': len(inside),
            'net': round(sum(t['net'] for t in inside), 2),
            'win_rate_pct': round(len(wins) / len(inside) * 100, 1) if inside else None,
            'reliable': len(inside) >= MIN_CELL,
        })

    scatter = [{'duration_sec': t['duration_sec'], 'net': t['net'],
                'volume': t.get('volume'), 'direction': t.get('direction'),
                'symbol': t.get('symbol')} for t in rows]
    return {'buckets': out, 'scatter': scatter, 'min_reliable': MIN_CELL}


def size_analysis(trades):
    """Performance grouped by position size — do bigger positions do worse?"""
    rows = [t for t in _closed(trades) if t.get('volume')]
    sizes = {}
    for trade in rows:
        key = round(trade['volume'], 2)
        bucket = sizes.setdefault(key, {'volume': key, 'trades': 0, 'net': 0.0, 'wins': 0})
        bucket['trades'] += 1
        bucket['net'] += trade['net']
        if trade['net'] > 0:
            bucket['wins'] += 1
    out = []
    for bucket in sorted(sizes.values(), key=lambda b: b['volume']):
        bucket['net'] = round(bucket['net'], 2)
        bucket['avg'] = round(bucket['net'] / bucket['trades'], 2)
        bucket['win_rate_pct'] = round(bucket['wins'] / bucket['trades'] * 100, 1)
        bucket['reliable'] = bucket['trades'] >= MIN_CELL
        out.append(bucket)
    return out


def _gap_after_losses(trades):
    """
    Median gap before the next entry, split by whether the last trade lost.

    The measurable half of 'revenge trading': re-entering markedly faster after
    a loss is a behaviour that shows up in timestamps. Whether it *felt* like
    revenge is not in this data and is not claimed.
    """
    rows = sorted([t for t in _closed(trades)
                   if t.get('closed_utc') and t.get('opened_utc')],
                  key=lambda t: t['closed_utc'])
    after_loss, after_win = [], []
    for prev, nxt in zip(rows, rows[1:]):
        gap = nxt['opened_utc'] - prev['closed_utc']
        if gap < 0:
            continue      # overlapping positions — no "next entry" to measure
        (after_loss if prev['net'] < 0 else after_win).append(gap)

    def median(values):
        if not values:
            return None
        ordered = sorted(values)
        mid = len(ordered) // 2
        return (ordered[mid] if len(ordered) % 2
                else round((ordered[mid - 1] + ordered[mid]) / 2))

    return {'after_loss_sec': median(after_loss), 'after_win_sec': median(after_win),
            'samples_after_loss': len(after_loss), 'samples_after_win': len(after_win)}


def behaviour(trades):
    """
    The behavioural patterns that *are* measurable from fills.

    Deliberately short. Revenge timing, holding asymmetry and size escalation
    leave traces in timestamps and volumes. Fear, greed and confidence do not,
    and are absent here rather than estimated — a number invented for a radar
    chart is still an invented number.
    """
    rows = sorted(_closed(trades), key=lambda t: t.get('closed_utc') or 0)
    timed = [t for t in rows if t.get('duration_sec') is not None]
    wins = [t for t in timed if t['net'] > 0]
    losses = [t for t in timed if t['net'] < 0]

    escalation = None
    pairs = [(prev, nxt) for prev, nxt in zip(rows, rows[1:])
             if prev.get('volume') and nxt.get('volume')]
    if pairs:
        after_loss = [nxt['volume'] / prev['volume']
                      for prev, nxt in pairs if prev['net'] < 0]
        after_win = [nxt['volume'] / prev['volume']
                     for prev, nxt in pairs if prev['net'] > 0]
        if after_loss and after_win:
            escalation = {
                'size_ratio_after_loss': round(_mean(after_loss), 3),
                'size_ratio_after_win': round(_mean(after_win), 3),
                'samples': len(after_loss) + len(after_win),
            }

    return {
        'reentry_timing': _gap_after_losses(trades),
        'hold_asymmetry': {
            'avg_win_sec': round(_mean([t['duration_sec'] for t in wins])) if wins else None,
            'avg_loss_sec': round(_mean([t['duration_sec'] for t in losses])) if losses else None,
            'samples': len(timed),
        },
        'size_escalation': escalation,
        'not_measurable': ['fear', 'greed', 'confidence', 'discipline', 'patience'],
    }
