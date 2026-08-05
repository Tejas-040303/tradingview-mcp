"""
What the strategy signalled against what was actually traded.

MT5 records fills, so it can only ever show trades that were *taken*. A setup
you saw and passed on leaves no trace anywhere, which means the two most
important questions about discretionary trading are unanswerable from broker
data alone:

  * the signals you skipped — were they the good ones?
  * the trades you took that the strategy never called — how did those do?

Matching simulated signals against real trades answers both. Three buckets come
out: `followed` (signalled and traded), `missed` (signalled, not traded), and
`discretionary` (traded with no signal behind it).

**The comparison that is fair, and the one that is not.** Simulated P&L assumes
perfect fills at the stop and the target, no spread, no slippage. Real P&L does
not. So "signals I took" versus "signals I skipped" is compared *simulated to
simulated* — same assumptions on both sides — and the real numbers are reported
separately. The gap between a followed signal's simulated and real result is
its own diagnostic: it is the execution cost, and it is the difference between
a backtest that says 1.2R and an account that says 0.4R.
"""

# Below this many trades in a bucket, its averages are reported as null with a
# reason. Three skipped setups do not establish that you skip the good ones.
MIN_BUCKET = 10

# How far apart an entry can be and still count as the same trade. Fifteen
# minutes is generous on a 5-minute chart and deliberately so: a match that is
# wrong in either direction corrupts two buckets at once.
DEFAULT_TOLERANCE_SEC = 900


def _distance(signal, trade):
    if signal.get('opened_utc') is None or trade.get('opened_utc') is None:
        return None
    if signal.get('symbol') != trade.get('symbol'):
        return None
    if signal.get('direction') != trade.get('direction'):
        return None
    return abs(signal['opened_utc'] - trade['opened_utc'])


def match(signals, trades, tolerance_sec=DEFAULT_TOLERANCE_SEC):
    """
    Pair each signal with at most one real trade, closest first.

    Globally closest-first rather than first-come-first-served: taking signals
    in order lets an early signal claim a trade that belonged to the next one,
    and the mismatch then cascades through everything after it.
    """
    pairs = []
    for i, signal in enumerate(signals or []):
        for j, trade in enumerate(trades or []):
            gap = _distance(signal, trade)
            if gap is not None and gap <= tolerance_sec:
                pairs.append((gap, i, j))
    pairs.sort()

    used_signals, used_trades, matched = set(), set(), []
    for gap, i, j in pairs:
        if i in used_signals or j in used_trades:
            continue
        used_signals.add(i)
        used_trades.add(j)
        matched.append({'signal': signals[i], 'trade': trades[j], 'gap_sec': gap})

    missed = [s for i, s in enumerate(signals or []) if i not in used_signals]
    discretionary = [t for j, t in enumerate(trades or []) if j not in used_trades]
    return matched, missed, discretionary


def _avg(values):
    rows = [v for v in values if v is not None]
    return round(sum(rows) / len(rows), 3) if rows else None


def _bucket(rows, key, label):
    """Average of one field, or null with the reason it is null."""
    values = [r.get(key) for r in rows if r.get(key) is not None]
    if len(values) < MIN_BUCKET:
        return {'n': len(values), 'avg': None,
                'note': f'{len(values)} {label} — below {MIN_BUCKET}, '
                        f'no average worth quoting'}
    return {'n': len(values), 'avg': _avg(values), 'note': None}


def _sim_r(trade):
    return (trade.get('sim') or {}).get('r_multiple')


def reconcile(signals, trades, tolerance_sec=DEFAULT_TOLERANCE_SEC):
    """
    Signals against fills, bucketed and compared only where comparison is fair.
    """
    matched, missed, discretionary = match(signals, trades, tolerance_sec)

    followed_sim = [m['signal'] for m in matched]
    followed_real = [m['trade'] for m in matched]

    taken = _bucket(followed_sim, 'net', 'followed signals')
    skipped = _bucket(missed, 'net', 'skipped signals')
    taken_r = _bucket([{'r': _sim_r(s)} for s in followed_sim], 'r', 'followed signals')
    skipped_r = _bucket([{'r': _sim_r(s)} for s in missed], 'r', 'skipped signals')

    # Execution gap: same trades, simulated versus what the account recorded.
    gaps = [m['trade']['net'] - m['signal']['net']
            for m in matched
            if m['trade'].get('net') is not None and m['signal'].get('net') is not None]

    return {
        'signals': len(signals or []),
        'real_trades': len(trades or []),
        'tolerance_sec': tolerance_sec,
        'followed': len(matched),
        'missed': len(missed),
        'discretionary': len(discretionary),
        'follow_rate_pct': (round(len(matched) / len(signals) * 100, 1)
                            if signals else None),
        # Both sides simulated — the only fair way to ask whether the skipped
        # setups were the good ones.
        'simulated': {
            'followed_net': taken,
            'missed_net': skipped,
            'followed_r': taken_r,
            'missed_r': skipped_r,
        },
        'real': {
            'followed_net': _bucket(followed_real, 'net', 'followed trades'),
            'discretionary_net': _bucket(discretionary, 'net', 'discretionary trades'),
        },
        'execution_gap': {
            'n': len(gaps),
            # Real minus simulated. Negative means the account did worse than
            # the backtest on the same signals — spread, slippage, a stop that
            # filled through, or an exit taken by hand.
            'avg': _avg(gaps) if len(gaps) >= MIN_BUCKET else None,
            'note': (None if len(gaps) >= MIN_BUCKET else
                     f'{len(gaps)} matched trades — below {MIN_BUCKET}'),
        },
        'findings': _findings(taken, skipped, taken_r, skipped_r,
                              discretionary, len(matched), len(missed)),
    }


def _findings(taken, skipped, taken_r, skipped_r, discretionary, n_followed, n_missed):
    """
    Only what the buckets actually support.

    Every claim here needs both sides above MIN_BUCKET, because "the setups you
    skip are the good ones" is a serious thing to tell somebody and it is
    trivially producible from four trades.
    """
    out = []

    if taken_r['avg'] is not None and skipped_r['avg'] is not None:
        difference = skipped_r['avg'] - taken_r['avg']
        if abs(difference) < 0.15:
            out.append(
                f'The signals you took and the ones you skipped performed about '
                f'the same in simulation ({taken_r["avg"]:+.2f} R against '
                f'{skipped_r["avg"]:+.2f} R). Your filtering is neither helping '
                f'nor hurting — it is noise.')
        elif difference > 0:
            out.append(
                f'The signals you skipped simulated better than the ones you took '
                f'({skipped_r["avg"]:+.2f} R against {taken_r["avg"]:+.2f} R over '
                f'{n_missed} and {n_followed} trades). Worth knowing before '
                f'trusting the filter you are applying by hand.')
        else:
            out.append(
                f'The signals you took simulated better than the ones you skipped '
                f'({taken_r["avg"]:+.2f} R against {skipped_r["avg"]:+.2f} R). '
                f'Your selection is adding something the rules do not capture.')

    real_disc = _bucket(discretionary, 'net', 'discretionary trades')
    if real_disc['avg'] is not None:
        out.append(
            f'{real_disc["n"]} trades had no signal behind them at all, averaging '
            f'{real_disc["avg"]:+.2f}. These are the ones no backtest can '
            f'evaluate, because the strategy never proposed them.')

    if not out:
        out.append('Nothing has enough trades behind it to support a finding yet.')
    return out
