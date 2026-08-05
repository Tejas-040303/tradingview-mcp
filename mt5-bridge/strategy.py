"""
A strategy as data, not code.

Every entry condition is a toggle, every threshold a number. The same dict
drives the backtest, the paper run and eventually the live bot, and it is what
the dashboard will edit — so a belief about the setup ("FVG should be required")
becomes a field that can be swept rather than an opinion baked into a branch.

Position sizing lives here too, because it is where the account this was built
for actually failed. Gold's minimum lot is 0.01, which on a 100-ounce contract
risks one dollar per dollar of stop distance. Against a median stop of 2.65 that
is $2.65 — the smallest risk the instrument can express. No `risk_pct` can go
below it, so a small balance does not get "1% risk", it gets whatever the
minimum lot happens to cost. Sizing here rounds down, refuses rather than
over-risking, and says why.

Pure functions: no MetaTrader5, no filesystem, no network.
"""
import copy

# Contract specifications. Wrong values here produce a backtest that is
# internally consistent and completely unachievable, so they are explicit per
# symbol rather than inferred.
#
#   contract_size  price-unit value of one lot, in account currency
#   pip            smallest quoted increment, used for buffers
SYMBOL_SPECS = {
    'GOLD.i#':  {'pip': 0.01, 'contract_size': 100.0, 'min_lot': 0.01, 'lot_step': 0.01},
    'BTCUSD#':  {'pip': 0.01, 'contract_size': 1.0, 'min_lot': 0.01, 'lot_step': 0.01},
    'OILCash#': {'pip': 0.01, 'contract_size': 100.0, 'min_lot': 0.01, 'lot_step': 0.01},
}

DEFAULT_SPEC = {'pip': 0.01, 'contract_size': 100.0, 'min_lot': 0.01, 'lot_step': 0.01}

CONDITIONS = ('fvg', 'liquidity_sweep', 'order_block', 'fib')
MODES = ('any', 'all', 'at_least')
CONFIRMATIONS = ('close_beyond', 'engulfing', 'rejection')

DEFAULT_CONFIG = {
    'name': 'untitled',
    'version': 1,
    'symbols': ['GOLD.i#'],
    'entry': {
        'conditions': {
            'fvg': {'on': True, 'required': False, 'max_age_bars': 20},
            'liquidity_sweep': {'on': True, 'required': False, 'lookback_bars': 50},
            'order_block': {'on': False, 'required': False, 'max_age_bars': 30},
            'fib': {'on': False, 'required': False, 'levels': [0.618, 0.705]},
        },
        # 'any' matches how this is traded today — each level is its own setup.
        # 'all' is the confluence reading. Both are built so the sweep can
        # settle which is better rather than the choice being assumed.
        'mode': 'any',
        'min_conditions': 2,
        'confirmation': {'timeframe': 5, 'type': 'close_beyond'},
    },
    'stop': {'anchor': 'confirmation_candle', 'buffer_pips': 7},
    'size': {
        'risk_pct': 1.0,
        # Refuse a trade whose minimum lot would risk more than this share of
        # the balance. Without it a small account silently takes 16% positions,
        # which is what happened.
        'max_risk_pct': 2.0,
    },
    'manage': {
        'partial_pct': 50,
        'partial_at_r': 1.0,
        # The parameter this whole exercise exists to test.
        'trail_to_be_at_r': 1.0,
    },
    'target': {'r': 2.0},
}


def spec_for(symbol):
    return SYMBOL_SPECS.get(symbol, DEFAULT_SPEC)


def merged(config=None):
    """A full config: supplied values over defaults, nested."""
    out = copy.deepcopy(DEFAULT_CONFIG)

    def merge(base, extra):
        for key, value in (extra or {}).items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                merge(base[key], value)
            else:
                base[key] = value

    merge(out, config)
    return out


