"""
Tests for pairing MetaTrader 5 deals into trades.

MT5 reports fills, not trades: an open and a close are separate rows sharing a
position_id, and a partial close adds more. These cover the grouping and the
edge cases that make it non-obvious — windows starting mid-position, positions
still open, partial closes, and reversals.
"""
import unittest

from analytics import pair_trades, summarize_trades

HOUR = 3600
MONDAY = 1_767_571_200  # 2026-01-05 00:00:00 UTC


def fill(position_id=1, entry='in', dtype='buy', price=4000.0, volume=0.01,
         at=MONDAY, profit=0.0, reason='client', symbol='GOLD.i#', **extra):
    return {
        'position_id': position_id, 'symbol': symbol, 'entry': entry,
        'type': dtype, 'price': price, 'volume': volume, 'profit': profit,
        'commission': 0.0, 'swap': 0.0, 'fee': 0.0, 'reason': reason,
        'time_utc': at, 'time_msc_utc': at * 1000, **extra,
    }


class TestPairTrades(unittest.TestCase):
    def test_pairs_an_open_and_a_close(self):
        trades = pair_trades([
            fill(entry='in', dtype='buy', price=4000.0, at=MONDAY),
            fill(entry='out', dtype='sell', price=4010.0, at=MONDAY + HOUR, profit=10.0),
        ])
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual(t['direction'], 'long')
        self.assertEqual(t['entry_price'], 4000.0)
        self.assertEqual(t['exit_price'], 4010.0)
        self.assertEqual(t['duration_sec'], HOUR)
        self.assertEqual(t['net'], 10.0)
        self.assertFalse(t['open'])

    def test_a_sell_entry_is_a_short(self):
        trades = pair_trades([
            fill(entry='in', dtype='sell', at=MONDAY),
            fill(entry='out', dtype='buy', at=MONDAY + 60, profit=-2.0),
        ])
        self.assertEqual(trades[0]['direction'], 'short')

    def test_separate_positions_stay_separate(self):
        trades = pair_trades([
            fill(position_id=1, entry='in'), fill(position_id=1, entry='out', profit=1.0),
            fill(position_id=2, entry='in'), fill(position_id=2, entry='out', profit=-1.0),
        ])
        self.assertEqual(len(trades), 2)
        self.assertEqual({t['position_id'] for t in trades}, {1, 2})

    def test_partial_closes_roll_into_one_trade(self):
        trades = pair_trades([
            fill(entry='in', volume=0.10, price=4000.0, at=MONDAY),
            fill(entry='out', volume=0.05, price=4010.0, at=MONDAY + 60, profit=5.0),
            fill(entry='out', volume=0.05, price=4020.0, at=MONDAY + 120, profit=10.0),
        ])
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual(t['partial_closes'], 1)
        self.assertEqual(t['net'], 15.0)
        # Volume-weighted across both exits.
        self.assertEqual(t['exit_price'], 4015.0)
        self.assertEqual(t['duration_sec'], 120)

    def test_entry_price_is_volume_weighted_across_scale_ins(self):
        trades = pair_trades([
            fill(entry='in', volume=0.01, price=4000.0, at=MONDAY),
            fill(entry='in', volume=0.03, price=4100.0, at=MONDAY + 60),
            fill(entry='out', volume=0.04, price=4200.0, at=MONDAY + 120, profit=8.0),
        ])
        self.assertEqual(trades[0]['entry_price'], 4075.0)
        self.assertEqual(trades[0]['volume'], 0.04)

    def test_a_still_open_position_has_no_exit(self):
        trades = pair_trades([fill(entry='in', at=MONDAY)])
        t = trades[0]
        self.assertTrue(t['open'])
        self.assertIsNone(t['exit_price'])
        self.assertIsNone(t['duration_sec'])
        self.assertIsNone(t['closed_utc'])

    def test_open_positions_can_be_excluded(self):
        deals = [fill(position_id=1, entry='in'),
                 fill(position_id=2, entry='in'), fill(position_id=2, entry='out')]
        self.assertEqual(len(pair_trades(deals, include_open=False)), 1)

    def test_a_close_without_its_open_is_flagged_not_invented(self):
        # A window starting mid-position: the entry genuinely predates the data.
        trades = pair_trades([fill(entry='out', dtype='sell', price=4010.0, profit=5.0)])
        t = trades[0]
        self.assertTrue(t['entry_missing'])
        self.assertIsNone(t['entry_price'])
        self.assertIsNone(t['duration_sec'])
        # Direction is still recoverable: a sell close means it was a long.
        self.assertEqual(t['direction'], 'long')

    def test_exit_reason_comes_from_the_final_close(self):
        trades = pair_trades([
            fill(entry='in', at=MONDAY),
            fill(entry='out', at=MONDAY + 60, reason='mobile', profit=2.0),
            fill(entry='out', at=MONDAY + 120, reason='stop_loss', profit=-5.0),
        ])
        self.assertEqual(trades[0]['exit_reason'], 'stop_loss')

    def test_costs_are_included_in_net(self):
        trades = pair_trades([
            fill(entry='in', commission=-0.5),
            fill(entry='out', profit=10.0, commission=-0.5, swap=-1.0),
        ])
        self.assertEqual(trades[0]['net'], 8.0)

    def test_non_trade_rows_are_ignored(self):
        trades = pair_trades([
            fill(entry='in'), fill(entry='out', profit=1.0),
            {'type': 'balance', 'entry': 'in', 'position_id': 99, 'profit': 500.0},
        ])
        self.assertEqual(len(trades), 1)

    def test_deals_without_a_position_id_are_skipped(self):
        self.assertEqual(pair_trades([fill(position_id=None), fill(position_id=0)]), [])

    def test_trades_are_ordered_by_open_time(self):
        trades = pair_trades([
            fill(position_id=2, entry='in', at=MONDAY + HOUR),
            fill(position_id=1, entry='in', at=MONDAY),
        ])
        self.assertEqual([t['position_id'] for t in trades], [1, 2])

    def test_tolerates_empty_input(self):
        self.assertEqual(pair_trades([]), [])
        self.assertEqual(pair_trades(None), [])


