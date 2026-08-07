"""
Replay for strategy 1 — the multi-timeframe liquidity sweep.

`sweep_strategy.find_setups` says where the trades are; this says what they
would have done. Trades come out in exactly the shape `pair_trades()` produces,
like every other backtest here, so the results flow through the existing
analytics and dashboards rather than needing their own reporting.

The intrabar walk is `simulate._walk`, deliberately reused rather than
reimplemented. That function holds the stop-wins-on-an-ambiguous-bar rule and
the minimum-lot partial guard, both of which were bugs once. A second copy is
a second place for them to come back.

Three things this adds over the single-timeframe engine:

  * **Sizing has two modes.** Risk-based (lot from stop distance) or fixed.
    Fixed is what gets traded by hand — 0.03 on a $1000 account — but a fixed
    lot means risk floats with stop width, so even in fixed mode the cap still
    applies and an unusually wide sweep is refused rather than over-risked.
  * **Confluence raises the lot.** Two sweeps in the same direction landing
    together is one trade at a larger size, not two trades.
  * **The trailed stop clears the spread.** Breakeven at exactly entry exits
    having paid the round trip; entry + spread + buffer does not.

Spread is a parameter here because bar data does not carry it. Live, it is read
from the tick. A backtest run with `spread=0` is describing a broker that does
not exist, so the value used is reported alongside the result.
"""
import sweep_strategy as ss
from simulate import END, STOP, _walk
from strategy import spec_for

# Typical XM gold spread. An assumption, and reported as one.
DEFAULT_SPREAD = 0.30


def _iso(ts):
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _round_lot(lot, spec):
    """Down to the broker's step, never up."""
    step = spec['lot_step']
    return round(int(lot / step + 1e-9) * step, 8)


def confluence_at(sweeps, setup, window_sec):
    """
    How many distinct timeframes swept in this direction around this entry.

    A 1H level and a 15M level taken together is one idea with more evidence
    behind it, not two ideas — so it becomes one trade at a larger size. Two
    positions would double the risk on a single thesis.
    """
    entry_at = setup['entry_time_utc']
    timeframes = set()
    for sweep in sweeps:
        if sweep['direction'] != setup['direction']:
            continue
        if 0 <= entry_at - sweep['knowable_utc'] <= window_sec:
            timeframes.add(sweep['timeframe'])
    return timeframes


def size_for(balance, stop_distance, symbol, cfg, confluence=1):
    """
    Lot for a trade, or a refusal with its arithmetic.

    In risk mode the lot comes from the stop distance. In fixed mode it is the
    configured lot — but the risk cap still applies, because a fixed lot on an
    unusually wide sweep is exactly how a 1% plan becomes a 3% trade.
    """
    spec = spec_for(symbol)
    size = cfg['size']
    if stop_distance is None or stop_distance <= 0:
        return {'ok': False, 'reason': 'stop distance must be positive'}
    if not balance or balance <= 0:
        return {'ok': False, 'reason': 'balance must be positive'}

    per_lot = stop_distance * spec['contract_size']
    cap = balance * size['max_risk_pct'] / 100

    if size['mode'] == 'fixed':
        lot = size['fixed_lot']
    else:
        lot = _round_lot(balance * size['risk_pct'] / 100 / per_lot, spec)

    # Confluence raises the size, bounded by max_lot. One extra step per extra
    # timeframe, so 15M+1H is one step up and not a doubling.
    if confluence > 1:
        lot = round(lot + spec['lot_step'] * (confluence - 1), 8)
    lot = min(lot, size['max_lot'])

    if lot < spec['min_lot']:
        lot = spec['min_lot']
    risk = lot * per_lot
    if risk > cap:
        return {'ok': False, 'lot': 0.0, 'risk': round(risk, 2),
                'reason': (f'{lot} lot on a {stop_distance:.2f} stop risks '
                           f'{risk:.2f}, which is {risk / balance * 100:.1f}% of a '
                           f'{balance:.2f} balance — above the '
                           f'{size["max_risk_pct"]}% cap')}

    return {'ok': True, 'lot': lot, 'risk': round(risk, 2),
            'risk_pct': round(risk / balance * 100, 3)}


