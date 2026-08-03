"""
Tests for trade-history analytics.

Pure functions, so no terminal and no network. Fixtures use the decoded deal
shape mt5_client.deals() emits.
"""
import unittest
from datetime import datetime, timezone

from analytics import (
    analyze,
    period_bounds,
    realized_pnl,
    closed_trades,
    equity_curve,
    expectancy,
    group_performance,
    max_drawdown,
    session_of,
    streaks,
    weekday_of,
)

HOUR = 3600
# 2026-01-05 00:00:00 UTC — a Monday, so weekday assertions are readable.
MONDAY = 1_767_571_200


def deal(profit=0.0, at=MONDAY, symbol='GOLD.i#', reason='stop_loss',
         entry='out', dtype='buy', volume=0.01, **extra):
    return {
        'ticket': 1, 'symbol': symbol, 'volume': volume, 'profit': profit,
        'commission': 0.0, 'swap': 0.0, 'fee': 0.0,
        'type': dtype, 'entry': entry, 'reason': reason,
        'time_utc': at, **extra,
    }


class TestClosedTrades(unittest.TestCase):
    def test_entries_are_not_trades(self):
        self.assertEqual(len(closed_trades([deal(entry='in'), deal(entry='out')])), 1)

    def test_balance_rows_are_not_trades(self):
        self.assertEqual(len(closed_trades([deal(dtype='balance', entry='in'),
                                            deal()])), 1)

    def test_tolerates_empty(self):
        self.assertEqual(closed_trades(None), [])


class TestSessionAndWeekday(unittest.TestCase):
    def test_sessions_span_the_day(self):
        self.assertEqual(session_of(MONDAY + 3 * HOUR), 'asia')
        self.assertEqual(session_of(MONDAY + 9 * HOUR), 'london')
        self.assertEqual(session_of(MONDAY + 14 * HOUR), 'overlap')
        self.assertEqual(session_of(MONDAY + 18 * HOUR), 'new_york')
        self.assertEqual(session_of(MONDAY + 22 * HOUR), 'late')

    def test_unknown_time_has_no_session(self):
        self.assertIsNone(session_of(None))
        self.assertIsNone(weekday_of(None))

    def test_weekday_name(self):
        self.assertEqual(weekday_of(MONDAY), 'monday')
        self.assertEqual(weekday_of(MONDAY + 4 * 86400), 'friday')


class TestEquityCurve(unittest.TestCase):
    def test_accumulates_in_chronological_order(self):
        curve = equity_curve([deal(profit=5.0, at=MONDAY + 60),
                              deal(profit=-2.0, at=MONDAY)])
        self.assertEqual([p['cumulative'] for p in curve], [-2.0, 3.0])

    def test_includes_costs_in_each_point(self):
        curve = equity_curve([deal(profit=10.0, commission=-1.0, swap=-0.5)])
        self.assertEqual(curve[0]['profit'], 8.5)

    def test_balance_only_when_a_starting_point_is_known(self):
        self.assertNotIn('balance', equity_curve([deal(profit=5.0)])[0])
        self.assertEqual(equity_curve([deal(profit=5.0)], starting_balance=100)[0]['balance'], 105.0)

    def test_deals_without_utc_time_are_dropped_not_guessed(self):
        curve = equity_curve([deal(profit=5.0), deal(profit=99.0, time_utc=None)])
        self.assertEqual(len(curve), 1)

    def test_empty_history(self):
        self.assertEqual(equity_curve([]), [])


