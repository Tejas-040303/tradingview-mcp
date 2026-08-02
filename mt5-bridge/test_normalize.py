"""
Tests for the MT5 bridge's pure logic.

Runs anywhere — no MetaTrader5, no terminal, no network:
    python -m unittest discover mt5-bridge
"""
import unittest

from normalize import (
    DEAL_ENTRY,
    DEAL_REASON,
    DEAL_TYPE,
    blackout_status,
    clean_last,
    decode_enums,
    msc_fields,
    paginate,
    summarize_deals,
    filter_calendar,
    filter_symbols,
    infer_server_offset,
    mid_price,
    normalize_calendar,
    resolve_timeframe,
    scaled,
    summarize_bars,
    time_fields,
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


class TestInferServerOffset(unittest.TestCase):
    """Guards against a stale tick being read as a real timezone offset."""

    def test_infers_utc_plus_3_from_a_fresh_tick(self):
        # XM's server clock runs 3h ahead; a tick 2s old reads as +10800.
        self.assertEqual(infer_server_offset(NOW + 10800 - 2, NOW), 10800)

    def test_infers_negative_offset(self):
        self.assertEqual(infer_server_offset(NOW - 18000 + 5, NOW), -18000)

    def test_rounds_to_the_nearest_half_hour(self):
        self.assertEqual(infer_server_offset(NOW + 10800 + 400, NOW), 10800)

    def test_handles_half_hour_timezones(self):
        self.assertEqual(infer_server_offset(NOW + 19800 - 3, NOW), 19800)

    def test_weekend_stale_tick_is_unknown_not_a_wrong_offset(self):
        # Two days stale — must not be reported as a plausible-looking offset.
        self.assertIsNone(infer_server_offset(NOW - 2 * 86400, NOW))

    def test_missing_tick_is_unknown(self):
        self.assertIsNone(infer_server_offset(None, NOW))

    def test_accepts_the_extremes_of_the_real_timezone_range(self):
        self.assertEqual(infer_server_offset(NOW + 50400, NOW), 50400)
        self.assertIsNone(infer_server_offset(NOW + 50400 + 3600, NOW))


class TestTimeFields(unittest.TestCase):
    """The mislabelling bug: server timestamps must never be stamped as UTC."""

    def test_server_time_is_not_labelled_utc(self):
        out = time_fields(NOW, 10800)
        self.assertFalse(out['time_server_iso'].endswith('Z'))

    def test_utc_is_the_server_stamp_minus_the_offset(self):
        out = time_fields(NOW, 10800)
        self.assertEqual(out['time_utc'], NOW - 10800)
        self.assertTrue(out['time_utc_iso'].endswith('Z'))

    def test_unknown_offset_yields_no_utc_rather_than_a_guess(self):
        out = time_fields(NOW, None)
        self.assertEqual(out['time_server'], NOW)
        self.assertIsNone(out['time_utc'])
        self.assertIsNone(out['time_utc_iso'])

    def test_zero_offset_still_produces_utc(self):
        self.assertEqual(time_fields(NOW, 0)['time_utc'], NOW)

    def test_missing_timestamp_yields_nothing(self):
        self.assertEqual(time_fields(None, 10800), {})

    def test_three_hour_gap_is_reflected_in_the_labels(self):
        out = time_fields(NOW, 10800)
        server_hour = out['time_server_iso'][11:13]
        utc_hour = out['time_utc_iso'][11:13]
        self.assertEqual((int(server_hour) - int(utc_hour)) % 24, 3)


class TestCfdPriceFields(unittest.TestCase):
    """CFDs report no last-trade price; 0.0 must not read as a real price."""

    def test_zero_last_becomes_none(self):
        self.assertIsNone(clean_last(0.0))

    def test_real_last_is_preserved(self):
        self.assertEqual(clean_last(4047.68), 4047.68)

    def test_mid_is_the_average_of_bid_and_ask(self):
        self.assertEqual(mid_price(4047.68, 4047.94, 2), 4047.81)

    def test_mid_is_none_when_a_side_is_missing(self):
        self.assertIsNone(mid_price(None, 4047.94, 2))
        self.assertIsNone(mid_price(4047.68, 0, 2))


class TestSummarizeBarsTimeLabels(unittest.TestCase):
    def test_uses_expanded_fields_when_present(self):
        bars = [{
            'time_server': NOW, 'time_server_iso': '2026-07-30T10:15:26',
            'time_utc': NOW - 10800, 'time_utc_iso': '2026-07-30T07:15:26Z',
            'open': 1.0, 'high': 2.0, 'low': 0.5, 'close': 1.5, 'tick_volume': 1,
        }]
        out = summarize_bars(bars)
        self.assertEqual(out['from_server'], '2026-07-30T10:15:26')
        self.assertEqual(out['from_utc'], '2026-07-30T07:15:26Z')

    def test_bare_time_is_treated_as_server_time_with_no_utc_claim(self):
        bars = [{'time': NOW, 'open': 1.0, 'high': 2.0, 'low': 0.5,
                 'close': 1.5, 'tick_volume': 1}]
        out = summarize_bars(bars)
        self.assertFalse(out['from_server'].endswith('Z'))
        self.assertIsNone(out['from_utc'])


class TestFilterSymbols(unittest.TestCase):
    def setUp(self):
        # Shape mirrors what XM actually returns.
        self.symbols = [
            {'name': 'GOLD.i#', 'description': 'GOLD'},
            {'name': 'GOLD24-7.i#', 'description': 'GOLD 24/7'},
            {'name': 'XAUEUR.i#', 'description': 'Gold vs Euro'},
            {'name': 'BarrickGold', 'description': 'Barrick Gold Corp'},
            {'name': 'EURUSD', 'description': 'Euro vs US Dollar'},
        ]

    def test_matches_on_name(self):
        names = [s['name'] for s in filter_symbols(self.symbols, 'gold')]
        self.assertIn('GOLD.i#', names)
        self.assertNotIn('EURUSD', names)

    def test_matches_on_description(self):
        names = [s['name'] for s in filter_symbols(self.symbols, 'euro')]
        self.assertIn('EURUSD', names)
        self.assertIn('XAUEUR.i#', names)

    def test_is_case_insensitive(self):
        self.assertEqual(len(filter_symbols(self.symbols, 'GoLd')),
                         len(filter_symbols(self.symbols, 'gold')))

    def test_empty_search_returns_everything_sorted(self):
        out = filter_symbols(self.symbols)
        self.assertEqual(len(out), 5)
        self.assertEqual([s['name'] for s in out],
                         sorted(s['name'] for s in self.symbols))

    def test_limit_is_applied(self):
        self.assertEqual(len(filter_symbols(self.symbols, limit=2)), 2)

    def test_tolerates_empty_input(self):
        self.assertEqual(filter_symbols(None, 'gold'), [])


def deal(profit=0.0, entry='out', dtype='buy', reason='client', **extra):
    """A decoded deal, shaped like what mt5_client emits."""
    return {
        'ticket': 1, 'symbol': 'GOLD.i#', 'volume': 0.01, 'price': 4069.07,
        'profit': profit, 'commission': 0.0, 'swap': 0.0, 'fee': 0.0,
        'type': dtype, 'entry': entry, 'reason': reason,
        'time_utc': NOW, **extra,
    }


class TestDecodeEnums(unittest.TestCase):
    """Raw integers make a journal unreadable; the mapping is not guessable."""

    def test_decodes_a_stop_loss_exit(self):
        # Straight from a real XM deal: buy, closed out, stopped.
        row = decode_enums({'type': 0, 'entry': 1, 'reason': 4},
                           {'type': DEAL_TYPE, 'entry': DEAL_ENTRY,
                            'reason': DEAL_REASON})
        self.assertEqual(row['type'], 'buy')
        self.assertEqual(row['entry'], 'out')
        self.assertEqual(row['reason'], 'stop_loss')

    def test_keeps_the_raw_value(self):
        row = decode_enums({'reason': 4}, {'reason': DEAL_REASON})
        self.assertEqual(row['reason_raw'], 4)

    def test_decodes_mobile_origin(self):
        row = decode_enums({'reason': 1}, {'reason': DEAL_REASON})
        self.assertEqual(row['reason'], 'mobile')

    def test_unknown_code_is_surfaced_not_dropped(self):
        row = decode_enums({'reason': 99}, {'reason': DEAL_REASON})
        self.assertEqual(row['reason'], 'unknown_99')
        self.assertEqual(row['reason_raw'], 99)

    def test_missing_field_is_left_alone(self):
        self.assertEqual(decode_enums({'ticket': 1}, {'reason': DEAL_REASON}),
                         {'ticket': 1})

    def test_already_decoded_values_are_not_remapped(self):
        row = decode_enums({'type': 'buy'}, {'type': DEAL_TYPE})
        self.assertEqual(row['type'], 'buy')
        self.assertNotIn('type_raw', row)


class TestMscFields(unittest.TestCase):
    def test_converts_millisecond_stamps(self):
        out = msc_fields(1785411084880, 10800)
        self.assertEqual(out['time_msc_server'], 1785411084880)
        self.assertEqual(out['time_msc_utc'], 1785411084880 - 10800 * 1000)

    def test_unknown_offset_yields_no_utc(self):
        self.assertIsNone(msc_fields(1785411084880, None)['time_msc_utc'])

    def test_missing_value_yields_nothing(self):
        self.assertEqual(msc_fields(None, 10800), {})


class TestSummarizeDeals(unittest.TestCase):
    def test_counts_only_closing_deals_as_trades(self):
        out = summarize_deals([deal(entry='in'), deal(profit=5.0, entry='out')])
        self.assertEqual(out['deals'], 2)
        self.assertEqual(out['closed_trades'], 1)

    def test_excludes_balance_rows_from_trades(self):
        out = summarize_deals([deal(profit=100.0, dtype='balance', entry='in'),
                               deal(profit=5.0)])
        self.assertEqual(out['closed_trades'], 1)
        self.assertEqual(out['gross_profit'], 5.0)

    def test_win_rate_and_extremes(self):
        out = summarize_deals([deal(profit=10.0), deal(profit=-4.78),
                               deal(profit=2.0), deal(profit=-1.0)])
        self.assertEqual(out['wins'], 2)
        self.assertEqual(out['losses'], 2)
        self.assertEqual(out['win_rate_pct'], 50.0)
        self.assertEqual(out['best'], 10.0)
        self.assertEqual(out['worst'], -4.78)

    def test_costs_are_subtracted_from_gross(self):
        out = summarize_deals([deal(profit=10.0, commission=-1.0, swap=-0.5)])
        self.assertEqual(out['gross_profit'], 10.0)
        self.assertEqual(out['costs'], -1.5)
        self.assertEqual(out['net_profit'], 8.5)

    def test_groups_exits_by_reason(self):
        out = summarize_deals([deal(reason='stop_loss'), deal(reason='stop_loss'),
                               deal(reason='take_profit')])
        self.assertEqual(out['closed_by'], {'stop_loss': 2, 'take_profit': 1})

    def test_reports_symbols_and_window(self):
        out = summarize_deals([deal(), deal(symbol='EURUSD', time_utc=NOW + 60)])
        self.assertEqual(out['symbols'], ['EURUSD', 'GOLD.i#'])
        self.assertTrue(out['from_utc'].endswith('Z'))

    def test_averages_are_none_without_samples(self):
        out = summarize_deals([deal(profit=5.0)])
        self.assertEqual(out['avg_win'], 5.0)
        self.assertIsNone(out['avg_loss'])

    def test_empty_history_is_none(self):
        self.assertIsNone(summarize_deals([]))


class TestPaginate(unittest.TestCase):
    def setUp(self):
        self.rows = list(range(421))  # a real month of scalping

    def test_default_page_caps_the_response(self):
        window, page = paginate(self.rows, limit=100)
        self.assertEqual(len(window), 100)
        self.assertEqual(page['total'], 421)
        self.assertTrue(page['has_more'])

    def test_offset_walks_the_set(self):
        window, page = paginate(self.rows, limit=100, offset=100)
        self.assertEqual(window[0], 100)
        self.assertEqual(page['offset'], 100)

    def test_final_page_reports_no_more(self):
        window, page = paginate(self.rows, limit=100, offset=400)
        self.assertEqual(len(window), 21)
        self.assertFalse(page['has_more'])

    def test_offset_past_the_end_is_empty_not_an_error(self):
        window, page = paginate(self.rows, limit=100, offset=1000)
        self.assertEqual(window, [])
        self.assertFalse(page['has_more'])

    def test_negative_offset_is_clamped(self):
        _, page = paginate(self.rows, limit=10, offset=-5)
        self.assertEqual(page['offset'], 0)


if __name__ == '__main__':
    unittest.main()
