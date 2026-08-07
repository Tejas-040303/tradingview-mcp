"""
Walk-forward validation for strategy 1.

Same discipline as `sweep.py` — chronological split, both halves reported, the
median beside the winner, rank correlation between them, and a best-of-N noise
floor. Those checks are imported rather than reimplemented, because the one
that matters most was missing for a while and a second copy is a second place
for that to happen.

What is genuinely different here is the split itself. Strategy 1 reads six
timeframes at once, so cutting each series at 70% of *its own length* puts the
boundary at a different instant on every one: 70% of 84 four-hour bars and 70%
of 6667 three-minute bars are not the same moment. The entry timeframe defines
a cut **time**, and every series is sliced against that.

The out-of-sample half also needs history before it, or its first sweeps have
no swings to be measured against. Each half therefore carries a warm-up of
higher-timeframe bars from before its own start — and because those bars are
only used to *detect*, never to trade, `find_setups` refuses any sweep older
than the entry window and no signal can leak across the boundary.
"""
import sweep_strategy as ss
from sweep import MIN_TRADES, _label, _median, _noise_floor, _spearman, _verdict, expand
from sweep_backtest import DEFAULT_SPREAD, backtest, detection_key

# Bars of higher-timeframe history each half gets before its own start, so
# swings can form. Detection only — nothing here can produce a trade before the
# half begins, because a sweep older than the entry window is refused.
WARMUP_BARS = 80

# The knobs worth asking about first. `min_r` is here because the structural
# target floor is the rule most likely to be wrong for gold, and `fallback`
# includes None so "does MSS add anything at all" has a control.
DEFAULT_AXES = {
    'target.min_r': [2.0, 3.0],
    'manage.trail_at_r': [0.5, 1.0, None],
    'manage.partial_pct': [0, 50],
    'entry.fallback': ['mss', None],
}


def cut_time(bars_by_tf, entry_tf, split=0.7):
    """
    The instant the in-sample half ends, taken from the entry timeframe.

    One time for every series. Slicing each timeframe at a fraction of its own
    length would put the boundary somewhere different on each, and the halves
    would silently overlap.
    """
    rows = bars_by_tf.get(str(entry_tf)) or []
    if len(rows) < 2:
        return None
    index = int(len(rows) * split)
    index = max(1, min(index, len(rows) - 1))
    return rows[index]['time_utc']


def slice_at(bars_by_tf, entry_tf, cut, warmup_bars=WARMUP_BARS):
    """
    Two bar sets, split at `cut`, each with detection warm-up.

    The entry timeframe is cut cleanly — a trade may only be entered inside its
    own half. The sweep timeframes get `warmup_bars` of history before the cut
    in the out-of-sample set, purely so swings exist; a sweep detected in that
    warm-up cannot be traded, because its close predates the entry window and
    `find_setups` refuses it.
    """
    entry_tf = str(entry_tf)
    in_sample, out_sample = {}, {}

    for timeframe, rows in bars_by_tf.items():
        if timeframe == entry_tf:
            in_sample[timeframe] = [b for b in rows if b['time_utc'] < cut]
            out_sample[timeframe] = [b for b in rows if b['time_utc'] >= cut]
            continue

        before = [b for b in rows if b['time_utc'] < cut]
        in_sample[timeframe] = before
        after = [b for b in rows if b['time_utc'] >= cut]
        out_sample[timeframe] = (before[-warmup_bars:] + after) if warmup_bars else after

    return in_sample, out_sample


def _detection_cache(bars_by_tf):
    """
    Setups keyed by the detection-affecting half of a config.

    Management and sizing cannot change which setups exist, and the default
    grid varies them heavily — twenty-four configurations reduce to four
    distinct detections. Detection is the expensive part.
    """
    cache = {}

    def get(config):
        key = detection_key(config)
        if key not in cache:
            cache[key] = ss.find_setups(bars_by_tf, config)
        return cache[key]

    return get