class TestSummarizeTrades(unittest.TestCase):
    def test_holding_times_separate_winners_from_losers(self):
        # The question deals alone cannot answer: are losers held longer?
        trades = pair_trades([
            fill(position_id=1, entry='in', at=MONDAY),
            fill(position_id=1, entry='out', at=MONDAY + 60, profit=5.0),
            fill(position_id=2, entry='in', at=MONDAY),
            fill(position_id=2, entry='out', at=MONDAY + 600, profit=-5.0),
        ])
        out = summarize_trades(trades)
        self.assertEqual(out['avg_win_duration_sec'], 60)
        self.assertEqual(out['avg_loss_duration_sec'], 600)

    def test_counts_and_win_rate(self):
        trades = pair_trades([
            fill(position_id=1, entry='in'), fill(position_id=1, entry='out', profit=5.0),
            fill(position_id=2, entry='in'), fill(position_id=2, entry='out', profit=-2.0),
        ])
        out = summarize_trades(trades)
        self.assertEqual(out['trades'], 2)
        self.assertEqual(out['win_rate_pct'], 50.0)
        self.assertEqual(out['net'], 3.0)

    def test_open_trades_are_counted_but_not_scored(self):
        trades = pair_trades([
            fill(position_id=1, entry='in'), fill(position_id=1, entry='out', profit=5.0),
            fill(position_id=2, entry='in'),
        ])
        out = summarize_trades(trades)
        self.assertEqual(out['trades'], 1)
        self.assertEqual(out['open_trades'], 1)

    def test_reports_how_many_entries_were_missing(self):
        trades = pair_trades([fill(entry='out', profit=1.0)])
        self.assertEqual(summarize_trades(trades)['entry_missing'], 1)

    def test_no_closed_trades_is_none(self):
        self.assertIsNone(summarize_trades(pair_trades([fill(entry='in')])))
        self.assertIsNone(summarize_trades([]))


if __name__ == '__main__':
    unittest.main()
