"""
Tests for strategy 1, the multi-timeframe liquidity sweep.

The load-bearing test in this file is the clock one. A 15M bar stamped 10:00 is
not finished until 10:15, and an entry at 10:05 acting on it is reading the
future — invisible in the results, and it makes a losing strategy backtest
beautifully. Several tests here fail if that filter is removed.

The rest pin refusals: a level used up, a target too close, a trigger that
never came. Those are the rows that distinguish "the strategy found nothing"
from "the filter is too tight", which look identical in a trade count.
"""
import unittest

import sweep_strategy as ss

START = 1_785_000_000       # aligned to a 4-hour boundary
GOLD = 'GOLD.i#'


def bar(time_utc, o, h, l, c):
    return {'time_utc': time_utc, 'open': o, 'high': h, 'low': l, 'close': c}


def series(timeframe, specs, start=START):
    """Bars at a timeframe from (open, high, low, close) tuples."""
    step = ss.period(timeframe)
    return [bar(start + i * step, *spec) for i, spec in enumerate(specs)]


def flat(timeframe, n, price=4000.0, start=START):
    return series(timeframe, [(price, price + 0.5, price - 0.5, price)] * n, start)


def swept_low_series(timeframe='15'):
    """
    A tradeable bullish sweep, with geometry that actually clears the 2R floor.

    Index 2 is a swing high at 4012, confirmed by index 4 — the eventual
    target, and confirmed *before* the sweep, since aiming at a swing that
    forms afterwards would be lookahead. Index 5 is a swing low at 3990,
    confirmed by 7. Index 8 sweeps it: down to 3989, closing back at 3997.

    The wick is deliberately shallow. An earlier version had it 13 points below
    the entry, which made the 2R floor demand a target 26 points away that
    structure did not offer — so every setup was correctly refused and two
    tests passed while asserting nothing.
    """
    return series(timeframe, [
        (4000, 4001, 3999, 4000), (4000, 4001, 3999, 4000),
        (4000, 4012, 3999, 4010),        # swing high — the target
        (4000, 4001, 3999, 4000), (4000, 4001, 3999, 4000),
        (4000, 4000, 3990, 3991),        # swing low — the pool
        (4000, 4001, 3999, 4000), (4000, 4001, 3999, 4000),
        (3993, 4000, 3989, 3997),        # the sweep
        (4000, 4001, 3999, 4000), (4000, 4001, 3999, 4000),
    ])


def entry_series(n=40, timeframe='3'):
    """
    Entry bars beginning after the 15M sweep bar has closed.

    The sweep sits at 15M index 8, so it is not finished until index 9 begins.
    Starting earlier would let an entry act on a bar that had not closed.
    """
    start = START + 9 * ss.period('15')
    return series(timeframe, [(3993, 3997, 3992, 3996.5)] * n, start=start)


class TestSweepDetection(unittest.TestCase):
    def test_taking_a_low_and_closing_back_above_is_a_bullish_sweep(self):
        found = ss.sweeps_on(swept_low_series(), '15')
        self.assertTrue(found)
        self.assertEqual(found[0]['direction'], 'long')
        self.assertEqual(found[0]['swept_level'], 3990)
        self.assertEqual(found[0]['wick'], 3989)

    def test_closing_below_the_level_is_continuation_not_a_sweep(self):
        bars = swept_low_series()
        bars[8] = bar(bars[8]['time_utc'], 3995, 3996, 3985, 3986)
        self.assertEqual(ss.sweeps_on(bars, '15'), [])

    def test_a_sweep_is_not_knowable_until_its_bar_closes(self):
        # The single most important property here.
        found = ss.sweeps_on(swept_low_series('15'), '15')[0]
        self.assertEqual(found['knowable_utc'], found['time_utc'] + 900)
        self.assertGreater(found['knowable_utc'], found['time_utc'])

    def test_the_period_scales_with_the_timeframe(self):
        for timeframe, seconds in (('15', 900), ('60', 3600), ('240', 14400)):
            found = ss.sweeps_on(swept_low_series(timeframe), timeframe)[0]
            self.assertEqual(found['knowable_utc'] - found['time_utc'], seconds)

    def test_flat_bars_produce_no_sweeps(self):
        self.assertEqual(ss.sweeps_on(flat('15', 20), '15'), [])


