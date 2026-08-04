"""
Trade-history analytics.

Pure functions over decoded deals — no MetaTrader5, no filesystem, no network —
so the dashboard, the MCP tools and the eventual backtester all share one
tested implementation rather than each growing their own arithmetic.

Input is the deal shape mt5_client.deals() emits: enums decoded to strings
(`type`, `entry`, `reason`), and timestamps expanded so `time_utc` is real UTC
or None. Everything here joins on `time_utc`, never the broker clock.
"""
from datetime import datetime, timedelta, timezone

from normalize import CLOSING_ENTRIES, iso

# Trading sessions by UTC hour. Boundaries are conventional rather than exact —
# they exist to answer "when do I lose money", which does not need precision.
SESSIONS = (
    ('asia', 0, 7),
    ('london', 7, 12),
    ('overlap', 12, 16),   # London/New York — usually the most active
    ('new_york', 16, 21),
    ('late', 21, 24),
)

WEEKDAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday',
            'saturday', 'sunday')


def closed_trades(deals):
    """
    Deals that closed exposure and carry realised P&L.

    Entries hold none, and balance/credit rows are not trades at all — counting
    either would distort every statistic downstream.
    """
    return [d for d in (deals or [])
            if str(d.get('entry')) in CLOSING_ENTRIES
            and str(d.get('type')) in ('buy', 'sell')]


def session_of(ts_utc):
    """Trading session for a UTC timestamp, or None if the time is unknown."""
    if ts_utc is None:
        return None
    hour = datetime.fromtimestamp(int(ts_utc), tz=timezone.utc).hour
    for name, start, end in SESSIONS:
        if start <= hour < end:
            return name
    return None


def weekday_of(ts_utc):
    if ts_utc is None:
        return None
    return WEEKDAYS[datetime.fromtimestamp(int(ts_utc), tz=timezone.utc).weekday()]


def _net(deal):
    """Realised P&L including costs, which is what actually hit the account."""
    return ((deal.get('profit') or 0)
            + (deal.get('commission') or 0)
            + (deal.get('swap') or 0)
            + (deal.get('fee') or 0))


def equity_curve(deals, starting_balance=None):
    """
    Cumulative realised P&L over time, oldest first.

    Deals with no resolved UTC time are dropped rather than guessed at — an
    equity curve ordered by a broker clock while everything else uses UTC is
    worse than a shorter curve.
    """
    trades = [d for d in closed_trades(deals) if d.get('time_utc') is not None]
    trades.sort(key=lambda d: d['time_utc'])

    points, cumulative = [], 0.0
    for deal in trades:
        cumulative += _net(deal)
        point = {
            'time_utc': deal['time_utc'],
            'time': iso(deal['time_utc']),
            'profit': round(_net(deal), 2),
            'cumulative': round(cumulative, 2),
            'symbol': deal.get('symbol'),
        }
        if starting_balance is not None:
            point['balance'] = round(starting_balance + cumulative, 2)
        points.append(point)
    return points


def max_drawdown(curve):
    """
    Largest peak-to-trough decline along an equity curve.

    Percentage is reported only when the curve carries balances — a drawdown of
    50 means nothing without knowing 50 out of what, and inventing a base is
    how a small account's risk gets understated.
    """
    if not curve:
        return {'max_drawdown': 0.0, 'max_drawdown_pct': None,
                'peak_at': None, 'trough_at': None, 'recovered_at': None}

    series = [(p['time_utc'], p.get('balance', p['cumulative'])) for p in curve]
    has_balance = 'balance' in curve[0]

    peak_value, peak_at = series[0][1], series[0][0]
    worst = {'drop': 0.0, 'pct': None, 'peak_at': None, 'trough_at': None,
             'peak_value': None}

    for ts, value in series:
        if value > peak_value:
            peak_value, peak_at = value, ts
        drop = peak_value - value
        if drop > worst['drop']:
            worst = {'drop': drop, 'peak_at': peak_at, 'trough_at': ts,
                     'peak_value': peak_value,
                     'pct': (drop / peak_value * 100) if (has_balance and peak_value > 0) else None}

    recovered_at = None
    if worst['trough_at'] is not None:
        for ts, value in series:
            if ts > worst['trough_at'] and value >= worst['peak_value']:
                recovered_at = ts
                break

    return {
        'max_drawdown': round(worst['drop'], 2),
        'max_drawdown_pct': round(worst['pct'], 2) if worst['pct'] is not None else None,
        'peak_at': iso(worst['peak_at']) if worst['peak_at'] else None,
        'trough_at': iso(worst['trough_at']) if worst['trough_at'] else None,
        'recovered_at': iso(recovered_at) if recovered_at else None,
        'still_in_drawdown': worst['trough_at'] is not None and recovered_at is None,
    }


