"""
Tests for advanced analytics and the coaching rules.

The recurring theme is refusal: these functions must decline to make a claim
when the sample is thin, and must not invent a number when the data cannot
produce one. Most of what is asserted below is an absence.
"""
import unittest

from advanced import (MIN_CELL, MIN_CLAIM, behaviour, daily_pnl, heatmap,
                      holding_time_analysis, kelly_fraction, monte_carlo,
                      recovery_factor, risk_adjusted, size_analysis)
from insights import coverage, generate

HOUR = 3600
MONDAY = 1_767_571_200   # 2026-01-05 00:00:00 UTC


def trade(position_id=1, symbol='GOLD.i#', direction='long', net=10.0,
          opened=MONDAY, duration=600, exit_reason='take_profit',
          volume=0.01, is_open=False):
    closed = None if is_open else opened + duration
    return {
        'position_id': position_id, 'symbol': symbol, 'direction': direction,
        'opened_utc': opened, 'closed_utc': closed,
        'duration_sec': None if is_open else duration,
        'volume': volume, 'net': net,
        'exit_reason': None if is_open else exit_reason,
        'partial_closes': 0, 'open': is_open, 'entry_missing': False,
    }


def many(count, **kw):
    """A run of trades on consecutive hours, so each has a distinct timestamp."""
    return [trade(position_id=i, opened=MONDAY + i * HOUR, **kw)
            for i in range(1, count + 1)]


def same_slot(count, **kw):
    """
    Trades that all land in one heatmap cell — same weekday, same session.

    Consecutive hours would spread across session boundaries, so a run of ten
    lands as six in asia and four in london rather than ten anywhere.
    """
    return [trade(position_id=i, opened=MONDAY + i * 7 * 86400 + 2 * HOUR, **kw)
            for i in range(1, count + 1)]


class TestDailyPnl(unittest.TestCase):
    def test_groups_by_utc_day(self):
        rows = [trade(1, opened=MONDAY, net=5.0),
                trade(2, opened=MONDAY + 2 * HOUR, net=-2.0),
                trade(3, opened=MONDAY + 26 * HOUR, net=7.0)]
        out = daily_pnl(rows)
        self.assertEqual([d['date'] for d in out], ['2026-01-05', '2026-01-06'])
        self.assertEqual(out[0]['net'], 3.0)
        self.assertEqual(out[0]['trades'], 2)

    def test_days_without_trades_are_absent_not_zero(self):
        # Padding flat days would understate volatility in every ratio below.
        rows = [trade(1, opened=MONDAY), trade(2, opened=MONDAY + 5 * 86400)]
        self.assertEqual(len(daily_pnl(rows)), 2)

    def test_open_trades_excluded(self):
        self.assertEqual(daily_pnl([trade(is_open=True)]), [])


class TestRiskAdjusted(unittest.TestCase):
    def test_needs_two_days(self):
        out = risk_adjusted([trade()])
        self.assertIsNone(out['sharpe'])
        self.assertIn('two trading days', out['note'])

    def test_sharpe_is_scale_invariant(self):
        # Doubling every position doubles mean and stdev alike, so the ratio
        # must not move. This is why no account balance is required.
        base = [trade(i, opened=MONDAY + i * 86400, net=n)
                for i, n in enumerate([5.0, -3.0, 8.0, -1.0, 4.0], start=1)]
        doubled = [trade(i, opened=MONDAY + i * 86400, net=n * 2)
                   for i, n in enumerate([5.0, -3.0, 8.0, -1.0, 4.0], start=1)]
        self.assertAlmostEqual(risk_adjusted(base)['sharpe'],
                               risk_adjusted(doubled)['sharpe'], places=2)

    def test_sortino_is_none_without_losing_days(self):
        rows = [trade(i, opened=MONDAY + i * 86400, net=5.0) for i in range(1, 5)]
        self.assertIsNone(risk_adjusted(rows)['sortino'])

    def test_reports_best_and_worst_day(self):
        rows = [trade(i, opened=MONDAY + i * 86400, net=n)
                for i, n in enumerate([5.0, -9.0, 2.0], start=1)]
        out = risk_adjusted(rows)
        self.assertEqual(out['best_day'], 5.0)
        self.assertEqual(out['worst_day'], -9.0)


