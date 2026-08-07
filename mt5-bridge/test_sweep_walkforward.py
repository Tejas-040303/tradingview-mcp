"""
Tests for strategy 1's walk-forward validation.

The split is the whole risk here. Strategy 1 reads six timeframes, and cutting
each at a fraction of its own length puts the boundary at a different instant
on every one — the halves then overlap, out-of-sample sees in-sample bars, and
the validation quietly stops validating. Most of these pin one cut time applied
everywhere, and that no trade can be entered on the wrong side of it.
"""
import random
import unittest

import sweep_strategy as ss
import sweep_walkforward as wf

GOLD = 'GOLD.i#'
START = 1_785_000_000 - (1_785_000_000 % 14400)


def resample(m1, minutes):
    step, bucket = minutes * 60, {}
    for b in m1:
        key = b['time_utc'] - (b['time_utc'] % step)
        if key not in bucket:
            bucket[key] = {'time_utc': key, 'open': b['open'], 'high': b['high'],
                           'low': b['low'], 'close': b['close']}
        else:
            g = bucket[key]
            g['high'] = max(g['high'], b['high'])
            g['low'] = min(g['low'], b['low'])
            g['close'] = b['close']
    return [bucket[k] for k in sorted(bucket)]


def gold_bars(minutes=12000, seed=19):
    """A random-walk gold series at every timeframe strategy 1 reads."""
    rng = random.Random(seed)
    price, m1 = 4000.0, []
    for i in range(minutes):
        o = price
        c = o + rng.gauss(0, 0.22)
        m1.append({'time_utc': START + i * 60, 'open': round(o, 2),
                   'high': round(max(o, c) + abs(rng.gauss(0, 0.12)), 2),
                   'low': round(min(o, c) - abs(rng.gauss(0, 0.12)), 2),
                   'close': round(c, 2)})
        price = c
    return {'1': m1, '3': resample(m1, 3), '15': resample(m1, 15),
            '30': resample(m1, 30), '60': resample(m1, 60),
            '240': resample(m1, 240)}


class TestCutTime(unittest.TestCase):
    BARS = gold_bars(3000)

    def test_the_cut_comes_from_the_entry_timeframe(self):
        cut = wf.cut_time(self.BARS, '3', 0.7)
        times = [b['time_utc'] for b in self.BARS['3']]
        self.assertIn(cut, times)

    def test_the_split_fraction_moves_the_cut(self):
        early = wf.cut_time(self.BARS, '3', 0.3)
        late = wf.cut_time(self.BARS, '3', 0.8)
        self.assertLess(early, late)

    def test_a_series_too_short_to_split_returns_none(self):
        self.assertIsNone(wf.cut_time({'3': []}, '3'))
        self.assertIsNone(wf.cut_time({'3': [{'time_utc': 1}]}, '3'))

    def test_neither_half_is_ever_empty(self):
        for split in (0.0, 0.01, 0.99, 1.0):
            cut = wf.cut_time(self.BARS, '3', split)
            before = [b for b in self.BARS['3'] if b['time_utc'] < cut]
            after = [b for b in self.BARS['3'] if b['time_utc'] >= cut]
            self.assertTrue(before, f'split {split} emptied the first half')
            self.assertTrue(after, f'split {split} emptied the second half')


class TestSlicing(unittest.TestCase):
    BARS = gold_bars(3000)

    def setUp(self):
        self.cut = wf.cut_time(self.BARS, '3', 0.7)
        self.in_bars, self.out_bars = wf.slice_at(self.BARS, '3', self.cut)

    def test_one_cut_time_is_applied_to_every_timeframe(self):
        # The bug this exists to prevent: 70% of 84 four-hour bars and 70% of
        # 6667 three-minute bars are not the same moment.
        for timeframe in self.in_bars:
            for bar in self.in_bars[timeframe]:
                self.assertLess(bar['time_utc'], self.cut,
                                f'{timeframe} in-sample bar crossed the cut')

    def test_the_entry_timeframe_is_cut_cleanly(self):
        # A trade may only be entered inside its own half, so no warm-up here.
        for bar in self.out_bars['3']:
            self.assertGreaterEqual(bar['time_utc'], self.cut)

    def test_sweep_timeframes_carry_warm_up_history(self):
        # Without it the out-of-sample half has no swings for its first sweeps
        # to be measured against.
        earliest = min(b['time_utc'] for b in self.out_bars['60'])
        self.assertLess(earliest, self.cut)

    def test_warm_up_can_be_turned_off(self):
        _, out_bars = wf.slice_at(self.BARS, '3', self.cut, warmup_bars=0)
        self.assertTrue(all(b['time_utc'] >= self.cut for b in out_bars['60']))

    def test_warm_up_bars_cannot_produce_a_trade(self):
        # They are detection-only. A sweep older than the entry window is
        # refused, so nothing can leak across the boundary.
        out = ss.find_setups(self.out_bars)
        for setup in out['setups']:
            self.assertGreaterEqual(setup['entry_time_utc'], self.cut,
                                    'a warm-up sweep produced a trade before the cut')

    def test_the_halves_do_not_overlap_on_the_entry_timeframe(self):
        before = {b['time_utc'] for b in self.in_bars['3']}
        after = {b['time_utc'] for b in self.out_bars['3']}
        self.assertEqual(before & after, set())


