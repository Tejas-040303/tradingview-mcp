"""
Tests for the live paper view.

The dominant hazard here has nothing to do with strategy quality: it is acting
on the bar that is still forming. MT5 hands back the current incomplete candle
looking exactly like a finished one, and a runner that trusts it produces
signals that appear and disappear as the minute progresses. Most of these pin
the guard against that, and the rest pin that two calls agree — a paper record
that depends on when it was asked is not a record.
"""
import unittest

from paper import (TIMEFRAME_SECONDS, affordability, closed_bars, status,
                   timeframe_seconds)
from simulate import END

GOLD = 'GOLD.i#'
MINUTE = 60
START = 1_767_571_200

PLAIN = {
    'entry': {'conditions': {'fvg': {'on': True, 'max_age_bars': 50},
                             'liquidity_sweep': {'on': False},
                             'order_block': {'on': False},
                             'fib': {'on': False}},
              'mode': 'any',
              'confirmation': {'timeframe': 5, 'type': 'close_beyond'}},
    'stop': {'anchor': 'confirmation_candle', 'buffer_pips': 0},
    'size': {'risk_pct': 1.0, 'max_risk_pct': 2.0},
    'manage': {'partial_pct': 0, 'partial_at_r': None, 'trail_to_be_at_r': None},
    'target': {'r': 2.0},
}


def bar(i, o, h, l, c, step=MINUTE):
    return {'time_utc': START + i * step, 'open': o, 'high': h, 'low': l, 'close': c}


def with_setup():
    """A bullish FVG at 101-105, a dip into it, and a decisive close above."""
    return [
        bar(0, 100, 101, 99, 100),
        bar(1, 101, 110, 101, 109),
        bar(2, 109, 112, 105, 111),
        bar(3, 111, 112, 102, 103),
        bar(4, 103, 107, 103, 106.8),   # confirmation
        bar(5, 106, 106.5, 105.5, 106), # entry at 106
    ]


class TestFormingBar(unittest.TestCase):
    def test_a_bar_whose_period_has_not_elapsed_is_dropped(self):
        bars = [bar(0, 100, 101, 99, 100), bar(1, 100, 101, 99, 100)]
        # 30 seconds into the second bar's minute.
        out = closed_bars(bars, now=START + MINUTE + 30, timeframe='1')
        self.assertEqual(len(out), 1)

    def test_a_bar_whose_period_has_elapsed_is_kept(self):
        bars = [bar(0, 100, 101, 99, 100), bar(1, 100, 101, 99, 100)]
        out = closed_bars(bars, now=START + 2 * MINUTE, timeframe='1')
        self.assertEqual(len(out), 2)

    def test_the_timeframe_determines_the_boundary(self):
        # The same instant closes an M1 bar and leaves an M5 bar forming.
        bars = [bar(0, 100, 101, 99, 100, step=300)]
        self.assertEqual(len(closed_bars(bars, now=START + 60, timeframe='1')), 1)
        self.assertEqual(len(closed_bars(bars, now=START + 60, timeframe='5')), 0)

    def test_without_a_clock_the_last_bar_is_dropped_anyway(self):
        # Assuming it closed is the failure that matters; one lost bar is not.
        bars = [bar(i, 100, 101, 99, 100) for i in range(4)]
        self.assertEqual(len(closed_bars(bars)), 3)
        self.assertEqual(len(closed_bars(bars, now=START + 99_999)), 3)

    def test_an_unknown_timeframe_falls_back_to_dropping_one(self):
        bars = [bar(i, 100, 101, 99, 100) for i in range(4)]
        self.assertIsNone(timeframe_seconds('7'))
        self.assertEqual(len(closed_bars(bars, now=START + 99_999, timeframe='7')), 3)

    def test_empty_input_is_tolerated(self):
        self.assertEqual(closed_bars([]), [])
        self.assertEqual(closed_bars(None), [])

    def test_every_timeframe_the_client_resolves_has_a_period(self):
        # Drift between the two tables would fall back to dropping one bar —
        # safe, but it hides the fact that the period is unknown.
        from normalize import TIMEFRAMES
        missing = set(TIMEFRAMES) - set(TIMEFRAME_SECONDS) - {'M'}
        extra = set(TIMEFRAME_SECONDS) - set(TIMEFRAMES)
        self.assertEqual(missing, set())
        self.assertEqual(extra, set())

    def test_monthly_has_no_fixed_period_and_says_so(self):
        # 30 days would be wrong eleven months a year.
        self.assertIsNone(timeframe_seconds('M'))

    def test_periods_are_positive_and_case_insensitive(self):
        self.assertEqual(timeframe_seconds('5'), 300)
        self.assertEqual(timeframe_seconds('d'), 86400)
        self.assertTrue(all(v > 0 for v in TIMEFRAME_SECONDS.values()))


