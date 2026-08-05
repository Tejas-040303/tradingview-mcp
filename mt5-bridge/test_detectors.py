"""
Tests for the level detectors.

The recurring assertion is about *when* a pattern becomes usable. A detector
that reports a swing at the bar it occurred hands the strategy information from
the future, and the resulting backtest is worthless in a way that looks
excellent. Several of these fail if `knowable_at` regresses to `index`.
"""
import unittest

from detectors import (SWING_RIGHT, active_zones, confirmation, detect,
                       fair_value_gaps, fib_zones, liquidity_sweeps,
                       order_blocks, present_at, swings)

MINUTE = 60
START = 1_767_571_200


def bar(i, o, h, l, c):
    return {'time_utc': START + i * MINUTE, 'open': o, 'high': h, 'low': l, 'close': c}


def flat(n, price, start=0):
    return [bar(start + i, price, price + 0.1, price - 0.1, price) for i in range(n)]


class TestSwings(unittest.TestCase):
    def test_finds_a_peak(self):
        bars = flat(3, 100) + [bar(3, 100, 105, 100, 104)] + flat(3, 100, start=4)
        highs = [s for s in swings(bars) if s['kind'] == 'high']
        self.assertEqual([s['index'] for s in highs], [3])

    def test_finds_a_trough(self):
        bars = flat(3, 100) + [bar(3, 100, 100, 95, 96)] + flat(3, 100, start=4)
        lows = [s for s in swings(bars) if s['kind'] == 'low']
        self.assertEqual([s['index'] for s in lows], [3])

    def test_a_swing_is_not_knowable_until_its_right_bars_exist(self):
        # The single most important property in this module.
        bars = flat(3, 100) + [bar(3, 100, 105, 100, 104)] + flat(3, 100, start=4)
        peak = [s for s in swings(bars) if s['kind'] == 'high'][0]
        self.assertEqual(peak['index'], 3)
        self.assertEqual(peak['knowable_at'], 3 + SWING_RIGHT)
        self.assertGreater(peak['knowable_at'], peak['index'])

    def test_a_flat_series_has_no_swings(self):
        # Every bar equal means no bar exceeds its neighbours.
        bars = [bar(i, 100, 100, 100, 100) for i in range(10)]
        self.assertEqual(swings(bars), [])

    def test_tolerates_a_series_shorter_than_the_window(self):
        self.assertEqual(swings(flat(3, 100)), [])


class TestFairValueGaps(unittest.TestCase):
    def test_bullish_gap_between_candle_one_and_three(self):
        bars = [bar(0, 100, 101, 99, 100),
                bar(1, 101, 110, 101, 109),      # the impulse
                bar(2, 109, 112, 105, 111)]      # low 105 > first high 101
        gaps = fair_value_gaps(bars)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]['direction'], 'long')
        self.assertEqual((gaps[0]['low'], gaps[0]['high']), (101, 105))

    def test_bearish_gap(self):
        bars = [bar(0, 110, 111, 109, 110),
                bar(1, 109, 109, 100, 101),
                bar(2, 101, 105, 99, 100)]       # high 105 < first low 109
        gaps = fair_value_gaps(bars)
        self.assertEqual(gaps[0]['direction'], 'short')
        self.assertEqual((gaps[0]['low'], gaps[0]['high']), (105, 109))

    def test_overlapping_candles_are_not_a_gap(self):
        bars = [bar(0, 100, 105, 99, 104), bar(1, 104, 108, 103, 107),
                bar(2, 107, 110, 104, 109)]
        self.assertEqual(fair_value_gaps(bars), [])

    def test_a_gap_is_knowable_only_once_the_third_candle_closes(self):
        bars = [bar(0, 100, 101, 99, 100), bar(1, 101, 110, 101, 109),
                bar(2, 109, 112, 105, 111)]
        gap = fair_value_gaps(bars)[0]
        self.assertEqual(gap['index'], 1)
        self.assertEqual(gap['knowable_at'], 2)


