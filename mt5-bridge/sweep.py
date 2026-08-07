"""
Parameter sweeps with walk-forward validation.

Running thirty configurations and reporting the best one is not research, it is
a lottery with the winning ticket announced afterwards. Thirty coin-flipping
strategies produce a best performer too, and it looks excellent.

So three things are always reported together:

  * **In-sample and out-of-sample side by side.** The split is chronological —
    the first 70% chooses, the last 30% judges. Shuffling bars would leak the
    future into the past and is never done.
  * **The median configuration**, not just the best. If the winner barely
    clears the median, the ranking is noise wearing a leaderboard.
  * **Rank correlation between the two halves.** If the ordering in-sample
    tells you nothing about the ordering out-of-sample, the sweep has measured
    randomness, and no amount of "the best config made 40%" changes that.
  * **The best-of-N noise floor.** Picking the largest of thirty averages
    scores well above zero even when nothing has an edge, and an earlier
    version of this module missed that: two random-walk seeds in five came
    back "trustworthy". The floor is measured from the winner's own trade
    spread, so a config that tops the table on fifteen volatile trades has to
    clear a far higher bar than one that does it on two hundred.

The question this exists for: does moving the stop to breakeven before 1R help
or hurt? `trail_to_be_at_r: None` is in the default axis as the control, and a
sweep without a control answers nothing.
"""
import copy
import json
import math

from simulate import find_setups, simulate
from strategy import merged, validate

# The axis this was built for. Trailing early is the suspected cause of the
# early exits, so it gets the most values; the other two are here because a
# trail interacts with both — a partial at 1R changes what a breakeven stop
# costs, and a 3R target changes how long the trade has to survive one.
DEFAULT_AXES = {
    'manage.trail_to_be_at_r': [0.5, 0.75, 1.0, 1.5, None],
    'manage.partial_pct': [0, 50, 100],
    'target.r': [2.0, 3.0],
}

# Below this many closed trades a configuration's numbers are reported but not
# ranked. A 100% win rate over two trades would otherwise top the table.
MIN_TRADES = 10


def _set(config, path, value):
    node = config
    parts = path.split('.')
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def expand(axes=None, base=None):
    """Every combination of the axes, as full configs."""
    axes = DEFAULT_AXES if axes is None else axes
    configs = [copy.deepcopy(base or {})]

    for path, values in axes.items():
        grown = []
        for config in configs:
            for value in values:
                variant = copy.deepcopy(config)
                _set(variant, path, value)
                grown.append(variant)
        configs = grown

    # A config the validator rejects would report zero trades and read as "this
    # setting does not work" rather than "this setting is nonsense".
    return [c for c in configs if not validate(c)]


def _label(config, axes):
    return {path: _get(config, path) for path in axes}


def _get(config, path):
    node = config
    for part in path.split('.'):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _spearman(a, b):
    """
    Rank correlation, stdlib only.

    None when there is nothing to correlate — fewer than three pairs, or one
    side with no variation at all. Returning 0.0 there would read as "no
    relationship measured" when the truth is "no measurement possible".
    """
    if len(a) < 3 or len(a) != len(b):
        return None

    def ranks(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            average = (i + j) / 2 + 1          # ties share the mean rank
            for k in range(i, j + 1):
                out[order[k]] = average
            i = j + 1
        return out

    ra, rb = ranks(a), ranks(b)
    n = len(ra)
    mean_a, mean_b = sum(ra) / n, sum(rb) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(ra, rb))
    var_a = sum((x - mean_a) ** 2 for x in ra)
    var_b = sum((y - mean_b) ** 2 for y in rb)
    if var_a <= 0 or var_b <= 0:
        return None
    return round(cov / (var_a * var_b) ** 0.5, 3)


def _noise_floor(row, configurations):
    """
    What the best of N configurations scores when none of them has an edge.

    Averaging R over `n` trades carries a standard error of sd/sqrt(n), and the
    largest of `N` such averages drawn from a zero-mean distribution sits
    around sd/sqrt(n) * sqrt(2 ln N). Reporting a winner below that is
    reporting the search, not the strategy.

    None when the spread cannot be measured, and the caller then makes no claim
    either way rather than treating an unmeasured floor as a passed test.

    **This is conservative, and knowingly so.** `sqrt(2 ln N)` assumes N
    independent tests, but a management sweep runs the same setups through
    different exits, so the configurations are heavily correlated and the
    effective N is well below the nominal one. The floor therefore sits above
    the true one and can reject a modest real edge. For a system that decides
    whether to risk money that is the right direction to be wrong in, and the
    number is reported so the margin can be judged rather than trusted.
    """
    trades, spread = row.get('trades') or 0, row.get('r_stdev')
    if not spread or trades < 2 or configurations < 2:
        return None
    standard_error = spread / math.sqrt(trades)
    return round(standard_error * math.sqrt(2 * math.log(configurations)), 4)


