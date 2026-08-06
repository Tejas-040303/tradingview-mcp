"""
The trading journal: the record MT5 structurally cannot keep.

MetaTrader reports fills. A setup you looked at and passed on leaves no trace
anywhere, and neither does *why* you passed on it, or why you closed early, or
what you were thinking. Those are the facts most likely to explain a losing
account, and they exist only if something writes them down at the time.

**This is a self-report store, and a self-report is not a fact.** "Skipped for
news" is a record of what was said, not of why the trade was skipped — the
reason may be rationalised after the outcome was known. Everything here is
labelled as claimed rather than observed, and nothing in this module ever
promotes a self-report into a measurement. What it does allow is the join: the
skipped signals can be replayed to see what they would have done, and *that*
comparison is evidence.

**Storage is SQLite, and nothing here can trade.** This module imports no
broker client, and there is a test asserting it never will. It writes to a
local file and nowhere else — the distinction between "a service that can
write" and "a service that can place an order" is the whole architecture, and
it is worth keeping structural rather than promised.

Stdlib only.
"""
import json
import os
import sqlite3
import time

# Recorded as given rather than forced into a taxonomy, but the dashboard needs
# a starting vocabulary and consistent spelling makes the groupings usable.
ACTIONS = ('taken', 'skipped')
EXIT_KINDS = ('target', 'stop', 'manual_profit', 'manual_loss', 'panic',
              'breakeven_fear', 'news', 'session_end', 'other')
EMOTIONS = ('calm', 'confident', 'hesitant', 'anxious', 'frustrated',
            'revenge', 'bored', 'fomo')

# Screenshots are ~300 KB each. Keeping every one turns the directory into the
# largest thing in the repository within a month; keeping a week covers the
# period where looking at the picture still tells you something.
DEFAULT_KEEP_DAYS = 7

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY,
    -- symbol|time|direction. Re-recording the same detection is a no-op, so a
    -- dashboard can post the whole visible window on every refresh without
    -- growing a duplicate every time.
    fingerprint TEXT    NOT NULL UNIQUE,
    symbol      TEXT    NOT NULL,
    time_utc    INTEGER NOT NULL,
    direction   TEXT    NOT NULL,
    conditions  TEXT,
    stop        REAL,
    target      REAL,
    zones       TEXT,
    recorded_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS signals_time ON signals(time_utc);

CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY,
    signal_id   INTEGER NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
    action      TEXT    NOT NULL,
    reason      TEXT,
    emotion     TEXT,
    position_id TEXT,
    recorded_at INTEGER NOT NULL,
    -- One decision per signal. Changing your mind updates it rather than
    -- appending, so a signal is never both taken and skipped.
    UNIQUE(signal_id)
);

