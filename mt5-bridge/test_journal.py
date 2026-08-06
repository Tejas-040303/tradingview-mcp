"""
Tests for the journal.

Two things dominate. The first is that recording must be idempotent — a
dashboard posting its visible window every thirty seconds must not multiply the
record of what it saw, and a journal that double-counts skipped setups produces
exactly the wrong conclusion about discipline.

The second is retention. Deleting a week of screenshots is not undoable, so the
dry-run default has its own tests: a retention job that silently deletes on
first call is one nobody audits.
"""
import unittest

import journal

DAY = 86400
NOW = 1_785_000_000


def signal(time_utc=NOW, direction='long', conditions=('fvg',), stop=4200.0):
    return {'time_utc': time_utc, 'direction': direction,
            'conditions': list(conditions), 'stop': stop, 'target': 4230.0,
            'zones': {'fvg': {'low': 4195.0, 'high': 4205.0}}}


class TestSchema(unittest.TestCase):
    def test_a_fresh_journal_is_empty_but_valid(self):
        conn = journal.connect()
        self.assertEqual(journal.summary(conn)['signals'], 0)
        self.assertIsNone(journal.summary(conn)['coverage_pct'])

    def test_connecting_twice_does_not_wipe_anything(self):
        conn = journal.connect()
        journal.record_signals(conn, [signal()], 'GOLD.i#')
        conn.executescript(journal.SCHEMA)      # what a second connect() does
        self.assertEqual(journal.summary(conn)['signals'], 1)


class TestRecordSignals(unittest.TestCase):
    def setUp(self):
        self.conn = journal.connect()

    def test_signals_are_stored(self):
        out = journal.record_signals(self.conn, [signal(), signal(NOW + 300)], 'GOLD.i#')
        self.assertEqual(out['added'], 2)
        self.assertEqual(journal.summary(self.conn)['signals'], 2)

    def test_recording_the_same_window_twice_adds_nothing(self):
        rows = [signal(), signal(NOW + 300)]
        journal.record_signals(self.conn, rows, 'GOLD.i#')
        again = journal.record_signals(self.conn, rows, 'GOLD.i#')
        self.assertEqual(again['added'], 0)
        self.assertEqual(again['already_known'], 2)
        self.assertEqual(journal.summary(self.conn)['signals'], 2)

    def test_the_same_bar_in_both_directions_is_two_signals(self):
        journal.record_signals(self.conn, [signal(direction='long'),
                                           signal(direction='short')], 'GOLD.i#')
        self.assertEqual(journal.summary(self.conn)['signals'], 2)

    def test_the_same_time_on_two_symbols_is_two_signals(self):
        journal.record_signals(self.conn, [signal()], 'GOLD.i#')
        journal.record_signals(self.conn, [signal()], 'BTCUSD#')
        self.assertEqual(journal.summary(self.conn)['signals'], 2)

    def test_malformed_rows_are_skipped_not_stored(self):
        out = journal.record_signals(self.conn, [{'direction': 'long'}, signal()],
                                     'GOLD.i#')
        self.assertEqual(out['added'], 1)

    def test_conditions_and_zones_survive_the_round_trip(self):
        journal.record_signals(self.conn, [signal(conditions=('fvg', 'liquidity_sweep'))],
                               'GOLD.i#')
        row = journal.signals_with_decisions(self.conn)[0]
        self.assertEqual(row['conditions'], ['fvg', 'liquidity_sweep'])
        self.assertEqual(row['zones']['fvg']['high'], 4205.0)

    def test_empty_input_is_tolerated(self):
        self.assertEqual(journal.record_signals(self.conn, [], 'GOLD.i#')['added'], 0)
        self.assertEqual(journal.record_signals(self.conn, None, 'GOLD.i#')['added'], 0)


