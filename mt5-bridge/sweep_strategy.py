"""
Strategy 1: liquidity sweep, multi-timeframe.

A sweep is price pushing briefly past a prior swing high or low — taking the
stops clustered there — and then failing to hold, closing back inside the
range. The premise is that the move existed to fill someone else's order, so
price now travels toward the *opposite* pool.

The structure is two-timeframe, which the earlier single-timeframe engine could
not express:

    sweep timeframes   4H, 1H, 30M, 15M   where the level lives and is taken
    entry timeframes   3M, 1M             where the trigger is looked for

**Everything here turns on one clock rule.** A 15M bar stamped 10:00 is not
finished until 10:15, so an entry bar at 10:05 cannot know it. `knowable_utc`
on every sweep is the close time of the bar that produced it, and entry bars
are filtered against it. Reading a higher-timeframe bar before it closes is the
multi-timeframe form of lookahead, it is invisible in the results, and it makes
a losing strategy backtest beautifully.

Units are prices, never pips. Gold's pip is 0.10, and an earlier config had it
at 0.01 — every buffer was wrong by ten and looked fine. Nothing here is
denominated in pips.
"""
from detectors import swings

# Bar periods in seconds. Needed to know when a higher-timeframe bar closed,
# which cannot be inferred from the bars themselves.
PERIODS = {'1': 60, '3': 180, '5': 300, '15': 900, '30': 1800,
           '60': 3600, '240': 14400}

SWEEP_TIMEFRAMES = ('240', '60', '30', '15')
ENTRY_TIMEFRAMES = ('3', '1')
TRIGGERS = ('confirmation', 'mss', 'tap_and_go')

# Deferred, deliberately. It is a discretionary read ("as per market
# behaviour") and there is no rule for it yet, so it is listed here to keep the
# UI honest about what exists rather than silently absent.
UNAVAILABLE_TRIGGERS = {'tap_and_go': 'needs a rule before it can be automated'}

DEFAULT_CONFIG = {
    'name': 'liquidity-sweep',
    'symbol': 'GOLD.i#',
    'sweep_timeframes': list(SWEEP_TIMEFRAMES),
    'entry_timeframe': '3',
    'entry': {
        # Confirmation is the default; MSS is the fallback when no
        # confirmation candle appears in time.
        'trigger': 'confirmation',
        'fallback': 'mss',
        # How long a sweep stays tradeable. Past this the move it was supposed
        # to start has already happened without us.
        'wait_bars': 12,
        'swing_left': 2,
        'swing_right': 2,
        # A sweep level may be traded twice. The third time it is not a
        # liquidity pool any more — whatever was resting there is gone.
        'max_uses': 2,
    },
    'stop': {
        # Past the tip of the sweep wick, in price. 0.25 is 2.5 gold pips.
        'buffer_price': 0.25,
    },
    'target': {
        # Opposite structural swing on the sweep's own timeframe, but never
        # a trade worse than this. A sub-2R target does not pay for the spread.
        'mode': 'structural',
        'min_r': 2.0,
        'fixed_r': None,          # set to a number to override structure
    },
    'manage': {
        'partial_pct': 50,
        'partial_at_r': 1.0,
        # Breakeven-plus: entry + live spread + buffer, so the stop clears the
        # cost of the round trip instead of sitting inside it.
        'trail_at_r': 1.0,
        'trail_buffer_price': 0.10,
    },
    'size': {
        'mode': 'risk',           # 'risk' or 'fixed'
        'risk_pct': 1.0,
        'max_risk_pct': 2.5,
        'fixed_lot': 0.03,
        'max_lot': 0.05,
    },
}


def period(timeframe):
    return PERIODS.get(str(timeframe))


def close_time(bar, timeframe):
    """When this bar finished — the instant its high, low and close stop moving."""
    return bar['time_utc'] + period(timeframe)


