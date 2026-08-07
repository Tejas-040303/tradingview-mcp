"""
Tests for the strategy 1 bridge routes.

The interesting part is `_sweep_bars`. Fetching a fixed number of bars per
timeframe would pull years of 4H candles against days of 3M ones, and every
ancient sweep would then trigger against the first few entry bars — signals
manufactured by the shape of the request rather than found in the market. The
window has to be derived, and these pin that.
"""
import unittest

import bridge
import mt5_client
import sweep_strategy as ss

GOLD = 'GOLD.i#'
START = 1_785_000_000


def q(**kwargs):
    return {k: [str(v)] for k, v in kwargs.items()}


class StubbedBars:
    """Stands in for the terminal, recording what was asked of it."""

    def __init__(self, available=None):
        self.calls = []
        self.available = available

    def __call__(self, symbol, timeframe='5', count=100, summary=False):
        self.calls.append({'timeframe': str(timeframe), 'count': count})
        if self.available is not None and str(timeframe) not in self.available:
            raise mt5_client.Mt5Error(f'no history for {timeframe}')
        step = ss.period(timeframe) or 300
        bars = [{'time_utc': START + i * step, 'open': 4000.0, 'high': 4000.5,
                 'low': 3999.5, 'close': 4000.0} for i in range(count)]
        return {'success': True, 'symbol': symbol, 'timeframe': str(timeframe),
                'bars': bars, 'count': len(bars)}

    def counts(self):
        return {c['timeframe']: c['count'] for c in self.calls}


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.stub = StubbedBars()
        self._real = mt5_client.bars
        mt5_client.bars = self.stub

    def tearDown(self):
        mt5_client.bars = self._real


class TestConfigParsing(unittest.TestCase):
    def test_no_parameters_means_the_default_strategy(self):
        self.assertEqual(bridge._sweep_config({}), {})
        self.assertEqual(ss.merged(bridge._sweep_config({})), ss.DEFAULT_CONFIG)

    def test_fallback_none_survives_as_null(self):
        # With no fallback the confirmation candle is the only way in, which is
        # the control case for asking whether MSS adds anything. Coercing it to
        # a string would silently keep MSS enabled.
        cfg = ss.merged(bridge._sweep_config(q(fallback='none')))
        self.assertIsNone(cfg['entry']['fallback'])

    def test_a_named_fallback_is_kept(self):
        cfg = ss.merged(bridge._sweep_config(q(fallback='mss')))
        self.assertEqual(cfg['entry']['fallback'], 'mss')

    def test_timeframes_parse_as_a_list(self):
        cfg = ss.merged(bridge._sweep_config(q(sweep_timeframes='240,60')))
        self.assertEqual(cfg['sweep_timeframes'], ['240', '60'])

    def test_numbers_reach_the_right_sections(self):
        cfg = ss.merged(bridge._sweep_config(
            q(buffer_price=0.4, min_r=3, partial_pct=60, trail_at_r=0.75,
              risk_pct=0.5, fixed_lot=0.02, wait_bars=20, max_uses=1)))
        self.assertEqual(cfg['stop']['buffer_price'], 0.4)
        self.assertEqual(cfg['target']['min_r'], 3.0)
        self.assertEqual(cfg['manage']['partial_pct'], 60.0)
        self.assertEqual(cfg['manage']['trail_at_r'], 0.75)
        self.assertEqual(cfg['size']['risk_pct'], 0.5)
        self.assertEqual(cfg['size']['fixed_lot'], 0.02)
        self.assertEqual(cfg['entry']['wait_bars'], 20)
        self.assertEqual(cfg['entry']['max_uses'], 1)

    def test_the_two_parsers_stay_separate(self):
        # strategy 1 uses buffer_price; the older engine uses buffer_pips.
        # One parser handling both would mean one silently ignoring its own
        # fields.
        self.assertEqual(bridge._sweep_config(q(buffer_pips=7)), {})
        self.assertEqual(bridge._strategy_config(q(buffer_price=0.4)), {})


