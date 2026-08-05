"""
Tests for stop-loss recoverability from order history.

This module's whole job is to answer one question honestly, including when the
answer is "not enough data to say". Most of these check that it withholds a
verdict rather than guessing one.
"""
import unittest

from stops import MIN_SAMPLE, coverage, implied_risk, is_entry_order


def order(position_id=1, otype='buy', price=4000.0, sl=3990.0, tp=4020.0,
          symbol='GOLD.i#', position_by_id=0, ticket=None):
    # A position's id is the ticket of the order that opened it, so an entry
    # order has ticket == position_id by default.
    return {'position_id': position_id, 'type': otype, 'price_open': price,
            'sl': sl, 'tp': tp, 'symbol': symbol,
            'position_by_id': position_by_id,
            'ticket': position_id if ticket is None else ticket}


def trade(position_id=1, is_open=False):
    return {'position_id': position_id, 'open': is_open, 'net': -5.0}


class TestEntryDetection(unittest.TestCase):
    def test_a_filled_buy_with_a_position_is_an_entry(self):
        self.assertTrue(is_entry_order(order()))

    def test_orders_without_a_position_are_skipped(self):
        self.assertFalse(is_entry_order(order(position_id=None)))
        self.assertFalse(is_entry_order(order(position_id=0)))

    def test_the_closing_order_is_not_an_entry(self):
        # Both rows share a position_id and both are ordinary buys or sells. On a
        # real account this counted every position twice — 1079 "entries"
        # against 534 closed trades.
        opening = order(position_id=500, ticket=500)
        closing = order(position_id=500, ticket=501, otype='sell')
        self.assertTrue(is_entry_order(opening))
        self.assertFalse(is_entry_order(closing))

    def test_orders_without_a_ticket_fall_back_gracefully(self):
        row = order(position_id=7)
        row['ticket'] = None
        self.assertTrue(is_entry_order(row))

    def test_close_by_orders_are_not_entries(self):
        self.assertFalse(is_entry_order(order(position_by_id=77)))

    def test_non_trading_order_types_are_skipped(self):
        self.assertFalse(is_entry_order(order(otype='balance')))

    def test_pending_order_types_count(self):
        for kind in ('buy_limit', 'sell_stop'):
            self.assertTrue(is_entry_order(order(otype=kind)))


class TestImpliedRisk(unittest.TestCase):
    def test_distance_from_entry_to_stop(self):
        self.assertEqual(implied_risk(order(price=4000.0, sl=3990.0)), 10.0)

    def test_works_for_a_short(self):
        self.assertEqual(implied_risk(order(otype='sell', price=4000.0, sl=4010.0)), 10.0)

    def test_unset_stop_is_none_not_zero(self):
        # MT5 reports "no stop" as 0.0. Treating that as a real level would put
        # the stop thousands of points away and make every R look tiny.
        self.assertIsNone(implied_risk(order(sl=0.0)))
        self.assertIsNone(implied_risk(order(sl=None)))

    def test_stop_equal_to_entry_is_none(self):
        # A zero denominator would surface downstream as an infinite R.
        self.assertIsNone(implied_risk(order(price=4000.0, sl=4000.0)))


class TestCoverage(unittest.TestCase):
    def _orders(self, total, with_stop):
        return [order(position_id=i, sl=3990.0 if i <= with_stop else 0.0)
                for i in range(1, total + 1)]

    def test_counts_and_percentages(self):
        out = coverage(self._orders(40, 30))
        self.assertEqual(out['sampled_orders'], 40)
        self.assertEqual(out['with_stop'], 30)
        self.assertEqual(out['stop_coverage_pct'], 75.0)

    def test_verdict_is_withheld_on_a_thin_sample(self):
        out = coverage(self._orders(MIN_SAMPLE - 1, 5))
        self.assertIsNone(out['viable'])
        self.assertIn('too few to judge', out['verdict'])

    def test_high_coverage_reads_as_viable(self):
        out = coverage(self._orders(40, 38))
        self.assertTrue(out['viable'])
        self.assertIn('viable', out['verdict'])

    def test_partial_coverage_says_so_and_warns_about_reporting(self):
        out = coverage(self._orders(40, 20))
        self.assertEqual(out['viable'], 'partial')
        self.assertIn('coverage', out['verdict'])

    def test_no_stops_at_all_is_a_clear_no(self):
        out = coverage(self._orders(40, 0))
        self.assertFalse(out['viable'])
        self.assertEqual(out['stop_coverage_pct'], 0.0)
        self.assertIn('journalled by hand', out['verdict'])

    def test_median_risk_distance_ignores_orders_without_a_stop(self):
        rows = [order(position_id=1, price=4000.0, sl=3990.0),
                order(position_id=2, price=4000.0, sl=3980.0),
                order(position_id=3, sl=0.0)]
        self.assertEqual(coverage(rows)['median_risk_distance'], 20.0)

    def test_coverage_against_trades_uses_closed_trades_as_the_denominator(self):
        orders = [order(position_id=1, sl=3990.0), order(position_id=2, sl=0.0)]
        trades = [trade(1), trade(2), trade(3, is_open=True)]
        out = coverage(orders, trades=trades)
        # Three trades, two closed, one of those has a recoverable stop.
        self.assertEqual(out['closed_trades'], 2)
        self.assertEqual(out['trades_with_recoverable_stop'], 1)
        self.assertEqual(out['trade_coverage_pct'], 50.0)

    def test_trade_coverage_is_not_capped_by_the_order_sample(self):
        # The sample bounds the cost of the percentage, not the matching. Using
        # it for both would report missing stops that are merely outside the
        # window this function chose to look at.
        orders = self._orders(300, 300)
        trades = [trade(i) for i in range(1, 301)]
        out = coverage(orders, trades=trades, sample_limit=50)
        self.assertEqual(out['sampled_orders'], 50)
        self.assertEqual(out['trades_with_recoverable_stop'], 300)
        self.assertEqual(out['trade_coverage_pct'], 100.0)

    def test_sample_limit_takes_the_most_recent_orders(self):
        out = coverage(self._orders(300, 300), sample_limit=50)
        self.assertEqual(out['sampled_orders'], 50)
        self.assertEqual(out['total_entry_orders'], 300)

    def test_examples_are_included_for_eyeballing(self):
        out = coverage(self._orders(40, 40))
        self.assertEqual(len(out['examples']), 5)
        self.assertIn('risk_distance', out['examples'][0])

    def test_tolerates_empty_input(self):
        out = coverage([])
        self.assertEqual(out['sampled_orders'], 0)
        self.assertIsNone(out['viable'])
        self.assertIsNone(coverage(None)['stop_coverage_pct'])


if __name__ == '__main__':
    unittest.main()
