"""
Smoke tests for mt5_client against a fake MetaTrader 5 module.

normalize.py was well covered while mt5_client.py had none, and the gap showed:
`deals()` assigned the clock offset to a local named `offset`, shadowing its own
pagination parameter, so every paginated call raised TypeError. Pure-function
tests could not catch it — the wiring is where it broke.

These exercise each route end to end with the terminal faked out, so they run
anywhere. They assert plumbing, not market behaviour.
"""
import os
import unittest

import mt5_client

NOW = 1_785_000_000
OFFSET = 10800  # UTC+3, as on XM


class FakeRecord:
    """Stands in for the namedtuples MetaTrader5 returns."""

    def __init__(self, **fields):
        self._fields = fields
        for key, value in fields.items():
            setattr(self, key, value)

    def _asdict(self):
        return dict(self._fields)


def fake_deal(ticket=1, dtype=0, entry=1, reason=4, profit=-4.78):
    return FakeRecord(
        ticket=ticket, order=99, time=NOW, time_msc=NOW * 1000,
        type=dtype, entry=entry, reason=reason, magic=0, position_id=7,
        volume=0.01, price=4069.07, commission=0.0, swap=0.0, profit=profit,
        fee=0.0, symbol='GOLD.i#', comment='[sl 4068.82]', external_id='',
    )


class FakeMt5:
    """Minimal stand-in covering only what mt5_client calls."""

    TIMEFRAME_M5 = 5
    TIMEFRAME_M1 = 1

    def __init__(self, deal_count=421):
        self._deals = [fake_deal(ticket=i) for i in range(deal_count)]

    def initialize(self, *args, **kwargs):
        return True

    def shutdown(self):
        pass

    def last_error(self):
        return (0, 'ok')

    def terminal_info(self):
        return FakeRecord(connected=True, trade_allowed=True,
                          name='MetaTrader 5', build=6061, path='C:\\MT5')

    def account_info(self):
        return FakeRecord(login=1, server='XMGlobal-MT5 9', currency='USD',
                          balance=18.54, equity=18.54, margin=0.0,
                          margin_free=18.54, margin_level=0.0, profit=0.0,
                          leverage=888, name='Tester')

    def symbols_get(self):
        return [FakeRecord(name='GOLD.i#', description='GOLD', digits=2, visible=True),
                FakeRecord(name='EURUSD', description='Euro vs USD', digits=5, visible=True)]

    def symbol_select(self, symbol, enable=True):
        return True

    def symbol_info(self, symbol):
        return FakeRecord(digits=2, description='GOLD')

    def symbol_info_tick(self, symbol):
        return FakeRecord(time=NOW + OFFSET, bid=4068.34, ask=4068.6,
                          last=0.0, volume=0)

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        return [{'time': NOW + i * 300, 'open': 4050.0, 'high': 4060.0,
                 'low': 4045.0, 'close': 4055.0, 'tick_volume': 1800,
                 'spread': 20} for i in range(count)]

    def positions_get(self, symbol=None):
        return [FakeRecord(ticket=5, time=NOW, time_msc=NOW * 1000, type=1,
                           reason=0, symbol='GOLD.i#', volume=0.01,
                           price_open=4050.0, profit=1.2)]

    def orders_get(self, symbol=None):
        return [FakeRecord(ticket=6, time_setup=NOW, time_setup_msc=NOW * 1000,
                           type=2, reason=0, symbol='GOLD.i#', volume_initial=0.01)]

    def history_deals_get(self, start, end, group=None):
        return self._deals


class ClientTestCase(unittest.TestCase):
    def setUp(self):
        # Pin the offset so no probing happens and results are deterministic.
        os.environ['MT5_SERVER_UTC_OFFSET_SEC'] = str(OFFSET)
        self.fake = FakeMt5()
        mt5_client._mt5 = self.fake
        mt5_client._offset_cache.update({'value': None, 'source': None, 'at': 0.0})

    def tearDown(self):
        mt5_client._mt5 = None
        os.environ.pop('MT5_SERVER_UTC_OFFSET_SEC', None)


