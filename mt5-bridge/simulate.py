"""
Bar-by-bar replay of a strategy config.

The output is deliberately the exact shape `analytics.pair_trades()` produces,
so a simulated run flows through `analyze_trades`, `excursions`, `insights` and
the dashboard without a second code path. A backtest that needs its own
reporting stack is a backtest whose numbers cannot be compared with the real
account's.

Three honesty rules, because each one flatters a strategy when broken:

  * **Entry is at the next bar's open.** The confirmation candle is only known
    once it has closed, and filling at that close assumes an order placed
    before the information existed.
  * **When one bar contains both the stop and the target, the stop wins.**
    Bar data cannot say which came first. Assuming the target is how a losing
    strategy backtests profitably.
  * **Sizing goes through `strategy.position_size`,** so a balance too small
    for the instrument produces refused setups rather than fractional lots the
    broker would never accept. Those refusals are counted and reported.

Every simulated trade carries a `sim` block with the things the real MT5
history cannot tell us — which zones triggered it, whether the stop that filled
was the initial one or a breakeven trail — and that second field is the one
that separates "my stop was too tight" from "I moved it too early".
"""
from detectors import confirmation, detect, present_at
from strategy import conditions_met, merged, position_size, spec_for

# Exit reasons use MT5's vocabulary (see normalize.DEAL_REASON) so simulated
# and real trades group together in the same breakdowns.
STOP, TARGET, END = 'stop_loss', 'take_profit', 'end_of_data'


def _iso(ts):
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _partial_lot(remaining, partial_pct, spec):
    """
    The lot to close for a partial, or None when a partial is impossible.

    Rounded **down** to the broker's step, never up: `round(0.01 * 0.5, 2)`
    returns 0.01, so an innocent-looking "take 50%" closes the entire position.
    Both the piece taken and the piece left must be at least one minimum lot —
    a broker rejects either side being smaller, and pretending otherwise makes
    the whole backtest describe trades that cannot be placed.
    """
    step, minimum = spec['lot_step'], spec['min_lot']
    wanted = remaining * partial_pct / 100
    lot = round(int(wanted / step + 1e-9) * step, 8)
    left = round(remaining - lot, 8)

    if lot < minimum:
        return None
    # Leaving nothing is a full close, which is fine. Leaving a sliver smaller
    # than one lot is an order the broker rejects.
    if 0 < left < minimum:
        return None
    return lot


def _pnl(direction, entry, exit_price, lot, contract_size):
    delta = (exit_price - entry) if direction == 'long' else (entry - exit_price)
    return delta * lot * contract_size


def find_setups(bars, config=None):
    """
    Every entry signal in the series, without simulating any of them.

    This is the checkpoint before any P&L number is worth quoting: each row has
    a timestamp, a direction, the levels involved and the conditions that fired,
    so it can be pulled up on a chart and judged as a setup you would or would
    not have taken. If the detectors find things you would not trade, no amount
    of parameter sweeping fixes that.
    """
    cfg = merged(config)
    zones_by_kind = detect(bars, cfg)
    conf_type = cfg['entry']['confirmation']['type']
    setups = []

    for i, bar in enumerate(bars):
        hits = present_at(zones_by_kind, bars, i, cfg)
        for direction, zones in hits.items():
            if not conditions_met(set(zones), cfg):
                continue
            # With several zones hit at once, confirm against their union. A
            # close that clears the widest of them clears all of them; picking
            # one arbitrarily would let a narrow zone wave through a bar that
            # never escaped the others.
            span = {'low': min(z['low'] for z in zones.values()),
                    'high': max(z['high'] for z in zones.values()),
                    'direction': direction}
            if not confirmation(bar, span, conf_type):
                continue
            setups.append({
                'index': i,
                'time_utc': bar['time_utc'],
                'time': _iso(bar['time_utc']),
                'direction': direction,
                'conditions': sorted(zones),
                'confirmation_close': bar['close'],
                'confirmation_low': bar['low'],
                'confirmation_high': bar['high'],
                'zones': {kind: {'low': z['low'], 'high': z['high'],
                                 'formed_at': _iso(z['time_utc'])}
                          for kind, z in zones.items()},
            })
    return setups


