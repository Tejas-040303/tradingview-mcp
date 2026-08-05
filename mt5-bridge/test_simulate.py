"""
Tests for the backtest replay.

Most of these exist because the corresponding bug makes a strategy look better
than it is: filling at the confirmation close, resolving an ambiguous bar in
the target's favour, sizing below the broker's minimum lot. A backtester
without these tests reports a number nobody should act on.
"""
import unittest

from simulate import END, STOP, TARGET, find_setups, simulate

GOLD = 'GOLD.i#'
MINUTE = 60
START = 1_767_571_200

# One condition, no partial, no trail: the plainest possible strategy, so a
# test that fails points at the mechanic under test and not the management.
PLAIN = {
    'entry': {'conditions': {'fvg': {'on': True, 'max_age_bars': 50},
                             'liquidity_sweep': {'on': False},
                             'order_block': {'on': False},
                             'fib': {'on': False}},
              'mode': 'any',
              'confirmation': {'timeframe': 5, 'type': 'close_beyond'}},
    'stop': {'anchor': 'confirmation_candle', 'buffer_pips': 0},
    'size': {'risk_pct': 1.0, 'max_risk_pct': 2.0},
    'manage': {'partial_pct': 0, 'partial_at_r': None, 'trail_to_be_at_r': None},
    'target': {'r': 2.0},
}


def bar(i, o, h, l, c):
    return {'time_utc': START + i * MINUTE, 'open': o, 'high': h, 'low': l, 'close': c}


def with_setup():
    """
    A bullish FVG (101-105), a bar dipping back into it and closing decisively
    above, then a bar to enter on. Entry at index 5's open, 106.
    """
    return [
        bar(0, 100, 101, 99, 100),
        bar(1, 101, 110, 101, 109),     # impulse leaves the gap
        bar(2, 109, 112, 105, 111),     # gap knowable here
        bar(3, 111, 112, 102, 103),     # dips into the gap
        bar(4, 103, 107, 103, 106.8),   # confirmation: closes above 105, near its high
        bar(5, 106, 106.5, 105.5, 106), # entry bar
    ]


class TestFindSetups(unittest.TestCase):
    def test_a_gap_touched_and_closed_beyond_is_a_setup(self):
        found = find_setups(with_setup(), PLAIN)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['direction'], 'long')
        self.assertEqual(found[0]['index'], 4)
        self.assertEqual(found[0]['conditions'], ['fvg'])

    def test_a_setup_reports_the_zone_that_produced_it(self):
        # This is what gets checked against a chart, so it has to be legible.
        setup = find_setups(with_setup(), PLAIN)[0]
        self.assertEqual(setup['zones']['fvg']['low'], 101)
        self.assertEqual(setup['zones']['fvg']['high'], 105)
        self.assertTrue(setup['time'].endswith('Z'))

    def test_touching_a_zone_without_confirming_is_not_a_setup(self):
        bars = with_setup()
        bars[4] = bar(4, 103, 107, 103, 103.5)   # closes back inside
        self.assertEqual(find_setups(bars, PLAIN), [])

    def test_no_zones_means_no_setups(self):
        flat = [bar(i, 100, 100.1, 99.9, 100) for i in range(20)]
        self.assertEqual(find_setups(flat, PLAIN), [])


class TestEntryTiming(unittest.TestCase):
    def test_entry_is_the_next_bar_open_not_the_confirmation_close(self):
        # Confirmation closes at 106.8; the next bar opens at 106. Filling at
        # the close would hand the strategy 0.8 of free edge on every trade.
        out = simulate(with_setup() + [bar(6, 106, 120, 106, 119)],
                       GOLD, PLAIN, balance=10_000)
        self.assertEqual(out['trades'][0]['entry_price'], 106)

    def test_a_confirmation_on_the_last_bar_cannot_be_traded(self):
        out = simulate(with_setup()[:5], GOLD, PLAIN, balance=10_000)
        self.assertEqual(out['trades'], [])
        self.assertIn('no bar after', out['skipped'][0]['reason'])