class TestMultiTimeframe(unittest.TestCase):
    def test_sweeps_are_collected_from_every_configured_timeframe(self):
        bars = {'15': swept_low_series('15'), '60': swept_low_series('60'),
                '30': flat('30', 20), '240': flat('240', 20)}
        found = ss.find_sweeps(bars)
        self.assertEqual({s['timeframe'] for s in found}, {'15', '60'})

    def test_a_missing_timeframe_is_skipped_not_fatal(self):
        found = ss.find_sweeps({'15': swept_low_series('15')})
        self.assertTrue(found)

    def test_sweeps_come_back_in_the_order_they_became_knowable(self):
        bars = {'15': swept_low_series('15'), '60': swept_low_series('60')}
        found = ss.find_sweeps(bars)
        stamps = [s['knowable_utc'] for s in found]
        self.assertEqual(stamps, sorted(stamps))


class TestClockDiscipline(unittest.TestCase):
    """The multi-timeframe form of lookahead, and the filter that prevents it."""

    def setUp(self):
        self.sweep = ss.sweeps_on(swept_low_series('15'), '15')[0]

    def test_entries_start_only_after_the_sweep_bar_closed(self):
        # 3M bars spanning the whole window; the entry must not land inside
        # the 15M bar that produced the signal.
        out = ss.find_setups({'15': swept_low_series('15'), '3': entry_series()})
        self.assertTrue(out['setups'], 'fixture produced nothing to check')
        for setup in out['setups']:
            self.assertGreaterEqual(setup['entry_time_utc'], self.sweep['knowable_utc'],
                                    'entry landed inside the unfinished sweep bar')

    def test_a_sweep_with_no_later_entry_bars_is_rejected(self):
        # Entry bars that all predate the sweep's close.
        early = series('3', [(4000, 4001, 3999, 4000)] * 3, start=START)
        out = ss.find_setups({'15': swept_low_series('15'), '3': early})
        self.assertEqual(out['setups'], [])
        self.assertTrue(any('no entry bars after' in r['reason']
                            for r in out['rejected']))


class TestTriggers(unittest.TestCase):
    def test_a_decisive_bullish_close_confirms(self):
        self.assertTrue(ss._confirmation(bar(0, 4000, 4005, 3999, 4004.5), 'long'))

    def test_a_close_in_the_middle_of_the_range_does_not(self):
        self.assertFalse(ss._confirmation(bar(0, 4000, 4005, 3999, 4001.5), 'long'))

    def test_a_bearish_bar_never_confirms_a_long(self):
        self.assertFalse(ss._confirmation(bar(0, 4004, 4005, 3999, 4000), 'long'))

    def test_direction_is_mirrored_for_shorts(self):
        self.assertTrue(ss._confirmation(bar(0, 4005, 4006, 4000, 4000.5), 'short'))

    def test_a_zero_range_bar_confirms_nothing(self):
        self.assertFalse(ss._confirmation(bar(0, 4000, 4000, 4000, 4000), 'long'))

    def test_mss_fires_when_confirmation_never_comes(self):
        # Bars that grind upward without any decisive close, then break the
        # prior swing high outright.
        cfg = {'entry': {'trigger': 'confirmation', 'fallback': 'mss',
                         'wait_bars': 40}}
        bars = series('3', [(4000, 4008, 3999, 4004)] * 6
                      + [(4004, 4004.5, 4003.5, 4004)] * 6
                      + [(4004, 4020, 4003, 4019)])
        index, rule = ss._trigger_index(bars, 0, 'long', ss.merged(cfg))
        self.assertIsNotNone(index)
        self.assertIn(rule, ('confirmation', 'mss'))

    def test_nothing_triggers_outside_the_wait_window(self):
        cfg = ss.merged({'entry': {'wait_bars': 3, 'fallback': None}})
        bars = series('3', [(4000, 4001, 3999, 4000)] * 10
                      + [(4000, 4005, 3999, 4004.8)])
        index, _ = ss._trigger_index(bars, 0, 'long', cfg)
        self.assertIsNone(index)


