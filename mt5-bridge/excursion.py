"""
What the price did during a trade, and after you left it.

MetaTrader 5 reports where you got in and out. It does not report how far the
trade went against you before it worked, how close to your target it came before
reversing, or — the question that matters most for a stop-heavy account — whether
price came straight back after stopping you out.

All three are recoverable from bar data with no journalling and no discipline,
and they apply retroactively to every trade already in the history.

Pure functions over bars: no MetaTrader5, no filesystem, no network. Bars are
the shape mt5_client.bars() emits — `time_utc`, open, high, low, close.

Terminology, since MAE/MFE are often stated loosely:

  MAE  maximum adverse excursion — the worst the position was underwater,
       measured from the entry price, as a positive number
  MFE  maximum favourable excursion — the best it was ever up, likewise

Both are measured between entry and exit, so they describe heat you actually
sat through rather than anything hypothetical.
"""
import bisect

# Post-exit horizons, in seconds. Five minutes is the one that tests "my stop
# sat inside normal noise"; an hour is long enough to distinguish a real
# reversal from a spread flicker.
DEFAULT_HORIZONS = (300, 900, 3600)

# Below this many trades an aggregate is reported but flagged, matching the
# convention the rest of the analytics use.
MIN_RELIABLE = 10


def _times(bars):
    return [b['time_utc'] for b in bars]


def _slice(bars, times, start, end):
    """Bars with start <= time_utc <= end. Bars must be sorted by time."""
    lo = bisect.bisect_left(times, start)
    hi = bisect.bisect_right(times, end)
    return bars[lo:hi]


def _slice_after(bars, times, start, end):
    """
    Bars strictly after `start`, up to `end`.

    Post-exit analysis must exclude the bar the exit happened on. That bar spans
    the moments before the fill, so its range still contains the entry price —
    including it makes "price came back to my entry" true for almost every
    trade, which is the opposite of a finding.
    """
    lo = bisect.bisect_right(times, start)
    hi = bisect.bisect_right(times, end)
    return bars[lo:hi]


def _signed(direction, move):
    """
    Convert a raw price move into 'in your favour' for this trade's direction.

    A long profits when price rises; a short profits when it falls. Everything
    downstream reads positive as good, so the sign flip lives here once rather
    than in every caller.
    """
    return move if direction == 'long' else -move


def excursion_for_trade(trade, bars, times=None, horizons=DEFAULT_HORIZONS):
    """
    MAE, MFE and post-exit behaviour for one trade.

    Returns None when the bars do not cover the trade — an absence, not zeros,
    because "no data" and "never moved" are different facts and only one of them
    should reach an average.
    """
    entry = trade.get('entry_price')
    exit_price = trade.get('exit_price')
    opened = trade.get('opened_utc')
    closed = trade.get('closed_utc')
    direction = trade.get('direction')

    if None in (entry, exit_price, opened, closed, direction) or trade.get('open'):
        return None

    times = times if times is not None else _times(bars)
    during = _slice(bars, times, opened, closed)
    if not during:
        return None

    highest = max(b['high'] for b in during)
    lowest = min(b['low'] for b in during)

    if direction == 'long':
        mfe, mae = highest - entry, entry - lowest
    else:
        mfe, mae = entry - lowest, highest - entry

    # Excursions cannot be negative: if price never traded against you, the
    # adverse excursion is zero, not a favourable move wearing a minus sign.
    mfe, mae = max(0.0, mfe), max(0.0, mae)

    realised = _signed(direction, exit_price - entry)
    row = {
        'position_id': trade.get('position_id'),
        'symbol': trade.get('symbol'),
        'direction': direction,
        'exit_reason': trade.get('exit_reason'),
        'net': trade.get('net'),
        'entry_price': entry,
        'exit_price': exit_price,
        'closed_utc': closed,
        'bars_during': len(during),
        'mae': round(mae, 5),
        'mfe': round(mfe, 5),
        'realised_move': round(realised, 5),
        # How much of the best available move you actually took. Above 1 means
        # you exited beyond the peak the position reached while open, which
        # happens on a gap.
        'capture_ratio': round(realised / mfe, 3) if mfe > 0 else None,
        # How close the worst moment came to the whole favourable move. A high
        # ratio means the trade was nearly stopped before it worked.
        'heat_ratio': round(mae / mfe, 3) if mfe > 0 else None,
    }

    # "Did price come back to my entry" only means anything when the exit was on
    # the losing side of it. Leave a winner and the exit already sits beyond the
    # entry, so price is trivially at-or-past it forever after — a vacuous 100%
    # that would sit next to the meaningful stop-loss figure and outrank it.
    row['shakeout_applicable'] = realised < 0

    row['post_exit'] = _post_exit(bars, times, closed, entry, exit_price,
                                  direction, horizons, row['shakeout_applicable'])
    row['returned_to_entry_sec'] = (
        _return_to_entry(bars, times, closed, entry, direction, max(horizons))
        if row['shakeout_applicable'] else None)
    return row


