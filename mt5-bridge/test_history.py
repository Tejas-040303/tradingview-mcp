"""
Tests for the trade-level analytics behind the history view.

These operate on paired trades rather than deals — the difference matters for
holding times, for grouping by entry time, and for anything that must count a
partially closed position once.
"""
import unittest

from analytics import (analyze_trades, filter_trades, pnl_distribution,
                       trade_equity_curve, trade_groups, trade_streaks)

HOUR = 3600
MONDAY = 1_767_571_200   # 2026-01-05 00:00:00 UTC


def trade(position_id=1, symbol='GOLD.i#', direction='long', net=10.0,
          opened=MONDAY, duration=600, exit_reason='take_profit',
          volume=0.01, is_open=False, entry_missing=False, partial_closes=0):
    closed = None if is_open else opened + duration
    return {
        'position_id': position_id, 'symbol': symbol, 'direction': direction,
        'opened_utc': opened, 'opened': '2026-01-05T00:00:00Z',
        'closed_utc': closed, 'closed': '2026-01-05T00:10:00Z' if closed else None,
        'duration_sec': None if is_open else duration,
        'entry_price': None if entry_missing else 4000.0,
        'exit_price': None if is_open else 4010.0,
        'volume': volume, 'net': net, 'exit_reason': None if is_open else exit_reason,
        'deals': 2, 'partial_closes': partial_closes,
        'open': is_open, 'entry_missing': entry_missing,
    }


class TestFilterTrades(unittest.TestCase):
    def test_filters_combine(self):
        rows = [
            trade(1, direction='long', exit_reason='stop_loss', net=-5.0),
            trade(2, direction='short', exit_reason='stop_loss', net=-8.0),
            trade(3, direction='short', exit_reason='take_profit', net=12.0),
        ]
        out = filter_trades(rows, direction='short', exit_reason='stop_loss')
        self.assertEqual([t['position_id'] for t in out], [2])

    def test_net_bounds_are_inclusive(self):
        rows = [trade(1, net=-10.0), trade(2, net=0.0), trade(3, net=10.0)]
        self.assertEqual(len(filter_trades(rows, min_net=0.0)), 2)
        self.assertEqual(len(filter_trades(rows, max_net=0.0)), 2)
        self.assertEqual(len(filter_trades(rows, min_net=-10.0, max_net=10.0)), 3)

    def test_open_trades_survive_a_net_filter(self):
        # An open trade has no realised result; a P&L bound must not silently
        # discard it as if it had lost nothing.
        rows = [trade(1, net=0.0, is_open=True), trade(2, net=-5.0)]
        out = filter_trades(rows, min_net=1.0)
        self.assertEqual([t['position_id'] for t in out], [1])

    def test_include_open_false_drops_them(self):
        rows = [trade(1, is_open=True), trade(2)]
        self.assertEqual(len(filter_trades(rows, include_open=False)), 1)

    def test_symbol_filter_is_exact(self):
        rows = [trade(1, symbol='GOLD.i#'), trade(2, symbol='GOLD')]
        self.assertEqual(len(filter_trades(rows, symbol='GOLD')), 1)

    def test_no_filters_returns_everything(self):
        rows = [trade(1), trade(2)]
        self.assertEqual(len(filter_trades(rows)), 2)
        self.assertEqual(filter_trades(None), [])


class TestTradeEquityCurve(unittest.TestCase):
    def test_accumulates_in_close_order(self):
        rows = [trade(1, net=10.0, opened=MONDAY + HOUR, duration=60),
                trade(2, net=-4.0, opened=MONDAY, duration=60)]
        curve = trade_equity_curve(rows)
        self.assertEqual([p['cumulative'] for p in curve], [-4.0, 6.0])

    def test_balance_only_when_a_starting_point_is_given(self):
        curve = trade_equity_curve([trade(net=5.0)], starting_balance=100.0)
        self.assertEqual(curve[0]['balance'], 105.0)
        self.assertNotIn('balance', trade_equity_curve([trade(net=5.0)])[0])

    def test_open_trades_are_not_plotted(self):
        self.assertEqual(trade_equity_curve([trade(is_open=True)]), [])

    def test_one_point_per_trade_not_per_fill(self):
        # A position closed in three parts is one step on the curve.
        curve = trade_equity_curve([trade(net=9.0, partial_closes=2)])
        self.assertEqual(len(curve), 1)


class TestTradeGroups(unittest.TestCase):
    def test_groups_by_exit_reason(self):
        rows = [trade(1, exit_reason='stop_loss', net=-5.0),
                trade(2, exit_reason='stop_loss', net=-3.0),
                trade(3, exit_reason='take_profit', net=9.0)]
        out = trade_groups(rows, 'exit_reason')
        self.assertEqual(out['stop_loss']['trades'], 2)
        self.assertEqual(out['stop_loss']['net'], -8.0)
        self.assertEqual(out['take_profit']['win_rate_pct'], 100.0)

    def test_session_uses_entry_time_not_exit(self):
        # Opened 11:30 UTC (london), closed 12:30 (overlap). The decision was
        # made in London, so that is where it belongs.
        rows = [trade(opened=MONDAY + 11 * HOUR + 1800, duration=HOUR)]
        self.assertEqual(list(trade_groups(rows, 'session')), ['london'])

    def test_groups_by_hour_and_weekday(self):
        rows = [trade(opened=MONDAY + 9 * HOUR)]
        self.assertEqual(list(trade_groups(rows, 'hour')), ['9'])
        self.assertEqual(list(trade_groups(rows, 'weekday')), ['monday'])

    def test_open_trades_are_excluded(self):
        self.assertEqual(trade_groups([trade(is_open=True)], 'symbol'), {})

    def test_unknown_key_is_refused(self):
        with self.assertRaises(ValueError):
            trade_groups([trade()], 'nonsense')

    def test_missing_label_becomes_unknown(self):
        rows = [trade(exit_reason=None)]
        self.assertIn('unknown', trade_groups(rows, 'exit_reason'))