def _bucket_stats(trades):
    nets = [_net(d) for d in trades]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    return {
        'trades': len(trades),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate_pct': round(len(wins) / len(trades) * 100, 1) if trades else None,
        'net': round(sum(nets), 2),
        'avg': round(sum(nets) / len(trades), 2) if trades else None,
        'avg_win': round(sum(wins) / len(wins), 2) if wins else None,
        'avg_loss': round(sum(losses) / len(losses), 2) if losses else None,
        'best': round(max(nets), 2) if nets else None,
        'worst': round(min(nets), 2) if nets else None,
        'volume': round(sum(d.get('volume') or 0 for d in trades), 2),
    }


GROUP_KEYS = ('symbol', 'reason', 'type', 'session', 'weekday', 'hour')


def group_performance(deals, key='reason'):
    """
    Break performance down by one dimension.

    'session' and 'weekday' answer when you lose money; 'reason' answers how
    trades end — a stop_loss-heavy distribution against almost no take_profit
    says something different from the reverse.
    """
    if key not in GROUP_KEYS:
        raise ValueError(f'Unknown group key {key!r}. Supported: {", ".join(GROUP_KEYS)}')

    buckets = {}
    for deal in closed_trades(deals):
        if key == 'session':
            label = session_of(deal.get('time_utc'))
        elif key == 'weekday':
            label = weekday_of(deal.get('time_utc'))
        elif key == 'hour':
            ts = deal.get('time_utc')
            label = (datetime.fromtimestamp(int(ts), tz=timezone.utc).hour
                     if ts is not None else None)
        else:
            label = deal.get(key)
        label = 'unknown' if label is None else label
        buckets.setdefault(label, []).append(deal)

    return {str(label): _bucket_stats(trades)
            for label, trades in sorted(buckets.items(), key=lambda kv: str(kv[0]))}


def streaks(deals):
    """Longest and current runs of wins and losses, in chronological order."""
    trades = [d for d in closed_trades(deals) if d.get('time_utc') is not None]
    trades.sort(key=lambda d: d['time_utc'])

    longest_win = longest_loss = run = 0
    current_kind = None
    for deal in trades:
        net = _net(deal)
        kind = 'win' if net > 0 else ('loss' if net < 0 else None)
        if kind is None:
            continue
        run = run + 1 if kind == current_kind else 1
        current_kind = kind
        if kind == 'win':
            longest_win = max(longest_win, run)
        else:
            longest_loss = max(longest_loss, run)

    return {
        'longest_win_streak': longest_win,
        'longest_loss_streak': longest_loss,
        'current_streak': run if current_kind else 0,
        'current_streak_kind': current_kind,
    }


def expectancy(deals):
    """
    Average money per trade, and the win/loss shape behind it.

    Negative expectancy is the number that matters before anything is
    automated: automation scales whatever it is given.
    """
    trades = closed_trades(deals)
    if not trades:
        return {'trades': 0, 'expectancy': None}

    nets = [_net(d) for d in trades]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    win_rate = len(wins) / len(trades)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0

    return {
        'trades': len(trades),
        'expectancy': round(sum(nets) / len(trades), 4),
        'win_rate_pct': round(win_rate * 100, 1),
        'avg_win': round(avg_win, 2) if wins else None,
        'avg_loss': round(avg_loss, 2) if losses else None,
        # How many times the average win covers the average loss. Below 1 with
        # a sub-50% win rate is a losing combination on both counts.
        'payoff_ratio': round(avg_win / abs(avg_loss), 2) if wins and losses else None,
    }


OPENING_ENTRIES = ('in',)
# 'inout' is a reversal — it realises P&L on the old exposure. Treated as a
# close here; on a hedging account (which retail brokers typically use) each
# position carries its own id, so this rarely appears.
CLOSING_DEAL_ENTRIES = ('out', 'out_by', 'inout')


def _weighted_price(deals):
    """Volume-weighted average fill price, or None if nothing filled."""
    volume = sum(d.get('volume') or 0 for d in deals)
    if not volume:
        return None
    return sum((d.get('price') or 0) * (d.get('volume') or 0) for d in deals) / volume