class TestSimpleRatios(unittest.TestCase):
    def test_recovery_factor_is_none_without_drawdown(self):
        self.assertIsNone(recovery_factor(100.0, 0))
        self.assertEqual(recovery_factor(100.0, 25.0), 4.0)

    def test_kelly_goes_negative_on_a_losing_edge(self):
        # Clamping at zero would hide exactly the case that matters.
        self.assertLess(kelly_fraction(40.0, 0.8), 0)
        self.assertGreater(kelly_fraction(60.0, 2.0), 0)

    def test_kelly_needs_both_inputs(self):
        self.assertIsNone(kelly_fraction(None, 2.0))
        self.assertIsNone(kelly_fraction(50.0, None))


class TestMonteCarlo(unittest.TestCase):
    def test_refuses_a_thin_sample(self):
        out = monte_carlo(many(MIN_CELL - 1, net=1.0))
        self.assertEqual(out['runs'], 0)
        self.assertIn('closed trades', out['note'])

    def test_a_losing_set_almost_always_ends_negative(self):
        out = monte_carlo(many(40, net=-2.0), runs=200)
        self.assertEqual(out['probability_of_loss_pct'], 100.0)

    def test_a_winning_set_almost_never_does(self):
        out = monte_carlo(many(40, net=3.0), runs=200)
        self.assertEqual(out['probability_of_loss_pct'], 0.0)

    def test_is_deterministic_for_a_given_seed(self):
        rows = many(40, net=1.0)
        self.assertEqual(monte_carlo(rows, runs=100)['final_p50'],
                         monte_carlo(rows, runs=100)['final_p50'])

    def test_percentiles_are_ordered(self):
        out = monte_carlo(many(50, net=1.0), runs=200)
        self.assertLessEqual(out['final_p5'], out['final_p50'])
        self.assertLessEqual(out['final_p50'], out['final_p95'])
        self.assertLessEqual(out['drawdown_p50'], out['drawdown_p95'])


class TestHeatmap(unittest.TestCase):
    def test_cells_carry_their_sample_and_a_reliability_flag(self):
        rows = many(3, net=5.0)
        out = heatmap(rows, 'weekday', 'session')
        cell = next(c for line in out['grid'] for c in line if c)
        self.assertEqual(cell['trades'], 3)
        self.assertFalse(cell['reliable'])      # 3 < MIN_CELL

    def test_a_full_bucket_is_marked_reliable(self):
        out = heatmap(same_slot(MIN_CELL, net=5.0), 'weekday', 'session')
        cell = next(c for line in out['grid'] for c in line if c)
        self.assertEqual(cell['trades'], MIN_CELL)
        self.assertTrue(cell['reliable'])

    def test_empty_cells_are_none_not_zero(self):
        # A cell you never traded is not a break-even cell.
        rows = [trade(1, opened=MONDAY + 2 * HOUR),
                trade(2, opened=MONDAY + 4 * 86400 + 18 * HOUR)]
        out = heatmap(rows, 'weekday', 'session')
        self.assertIn(None, [c for line in out['grid'] for c in line])

    def test_weekdays_come_out_in_calendar_order(self):
        rows = [trade(1, opened=MONDAY + 2 * 86400), trade(2, opened=MONDAY)]
        self.assertEqual(heatmap(rows, 'weekday', 'session')['row_labels'],
                         ['monday', 'wednesday'])

    def test_unknown_axis_is_refused(self):
        with self.assertRaises(ValueError):
            heatmap([trade()], 'weekday', 'nonsense')


class TestHoldingTime(unittest.TestCase):
    def test_buckets_are_geometric_and_cover_every_trade(self):
        rows = [trade(i, duration=d, opened=MONDAY + i * HOUR)
                for i, d in enumerate([30, 60, 300, 1800, 7200], start=1)]
        out = holding_time_analysis(rows)
        self.assertEqual(sum(b['trades'] for b in out['buckets']), len(rows))

    def test_the_fastest_and_slowest_trade_are_not_lost_to_float_error(self):
        # Regression: exp(log(30)) came back as 30.000000000000004 and the
        # top edge as 7199.999999999999, dropping both extremes silently.
        rows = [trade(i, duration=d, opened=MONDAY + i * HOUR)
                for i, d in enumerate([30, 7200], start=1)]
        buckets = holding_time_analysis(rows)['buckets']
        self.assertEqual(sum(b['trades'] for b in buckets), 2)

    def test_scatter_has_one_point_per_trade(self):
        self.assertEqual(len(holding_time_analysis(many(6))['scatter']), 6)

    def test_tolerates_no_durations(self):
        out = holding_time_analysis([trade(is_open=True)])
        self.assertEqual(out['buckets'], [])


