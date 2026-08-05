"""
Tests for post-trade excursion analysis.

The direction handling is where this gets subtle: for a long, adverse means
price fell; for a short, it means price rose. Most of these fix that, plus the
refusals — a trade the bars do not cover produces nothing rather than zeros.
"""
import unittest

from excursion import (DEFAULT_HORIZONS, MIN_RELIABLE, compute,
                       excursion_for_trade, required_window, summarize)

MINUTE = 60
MONDAY = 1_767_571_200   # 2026-01-05 00:00:00 UTC


def bar(at, o, h, l, c):
    return {'time_utc': at, 'open': o, 'high': h, 'low': l, 'close': c}


def flat_bars(start, count, price, step=MINUTE):
    return [bar(start + i * step, price, price, price, price) for i in range(count)]


def trade(direction='long', entry=4000.0, exit_price=4010.0, opened=MONDAY,
          closed=MONDAY + 10 * MINUTE, exit_reason='take_profit', net=10.0,
          position_id=1, symbol='GOLD.i#', is_open=False):
    return {
        'position_id': position_id, 'symbol': symbol, 'direction': direction,
        'entry_price': entry, 'exit_price': None if is_open else exit_price,
        'opened_utc': opened, 'closed_utc': None if is_open else closed,
        'exit_reason': None if is_open else exit_reason, 'net': net,
        'open': is_open,
    }


class TestExcursionDirection(unittest.TestCase):
    def test_long_measures_adverse_downward(self):
        bars = [bar(MONDAY, 4000, 4020, 3990, 4010),
                bar(MONDAY + MINUTE, 4010, 4015, 4005, 4010)]
        row = excursion_for_trade(trade(direction='long', entry=4000.0,
                                        exit_price=4010.0,
                                        closed=MONDAY + MINUTE), bars)
        self.assertEqual(row['mae'], 10.0)    # dipped to 3990
        self.assertEqual(row['mfe'], 20.0)    # ran to 4020
        self.assertEqual(row['realised_move'], 10.0)

    def test_short_measures_adverse_upward(self):
        bars = [bar(MONDAY, 4000, 4020, 3990, 3995),
                bar(MONDAY + MINUTE, 3995, 4000, 3980, 3990)]
        row = excursion_for_trade(trade(direction='short', entry=4000.0,
                                        exit_price=3990.0,
                                        closed=MONDAY + MINUTE), bars)
        self.assertEqual(row['mae'], 20.0)    # rose to 4020 against a short
        self.assertEqual(row['mfe'], 20.0)    # fell to 3980
        self.assertEqual(row['realised_move'], 10.0)

    def test_excursions_are_never_negative(self):
        # Price only ever moved in favour: adverse excursion is zero, not a
        # favourable move with the sign flipped.
        bars = [bar(MONDAY, 4000, 4030, 4000, 4025)]
        row = excursion_for_trade(trade(closed=MONDAY), bars)
        self.assertEqual(row['mae'], 0.0)
        self.assertGreater(row['mfe'], 0)

    def test_capture_ratio_compares_taken_to_available(self):
        bars = [bar(MONDAY, 4000, 4020, 4000, 4010)]
        row = excursion_for_trade(trade(exit_price=4010.0, closed=MONDAY), bars)
        self.assertEqual(row['capture_ratio'], 0.5)   # took 10 of 20

    def test_ratios_are_none_when_nothing_went_your_way(self):
        bars = [bar(MONDAY, 4000, 4000, 3980, 3990)]
        row = excursion_for_trade(trade(exit_price=3990.0, closed=MONDAY), bars)
        self.assertIsNone(row['capture_ratio'])
        self.assertIsNone(row['heat_ratio'])