def _run(bars_by_tf, config, balance, spread, found=None):
    out = backtest(bars_by_tf, config, balance=balance, spread=spread, found=found)
    if not out['success']:
        return {'trades': 0, 'avg_r': None, 'r_stdev': None,
                'problems': out['problems']}
    summary = out['summary']
    return {
        'trades': summary.get('closed', 0),
        'avg_r': summary.get('avg_r'),
        'r_stdev': summary.get('r_stdev'),
        'win_rate_pct': summary.get('win_rate_pct'),
        'expectancy': summary.get('expectancy'),
        'net': summary.get('net'),
        'profit_factor': summary.get('profit_factor'),
        # Carried through because it is often the finding: a config with no
        # trades because every target was too close is not a config that
        # "does not work".
        'skipped_reasons': summary.get('skipped_reasons') or {},
    }


def walk_forward(bars_by_tf, axes=None, base=None, split=0.7, balance=1000.0,
                 spread=DEFAULT_SPREAD, min_trades=MIN_TRADES,
                 warmup_bars=WARMUP_BARS):
    """
    Every configuration of strategy 1, run on both halves of the window.

    Ranked by out-of-sample average R — the only column the ranking is entitled
    to claim anything about. The verdict is the same one `sweep.py` applies,
    including the best-of-N floor, so a winner that merely reflects having
    searched a grid is not endorsed.
    """
    axes = DEFAULT_AXES if axes is None else axes
    configs = expand(axes, base, validator=ss.validate)
    entry_tf = str(ss.merged(base)['entry_timeframe'])

    if not configs:
        return {'success': True, 'rows': [],
                'summary': {'note': 'no valid configurations in this grid'}}

    cut = cut_time(bars_by_tf, entry_tf, split)
    if cut is None:
        return {'success': False,
                'error': f'not enough {entry_tf}-minute bars to split'}

    in_bars, out_bars = slice_at(bars_by_tf, entry_tf, cut, warmup_bars)
    if not in_bars.get(entry_tf) or not out_bars.get(entry_tf):
        return {'success': False,
                'error': f'split {split} leaves one half with no entry bars'}

    in_setups, out_setups = _detection_cache(in_bars), _detection_cache(out_bars)

    rows = []
    for config in configs:
        merged_cfg = {**(base or {}), **config} if base else config
        in_sample = _run(in_bars, merged_cfg, balance, spread,
                         in_setups(merged_cfg))
        out_sample = _run(out_bars, merged_cfg, balance, spread,
                          out_setups(merged_cfg))
        rows.append({
            'params': _label(config, axes),
            'in_sample': in_sample,
            'out_of_sample': out_sample,
            'ranked': (in_sample['trades'] >= min_trades
                       and out_sample['trades'] >= min_trades),
        })

    ranked = [r for r in rows if r['ranked'] and r['out_of_sample']['avg_r'] is not None]
    ranked.sort(key=lambda r: r['out_of_sample']['avg_r'], reverse=True)
    rows.sort(key=lambda r: (not r['ranked'], -(r['out_of_sample']['avg_r'] or -99)))

    return {
        'success': True,
        'strategy': 'liquidity-sweep',
        'configurations': len(configs),
        'split': {
            'cut_utc': cut,
            'in_sample_entry_bars': len(in_bars[entry_tf]),
            'out_of_sample_entry_bars': len(out_bars[entry_tf]),
            'warmup_bars': warmup_bars,
            'note': ('one cut time applied to every timeframe — splitting each '
                     'series at a fraction of its own length would put the '
                     'boundary at a different instant on each'),
        },
        'rows': rows,
        'summary': _verdict(ranked, len(configs), min_trades),
        'spread_assumed': spread,
    }


__all__ = ['DEFAULT_AXES', 'WARMUP_BARS', 'cut_time', 'slice_at', 'walk_forward',
           '_median', '_noise_floor', '_spearman']