def sweeps_on(bars, timeframe, left=2, right=2, lookback_bars=50):
    """
    Sweeps found on one timeframe.

    A bullish sweep takes out a prior swing low and closes back above it: the
    stops below were taken and price refused to stay there. Closing *beyond*
    the level instead is continuation, not a sweep, and is not a signal.

    Only swings already confirmed before the sweeping bar are eligible — a
    swing needs `right` bars after it to exist at all.
    """
    points = swings(bars, left=left, right=right)
    found = []

    for i, bar in enumerate(bars):
        for point in points:
            if point['knowable_at'] >= i or i - point['index'] > lookback_bars:
                continue

            if point['kind'] == 'low' and bar['low'] < point['price'] < bar['close']:
                direction, wick = 'long', bar['low']
            elif point['kind'] == 'high' and bar['high'] > point['price'] > bar['close']:
                direction, wick = 'short', bar['high']
            else:
                continue

            found.append({
                'timeframe': str(timeframe),
                'direction': direction,
                'swept_level': point['price'],
                # The extreme of the sweep, which is where the stop goes.
                'wick': wick,
                'index': i,
                'time_utc': bar['time_utc'],
                # The bar is not finished until its period elapses, and
                # nothing on a lower timeframe may act before then.
                'knowable_utc': close_time(bar, timeframe),
                'level_index': point['index'],
            })
    return found


def find_sweeps(bars_by_tf, config=None):
    """Every sweep across the configured timeframes, oldest first."""
    cfg = merged(config)
    entry = cfg['entry']
    out = []
    for timeframe in cfg['sweep_timeframes']:
        bars = bars_by_tf.get(str(timeframe))
        if not bars or not period(timeframe):
            continue
        out.extend(sweeps_on(bars, timeframe,
                             left=entry['swing_left'], right=entry['swing_right']))
    out.sort(key=lambda s: (s['knowable_utc'], s['timeframe']))
    return out


def _confirmation(bar, direction):
    """
    A confirmation candle: closes in the trade's direction, decisively.

    "Decisively" is the close sitting in the outer third of the bar's own
    range. A bar that closes green by a hair after wicking both ways has not
    confirmed anything, and counting it is how a trigger fires on noise.
    """
    span = bar['high'] - bar['low']
    if span <= 0:
        return False
    position = (bar['close'] - bar['low']) / span
    if direction == 'long':
        return bar['close'] > bar['open'] and position >= 2 / 3
    return bar['close'] < bar['open'] and position <= 1 / 3


def _mss_level(bars, upto_index, direction, left=2, right=2):
    """
    The structural level a market-structure shift has to break.

    For a long, the most recent confirmed swing high at or before the sweep.
    Breaking it says the move that caused the sweep has been reversed, rather
    than merely paused.
    """
    kind = 'high' if direction == 'long' else 'low'
    candidates = [p for p in swings(bars, left=left, right=right)
                  if p['kind'] == kind and p['knowable_at'] <= upto_index]
    return candidates[-1]['price'] if candidates else None


def _trigger_index(bars, start, direction, cfg):
    """
    The bar that triggers entry, and which rule fired.

    Confirmation is checked first on every bar; MSS is the fallback and is only
    consulted once the confirmation window has been given its chance on that
    bar. Both are bounded by `wait_bars` — a sweep whose move never started is
    not a setup that got slower, it is a setup that failed.
    """
    entry = cfg['entry']
    window = range(start, min(len(bars), start + entry['wait_bars']))
    mss_level = (_mss_level(bars, start, direction,
                            entry['swing_left'], entry['swing_right'])
                 if entry.get('fallback') == 'mss' else None)

    for i in window:
        bar = bars[i]
        if entry['trigger'] == 'confirmation' and _confirmation(bar, direction):
            return i, 'confirmation'
        if mss_level is not None:
            broke = (bar['close'] > mss_level if direction == 'long'
                     else bar['close'] < mss_level)
            if broke:
                return i, 'mss'
    return None, None


def _structural_target(bars, index, direction, entry_price, min_distance=0.0):
    """
    The opposite pool: the nearest confirmed swing at least `min_distance` away.

    The strategy's premise is that price is travelling toward liquidity on the
    other side, so the target is a place rather than a ratio. But "nearest
    swing" alone picks up noise — a one-tick high left by a quiet stretch is
    not a liquidity pool, and letting one sit 1.9R away would cancel a setup
    whose real pool was 4R out. Candidates closer than `min_distance` are
    stepped over rather than allowed to veto the trade.

    Only swings confirmed at or before `index` count. Aiming at a swing that
    forms after the sweep is lookahead wearing a target.
    """
    kind = 'high' if direction == 'long' else 'low'
    long_side = direction == 'long'
    levels = [p['price'] for p in swings(bars)
              if p['kind'] == kind and p['knowable_at'] <= index
              and ((p['price'] > entry_price) if long_side
                   else (p['price'] < entry_price))
              and abs(p['price'] - entry_price) >= min_distance]
    if not levels:
        return None
    return min(levels) if long_side else max(levels)


