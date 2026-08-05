"""
Tests for the query-parameter surface of /setups and /backtest.

This is the layer a dashboard control will eventually write to, and it has one
failure mode worth pinning: turning "no trail" into 0 or 1.0 rather than null.
The whole point of the parameter sweep is comparing a trailed run against an
untrailed one, and a config parser that cannot express "off" quietly removes
the control case from the experiment.
"""
import unittest

from bridge import _axis_value, _strategy_config
from strategy import DEFAULT_CONFIG, merged, validate


def q(**kwargs):
    """Query parameters as parse_qs produces them — every value a list."""
    return {k: [str(v)] for k, v in kwargs.items()}


class TestDefaults(unittest.TestCase):
    def test_no_parameters_means_the_default_strategy(self):
        self.assertEqual(_strategy_config({}), {})
        self.assertEqual(merged(_strategy_config({})), DEFAULT_CONFIG)

    def test_whatever_it_produces_is_valid(self):
        self.assertEqual(validate(_strategy_config(q(target_r=3, trail=1.0))), [])


class TestTrail(unittest.TestCase):
    def test_trail_none_disables_it_rather_than_setting_zero(self):
        cfg = merged(_strategy_config(q(trail='none')))
        self.assertIsNone(cfg['manage']['trail_to_be_at_r'])

    def test_trail_off_is_accepted_too(self):
        self.assertIsNone(merged(_strategy_config(q(trail='off')))['manage']['trail_to_be_at_r'])

    def test_a_number_is_kept_as_a_number(self):
        self.assertEqual(merged(_strategy_config(q(trail=0.5)))['manage']['trail_to_be_at_r'], 0.5)

    def test_omitting_trail_leaves_the_default_alone(self):
        self.assertEqual(merged(_strategy_config({}))['manage']['trail_to_be_at_r'],
                         DEFAULT_CONFIG['manage']['trail_to_be_at_r'])


class TestConditions(unittest.TestCase):
    def test_naming_conditions_switches_the_others_off(self):
        cfg = merged(_strategy_config(q(conditions='fvg')))
        on = {n for n, c in cfg['entry']['conditions'].items() if c['on']}
        self.assertEqual(on, {'fvg'})

    def test_required_is_expressible(self):
        cfg = merged(_strategy_config(q(conditions='fvg,liquidity_sweep',
                                        required='liquidity_sweep')))
        self.assertTrue(cfg['entry']['conditions']['liquidity_sweep']['required'])
        self.assertFalse(cfg['entry']['conditions']['fvg']['required'])

    def test_an_unknown_condition_name_is_rejected_by_validate(self):
        problems = validate(_strategy_config(q(conditions='moon_phase')))
        self.assertTrue(problems)


class TestNumbers(unittest.TestCase):
    def test_every_numeric_field_survives_the_round_trip(self):
        cfg = merged(_strategy_config(q(target_r=3, risk_pct=0.5, max_risk_pct=1.5,
                                        buffer_pips=10, partial_pct=60,
                                        partial_at_r=0.75, min_conditions=2,
                                        mode='at_least')))
        self.assertEqual(cfg['target']['r'], 3.0)
        self.assertEqual(cfg['size']['risk_pct'], 0.5)
        self.assertEqual(cfg['size']['max_risk_pct'], 1.5)
        self.assertEqual(cfg['stop']['buffer_pips'], 10.0)
        self.assertEqual(cfg['manage']['partial_pct'], 60.0)
        self.assertEqual(cfg['manage']['partial_at_r'], 0.75)
        self.assertEqual(cfg['entry']['min_conditions'], 2)
        self.assertEqual(cfg['entry']['mode'], 'at_least')

    def test_a_contradictory_config_is_caught_before_it_runs(self):
        # risk above the cap would refuse every trade and report zero setups
        # traded, which looks like "the strategy found nothing".
        self.assertTrue(validate(_strategy_config(q(risk_pct=5, max_risk_pct=2))))


class TestAxisValues(unittest.TestCase):
    """Sweep axes are parsed from a URL, and 'none' is the control case."""

    def test_none_survives_as_null(self):
        self.assertIsNone(_axis_value('none'))
        self.assertIsNone(_axis_value('off'))
        self.assertIsNone(_axis_value(' NULL '))

    def test_numbers_keep_their_type(self):
        self.assertEqual(_axis_value('50'), 50)
        self.assertEqual(_axis_value('1.5'), 1.5)
        self.assertIsInstance(_axis_value('50'), int)

    def test_booleans_are_recognised(self):
        # entry.conditions.fvg.on takes true/false, not 1/0.
        self.assertIs(_axis_value('true'), True)
        self.assertIs(_axis_value('false'), False)

    def test_anything_else_stays_a_string(self):
        self.assertEqual(_axis_value('close_beyond'), 'close_beyond')


if __name__ == '__main__':
    unittest.main()