class TestPostExit(unittest.TestCase):
    def test_records_where_price_went_after_leaving(self):
        during = [bar(MONDAY, 4000, 4005, 3995, 3995)]
        after = [bar(MONDAY + i * MINUTE, 3995, 3995 + i, 3995, 3995 + i)
                 for i in range(1, 12)]
        row = excursion_for_trade(
            trade(entry=4000.0, exit_price=3995.0, closed=MONDAY,
                  exit_reason='stop_loss', net=-5.0), during + after)
        post = row['post_exit']['300']
        self.assertIsNotNone(post)
        self.assertGreater(post['move'], 0)     # kept rising after a long exit

    def test_horizon_with_no_bars_is_none_not_zero(self):
        # A trade that closed moments ago has no hour of data yet. Reporting 0
        # would drag every average toward "price did nothing".
        bars = [bar(MONDAY, 4000, 4005, 3995, 4000),
                bar(MONDAY + MINUTE, 4000, 4005, 3995, 4000)]
        row = excursion_for_trade(trade(closed=MONDAY + MINUTE), bars)
        self.assertIsNone(row['post_exit']['3600'])

    def test_reached_entry_detects_the_shakeout(self):
        # Stopped out of a long at 3990, price back through 4000 four minutes on.
        during = [bar(MONDAY, 4000, 4001, 3990, 3990)]
        after = ([bar(MONDAY + MINUTE, 3990, 3995, 3990, 3995),
                  bar(MONDAY + 2 * MINUTE, 3995, 4005, 3995, 4002)]
                 + flat_bars(MONDAY + 3 * MINUTE, 4, 4002))
        row = excursion_for_trade(
            trade(entry=4000.0, exit_price=3990.0, closed=MONDAY,
                  exit_reason='stop_loss', net=-10.0), during + after)
        self.assertTrue(row['post_exit']['300']['reached_entry'])
        self.assertEqual(row['returned_to_entry_sec'], 2 * MINUTE)

    def test_shakeout_is_not_asked_of_a_winning_exit(self):
        # A profitable exit already sits beyond the entry, so "price reached the
        # entry" is trivially true forever after. Reporting it would give
        # winners a vacuous 100% next to the stop-loss figure that means
        # something, and rank above it.
        during = [bar(MONDAY, 4000, 4012, 4000, 4010)]
        after = flat_bars(MONDAY + MINUTE, 10, 4011)
        row = excursion_for_trade(
            trade(entry=4000.0, exit_price=4010.0, closed=MONDAY,
                  exit_reason='take_profit', net=10.0), during + after)
        self.assertFalse(row['shakeout_applicable'])
        self.assertIsNone(row['returned_to_entry_sec'])
        self.assertIsNone(row['post_exit']['300']['reached_entry'])

    def test_shakeout_rate_ignores_winners_in_the_denominator(self):
        during = [bar(MONDAY, 4000, 4001, 3990, 3990)]
        loser = excursion_for_trade(
            trade(position_id=1, entry=4000.0, exit_price=3990.0, closed=MONDAY,
                  exit_reason='stop_loss', net=-10.0),
            during + flat_bars(MONDAY + MINUTE, 8, 3985))
        winner = excursion_for_trade(
            trade(position_id=2, entry=4000.0, exit_price=4010.0, closed=MONDAY,
                  exit_reason='stop_loss', net=10.0),
            [bar(MONDAY, 4000, 4012, 4000, 4010)] + flat_bars(MONDAY + MINUTE, 8, 4011))
        stats = summarize([loser, winner])['by_exit_reason']['stop_loss']
        # Two trades, one askable, and it did not come back: 0%, not 50%.
        self.assertEqual(stats['shakeout_samples'], 1)
        self.assertEqual(stats['returned_to_entry_pct'], 0.0)

    def test_return_to_entry_is_none_when_it_never_comes_back(self):
        during = [bar(MONDAY, 4000, 4001, 3990, 3990)]
        after = flat_bars(MONDAY + MINUTE, 70, 3985)
        row = excursion_for_trade(
            trade(entry=4000.0, exit_price=3990.0, closed=MONDAY), during + after)
        self.assertIsNone(row['returned_to_entry_sec'])

    def test_short_return_to_entry_is_a_fall_not_a_rise(self):
        during = [bar(MONDAY, 4000, 4010, 4000, 4010)]
        after = [bar(MONDAY + MINUTE, 4010, 4010, 3999, 4000)]
        row = excursion_for_trade(
            trade(direction='short', entry=4000.0, exit_price=4010.0,
                  closed=MONDAY, net=-10.0), during + after)
        self.assertEqual(row['returned_to_entry_sec'], MINUTE)


