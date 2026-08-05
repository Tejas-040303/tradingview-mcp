"""
Level detectors: FVG, liquidity sweep, order block, fibonacci.

Everything here carries a `knowable_at` index — the bar by which the pattern
could actually have been seen — and it is never the bar where the pattern
started. A swing high found with two bars either side is not confirmed until
those two bars exist; an FVG is not visible until its third candle closes.

That distinction is the whole difference between a backtest and a fantasy. Using
a swing at the bar it occurred means the strategy trades on information from the
future, and the equity curve that produces is beautiful and worthless. Every
consumer here filters on `knowable_at <= current_bar`, and there are tests that
fail if it does not.

Pure functions over the bar shape mt5_client emits: `time_utc`, open, high,
low, close.
"""

# Bars either side of a candidate swing. Two is conventional for intraday and
# costs two bars of delay before the level can be used.
SWING_LEFT = 2
SWING_RIGHT = 2


def swings(bars, left=SWING_LEFT, right=SWING_RIGHT):
    """
    Fractal swing highs and lows.

    A swing high is a bar whose high exceeds every bar within `left` before and
    `right` after. It becomes knowable `right` bars later, which is recorded
    rather than assumed away.
    """
    found = []
    for i in range(left, len(bars) - right):
        window = bars[i - left:i + right + 1]
        high, low = bars[i]['high'], bars[i]['low']

        if all(high >= b['high'] for b in window) and \
                any(high > b['high'] for b in window if b is not bars[i]):
            found.append({'kind': 'high', 'index': i, 'price': high,
                          'time_utc': bars[i]['time_utc'],
                          'knowable_at': i + right})
        if all(low <= b['low'] for b in window) and \
                any(low < b['low'] for b in window if b is not bars[i]):
            found.append({'kind': 'low', 'index': i, 'price': low,
                          'time_utc': bars[i]['time_utc'],
                          'knowable_at': i + right})
    return found


def fair_value_gaps(bars):
    """
    Three-candle imbalance: the middle candle moves far enough that candle 1 and
    candle 3 do not overlap.

    Bullish when candle 1's high sits below candle 3's low — price left a gap on
    the way up. The zone is that untraded band, and it is knowable only once
    candle 3 has closed.
    """
    out = []
    for i in range(1, len(bars) - 1):
        first, third = bars[i - 1], bars[i + 1]

        if first['high'] < third['low']:
            out.append({'kind': 'fvg', 'direction': 'long',
                        'low': first['high'], 'high': third['low'],
                        'index': i, 'knowable_at': i + 1,
                        'time_utc': bars[i]['time_utc']})
        elif first['low'] > third['high']:
            out.append({'kind': 'fvg', 'direction': 'short',
                        'low': third['high'], 'high': first['low'],
                        'index': i, 'knowable_at': i + 1,
                        'time_utc': bars[i]['time_utc']})
    return out


def liquidity_sweeps(bars, swing_points=None, lookback_bars=50):
    """
    Price takes out a prior swing extreme, then closes back inside it.

    The close is what makes it a sweep rather than a break: exceeding the level
    and holding is continuation, exceeding it and rejecting is the liquidity
    grab. A sweep of a low is a long signal, since the stops below have been
    taken and price refused to stay there.

    Only swings already confirmed before the sweeping bar are eligible.
    """
    points = swing_points if swing_points is not None else swings(bars)
    out = []

    for i, bar in enumerate(bars):
        for point in points:
            if point['knowable_at'] >= i or i - point['index'] > lookback_bars:
                continue
            if point['kind'] == 'low' and bar['low'] < point['price'] < bar['close']:
                out.append({'kind': 'liquidity_sweep', 'direction': 'long',
                            'low': bar['low'], 'high': point['price'],
                            'index': i, 'knowable_at': i,
                            'swept': point['price'],
                            'time_utc': bar['time_utc']})
            elif point['kind'] == 'high' and bar['high'] > point['price'] > bar['close']:
                out.append({'kind': 'liquidity_sweep', 'direction': 'short',
                            'low': point['price'], 'high': bar['high'],
                            'index': i, 'knowable_at': i,
                            'swept': point['price'],
                            'time_utc': bar['time_utc']})
    return out


def order_blocks(bars, swing_points=None, lookback_bars=30):
    """
    The last opposing candle before the move that broke structure.

    A bullish order block is the final down-candle before price closed above a
    prior swing high. The break is what makes it an order block rather than an
    ordinary candle, so it is knowable at the breaking bar — not at the candle
    itself, which is earlier and would be lookahead.
    """
    points = swing_points if swing_points is not None else swings(bars)
    out = []

    for i, bar in enumerate(bars):
        for point in points:
            if point['knowable_at'] >= i or i - point['index'] > lookback_bars:
                continue

            broke_up = point['kind'] == 'high' and bar['close'] > point['price']
            broke_down = point['kind'] == 'low' and bar['close'] < point['price']
            if not (broke_up or broke_down):
                continue

            # Walk back for the last candle opposing the break.
            for j in range(i - 1, max(-1, i - lookback_bars) - 1, -1):
                candle = bars[j]
                bearish = candle['close'] < candle['open']
                if (broke_up and bearish) or (broke_down and not bearish):
                    out.append({
                        'kind': 'order_block',
                        'direction': 'long' if broke_up else 'short',
                        'low': candle['low'], 'high': candle['high'],
                        'index': j, 'knowable_at': i,
                        'time_utc': candle['time_utc'],
                    })
                    break
            break   # one order block per breaking bar
    return out