def backtest(bars_by_tf, config=None, balance=1000.0, spread=DEFAULT_SPREAD):
    """
    Replay strategy 1 and report what it would have done.

    One position at a time. A setup arriving while a trade is open is recorded
    as skipped with its reason rather than stacked — that matches how this is
    traded and keeps risk per trade meaningful.
    """
    cfg = ss.merged(config)
    problems = ss.validate(config)
    if problems:
        return {'success': False, 'problems': problems}

    entry_tf = str(cfg['entry_timeframe'])
    entry_bars = bars_by_tf.get(entry_tf) or []
    found = ss.find_setups(bars_by_tf, cfg)
    if not entry_bars or not found['setups']:
        # The rejections are the whole story when nothing survived detection.
        # Returning an empty `skipped` here made "found nothing" and "the
        # target rule is too strict" indistinguishable, which is the one
        # confusion this function exists to prevent.
        rejected = found.get('rejected') or []
        return {'success': True, 'trades': [], 'skipped': rejected,
                'summary': _summary([], rejected, balance,
                                    balance, cfg, spread),
                'detection': {'sweeps_found': found.get('sweeps_found', 0),
                              'reject_reasons': found.get('reject_reasons') or {}}}

    sweeps = ss.find_sweeps(bars_by_tf, cfg)
    window = ss.period(entry_tf) * cfg['entry']['wait_bars']
    symbol = cfg['symbol']

    # Management is expressed in the vocabulary simulate._walk understands.
    # The trail offset is what makes the breakeven stop clear the round trip.
    walk_cfg = {'manage': {
        'partial_pct': cfg['manage']['partial_pct'],
        'partial_at_r': cfg['manage']['partial_at_r'],
        'trail_to_be_at_r': cfg['manage']['trail_at_r'],
        'trail_offset_price': spread + cfg['manage']['trail_buffer_price'],
    }}

    trades, skipped = [], list(found.get('rejected') or [])
    balance_now = balance
    busy_until = -1

    for setup in found['setups']:
        if setup['entry_index'] <= busy_until:
            skipped.append({**_ref(setup), 'reason': 'a position was already open'})
            continue

        timeframes = confluence_at(sweeps, setup, window)
        size = size_for(balance, setup['r_distance'], symbol, cfg,
                        confluence=len(timeframes) or 1)
        if not size['ok']:
            skipped.append({**_ref(setup), 'reason': size['reason']})
            continue

        plan = {'entry': setup['entry_price'], 'stop': setup['stop'],
                'target': setup['target'], 'r_distance': setup['r_distance'],
                'lot': size['lot'], 'risk': size['risk']}
        result = _walk(entry_bars, setup['entry_index'], plan,
                       {'direction': setup['direction']}, symbol, walk_cfg)
        busy_until = result['exit_index']
        balance_now = round(balance_now + result['net'], 2)

        total_lot = sum(lot for _, lot in result['fills'])
        exit_price = (sum(p * lot for p, lot in result['fills']) / total_lot
                      if total_lot else None)
        opened = entry_bars[setup['entry_index']]['time_utc']
        closed = entry_bars[result['exit_index']]['time_utc']

        trades.append({
            'position_id': f'sweep-{len(trades) + 1}',
            'symbol': symbol,
            'direction': setup['direction'],
            'opened_utc': opened, 'opened': _iso(opened),
            'closed_utc': closed, 'closed': _iso(closed),
            'duration_sec': closed - opened,
            'entry_price': setup['entry_price'],
            'exit_price': round(exit_price, 5) if exit_price is not None else None,
            'volume': round(size['lot'], 2),
            'net': round(result['net'], 2),
            'exit_reason': result['reason'],
            'deals': 1 + len(result['fills']),
            'partial_closes': max(0, len(result['fills']) - 1),
            'open': False, 'entry_missing': False,
            'sim': {
                'strategy': 'liquidity-sweep',
                'sweep_timeframe': setup['sweep_timeframe'],
                'entry_timeframe': entry_tf,
                'trigger': setup['trigger'],
                'swept_level': setup['swept_level'],
                'confluence': sorted(timeframes),
                'stop': setup['stop'], 'target': setup['target'],
                'target_kind': setup['target_kind'],
                'r_distance': setup['r_distance'],
                'planned_risk': size['risk'],
                'r_multiple': (round(result['net'] / size['risk'], 2)
                               if size['risk'] else None),
                'stop_kind': result['stop_kind'],
                'partial_taken': result['partial_taken'],
                'partial_skipped': result.get('partial_skipped'),
                'level_use': setup['level_use'],
                'balance_after': balance_now,
            },
        })

    return {
        'success': True,
        'trades': trades,
        'skipped': skipped,
        'summary': _summary(trades, skipped, balance, balance_now, cfg, spread),
        'detection': {'sweeps_found': found.get('sweeps_found', 0),
                      'setups': len(found['setups']),
                      'reject_reasons': found.get('reject_reasons') or {}},
    }