class TestLevelReuse(unittest.TestCase):
    def test_a_level_may_be_traded_twice_then_becomes_invalid(self):
        # The behaviour the dashboard exposed: one swept level firing over and
        # over because nothing marked it as used.
        sweep = {'timeframe': '15', 'swept_level': 3990.0, 'direction': 'long',
                 'wick': 3985.0, 'index': 8, 'time_utc': START,
                 'knowable_utc': START + 900, 'level_index': 3}
        uses = {}
        key = (sweep['timeframe'], round(sweep['swept_level'], 5), sweep['direction'])
        allowed = []
        for _ in range(4):
            if uses.get(key, 0) >= 2:
                allowed.append(False)
            else:
                uses[key] = uses.get(key, 0) + 1
                allowed.append(True)
        self.assertEqual(allowed, [True, True, False, False])

    def test_the_limit_is_configurable(self):
        self.assertEqual(ss.merged()['entry']['max_uses'], 2)
        self.assertEqual(ss.merged({'entry': {'max_uses': 1}})['entry']['max_uses'], 1)


class TestTargets(unittest.TestCase):
    def test_a_structural_target_beyond_the_entry_is_used(self):
        bars = swept_low_series('15')
        target = ss._structural_target(bars, 9, 'long', 4000.0)
        self.assertIsNotNone(target)
        self.assertGreater(target, 4000.0)

    def test_no_swing_beyond_the_entry_means_no_target(self):
        self.assertIsNone(ss._structural_target(flat('15', 20), 10, 'long', 4000.0))

    def test_a_noise_swing_is_stepped_over_rather_than_vetoing_the_setup(self):
        # The fixture has a 4001 high left by quiet bars and a real 4012 pool.
        # Taking the nearest swing regardless of distance let the noise one
        # sit just inside the 2R floor and cancel a 4R trade.
        bars = swept_low_series('15')
        self.assertEqual(ss._structural_target(bars, 8, 'long', 3993.0), 4001)
        self.assertEqual(ss._structural_target(bars, 8, 'long', 3993.0,
                                               min_distance=8.5), 4012)

    def test_a_target_closer_than_the_floor_is_refused_not_widened(self):
        # Widening it to min_r would invent a target where structure says
        # there is none.
        sweep = {'timeframe': '15', 'index': 9, 'direction': 'long'}
        cfg = ss.merged({'target': {'min_r': 10.0}})
        target, _ = ss._pick_target({'15': swept_low_series('15')}, sweep, cfg,
                                    4000.0, 15.0, True)
        self.assertIsNone(target)

    def test_a_fixed_target_bypasses_structure(self):
        cfg = ss.merged({'target': {'mode': 'fixed', 'fixed_r': 3.0}})
        target, kind = ss._pick_target({}, {'timeframe': '15', 'index': 0,
                                            'direction': 'long'},
                                       cfg, 4000.0, 2.0, True)
        self.assertEqual(kind, 'fixed')
        self.assertEqual(target, 4006.0)