def merged(config=None):
    """A full config: supplied values over defaults, nested."""
    import copy
    out = copy.deepcopy(DEFAULT_CONFIG)

    def merge(base, extra):
        for key, value in (extra or {}).items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                merge(base[key], value)
            else:
                base[key] = value

    merge(out, config)
    return out


def validate(config=None):
    """Every problem with a config, as sentences. All of them, not the first."""
    cfg = merged(config)
    problems = []
    entry = cfg['entry']

    for name in cfg['sweep_timeframes']:
        if str(name) not in PERIODS:
            problems.append(f'Unknown sweep timeframe {name!r}. '
                            f'Known: {", ".join(PERIODS)}')
    if not cfg['sweep_timeframes']:
        problems.append('No sweep timeframes selected, so nothing can trigger.')

    if str(cfg['entry_timeframe']) not in PERIODS:
        problems.append(f'Unknown entry timeframe {cfg["entry_timeframe"]!r}')
    elif any(period(cfg['entry_timeframe']) > period(t)
             for t in cfg['sweep_timeframes'] if str(t) in PERIODS):
        problems.append('The entry timeframe is higher than a sweep timeframe. '
                        'Entries are meant to be found inside the sweep bar, '
                        'not around it.')

    for name, key in (('trigger', 'trigger'), ('fallback', 'fallback')):
        value = entry.get(key)
        if value in (None, ''):
            continue
        if value not in TRIGGERS:
            problems.append(f'Unknown {name} {value!r}. Supported: {", ".join(TRIGGERS)}')
        elif value in UNAVAILABLE_TRIGGERS:
            problems.append(f'{value!r} is not available yet — '
                            f'{UNAVAILABLE_TRIGGERS[value]}')

    if entry['wait_bars'] < 1:
        problems.append('wait_bars must be at least 1.')
    if entry['max_uses'] < 1:
        problems.append('max_uses must be at least 1, or no level could be traded.')

    if cfg['stop']['buffer_price'] < 0:
        problems.append('stop.buffer_price cannot be negative — the stop would '
                        'sit inside the wick it is meant to clear.')

    target = cfg['target']
    if target['mode'] not in ('structural', 'fixed'):
        problems.append(f'Unknown target mode {target["mode"]!r}')
    if target['mode'] == 'fixed' and not target.get('fixed_r'):
        problems.append('target.mode is "fixed" but fixed_r is not set.')
    if target['min_r'] <= 0:
        problems.append('target.min_r must be positive.')

    size = cfg['size']
    if size['mode'] not in ('risk', 'fixed'):
        problems.append(f'Unknown size mode {size["mode"]!r}')
    if size['risk_pct'] > size['max_risk_pct']:
        problems.append(f'risk_pct ({size["risk_pct"]}) is above max_risk_pct '
                        f'({size["max_risk_pct"]}), so every trade is refused.')
    if size['fixed_lot'] > size['max_lot']:
        problems.append(f'fixed_lot ({size["fixed_lot"]}) is above max_lot '
                        f'({size["max_lot"]}).')

    manage = cfg['manage']
    if not 0 <= manage['partial_pct'] <= 100:
        problems.append('partial_pct must be between 0 and 100.')

    return problems


