"""
Tests for the parameter sweep.

The sweep's job is to resist a conclusion, not to produce one. Most of these
pin a refusal: no ranking without a sample, no claim of an edge when the
in-sample ordering does not survive the split, no correlation invented out of
constant inputs. A sweep that always names a winner is a sweep that will name
one on random data too.
"""
import random
import unittest

from sweep import DEFAULT_AXES, _spearman, expand, sweep

GOLD = 'GOLD.i#'
START = 1_767_571_200


def random_walk(n, seed=7, step=0.45):
    """Bars with no edge in them, at gold-like prices."""
    rng = random.Random(seed)
    price, bars = 4000.0, []
    for i in range(n):
        o = price
        c = o + rng.gauss(0, step)
        bars.append({'time_utc': START + i * 300, 'open': round(o, 2),
                     'high': round(max(o, c) + abs(rng.gauss(0, 0.25)), 2),
                     'low': round(min(o, c) - abs(rng.gauss(0, 0.25)), 2),
                     'close': round(c, 2)})
        price = c
    return bars


class TestExpand(unittest.TestCase):
    def test_the_default_grid_is_the_product_of_its_axes(self):
        self.assertEqual(len(expand()), 5 * 3 * 2)

    def test_the_control_case_is_in_the_grid(self):
        # No trail at all. A sweep of trail values without "off" cannot answer
        # whether trailing helps.
        trails = [c['manage']['trail_to_be_at_r'] for c in expand()]
        self.assertIn(None, trails)

    def test_invalid_combinations_are_dropped_not_run(self):
        # Trailing at 3R with a 3R target is unreachable; running it would
        # report zero improvement and read as a finding.
        configs = expand({'manage.trail_to_be_at_r': [1.0, 3.0], 'target.r': [3.0]})
        self.assertEqual([c['manage']['trail_to_be_at_r'] for c in configs], [1.0])

    def test_a_base_config_is_carried_into_every_variant(self):
        configs = expand({'target.r': [2.0, 3.0]}, base={'symbols': ['BTCUSD#']})
        self.assertTrue(all(c['symbols'] == ['BTCUSD#'] for c in configs))


class TestSpearman(unittest.TestCase):
    def test_perfect_agreement(self):
        self.assertEqual(_spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)

    def test_perfect_reversal(self):
        self.assertEqual(_spearman([1, 2, 3, 4], [40, 30, 20, 10]), -1.0)

    def test_constant_input_is_none_not_zero(self):
        # Zero would read as "measured, no relationship". Nothing was measured.
        self.assertIsNone(_spearman([1, 1, 1, 1], [1, 2, 3, 4]))

    def test_too_few_points_is_none(self):
        self.assertIsNone(_spearman([1, 2], [1, 2]))


class TestSplit(unittest.TestCase):
    BARS = random_walk(600)

    def test_the_split_is_chronological(self):
        out = sweep(self.BARS, GOLD, {'target.r': [2.0]}, balance=5000)
        self.assertLess(out['split']['in_sample_to'], out['split']['out_of_sample_from'])
        self.assertEqual(out['split']['in_sample_bars'] + out['split']['out_of_sample_bars'],
                         len(self.BARS))

    def test_a_split_that_empties_one_half_is_refused(self):
        out = sweep(self.BARS, GOLD, {'target.r': [2.0]}, split=1.0)
        self.assertFalse(out['success'])
        self.assertIn('empty', out['error'])

    def test_no_bars_is_reported_not_crashed(self):
        self.assertEqual(sweep([], GOLD)['rows'], [])


class TestRanking(unittest.TestCase):
    BARS = random_walk(1200)

    def test_both_halves_are_reported_for_every_configuration(self):
        out = sweep(self.BARS, GOLD, {'target.r': [2.0, 3.0]}, balance=5000)
        for row in out['rows']:
            self.assertIn('in_sample', row)
            self.assertIn('out_of_sample', row)

    def test_a_thin_configuration_is_reported_but_not_ranked(self):
        out = sweep(self.BARS, GOLD, {'target.r': [2.0]}, balance=5000,
                    min_trades=10_000)
        self.assertTrue(out['rows'])
        self.assertFalse(any(r['ranked'] for r in out['rows']))
        self.assertEqual(out['summary']['ranked'], 0)
        self.assertIn('nothing can be ranked', out['summary']['note'])

    def test_unranked_configurations_sort_below_ranked_ones(self):
        out = sweep(self.BARS, GOLD, DEFAULT_AXES, balance=5000)
        flags = [r['ranked'] for r in out['rows']]
        self.assertEqual(flags, sorted(flags, reverse=True))