def pair_trades(deals, include_open=True):
    """
    Group fills into trades by position_id.

    MetaTrader 5 reports *deals*, not trades: opening a position and closing it
    are separate rows, and a partial close adds more. Treating each closing deal
    as a trade is fine for win rate and net P&L, but it cannot answer how long
    a position was held or where it was entered — which is the question behind
    "do I hold losers longer than winners".

    Windows that start mid-position see closes with no matching open. Those are
    reported with entry_missing set rather than a fabricated entry price, since
    the entry genuinely happened before the data we have.
    """
    groups = {}
    for deal in deals or []:
        # Balance, credit and commission rows are not part of any position.
        if str(deal.get('type')) not in ('buy', 'sell'):
            continue
        pid = deal.get('position_id')
        if not pid:
            continue
        groups.setdefault(pid, []).append(deal)

    trades = []
    for pid, rows in groups.items():
        rows.sort(key=lambda d: (d.get('time_msc_utc') or 0, d.get('time_utc') or 0))
        opens = [d for d in rows if str(d.get('entry')) in OPENING_ENTRIES]
        closes = [d for d in rows if str(d.get('entry')) in CLOSING_DEAL_ENTRIES]

        if not include_open and not closes:
            continue

        open_times = [d['time_utc'] for d in opens if d.get('time_utc') is not None]
        close_times = [d['time_utc'] for d in closes if d.get('time_utc') is not None]
        opened_at = min(open_times) if open_times else None
        closed_at = max(close_times) if close_times else None

        # Direction comes from the opening fill: a buy entry is a long. Falling
        # back to the inverse of the closing fill keeps windows that start
        # mid-position usable.
        if opens:
            direction = 'long' if str(opens[0].get('type')) == 'buy' else 'short'
        elif closes:
            direction = 'short' if str(closes[0].get('type')) == 'buy' else 'long'
        else:
            direction = None

        trades.append({
            'position_id': pid,
            'symbol': (rows[0].get('symbol') if rows else None),
            'direction': direction,
            'opened_utc': opened_at,
            'opened': iso(opened_at) if opened_at else None,
            'closed_utc': closed_at,
            'closed': iso(closed_at) if closed_at else None,
            'duration_sec': (closed_at - opened_at) if (opened_at and closed_at) else None,
            'entry_price': round(_weighted_price(opens), 5) if opens else None,
            'exit_price': round(_weighted_price(closes), 5) if closes else None,
            'volume': round(sum(d.get('volume') or 0 for d in opens), 2) if opens
                      else round(sum(d.get('volume') or 0 for d in closes), 2),
            'net': round(sum(_net(d) for d in rows), 2),
            'exit_reason': str(closes[-1].get('reason')) if closes else None,
            'deals': len(rows),
            'partial_closes': max(0, len(closes) - 1),
            'open': not closes,
            'entry_missing': not opens,
        })

    trades.sort(key=lambda t: t['opened_utc'] or t['closed_utc'] or 0)
    return trades


def summarize_trades(trades):
    """
    Trade-level summary, including how long winners are held versus losers.

    That comparison is the one deals alone cannot produce, and it separates
    "my exits are good" from "I cut winners early and let losers reach the
    stop" — two stories that look identical in a win-rate table.
    """
    closed = [t for t in (trades or []) if not t['open']]
    if not closed:
        return None

    wins = [t for t in closed if t['net'] > 0]
    losses = [t for t in closed if t['net'] < 0]
    timed = [t for t in closed if t['duration_sec'] is not None]
    timed_wins = [t for t in timed if t['net'] > 0]
    timed_losses = [t for t in timed if t['net'] < 0]

    def avg_seconds(rows):
        return round(sum(t['duration_sec'] for t in rows) / len(rows)) if rows else None

    return {
        'trades': len(closed),
        'open_trades': len([t for t in trades if t['open']]),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate_pct': round(len(wins) / len(closed) * 100, 1),
        'net': round(sum(t['net'] for t in closed), 2),
        'avg_duration_sec': avg_seconds(timed),
        'avg_win_duration_sec': avg_seconds(timed_wins),
        'avg_loss_duration_sec': avg_seconds(timed_losses),
        'partial_closes': sum(t['partial_closes'] for t in closed),
        'entry_missing': len([t for t in closed if t['entry_missing']]),
    }


TRADE_GROUP_KEYS = ('symbol', 'exit_reason', 'direction', 'session', 'weekday', 'hour')