class TestDealsPagination(ClientTestCase):
    """The regression: pagination args must survive the clock-offset lookup."""

    def test_paginated_call_does_not_raise(self):
        out = mt5_client.deals(NOW - 86400, NOW)
        self.assertTrue(out['success'])

    def test_default_page_is_capped(self):
        out = mt5_client.deals(NOW - 86400, NOW)
        self.assertEqual(len(out['deals']), 100)
        self.assertEqual(out['page']['total'], 421)
        self.assertTrue(out['page']['has_more'])

    def test_offset_is_honoured_and_not_the_clock_offset(self):
        out = mt5_client.deals(NOW - 86400, NOW, limit=100, offset=400)
        self.assertEqual(out['page']['offset'], 400)
        self.assertEqual(len(out['deals']), 21)
        self.assertFalse(out['page']['has_more'])
        # The clock offset must still be reported correctly alongside it.
        self.assertEqual(out['server_utc_offset_sec'], OFFSET)

    def test_summary_mode_omits_the_page(self):
        out = mt5_client.deals(NOW - 86400, NOW, summary=True)
        self.assertNotIn('deals', out)
        self.assertNotIn('page', out)
        self.assertEqual(out['summary']['deals'], 421)

    def test_summary_covers_the_window_not_the_page(self):
        out = mt5_client.deals(NOW - 86400, NOW, limit=10)
        self.assertEqual(len(out['deals']), 10)
        self.assertEqual(out['summary']['deals'], 421)

    def test_enums_are_decoded_and_timestamps_expanded(self):
        row = mt5_client.deals(NOW - 86400, NOW, limit=1)['deals'][0]
        self.assertEqual(row['type'], 'buy')
        self.assertEqual(row['entry'], 'out')
        self.assertEqual(row['reason'], 'stop_loss')
        self.assertEqual(row['time_utc'], NOW - OFFSET)
        self.assertEqual(row['time_msc_utc'], NOW * 1000 - OFFSET * 1000)


class TestOtherRoutes(ClientTestCase):
    """Cheap wiring checks — these routes had no coverage at all."""

    def test_health(self):
        out = mt5_client.health()
        self.assertTrue(out['connected'])
        self.assertTrue(out['read_only'])
        self.assertEqual(out['server_utc_offset_sec'], OFFSET)
        self.assertEqual(out['server_utc_offset_source'], 'env')

    def test_account(self):
        self.assertEqual(mt5_client.account()['currency'], 'USD')

    def test_symbols_search(self):
        out = mt5_client.symbols(search='gold')
        self.assertEqual(out['total_available'], 2)
        self.assertEqual([s['name'] for s in out['symbols']], ['GOLD.i#'])

    def test_quote_normalises_cfd_fields(self):
        out = mt5_client.quote('GOLD.i#')
        self.assertIsNone(out['last'])
        self.assertIsNone(out['volume'])
        self.assertEqual(out['mid'], 4068.47)
        self.assertEqual(out['time_utc'], NOW)

    def test_bars_expand_timestamps(self):
        out = mt5_client.bars('GOLD.i#', timeframe='5', count=3)
        self.assertEqual(out['count'], 3)
        self.assertEqual(out['bars'][0]['time_utc'], NOW - OFFSET)
        self.assertNotIn('time', out['bars'][0])

    def test_bars_summary_mode(self):
        out = mt5_client.bars('GOLD.i#', timeframe='5', count=5, summary=True)
        self.assertEqual(out['summary']['count'], 5)
        self.assertNotIn('bars', out)

    def test_positions_decode_type(self):
        row = mt5_client.positions()['positions'][0]
        self.assertEqual(row['type'], 'sell')
        self.assertEqual(row['type_raw'], 1)
        self.assertEqual(row['time_utc'], NOW - OFFSET)

    def test_orders_decode_type(self):
        row = mt5_client.orders()['orders'][0]
        self.assertEqual(row['type'], 'buy_limit')
        self.assertEqual(row['time_utc'], NOW - OFFSET)


if __name__ == '__main__':
    unittest.main()