class TestDecisions(unittest.TestCase):
    def setUp(self):
        self.conn = journal.connect()
        journal.record_signals(self.conn, [signal()], 'GOLD.i#')

    def test_a_decision_attaches_to_its_signal(self):
        journal.record_decision(self.conn, 'GOLD.i#', NOW, 'long', 'skipped',
                                reason='news in 10 minutes')
        row = journal.signals_with_decisions(self.conn)[0]
        self.assertEqual(row['action'], 'skipped')
        self.assertEqual(row['reason'], 'news in 10 minutes')

    def test_changing_your_mind_updates_rather_than_appends(self):
        # A signal that is both taken and skipped would corrupt every count.
        journal.record_decision(self.conn, 'GOLD.i#', NOW, 'long', 'skipped')
        journal.record_decision(self.conn, 'GOLD.i#', NOW, 'long', 'taken',
                                position_id='12345')
        rows = journal.signals_with_decisions(self.conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['action'], 'taken')
        self.assertEqual(rows[0]['position_id'], '12345')

    def test_deciding_on_an_unknown_signal_is_refused(self):
        # Otherwise the journal can hold a decision about a setup it never saw.
        with self.assertRaises(LookupError) as ctx:
            journal.record_decision(self.conn, 'GOLD.i#', NOW + 999, 'long', 'taken')
        self.assertIn('Record the signal before', str(ctx.exception))

    def test_an_invalid_action_is_refused(self):
        with self.assertRaises(ValueError):
            journal.record_decision(self.conn, 'GOLD.i#', NOW, 'long', 'maybe')

    def test_an_unstated_emotion_stays_null(self):
        # Not 'neutral'. Nothing was said, and inventing a value would put a
        # fabricated mood into the groupings.
        journal.record_decision(self.conn, 'GOLD.i#', NOW, 'long', 'taken')
        self.assertIsNone(journal.signals_with_decisions(self.conn)[0]['emotion'])

    def test_filtering_by_action(self):
        journal.record_signals(self.conn, [signal(NOW + 300)], 'GOLD.i#')
        journal.record_decision(self.conn, 'GOLD.i#', NOW, 'long', 'taken')
        journal.record_decision(self.conn, 'GOLD.i#', NOW + 300, 'long', 'skipped')
        self.assertEqual(len(journal.signals_with_decisions(self.conn, action='taken')), 1)


class TestTradeNotes(unittest.TestCase):
    def setUp(self):
        self.conn = journal.connect()

    def test_an_exit_kind_separates_a_planned_stop_from_a_panic(self):
        # MT5 reports 'stop_loss' for both, and the difference is the point.
        journal.annotate_trade(self.conn, '9001', exit_kind='panic',
                               emotion='anxious', note='saw it spike against me')
        self.assertEqual(journal.summary(self.conn)['exit_kinds_claimed'], {'panic': 1})

    def test_annotating_twice_merges_rather_than_replacing(self):
        # Adding a note later must not blank out the exit kind recorded earlier.
        journal.annotate_trade(self.conn, '9001', exit_kind='panic')
        journal.annotate_trade(self.conn, '9001', note='added this later')
        rows = journal.summary(self.conn)
        self.assertEqual(rows['exit_kinds_claimed'], {'panic': 1})
        self.assertEqual(rows['trade_notes'], 1)

    def test_an_invalid_exit_kind_is_refused(self):
        with self.assertRaises(ValueError):
            journal.annotate_trade(self.conn, '9001', exit_kind='vibes')

    def test_exit_kind_is_optional(self):
        journal.annotate_trade(self.conn, '9001', strategy='sweep + fib')
        self.assertEqual(journal.summary(self.conn)['trade_notes'], 1)