def _median(values):
    rows = sorted(values)
    if not rows:
        return None
    mid = len(rows) // 2
    return rows[mid] if len(rows) % 2 else (rows[mid - 1] + rows[mid]) / 2


def _setup_cache(bars):
    """
    Detected setups keyed by the entry half of a config.

    Sweeping management parameters leaves the entry signals identical across
    every configuration, and detection is the expensive part. Keyed rather than
    computed once, because an axis *can* vary entry conditions and silently
    reusing another config's signals would be a spectacular bug.
    """
    cache = {}

    def get(cfg):
        key = json.dumps(cfg['entry'], sort_keys=True, default=str)
        if key not in cache:
            cache[key] = find_setups(bars, cfg)
        return cache[key]

    return get


def _run(bars, symbol, config, balance, setups=None):
    summary = simulate(bars, symbol, config, balance=balance,
                       setups=setups)['summary']
    return {
        'trades': summary.get('closed', 0),
        'avg_r': summary.get('avg_r'),
        'r_stdev': summary.get('r_stdev'),
        'win_rate_pct': summary.get('win_rate_pct'),
        'expectancy': summary.get('expectancy'),
        'net': summary.get('net'),
        'profit_factor': summary.get('profit_factor'),
    }


def sweep(bars, symbol, axes=None, base=None, split=0.7, balance=1000.0,
          min_trades=MIN_TRADES):
    """
    Every configuration run on both halves of the series.

    Ranked by out-of-sample average R, because that is the number the ranking
    is allowed to claim anything about. In-sample sits beside it so the gap is
    visible: a config that is excellent in the first half and mediocre in the
    second has been fitted, and the gap is the evidence.
    """
    axes = DEFAULT_AXES if axes is None else axes
    configs = expand(axes, base)
    if not bars or not configs:
        return {'success': True, 'rows': [], 'summary':
                {'note': 'nothing to sweep — no bars or no valid configurations'}}

    cut = int(len(bars) * split)
    in_bars, out_bars = bars[:cut], bars[cut:]
    if not in_bars or not out_bars:
        return {'success': False,
                'error': f'split {split} leaves one half empty for {len(bars)} bars'}

    in_setups, out_setups = _setup_cache(in_bars), _setup_cache(out_bars)

    rows = []
    for config in configs:
        full = merged(config)
        in_sample = _run(in_bars, symbol, config, balance, in_setups(full))
        out_sample = _run(out_bars, symbol, config, balance, out_setups(full))
        rows.append({
            'params': _label(config, axes),
            'in_sample': in_sample,
            'out_of_sample': out_sample,
            # Ranked only with enough closed trades to mean something.
            'ranked': (in_sample['trades'] >= min_trades
                       and out_sample['trades'] >= min_trades),
        })

    ranked = [r for r in rows if r['ranked'] and r['out_of_sample']['avg_r'] is not None]
    ranked.sort(key=lambda r: r['out_of_sample']['avg_r'], reverse=True)
    rows.sort(key=lambda r: (not r['ranked'],
                             -(r['out_of_sample']['avg_r'] or -99)))

    return {
        'success': True,
        'symbol': symbol,
        'bars': len(bars),
        'split': {'in_sample_bars': len(in_bars), 'out_of_sample_bars': len(out_bars),
                  'in_sample_to': bars[cut - 1]['time_utc'],
                  'out_of_sample_from': bars[cut]['time_utc']},
        'configurations': len(configs),
        'rows': rows,
        'summary': _verdict(ranked, len(configs), min_trades),
    }


