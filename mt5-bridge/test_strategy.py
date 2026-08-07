"""
Tests for the strategy config and position sizing.

Sizing carries the weight here. The account this was built for risked roughly
16% per trade because the minimum lot was the only lot available — these pin the
behaviour that refuses that rather than reporting it as "1% risk".
"""
import unittest

from strategy import (DEFAULT_CONFIG, conditions_met, merged, minimum_balance,
                      position_size, spec_for, validate)

GOLD = 'GOLD.i#'


class TestMerge(unittest.TestCase):
    def test_nested_values_override_without_dropping_siblings(self):
        cfg = merged({'size': {'risk_pct': 0.5}})
        self.assertEqual(cfg['size']['risk_pct'], 0.5)
        self.assertEqual(cfg['size']['max_risk_pct'],
                         DEFAULT_CONFIG['size']['max_risk_pct'])

    def test_defaults_are_not_mutated_by_a_merge(self):
        merged({'entry': {'conditions': {'fvg': {'on': False}}}})
        self.assertTrue(DEFAULT_CONFIG['entry']['conditions']['fvg']['on'])


class TestValidate(unittest.TestCase):
    def test_a_default_config_is_valid(self):
        self.assertEqual(validate({}), [])

    def test_all_conditions_off_cannot_trigger(self):
        cfg = {'entry': {'conditions': {name: {'on': False}
                                        for name in DEFAULT_CONFIG['entry']['conditions']}}}
        self.assertTrue(any('nothing can ever trigger' in p for p in validate(cfg)))

    def test_min_conditions_above_the_number_enabled_is_unreachable(self):
        cfg = {'entry': {'mode': 'at_least', 'min_conditions': 4,
                         'conditions': {'fvg': {'on': True},
                                        'liquidity_sweep': {'on': False}}}}
        self.assertTrue(any('can never trigger' in p for p in validate(cfg)))

    def test_risk_above_the_cap_would_refuse_everything(self):
        problems = validate({'size': {'risk_pct': 5.0, 'max_risk_pct': 2.0}})
        self.assertTrue(any('every trade would be refused' in p for p in problems))

    def test_trailing_at_or_past_the_target_is_pointless(self):
        problems = validate({'manage': {'trail_to_be_at_r': 2.0}, 'target': {'r': 2.0}})
        self.assertTrue(any('at or beyond the target' in p for p in problems))

    def test_no_trail_is_allowed(self):
        # Disabling the trail entirely is the control case for the sweep.
        self.assertEqual(validate({'manage': {'trail_to_be_at_r': None}}), [])

    def test_every_problem_is_reported_not_just_the_first(self):
        problems = validate({'entry': {'mode': 'nonsense'},
                             'size': {'risk_pct': -1},
                             'target': {'r': 0}})
        self.assertGreaterEqual(len(problems), 3)


class TestConditionsMet(unittest.TestCase):
    ANY = {'entry': {'mode': 'any',
                     'conditions': {'fvg': {'on': True},
                                    'liquidity_sweep': {'on': True},
                                    'order_block': {'on': False},
                                    'fib': {'on': False}}}}

    def test_any_mode_needs_one_enabled_condition(self):
        self.assertTrue(conditions_met({'fvg'}, self.ANY))
        self.assertTrue(conditions_met({'liquidity_sweep'}, self.ANY))
        self.assertFalse(conditions_met(set(), self.ANY))

    def test_a_disabled_condition_does_not_count(self):
        self.assertFalse(conditions_met({'order_block'}, self.ANY))

    def test_all_mode_needs_every_enabled_condition(self):
        cfg = {**self.ANY, 'entry': {**self.ANY['entry'], 'mode': 'all'}}
        self.assertFalse(conditions_met({'fvg'}, cfg))
        self.assertTrue(conditions_met({'fvg', 'liquidity_sweep'}, cfg))

    def test_at_least_counts_them(self):
        cfg = {'entry': {'mode': 'at_least', 'min_conditions': 2,
                         'conditions': {'fvg': {'on': True},
                                        'liquidity_sweep': {'on': True},
                                        'order_block': {'on': True},
                                        'fib': {'on': False}}}}
        self.assertFalse(conditions_met({'fvg'}, cfg))
        self.assertTrue(conditions_met({'fvg', 'order_block'}, cfg))

    def test_required_conditions_override_the_mode(self):
        # "sweep is mandatory, FVG is a bonus" — expressible in any mode.
        cfg = {'entry': {'mode': 'any',
                         'conditions': {'fvg': {'on': True},
                                        'liquidity_sweep': {'on': True, 'required': True},
                                        'order_block': {'on': False},
                                        'fib': {'on': False}}}}
        self.assertFalse(conditions_met({'fvg'}, cfg))
        self.assertTrue(conditions_met({'fvg', 'liquidity_sweep'}, cfg))