def filter_trades(trades, symbol=None, direction=None, exit_reason=None,
                  min_net=None, max_net=None, include_open=True):
    """
    Narrow a trade list to the slice the history view is asking about.

    Applied over paired trades rather than deals so a filter means what a human
    means by it: "short trades that hit the stop" is one predicate per trade,
    not per fill.
    """
    out = []
    for trade in trades or []:
        if not include_open and trade.get('open'):
            continue
        if symbol and trade.get('symbol') != symbol:
            continue
        if direction and trade.get('direction') != direction:
            continue
        if exit_reason and trade.get('exit_reason') != exit_reason:
            continue
        # P&L bounds only apply to closed trades; an open one has no realised
        # result to compare against and is kept or dropped by include_open.
        if not trade.get('open'):
            net = trade.get('net')
            if min_net is not None and (net is None or net < min_net):
                continue
            if max_net is not None and (net is None or net > max_net):
                continue
        out.append(trade)
    return out


def _closed_in_order(trades):
    """Closed trades with a known close time, oldest first."""
    rows = [t for t in (trades or [])
            if not t.get('open') and t.get('closed_utc') is not None]
    rows.sort(key=lambda t: t['closed_utc'])
    return rows


def trade_equity_curve(trades, starting_balance=None):
    """
    Cumulative realised P&L per trade, oldest first.

    Ordered by close time, since that is when the money actually moved. One
    point per trade rather than per fill, so a partial close no longer shows up
    as two separate steps on the curve.
    """
    points, cumulative = [], 0.0
    for trade in _closed_in_order(trades):
        cumulative += trade['net']
        point = {
            'time_utc': trade['closed_utc'],
            'time': trade.get('closed'),
            'profit': round(trade['net'], 2),
            'cumulative': round(cumulative, 2),
            'symbol': trade.get('symbol'),
            'position_id': trade.get('position_id'),
        }
        if starting_balance is not None:
            point['balance'] = round(starting_balance + cumulative, 2)
        points.append(point)
    return points


def _trade_bucket_stats(trades):
    nets = [t['net'] for t in trades]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    held = [t['duration_sec'] for t in trades if t.get('duration_sec') is not None]
    return {
        'trades': len(trades),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate_pct': round(len(wins) / len(trades) * 100, 1) if trades else None,
        'net': round(sum(nets), 2),
        'avg': round(sum(nets) / len(trades), 2) if trades else None,
        'avg_win': round(sum(wins) / len(wins), 2) if wins else None,
        'avg_loss': round(sum(losses) / len(losses), 2) if losses else None,
        'best': round(max(nets), 2) if nets else None,
        'worst': round(min(nets), 2) if nets else None,
        'avg_duration_sec': round(sum(held) / len(held)) if held else None,
        'volume': round(sum(t.get('volume') or 0 for t in trades), 2),
    }


def trade_groups(trades, key='exit_reason'):
    """
    Break trade performance down by one dimension.

    Time-based keys use the *entry* time, not the exit: "which session do I lose
    money in" is a question about when a position was opened. Grouping by exit
    time would credit a London entry that ran into New York to the wrong bucket.
    """
    if key not in TRADE_GROUP_KEYS:
        raise ValueError(
            f'Unknown group key {key!r}. Supported: {", ".join(TRADE_GROUP_KEYS)}')

    buckets = {}
    for trade in trades or []:
        if trade.get('open'):
            continue
        when = trade.get('opened_utc') or trade.get('closed_utc')
        if key == 'session':
            label = session_of(when)
        elif key == 'weekday':
            label = weekday_of(when)
        elif key == 'hour':
            label = (datetime.fromtimestamp(int(when), tz=timezone.utc).hour
                     if when is not None else None)
        else:
            label = trade.get(key)
        label = 'unknown' if label is None else label
        buckets.setdefault(label, []).append(trade)

    return {str(label): _trade_bucket_stats(rows)
            for label, rows in sorted(buckets.items(), key=lambda kv: str(kv[0]))}


def trade_streaks(trades):
    """Longest and current runs of winning and losing trades, by close time."""
    longest_win = longest_loss = run = 0
    current_kind = None
    for trade in _closed_in_order(trades):
        net = trade['net']
        kind = 'win' if net > 0 else ('loss' if net < 0 else None)
        if kind is None:
            continue
        run = run + 1 if kind == current_kind else 1
        current_kind = kind
        if kind == 'win':
            longest_win = max(longest_win, run)
        else:
            longest_loss = max(longest_loss, run)

    return {
        'longest_win_streak': longest_win,
        'longest_loss_streak': longest_loss,
        'current_streak': run if current_kind else 0,
        'current_streak_kind': current_kind,
    }