class TestExits(unittest.TestCase):
    def test_the_target_pays_two_r(self):
        # Entry 106, confirmation low 103, so R = 3 and the target is 112.
        bars = with_setup() + [bar(6, 106, 113, 105.9, 112)]
        trade = simulate(bars, GOLD, PLAIN, balance=10_000)['trades'][0]
        self.assertEqual(trade['exit_reason'], TARGET)
        self.assertEqual(trade['exit_price'], 112)
        self.assertEqual(trade['sim']['r_multiple'], 2.0)

    def test_the_stop_costs_one_r(self):
        bars = with_setup() + [bar(6, 106, 106, 102, 102.5)]
        trade = simulate(bars, GOLD, PLAIN, balance=10_000)['trades'][0]
        self.assertEqual(trade['exit_reason'], STOP)
        self.assertEqual(trade['exit_price'], 103)
        self.assertEqual(trade['sim']['r_multiple'], -1.0)

    def test_a_bar_holding_both_stop_and_target_resolves_as_the_stop(self):
        # The single most flattering bug available to a backtester. Bar data
        # cannot order two intrabar touches, so the loss is assumed.
        bars = with_setup() + [bar(6, 106, 113, 102, 110)]
        trade = simulate(bars, GOLD, PLAIN, balance=10_000)['trades'][0]
        self.assertEqual(trade['exit_reason'], STOP)

    def test_an_unresolved_position_is_marked_not_counted(self):
        bars = with_setup() + [bar(6, 106, 107, 105, 106)]
        out = simulate(bars, GOLD, PLAIN, balance=10_000)
        self.assertEqual(out['trades'][0]['exit_reason'], END)
        self.assertEqual(out['summary']['unresolved'], 1)
        self.assertEqual(out['summary']['closed'], 0)


class TestManagement(unittest.TestCase):
    BE = {**PLAIN, 'manage': {'partial_pct': 0, 'partial_at_r': None,
                              'trail_to_be_at_r': 1.0}}
    PARTIAL = {**PLAIN, 'manage': {'partial_pct': 50, 'partial_at_r': 1.0,
                                   'trail_to_be_at_r': 1.0}}

    # Entry 106, R = 3, so 1R is 109. The run up and the collapse are separate
    # bars on purpose: packed into one, the stop-wins rule resolves it as a
    # stop before the trail ever moves, which is correct and tests nothing.
    RUN_UP = bar(6, 106, 109.5, 105.5, 109)
    COLLAPSE = bar(7, 109, 109, 102, 102.5)

    def test_reaching_one_r_then_reversing_exits_at_breakeven(self):
        bars = with_setup() + [self.RUN_UP, self.COLLAPSE]
        trade = simulate(bars, GOLD, self.BE, balance=10_000)['trades'][0]
        self.assertEqual(trade['exit_price'], 106)
        self.assertEqual(trade['sim']['stop_kind'], 'breakeven')
        self.assertEqual(trade['net'], 0.0)

    def test_the_stop_that_filled_is_labelled(self):
        # Real MT5 history calls both of these 'stop_loss', which is exactly
        # why the observed-stop medians came out bimodal.
        early = simulate(with_setup() + [bar(6, 106, 106, 102, 102.5)],
                         GOLD, self.BE, balance=10_000)['trades'][0]
        self.assertEqual(early['sim']['stop_kind'], 'initial')

    def test_a_partial_banks_profit_before_the_breakeven_stop(self):
        bars = with_setup() + [self.RUN_UP, self.COLLAPSE]
        trade = simulate(bars, GOLD, self.PARTIAL, balance=10_000)['trades'][0]
        self.assertGreater(trade['net'], 0)
        self.assertTrue(trade['sim']['partial_taken'])
        self.assertEqual(trade['partial_closes'], 1)

    def test_a_full_partial_closes_the_position(self):
        cfg = {**PLAIN, 'manage': {'partial_pct': 100, 'partial_at_r': 1.0,
                                   'trail_to_be_at_r': None}}
        bars = with_setup() + [bar(6, 106, 109.5, 105, 109)]
        trade = simulate(bars, GOLD, cfg, balance=10_000)['trades'][0]
        self.assertEqual(trade['sim']['r_multiple'], 1.0)