class TestMaxDrawdown(unittest.TestCase):
    def test_measures_peak_to_trough(self):
        curve = equity_curve([deal(profit=10.0, at=MONDAY),
                              deal(profit=-4.0, at=MONDAY + 60),
                              deal(profit=-3.0, at=MONDAY + 120)])
        self.assertEqual(max_drawdown(curve)['max_drawdown'], 7.0)

    def test_percentage_requires_a_balance_base(self):
        deals = [deal(profit=10.0, at=MONDAY), deal(profit=-5.0, at=MONDAY + 60)]
        self.assertIsNone(max_drawdown(equity_curve(deals))['max_drawdown_pct'])
        with_balance = max_drawdown(equity_curve(deals, starting_balance=100))
        self.assertEqual(with_balance['max_drawdown_pct'], 4.55)

    def test_reports_recovery(self):
        curve = equity_curve([deal(profit=10.0, at=MONDAY),
                              deal(profit=-5.0, at=MONDAY + 60),
                              deal(profit=8.0, at=MONDAY + 120)])
        out = max_drawdown(curve)
        self.assertIsNotNone(out['recovered_at'])
        self.assertFalse(out['still_in_drawdown'])

    def test_flags_an_unrecovered_drawdown(self):
        curve = equity_curve([deal(profit=10.0, at=MONDAY),
                              deal(profit=-5.0, at=MONDAY + 60)])
        self.assertTrue(max_drawdown(curve)['still_in_drawdown'])

    def test_a_rising_curve_has_no_drawdown(self):
        curve = equity_curve([deal(profit=1.0, at=MONDAY),
                              deal(profit=2.0, at=MONDAY + 60)])
        self.assertEqual(max_drawdown(curve)['max_drawdown'], 0.0)

    def test_empty_curve(self):
        self.assertEqual(max_drawdown([])['max_drawdown'], 0.0)


class TestGroupPerformance(unittest.TestCase):
    def setUp(self):
        self.deals = [
            deal(profit=5.0, reason='take_profit', at=MONDAY + 9 * HOUR),
            deal(profit=-3.0, reason='stop_loss', at=MONDAY + 9 * HOUR),
            deal(profit=-2.0, reason='stop_loss', at=MONDAY + 18 * HOUR),
            deal(profit=4.0, reason='mobile', symbol='EURUSD', at=MONDAY + 18 * HOUR),
        ]

    def test_groups_by_exit_reason(self):
        out = group_performance(self.deals, 'reason')
        self.assertEqual(out['stop_loss']['trades'], 2)
        self.assertEqual(out['stop_loss']['net'], -5.0)
        self.assertEqual(out['take_profit']['wins'], 1)

    def test_groups_by_session(self):
        out = group_performance(self.deals, 'session')
        self.assertEqual(out['london']['trades'], 2)
        self.assertEqual(out['new_york']['trades'], 2)

    def test_groups_by_symbol(self):
        out = group_performance(self.deals, 'symbol')
        self.assertEqual(set(out), {'GOLD.i#', 'EURUSD'})

    def test_groups_by_hour_and_weekday(self):
        self.assertIn('9', group_performance(self.deals, 'hour'))
        self.assertIn('monday', group_performance(self.deals, 'weekday'))

    def test_unknown_time_buckets_as_unknown(self):
        out = group_performance([deal(profit=1.0, time_utc=None)], 'session')
        self.assertIn('unknown', out)

    def test_rejects_an_unsupported_key(self):
        with self.assertRaises(ValueError):
            group_performance(self.deals, 'phase_of_moon')

    def test_bucket_stats_are_complete(self):
        stats = group_performance(self.deals, 'reason')['stop_loss']
        self.assertEqual(stats['win_rate_pct'], 0.0)
        self.assertEqual(stats['worst'], -3.0)
        self.assertEqual(stats['volume'], 0.02)


class TestStreaks(unittest.TestCase):
    def test_longest_runs(self):
        deals = [deal(profit=p, at=MONDAY + i * 60)
                 for i, p in enumerate([1.0, 2.0, 3.0, -1.0, -2.0, 5.0])]
        out = streaks(deals)
        self.assertEqual(out['longest_win_streak'], 3)
        self.assertEqual(out['longest_loss_streak'], 2)

    def test_current_streak(self):
        deals = [deal(profit=p, at=MONDAY + i * 60) for i, p in enumerate([1.0, -1.0, -2.0])]
        out = streaks(deals)
        self.assertEqual(out['current_streak'], 2)
        self.assertEqual(out['current_streak_kind'], 'loss')

    def test_breakeven_trades_do_not_break_a_run(self):
        deals = [deal(profit=p, at=MONDAY + i * 60) for i, p in enumerate([1.0, 0.0, 2.0])]
        self.assertEqual(streaks(deals)['longest_win_streak'], 2)

    def test_empty_history(self):
        self.assertEqual(streaks([])['longest_win_streak'], 0)