class TestPositionSize(unittest.TestCase):
    def test_lot_is_rounded_down_never_up(self):
        # 1% of 1000 is 10; a 2.65 stop on gold costs 265 per lot, so 0.0377
        # lots. Rounding up would exceed the risk that was asked for.
        out = position_size(1000.0, 2.65, GOLD, {'size': {'risk_pct': 1.0}})
        self.assertTrue(out['ok'])
        self.assertEqual(out['lot'], 0.03)
        self.assertLess(out['risk'], 10.0)

    def test_realised_risk_is_reported_not_the_requested_one(self):
        out = position_size(1000.0, 2.65, GOLD, {'size': {'risk_pct': 1.0}})
        self.assertEqual(out['wanted_risk'], 10.0)
        self.assertNotEqual(out['risk'], out['wanted_risk'])

    def test_a_tiny_balance_is_refused_rather_than_over_risked(self):
        # The real case: 16.84 balance, 2.65 stop. One minimum lot risks 2.65,
        # which is 15.7% of the account.
        out = position_size(16.84, 2.65, GOLD)
        self.assertFalse(out['ok'])
        self.assertEqual(out['minimum_risk'], 2.65)
        self.assertAlmostEqual(out['minimum_risk_pct'], 15.74, places=1)
        self.assertIn('above the', out['reason'])

    def test_minimum_lot_is_allowed_when_it_fits_under_the_cap(self):
        # Same stop, a balance that can carry it: 2.65 is 1.3% of 200.
        out = position_size(200.0, 2.65, GOLD)
        self.assertTrue(out['ok'])
        self.assertEqual(out['lot'], 0.01)
        self.assertLess(out['risk_pct'], 2.0)

    def test_gold_contract_maths_matches_the_broker(self):
        # 0.01 lot, one dollar of movement, one dollar of P&L.
        out = position_size(10_000.0, 1.0, GOLD, {'size': {'risk_pct': 0.01}})
        self.assertEqual(out['lot'], 0.01)
        self.assertEqual(out['risk'], 1.0)

    def test_a_wider_stop_buys_fewer_lots(self):
        tight = position_size(1000.0, 1.0, GOLD)
        wide = position_size(1000.0, 5.0, GOLD)
        self.assertGreater(tight['lot'], wide['lot'])
        # Risk stays comparable — that is the whole point of sizing off the stop.
        self.assertAlmostEqual(tight['risk'], wide['risk'], delta=3.0)

    def test_nonsense_inputs_are_refused_with_a_reason(self):
        self.assertFalse(position_size(1000.0, 0, GOLD)['ok'])
        self.assertFalse(position_size(0, 2.65, GOLD)['ok'])
        self.assertIn('positive', position_size(1000.0, -1, GOLD)['reason'])


class TestMinimumBalance(unittest.TestCase):
    def test_the_floor_this_strategy_needs(self):
        # A 2.65 stop at minimum lot risks 2.65; for that to be 1%, 265 is needed.
        self.assertEqual(minimum_balance(2.65, GOLD, risk_pct=1.0), 265.0)
        self.assertEqual(minimum_balance(2.65, GOLD, risk_pct=0.5), 530.0)

    def test_none_for_nonsense(self):
        self.assertIsNone(minimum_balance(0, GOLD))
        self.assertIsNone(minimum_balance(2.65, GOLD, risk_pct=0))


class TestSpecs(unittest.TestCase):
    def test_known_symbols_have_explicit_specs(self):
        self.assertEqual(spec_for(GOLD)['contract_size'], 100.0)
        # Gold's pip is 0.10. It was 0.01 here once, which made every
        # pip-denominated setting wrong by ten while looking reasonable.
        self.assertEqual(spec_for(GOLD)['pip'], 0.10)

    def test_the_contract_maths_is_independent_of_the_pip(self):
        # 0.01 lot, one dollar of movement, one dollar of P&L — unchanged by
        # the pip correction, which is what makes that correction safe.
        out = position_size(10_000.0, 1.0, GOLD, {'size': {'risk_pct': 0.01}})
        self.assertEqual(out['risk'], 1.0)

    def test_unknown_symbols_fall_back_rather_than_crash(self):
        self.assertIsNotNone(spec_for('SOMETHING#')['contract_size'])


if __name__ == '__main__':
    unittest.main()