class TestBarWindow(BridgeTest):
    def test_every_configured_timeframe_is_fetched(self):
        _, _, bars, _, _ = bridge._sweep_bars(q(symbol=GOLD, count=600))
        self.assertEqual(set(bars), {'3', '15', '30', '60', '240'})

    def test_higher_timeframes_get_fewer_bars_over_the_same_window(self):
        # The point of deriving the window: 600 three-minute bars is 30 hours,
        # so a handful of 4H candles cover it — not thousands.
        bridge._sweep_bars(q(symbol=GOLD, count=600))
        counts = self.stub.counts()
        self.assertEqual(counts['3'], 600)
        self.assertLess(counts['240'], counts['60'])
        self.assertLess(counts['60'], counts['15'])

    def test_each_sweep_timeframe_covers_the_entry_window_plus_a_lookback(self):
        bridge._sweep_bars(q(symbol=GOLD, count=600))
        counts = self.stub.counts()
        # last.time - first.time spans count-1 bars, not count.
        span = (600 - 1) * ss.period('3')
        for timeframe in ('15', '30', '60', '240'):
            expected = int(span / ss.period(timeframe)) + bridge.SWEEP_LOOKBACK_BARS
            self.assertEqual(counts[timeframe], min(5000, expected))

    def test_the_entry_timeframe_is_not_fetched_twice(self):
        # '15' as the entry timeframe is also a sweep timeframe by default.
        bridge._sweep_bars(q(symbol=GOLD, entry_timeframe='15', count=200))
        fetched = [c['timeframe'] for c in self.stub.calls]
        self.assertEqual(fetched.count('15'), 1)

    def test_the_count_is_capped(self):
        bridge._sweep_bars(q(symbol=GOLD, count=99_999))
        self.assertLessEqual(self.stub.counts()['3'], 5000)

    def test_the_window_is_reported(self):
        _, _, _, window, _ = bridge._sweep_bars(q(symbol=GOLD, count=100))
        self.assertTrue(window['from'].endswith('Z'))
        self.assertLess(window['from'], window['to'])

    def test_a_missing_symbol_is_refused(self):
        with self.assertRaises(ValueError):
            bridge._sweep_bars({})

    def test_an_invalid_config_is_refused_before_any_fetch(self):
        with self.assertRaises(ValueError):
            bridge._sweep_bars(q(symbol=GOLD, sweep_timeframes='7'))
        self.assertEqual(self.stub.calls, [])


class TestPartialAvailability(unittest.TestCase):
    def setUp(self):
        # Only 3M and 15M have history; the higher timeframes fail.
        self.stub = StubbedBars(available={'3', '15'})
        self._real = mt5_client.bars
        mt5_client.bars = self.stub

    def tearDown(self):
        mt5_client.bars = self._real

    def test_a_missing_timeframe_narrows_the_strategy_rather_than_failing(self):
        symbol, _, bars, _, unavailable = bridge._sweep_bars(q(symbol=GOLD, count=200))
        self.assertEqual(set(bars), {'3', '15'})
        self.assertEqual(set(unavailable), {'30', '60', '240'})

    def test_the_missing_ones_are_named_not_silently_dropped(self):
        _, _, _, _, unavailable = bridge._sweep_bars(q(symbol=GOLD, count=200))
        self.assertIn('no history for', unavailable['60'])


class TestRoutes(BridgeTest):
    def test_setups_reports_detection_and_its_rejections(self):
        out = bridge.route('/strategy1/setups', q(symbol=GOLD, count=300))
        self.assertTrue(out['success'])
        self.assertEqual(out['strategy'], 'liquidity-sweep')
        self.assertIn('sweeps_found', out)
        self.assertIn('reject_reasons', out)
        self.assertIn('config', out)

    def test_backtest_reports_a_summary_without_the_trades(self):
        out = bridge.route('/strategy1/backtest', q(symbol=GOLD, count=300))
        self.assertTrue(out['success'])
        self.assertIn('summary', out)
        self.assertIn('detection', out)
        self.assertNotIn('trades', out)

    def test_trades_and_skips_are_opt_in(self):
        out = bridge.route('/strategy1/backtest',
                            q(symbol=GOLD, count=300, trades='1', skipped='1'))
        self.assertIn('trades', out)
        self.assertIn('skipped', out)

    def test_the_assumed_spread_reaches_the_backtest(self):
        out = bridge.route('/strategy1/backtest',
                            q(symbol=GOLD, count=300, spread=0.45))
        self.assertEqual(out['summary']['spread_assumed'], 0.45)

    def test_flat_bars_produce_no_setups_and_say_so(self):
        # The stub returns identical candles, so there is nothing to sweep.
        out = bridge.route('/strategy1/setups', q(symbol=GOLD, count=300))
        self.assertEqual(out['setups'], 0)
        self.assertEqual(out['sweeps_found'], 0)

    def test_walkforward_splits_and_reports_both_halves(self):
        out = bridge.route('/strategy1/walkforward',
                           q(symbol=GOLD, count=400, axes='target.min_r:2,3'))
        self.assertTrue(out['success'])
        self.assertEqual(out['configurations'], 2)
        self.assertIn('cut_utc', out['split'])
        self.assertIn('summary', out)

    def test_walkforward_axis_values_keep_their_meaning(self):
        # 'none' on the trail axis is the control case, and turning it into
        # the string 'none' or the number 0 would delete the comparison.
        out = bridge.route('/strategy1/walkforward',
                           q(symbol=GOLD, count=400,
                             axes='manage.trail_at_r:1.0,none'))
        trails = [r['params']['manage.trail_at_r'] for r in out['rows']]
        self.assertIn(None, trails)
        self.assertIn(1.0, trails)

    def test_bar_counts_per_timeframe_are_reported(self):
        out = bridge.route('/strategy1/setups', q(symbol=GOLD, count=300))
        self.assertEqual(set(out['bars']), {'3', '15', '30', '60', '240'})


if __name__ == '__main__':
    unittest.main()