class TestSizeAnalysis(unittest.TestCase):
    def test_groups_by_volume_with_reliability(self):
        rows = many(3, volume=0.01, net=5.0) + many(2, volume=0.05, net=-3.0)
        out = size_analysis(rows)
        self.assertEqual([b['volume'] for b in out], [0.01, 0.05])
        self.assertFalse(out[0]['reliable'])


class TestBehaviour(unittest.TestCase):
    def test_reports_hold_asymmetry(self):
        rows = [trade(1, net=5.0, duration=60, opened=MONDAY),
                trade(2, net=-5.0, duration=600, opened=MONDAY + HOUR)]
        out = behaviour(rows)
        self.assertEqual(out['hold_asymmetry']['avg_win_sec'], 60)
        self.assertEqual(out['hold_asymmetry']['avg_loss_sec'], 600)

    def test_unmeasurable_traits_are_named_not_scored(self):
        out = behaviour(many(30))
        self.assertIn('fear', out['not_measurable'])
        self.assertNotIn('fear_score', out)

    def test_reentry_timing_splits_by_previous_result(self):
        rows = [
            trade(1, net=-5.0, opened=MONDAY, duration=60),
            trade(2, net=1.0, opened=MONDAY + 120, duration=60),      # 60s after a loss
            trade(3, net=1.0, opened=MONDAY + 900, duration=60),      # 720s after a win
        ]
        out = behaviour(rows)['reentry_timing']
        self.assertEqual(out['after_loss_sec'], 60)
        self.assertEqual(out['after_win_sec'], 720)


class TestInsights(unittest.TestCase):
    def test_says_nothing_below_the_sample_bar(self):
        thin = many(MIN_CLAIM - 1, net=-5.0)
        headline = {'trades': len(thin), 'expectancy': -5.0,
                    'win_rate_pct': 0.0, 'payoff_ratio': 0.5}
        self.assertEqual(generate(thin, headline=headline), [])

    def test_flags_negative_expectancy_once_the_sample_is_there(self):
        rows = many(MIN_CLAIM, net=-5.0)
        headline = {'trades': len(rows), 'expectancy': -5.0,
                    'win_rate_pct': 0.0, 'payoff_ratio': None}
        titles = [i['title'] for i in generate(rows, headline=headline)]
        self.assertIn('Negative expectancy', titles)

    def test_every_insight_carries_its_sample(self):
        rows = many(MIN_CLAIM, net=-5.0)
        headline = {'trades': len(rows), 'expectancy': -5.0,
                    'win_rate_pct': 0.0, 'payoff_ratio': 0.5}
        for insight in generate(rows, headline=headline):
            self.assertGreaterEqual(insight['sample'], MIN_CLAIM)
            self.assertTrue(insight['evidence'])

    def test_bucket_comparison_needs_both_sides_to_qualify(self):
        groups = {'session': {
            'london': {'trades': MIN_CLAIM, 'net': -50.0, 'win_rate_pct': 20.0},
            'asia': {'trades': 2, 'net': 90.0, 'win_rate_pct': 100.0},
        }}
        titles = [i['title'] for i in generate(many(MIN_CLAIM), groups=groups)]
        # asia has 2 trades — it must not be promoted as the best session.
        self.assertNotIn('Best session: asia', titles)

    def test_holding_asymmetry_is_not_claimed_on_a_narrow_gap(self):
        # The exact false positive this project already hit: 16.5m vs 14.8m.
        rows = ([trade(i, net=5.0, duration=990, opened=MONDAY + i * HOUR)
                 for i in range(1, MIN_CLAIM + 1)]
                + [trade(100 + i, net=-5.0, duration=888, opened=MONDAY + (100 + i) * HOUR)
                   for i in range(1, MIN_CLAIM + 1)])
        titles = [i['title'] for i in generate(rows)]
        self.assertNotIn('Losers held longer than winners', titles)
        self.assertNotIn('Winners held longer than losers', titles)

    def test_severity_ordering_puts_critical_first(self):
        rows = many(MIN_CLAIM, net=-5.0)
        headline = {'trades': len(rows), 'expectancy': -5.0,
                    'win_rate_pct': 10.0, 'payoff_ratio': 0.5}
        found = generate(rows, headline=headline,
                         monte=monte_carlo(rows, runs=100))
        self.assertEqual(found[0]['severity'], 'critical')

    def test_stop_heavy_exit_is_reported_without_diagnosing_the_cause(self):
        groups = {'exit_reason': {
            'stop_loss': {'trades': 60, 'net': -300.0, 'win_rate_pct': 20.0},
            'take_profit': {'trades': 20, 'net': 100.0, 'win_rate_pct': 100.0},
        }}
        found = generate(many(MIN_CLAIM), groups=groups)
        stop = next(i for i in found if i['title'] == 'Most trades end at the stop')
        # It must not assert the stops are too tight — that is untestable here.
        self.assertIn('cannot separate', stop['detail'])

    def test_no_data_produces_no_advice(self):
        self.assertEqual(generate([], headline=None), [])


