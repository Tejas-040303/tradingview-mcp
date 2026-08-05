"""
Tests for the stop-distance analysis.

Two things carry the most weight here: that the stop distance is only taken from
trades that actually hit a stop, and that ATR never sees a bar from after the
entry it is meant to characterise.
"""
import unittest

from stopsize import (ATR_PERIOD, MIN_STOPS, analyze, atr, atr_before,
                      observed_stops, survival_test, true_range)

MINUTE = 60
MONDAY = 1_767_571_200


def bar(at, o, h, l, c):
    return {'time_utc': at, 'open': o, 'high': h, 'low': l, 'close': c}


def series(start, count, low, high, step=MINUTE):
    return [bar(start + i * step, low, high, low, high) for i in range(count)]


def trade(position_id=1, entry=4000.0, exit_price=3997.5, exit_reason='stop_loss',
          symbol='GOLD.i#', opened=MONDAY, is_open=False):
    return {
        'position_id': position_id, 'symbol': symbol,
        'entry_price': entry, 'exit_price': None if is_open else exit_price,
        'opened_utc': opened, 'closed_utc': None if is_open else opened + 600,
        'exit_reason': None if is_open else exit_reason, 'open': is_open,
    }


def row(mae=1.0, net=5.0, symbol='GOLD.i#'):
    return {'mae': mae, 'net': net, 'symbol': symbol}


class TestTrueRange(unittest.TestCase):
    def test_first_bar_uses_its_own_span(self):
        self.assertEqual(true_range(bar(0, 10, 12, 9, 11), None), 3)

    def test_a_gap_up_widens_the_range(self):
        # Bar spans 1, but it opened 5 above the previous close.
        self.assertEqual(true_range(bar(0, 15, 16, 15, 15.5), 11), 5)

    def test_a_gap_down_widens_it_too(self):
        self.assertEqual(true_range(bar(0, 6, 7, 6, 6.5), 11), 5)


class TestAtr(unittest.TestCase):
    def test_averages_the_ranges(self):
        bars = series(MONDAY, 20, 4000.0, 4002.0)
        self.assertAlmostEqual(atr(bars), 2.0, places=5)

    def test_too_few_bars_is_none(self):
        self.assertIsNone(atr([]))
        self.assertIsNone(atr(series(MONDAY, 1, 4000.0, 4002.0)))

    def test_never_looks_past_the_cut(self):
        # Quiet before, violent after. An ATR computed at the boundary must not
        # see the violent bars — that would be lookahead, and it would flatter
        # every stop that happened to precede a calm stretch.
        quiet = series(MONDAY, 20, 4000.0, 4001.0)
        loud = series(MONDAY + 20 * MINUTE, 20, 4000.0, 4050.0)
        bars = quiet + loud
        times = [b['time_utc'] for b in bars]
        value = atr_before(bars, times, MONDAY + 20 * MINUTE)
        self.assertLess(value, 2.0)

    def test_none_when_the_cut_leaves_nothing_behind_it(self):
        bars = series(MONDAY, 10, 4000.0, 4001.0)
        times = [b['time_utc'] for b in bars]
        self.assertIsNone(atr_before(bars, times, MONDAY))


class TestObservedStops(unittest.TestCase):
    def test_distance_comes_only_from_stopped_out_trades(self):
        rows = [
            trade(1, entry=4000.0, exit_price=3997.0, exit_reason='stop_loss'),
            # A manual exit at a loss is where the trader gave up, not where the
            # stop was — including it would corrupt the typical distance.
            trade(2, entry=4000.0, exit_price=3950.0, exit_reason='mobile'),
            trade(3, entry=4000.0, exit_price=4020.0, exit_reason='take_profit'),
        ]
        out = observed_stops(rows)['GOLD.i#']
        self.assertEqual(out['stops_observed'], 1)
        self.assertEqual(out['median_distance'], 3.0)

    def test_median_and_spread(self):
        rows = [trade(i, entry=4000.0, exit_price=4000.0 - d, opened=MONDAY + i * 60)
                for i, d in enumerate([1.0, 2.0, 3.0, 4.0, 5.0], start=1)]
        out = observed_stops(rows)['GOLD.i#']
        self.assertEqual(out['median_distance'], 3.0)
        self.assertLessEqual(out['p25'], out['median_distance'])
        self.assertGreaterEqual(out['p75'], out['median_distance'])

    def test_symbols_are_kept_apart(self):
        rows = [trade(1, symbol='GOLD.i#', entry=4000.0, exit_price=3997.0),
                trade(2, symbol='OILCash#', entry=75.0, exit_price=74.7)]
        out = observed_stops(rows)
        self.assertEqual(set(out), {'GOLD.i#', 'OILCash#'})
        self.assertAlmostEqual(out['OILCash#']['median_distance'], 0.3, places=5)

    def test_thin_symbols_are_flagged(self):
        few = observed_stops([trade(i, opened=MONDAY + i * 60) for i in range(1, 4)])
        self.assertFalse(few['GOLD.i#']['reliable'])
        many = observed_stops([trade(i, opened=MONDAY + i * 60)
                               for i in range(1, MIN_STOPS + 1)])
        self.assertTrue(many['GOLD.i#']['reliable'])

    def test_open_trades_and_zero_distances_are_ignored(self):
        rows = [trade(1, is_open=True),
                trade(2, entry=4000.0, exit_price=4000.0)]
        self.assertEqual(observed_stops(rows), {})