class TestUnitSafety(unittest.TestCase):
    """Price distances must not be averaged across instruments."""

    def _mixed(self):
        gold = excursion_for_trade(
            trade(position_id=1, symbol='GOLD.i#', entry=4000.0,
                  exit_price=4010.0, closed=MONDAY),
            [bar(MONDAY, 4000, 4020, 3990, 4010)])
        btc = excursion_for_trade(
            trade(position_id=2, symbol='BTCUSD#', entry=90000.0,
                  exit_price=90500.0, closed=MONDAY),
            [bar(MONDAY, 90000, 91000, 89000, 90500)])
        return [gold, btc]

    def test_price_stats_are_withheld_when_symbols_are_mixed(self):
        # Averaging gold points with bitcoin dollars produced a 17.2 "average"
        # adverse excursion on a real account — a number with no unit.
        overall = summarize(self._mixed())['overall']
        self.assertIsNone(overall['avg_mae'])
        self.assertIsNone(overall['avg_mfe'])
        self.assertEqual(overall['symbols'], 2)
        self.assertIn('cannot be averaged', overall['price_stats_note'])

    def test_per_symbol_buckets_keep_their_price_stats(self):
        out = summarize(self._mixed())['by_symbol']
        self.assertEqual(set(out), {'GOLD.i#', 'BTCUSD#'})
        self.assertIsNotNone(out['GOLD.i#']['avg_mae'])
        self.assertIsNotNone(out['BTCUSD#']['avg_mae'])
        self.assertNotIn('price_stats_note', out['GOLD.i#'])

    def test_capture_ratio_uses_a_median_not_a_mean(self):
        # A trade that offered almost nothing and lost contributes an enormous
        # negative ratio. The mean read -5.9 on real data purely from those.
        rows = []
        for i in range(1, 10):
            rows.append(excursion_for_trade(
                trade(position_id=i, entry=4000.0, exit_price=4010.0,
                      closed=MONDAY, opened=MONDAY),
                [bar(MONDAY, 4000, 4020, 4000, 4010)]))
        rows.append(excursion_for_trade(
            trade(position_id=99, entry=4000.0, exit_price=3995.0, closed=MONDAY,
                  net=-5.0),
            [bar(MONDAY, 4000, 4000.01, 3990, 3995)]))
        stats = summarize(rows)['overall']
        # Nine trades captured 0.5; one outlier is around -500. The median holds.
        self.assertEqual(stats['median_capture_ratio'], 0.5)


class TestRelevantHorizon(unittest.TestCase):
    def test_horizon_follows_the_median_holding_time(self):
        # A fifteen-minute style should be read at the fifteen-minute horizon;
        # asking "did price come back within five" understates the shakeout.
        rows = [excursion_for_trade(
            trade(position_id=i, closed=MONDAY + 15 * MINUTE,
                  opened=MONDAY + (i - 1) * 3600),
            [bar(MONDAY + (i - 1) * 3600, 4000, 4020, 3990, 4010)])
            for i in range(1, 3)]
        rows = [r for r in rows if r]
        out = summarize(rows)
        self.assertEqual(out['relevant_horizon'], 900)

    def test_falls_back_to_the_shortest_horizon_without_durations(self):
        row = excursion_for_trade(trade(closed=MONDAY, opened=MONDAY),
                                  [bar(MONDAY, 4000, 4020, 3990, 4010)])
        self.assertEqual(summarize([row])['relevant_horizon'], 300)


