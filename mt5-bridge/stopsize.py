"""
Was the stop too tight?

The exit distribution says 60% of trades close at stop loss without being able
to say why. Two explanations fit: the stop sat inside normal noise, or the
entries were wrong. This module settles it, and needs nothing journalled.

The key observation is that **for a trade closed at stop loss, the exit price is
the stop**. MetaTrader 5 does not record the stop level in deal history, but it
does not need to — the fill tells you where it was. So the typical stop distance
is recoverable from the trades that actually hit one.

Two tests, in increasing order of usefulness:

  ATR ratio        stop distance against one bar's typical range. Context, but
                   sensitive to which timeframe the bars came from.

  Survival test    the counterfactual that matters: apply the typical stop
                   distance to every winning trade and count how many would have
                   been stopped out before they worked. A high number means the
                   stop sits inside the range winners routinely travel through,
                   which is the concrete form of "too tight" — and it assumes
                   nothing about volatility models or timeframes.

Pure functions over paired trades, excursion rows and bars.
"""
import bisect

# A stop distance is only observable on trades that hit one. Manual exits at a
# loss are where the trader gave up, not where the stop was.
STOP_EXITS = ('stop_loss',)

# Below this, a per-symbol stop distance is an anecdote rather than a typical.
MIN_STOPS = 10

ATR_PERIOD = 14


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def true_range(bar, previous_close):
    """
    Classic true range: the widest of the bar, the gap up, and the gap down.

    Falls back to the bar's own range on the first bar, where there is no
    previous close to gap from.
    """
    span = bar['high'] - bar['low']
    if previous_close is None:
        return span
    return max(span, abs(bar['high'] - previous_close), abs(bar['low'] - previous_close))


def atr(bars, period=ATR_PERIOD):
    """Average true range over the given bars, or None if there are too few."""
    if not bars or len(bars) < 2:
        return None
    window = bars[-(period + 1):]
    ranges = [true_range(bar, window[i - 1]['close'] if i else None)
              for i, bar in enumerate(window)]
    ranges = ranges[1:] if len(ranges) > 1 else ranges
    return sum(ranges) / len(ranges) if ranges else None


def atr_before(bars, times, at_ts, period=ATR_PERIOD):
    """
    ATR over the bars strictly before a timestamp.

    Strictly before, because a stop placed at entry cannot have known the range
    of the bars that came after it. Including them would be lookahead, and it
    would flatter every stop that happened to precede a quiet stretch.
    """
    cut = bisect.bisect_left(times, at_ts)
    if cut < 2:
        return None
    return atr(bars[max(0, cut - period - 1):cut], period=period)


def observed_stops(trades):
    """
    Stop distances per symbol, taken from the trades that hit a stop.

    Returns the median and the spread. The spread matters: a tight
    interquartile range means one consistent stop rule, a wide one means the
    "typical" stop is an average of several different habits and the survival
    test below should be read more loosely.
    """
    by_symbol = {}
    for trade in trades or []:
        if trade.get('open') or str(trade.get('exit_reason')) not in STOP_EXITS:
            continue
        entry, exit_price = trade.get('entry_price'), trade.get('exit_price')
        if entry is None or exit_price is None:
            continue
        distance = abs(entry - exit_price)
        if distance > 0:
            by_symbol.setdefault(trade.get('symbol'), []).append(distance)

    out = {}
    for symbol, distances in by_symbol.items():
        ordered = sorted(distances)
        n = len(ordered)
        out[symbol] = {
            'stops_observed': n,
            'median_distance': round(_median(ordered), 5),
            'p25': round(ordered[max(0, int(n * 0.25) - 1)], 5),
            'p75': round(ordered[min(n - 1, int(n * 0.75))], 5),
            'reliable': n >= MIN_STOPS,
        }
    return out


def survival_test(rows, stops_by_symbol):
    """
    Would the typical stop have killed the winners too?

    For each winning trade, compare the worst it ever went against you (MAE) to
    the median stop distance for that symbol. A winner whose MAE exceeded that
    distance would have been stopped out before it worked — so a high share is
    direct evidence the stop sits inside the range winning trades pass through.

    This is deliberately preferred over an ATR comparison: it uses the same
    instrument, the same setups and the same holding times, and assumes nothing
    about how volatility scales.
    """
    winners = [r for r in (rows or [])
               if (r.get('net') or 0) > 0 and r.get('mae') is not None]
    if not winners:
        return None

    killed, examined, skipped = [], 0, 0
    for row in winners:
        stop = (stops_by_symbol.get(row.get('symbol')) or {}).get('median_distance')
        if stop is None:
            skipped += 1
            continue
        examined += 1
        if row['mae'] >= stop:
            killed.append(row)

    if not examined:
        return None

    return {
        'winners_examined': examined,
        'winners_without_a_stop_reference': skipped,
        'would_have_been_stopped': len(killed),
        'would_have_been_stopped_pct': round(len(killed) / examined * 100, 1),
        # What that would have cost, had every one of them been cut at the stop.
        'profit_at_risk': round(sum(r.get('net') or 0 for r in killed), 2),
        'reliable': examined >= MIN_STOPS,
    }