def find_setups(bars_by_tf, config=None):
    """
    Every tradeable sweep, with its entry, stop and target.

    The pipeline: find sweeps on the higher timeframes, wait for each to be
    knowable, look for a trigger on the entry timeframe, then price the trade.
    A sweep that never triggers, whose level is used up, or whose target is
    closer than `min_r` is returned as a *rejected* row with its reason rather
    than dropped — the rejections are how you tell a strategy that found
    nothing from one whose filter is too tight.
    """
    cfg = merged(config)
    entry_tf = str(cfg['entry_timeframe'])
    entry_bars = bars_by_tf.get(entry_tf) or []
    if not entry_bars:
        return {'setups': [], 'rejected': [],
                'note': f'no {entry_tf}-minute bars supplied'}

    times = [b['time_utc'] for b in entry_bars]
    sweeps = find_sweeps(bars_by_tf, cfg)
    uses = {}
    setups, rejected = [], []

    for sweep in sweeps:
        key = (sweep['timeframe'], round(sweep['swept_level'], 5), sweep['direction'])
        if uses.get(key, 0) >= cfg['entry']['max_uses']:
            rejected.append({**_ref(sweep),
                             'reason': f'level already traded '
                                       f'{cfg["entry"]["max_uses"]} times'})
            continue

        # The first entry bar that opens at or after the sweep bar closed.
        start = next((i for i, t in enumerate(times)
                      if t >= sweep['knowable_utc']), None)
        if start is None:
            rejected.append({**_ref(sweep), 'reason': 'no entry bars after the sweep'})
            continue

        index, rule = _trigger_index(entry_bars, start, sweep['direction'], cfg)
        if index is None:
            rejected.append({**_ref(sweep),
                             'reason': f'no trigger within '
                                       f'{cfg["entry"]["wait_bars"]} bars'})
            continue
        if index + 1 >= len(entry_bars):
            rejected.append({**_ref(sweep), 'reason': 'no bar after the trigger'})
            continue

        entry_price = entry_bars[index + 1]['open']
        buffer = cfg['stop']['buffer_price']
        long_side = sweep['direction'] == 'long'
        stop = (sweep['wick'] - buffer) if long_side else (sweep['wick'] + buffer)
        risk = abs(entry_price - stop)
        if risk <= 0:
            rejected.append({**_ref(sweep), 'reason': 'entry is already past the stop'})
            continue

        target, target_kind = _pick_target(bars_by_tf, sweep, cfg, entry_price,
                                           risk, long_side)
        if target is None:
            rejected.append({**_ref(sweep),
                             'reason': f'no target at least '
                                       f'{cfg["target"]["min_r"]}R away'})
            continue

        uses[key] = uses.get(key, 0) + 1
        setups.append({
            'symbol': cfg['symbol'],
            'direction': sweep['direction'],
            'sweep_timeframe': sweep['timeframe'],
            'entry_timeframe': entry_tf,
            'swept_level': sweep['swept_level'],
            'sweep_wick': sweep['wick'],
            'sweep_time_utc': sweep['time_utc'],
            'trigger': rule,
            'trigger_index': index,
            'entry_index': index + 1,
            'entry_time_utc': entry_bars[index + 1]['time_utc'],
            'entry_price': round(entry_price, 5),
            'stop': round(stop, 5),
            'target': round(target, 5),
            'target_kind': target_kind,
            'r_distance': round(risk, 5),
            'r_multiple_available': round(abs(target - entry_price) / risk, 2),
            'level_use': uses[key],
        })

    return {'setups': setups, 'rejected': rejected,
            'sweeps_found': len(sweeps),
            'reject_reasons': _count(r['reason'] for r in rejected)}


def _pick_target(bars_by_tf, sweep, cfg, entry_price, risk, long_side):
    """Structural target if there is one far enough away, else nothing."""
    target_cfg = cfg['target']
    floor = risk * target_cfg['min_r']

    if target_cfg['mode'] == 'fixed':
        distance = risk * (target_cfg.get('fixed_r') or target_cfg['min_r'])
        return (entry_price + distance if long_side else entry_price - distance), 'fixed'

    level_bars = bars_by_tf.get(sweep['timeframe']) or []
    structural = _structural_target(level_bars, sweep['index'],
                                    sweep['direction'], entry_price,
                                    min_distance=floor)
    if structural is None:
        # No pool at least min_r away. Manufacturing one at exactly min_r would
        # invent a target where structure offers none, so the setup is skipped.
        return None, None
    return structural, 'structural'


def _ref(sweep):
    return {'timeframe': sweep['timeframe'], 'direction': sweep['direction'],
            'swept_level': sweep['swept_level'],
            'sweep_time_utc': sweep['time_utc']}


def _count(values):
    out = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