class TestSetupCache(unittest.TestCase):
    """Detection is cached across configs; it must not be shared across entries."""

    BARS = random_walk(1500, seed=2)

    def test_varying_an_entry_axis_still_detects_separately(self):
        # If the cache keyed too loosely, every row here would carry identical
        # trade counts — a config sweep that silently tested one config.
        out = sweep(self.BARS, GOLD, {
            'entry.conditions.liquidity_sweep.on': [True, False],
        }, balance=5000)
        counts = {r['in_sample']['trades'] for r in out['rows']}
        self.assertEqual(len(out['rows']), 2)
        self.assertGreater(len(counts), 1)

    def test_management_axes_produce_the_same_signals(self):
        # The other direction: management cannot change which setups exist, so
        # a difference here would mean the cache was being bypassed wrongly.
        from simulate import find_setups
        cut = int(len(self.BARS) * 0.7)
        a = find_setups(self.BARS[:cut], {'manage': {'trail_to_be_at_r': None}})
        b = find_setups(self.BARS[:cut], {'manage': {'trail_to_be_at_r': 1.5}})
        self.assertEqual(a, b)


class TestVerdict(unittest.TestCase):
    def test_random_data_does_not_produce_a_trustworthy_edge(self):
        # The test this module exists for, run across several seeds because a
        # single one hid the bug: with only the correlation and median checks,
        # two seeds in five endorsed a random-walk winner. One seed passing is
        # not evidence that the guard works.
        for seed in (3, 5, 11, 23, 41):
            out = sweep(random_walk(3000, seed=seed), GOLD, DEFAULT_AXES,
                        balance=5000)
            self.assertFalse(out['summary'].get('trustworthy'),
                             f'seed {seed} endorsed an edge in random data')

    def test_the_noise_floor_rises_when_the_winner_has_fewer_trades(self):
        # A config topping the table on fifteen volatile trades must clear a
        # much higher bar than one doing it on two hundred — that asymmetry is
        # the whole point of measuring the floor rather than fixing it.
        from sweep import _noise_floor
        thin = _noise_floor({'trades': 15, 'r_stdev': 1.8}, 30)
        thick = _noise_floor({'trades': 200, 'r_stdev': 1.8}, 30)
        self.assertGreater(thin, thick)

    def test_the_floor_grows_with_the_number_of_configurations_searched(self):
        from sweep import _noise_floor
        few = _noise_floor({'trades': 100, 'r_stdev': 1.5}, 4)
        many = _noise_floor({'trades': 100, 'r_stdev': 1.5}, 200)
        self.assertGreater(many, few)

    def test_an_unmeasurable_floor_is_none_rather_than_a_passed_test(self):
        from sweep import _noise_floor
        self.assertIsNone(_noise_floor({'trades': 100, 'r_stdev': None}, 30))
        self.assertIsNone(_noise_floor({'trades': 1, 'r_stdev': 1.5}, 30))

    def test_a_losing_winner_is_never_endorsed(self):
        # The bug this catches: rank correlation on a random walk came out at
        # 0.55 and the verdict called a -0.01 R winner "worth acting on".
        # Management parameters order consistently in any window because the
        # effect is mechanical, so correlation alone cannot carry the claim.
        out = sweep(random_walk(4000, seed=23), GOLD, DEFAULT_AXES, balance=5000)
        summary = out['summary']
        self.assertLessEqual(summary['best_out_of_sample_avg_r'], 0)
        self.assertFalse(summary['profitable_out_of_sample'])
        self.assertFalse(summary['trustworthy'])
        self.assertIn('degrees of loss', summary['reading'])

    def test_the_number_of_configurations_tested_is_stated(self):
        out = sweep(random_walk(1200), GOLD, DEFAULT_AXES, balance=5000)
        self.assertIn(str(out['summary']['of']), out['summary']['caveat'])

    def test_the_overfit_gap_is_reported_alongside_the_winner(self):
        out = sweep(random_walk(2000, seed=3), GOLD, DEFAULT_AXES, balance=5000)
        if out['summary']['ranked']:
            self.assertIn('overfit_gap', out['summary'])
            self.assertIn('best_in_sample_avg_r', out['summary'])

    def test_the_reading_always_says_something_about_the_correlation(self):
        out = sweep(random_walk(2000, seed=5), GOLD, DEFAULT_AXES, balance=5000)
        self.assertTrue(out['summary'].get('reading') or out['summary'].get('note'))


if __name__ == '__main__':
    unittest.main()