class TestLiquiditySweeps(unittest.TestCase):
    def _with_swing_low(self):
        # A trough at index 3, price 95, confirmed at index 5.
        return flat(3, 100) + [bar(3, 100, 100, 95, 96)] + flat(4, 100, start=4)

    def test_taking_out_a_low_and_closing_back_above_is_a_long_signal(self):
        bars = self._with_swing_low() + [bar(8, 99, 100, 93, 99)]
        found = liquidity_sweeps(bars)
        self.assertTrue(found)
        self.assertEqual(found[0]['direction'], 'long')
        self.assertEqual(found[0]['swept'], 95)

    def test_breaking_a_low_and_staying_below_is_not_a_sweep(self):
        # Closing beyond the level is continuation, not a liquidity grab.
        bars = self._with_swing_low() + [bar(8, 99, 100, 93, 94)]
        self.assertEqual(liquidity_sweeps(bars), [])

    def test_a_swing_cannot_be_swept_before_it_is_confirmed(self):
        # The sweeping bar sits inside the swing's own confirmation window.
        bars = flat(3, 100) + [bar(3, 100, 100, 95, 96), bar(4, 96, 100, 93, 99)]
        self.assertEqual(liquidity_sweeps(bars), [])

    def test_old_swings_fall_out_of_the_lookback(self):
        bars = self._with_swing_low() + flat(40, 100, start=8) + \
            [bar(48, 99, 100, 93, 99)]
        self.assertEqual(liquidity_sweeps(bars, lookback_bars=10), [])


class TestOrderBlocks(unittest.TestCase):
    def test_last_down_candle_before_an_upward_break(self):
        bars = (flat(3, 100)
                + [bar(3, 100, 105, 100, 104)]        # swing high at 105
                + flat(2, 100, start=4)
                + [bar(6, 102, 102, 100, 100.5)]      # the down candle
                + [bar(7, 100.5, 107, 100.5, 106)])   # break above 105
        blocks = order_blocks(bars)
        self.assertTrue(blocks)
        self.assertEqual(blocks[0]['direction'], 'long')
        self.assertEqual(blocks[0]['index'], 6)

    def test_the_block_is_knowable_at_the_break_not_at_the_candle(self):
        # Knowing at index 6 would mean trading on a break that had not happened.
        bars = (flat(3, 100) + [bar(3, 100, 105, 100, 104)] + flat(2, 100, start=4)
                + [bar(6, 102, 102, 100, 100.5)] + [bar(7, 100.5, 107, 100.5, 106)])
        block = order_blocks(bars)[0]
        self.assertEqual(block['index'], 6)
        self.assertEqual(block['knowable_at'], 7)

    def test_no_break_means_no_block(self):
        bars = flat(3, 100) + [bar(3, 100, 105, 100, 104)] + flat(5, 100, start=4)
        self.assertEqual(order_blocks(bars), [])


class TestFibZones(unittest.TestCase):
    def test_band_sits_inside_the_leg(self):
        bars = (flat(3, 100) + [bar(3, 100, 100, 90, 91)] + flat(2, 100, start=4)
                + [bar(6, 100, 110, 100, 109)] + flat(3, 100, start=7))
        zones = fib_zones(bars, levels=(0.5,))
        self.assertTrue(zones)
        leg = zones[0]['leg']
        self.assertTrue(leg['low'] <= zones[0]['low'] <= leg['high'])

    def test_knowable_only_once_both_swings_are_confirmed(self):
        bars = (flat(3, 100) + [bar(3, 100, 100, 90, 91)] + flat(2, 100, start=4)
                + [bar(6, 100, 110, 100, 109)] + flat(3, 100, start=7))
        zone = fib_zones(bars)[0]
        self.assertGreaterEqual(zone['knowable_at'], zone['index'])

    def test_two_swings_of_the_same_kind_are_not_a_leg(self):
        # High to high is not an impulse, it is two peaks. Supplied directly
        # because a bar series that produces only highs does not exist — a peak
        # always leaves troughs either side of it.
        points = [{'kind': 'high', 'index': 3, 'price': 105, 'knowable_at': 5},
                  {'kind': 'high', 'index': 7, 'price': 107, 'knowable_at': 9}]
        self.assertEqual(fib_zones(flat(10, 100), points), [])


