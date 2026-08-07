"""
Tests for the strategy 1 replay.

Two themes. The first is sizing: a fixed lot on an unusually wide sweep is
exactly how a 1% plan becomes a 3% trade, so the cap has to apply in fixed mode
too. The second is that rejections survive into the output — "the strategy
found nothing" and "the target rule is too strict" produce the same trade count
and are completely different findings.
"""
import unittest

import sweep_backtest as sb
import sweep_strategy as ss
from test_sweep_strategy import entry_series, swept_low_series

GOLD = 'GOLD.i#'


def bars():
    return {'15': swept_low_series('15'), '3': entry_series(60)}


class TestSizing(unittest.TestCase):
    CFG = ss.merged()

    def test_risk_mode_scales_the_lot_off_the_stop(self):
        tight = sb.size_for(1000.0, 1.0, GOLD, self.CFG)
        wide = sb.size_for(1000.0, 5.0, GOLD, self.CFG)
        self.assertGreater(tight['lot'], wide['lot'])
        self.assertLessEqual(wide['risk'], 25.0)

    def test_fixed_mode_uses_the_configured_lot(self):
        cfg = ss.merged({'size': {'mode': 'fixed', 'fixed_lot': 0.03}})
        out = sb.size_for(1000.0, 2.0, GOLD, cfg)
        self.assertTrue(out['ok'])
        self.assertEqual(out['lot'], 0.03)

    def test_a_fixed_lot_on_a_wide_stop_is_still_refused(self):
        # The whole reason the cap survives into fixed mode: 0.03 lot on a
        # 10-point stop risks $30, which is 3% of a $1000 account.
        cfg = ss.merged({'size': {'mode': 'fixed', 'fixed_lot': 0.03,
                                  'max_risk_pct': 2.5}})
        out = sb.size_for(1000.0, 10.0, GOLD, cfg)
        self.assertFalse(out['ok'])
        self.assertIn('above the', out['reason'])

    def test_the_lot_is_a_whole_number_of_broker_steps(self):
        # Float modulo is unreliable here — 0.03 % 0.01 comes back as
        # 0.00999..., so the check is against the step count instead.
        out = sb.size_for(1000.0, 2.65, GOLD, self.CFG)
        steps = out['lot'] / 0.01
        self.assertAlmostEqual(steps, round(steps), places=6)
        self.assertLessEqual(out['risk'], 1000.0 * self.CFG['size']['risk_pct'] / 100)

    def test_confluence_raises_the_lot_within_the_cap(self):
        # A 5-point stop sizes to 0.02, leaving room below max_lot for the
        # bump to be visible. At a 2-point stop the base lot is already at the
        # cap and the test would pass or fail on the cap, not on confluence.
        one = sb.size_for(1000.0, 5.0, GOLD, self.CFG, confluence=1)
        two = sb.size_for(1000.0, 5.0, GOLD, self.CFG, confluence=2)
        self.assertGreater(two['lot'], one['lot'])
        self.assertLessEqual(two['lot'], self.CFG['size']['max_lot'])

    def test_confluence_never_exceeds_max_lot(self):
        out = sb.size_for(100_000.0, 2.0, GOLD, self.CFG, confluence=5)
        self.assertLessEqual(out['lot'], self.CFG['size']['max_lot'])

    def test_nonsense_inputs_are_refused_with_a_reason(self):
        self.assertFalse(sb.size_for(1000.0, 0, GOLD, self.CFG)['ok'])
        self.assertFalse(sb.size_for(0, 2.0, GOLD, self.CFG)['ok'])


class TestConfluence(unittest.TestCase):
    def test_sweeps_in_the_same_direction_near_the_entry_count(self):
        setup = {'direction': 'long', 'entry_time_utc': 1000}
        sweeps = [{'direction': 'long', 'timeframe': '15', 'knowable_utc': 900},
                  {'direction': 'long', 'timeframe': '60', 'knowable_utc': 950}]
        self.assertEqual(sb.confluence_at(sweeps, setup, 500), {'15', '60'})

    def test_the_opposite_direction_is_not_confluence(self):
        # A bullish and a bearish sweep together is a disagreement, not
        # evidence.
        setup = {'direction': 'long', 'entry_time_utc': 1000}
        sweeps = [{'direction': 'short', 'timeframe': '60', 'knowable_utc': 950}]
        self.assertEqual(sb.confluence_at(sweeps, setup, 500), set())

    def test_a_sweep_after_the_entry_does_not_count(self):
        # It had not happened yet.
        setup = {'direction': 'long', 'entry_time_utc': 1000}
        sweeps = [{'direction': 'long', 'timeframe': '60', 'knowable_utc': 1100}]
        self.assertEqual(sb.confluence_at(sweeps, setup, 500), set())

    def test_a_stale_sweep_falls_out_of_the_window(self):
        setup = {'direction': 'long', 'entry_time_utc': 10_000}
        sweeps = [{'direction': 'long', 'timeframe': '60', 'knowable_utc': 100}]
        self.assertEqual(sb.confluence_at(sweeps, setup, 500), set())