class TestStatus(unittest.TestCase):
    def test_only_the_forming_bar_means_nothing_is_actionable(self):
        out = status([bar(0, 100, 101, 99, 100)], GOLD, PLAIN)
        self.assertEqual(out['bars_used'], 0)
        self.assertIsNone(out['position'])
        self.assertIn('lookahead', out['note'])

    def test_an_unresolved_trade_is_the_open_position_not_a_result(self):
        bars = with_setup() + [bar(6, 106, 107, 105, 106), bar(7, 106, 107, 105, 106)]
        out = status(bars, GOLD, PLAIN, balance=10_000)
        self.assertIsNotNone(out['position'])
        self.assertEqual(out['position']['direction'], 'long')
        self.assertEqual(out['position']['entry_price'], 106)
        self.assertNotIn(END, [t['exit_reason'] for t in out['recent']])

    def test_the_open_position_carries_its_levels(self):
        bars = with_setup() + [bar(6, 106, 107, 105, 106), bar(7, 106, 107, 105, 106)]
        position = status(bars, GOLD, PLAIN, balance=10_000)['position']
        self.assertEqual(position['stop'], 103)
        self.assertEqual(position['target'], 112)
        self.assertEqual(position['stop_kind'], 'initial')

    def test_a_resolved_trade_leaves_no_open_position(self):
        bars = with_setup() + [bar(6, 106, 113, 106, 112), bar(7, 112, 113, 111, 112)]
        out = status(bars, GOLD, PLAIN, balance=10_000)
        self.assertIsNone(out['position'])
        self.assertEqual(len(out['recent']), 1)
        self.assertEqual(out['recent'][0]['exit_reason'], 'take_profit')

    def test_two_calls_on_the_same_bars_agree(self):
        # A paper record that depends on when it was asked is not a record.
        bars = with_setup() + [bar(6, 106, 107, 105, 106), bar(7, 106, 107, 105, 106)]
        first = status(bars, GOLD, PLAIN, balance=10_000)
        second = status(bars, GOLD, PLAIN, balance=10_000)
        self.assertEqual(first, second)

    def test_the_number_of_dropped_bars_is_reported(self):
        bars = with_setup()
        out = status(bars, GOLD, PLAIN, balance=10_000)
        self.assertEqual(out['bars_dropped_as_forming'], 1)
        self.assertEqual(out['bars_used'], len(bars) - 1)

    def test_nothing_here_claims_to_have_traded(self):
        out = status(with_setup(), GOLD, PLAIN, balance=10_000)
        self.assertTrue(out['read_only'])


class TestPending(unittest.TestCase):
    def test_a_confirmation_on_the_last_closed_bar_is_pending(self):
        # Confirmation at index 4, and index 5 is still forming, so the entry
        # bar does not exist yet.
        out = status(with_setup(), GOLD, PLAIN, balance=10_000)
        self.assertIsNotNone(out['pending'])
        self.assertEqual(out['pending']['direction'], 'long')
        self.assertEqual(out['pending']['stop'], 103)

    def test_the_pending_entry_price_is_null_not_guessed(self):
        # The next bar's open has not happened. Filling it from the last close
        # is exactly the optimism the simulator refuses.
        pending = status(with_setup(), GOLD, PLAIN, balance=10_000)['pending']
        self.assertIsNone(pending['entry_price'])
        self.assertIn('has not happened yet', pending['entry_note'])

    def test_an_older_confirmation_is_not_pending(self):
        bars = with_setup() + [bar(6, 106, 107, 105, 106), bar(7, 106, 107, 105, 106)]
        self.assertIsNone(status(bars, GOLD, PLAIN, balance=10_000)['pending'])

    def test_nothing_pending_while_a_position_is_open(self):
        bars = with_setup() + [bar(6, 106, 107, 104, 106.8), bar(7, 106, 107, 105, 106)]
        out = status(bars, GOLD, PLAIN, balance=10_000)
        self.assertIsNotNone(out['position'])
        self.assertIsNone(out['pending'])


class TestAffordability(unittest.TestCase):
    def test_the_live_account_could_not_take_its_own_setup(self):
        out = affordability(16.84, GOLD, 2.65)
        self.assertFalse(out['ok'])
        self.assertEqual(out['minimum_risk'], 2.65)
        self.assertEqual(out['minimum_balance_for_target_risk'], 265.0)

    def test_a_thousand_dollar_account_sizes_properly(self):
        out = affordability(1000.0, GOLD, 2.65)
        self.assertTrue(out['ok'])
        self.assertEqual(out['lot'], 0.03)
        self.assertLess(out['risk_pct'], 1.0)

    def test_the_floor_scales_with_the_requested_risk(self):
        half = affordability(1000.0, GOLD, 2.65, {'size': {'risk_pct': 0.5}})
        self.assertEqual(half['minimum_balance_for_target_risk'], 530.0)

    def test_nonsense_is_refused_without_inventing_a_floor(self):
        self.assertFalse(affordability(1000.0, GOLD, 0)['ok'])


if __name__ == '__main__':
    unittest.main()
