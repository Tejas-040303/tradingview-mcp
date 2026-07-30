"""
Tests for the MT5 bridge's pure logic.

Runs anywhere — no MetaTrader5, no terminal, no network:
    python -m unittest discover mt5-bridge
"""
import unittest

from normalize import (
    blackout_status,
    filter_calendar,
    normalize_calendar,
    resolve_timeframe,
    scaled,
    summarize_bars,
)

HOUR = 3600
NOW = 1_785_000_000  # fixed reference so tests never depend on wall clock


def event(offset_min, importance='high', currency='USD', name='CPI', **extra):
    """A raw exporter row, `offset_min` minutes from NOW."""
    return {
        'time': NOW + int(offset_min * 60),
        'currency': currency,
        'country': 'United States',
        'event': name,
        'importance': importance,
        'digits': 1,
        **extra,
    }


class TestResolveTimeframe(unittest.TestCase):
    def test_maps_tradingview_style_strings(self):
        self.assertEqual(resolve_timeframe('5'), 'TIMEFRAME_M5')
        self.assertEqual(resolve_timeframe('60'), 'TIMEFRAME_H1')
        self.assertEqual(resolve_timeframe('D'), 'TIMEFRAME_D1')

    def test_accepts_aliases_and_case(self):
        self.assertEqual(resolve_timeframe('15m'), 'TIMEFRAME_M15')
        self.assertEqual(resolve_timeframe('4h'), 'TIMEFRAME_H4')
        self.assertEqual(resolve_timeframe('daily'), 'TIMEFRAME_D1')

    def test_rejects_unknown(self):
        with self.assertRaises(ValueError):
            resolve_timeframe('7s')


class TestScaled(unittest.TestCase):
    def test_decodes_mql5_scaling(self):
        self.assertEqual(scaled(3_100_000, 1), 3.1)

    def test_passes_through_missing_values(self):
        self.assertIsNone(scaled(None))


class TestSummarizeBars(unittest.TestCase):
    def setUp(self):
        self.bars = [
            {'time': NOW, 'open': 100.0, 'high': 105.0, 'low': 99.0, 'close': 104.0, 'tick_volume': 10},
            {'time': NOW + 60, 'open': 104.0, 'high': 110.0, 'low': 103.0, 'close': 108.0, 'tick_volume': 20},
            {'time': NOW + 120, 'open': 108.0, 'high': 112.0, 'low': 96.0, 'close': 110.0, 'tick_volume': 30},
        ]

    def test_computes_extremes_across_series(self):
        out = summarize_bars(self.bars)
        self.assertEqual(out['high'], 112.0)
        self.assertEqual(out['low'], 96.0)
        self.assertEqual(out['range'], 16.0)

    def test_change_spans_first_open_to_last_close(self):
        out = summarize_bars(self.bars)
        self.assertEqual(out['open'], 100.0)
        self.assertEqual(out['close'], 110.0)
        self.assertEqual(out['change'], 10.0)
        self.assertEqual(out['change_pct'], 10.0)

    def test_reports_count_and_average_volume(self):
        out = summarize_bars(self.bars)
        self.assertEqual(out['count'], 3)
        self.assertEqual(out['avg_volume'], 20.0)

    def test_empty_series_is_none_not_an_error(self):
        self.assertIsNone(summarize_bars([]))

    def test_last_5_never_exceeds_series_length(self):
        self.assertEqual(len(summarize_bars(self.bars)['last_5']), 3)


class TestNormalizeCalendar(unittest.TestCase):
    def test_decodes_values_and_adds_iso_time(self):
        rows = normalize_calendar([event(0, actual=3_100_000, forecast=2_900_000)])
        self.assertEqual(rows[0]['actual'], 3.1)
        self.assertEqual(rows[0]['forecast'], 2.9)
        self.assertTrue(rows[0]['time'].endswith('Z'))

    def test_accepts_numeric_mql5_importance(self):
        rows = normalize_calendar([event(0, importance=3), event(10, importance=1)])
        self.assertEqual(rows[0]['importance'], 'high')
        self.assertEqual(rows[1]['importance'], 'low')

    def test_unknown_importance_degrades_to_none(self):
        self.assertEqual(normalize_calendar([event(0, importance='critical')])[0]['importance'], 'none')

    def test_sorts_by_time(self):
        rows = normalize_calendar([event(60), event(-60), event(0)])
        self.assertEqual([r['timestamp'] for r in rows],
                         sorted(r['timestamp'] for r in rows))

    def test_skips_malformed_rows_without_losing_the_batch(self):
        rows = normalize_calendar([{'currency': 'USD'}, event(0), {'time': 'nonsense'}])
        self.assertEqual(len(rows), 1)

    def test_tolerates_empty_input(self):
        self.assertEqual(normalize_calendar(None), [])