def atr_context(trades, bars_by_symbol, period=ATR_PERIOD):
    """
    Stop distance expressed in ATR, measured on the bars before each entry.

    Supporting evidence rather than the headline: the ratio depends entirely on
    the timeframe of the bars supplied, so a "0.8x ATR" on one-minute bars means
    something different from the same figure on hourly ones.
    """
    indexed = {symbol: (rows, [b['time_utc'] for b in rows])
               for symbol, rows in (bars_by_symbol or {}).items()}

    ratios = []
    for trade in trades or []:
        if trade.get('open') or str(trade.get('exit_reason')) not in STOP_EXITS:
            continue
        entry, exit_price = trade.get('entry_price'), trade.get('exit_price')
        opened = trade.get('opened_utc')
        found = indexed.get(trade.get('symbol'))
        if not found or entry is None or exit_price is None or opened is None:
            continue
        bars, times = found
        value = atr_before(bars, times, opened, period=period)
        if not value:
            continue
        ratios.append(abs(entry - exit_price) / value)

    if not ratios:
        return None
    return {
        'samples': len(ratios),
        'period': period,
        'median_stop_in_atr': round(_median(ratios), 2),
        'reliable': len(ratios) >= MIN_STOPS,
    }


def _verdict(stops, survival, atr_stats, spread=None):
    """
    One sentence, and only when the evidence supports one.

    Order matters. Inconsistent stop placement is checked first because it is a
    fact about the stops themselves — it needs no winners to establish, and it
    outranks the survival percentage: a stop that ranges over an order of
    magnitude is not a rule with variance, it is the absence of a rule, and no
    backtest can express one.
    """
    atr_note = (f' The typical stop is {atr_stats["median_stop_in_atr"]}x the '
                f'average bar range before entry.' if atr_stats else '')

    if spread and spread['ratio'] >= 5:
        # The survival test compares every winner against a single median stop,
        # which is weak evidence when no such stop meaningfully exists. Quote it
        # only to say so.
        rate = (survival or {}).get('would_have_been_stopped_pct')
        caveat = (f' The survival figure below ({rate:.0f}%) compares winners '
                  f'against one median stop, so read it as under-powered rather '
                  f'than reassuring.' if rate is not None else '')
        return {
            'too_tight': 'inconsistent',
            'verdict': (
                f'Stop placement varies too much to judge as a single rule: on '
                f'{spread["symbol"]} the middle half of your stops span '
                f'{spread["p25"]:g} to {spread["p75"]:g}, a {spread["ratio"]:.0f}x '
                f'range.{caveat} Before anything can be backtested, where the '
                f'stop goes needs an answer.{atr_note}'),
        }

    if not survival or not survival['reliable']:
        n = (survival or {}).get('winners_examined', 0)
        return {
            'too_tight': None,
            'verdict': (f'Only {n} winning trades could be compared against an '
                        f'observed stop distance — not enough to judge. A wider '
                        f'window (from=0) will usually fix this.{atr_note}'),
        }

    rate = survival['would_have_been_stopped_pct']

    if rate >= 40:
        return {
            'too_tight': True,
            'verdict': (
                f'{rate:.0f}% of your winning trades dipped further against you '
                f'than your typical stop distance. Applied consistently, that '
                f'stop would have cut them before they worked — costing '
                f'{abs(survival["profit_at_risk"]):.2f} of realised profit. '
                f'The stop is inside the range your winners routinely travel '
                f'through.{atr_note}'),
        }
    if rate <= 15:
        return {
            'too_tight': False,
            'verdict': (
                f'Only {rate:.0f}% of winners went further against you than your '
                f'typical stop distance, so the stop is not what is killing them. '
                f'Widening it would mostly just increase the loss per stop-out.'
                f'{atr_note}'),
        }
    return {
        'too_tight': 'partial',
        'verdict': (
            f'{rate:.0f}% of winners dipped past your typical stop distance — '
            f'enough to cost something, not enough to call the stop the main '
            f'problem on its own. Worth testing against the shakeout rate rather '
            f'than acting on alone.{atr_note}'),
    }


def widest_spread(stops):
    """
    The symbol whose stop placement varies most, among those with enough data.

    Reported because consistency is a precondition for everything downstream: a
    stop that ranges over an order of magnitude is not a rule with variance, it
    is the absence of a rule, and no backtest can express it.
    """
    worst = None
    for symbol, stats in (stops or {}).items():
        if not stats.get('reliable') or not stats.get('p25'):
            continue
        ratio = stats['p75'] / stats['p25']
        if worst is None or ratio > worst['ratio']:
            worst = {'symbol': symbol, 'ratio': round(ratio, 1),
                     'p25': stats['p25'], 'p75': stats['p75'],
                     'median': stats['median_distance']}
    return worst


def analyze(trades, excursion_rows, bars_by_symbol=None, period=ATR_PERIOD):
    """Everything the stop-distance question needs, in one pass."""
    stops = observed_stops(trades)
    survival = survival_test(excursion_rows, stops)
    atr_stats = atr_context(trades, bars_by_symbol, period=period)
    spread = widest_spread(stops)
    return {
        'by_symbol': stops,
        'survival': survival,
        'atr': atr_stats,
        'spread': spread,
        'min_reliable': MIN_STOPS,
        **_verdict(stops, survival, atr_stats, spread),
    }