class TestCoverage(unittest.TestCase):
    def test_lists_what_cannot_be_computed_and_why(self):
        out = coverage({'trades': 5})
        self.assertFalse(out['sufficient'])
        reasons = {row['metric']: row['reason'] for row in out['unavailable']}
        self.assertTrue(any('stop-loss' in r.lower() for r in reasons.values()))
        self.assertTrue(all(row['reason'] for row in out['unavailable']))

    def test_sufficient_once_the_bar_is_cleared(self):
        self.assertTrue(coverage({'trades': MIN_CLAIM})['sufficient'])


class TestBridgeWiring(unittest.TestCase):
    """The advanced blocks must be opt-in, so the existing page is untouched."""

    def setUp(self):
        import bridge
        self.bridge = bridge
        self._real = bridge.mt5_client.deals

        def fills(**_):
            rows = []
            for i in range(1, 41):
                at = MONDAY + i * 6 * HOUR
                common = {'symbol': 'GOLD.i#', 'volume': 0.01, 'commission': 0.0,
                          'swap': 0.0, 'fee': 0.0, 'position_id': i}
                rows.append({**common, 'entry': 'in', 'type': 'buy', 'price': 4000.0,
                             'profit': 0.0, 'reason': 'client',
                             'time_utc': at, 'time_msc_utc': at * 1000})
                rows.append({**common, 'entry': 'out', 'type': 'sell', 'price': 4001.0,
                             'profit': -2.0 if i % 3 else 5.0, 'reason': 'stop_loss',
                             'time_utc': at + 600, 'time_msc_utc': (at + 600) * 1000})
            return {'deals': rows,
                    'requested_window_utc': {'from': MONDAY, 'to': MONDAY + 10 ** 7}}

        bridge.mt5_client.deals = fills

    def tearDown(self):
        self.bridge.mt5_client.deals = self._real

    def test_default_response_has_no_advanced_blocks(self):
        out = self.bridge.route('/history', {})
        for key in ('risk', 'monte_carlo', 'heatmaps', 'insights', 'coverage'):
            self.assertNotIn(key, out)

    def test_advanced_adds_them_without_changing_the_base(self):
        plain = self.bridge.route('/history', {})
        rich = self.bridge.route('/history', {'advanced': ['true'], 'mc_runs': ['50']})
        for key in ('risk', 'monte_carlo', 'daily', 'heatmaps', 'holding',
                    'sizes', 'behaviour', 'insights', 'coverage'):
            self.assertIn(key, rich)
        # Backward compatibility: every original field is untouched.
        self.assertEqual(plain['headline'], rich['headline'])
        self.assertEqual(plain['groups'], rich['groups'])

    def test_a_bad_heatmap_spec_reports_instead_of_500ing(self):
        out = self.bridge.route('/history', {'advanced': ['true'], 'mc_runs': ['50'],
                                             'heatmaps': ['weekday:nonsense']})
        self.assertIn('error', out['heatmaps']['weekday:nonsense'])

    def test_insights_are_computed_from_the_filtered_set(self):
        # Filtering to a slice with too few trades must silence the coach
        # rather than leave claims from the unfiltered data standing.
        out = self.bridge.route('/history', {'advanced': ['true'], 'mc_runs': ['50'],
                                             'direction': ['short']})
        self.assertEqual(out['insights'], [])


if __name__ == '__main__':
    unittest.main()
