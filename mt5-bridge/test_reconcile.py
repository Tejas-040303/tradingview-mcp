"""
Tests for signal-versus-fill reconciliation.

Two hazards dominate. The first is matching: a wrong pairing corrupts two
buckets at once, turning a followed signal into both a "missed" and a
"discretionary" row. The second is the claim — "the setups you skip are the
good ones" is a serious thing to tell somebody and four trades can produce it,
so most of these pin a refusal to say anything.
"""
import unittest

from reconcile import MIN_BUCKET, match, reconcile

HOUR = 3600
START = 1_767_571_200


def signal(opened, net=10.0, r=1.0, direction='long', symbol='GOLD.i#'):
    return {'symbol': symbol, 'direction': direction, 'opened_utc': opened,
            'net': net, 'sim': {'r_multiple': r}}


def real(opened, net=10.0, direction='long', symbol='GOLD.i#'):
    return {'symbol': symbol, 'direction': direction, 'opened_utc': opened,
            'net': net}


class TestMatching(unittest.TestCase):
    def test_a_trade_near_a_signal_is_a_follow(self):
        matched, missed, extra = match([signal(START)], [real(START + 120)])
        self.assertEqual(len(matched), 1)
        self.assertEqual((missed, extra), ([], []))
        self.assertEqual(matched[0]['gap_sec'], 120)

    def test_a_trade_outside_the_tolerance_is_not(self):
        matched, missed, extra = match([signal(START)], [real(START + 4 * HOUR)])
        self.assertEqual(len(matched), 0)
        self.assertEqual((len(missed), len(extra)), (1, 1))

    def test_direction_must_agree(self):
        matched, _, _ = match([signal(START, direction='long')],
                              [real(START, direction='short')])
        self.assertEqual(matched, [])

    def test_symbol_must_agree(self):
        matched, _, _ = match([signal(START, symbol='GOLD.i#')],
                              [real(START, symbol='BTCUSD#')])
        self.assertEqual(matched, [])

    def test_the_closest_pairing_wins_globally(self):
        # First-come-first-served would let the earlier signal claim the trade
        # that belongs to the later one, and the error cascades from there.
        signals = [signal(START), signal(START + 600)]
        trades = [real(START + 590)]
        matched, missed, _ = match(signals, trades)
        self.assertEqual(matched[0]['signal']['opened_utc'], START + 600)
        self.assertEqual(missed[0]['opened_utc'], START)

    def test_one_trade_cannot_satisfy_two_signals(self):
        matched, missed, _ = match([signal(START), signal(START + 60)],
                                   [real(START + 30)])
        self.assertEqual(len(matched), 1)
        self.assertEqual(len(missed), 1)

    def test_a_signal_with_no_open_time_is_never_matched(self):
        matched, missed, _ = match([{'symbol': 'GOLD.i#', 'direction': 'long',
                                     'opened_utc': None, 'net': 1}],
                                   [real(START)])
        self.assertEqual(matched, [])
        self.assertEqual(len(missed), 1)

    def test_empty_inputs_are_tolerated(self):
        self.assertEqual(match([], []), ([], [], []))
        self.assertEqual(match(None, None), ([], [], []))


class TestBuckets(unittest.TestCase):
    def test_a_trade_with_no_signal_is_discretionary(self):
        out = reconcile([], [real(START)])
        self.assertEqual(out['discretionary'], 1)
        self.assertEqual(out['missed'], 0)

    def test_a_signal_with_no_trade_is_missed(self):
        out = reconcile([signal(START)], [])
        self.assertEqual(out['missed'], 1)
        self.assertEqual(out['follow_rate_pct'], 0.0)

    def test_follow_rate_counts_signals_not_trades(self):
        out = reconcile([signal(START), signal(START + HOUR)],
                        [real(START + 60)])
        self.assertEqual(out['follow_rate_pct'], 50.0)

    def test_follow_rate_is_null_with_no_signals(self):
        self.assertIsNone(reconcile([], [real(START)])['follow_rate_pct'])


class TestSampleGuards(unittest.TestCase):
    def test_a_thin_bucket_reports_null_with_a_reason(self):
        out = reconcile([signal(START + i * HOUR) for i in range(3)], [])
        missed = out['simulated']['missed_net']
        self.assertIsNone(missed['avg'])
        self.assertIn(f'below {MIN_BUCKET}', missed['note'])

    def test_no_finding_is_claimed_from_a_thin_sample(self):
        out = reconcile([signal(START + i * HOUR) for i in range(4)], [])
        self.assertIn('Nothing has enough trades', out['findings'][0])

    def test_a_full_bucket_reports_its_average(self):
        signals = [signal(START + i * HOUR, net=10.0) for i in range(MIN_BUCKET)]
        out = reconcile(signals, [])
        self.assertEqual(out['simulated']['missed_net']['avg'], 10.0)
        self.assertIsNone(out['simulated']['missed_net']['note'])


class TestFindings(unittest.TestCase):
    def _both_buckets(self, taken_r, skipped_r):
        signals, trades = [], []
        for i in range(MIN_BUCKET):
            at = START + i * HOUR
            signals.append(signal(at, r=taken_r))
            trades.append(real(at + 60))
        for i in range(MIN_BUCKET):
            signals.append(signal(START + (100 + i) * HOUR, r=skipped_r))
        return signals, trades

    def test_skipping_the_better_signals_is_named(self):
        out = reconcile(*self._both_buckets(taken_r=-0.5, skipped_r=1.5))
        self.assertTrue(any('skipped simulated better' in f for f in out['findings']))

    def test_selection_that_adds_value_is_named_too(self):
        # The finding must be able to come out in the flattering direction, or
        # it is not a measurement.
        out = reconcile(*self._both_buckets(taken_r=1.5, skipped_r=-0.5))
        self.assertTrue(any('took simulated better' in f for f in out['findings']))

    def test_a_small_difference_is_called_noise(self):
        out = reconcile(*self._both_buckets(taken_r=0.50, skipped_r=0.55))
        self.assertTrue(any('noise' in f for f in out['findings']))


class TestExecutionGap(unittest.TestCase):
    def test_the_gap_between_simulated_and_real_is_measured(self):
        # Same trades, both numbers: the backtest's optimism, quantified.
        signals = [signal(START + i * HOUR, net=10.0) for i in range(MIN_BUCKET)]
        trades = [real(START + i * HOUR + 60, net=6.0) for i in range(MIN_BUCKET)]
        out = reconcile(signals, trades)
        self.assertEqual(out['execution_gap']['n'], MIN_BUCKET)
        self.assertEqual(out['execution_gap']['avg'], -4.0)

    def test_a_thin_gap_sample_is_withheld(self):
        out = reconcile([signal(START)], [real(START + 60)])
        self.assertIsNone(out['execution_gap']['avg'])
        self.assertIn('below', out['execution_gap']['note'])


if __name__ == '__main__':
    unittest.main()