class TestActiveZones(unittest.TestCase):
    ZONES = [{'index': 5, 'knowable_at': 7, 'low': 100, 'high': 101,
              'direction': 'long'}]

    def test_a_zone_is_invisible_before_it_is_knowable(self):
        self.assertEqual(active_zones(self.ZONES, 6), [])
        self.assertEqual(len(active_zones(self.ZONES, 7)), 1)

    def test_stale_zones_expire(self):
        self.assertEqual(len(active_zones(self.ZONES, 20, max_age_bars=20)), 1)
        self.assertEqual(active_zones(self.ZONES, 40, max_age_bars=20), [])


class TestConfirmation(unittest.TestCase):
    ZONE = {'low': 100, 'high': 101, 'direction': 'long'}

    def test_close_beyond_needs_a_decisive_close(self):
        strong = bar(0, 100, 103, 100, 102.8)     # closes near its high
        weak = bar(0, 100, 103, 100, 101.2)       # closes past, but in the middle
        self.assertTrue(confirmation(strong, self.ZONE))
        self.assertFalse(confirmation(weak, self.ZONE))

    def test_close_inside_the_zone_is_not_confirmation(self):
        self.assertFalse(confirmation(bar(0, 100, 101, 100, 100.5), self.ZONE))

    def test_engulfing_requires_covering_the_zone_the_right_way(self):
        covering = bar(0, 99, 103, 99, 102)
        wrong_way = bar(0, 102, 103, 99, 99.5)
        self.assertTrue(confirmation(covering, self.ZONE, 'engulfing'))
        self.assertFalse(confirmation(wrong_way, self.ZONE, 'engulfing'))

    def test_rejection_needs_a_long_wick_the_right_way(self):
        wicked = bar(0, 101.5, 102, 98, 101.8)
        self.assertTrue(confirmation(wicked, self.ZONE, 'rejection'))

    def test_a_zero_range_bar_confirms_nothing(self):
        self.assertFalse(confirmation(bar(0, 100, 100, 100, 100), self.ZONE))


class TestDetectAndPresent(unittest.TestCase):
    CONFIG = {'entry': {'conditions': {
        'fvg': {'on': True, 'max_age_bars': 20},
        'liquidity_sweep': {'on': False},
        'order_block': {'on': False},
        'fib': {'on': False},
    }}}

    def test_only_enabled_detectors_run(self):
        bars = flat(10, 100)
        out = detect(bars, self.CONFIG)
        self.assertEqual(set(out), {'fvg'})

    def test_present_at_groups_by_direction(self):
        bars = [bar(0, 100, 101, 99, 100), bar(1, 101, 110, 101, 109),
                bar(2, 109, 112, 105, 111), bar(3, 111, 112, 102, 103)]
        zones = detect(bars, self.CONFIG)
        # Bar 3 dips back into the 101-105 gap.
        hits = present_at(zones, bars, 3, self.CONFIG)
        self.assertIn('long', hits)
        self.assertIn('fvg', hits['long'])

    def test_a_gap_does_not_count_as_touched_on_its_own_third_candle(self):
        # The gap's upper edge is that candle's low, so an inclusive overlap
        # test fires the setup at formation instead of on the retest.
        bars = [bar(0, 100, 101, 99, 100), bar(1, 101, 110, 101, 109),
                bar(2, 109, 112, 105, 111)]
        zones = detect(bars, self.CONFIG)
        self.assertEqual(present_at(zones, bars, 2, self.CONFIG), {})

    def test_nothing_is_present_before_the_zone_is_knowable(self):
        bars = [bar(0, 100, 101, 99, 100), bar(1, 101, 110, 101, 109),
                bar(2, 109, 112, 105, 111)]
        zones = detect(bars, self.CONFIG)
        self.assertEqual(present_at(zones, bars, 1, self.CONFIG), {})

    def test_tolerates_empty_input(self):
        self.assertEqual(detect([], self.CONFIG), {'fvg': []})
        self.assertEqual(present_at({}, flat(3, 100), 0, self.CONFIG), {})


if __name__ == '__main__':
    unittest.main()