class TestBacktest(unittest.TestCase):
    def test_a_setup_becomes_a_trade_in_the_pair_trades_shape(self):
        out = sb.backtest(bars(), balance=1000.0)
        self.assertTrue(out['success'])
        self.assertEqual(len(out['trades']), 1, out['summary'].get('skipped_reasons'))
        trade = out['trades'][0]
        for field in ('position_id', 'symbol', 'direction', 'opened_utc', 'closed_utc',
                      'entry_price', 'exit_price', 'volume', 'net', 'exit_reason',
                      'deals', 'partial_closes', 'open', 'entry_missing'):
            self.assertIn(field, trade)

    def test_simulated_trades_flow_through_the_real_analytics(self):
        from analytics import summarize_trades
        out = sb.backtest(bars(), balance=1000.0)
        summary = summarize_trades(out['trades'])
        self.assertEqual(summary['trades'], 1)

    def test_the_trade_records_which_timeframe_swept(self):
        trade = sb.backtest(bars(), balance=1000.0)['trades'][0]
        self.assertEqual(trade['sim']['sweep_timeframe'], '15')
        self.assertEqual(trade['sim']['entry_timeframe'], '3')
        self.assertEqual(trade['sim']['strategy'], 'liquidity-sweep')

    def test_an_invalid_config_is_refused_before_anything_runs(self):
        out = sb.backtest(bars(), config={'sweep_timeframes': ['7']})
        self.assertFalse(out['success'])
        self.assertTrue(out['problems'])

    def test_detection_rejections_survive_into_the_result(self):
        # "Found nothing" and "target rule too strict" give the same trade
        # count and are entirely different findings.
        out = sb.backtest(bars(), config={'target': {'min_r': 50.0}}, balance=1000.0)
        self.assertEqual(out['trades'], [])
        self.assertTrue(any('no target at least' in s['reason']
                            for s in out['skipped']))
        self.assertIn('reject_reasons', out['detection'])

    def test_no_bars_is_reported_not_crashed(self):
        out = sb.backtest({}, balance=1000.0)
        self.assertTrue(out['success'])
        self.assertEqual(out['trades'], [])


class TestTrailClearsTheSpread(unittest.TestCase):
    def test_the_breakeven_stop_sits_beyond_entry_by_spread_plus_buffer(self):
        # Breakeven at exactly entry exits having paid the round trip. The
        # offset is what makes a "free" trade actually free.
        from simulate import _walk
        cfg = {'manage': {'partial_pct': 0, 'partial_at_r': None,
                          'trail_to_be_at_r': 1.0, 'trail_offset_price': 0.4}}
        plan = {'entry': 4000.0, 'stop': 3996.0, 'target': 4008.0,
                'r_distance': 4.0, 'lot': 0.01, 'risk': 4.0}
        walk_bars = [
            {'time_utc': 0, 'open': 4000, 'high': 4004.5, 'low': 3999, 'close': 4004},
            {'time_utc': 60, 'open': 4004, 'high': 4004, 'low': 3990, 'close': 3991},
        ]
        out = _walk(walk_bars, 0, plan, {'direction': 'long'}, 'GOLD.i#', cfg)
        self.assertEqual(out['stop_kind'], 'breakeven')
        # Exited above entry, not at it.
        self.assertEqual(out['fills'][-1][0], 4000.4)
        self.assertGreater(out['net'], 0)


class TestSummary(unittest.TestCase):
    def test_a_thin_sample_says_so_rather_than_quoting_a_win_rate(self):
        summary = sb.backtest(bars(), balance=1000.0)['summary']
        self.assertFalse(summary['reliable'])
        self.assertIn('smoke test', summary['note'])

    def test_the_assumed_spread_is_reported(self):
        # A backtest at zero spread describes a broker that does not exist.
        summary = sb.backtest(bars(), balance=1000.0, spread=0.25)['summary']
        self.assertEqual(summary['spread_assumed'], 0.25)

    def test_results_are_broken_down_by_sweep_timeframe(self):
        # Which timeframe's sweeps actually work is the first thing worth
        # knowing, and it decides whether 15M belongs in the list at all.
        summary = sb.backtest(bars(), balance=1000.0)['summary']
        self.assertIn('by_sweep_timeframe', summary)
        self.assertIn('by_trigger', summary)

    def test_no_trades_still_reports_why(self):
        summary = sb.backtest({}, balance=1000.0)['summary']
        self.assertEqual(summary['trades'], 0)
        self.assertIn('skipped_reasons', summary)


if __name__ == '__main__':
    unittest.main()