def fib_zones(bars, swing_points=None, levels=(0.618, 0.705)):
    """
    Retracement bands on the most recent confirmed impulse leg.

    A leg is a confirmed swing low followed by a confirmed swing high (or the
    reverse). The zone spans the requested retracement levels, and is knowable
    once the *later* of the two swings is confirmed.
    """
    points = sorted(swing_points if swing_points is not None else swings(bars),
                    key=lambda p: p['index'])
    out = []

    for first, second in zip(points, points[1:]):
        if first['kind'] == second['kind']:
            continue
        low = min(first['price'], second['price'])
        high = max(first['price'], second['price'])
        span = high - low
        if span <= 0:
            continue

        # A leg up retraces downward, so the band sits below the high.
        upward = second['kind'] == 'high'
        prices = [high - span * level if upward else low + span * level
                  for level in levels]
        out.append({
            'kind': 'fib', 'direction': 'long' if upward else 'short',
            'low': min(prices), 'high': max(prices),
            'index': second['index'],
            'knowable_at': max(first['knowable_at'], second['knowable_at']),
            'leg': {'low': low, 'high': high},
            'time_utc': bars[second['index']]['time_utc'],
        })
    return out


def _touching(zone, bar):
    """
    Did this bar trade *inside* the zone?

    Strict, and it has to be. An FVG's boundary is by construction the third
    candle's own low, so an inclusive test reports every gap as touched at the
    instant it forms — the setup fires on its own formation bar instead of on
    the return to it, which is the entire premise. Requiring penetration also
    keeps a bar that merely grazes a boundary from counting as a retest.
    """
    return bar['low'] < zone['high'] and bar['high'] > zone['low']


def active_zones(zones, index, max_age_bars=None):
    """
    Zones that could be acted on at `index`.

    Knowable by now, and not older than `max_age_bars`. An FVG from three days
    ago is not the setup being traded, and letting one count is how a detector
    quietly starts firing on stale levels.
    """
    out = []
    for zone in zones:
        if zone['knowable_at'] > index:
            continue
        if max_age_bars is not None and index - zone['index'] > max_age_bars:
            continue
        out.append(zone)
    return out


def confirmation(bar, zone, kind='close_beyond'):
    """
    Did this bar confirm the setup?

    close_beyond  closes past the zone in the trade's direction, with the close
                  in the outer third of its own range — a decisive close rather
                  than a bar that merely ended on the right side
    engulfing     body covers the previous bar's body
    rejection     long wick into the zone, body closing away from it
    """
    span = bar['high'] - bar['low']
    if span <= 0:
        return False
    long_side = zone['direction'] == 'long'

    if kind == 'close_beyond':
        beyond = bar['close'] > zone['high'] if long_side else bar['close'] < zone['low']
        position = (bar['close'] - bar['low']) / span
        decisive = position >= (2 / 3) if long_side else position <= (1 / 3)
        return beyond and decisive

    if kind == 'engulfing':
        body_low, body_high = min(bar['open'], bar['close']), max(bar['open'], bar['close'])
        covered = body_low <= zone['low'] and body_high >= zone['high']
        right_way = bar['close'] > bar['open'] if long_side else bar['close'] < bar['open']
        return covered and right_way

    if kind == 'rejection':
        body_low, body_high = min(bar['open'], bar['close']), max(bar['open'], bar['close'])
        wick = (body_low - bar['low']) if long_side else (bar['high'] - body_high)
        right_way = bar['close'] > bar['open'] if long_side else bar['close'] < bar['open']
        return right_way and wick >= span / 2

    return False


def detect(bars, config):
    """
    Every zone each enabled detector finds, keyed by condition name.

    Computed once for the whole series rather than per bar; `active_zones`
    then narrows to what was knowable at any given point.
    """
    conditions = (config or {}).get('entry', {}).get('conditions', {})
    points = swings(bars)
    out = {}

    if conditions.get('fvg', {}).get('on'):
        out['fvg'] = fair_value_gaps(bars)
    if conditions.get('liquidity_sweep', {}).get('on'):
        out['liquidity_sweep'] = liquidity_sweeps(
            bars, points,
            lookback_bars=conditions['liquidity_sweep'].get('lookback_bars', 50))
    if conditions.get('order_block', {}).get('on'):
        out['order_block'] = order_blocks(bars, points)
    if conditions.get('fib', {}).get('on'):
        out['fib'] = fib_zones(bars, points,
                               levels=tuple(conditions['fib'].get('levels', (0.618, 0.705))))
    return out


def present_at(zones_by_kind, bars, index, config):
    """
    Which conditions are satisfied at this bar, and on which zones.

    A condition counts when a knowable, unexpired zone of its kind is being
    touched by this bar. Direction has to agree across them — a bullish FVG and
    a bearish sweep are not confluence, they are a disagreement.
    """
    conditions = (config or {}).get('entry', {}).get('conditions', {})
    bar = bars[index]
    hits = {}

    for kind, zones in (zones_by_kind or {}).items():
        settings = conditions.get(kind, {})
        for zone in active_zones(zones, index, settings.get('max_age_bars')):
            if _touching(zone, bar):
                hits.setdefault(zone['direction'], {}).setdefault(kind, zone)

    return hits
