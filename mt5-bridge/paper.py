"""
What the strategy would be doing right now, without touching the account.

Reconstructed from bars on every call rather than accumulated in a state file.
A paper runner that carries state is a paper runner that drifts: restart it and
the position is gone, run two and they disagree, and the record of what it
"would have done" quietly becomes a record of when the process happened to be
alive. Recomputing from the same bars every time cannot drift, survives a
restart by construction, and gives an identical answer to two callers.

**The forming bar.** MT5 returns the current, incomplete candle as the last row
of `copy_rates_from_pos`. Its high, low and close are whatever price has done
so far this minute, and all three keep changing. Detecting on it means acting
on a candle that has not closed — signals appear and vanish as the bar moves,
a stop looks hit and then does not, and the live results stop resembling the
backtest for reasons nobody can reproduce afterwards. It is the live twin of
the lookahead that `knowable_at` exists to prevent, and every function here
drops it.

Nothing in this module can place, modify or cancel an order. It reports what a
strategy would do; acting on that is a decision made elsewhere, by a person.
"""
from simulate import END, find_setups, simulate
from strategy import merged, position_size, spec_for

# Seconds per bar, for deciding whether the last bar has closed. That cannot be
# inferred from the bars themselves — a quiet market and a missing bar look
# identical — so it has to be known.
#
# Keys mirror normalize.TIMEFRAMES exactly, and a test fails if the two drift:
# a timeframe the client accepts but this does not would silently fall back to
# dropping one bar, which is safe but hides the mismatch. 'M' is deliberately
# absent — a calendar month is not a fixed number of seconds, and inventing 30
# days would be wrong eleven months a year.
TIMEFRAME_SECONDS = {
    '1': 60, '2': 120, '3': 180, '5': 300, '10': 600, '15': 900, '30': 1800,
    '60': 3600, '120': 7200, '240': 14400,
    'D': 86400, 'W': 604800,
}


def timeframe_seconds(timeframe):
    return TIMEFRAME_SECONDS.get(str(timeframe).upper().strip())


def closed_bars(bars, now=None, timeframe=None):
    """
    Bars that have finished, dropping the one still forming.

    A bar timestamped `t` covers `[t, t + period)`, so it is complete only once
    `now` has reached `t + period`. Without a `now` or a known period the last
    bar is dropped anyway: assuming it closed is the failure that matters, and
    losing one bar of history costs nothing by comparison.
    """
    if not bars:
        return []

    period = timeframe_seconds(timeframe) if timeframe is not None else None
    if now is None or period is None:
        return bars[:-1]

    return [b for b in bars if b['time_utc'] + period <= now]


def position_view(trade, symbol, config):
    """The open paper position, described the way a person would need to read it."""
    if trade is None:
        return None

    cfg = merged(config)
    sim = trade.get('sim') or {}
    return {
        'direction': trade['direction'],
        'opened': trade['opened'],
        'entry_price': trade['entry_price'],
        'stop': sim.get('stop'),
        'target': sim.get('target'),
        'lot': trade['volume'],
        'risk': sim.get('planned_risk'),
        'r_distance': sim.get('r_distance'),
        # Whether the stop has already been moved is the single most useful
        # thing to know about a live position, and the parameter under test.
        'stop_kind': sim.get('stop_kind'),
        'partial_taken': sim.get('partial_taken'),
        'conditions': sim.get('conditions'),
        'management': {
            'partial_pct': cfg['manage']['partial_pct'],
            'partial_at_r': cfg['manage']['partial_at_r'],
            'trail_to_be_at_r': cfg['manage']['trail_to_be_at_r'],
            'target_r': cfg['target']['r'],
        },
    }