class TestRetention(unittest.TestCase):
    def setUp(self):
        self.conn = journal.connect()
        for days in (0, 3, 8, 30):
            journal.attach_screenshot(self.conn, f'/shots/{days}.png', 'entry',
                                      now=NOW - days * DAY)

    def test_dry_run_is_the_default_and_deletes_nothing(self):
        removed = []
        out = journal.prune_screenshots(self.conn, now=NOW, unlink=removed.append)
        self.assertFalse(out['applied'])
        self.assertEqual(removed, [])
        self.assertEqual(out['candidates'], 2)          # the 8- and 30-day ones
        self.assertIn('dry run', out['note'])

    def test_applying_deletes_only_what_is_past_the_window(self):
        removed = []
        out = journal.prune_screenshots(self.conn, now=NOW, apply=True,
                                        unlink=removed.append)
        self.assertEqual(out['deleted'], 2)
        self.assertEqual(sorted(removed), ['/shots/30.png', '/shots/8.png'])

    def test_rows_survive_the_file_being_deleted(self):
        # Losing the picture is not the same as losing the fact that one existed.
        journal.prune_screenshots(self.conn, now=NOW, apply=True, unlink=lambda p: None)
        shots = journal.summary(self.conn)['screenshots']
        self.assertEqual(shots['rows'], 4)
        self.assertEqual(shots['pruned'], 2)

    def test_pruning_twice_does_not_retry_the_same_files(self):
        journal.prune_screenshots(self.conn, now=NOW, apply=True, unlink=lambda p: None)
        again = journal.prune_screenshots(self.conn, now=NOW, apply=True,
                                          unlink=lambda p: None)
        self.assertEqual(again['candidates'], 0)

    def test_a_missing_file_still_marks_the_row(self):
        # Otherwise it is retried forever.
        def gone(_):
            raise FileNotFoundError()
        out = journal.prune_screenshots(self.conn, now=NOW, apply=True, unlink=gone)
        self.assertEqual(out['already_missing'], 2)
        self.assertEqual(journal.prune_screenshots(self.conn, now=NOW)['candidates'], 0)

    def test_a_failed_delete_is_reported_and_left_for_next_time(self):
        def denied(_):
            raise PermissionError('in use')
        out = journal.prune_screenshots(self.conn, now=NOW, apply=True, unlink=denied)
        self.assertEqual(len(out['failed']), 2)
        self.assertEqual(out['deleted'], 0)
        self.assertEqual(journal.prune_screenshots(self.conn, now=NOW)['candidates'], 2)

    def test_the_window_is_configurable(self):
        out = journal.prune_screenshots(self.conn, keep_days=1, now=NOW)
        self.assertEqual(out['candidates'], 3)


class TestSummary(unittest.TestCase):
    def setUp(self):
        self.conn = journal.connect()

    def test_undecided_signals_are_counted_and_named(self):
        # The number that invalidates the rest: a journal covering a fifth of
        # the signals cannot support a claim about which ones get skipped.
        journal.record_signals(self.conn, [signal(NOW + i * 300) for i in range(5)],
                               'GOLD.i#')
        journal.record_decision(self.conn, 'GOLD.i#', NOW, 'long', 'taken')
        out = journal.summary(self.conn)
        self.assertEqual(out['undecided'], 4)
        self.assertEqual(out['coverage_pct'], 20.0)
        self.assertIn('no decision recorded', out['note'])

    def test_full_coverage_carries_no_warning(self):
        journal.record_signals(self.conn, [signal()], 'GOLD.i#')
        journal.record_decision(self.conn, 'GOLD.i#', NOW, 'long', 'skipped')
        out = journal.summary(self.conn)
        self.assertEqual(out['coverage_pct'], 100.0)
        self.assertIsNone(out['note'])

    def test_skip_reasons_are_labelled_as_claimed(self):
        journal.record_signals(self.conn, [signal()], 'GOLD.i#')
        journal.record_decision(self.conn, 'GOLD.i#', NOW, 'long', 'skipped',
                                reason='news')
        out = journal.summary(self.conn)
        self.assertIn('skip_reasons_claimed', out)
        self.assertEqual(out['skip_reasons_claimed'], {'news': 1})


class TestIsolation(unittest.TestCase):
    def test_the_journal_cannot_reach_the_broker(self):
        # Structural, not promised. This module is the first thing in the
        # project allowed to write, and the line between "can write a file" and
        # "can place an order" is the whole architecture.
        import inspect
        source = inspect.getsource(journal)
        for forbidden in ('mt5_client', 'MetaTrader5', 'order_send'):
            self.assertNotIn(forbidden, source,
                             f'journal.py must not reference {forbidden}')


if __name__ == '__main__':
    unittest.main()
