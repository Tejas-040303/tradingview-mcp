"""
Tests for the execution service.

The policy is tested in test_execution.py; these cover the wiring, and the
wiring is where a safe policy gets bypassed. The recurring assertion is that
the service *refuses* — when disarmed, when it cannot see the terminal, when
it cannot write the audit log. A trading service that fails open is worse than
one that does not exist.

`_send` is never called here. It is the one function that can place an order
and it needs a live Windows terminal, so every test below stops at the point
where it would be reached and asserts what happened instead.
"""
import json
import os
import tempfile
import unittest

import execution
import execution_service as service

GOLD = 'GOLD.i#'


def order(**overrides):
    return {'symbol': GOLD, 'side': 'buy', 'lot': 0.01, 'stop': 4200.0,
            'target': 4260.0, 'risk': 5.0, **overrides}


class ServiceTest(unittest.TestCase):
    """Each test gets a fresh disarmed state and its own audit file."""

    def setUp(self):
        service._state = execution.default_state()
        self.tmp = tempfile.TemporaryDirectory()
        self.audit_path = os.path.join(self.tmp.name, 'audit.jsonl')
        self._real_audit = service.audit
        service.audit = lambda event, payload, path=self.audit_path: \
            self._real_audit(event, payload, path)
        # No terminal in CI, so every test says explicitly what it is
        # connected to. Left unset, the service refuses — which is itself a
        # test below.
        self._real_view = service._terminal_view
        service._terminal_view = lambda: (
            {'login': 12345, 'balance': 1000.0, 'trade_mode': 'demo'}, [])

    def tearDown(self):
        service.audit = self._real_audit
        service._terminal_view = self._real_view
        service._state = execution.default_state()
        self.tmp.cleanup()

    def arm(self, **kw):
        body = {'minutes': 60, 'account': 12345,
                'limits': {'symbols': [GOLD]}, **kw}
        return service.handle_post('/arm', body)

    def audit_events(self):
        if not os.path.exists(self.audit_path):
            return []
        with open(self.audit_path, encoding='utf-8') as handle:
            return [json.loads(line) for line in handle if line.strip()]


class TestArming(ServiceTest):
    def test_the_service_starts_disarmed(self):
        payload = service.Handler.__dict__  # noqa: F841 - readability only
        status = execution.describe(service._state)
        self.assertFalse(status['armed'])
        self.assertIn('no order can be placed', status['note'])

    def test_arming_works_and_is_audited(self):
        payload, status = self.arm()
        self.assertEqual(status, 200)
        self.assertTrue(payload['armed'])
        self.assertEqual([e['event'] for e in self.audit_events()], ['arm'])

    def test_arming_defaults_to_dry_run(self):
        payload, _ = self.arm()
        self.assertTrue(payload['dry_run'])

    def test_going_live_takes_an_explicit_false(self):
        payload, _ = self.arm(dry_run=False)
        self.assertFalse(payload['dry_run'])
        self.assertIn('ARMED AND LIVE', payload['note'])

    def test_a_bad_arm_request_is_rejected_with_every_problem(self):
        payload, status = service.handle_post('/arm', {'minutes': -1})
        self.assertEqual(status, 400)
        self.assertGreaterEqual(len(payload['problems']), 2)

    def test_disarm_is_audited_and_returns_to_dry_run(self):
        self.arm(dry_run=False)
        payload, status = service.handle_post('/disarm', {})
        self.assertEqual(status, 200)
        self.assertFalse(payload['armed'])
        self.assertTrue(payload['dry_run'])
        self.assertIn('disarm', [e['event'] for e in self.audit_events()])


