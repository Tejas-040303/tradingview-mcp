"""
Tests for the execution guards.

These are the most consequential tests in the project: every one of them
describes an order that must *not* be placed. A false negative anywhere here
costs real money, so each guard is tested in isolation and then again in
combination, because a guard that works alone and is skipped when another
already failed is a guard that disappears the moment two things go wrong at
once.
"""
import time
import unittest

import execution

NOW = 1_785_000_000
GOLD = 'GOLD.i#'


def armed(**overrides):
    """A state armed for gold on a demo account, dry run off unless said."""
    limits = {'symbols': [GOLD], **overrides.pop('limits', {})}
    out = execution.arm(execution.default_state(), minutes=60, account=12345,
                        limits=limits, dry_run=overrides.pop('dry_run', False),
                        now=NOW)
    assert out['ok'], out['problems']
    return {**out['state'], **overrides}


def order(**overrides):
    return {'symbol': GOLD, 'side': 'buy', 'lot': 0.01, 'stop': 4200.0,
            'target': 4260.0, 'risk': 5.0, **overrides}


def account(**overrides):
    return {'login': 12345, 'balance': 1000.0, 'trade_mode': 'demo', **overrides}


class TestArming(unittest.TestCase):
    def test_a_fresh_state_is_disarmed(self):
        state = execution.default_state()
        self.assertFalse(execution.is_armed(state, NOW))
        self.assertTrue(state['dry_run'])

    def test_arming_requires_naming_the_account(self):
        # Arming "whatever is connected" is how an order meant for demo
        # reaches a live account.
        out = execution.arm(execution.default_state(), 60, account=None,
                            limits={'symbols': [GOLD]}, now=NOW)
        self.assertFalse(out['ok'])
        self.assertTrue(any('account is required' in p for p in out['problems']))

    def test_arming_requires_an_explicit_symbol_list(self):
        out = execution.arm(execution.default_state(), 60, account=1, now=NOW)
        self.assertFalse(out['ok'])
        self.assertTrue(any('allowlist' in p for p in out['problems']))

    def test_the_armed_window_expires(self):
        state = armed()
        self.assertTrue(execution.is_armed(state, NOW + 3599))
        self.assertFalse(execution.is_armed(state, NOW + 3601))

    def test_an_absurd_window_is_refused(self):
        out = execution.arm(execution.default_state(), 60 * 24 * 7, account=1,
                            limits={'symbols': [GOLD]}, now=NOW)
        self.assertFalse(out['ok'])
        self.assertTrue(any('max_arm_minutes' in p for p in out['problems']))

    def test_every_arming_problem_is_reported_at_once(self):
        out = execution.arm(execution.default_state(), -5, account=None, now=NOW)
        self.assertGreaterEqual(len(out['problems']), 3)

    def test_arming_defaults_to_dry_run(self):
        out = execution.arm(execution.default_state(), 60, account=1,
                            limits={'symbols': [GOLD]}, now=NOW)
        self.assertTrue(out['state']['dry_run'])

    def test_disarm_always_works(self):
        state = execution.disarm(armed(), now=NOW)
        self.assertFalse(execution.is_armed(state, NOW))
        # And drops back to dry run, so re-arming cannot inherit live mode.
        self.assertTrue(state['dry_run'])