class TestDetectionCache(unittest.TestCase):
    """Detection is cached across configs; it must not be shared across entries."""

    BARS = gold_bars(3000)

    def test_management_does_not_change_which_setups_exist(self):
        from sweep_backtest import detection_key
        a = detection_key({'manage': {'trail_at_r': None}, 'size': {'risk_pct': 2}})
        b = detection_key({'manage': {'trail_at_r': 1.5}, 'size': {'risk_pct': 0.5}})
        self.assertEqual(a, b)

    def test_the_entry_and_target_do_change_it(self):
        # If the key were too loose, every row in a grid varying these would
        # carry identical trade counts — a sweep that silently tested one
        # configuration.
        from sweep_backtest import detection_key
        base = detection_key({})
        self.assertNotEqual(base, detection_key({'entry': {'fallback': None}}))
        self.assertNotEqual(base, detection_key({'target': {'min_r': 5.0}}))
        self.assertNotEqual(base, detection_key({'stop': {'buffer_price': 1.0}}))
        self.assertNotEqual(base, detection_key({'sweep_timeframes': ['15']}))

    def test_a_grid_over_detection_axes_produces_different_results(self):
        out = wf.walk_forward(self.BARS, {'target.min_r': [2.0, 8.0]}, balance=5000)
        counts = {r['in_sample']['trades'] for r in out['rows']}
        self.assertEqual(len(out['rows']), 2)
        self.assertGreater(len(counts), 1, 'the cache collapsed two detections into one')


class TestWalkForward(unittest.TestCase):
    BARS = gold_bars(12000)

    def test_both_halves_are_reported_for_every_configuration(self):
        out = wf.walk_forward(self.BARS, {'target.min_r': [2.0, 3.0]},
                              balance=5000)
        self.assertTrue(out['success'])
        self.assertEqual(len(out['rows']), 2)
        for row in out['rows']:
            self.assertIn('in_sample', row)
            self.assertIn('out_of_sample', row)

    def test_the_split_is_described_in_the_result(self):
        out = wf.walk_forward(self.BARS, {'target.min_r': [2.0]}, balance=5000)
        self.assertIn('cut_utc', out['split'])
        self.assertGreater(out['split']['in_sample_entry_bars'], 0)
        self.assertGreater(out['split']['out_of_sample_entry_bars'], 0)

    def test_the_control_case_is_in_the_default_grid(self):
        # trail_at_r None answers "does trailing help at all", and fallback
        # None answers "does MSS add anything". A grid without controls
        # compares variants of a belief rather than testing it.
        self.assertIn(None, wf.DEFAULT_AXES['manage.trail_at_r'])
        self.assertIn(None, wf.DEFAULT_AXES['entry.fallback'])

    def test_random_data_is_not_endorsed(self):
        # Same guard as sweep.py, reused rather than reimplemented.
        out = wf.walk_forward(self.BARS, balance=5000)
        self.assertFalse(out['summary'].get('trustworthy'))

    def test_the_assumed_spread_is_reported(self):
        out = wf.walk_forward(self.BARS, {'target.min_r': [2.0]}, balance=5000,
                              spread=0.45)
        self.assertEqual(out['spread_assumed'], 0.45)

    def test_skip_reasons_survive_into_each_half(self):
        # A config trading rarely because most targets were too close is not
        # a config that "does not work", and the reasons are how you tell.
        # An absurd floor does not zero the trade count — the target search
        # steps over near candidates and finds genuinely distant pools — so
        # the assertion is on the reasons and the direction, not on zero.
        strict = wf.walk_forward(self.BARS, {'target.min_r': [50.0]}, balance=5000)
        loose = wf.walk_forward(self.BARS, {'target.min_r': [2.0]}, balance=5000)
        reasons = strict['rows'][0]['out_of_sample']['skipped_reasons']
        self.assertTrue(reasons)
        self.assertTrue(any('no target at least' in r for r in reasons))
        self.assertLess(strict['rows'][0]['out_of_sample']['trades'],
                        loose['rows'][0]['out_of_sample']['trades'])

    def test_thin_configurations_are_reported_but_not_ranked(self):
        out = wf.walk_forward(self.BARS, {'target.min_r': [2.0]}, balance=5000,
                              min_trades=100_000)
        self.assertTrue(out['rows'])
        self.assertFalse(any(r['ranked'] for r in out['rows']))
        self.assertEqual(out['summary']['ranked'], 0)

    def test_too_few_bars_is_refused_with_a_reason(self):
        out = wf.walk_forward({'3': []}, {'target.min_r': [2.0]})
        self.assertFalse(out['success'])
        self.assertIn('split', out['error'])

    def test_an_empty_grid_is_reported_not_crashed(self):
        out = wf.walk_forward(self.BARS, {'target.min_r': [-1.0]})
        self.assertEqual(out['rows'], [])
        self.assertIn('no valid configurations', out['summary']['note'])


if __name__ == '__main__':
    unittest.main()