class TestSurvivalTest(unittest.TestCase):
    STOPS = {'GOLD.i#': {'median_distance': 2.0, 'stops_observed': 20, 'reliable': True}}

    def test_counts_winners_that_dipped_past_the_stop(self):
        rows = [row(mae=3.0, net=5.0), row(mae=0.5, net=5.0), row(mae=2.5, net=8.0)]
        out = survival_test(rows, self.STOPS)
        self.assertEqual(out['winners_examined'], 3)
        self.assertEqual(out['would_have_been_stopped'], 2)
        self.assertEqual(out['profit_at_risk'], 13.0)

    def test_losers_are_not_part_of_the_question(self):
        # The counterfactual is about winners the stop would have killed.
        rows = [row(mae=9.0, net=-5.0), row(mae=3.0, net=5.0)]
        self.assertEqual(survival_test(rows, self.STOPS)['winners_examined'], 1)

    def test_a_symbol_with_no_observed_stop_is_skipped_not_assumed(self):
        rows = [row(mae=3.0, net=5.0, symbol='OILCash#'), row(mae=3.0, net=5.0)]
        out = survival_test(rows, self.STOPS)
        self.assertEqual(out['winners_examined'], 1)
        self.assertEqual(out['winners_without_a_stop_reference'], 1)

    def test_no_winners_at_all_is_none(self):
        self.assertIsNone(survival_test([row(mae=1.0, net=-5.0)], self.STOPS))
        self.assertIsNone(survival_test([], self.STOPS))


class TestAnalyze(unittest.TestCase):
    def _case(self, winner_mae, count=MIN_STOPS + 5):
        trades = [trade(i, entry=4000.0, exit_price=3998.0, opened=MONDAY + i * 60)
                  for i in range(1, count + 1)]
        rows = [row(mae=winner_mae, net=5.0) for _ in range(count)]
        return analyze(trades, rows)

    def test_high_kill_rate_reads_as_too_tight(self):
        out = self._case(winner_mae=5.0)     # every winner dipped past a 2.0 stop
        self.assertTrue(out['too_tight'])
        self.assertIn('inside the range', out['verdict'])

    def test_low_kill_rate_clears_the_stop(self):
        out = self._case(winner_mae=0.2)
        self.assertFalse(out['too_tight'])
        self.assertIn('not what is killing them', out['verdict'])

    def test_verdict_is_withheld_without_enough_winners(self):
        out = self._case(winner_mae=5.0, count=3)
        self.assertIsNone(out['too_tight'])
        self.assertIn('not enough to judge', out['verdict'])

    def test_atr_is_reported_when_bars_are_supplied(self):
        trades = [trade(i, entry=4000.0, exit_price=3998.0,
                        opened=MONDAY + (30 + i) * MINUTE)
                  for i in range(1, MIN_STOPS + 1)]
        bars = series(MONDAY, 120, 4000.0, 4001.0)
        out = analyze(trades, [row(mae=5.0, net=5.0) for _ in range(MIN_STOPS)],
                      {'GOLD.i#': bars})
        self.assertIsNotNone(out['atr'])
        self.assertEqual(out['atr']['median_stop_in_atr'], 2.0)   # 2.0 stop / 1.0 range

    def test_atr_is_none_without_bars_and_the_verdict_survives(self):
        out = self._case(winner_mae=5.0)
        self.assertIsNone(out['atr'])
        self.assertTrue(out['too_tight'])

    def test_tolerates_empty_input(self):
        out = analyze([], [])
        self.assertEqual(out['by_symbol'], {})
        self.assertIsNone(out['survival'])
        self.assertIsNone(out['too_tight'])


if __name__ == '__main__':
    unittest.main()