class TestValidation(unittest.TestCase):
    def test_the_default_config_is_valid(self):
        self.assertEqual(ss.validate(), [])

    def test_tap_and_go_is_refused_with_a_reason_rather_than_missing(self):
        # Present in the vocabulary so the UI can show it as unavailable,
        # rather than silently absent and apparently forgotten.
        problems = ss.validate({'entry': {'trigger': 'tap_and_go'}})
        self.assertTrue(any('not available yet' in p for p in problems))
        self.assertIn('tap_and_go', ss.TRIGGERS)

    def test_an_entry_timeframe_above_a_sweep_timeframe_is_refused(self):
        problems = ss.validate({'entry_timeframe': '60',
                                'sweep_timeframes': ['15']})
        self.assertTrue(any('higher than a sweep timeframe' in p for p in problems))

    def test_an_unknown_timeframe_is_named(self):
        self.assertTrue(ss.validate({'sweep_timeframes': ['7']}))

    def test_risk_above_the_cap_is_refused(self):
        problems = ss.validate({'size': {'risk_pct': 5.0, 'max_risk_pct': 2.5}})
        self.assertTrue(any('every trade is refused' in p for p in problems))

    def test_a_fixed_lot_above_the_max_is_refused(self):
        problems = ss.validate({'size': {'fixed_lot': 0.10, 'max_lot': 0.05}})
        self.assertTrue(any('above max_lot' in p for p in problems))

    def test_a_negative_stop_buffer_is_refused(self):
        problems = ss.validate({'stop': {'buffer_price': -0.5}})
        self.assertTrue(any('inside the wick' in p for p in problems))

    def test_every_problem_is_reported_not_just_the_first(self):
        problems = ss.validate({'sweep_timeframes': ['7'], 'entry': {'wait_bars': 0},
                                'target': {'min_r': -1}})
        self.assertGreaterEqual(len(problems), 3)


class TestUnits(unittest.TestCase):
    def test_nothing_in_the_config_is_denominated_in_pips(self):
        # Gold's pip was 0.01 in an earlier config and is 0.10; every
        # pip-denominated setting was silently wrong by ten. Prices only.
        import json
        text = json.dumps(ss.DEFAULT_CONFIG)
        self.assertNotIn('pips', text)
        self.assertIn('buffer_price', text)

    def test_the_stop_buffer_is_two_and_a_half_gold_pips(self):
        from strategy import spec_for
        self.assertAlmostEqual(
            ss.DEFAULT_CONFIG['stop']['buffer_price'] / spec_for(GOLD)['pip'],
            2.5, places=6)


class TestFindSetups(unittest.TestCase):
    def test_a_full_setup_carries_its_levels(self):
        out = ss.find_setups({'15': swept_low_series('15'), '3': entry_series()})
        self.assertEqual(len(out['setups']), 1, out['reject_reasons'])
        setup = out['setups'][0]
        self.assertEqual(setup['direction'], 'long')
        self.assertEqual(setup['sweep_timeframe'], '15')
        self.assertEqual(setup['entry_timeframe'], '3')
        self.assertLess(setup['stop'], setup['entry_price'])
        self.assertGreater(setup['target'], setup['entry_price'])
        self.assertGreaterEqual(setup['r_multiple_available'], 2.0)
        self.assertEqual(setup['trigger'], 'confirmation')

    def test_the_stop_sits_past_the_sweep_wick(self):
        out = ss.find_setups({'15': swept_low_series('15'), '3': entry_series()})
        self.assertTrue(out['setups'])
        for setup in out['setups']:
            self.assertAlmostEqual(setup['stop'], setup['sweep_wick'] - 0.25, places=5)

    def test_rejections_are_returned_with_reasons_not_dropped(self):
        # How you tell "found nothing" from "filter too tight".
        out = ss.find_setups({'15': swept_low_series('15'),
                              '3': series('3', [(4000, 4001, 3999, 4000)] * 3)})
        self.assertTrue(out['rejected'])
        self.assertTrue(out['reject_reasons'])

    def test_no_entry_bars_is_reported_not_crashed(self):
        out = ss.find_setups({'15': swept_low_series('15')})
        self.assertEqual(out['setups'], [])
        self.assertIn('no 3-minute bars', out['note'])

    def test_empty_input_is_tolerated(self):
        self.assertEqual(ss.find_setups({})['setups'], [])


if __name__ == '__main__':
    unittest.main()