def _plan(setup, entry_price, symbol, balance, cfg):
    """Stop, target and lot for a setup, or a refusal carrying its reason."""
    spec = spec_for(symbol)
    buffer = cfg['stop']['buffer_pips'] * spec['pip']
    long_side = setup['direction'] == 'long'

    stop = (setup['confirmation_low'] - buffer) if long_side \
        else (setup['confirmation_high'] + buffer)
    distance = abs(entry_price - stop)
    if distance <= 0:
        return None, 'confirmation candle gives a zero-width stop'

    size = position_size(balance, distance, symbol, cfg)
    if not size['ok']:
        return None, size['reason']

    target = (entry_price + distance * cfg['target']['r']) if long_side \
        else (entry_price - distance * cfg['target']['r'])
    return {'stop': stop, 'target': target, 'r_distance': distance,
            'lot': size['lot'], 'risk': size['risk']}, None


def _walk(bars, start, plan, setup, symbol, cfg):
    """
    Carry a position forward until something closes it.

    Partials and the breakeven trail both trigger on price *reaching* an R
    multiple intrabar, which is how they behave live — the order sits at that
    price, it does not wait for a close.
    """
    spec = spec_for(symbol)
    long_side = setup['direction'] == 'long'
    entry, stop, target = plan['entry'], plan['stop'], plan['target']
    r = plan['r_distance']

    remaining = plan['lot']
    banked = 0.0
    fills = []            # (price, lot) for the volume-weighted exit price
    partial_done = False
    partial_skipped = None
    stop_kind = 'initial'
    trail_r = cfg['manage']['trail_to_be_at_r']
    partial_r = cfg['manage']['partial_at_r']
    partial_pct = cfg['manage']['partial_pct']
    offset = cfg['manage'].get('trail_offset_price') or 0.0

    def reached(bar, price):
        return (bar['high'] >= price) if long_side else (bar['low'] <= price)

    for i in range(start, len(bars)):
        bar = bars[i]

        # Stop first, always. This bar may also have touched the target; there
        # is no way to know the order, so assume the worse one.
        hit_stop = (bar['low'] <= stop) if long_side else (bar['high'] >= stop)
        if hit_stop:
            fills.append((stop, remaining))
            banked += _pnl(setup['direction'], entry, stop, remaining, spec['contract_size'])
            return {'exit_index': i, 'reason': STOP, 'fills': fills, 'net': banked,
                    'stop_kind': stop_kind, 'partial_taken': partial_done,
                    'partial_skipped': partial_skipped}

        if partial_pct and not partial_done and partial_r:
            level = entry + r * partial_r if long_side else entry - r * partial_r
            if reached(bar, level):
                lot = _partial_lot(remaining, partial_pct, spec)
                partial_done = True
                if lot is None:
                    # A position of one minimum lot cannot be halved, and
                    # rounding the request to a whole lot closes everything at
                    # 1R — which caps every winner at exactly +1R while losers
                    # stay at -1R, forcing negative expectancy no matter how
                    # good the entry is. The position is held instead, and the
                    # skip is recorded rather than silently reinterpreted.
                    partial_skipped = (
                        f'{remaining} lot is the smallest tradeable size for '
                        f'{symbol} and cannot be split, so the full position '
                        f'was carried to the target')
                else:
                    fills.append((level, lot))
                    banked += _pnl(setup['direction'], entry, level, lot,
                                   spec['contract_size'])
                    remaining = round(remaining - lot, 8)
                    if remaining <= 0:
                        return {'exit_index': i, 'reason': TARGET, 'fills': fills,
                                'net': banked, 'stop_kind': stop_kind,
                                'partial_taken': True,
                                'partial_skipped': partial_skipped}

        if trail_r and stop_kind == 'initial':
            level = entry + r * trail_r if long_side else entry - r * trail_r
            if reached(bar, level):
                # Not necessarily entry itself. An offset lets the stop clear
                # the spread, so a "free" trade does not exit having paid the
                # round trip.
                stop = entry + offset if long_side else entry - offset
                stop_kind = 'breakeven'

        hit_target = (bar['high'] >= target) if long_side else (bar['low'] <= target)
        if hit_target:
            fills.append((target, remaining))
            banked += _pnl(setup['direction'], entry, target, remaining,
                           spec['contract_size'])
            return {'exit_index': i, 'reason': TARGET, 'fills': fills, 'net': banked,
                    'stop_kind': stop_kind, 'partial_taken': partial_done,
                    'partial_skipped': partial_skipped}

    # Ran out of bars with the position open. Marking to the last close is a
    # valuation, not a trade, and the caller flags it as such.
    last = bars[-1]
    fills.append((last['close'], remaining))
    banked += _pnl(setup['direction'], entry, last['close'], remaining,
                   spec['contract_size'])
    return {'exit_index': len(bars) - 1, 'reason': END, 'fills': fills,
            'net': banked, 'stop_kind': stop_kind, 'partial_taken': partial_done,
            'partial_skipped': partial_skipped}