class TestFilterCalendar(unittest.TestCase):
    def setUp(self):
        self.rows = normalize_calendar([
            event(10, importance='high', currency='USD', name='CPI'),
            event(20, importance='low', currency='USD', name='Truck Sales'),
            event(30, importance='high', currency='EUR', name='ECB Rate'),
        ])

    def test_importance_floor_is_inclusive(self):
        kept = filter_calendar(self.rows, min_importance='high')
        self.assertEqual({r['event'] for r in kept}, {'CPI', 'ECB Rate'})

    def test_filters_by_currency(self):
        kept = filter_calendar(self.rows, currencies=['usd'])
        self.assertTrue(all(r['currency'] == 'USD' for r in kept))

    def test_filters_by_time_window(self):
        kept = filter_calendar(self.rows, from_ts=NOW + 15 * 60)
        self.assertEqual(len(kept), 2)


class TestBlackoutStatus(unittest.TestCase):
    """The ±15m rule an automated strategy consults before acting."""

    def test_pending_release_inside_window_blacks_out(self):
        out = blackout_status(normalize_calendar([event(10)]), NOW)
        self.assertTrue(out['blackout'])
        self.assertEqual(out['active'][0]['minutes_until'], 10.0)

    def test_recent_release_inside_window_blacks_out(self):
        out = blackout_status(normalize_calendar([event(-10)]), NOW)
        self.assertTrue(out['blackout'])
        self.assertEqual(out['active'][0]['minutes_until'], -10.0)

    def test_window_boundaries_are_inclusive(self):
        self.assertTrue(blackout_status(normalize_calendar([event(15)]), NOW)['blackout'])
        self.assertTrue(blackout_status(normalize_calendar([event(-15)]), NOW)['blackout'])

    def test_outside_window_is_clear(self):
        out = blackout_status(normalize_calendar([event(16)]), NOW)
        self.assertFalse(out['blackout'])
        self.assertEqual(out['minutes_until_next'], 16.0)

    def test_asymmetric_windows_are_honoured(self):
        rows = normalize_calendar([event(-25)])
        self.assertFalse(blackout_status(rows, NOW, after_min=15)['blackout'])
        self.assertTrue(blackout_status(rows, NOW, after_min=30)['blackout'])

    def test_low_importance_event_does_not_block_by_default(self):
        out = blackout_status(normalize_calendar([event(5, importance='low')]), NOW)
        self.assertFalse(out['blackout'])

    def test_importance_floor_can_be_lowered(self):
        rows = normalize_calendar([event(5, importance='moderate')])
        self.assertTrue(blackout_status(rows, NOW, min_importance='moderate')['blackout'])

    def test_currency_filter_excludes_unrelated_events(self):
        rows = normalize_calendar([event(5, currency='JPY')])
        self.assertFalse(blackout_status(rows, NOW, currencies=['USD'])['blackout'])
        self.assertTrue(blackout_status(rows, NOW, currencies=['JPY'])['blackout'])

    def test_binding_constraint_is_reported_first(self):
        rows = normalize_calendar([event(14, name='Far'), event(2, name='Near')])
        out = blackout_status(rows, NOW)
        self.assertEqual(out['active'][0]['event'], 'Near')

    def test_next_event_skips_ones_already_past(self):
        rows = normalize_calendar([event(-600, name='Old'), event(600, name='Future')])
        out = blackout_status(rows, NOW)
        self.assertFalse(out['blackout'])
        self.assertEqual(out['next']['event'], 'Future')

    def test_empty_calendar_is_clear_not_an_error(self):
        out = blackout_status([], NOW)
        self.assertFalse(out['blackout'])
        self.assertIsNone(out['next'])
        self.assertIsNone(out['minutes_until_next'])

    def test_echoes_the_settings_it_applied(self):
        out = blackout_status(normalize_calendar([event(5)]), NOW,
                              before_min=30, after_min=45, currencies=['usd'])
        self.assertEqual(out['window'], {'before_min': 30, 'after_min': 45})
        self.assertEqual(out['currencies'], ['USD'])
        self.assertEqual(out['min_importance'], 'high')


if __name__ == '__main__':
    unittest.main()