def validate(config):
    """
    Every problem with a config, as a list of sentences.

    Returns all of them rather than raising on the first: a dashboard form
    should be able to show every invalid field at once.
    """
    cfg = merged(config)
    problems = []
    entry = cfg['entry']

    conditions = entry['conditions']
    for name in conditions:
        if name not in CONDITIONS:
            problems.append(f'Unknown entry condition {name!r}. '
                            f'Supported: {", ".join(CONDITIONS)}')

    enabled = [n for n, c in conditions.items()
               if n in CONDITIONS and c.get('on')]
    if not enabled:
        problems.append('No entry conditions are switched on, so nothing can '
                        'ever trigger a trade.')

    if entry['mode'] not in MODES:
        problems.append(f'Unknown mode {entry["mode"]!r}. Supported: {", ".join(MODES)}')

    if entry['mode'] == 'at_least':
        need = entry.get('min_conditions', 0)
        if need < 1:
            problems.append('min_conditions must be at least 1 in "at_least" mode.')
        elif need > len(enabled):
            problems.append(
                f'min_conditions is {need} but only {len(enabled)} condition(s) '
                f'are on, so the strategy can never trigger.')

    if entry['confirmation']['type'] not in CONFIRMATIONS:
        problems.append(f'Unknown confirmation type '
                        f'{entry["confirmation"]["type"]!r}. '
                        f'Supported: {", ".join(CONFIRMATIONS)}')

    if cfg['size']['risk_pct'] <= 0:
        problems.append('risk_pct must be greater than zero.')
    if cfg['size']['risk_pct'] > cfg['size']['max_risk_pct']:
        problems.append(
            f'risk_pct ({cfg["size"]["risk_pct"]}) is above max_risk_pct '
            f'({cfg["size"]["max_risk_pct"]}), so every trade would be refused.')

    if cfg['target']['r'] <= 0:
        problems.append('target.r must be greater than zero.')

    manage = cfg['manage']
    if not 0 <= manage['partial_pct'] <= 100:
        problems.append('partial_pct must be between 0 and 100.')
    if manage['trail_to_be_at_r'] is not None and manage['trail_to_be_at_r'] <= 0:
        problems.append('trail_to_be_at_r must be positive, or null for no trail.')
    if manage['trail_to_be_at_r'] and manage['trail_to_be_at_r'] >= cfg['target']['r']:
        problems.append(
            f'trail_to_be_at_r ({manage["trail_to_be_at_r"]}) is at or beyond the '
            f'target ({cfg["target"]["r"]}), so breakeven would never be reached '
            f'before the trade closed anyway.')

    if not cfg['symbols']:
        problems.append('No symbols selected.')

    return problems


def conditions_met(present, config):
    """
    Do the detected conditions satisfy this config's entry rule?

    `present` is the set of condition names found at this bar. A condition
    marked `required` must appear regardless of mode — that is what makes
    "sweep is mandatory, FVG is a bonus" expressible.
    """
    cfg = merged(config)
    entry = cfg['entry']
    conditions = entry['conditions']

    enabled = {n for n, c in conditions.items() if n in CONDITIONS and c.get('on')}
    required = {n for n in enabled if conditions[n].get('required')}
    found = set(present) & enabled

    if not required <= found:
        return False
    if not found:
        return False

    if entry['mode'] == 'all':
        return found == enabled
    if entry['mode'] == 'at_least':
        return len(found) >= entry['min_conditions']
    return True   # 'any' — one enabled condition is enough


def position_size(balance, stop_distance, symbol, config=None):
    """
    Lot size for a given stop distance, or a refusal with a reason.

    Three rules, each learned from the account this was written for:

      * round the lot **down** to the broker's step, never up
      * if the minimum lot risks more than max_risk_pct, refuse the trade —
        do not take it smaller, because there is nothing smaller
      * report the risk actually taken, which is rarely the risk requested

    A balance of 16.84 against a 2.65 stop gives a minimum-lot risk of 2.65,
    which is 15.7% of the account. That trade should not be taken, and this
    returns `False` with the arithmetic rather than a lot size.
    """
    cfg = merged(config)
    spec = spec_for(symbol)

    if stop_distance is None or stop_distance <= 0:
        return {'ok': False, 'lot': 0.0, 'reason': 'stop distance must be positive'}
    if balance is None or balance <= 0:
        return {'ok': False, 'lot': 0.0, 'reason': 'balance must be positive'}

    wanted = balance * cfg['size']['risk_pct'] / 100
    cap = balance * cfg['size']['max_risk_pct'] / 100
    per_lot = stop_distance * spec['contract_size']

    raw = wanted / per_lot
    steps = int(raw / spec['lot_step'])
    lot = round(steps * spec['lot_step'], 8)

    minimum_risk = spec['min_lot'] * per_lot
    if lot < spec['min_lot']:
        # The requested risk is below what one minimum lot costs.
        if minimum_risk > cap:
            return {
                'ok': False, 'lot': 0.0,
                'wanted_risk': round(wanted, 2),
                'minimum_risk': round(minimum_risk, 2),
                'minimum_risk_pct': round(minimum_risk / balance * 100, 2),
                'reason': (
                    f'the smallest tradeable lot ({spec["min_lot"]}) risks '
                    f'{minimum_risk:.2f} on this stop, which is '
                    f'{minimum_risk / balance * 100:.1f}% of a {balance:.2f} '
                    f'balance — above the {cfg["size"]["max_risk_pct"]}% cap'),
            }
        lot = spec['min_lot']

    risk = lot * per_lot
    return {
        'ok': True,
        'lot': lot,
        'risk': round(risk, 2),
        'risk_pct': round(risk / balance * 100, 3),
        'wanted_risk': round(wanted, 2),
        # Granularity means the realised risk almost never equals the request.
        # Surfacing it stops "1% risk" being believed when it is 1.6%.
        'rounded_from': round(raw, 4),
    }


def minimum_balance(stop_distance, symbol, risk_pct=1.0):
    """
    The balance this strategy needs before its own stop can be sized sanely.

    Below this the minimum lot is the only lot, and risk per trade is whatever
    the instrument decides rather than whatever was configured.
    """
    spec = spec_for(symbol)
    if not stop_distance or stop_distance <= 0 or risk_pct <= 0:
        return None
    minimum_risk = spec['min_lot'] * stop_distance * spec['contract_size']
    return round(minimum_risk / (risk_pct / 100), 2)