class TestGuards(unittest.TestCase):
    def check(self, state=None, **kw):
        return execution.check(state or armed(), kw.pop('order_', order()),
                               account=kw.pop('account_', account()),
                               positions=kw.pop('positions', []),
                               now=kw.pop('now', NOW), **kw)

    def test_a_good_order_is_allowed(self):
        out = self.check()
        self.assertTrue(out['allow'], out['reasons'])
        self.assertEqual(out['would_place']['symbol'], GOLD)

    def test_a_disarmed_state_refuses(self):
        out = self.check(state=execution.default_state())
        self.assertFalse(out['allow'])
        self.assertIn('not armed', out['reasons'])

    def test_an_expired_window_refuses(self):
        out = self.check(now=NOW + 99_999)
        self.assertTrue(any('expired' in r for r in out['reasons']))

    def test_an_order_with_no_stop_is_always_refused(self):
        # No configuration allows this. It is the position that becomes a
        # balance of zero.
        out = self.check(order_=order(stop=None))
        self.assertFalse(out['allow'])
        self.assertTrue(any('stop is required' in r for r in out['reasons']))

    def test_a_symbol_outside_the_allowlist_refuses(self):
        out = self.check(order_=order(symbol='BTCUSD#'))
        self.assertTrue(any('not in the armed symbol list' in r for r in out['reasons']))

    def test_a_lot_above_the_cap_refuses(self):
        out = self.check(order_=order(lot=5.0))
        self.assertTrue(any('above the armed max_lot' in r for r in out['reasons']))

    def test_a_nonsense_lot_refuses(self):
        for bad in (0, -1, None, 'lots'):
            out = self.check(order_=order(lot=bad))
            self.assertFalse(out['allow'], f'lot={bad!r} should be refused')

    def test_an_unknown_side_refuses(self):
        out = self.check(order_=order(side='hodl'))
        self.assertTrue(any('side must be one of' in r for r in out['reasons']))

    def test_the_wrong_account_refuses(self):
        # The terminal's own report, not what the caller believes.
        out = self.check(account_=account(login=99999))
        self.assertTrue(any('arming was for' in r for r in out['reasons']))

    def test_a_live_account_refuses_by_default(self):
        out = self.check(account_=account(trade_mode='real'))
        self.assertTrue(any('live account' in r for r in out['reasons']))

    def test_a_live_account_is_allowed_only_when_armed_for_it(self):
        state = armed(limits={'allow_live': True})
        out = execution.check(state, order(), account=account(trade_mode='real'),
                              positions=[], now=NOW)
        self.assertTrue(out['allow'], out['reasons'])

    def test_risk_above_the_cap_refuses(self):
        out = self.check(order_=order(risk=100.0))     # 10% of 1000
        self.assertTrue(any('above the' in r and '% cap' in r for r in out['reasons']))

    def test_too_many_open_positions_refuses(self):
        out = self.check(positions=[{'ticket': 1}, {'ticket': 2}])
        self.assertTrue(any('already open' in r for r in out['reasons']))

    def test_a_news_blackout_refuses(self):
        out = self.check(blackout={'in_blackout': True,
                                   'events': [{'event': 'Non-Farm Payrolls'}]})
        self.assertTrue(any('news blackout' in r for r in out['reasons']))
        self.assertTrue(any('Non-Farm Payrolls' in r for r in out['reasons']))

    def test_a_clear_blackout_does_not_refuse(self):
        out = self.check(blackout={'in_blackout': False})
        self.assertTrue(out['allow'], out['reasons'])

    def test_every_violation_is_reported_not_just_the_first(self):
        # Fixing one at a time means learning about the next by trying again,
        # and with orders "trying again" means for real.
        out = execution.check(execution.default_state(),
                              order(symbol='BTCUSD#', lot=99, stop=None),
                              account=account(login=1, trade_mode='real'),
                              positions=[{}, {}, {}], now=NOW)
        self.assertGreaterEqual(len(out['reasons']), 5)

    def test_missing_account_information_does_not_silently_pass(self):
        # With no account to check against, account-dependent guards cannot
        # run — but the rest still must.
        out = execution.check(armed(), order(stop=None), account=None, now=NOW)
        self.assertFalse(out['allow'])