def _verdict(ranked, total, min_trades):
    """
    What the sweep is entitled to claim.

    Deliberately conservative. The failure mode being guarded against is a
    dashboard that shows a leaderboard, because a leaderboard always has a
    winner whether or not the differences are real.
    """
    if not ranked:
        return {'ranked': 0, 'of': total,
                'note': (f'No configuration produced {min_trades} closed trades in '
                         f'both halves, so nothing can be ranked. Widen the window '
                         f'or loosen the entry conditions before reading anything '
                         f'into this.')}

    in_rs = [r['in_sample']['avg_r'] for r in ranked]
    out_rs = [r['out_of_sample']['avg_r'] for r in ranked]
    correlation = _spearman(in_rs, out_rs)
    best = ranked[0]
    median_out = _median(out_rs)

    # An edge is only claimed when the winner beats what the best of N would
    # have scored on data with no edge at all, makes money out of sample, the
    # ordering survives the split, and it is clear of the middle of the pack.
    margin = (best['out_of_sample']['avg_r'] - median_out) if median_out is not None else None
    profitable = best['out_of_sample']['avg_r'] > 0
    floor = _noise_floor(best['out_of_sample'], len(ranked))
    beats_noise = floor is None or best['out_of_sample']['avg_r'] > floor
    trustworthy = (profitable and beats_noise
                   and correlation is not None and correlation >= 0.3
                   and margin is not None and margin >= 0.1)

    if correlation is None:
        reading = ('Too few ranked configurations to correlate the two halves — '
                   'the ordering below is not evidence of anything.')
    elif profitable and not beats_noise:
        reading = (f'The best configuration scores {best["out_of_sample"]["avg_r"]:+.2f} R, '
                   f'but picking the best of {len(ranked)} would score about '
                   f'{floor:+.2f} R on data with no edge in it at all. This result '
                   f'is inside that range — it is what searching thirty '
                   f'configurations looks like, not what an edge looks like.')
    elif not profitable:
        # The trap this check exists for. Management parameters shift the R
        # distribution in a consistent, mechanical way, so the ranking holds
        # across any split — including on random data, where an early version
        # of this verdict called a −0.01 R winner "worth acting on". Ordering
        # degrees of loss is not an edge.
        reading = (f'Every configuration loses out of sample — the best manages '
                   f'{best["out_of_sample"]["avg_r"]:+.2f} R. The ranking below '
                   f'orders degrees of loss, not degrees of profit, and a high '
                   f'rank correlation ({correlation}) does not change that: '
                   f'management parameters shift the R distribution the same way '
                   f'in any window. The entry needs to work before the '
                   f'management is worth tuning.')
    elif correlation < 0.3:
        reading = (f'In-sample ranking barely predicts out-of-sample ranking '
                   f'(rho {correlation}). These parameters are not separating '
                   f'strategies; the leaderboard is noise.')
    elif not trustworthy:
        reading = (f'Ranking holds across the split (rho {correlation}), but the '
                   f'best configuration is only {margin:+.2f} R clear of the '
                   f'median. Real, but small enough that it may not survive '
                   f'another window.')
    else:
        reading = (f'Ranking holds across the split (rho {correlation}) and the '
                   f'best configuration is {margin:+.2f} R clear of the median. '
                   f'Worth acting on, and worth re-running on a different window '
                   f'before trusting it with money.')

    return {
        'ranked': len(ranked),
        'of': total,
        'rank_correlation': correlation,
        'best': best['params'],
        'best_out_of_sample_avg_r': best['out_of_sample']['avg_r'],
        'best_in_sample_avg_r': best['in_sample']['avg_r'],
        # The gap between the two is the overfitting measure. A config that
        # halves across the split was fitted to the first window.
        'overfit_gap': (round(best['in_sample']['avg_r'] - best['out_of_sample']['avg_r'], 3)
                        if best['in_sample']['avg_r'] is not None else None),
        'median_out_of_sample_avg_r': round(median_out, 3) if median_out is not None else None,
        'margin_over_median': round(margin, 3) if margin is not None else None,
        'profitable_out_of_sample': profitable,
        # What the best of N scores on data with no edge. Measured from the
        # spread of the winner's own trades rather than assumed, and it is the
        # check the earlier version was missing: two random-walk seeds in five
        # produced a "trustworthy" verdict without it.
        'noise_floor_avg_r': round(floor, 3) if floor is not None else None,
        'beats_noise': beats_noise,
        'trustworthy': trustworthy,
        'reading': reading,
        'caveat': (f'{total} configurations were tested. The best of {total} looks '
                   f'good by chance alone, which is what the rank correlation and '
                   f'the margin over median are there to check.'),
    }