CREATE TABLE IF NOT EXISTS trade_notes (
    id          INTEGER PRIMARY KEY,
    position_id TEXT    NOT NULL UNIQUE,
    strategy    TEXT,
    exit_kind   TEXT,
    emotion     TEXT,
    note        TEXT,
    recorded_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS screenshots (
    id          INTEGER PRIMARY KEY,
    path        TEXT    NOT NULL,
    kind        TEXT    NOT NULL,
    signal_id   INTEGER REFERENCES signals(id) ON DELETE SET NULL,
    position_id TEXT,
    created_at  INTEGER NOT NULL,
    -- Set when the image was deleted by retention. The row survives, so the
    -- journal still knows a screenshot existed and when — losing the file is
    -- not the same as losing the fact.
    pruned_at   INTEGER
);
CREATE INDEX IF NOT EXISTS screenshots_created ON screenshots(created_at);
"""


def connect(path=':memory:'):
    """Open the journal, creating it if absent."""
    if path != ':memory:':
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def fingerprint(symbol, time_utc, direction):
    return f'{symbol}|{int(time_utc)}|{direction}'


def _rows(cursor):
    return [dict(row) for row in cursor.fetchall()]


def _loads(value, fallback=None):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return fallback


def record_signals(conn, signals, symbol, now=None):
    """
    Store detected setups, ignoring ones already present.

    Idempotent by fingerprint so the same window can be posted repeatedly — a
    dashboard refreshing every thirty seconds must not multiply the record of
    what it saw.
    """
    now = int(now if now is not None else time.time())
    added = 0

    for signal in signals or []:
        stamp = signal.get('time_utc')
        direction = signal.get('direction')
        if stamp is None or not direction:
            continue
        cursor = conn.execute(
            """INSERT OR IGNORE INTO signals
               (fingerprint, symbol, time_utc, direction, conditions, stop,
                target, zones, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (fingerprint(symbol, stamp, direction), symbol, int(stamp), direction,
             json.dumps(signal.get('conditions') or []),
             signal.get('stop'), signal.get('target'),
             json.dumps(signal.get('zones') or {}), now))
        added += cursor.rowcount
    conn.commit()
    return {'received': len(signals or []), 'added': added,
            'already_known': len(signals or []) - added}


def record_decision(conn, symbol, time_utc, direction, action, reason=None,
                    emotion=None, position_id=None, now=None):
    """
    What was done about a signal, and what was said about why.

    The reason is stored verbatim and never interpreted. It is a claim made at
    a moment, which is exactly its value and exactly its limit.
    """
    if action not in ACTIONS:
        raise ValueError(f'action must be one of {", ".join(ACTIONS)}, got {action!r}')

    row = conn.execute('SELECT id FROM signals WHERE fingerprint = ?',
                       (fingerprint(symbol, time_utc, direction),)).fetchone()
    if row is None:
        raise LookupError(
            f'no signal recorded for {symbol} {direction} at {time_utc}. '
            f'Record the signal before deciding on it, so the journal cannot '
            f'hold a decision about a setup it never saw.')

    now = int(now if now is not None else time.time())
    conn.execute(
        """INSERT INTO decisions
           (signal_id, action, reason, emotion, position_id, recorded_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(signal_id) DO UPDATE SET
             action = excluded.action, reason = excluded.reason,
             emotion = excluded.emotion, position_id = excluded.position_id,
             recorded_at = excluded.recorded_at""",
        (row['id'], action, reason, emotion, position_id, now))
    conn.commit()
    return {'signal_id': row['id'], 'action': action}


def annotate_trade(conn, position_id, strategy=None, exit_kind=None,
                   emotion=None, note=None, now=None):
    """
    Notes against a real MT5 position.

    `exit_kind` is the field this exists for. MT5 reports 'stop_loss' whether
    the stop was the plan or a panic, and that difference is the one worth
    knowing.
    """
    if exit_kind is not None and exit_kind not in EXIT_KINDS:
        raise ValueError(f'exit_kind must be one of {", ".join(EXIT_KINDS)}, '
                         f'got {exit_kind!r}')

    now = int(now if now is not None else time.time())
    conn.execute(
        """INSERT INTO trade_notes
           (position_id, strategy, exit_kind, emotion, note, recorded_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(position_id) DO UPDATE SET
             strategy = COALESCE(excluded.strategy, trade_notes.strategy),
             exit_kind = COALESCE(excluded.exit_kind, trade_notes.exit_kind),
             emotion = COALESCE(excluded.emotion, trade_notes.emotion),
             note = COALESCE(excluded.note, trade_notes.note),
             recorded_at = excluded.recorded_at""",
        (str(position_id), strategy, exit_kind, emotion, note, now))
    conn.commit()
    return {'position_id': str(position_id)}


def attach_screenshot(conn, path, kind, signal_id=None, position_id=None, now=None):
    """Record that an image exists. The file itself lives on disk."""
    now = int(now if now is not None else time.time())
    cursor = conn.execute(
        """INSERT INTO screenshots (path, kind, signal_id, position_id, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (path, kind, signal_id, str(position_id) if position_id else None, now))
    conn.commit()
    return {'id': cursor.lastrowid, 'path': path}


def prune_screenshots(conn, keep_days=DEFAULT_KEEP_DAYS, now=None, apply=False,
                      unlink=os.unlink):
    """
    Delete screenshot files older than `keep_days`, keeping their rows.

    **Dry run by default.** This is the only destructive operation in the
    module and deleting the wrong week of images is not undoable, so the
    caller has to say `apply=True` — a retention job that silently deletes on
    first call is a retention job nobody audits.

    The rows survive with `pruned_at` set: the journal continues to know a
    screenshot existed and when it was taken. Losing the picture is not the
    same as losing the fact.
    """
    now = int(now if now is not None else time.time())
    cutoff = now - keep_days * 86400
    rows = _rows(conn.execute(
        'SELECT id, path, created_at FROM screenshots '
        'WHERE created_at < ? AND pruned_at IS NULL ORDER BY created_at',
        (cutoff,)))

    if not apply:
        return {'applied': False, 'cutoff': cutoff, 'candidates': len(rows),
                'paths': [r['path'] for r in rows],
                'note': 'dry run — pass apply=True to delete these files'}

    deleted, missing, failed = 0, 0, []
    for row in rows:
        try:
            unlink(row['path'])
            deleted += 1
        except FileNotFoundError:
            # Already gone. Still mark the row, or it is re-attempted forever.
            missing += 1
        except OSError as exc:
            failed.append({'path': row['path'], 'error': str(exc)})
            continue
        conn.execute('UPDATE screenshots SET pruned_at = ? WHERE id = ?', (now, row['id']))
    conn.commit()

    return {'applied': True, 'cutoff': cutoff, 'candidates': len(rows),
            'deleted': deleted, 'already_missing': missing,
            'failed': failed or None,
            # Rows are never removed, so the count of known screenshots does
            # not drop when the files do.
            'rows_kept': len(rows)}


def signals_with_decisions(conn, from_ts=None, to_ts=None, symbol=None,
                           action=None, limit=200, offset=0):
    """Recorded signals and whatever was decided about each."""
    where, args = ['1=1'], []
    if from_ts is not None:
        where.append('s.time_utc >= ?')
        args.append(int(from_ts))
    if to_ts is not None:
        where.append('s.time_utc <= ?')
        args.append(int(to_ts))
    if symbol:
        where.append('s.symbol = ?')
        args.append(symbol)
    if action:
        where.append('d.action = ?')
        args.append(action)

    sql = f"""SELECT s.*, d.action, d.reason, d.emotion, d.position_id,
                     d.recorded_at AS decided_at
              FROM signals s LEFT JOIN decisions d ON d.signal_id = s.id
              WHERE {' AND '.join(where)}
              ORDER BY s.time_utc DESC LIMIT ? OFFSET ?"""
    rows = _rows(conn.execute(sql, (*args, int(limit), int(offset))))
    for row in rows:
        row['conditions'] = _loads(row.get('conditions'), [])
        row['zones'] = _loads(row.get('zones'), {})
    return rows


def summary(conn, from_ts=None, to_ts=None):
    """
    What the journal knows, and how much of it is undecided.

    The undecided count leads because it is the number that invalidates the
    rest: a journal covering a fifth of the signals cannot support a claim
    about which ones get skipped.
    """
    where, args = ['1=1'], []
    if from_ts is not None:
        where.append('time_utc >= ?')
        args.append(int(from_ts))
    if to_ts is not None:
        where.append('time_utc <= ?')
        args.append(int(to_ts))
    clause = ' AND '.join(where)

    total = conn.execute(f'SELECT COUNT(*) c FROM signals WHERE {clause}',
                         args).fetchone()['c']
    by_action = {r['action'] or 'undecided': r['c'] for r in _rows(conn.execute(
        f"""SELECT d.action, COUNT(*) c FROM signals s
            LEFT JOIN decisions d ON d.signal_id = s.id
            WHERE {clause.replace('time_utc', 's.time_utc')}
            GROUP BY d.action""", args))}

    reasons = {r['reason']: r['c'] for r in _rows(conn.execute(
        """SELECT d.reason, COUNT(*) c FROM decisions d
           WHERE d.action = 'skipped' AND d.reason IS NOT NULL
           GROUP BY d.reason ORDER BY c DESC LIMIT 20"""))}
    exits = {r['exit_kind']: r['c'] for r in _rows(conn.execute(
        """SELECT exit_kind, COUNT(*) c FROM trade_notes
           WHERE exit_kind IS NOT NULL GROUP BY exit_kind ORDER BY c DESC"""))}

    decided = sum(v for k, v in by_action.items() if k != 'undecided')
    shots = conn.execute(
        'SELECT COUNT(*) c, SUM(pruned_at IS NOT NULL) p FROM screenshots').fetchone()

    return {
        'signals': total,
        'decided': decided,
        'undecided': by_action.get('undecided', 0),
        'coverage_pct': round(decided / total * 100, 1) if total else None,
        'by_action': by_action,
        # Self-reported, and labelled as such wherever it is rendered.
        'skip_reasons_claimed': reasons,
        'exit_kinds_claimed': exits,
        'trade_notes': conn.execute('SELECT COUNT(*) c FROM trade_notes').fetchone()['c'],
        'screenshots': {'rows': shots['c'], 'pruned': shots['p'] or 0},
        'note': (None if not total or decided == total else
                 f'{by_action.get("undecided", 0)} of {total} signals have no '
                 f'decision recorded. Any claim about which setups get skipped '
                 f'is limited to the {decided} that do.'),
    }