def _ref(row):
    return {'direction': row.get('direction'),
            'sweep_timeframe': row.get('sweep_timeframe') or row.get('timeframe'),
            'time_utc': row.get('entry_time_utc') or row.get('sweep_time_utc')}


def _summary(trades, skipped, starting_balance, ending_balance, cfg, spread):
    """
    Headline numbers, with the ones needing a sample size withheld.

    The rejection counts sit here rather than in a footnote because they are
    how you tell "the strategy found nothing" from "the target rule is too
    strict" — two very different findings that produce the same trade count.
    """
    closed = [t for t in trades if t['exit_reason'] != END]
    reasons = _count(s['reason'] for s in skipped)

    if not trades:
        return {'trades': 0, 'closed': 0, 'skipped': len(skipped),
                'skipped_reasons': reasons, 'spread_assumed': spread,
                'note': 'no setup produced a trade'}

    wins = [t for t in closed if t['net'] > 0]
    losses = [t for t in closed if t['net'] < 0]
    rs = [t['sim']['r_multiple'] for t in closed if t['sim']['r_multiple'] is not None]
    gross_win = sum(t['net'] for t in wins)
    gross_loss = abs(sum(t['net'] for t in losses))
    reliable = len(closed) >= 10

    return {
        'trades': len(trades),
        'closed': len(closed),
        'unresolved': len(trades) - len(closed),
        'skipped': len(skipped),
        'skipped_reasons': reasons,
        'wins': len(wins), 'losses': len(losses),
        'win_rate_pct': round(len(wins) / len(closed) * 100, 1) if closed else None,
        'net': round(sum(t['net'] for t in trades), 2),
        'expectancy': round(sum(t['net'] for t in closed) / len(closed), 2)
                      if closed else None,
        'avg_r': round(sum(rs) / len(rs), 2) if rs else None,
        'r_stdev': round(_stdev(rs), 3) if len(rs) > 1 else None,
        'profit_factor': round(gross_win / gross_loss, 2) if gross_loss else None,
        'starting_balance': starting_balance,
        'ending_balance': ending_balance,
        'exit_reasons': _count(t['exit_reason'] for t in trades),
        'stop_kinds': _count(t['sim']['stop_kind'] for t in trades
                             if t['exit_reason'] == STOP),
        'by_sweep_timeframe': _count(t['sim']['sweep_timeframe'] for t in trades),
        'by_trigger': _count(t['sim']['trigger'] for t in trades),
        # An assumption, not a measurement. A run at zero describes a broker
        # that does not exist.
        'spread_assumed': spread,
        'reliable': reliable,
        'note': (None if reliable else
                 f'{len(closed)} closed trades — below the 10 these figures need '
                 f'to mean anything. Read them as a smoke test, not a result.'),
        'config': {'sweep_timeframes': cfg['sweep_timeframes'],
                   'entry_timeframe': cfg['entry_timeframe'],
                   'trigger': cfg['entry']['trigger'],
                   'fallback': cfg['entry']['fallback'],
                   'target_mode': cfg['target']['mode'],
                   'min_r': cfg['target']['min_r'],
                   'size_mode': cfg['size']['mode']},
    }


def _stdev(values):
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return (sum((v - mean) ** 2 for v in values) / (n - 1)) ** 0.5


def _count(values):
    out = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