def pending_view(setup, bars, symbol, config, balance):
    """
    A confirmation that has fired with no bar yet to enter on.

    This is the one moment a live runner and a backtest genuinely differ: the
    backtest already knows the next bar's open, and here it does not exist. The
    levels are quoted against the confirmation candle, and the entry price is
    explicitly null rather than guessed from the last close.
    """
    if setup is None:
        return None

    cfg = merged(config)
    spec = spec_for(symbol)
    buffer = cfg['stop']['buffer_pips'] * spec['pip']
    long_side = setup['direction'] == 'long'
    stop = (setup['confirmation_low'] - buffer) if long_side \
        else (setup['confirmation_high'] + buffer)

    return {
        'direction': setup['direction'],
        'confirmed_at': setup['time'],
        'conditions': setup['conditions'],
        'stop': round(stop, 5),
        # Not knowable until the next bar opens. Filling this in from the last
        # close would be the exact optimism the simulator refuses.
        'entry_price': None,
        'entry_note': 'entry is the next bar open, which has not happened yet',
        'zones': setup['zones'],
    }


def status(bars, symbol, config=None, balance=1000.0, now=None, timeframe=None,
           recent=5):
    """
    The strategy's current standing: open position, pending signal, recent history.

    Everything is derived from the closed bars, so two calls a second apart
    agree, and a restart changes nothing.
    """
    cfg = merged(config)
    usable = closed_bars(bars, now=now, timeframe=timeframe)
    if not usable:
        return {'success': True, 'symbol': symbol, 'bars_used': 0,
                'position': None, 'pending': None, 'recent': [],
                'note': ('no closed bars yet — the only bar available is still '
                         'forming, and acting on it is the live equivalent of '
                         'lookahead')}

    run = simulate(usable, symbol, cfg, balance=balance)
    trades = run['trades']

    # A trade still marked end_of_data at the last bar has not resolved: that
    # is the open position, not a completed trade.
    open_trade = trades[-1] if trades and trades[-1]['exit_reason'] == END else None
    closed = [t for t in trades if t is not open_trade]

    pending = None
    if open_trade is None:
        setups = find_setups(usable, cfg)
        # Only a confirmation on the very last closed bar is still actionable;
        # anything earlier already had its entry bar and was either taken or
        # skipped for a reason the simulation recorded.
        if setups and setups[-1]['index'] == len(usable) - 1:
            pending = pending_view(setups[-1], usable, symbol, cfg, balance)

    return {
        'success': True,
        'symbol': symbol,
        'timeframe': str(timeframe) if timeframe is not None else None,
        'bars_used': len(usable),
        'bars_dropped_as_forming': len(bars) - len(usable),
        'last_closed_bar': usable[-1]['time_utc'],
        # The clock the forming-bar boundary was judged against. Surfaced so a
        # skewed machine clock is visible rather than silently keeping a bar
        # that had not closed.
        'evaluated_at': now,
        'bar_period_sec': timeframe_seconds(timeframe) if timeframe is not None else None,
        'balance': balance,
        'position': position_view(open_trade, symbol, cfg),
        'pending': pending,
        'recent': closed[-recent:] if recent else [],
        'totals': run['summary'],
        'skipped': run['summary'].get('skipped_reasons'),
        'read_only': True,
    }


def affordability(balance, symbol, stop_distance, config=None):
    """
    Whether this account can take this trade at all, before anything else.

    Separated out because it is the question that mattered most on the live
    account and it has nothing to do with whether the setup is good. A balance
    below the floor does not get a smaller position — there is nothing smaller
    — it gets whatever the minimum lot costs.
    """
    sizing = position_size(balance, stop_distance, symbol, config)
    spec = spec_for(symbol)
    cfg = merged(config)
    per_lot = stop_distance * spec['contract_size'] if stop_distance else None
    floor = (spec['min_lot'] * per_lot / (cfg['size']['risk_pct'] / 100)
             if per_lot and cfg['size']['risk_pct'] else None)

    return {
        'ok': sizing['ok'],
        'balance': balance,
        'stop_distance': stop_distance,
        **{k: v for k, v in sizing.items() if k != 'ok'},
        'minimum_balance_for_target_risk': round(floor, 2) if floor else None,
    }