class TestDailyLoss(unittest.TestCase):
    def test_losses_accumulate_and_eventually_halt_trading(self):
        state = armed()
        for _ in range(5):
            state = execution.record_result(state, -10.0, now=NOW)
        out = execution.check(state, order(), account=account(), positions=[], now=NOW)
        self.assertTrue(any('daily loss limit' in r for r in out['reasons']))

    def test_a_small_loss_does_not_halt_trading(self):
        state = execution.record_result(armed(), -10.0, now=NOW)
        out = execution.check(state, order(), account=account(), positions=[], now=NOW)
        self.assertTrue(out['allow'], out['reasons'])

    def test_profit_does_not_count_against_the_limit(self):
        state = armed()
        for net in (-40.0, 60.0):
            state = execution.record_result(state, net, now=NOW)
        out = execution.check(state, order(), account=account(), positions=[], now=NOW)
        self.assertTrue(out['allow'], out['reasons'])

    def test_the_counter_resets_on_a_new_utc_day(self):
        state = armed()
        for _ in range(5):
            state = execution.record_result(state, -10.0, now=NOW)
        tomorrow = NOW + 86_400
        rolled = execution.roll_day(state, tomorrow)
        self.assertEqual(rolled['realised_today'], 0.0)

    def test_the_day_boundary_is_utc(self):
        state = execution.roll_day(execution.default_state(), NOW)
        self.assertEqual(state['day'], time.strftime('%Y-%m-%d', time.gmtime(NOW)))


class TestDuplicateGuard(unittest.TestCase):
    def test_a_repeated_client_id_is_refused(self):
        # A retried request that fills twice is the worst failure available here.
        state = execution.record_submission(armed(), 'abc-123')
        out = execution.check(state, order(client_id='abc-123'), account=account(),
                              positions=[], now=NOW)
        self.assertTrue(any('already been submitted' in r for r in out['reasons']))

    def test_a_fresh_client_id_passes(self):
        state = execution.record_submission(armed(), 'abc-123')
        out = execution.check(state, order(client_id='abc-124'), account=account(),
                              positions=[], now=NOW)
        self.assertTrue(out['allow'], out['reasons'])

    def test_the_seen_list_stays_bounded(self):
        state = armed()
        for i in range(700):
            state = execution.record_submission(state, f'id-{i}')
        self.assertLessEqual(len(state['seen_client_ids']), 500)
        self.assertEqual(state['orders'], 700)


class TestDescribe(unittest.TestCase):
    def test_disarmed_says_so_plainly(self):
        out = execution.describe(execution.default_state(), now=NOW)
        self.assertFalse(out['armed'])
        self.assertIn('no order can be placed', out['note'])

    def test_dry_run_is_distinguished_from_live(self):
        dry = execution.describe(armed(dry_run=True), now=NOW)
        live = execution.describe(armed(), now=NOW)
        self.assertIn('validated, not sent', dry['note'])
        self.assertIn('ARMED AND LIVE', live['note'])

    def test_time_remaining_is_reported(self):
        out = execution.describe(armed(), now=NOW + 600)
        self.assertEqual(out['seconds_remaining'], 3000)


class TestPurity(unittest.TestCase):
    def test_the_guards_cannot_place_an_order_themselves(self):
        # All policy, no capability. The module that decides must not be the
        # module that can act.
        #
        # Parsed rather than grepped: the docstring names order_send while
        # explaining that the *service* calls it, and a substring check would
        # fail on the explanation instead of on the code. This tests what the
        # module can actually reach.
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(execution))

        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split('.')[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split('.')[0])
        self.assertEqual(imported, {'time'},
                         f'execution.py should import nothing but time, got {imported}')

        called = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        called |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        for forbidden in ('order_send', 'order_check', 'positions_get', 'open'):
            self.assertNotIn(forbidden, called,
                             f'execution.py must not call {forbidden}')

    def test_check_does_not_mutate_the_state_it_is_given(self):
        state = armed()
        before = dict(state)
        execution.check(state, order(), account=account(), positions=[], now=NOW)
        self.assertEqual(state, before)


if __name__ == '__main__':
    unittest.main()