def simulate(bars, symbol, config=None, balance=1000.0, compound=False, setups=None):
    """
    Replay the config over `bars` and return trades plus what was skipped.

    One position at a time: a setup found while a trade is open is recorded as
    skipped rather than stacked, which matches how this is traded and keeps
    risk-per-trade meaningful.

    `compound` sizes each trade off the running balance instead of the starting
    one. Left off by default — with a small account it turns a sizing artefact
    into an exponential curve and makes two parameter sets incomparable.

    `setups` skips detection when the caller already has it. A sweep over
    management parameters runs dozens of configs whose entry signals are
    identical, and re-detecting for each one is the bulk of the work.
    """
    cfg = merged(config)
    if setups is None:
        setups = find_setups(bars, cfg)
    trades, skipped = [], []
    balance_now = balance
    busy_until = -1

    for setup in setups:
        entry_index = setup['index'] + 1
        if entry_index <= busy_until:
            skipped.append({**_ref(setup), 'reason': 'a position was already open'})
            continue
        if entry_index >= len(bars):
            skipped.append({**_ref(setup), 'reason': 'no bar after the confirmation'})
            continue

        entry_price = bars[entry_index]['open']
        plan, refusal = _plan(setup, entry_price, symbol,
                              balance_now if compound else balance, cfg)
        if plan is None:
            skipped.append({**_ref(setup), 'reason': refusal})
            continue

        plan['entry'] = entry_price
        result = _walk(bars, entry_index, plan, setup, symbol, cfg)
        busy_until = result['exit_index']
        balance_now = round(balance_now + result['net'], 2)

        total_lot = sum(lot for _, lot in result['fills'])
        exit_price = (sum(p * lot for p, lot in result['fills']) / total_lot
                      if total_lot else None)
        opened = bars[entry_index]['time_utc']
        closed = bars[result['exit_index']]['time_utc']

        trades.append({
            # The pair_trades() shape, field for field.
            'position_id': f'sim-{len(trades) + 1}',
            'symbol': symbol,
            'direction': setup['direction'],
            'opened_utc': opened,
            'opened': _iso(opened),
            'closed_utc': closed,
            'closed': _iso(closed),
            'duration_sec': closed - opened,
            'entry_price': round(entry_price, 5),
            'exit_price': round(exit_price, 5) if exit_price is not None else None,
            'volume': round(plan['lot'], 2),
            'net': round(result['net'], 2),
            'exit_reason': result['reason'],
            'deals': 1 + len(result['fills']),
            'partial_closes': max(0, len(result['fills']) - 1),
            'open': False,
            'entry_missing': False,
            # Everything the real history cannot tell us.
            'sim': {
                'conditions': setup['conditions'],
                'stop': round(plan['stop'], 5),
                'target': round(plan['target'], 5),
                'r_distance': round(plan['r_distance'], 5),
                'planned_risk': plan['risk'],
                'r_multiple': (round(result['net'] / plan['risk'], 2)
                               if plan['risk'] else None),
                # Which stop actually filled. Real MT5 history reports both as
                # 'stop_loss', which is why the observed-stop medians came out
                # bimodal and unreadable.
                'stop_kind': result['stop_kind'],
                'partial_taken': result['partial_taken'],
                # Set when the position was too small to split. Without it the
                # config says "50% partial" and the trades quietly say
                # otherwise.
                'partial_skipped': result.get('partial_skipped'),
                'balance_after': balance_now,
            },
        })

    return {'trades': trades, 'skipped': skipped,
            'summary': summarize(trades, skipped, balance, balance_now, cfg)}