class TestTradeStreaks(unittest.TestCase):
    def test_longest_runs_in_close_order(self):
        rows = [trade(i, net=n, opened=MONDAY + i * HOUR, duration=60)
                for i, n in enumerate([-1.0, -2.0, -3.0, 5.0, 6.0], start=1)]
        out = trade_streaks(rows)
        self.assertEqual(out['longest_loss_streak'], 3)
        self.assertEqual(out['longest_win_streak'], 2)
        self.assertEqual(out['current_streak_kind'], 'win')
        self.assertEqual(out['current_streak'], 2)

    def test_breakeven_trades_do_not_break_a_run(self):
        rows = [trade(i, net=n, opened=MONDAY + i * HOUR, duration=60)
                for i, n in enumerate([-1.0, 0.0, -1.0], start=1)]
        self.assertEqual(trade_streaks(rows)['longest_loss_streak'], 2)

    def test_empty(self):
        self.assertEqual(trade_streaks([])['longest_win_streak'], 0)


class TestPnlDistribution(unittest.TestCase):
    def test_every_trade_lands_in_exactly_one_bucket(self):
        rows = [trade(i, net=float(n)) for i, n in enumerate(range(-20, 21), start=1)]
        buckets = pnl_distribution(rows)
        self.assertEqual(sum(b['count'] for b in buckets), len(rows))

    def test_no_bucket_straddles_zero(self):
        rows = [trade(i, net=float(n)) for i, n in enumerate([-9, -4, -1, 2, 7], start=1)]
        for bucket in pnl_distribution(rows):
            self.assertFalse(bucket['from'] < 0 < bucket['to'],
                             f'bucket {bucket} mixes wins and losses')

    def test_identical_results_collapse_to_one_bucket(self):
        rows = [trade(1, net=-5.0), trade(2, net=-5.0)]
        buckets = pnl_distribution(rows)
        self.assertEqual(len(buckets), 1)
        self.assertEqual(buckets[0]['count'], 2)

    def test_all_losses_needs_no_winning_bucket(self):
        rows = [trade(i, net=float(-n)) for i, n in enumerate([1, 5, 9], start=1)]
        buckets = pnl_distribution(rows)
        self.assertEqual(sum(b['count'] for b in buckets), 3)
        self.assertTrue(all(b['from'] <= 0 for b in buckets))

    def test_open_trades_are_excluded(self):
        self.assertEqual(pnl_distribution([trade(is_open=True)]), [])
        self.assertEqual(pnl_distribution([]), [])


class TestAnalyzeTrades(unittest.TestCase):
    def setUp(self):
        self.rows = [
            trade(1, net=10.0, opened=MONDAY, duration=60, exit_reason='take_profit'),
            trade(2, net=-5.0, opened=MONDAY + HOUR, duration=600, exit_reason='stop_loss'),
            trade(3, net=-5.0, opened=MONDAY + 2 * HOUR, duration=600, exit_reason='stop_loss'),
        ]

    def test_headline_carries_expectancy_and_profit_factor(self):
        out = analyze_trades(self.rows)
        head = out['headline']
        self.assertEqual(head['trades'], 3)
        self.assertEqual(head['net'], 0.0)
        self.assertEqual(head['expectancy'], 0.0)
        self.assertEqual(head['profit_factor'], 1.0)     # 10 won / 10 lost
        self.assertEqual(head['payoff_ratio'], 2.0)      # avg win 10 / avg loss 5
        self.assertEqual(head['best'], 10.0)
        self.assertEqual(head['worst'], -5.0)

    def test_holding_times_survive_into_the_headline(self):
        head = analyze_trades(self.rows)['headline']
        self.assertEqual(head['avg_win_duration_sec'], 60)
        self.assertEqual(head['avg_loss_duration_sec'], 600)

    def test_profit_factor_is_none_rather_than_infinite(self):
        head = analyze_trades([trade(net=5.0)])['headline']
        self.assertIsNone(head['profit_factor'])

    def test_drawdown_percent_needs_a_starting_balance(self):
        self.assertIsNone(analyze_trades(self.rows)['drawdown']['max_drawdown_pct'])
        with_balance = analyze_trades(self.rows, starting_balance=100.0)
        self.assertIsNotNone(with_balance['drawdown']['max_drawdown_pct'])

    def test_drawdown_measures_the_peak_to_trough_fall(self):
        out = analyze_trades(self.rows)
        # +10 then -5 then -5: peak 10, trough 0.
        self.assertEqual(out['drawdown']['max_drawdown'], 10.0)

    def test_requested_groups_are_the_ones_returned(self):
        out = analyze_trades(self.rows, group_by=('exit_reason', 'weekday'))
        self.assertEqual(set(out['groups']), {'exit_reason', 'weekday'})

    def test_no_closed_trades_gives_a_null_headline_not_zeros(self):
        out = analyze_trades([trade(is_open=True)])
        self.assertIsNone(out['headline'])
        self.assertEqual(out['equity_curve'], [])
        self.assertEqual(out['distribution'], [])

    def test_tolerates_empty_input(self):
        out = analyze_trades([])
        self.assertIsNone(out['headline'])
        self.assertEqual(out['drawdown']['max_drawdown'], 0.0)


if __name__ == '__main__':
    unittest.main()