class TestSizing(unittest.TestCase):
    def test_a_balance_too_small_refuses_the_setup(self):
        # The live account: minimum lot on a 3-point gold stop risks $3.
        out = simulate(with_setup() + [bar(6, 106, 113, 106, 112)],
                       GOLD, PLAIN, balance=16.84)
        self.assertEqual(out['trades'], [])
        self.assertIn('smallest tradeable lot', out['skipped'][0]['reason'])
        self.assertEqual(out['summary']['trades'], 0)

    def test_risk_taken_is_recorded_alongside_the_result(self):
        trade = simulate(with_setup() + [bar(6, 106, 113, 106, 112)],
                         GOLD, PLAIN, balance=10_000)['trades'][0]
        self.assertAlmostEqual(trade['sim']['r_distance'], 3.0, places=5)
        self.assertGreater(trade['sim']['planned_risk'], 0)


class TestConcurrency(unittest.TestCase):
    def test_a_setup_during_an_open_position_is_skipped_not_stacked(self):
        # Two confirmations back to back; only the first can be traded.
        bars = with_setup() + [
            bar(6, 106, 107, 104, 106.8),   # dips into the gap and confirms again
            bar(7, 106, 107, 105, 106.5),
            bar(8, 106, 113, 106, 112),     # the first trade finally reaches target
        ]
        out = simulate(bars, GOLD, PLAIN, balance=10_000)
        self.assertEqual(len(out['trades']), 1)
        self.assertTrue(any('already open' in s['reason'] for s in out['skipped']))


class TestShape(unittest.TestCase):
    """The output has to be indistinguishable from pair_trades() to downstream code."""

    PAIR_TRADES_FIELDS = {
        'position_id', 'symbol', 'direction', 'opened_utc', 'opened', 'closed_utc',
        'closed', 'duration_sec', 'entry_price', 'exit_price', 'volume', 'net',
        'exit_reason', 'deals', 'partial_closes', 'open', 'entry_missing',
    }

    def test_every_pair_trades_field_is_present(self):
        trade = simulate(with_setup() + [bar(6, 106, 113, 106, 112)],
                         GOLD, PLAIN, balance=10_000)['trades'][0]
        self.assertTrue(self.PAIR_TRADES_FIELDS <= set(trade))

    def test_simulated_trades_flow_through_the_real_analytics(self):
        # The whole reason for matching the shape: no second reporting stack.
        from analytics import summarize_trades
        bars = with_setup() + [bar(6, 106, 113, 106, 112)]
        trades = simulate(bars, GOLD, PLAIN, balance=10_000)['trades']
        summary = summarize_trades(trades)
        self.assertEqual(summary['trades'], 1)
        self.assertEqual(summary['wins'], 1)


class TestSummary(unittest.TestCase):
    def test_a_thin_sample_says_so_instead_of_quoting_a_win_rate(self):
        out = simulate(with_setup() + [bar(6, 106, 113, 106, 112)],
                       GOLD, PLAIN, balance=10_000)
        self.assertFalse(out['summary']['reliable'])
        self.assertIn('smoke test', out['summary']['note'])

    def test_profit_factor_is_null_without_a_loss_rather_than_infinite(self):
        out = simulate(with_setup() + [bar(6, 106, 113, 106, 112)],
                       GOLD, PLAIN, balance=10_000)
        self.assertIsNone(out['summary']['profit_factor'])

    def test_skip_reasons_are_counted(self):
        out = simulate(with_setup() + [bar(6, 106, 113, 106, 112)],
                       GOLD, PLAIN, balance=16.84)
        self.assertEqual(sum(out['summary']['skipped_reasons'].values()),
                         out['summary']['skipped'])

    def test_no_setups_at_all_is_reported_not_crashed(self):
        flat = [bar(i, 100, 100.1, 99.9, 100) for i in range(20)]
        out = simulate(flat, GOLD, PLAIN, balance=1000)
        self.assertEqual(out['summary']['trades'], 0)
        self.assertIn('no setups', out['summary']['note'])

    def test_empty_bars_are_tolerated(self):
        out = simulate([], GOLD, PLAIN, balance=1000)
        self.assertEqual(out['trades'], [])


if __name__ == '__main__':
    unittest.main()