def _ref(setup):
    return {'time': setup['time'], 'direction': setup['direction'],
            'conditions': setup['conditions']}


def summarize(trades, skipped, starting_balance, ending_balance, cfg):
    """
    Headline numbers, with the ones that need a sample size withheld.

    A win rate over six trades is noise wearing a percentage sign. The
    threshold is the same MIN_RELIABLE used elsewhere so a simulated result and
    a real one are held to one standard.
    """
    closed = [t for t in trades if t['exit_reason'] != END]
    if not trades:
        # The reasons matter most here. "No trades" because the balance could
        # not carry one minimum lot is a different finding from "no setups",
        # and reporting a bare zero hides which one happened.
        return {'trades': 0, 'closed': 0, 'skipped': len(skipped),
                'skipped_reasons': _count(s['reason'] for s in skipped),
                'note': 'no setups produced a trade'}

    wins = [t for t in closed if t['net'] > 0]
    losses = [t for t in closed if t['net'] < 0]
    rs = [t['sim']['r_multiple'] for t in closed if t['sim']['r_multiple'] is not None]
    reliable = len(closed) >= 10

    gross_win = sum(t['net'] for t in wins)
    gross_loss = abs(sum(t['net'] for t in losses))

    return {
        'trades': len(trades),
        'closed': len(closed),
        'unresolved': len(trades) - len(closed),
        'skipped': len(skipped),
        'skipped_reasons': _count(s['reason'] for s in skipped),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate_pct': round(len(wins) / len(closed) * 100, 1) if closed else None,
        'net': round(sum(t['net'] for t in trades), 2),
        'expectancy': round(sum(t['net'] for t in closed) / len(closed), 2)
                      if closed else None,
        'avg_r': round(sum(rs) / len(rs), 2) if rs else None,
        # Spread of the R outcomes, so a caller can tell an average of +0.2
        # from twenty trades apart from the same average from two hundred.
        # The parameter sweep needs it to know what its own noise floor is.
        'r_stdev': round(_stdev(rs), 3) if len(rs) > 1 else None,
        # Undefined without a losing trade — reported as null rather than as a
        # spectacular infinity.
        'profit_factor': round(gross_win / gross_loss, 2) if gross_loss else None,
        'starting_balance': starting_balance,
        'ending_balance': ending_balance,
        'exit_reasons': _count(t['exit_reason'] for t in trades),
        'stop_kinds': _count(t['sim']['stop_kind'] for t in trades
                             if t['exit_reason'] == STOP),
        'reliable': reliable,
        'note': None if reliable else
                f'{len(closed)} closed trades — below the 10 needed for these '
                f'figures to mean anything. Read them as a smoke test, not a result.',
        'config': {'mode': cfg['entry']['mode'],
                   'conditions_on': sorted(n for n, c in cfg['entry']['conditions'].items()
                                           if c.get('on')),
                   'trail_to_be_at_r': cfg['manage']['trail_to_be_at_r'],
                   'partial_pct': cfg['manage']['partial_pct'],
                   'target_r': cfg['target']['r']},
    }


def _stdev(values):
    """Sample standard deviation. Stdlib statistics would do, but this module
    stays dependency-free like the rest of the pure layer."""
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