class TestRefusals(unittest.TestCase):
    def test_open_trades_produce_nothing(self):
        self.assertIsNone(excursion_for_trade(trade(is_open=True),
                                              flat_bars(MONDAY, 5, 4000)))

    def test_bars_that_do_not_cover_the_trade_produce_nothing(self):
        far_away = flat_bars(MONDAY + 30 * 86400, 10, 4000)
        self.assertIsNone(excursion_for_trade(trade(), far_away))

    def test_missing_entry_price_produces_nothing(self):
        row = trade()
        row['entry_price'] = None       # window started mid-position
        self.assertIsNone(excursion_for_trade(row, flat_bars(MONDAY, 20, 4000)))

    def test_no_bars_at_all(self):
        self.assertIsNone(excursion_for_trade(trade(), []))


class TestCompute(unittest.TestCase):
    def test_matches_trades_to_their_own_symbol(self):
        bars = {'GOLD.i#': [bar(MONDAY, 4000, 4020, 3990, 4010)]}
        rows = compute([trade(position_id=1, symbol='GOLD.i#', closed=MONDAY),
                        trade(position_id=2, symbol='EURUSD', closed=MONDAY)], bars)
        self.assertEqual([r['position_id'] for r in rows], [1])

    def test_tolerates_empty_input(self):
        self.assertEqual(compute([], {}), [])
        self.assertEqual(compute(None, None), [])


class TestRequiredWindow(unittest.TestCase):
    def test_spans_all_trades_per_symbol_and_pads_for_the_horizon(self):
        trades = [trade(position_id=1, opened=MONDAY, closed=MONDAY + 600),
                  trade(position_id=2, opened=MONDAY + 5000, closed=MONDAY + 6000)]
        window = required_window(trades)
        start, end = window['GOLD.i#']
        self.assertEqual(start, MONDAY)
        self.assertEqual(end, MONDAY + 6000 + max(DEFAULT_HORIZONS))

    def test_open_trades_are_not_part_of_the_window(self):
        self.assertIsNone(required_window([trade(is_open=True)]))
        self.assertIsNone(required_window([]))


class TestSummarize(unittest.TestCase):
    def _stopped_out(self, count, came_back):
        """`count` stop-outs, of which `came_back` see price return to entry."""
        rows = []
        for i in range(count):
            opened = MONDAY + i * 3600
            during = [bar(opened, 4000, 4001, 3990, 3990)]
            after = ([bar(opened + MINUTE, 3990, 4005, 3990, 4002)]
                     if i < came_back else flat_bars(opened + MINUTE, 8, 3985))
            row = excursion_for_trade(
                trade(position_id=i + 1, entry=4000.0, exit_price=3990.0,
                      opened=opened, closed=opened, exit_reason='stop_loss',
                      net=-10.0),
                during + after)
            rows.append(row)
        return rows

    def test_reports_the_shakeout_rate_for_stop_outs(self):
        rows = self._stopped_out(10, came_back=7)
        out = summarize(rows)
        self.assertEqual(out['by_exit_reason']['stop_loss']['returned_to_entry_pct'], 70.0)
        self.assertEqual(out['by_exit_reason']['stop_loss']['post_300']['reached_entry_pct'], 70.0)

    def test_thin_buckets_are_flagged(self):
        self.assertFalse(summarize(self._stopped_out(3, 1))['overall']['reliable'])
        self.assertTrue(summarize(self._stopped_out(MIN_RELIABLE, 1))['overall']['reliable'])

    def test_splits_by_exit_reason(self):
        bars = [bar(MONDAY, 4000, 4020, 3990, 4010)]
        rows = [excursion_for_trade(trade(position_id=1, closed=MONDAY,
                                          exit_reason='stop_loss'), bars),
                excursion_for_trade(trade(position_id=2, closed=MONDAY,
                                          exit_reason='mobile'), bars)]
        out = summarize(rows)
        self.assertEqual(set(out['by_exit_reason']), {'stop_loss', 'mobile'})

    def test_no_rows_is_none(self):
        self.assertIsNone(summarize([]))
        self.assertIsNone(summarize(None))


if __name__ == '__main__':
    unittest.main()