def _post_exit(bars, times, closed, entry, exit_price, direction, horizons,
               shakeout_applicable=True):
    """
    Where price went after the exit, per horizon.

    Positive means the trade would have made more had it been left alone;
    negative means leaving was the better choice. Horizons with no bars yet —
    a trade that closed minutes ago — report None rather than a stale value.
    """
    out = {}
    for horizon in horizons:
        window = _slice_after(bars, times, closed, closed + horizon)
        if not window:
            out[str(horizon)] = None
            continue
        last = window[-1]['close']
        best = max(b['high'] for b in window)
        worst = min(b['low'] for b in window)
        out[str(horizon)] = {
            'move': round(_signed(direction, last - exit_price), 5),
            'best': round(_signed(direction, (best if direction == 'long' else worst) - exit_price), 5),
            'worst': round(_signed(direction, (worst if direction == 'long' else best) - exit_price), 5),
            # Did price get back to where the trade was opened? For a losing
            # exit this is the concrete form of "I was shaken out of a good
            # entry". For a winning one it is meaningless, so it is None rather
            # than a true that means nothing.
            'reached_entry': (((best >= entry) if direction == 'long' else (worst <= entry))
                              if shakeout_applicable else None),
            'bars': len(window),
        }
    return out


def _return_to_entry(bars, times, closed, entry, direction, within):
    """Seconds until price first touches the entry again, or None."""
    for bar in _slice_after(bars, times, closed, closed + within):
        touched = bar['high'] >= entry if direction == 'long' else bar['low'] <= entry
        if touched:
            return max(0, bar['time_utc'] - closed)
    return None


def compute(trades, bars_by_symbol, horizons=DEFAULT_HORIZONS):
    """
    Excursions for every trade the bars can cover.

    Bars are passed in per symbol and pre-sorted once, so a few hundred trades
    cost one pass rather than one query each.
    """
    indexed = {symbol: (rows, _times(rows))
               for symbol, rows in (bars_by_symbol or {}).items()}

    out = []
    for trade in trades or []:
        entry = indexed.get(trade.get('symbol'))
        if not entry:
            continue
        bars, times = entry
        row = excursion_for_trade(trade, bars, times, horizons)
        if row:
            out.append(row)
    return out


def _mean(values):
    return sum(values) / len(values) if values else None


def summarize(rows, horizons=DEFAULT_HORIZONS):
    """
    Aggregate excursions, split by how the trade ended.

    The headline this exists to produce: of the trades that closed at stop loss,
    what share saw price return to the entry within five minutes. A high number
    is evidence the stop sat inside normal noise rather than that the entries
    were wrong — the two explanations the exit distribution alone cannot separate.
    """
    if not rows:
        return None

    def bucket(subset):
        if not subset:
            return None
        stats = {
            'trades': len(subset),
            'avg_mae': round(_mean([r['mae'] for r in subset]), 5),
            'avg_mfe': round(_mean([r['mfe'] for r in subset]), 5),
            'reliable': len(subset) >= MIN_RELIABLE,
        }
        captures = [r['capture_ratio'] for r in subset if r['capture_ratio'] is not None]
        stats['avg_capture_ratio'] = round(_mean(captures), 3) if captures else None

        for horizon in horizons:
            key = str(horizon)
            seen = [r['post_exit'][key] for r in subset if r['post_exit'].get(key)]
            if not seen:
                stats[f'post_{key}'] = None
                continue
            # The shakeout rate is computed only over trades the question
            # applies to — losing exits. Including winners, where it is
            # trivially satisfied, would report 100% and mean nothing.
            askable = [s for s in seen if s['reached_entry'] is not None]
            stats[f'post_{key}'] = {
                'samples': len(seen),
                'avg_move': round(_mean([s['move'] for s in seen]), 5),
                'shakeout_samples': len(askable),
                'reached_entry_pct': (round(
                    len([s for s in askable if s['reached_entry']]) / len(askable) * 100, 1)
                    if askable else None),
                'continued_pct': round(
                    len([s for s in seen if s['move'] > 0]) / len(seen) * 100, 1),
            }
        askable = [r for r in subset if r['shakeout_applicable']]
        returns = [r['returned_to_entry_sec'] for r in askable
                   if r['returned_to_entry_sec'] is not None]
        stats['shakeout_samples'] = len(askable)
        stats['returned_to_entry_pct'] = (round(len(returns) / len(askable) * 100, 1)
                                          if askable else None)
        stats['median_return_sec'] = (sorted(returns)[len(returns) // 2]
                                      if returns else None)
        return stats

    by_reason = {}
    for row in rows:
        by_reason.setdefault(row.get('exit_reason') or 'unknown', []).append(row)

    return {
        'overall': bucket(rows),
        'by_exit_reason': {name: bucket(subset)
                           for name, subset in sorted(by_reason.items())},
        'horizons': list(horizons),
        'min_reliable': MIN_RELIABLE,
    }


def required_window(trades, horizons=DEFAULT_HORIZONS):
    """
    The bar window that covers these trades plus their longest horizon.

    Fetching one range per symbol beats one query per trade by two orders of
    magnitude on a few hundred trades.
    """
    closed = [t for t in (trades or [])
              if not t.get('open') and t.get('opened_utc') and t.get('closed_utc')]
    if not closed:
        return None

    by_symbol = {}
    for trade in closed:
        symbol = trade.get('symbol')
        if not symbol:
            continue
        low, high = by_symbol.get(symbol, (trade['opened_utc'], trade['closed_utc']))
        by_symbol[symbol] = (min(low, trade['opened_utc']),
                             max(high, trade['closed_utc']))
    pad = max(horizons)
    return {symbol: (start, end + pad) for symbol, (start, end) in by_symbol.items()}