class TestExpectancy(unittest.TestCase):
    def test_negative_expectancy_is_reported_plainly(self):
        # Shaped like the real account: sub-50% win rate and losses larger
        # than wins, which compound rather than offset.
        deals = ([deal(profit=7.0, at=MONDAY + i * 60) for i in range(4)]
                 + [deal(profit=-9.0, at=MONDAY + (10 + i) * 60) for i in range(6)])
        out = expectancy(deals)
        self.assertEqual(out['trades'], 10)
        self.assertEqual(out['win_rate_pct'], 40.0)
        self.assertLess(out['expectancy'], 0)
        self.assertEqual(out['payoff_ratio'], 0.78)

    def test_payoff_ratio_needs_both_sides(self):
        self.assertIsNone(expectancy([deal(profit=5.0)])['payoff_ratio'])

    def test_empty_history(self):
        self.assertEqual(expectancy([])['trades'], 0)


class TestPeriodBounds(unittest.TestCase):
    """Windows for the status view's P&L strip, anchored to UTC."""

    def setUp(self):
        # Wednesday 2026-01-07 15:30 UTC
        self.now = MONDAY + 2 * 86400 + 15 * HOUR + 1800

    def test_today_starts_at_utc_midnight(self):
        start, end = period_bounds(self.now)['today']
        self.assertEqual(start, MONDAY + 2 * 86400)
        self.assertEqual(end, self.now)

    def test_week_starts_on_monday(self):
        start, _ = period_bounds(self.now)['week']
        self.assertEqual(start, MONDAY)
        self.assertEqual(weekday_of(start), 'monday')

    def test_month_starts_on_the_first(self):
        start, _ = period_bounds(self.now)['month']
        self.assertEqual(
            datetime.fromtimestamp(start, tz=timezone.utc).strftime('%Y-%m-%d'),
            '2026-01-01')

    def test_a_monday_is_its_own_week_start(self):
        start, _ = period_bounds(MONDAY + 6 * HOUR)['week']
        self.assertEqual(start, MONDAY)


class TestRealizedPnl(unittest.TestCase):
    def test_sums_only_inside_the_window(self):
        deals = [deal(profit=5.0, at=MONDAY), deal(profit=-3.0, at=MONDAY + 86400)]
        out = realized_pnl(deals, MONDAY, MONDAY + 3600)
        self.assertEqual(out, {'net': 5.0, 'trades': 1})

    def test_includes_costs(self):
        out = realized_pnl([deal(profit=10.0, commission=-1.0, at=MONDAY)],
                           MONDAY - 60, MONDAY + 60)
        self.assertEqual(out['net'], 9.0)

    def test_entries_are_not_counted(self):
        out = realized_pnl([deal(profit=0.0, entry='in', at=MONDAY)], MONDAY - 60, MONDAY + 60)
        self.assertEqual(out['trades'], 0)

    def test_deals_without_utc_time_are_excluded(self):
        out = realized_pnl([deal(profit=5.0, time_utc=None)], 0, 9_999_999_999)
        self.assertEqual(out['trades'], 0)

    def test_empty_window_is_zero_not_an_error(self):
        self.assertEqual(realized_pnl([], MONDAY, MONDAY + 60), {'net': 0.0, 'trades': 0})


class TestAnalyze(unittest.TestCase):
    def test_returns_every_section(self):
        deals = [deal(profit=5.0, at=MONDAY), deal(profit=-2.0, at=MONDAY + 60)]
        out = analyze(deals, starting_balance=100)
        self.assertEqual(set(out), {'expectancy', 'streaks', 'drawdown', 'groups', 'equity_curve'})
        self.assertEqual(set(out['groups']), {'reason', 'session', 'symbol'})
        self.assertEqual(out['equity_curve'][-1]['balance'], 103.0)

    def test_group_selection_is_configurable(self):
        out = analyze([deal(profit=1.0)], group_by=('symbol',))
        self.assertEqual(set(out['groups']), {'symbol'})


if __name__ == '__main__':
    unittest.main()
