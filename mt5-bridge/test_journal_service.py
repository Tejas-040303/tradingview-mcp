"""
Tests for the journal write service.

The routing is thin, so most of these guard the boundary rather than the logic:
this is the first process in the project allowed to write, and what it is
*unable* to do matters more than what it does. The isolation tests are
structural on purpose — a promise in a docstring is not a boundary.
"""
import unittest

import journal
import journal_service as service

NOW = 1_785_000_000


def signal(time_utc=NOW, direction='long'):
    return {'time_utc': time_utc, 'direction': direction,
            'conditions': ['fvg'], 'stop': 4200.0, 'target': 4230.0}


class TestGet(unittest.TestCase):
    def setUp(self):
        self.conn = journal.connect()

    def test_health_states_what_it_cannot_do(self):
        out = service.handle_get('/health', {}, self.conn)
        self.assertTrue(out['writable'])
        self.assertFalse(out['can_trade'])

    def test_summary_and_signals_read_back(self):
        journal.record_signals(self.conn, [signal()], 'GOLD.i#')
        self.assertEqual(service.handle_get('/summary', {}, self.conn)['signals'], 1)
        self.assertEqual(service.handle_get('/signals', {}, self.conn)['count'], 1)

    def test_query_filters_are_passed_through(self):
        journal.record_signals(self.conn, [signal()], 'GOLD.i#')
        out = service.handle_get('/signals', {'symbol': ['BTCUSD#']}, self.conn)
        self.assertEqual(out['count'], 0)

    def test_an_unknown_route_is_a_value_error(self):
        with self.assertRaises(ValueError):
            service.handle_get('/nope', {}, self.conn)


class TestPost(unittest.TestCase):
    def setUp(self):
        self.conn = journal.connect()

    def test_signals_are_recorded(self):
        out = service.handle_post('/signals',
                                  {'symbol': 'GOLD.i#', 'signals': [signal()]},
                                  self.conn)
        self.assertEqual(out['added'], 1)

    def test_posting_the_same_window_again_adds_nothing(self):
        body = {'symbol': 'GOLD.i#', 'signals': [signal()]}
        service.handle_post('/signals', body, self.conn)
        self.assertEqual(service.handle_post('/signals', body, self.conn)['added'], 0)

    def test_a_missing_symbol_is_refused(self):
        with self.assertRaises(ValueError):
            service.handle_post('/signals', {'signals': [signal()]}, self.conn)

    def test_a_decision_needs_every_identifying_field(self):
        for missing in ('symbol', 'time_utc', 'direction', 'action'):
            body = {'symbol': 'GOLD.i#', 'time_utc': NOW,
                    'direction': 'long', 'action': 'taken'}
            del body[missing]
            with self.assertRaises(ValueError, msg=f'{missing} should be required'):
                service.handle_post('/decision', body, self.conn)

    def test_a_decision_on_an_unrecorded_signal_is_a_lookup_error(self):
        # Mapped to 404 by the handler rather than 400: the request is
        # well-formed, the signal simply is not there.
        with self.assertRaises(LookupError):
            service.handle_post('/decision',
                                {'symbol': 'GOLD.i#', 'time_utc': NOW,
                                 'direction': 'long', 'action': 'taken'}, self.conn)

    def test_a_full_round_trip(self):
        service.handle_post('/signals', {'symbol': 'GOLD.i#', 'signals': [signal()]},
                            self.conn)
        service.handle_post('/decision',
                            {'symbol': 'GOLD.i#', 'time_utc': NOW, 'direction': 'long',
                             'action': 'skipped', 'reason': 'news in 10 minutes'},
                            self.conn)
        out = service.handle_get('/summary', {}, self.conn)
        self.assertEqual(out['coverage_pct'], 100.0)
        self.assertEqual(out['skip_reasons_claimed'], {'news in 10 minutes': 1})

    def test_trade_annotation_requires_a_position(self):
        with self.assertRaises(ValueError):
            service.handle_post('/trade', {'exit_kind': 'panic'}, self.conn)

    def test_screenshot_requires_a_path_and_kind(self):
        with self.assertRaises(ValueError):
            service.handle_post('/screenshot', {'path': '/a.png'}, self.conn)


class TestPruneRoute(unittest.TestCase):
    def setUp(self):
        self.conn = journal.connect()
        journal.attach_screenshot(self.conn, '/old.png', 'entry', now=NOW - 30 * 86400)

    def test_a_bare_post_is_a_dry_run(self):
        # A retention endpoint that deletes on an empty body is one that
        # eventually deletes by accident.
        out = service.handle_post('/prune', {}, self.conn)
        self.assertFalse(out['applied'])
        self.assertEqual(out['candidates'], 1)

    def test_deleting_requires_saying_so(self):
        out = service.handle_post('/prune', {'apply': True}, self.conn)
        self.assertTrue(out['applied'])

    def test_the_window_is_configurable_from_the_body(self):
        out = service.handle_post('/prune', {'keep_days': 90}, self.conn)
        self.assertEqual(out['candidates'], 0)


class TestBoundary(unittest.TestCase):
    """What this service cannot do matters more than what it can."""

    def test_it_cannot_reach_the_broker(self):
        import inspect
        source = inspect.getsource(service)
        for forbidden in ('mt5_client', 'MetaTrader5', 'order_send', 'positions'):
            self.assertNotIn(forbidden, source,
                             f'the journal service must not reference {forbidden}')

    def test_it_binds_loopback_only(self):
        import inspect
        source = inspect.getsource(service)
        self.assertIn("'127.0.0.1'", source)
        self.assertNotIn("'0.0.0.0'", source)

    def test_it_runs_on_its_own_port(self):
        # Sharing the read bridge's port would mean sharing its process, and
        # the read bridge's value is that it cannot be made to write.
        import bridge
        self.assertNotEqual(service.DEFAULT_PORT, bridge.DEFAULT_PORT)

    def test_the_read_bridge_still_refuses_to_write(self):
        import inspect
        source = inspect.getsource(__import__('bridge'))
        self.assertNotIn('def do_POST', source)

    def test_entries_cannot_be_deleted(self):
        # A record you can quietly remove after a bad trade is not a record.
        import inspect
        source = inspect.getsource(service.Handler.do_DELETE)
        self.assertIn('does not delete entries', source)


if __name__ == '__main__':
    unittest.main()