class TestOrders(ServiceTest):
    def test_a_disarmed_service_refuses(self):
        payload, status = service.handle_post('/order', {'order': order()})
        self.assertEqual(status, 409)
        self.assertFalse(payload['placed'])
        self.assertIn('not armed', payload['reasons'])

    def test_dry_run_validates_without_sending(self):
        # _send is not stubbed anywhere in this file; reaching it would raise
        # on the MetaTrader5 import, so a pass here proves it was not called.
        self.arm()
        payload, status = service.handle_post('/order', {'order': order()})
        self.assertEqual(status, 200)
        self.assertFalse(payload['placed'])
        self.assertTrue(payload['dry_run'])
        self.assertEqual(payload['would_place']['symbol'], GOLD)

    def test_a_refused_order_is_still_audited(self):
        # The refusals are the interesting half of the record.
        service.handle_post('/order', {'order': order()})
        events = self.audit_events()
        self.assertEqual(events[-1]['event'], 'order_request')
        self.assertFalse(events[-1]['verdict']['allow'])

    def test_an_order_with_no_stop_is_refused_even_when_armed(self):
        self.arm(dry_run=False)
        payload, status = service.handle_post('/order', {'order': order(stop=None)})
        self.assertEqual(status, 409)
        self.assertTrue(any('stop is required' in r for r in payload['reasons']))

    def test_a_symbol_outside_the_allowlist_is_refused(self):
        self.arm(dry_run=False)
        payload, _ = service.handle_post('/order', {'order': order(symbol='BTCUSD#')})
        self.assertFalse(payload['placed'])

    def test_the_terminals_account_is_checked_not_the_callers_claim(self):
        service._terminal_view = lambda: (
            {'login': 99999, 'balance': 1000.0, 'trade_mode': 'demo'}, [])
        self.arm(dry_run=False)
        payload, _ = service.handle_post('/order', {'order': order()})
        self.assertTrue(any('arming was for' in r for r in payload['reasons']))

    def test_a_live_account_is_refused_unless_armed_for_one(self):
        service._terminal_view = lambda: (
            {'login': 12345, 'balance': 1000.0, 'trade_mode': 'real'}, [])
        self.arm(dry_run=False)
        payload, _ = service.handle_post('/order', {'order': order()})
        self.assertTrue(any('live account' in r for r in payload['reasons']))

    def test_an_unreachable_terminal_refuses_rather_than_trading_blind(self):
        def broken():
            raise ConnectionError('terminal not running')
        service._terminal_view = broken
        self.arm(dry_run=False)
        payload, status = service.handle_post('/order', {'order': order()})
        self.assertEqual(status, 409)
        self.assertTrue(any('refusing rather than trading blind' in r
                            for r in payload['reasons']))

    def test_a_missing_terminal_does_not_hide_the_other_reasons(self):
        # Short-circuiting would answer "cannot read the terminal" to an order
        # that was also unarmed and missing a stop, and the caller would fix
        # them one round trip at a time.
        def broken():
            raise ConnectionError('terminal not running')
        service._terminal_view = broken
        payload, _ = service.handle_post('/order', {'order': order(stop=None)})
        self.assertTrue(any('not armed' in r for r in payload['reasons']))
        self.assertTrue(any('stop is required' in r for r in payload['reasons']))
        self.assertTrue(any('trading blind' in r for r in payload['reasons']))

    def test_a_blackout_passed_in_is_honoured(self):
        self.arm(dry_run=False)
        payload, _ = service.handle_post('/order', {
            'order': order(), 'blackout': {'in_blackout': True, 'events': []}})
        self.assertTrue(any('news blackout' in r for r in payload['reasons']))

    def test_an_unwritable_audit_log_stops_trading(self):
        # The right response to "I cannot record this" is to stop, not to
        # trade unrecorded.
        self.arm(dry_run=False)
        service.audit = self.broken_audit
        payload, status = service.handle_post('/order', {'order': order()})
        self.assertEqual(status, 503)
        self.assertFalse(payload['placed'])
        self.assertTrue(any('cannot be recorded is not placed' in r
                            for r in payload['reasons']))

    @staticmethod
    def broken_audit(event, payload, path=None):
        raise OSError('disk full')

    def test_an_unwritable_audit_log_refuses_to_arm(self):
        # Fails closed: the state is left untouched, so the service stays
        # disarmed rather than armed-but-unrecorded.
        service.audit = self.broken_audit
        payload, status = self.arm(dry_run=False)
        self.assertEqual(status, 503)
        self.assertFalse(execution.is_armed(service._state))

    def test_disarming_works_even_with_a_broken_audit_log(self):
        # A kill switch with preconditions is not a kill switch.
        self.arm(dry_run=False)
        service.audit = self.broken_audit
        payload, status = service.handle_post('/disarm', {})
        self.assertEqual(status, 200)
        self.assertFalse(payload['armed'])
        self.assertFalse(execution.is_armed(service._state))
        self.assertIn('not recorded', payload['audit_warning'])


class TestBoundary(ServiceTest):
    def test_it_runs_on_its_own_port(self):
        import bridge
        import journal_service
        ports = {service.DEFAULT_PORT, bridge.DEFAULT_PORT,
                 journal_service.DEFAULT_PORT}
        self.assertEqual(len(ports), 3, 'each service needs its own port')

    def test_it_binds_loopback_only(self):
        import inspect
        source = inspect.getsource(service)
        self.assertIn("'127.0.0.1'", source)
        self.assertNotIn("'0.0.0.0'", source)

    def test_no_strategy_is_reachable_from_here(self):
        # A bug in the detectors must not be able to become a bug that places
        # orders, and the way to guarantee that is for the detectors to be
        # unreachable rather than unused.
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(service))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split('.')[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split('.')[0])
        for forbidden in ('detectors', 'simulate', 'strategy', 'sweep', 'paper'):
            self.assertNotIn(forbidden, imported,
                             f'the execution service must not import {forbidden}')

    def test_only_one_function_can_place_an_order(self):
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(service))
        senders = [node.name for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef)
                   and 'order_send' in ast.dump(node)]
        self.assertEqual(senders, ['_send'])

    def test_the_read_bridge_and_journal_still_cannot_trade(self):
        import ast
        import inspect
        for name in ('bridge', 'journal_service'):
            module = __import__(name)
            self.assertNotIn('order_send', ast.dump(ast.parse(inspect.getsource(module))),
                             f'{name} must not be able to place an order')


if __name__ == '__main__':
    unittest.main()