def pnl_distribution(trades, buckets=12):
    """
    Histogram of per-trade results.

    Shows the shape a win rate hides: a handful of large losses against many
    small wins reads as 'mostly green' in a table and as a cliff here. Bucket
    edges are split at zero so no bar mixes winners with losers.
    """
    nets = [t['net'] for t in (trades or []) if not t.get('open')]
    if not nets:
        return []

    low, high = min(nets), max(nets)
    if low == high:
        return [{'from': round(low, 2), 'to': round(high, 2),
                 'count': len(nets), 'net': round(sum(nets), 2)}]

    # Split the range at zero so a bucket is never part win, part loss.
    negative = max(0, -min(low, 0.0))
    positive = max(0, max(high, 0.0))
    span = negative + positive
    loss_buckets = max(1, round(buckets * negative / span)) if negative else 0
    win_buckets = max(1, buckets - loss_buckets) if positive else 0

    edges = []
    if loss_buckets:
        step = negative / loss_buckets
        edges.extend(low + step * i for i in range(loss_buckets))
    edges.append(0.0 if (negative and positive) else low)
    if win_buckets:
        step = positive / win_buckets
        edges.extend(step * (i + 1) for i in range(win_buckets))
    edges = sorted(set(round(e, 6) for e in edges))
    # Anchor the outer edges to real values: accumulated float error otherwise
    # leaves the best and worst trades sitting just outside their own buckets.
    edges[0], edges[-1] = low, high

    out = []
    for i in range(len(edges) - 1):
        start, end = edges[i], edges[i + 1]
        last = i == len(edges) - 2
        # Half-open everywhere except the final bucket, which must own its
        # upper edge or the single best trade belongs to no bucket at all.
        rows = [n for n in nets
                if ((start <= n <= end) if last else (start <= n < end))]
        out.append({'from': round(start, 2), 'to': round(end, 2),
                    'count': len(rows), 'net': round(sum(rows), 2)})
    return out


def analyze_trades(trades, starting_balance=None,
                   group_by=('exit_reason', 'session', 'symbol')):
    """
    Everything the history view needs, computed once over paired trades.

    Deliberately parallel to analyze(), which works on raw deals: that one
    answers "how did my fills go", this one answers "how did my trades go", and
    holding times only exist on this side.
    """
    closed = [t for t in (trades or []) if not t.get('open')]
    curve = trade_equity_curve(trades, starting_balance=starting_balance)
    nets = [t['net'] for t in closed]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    gross_loss = abs(sum(losses))

    headline = summarize_trades(trades) or {}
    if closed:
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        headline.update({
            'expectancy': round(sum(nets) / len(closed), 4),
            'avg_win': round(avg_win, 2) if wins else None,
            'avg_loss': round(avg_loss, 2) if losses else None,
            'payoff_ratio': round(avg_win / abs(avg_loss), 2) if wins and losses else None,
            # Gross win over gross loss. None rather than infinity when nothing
            # lost — a number that cannot be plotted is worse than an absence.
            'profit_factor': round(sum(wins) / gross_loss, 2) if gross_loss else None,
            'best': round(max(nets), 2),
            'worst': round(min(nets), 2),
            'volume': round(sum(t.get('volume') or 0 for t in closed), 2),
        })

    return {
        'headline': headline or None,
        'streaks': trade_streaks(trades),
        'drawdown': max_drawdown(curve),
        'groups': {key: trade_groups(trades, key) for key in group_by},
        'distribution': pnl_distribution(trades),
        'equity_curve': curve,
    }


def period_bounds(now_ts):
    """
    UTC day/week/month boundaries for the P&L strip.

    Everything is anchored to UTC because that is the clock the rest of the
    system joins on. A broker-local "today" would disagree with the calendar
    and with every timestamp elsewhere.
    """
    now = datetime.fromtimestamp(int(now_ts), tz=timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=day_start.weekday())   # Monday
    month_start = day_start.replace(day=1)
    return {
        'today': (int(day_start.timestamp()), int(now_ts)),
        'week': (int(week_start.timestamp()), int(now_ts)),
        'month': (int(month_start.timestamp()), int(now_ts)),
    }


def realized_pnl(deals, from_ts, to_ts):
    """Net realised P&L for closed trades inside a UTC window."""
    total, count = 0.0, 0
    for deal in closed_trades(deals):
        ts = deal.get('time_utc')
        if ts is None or ts < from_ts or ts > to_ts:
            continue
        total += _net(deal)
        count += 1
    return {'net': round(total, 2), 'trades': count}


def analyze(deals, starting_balance=None, group_by=('reason', 'session', 'symbol')):
    """Everything the history view needs, in one pass."""
    curve = equity_curve(deals, starting_balance=starting_balance)
    return {
        'expectancy': expectancy(deals),
        'streaks': streaks(deals),
        'drawdown': max_drawdown(curve),
        'groups': {key: group_performance(deals, key) for key in group_by},
        'equity_curve': curve,
    }
