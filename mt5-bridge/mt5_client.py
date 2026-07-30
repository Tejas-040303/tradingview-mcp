"""
Read-only MetaTrader 5 terminal access.

Every function here reads. Nothing in this module sends an order, modifies a
position, or writes to the terminal — `order_send`, `order_check` and friends
are deliberately absent, so the execution path cannot be reached by accident
from the read-only server. Execution, when it arrives, belongs in a separate
module behind its own explicit guards.

Requires the MetaTrader5 package, which is Windows-only:
    pip install MetaTrader5
"""
import json
import os
from datetime import datetime, timezone

from normalize import resolve_timeframe, normalize_calendar

# Imported lazily so this module can be inspected (and normalize.py tested) on
# platforms where the package cannot install.
_mt5 = None


class Mt5Error(RuntimeError):
    pass


def _mt5_module():
    global _mt5
    if _mt5 is None:
        try:
            import MetaTrader5  # noqa: N813
        except ImportError as exc:
            raise Mt5Error(
                'MetaTrader5 package not available. It is Windows-only: '
                'pip install MetaTrader5'
            ) from exc
        _mt5 = MetaTrader5
    return _mt5


def _last_error(mt5):
    try:
        code, text = mt5.last_error()
        return f'{text} (code {code})'
    except Exception:  # pragma: no cover - defensive
        return 'unknown error'


def connect(path=None, timeout_ms=30000):
    """
    Attach to a running terminal, or launch the one at `path`.

    Idempotent: MetaTrader5.initialize() is safe to call repeatedly.
    """
    mt5 = _mt5_module()
    ok = mt5.initialize(path, timeout=timeout_ms) if path else mt5.initialize(timeout=timeout_ms)
    if not ok:
        raise Mt5Error(f'Could not initialize MT5 terminal: {_last_error(mt5)}')
    return True


def shutdown():
    if _mt5 is not None:
        _mt5.shutdown()


def _as_dict(record):
    """MetaTrader5 returns namedtuples; make them JSON-serialisable."""
    if record is None:
        return None
    if hasattr(record, '_asdict'):
        return dict(record._asdict())
    return dict(record)


def health():
    """Connection state plus the timezone offset needed to align timestamps."""
    mt5 = _mt5_module()
    connect()
    terminal = _as_dict(mt5.terminal_info())
    account = _as_dict(mt5.account_info())

    # Bar timestamps come back in broker-server time while the economic
    # calendar is UTC. Expose the offset so callers can reconcile the two
    # rather than silently comparing mismatched clocks.
    server_offset_sec = None
    tick = mt5.symbol_info_tick('EURUSD')
    if tick is not None:
        server_offset_sec = int(tick.time - datetime.now(tz=timezone.utc).timestamp())
        server_offset_sec = int(round(server_offset_sec / 900.0) * 900)

    return {
        'success': terminal is not None,
        'connected': bool(terminal and terminal.get('connected')),
        'trade_allowed': bool(terminal and terminal.get('trade_allowed')),
        'terminal': {
            'name': (terminal or {}).get('name'),
            'build': (terminal or {}).get('build'),
            'path': (terminal or {}).get('path'),
        },
        'account': {
            'login': (account or {}).get('login'),
            'server': (account or {}).get('server'),
            'currency': (account or {}).get('currency'),
        },
        'server_utc_offset_sec': server_offset_sec,
        'read_only': True,
    }


def account():
    mt5 = _mt5_module()
    connect()
    info = _as_dict(mt5.account_info())
    if info is None:
        raise Mt5Error(f'Could not read account info: {_last_error(mt5)}')
    keep = ('login', 'server', 'currency', 'balance', 'equity', 'margin',
            'margin_free', 'margin_level', 'profit', 'leverage', 'name')
    return {'success': True, **{k: info.get(k) for k in keep}}


def positions(symbol=None):
    mt5 = _mt5_module()
    connect()
    raw = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    rows = [_as_dict(p) for p in (raw or [])]
    for row in rows:
        row['type'] = 'buy' if row.get('type') == 0 else 'sell'
        row['time_iso'] = _iso_or_none(row.get('time'))
    return {'success': True, 'count': len(rows), 'positions': rows}


def orders(symbol=None):
    mt5 = _mt5_module()
    connect()
    raw = mt5.orders_get(symbol=symbol) if symbol else mt5.orders_get()
    rows = [_as_dict(o) for o in (raw or [])]
    for row in rows:
        row['time_setup_iso'] = _iso_or_none(row.get('time_setup'))
    return {'success': True, 'count': len(rows), 'orders': rows}


def quote(symbol):
    mt5 = _mt5_module()
    connect()
    if not mt5.symbol_select(symbol, True):
        raise Mt5Error(f'Symbol {symbol!r} not available: {_last_error(mt5)}')
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise Mt5Error(f'No tick for {symbol!r}: {_last_error(mt5)}')
    info = _as_dict(mt5.symbol_info(symbol)) or {}
    row = _as_dict(tick)
    spread = None
    if row.get('ask') is not None and row.get('bid') is not None:
        spread = round(row['ask'] - row['bid'], int(info.get('digits') or 5))
    return {
        'success': True,
        'symbol': symbol,
        'bid': row.get('bid'),
        'ask': row.get('ask'),
        'last': row.get('last'),
        'spread': spread,
        'volume': row.get('volume'),
        'time': row.get('time'),
        'time_iso': _iso_or_none(row.get('time')),
        'digits': info.get('digits'),
        'description': info.get('description'),
    }


def bars(symbol, timeframe='5', count=100, summary=False):
    """
    Copy the most recent `count` bars.

    Capped at 5000 to keep a stray request from pulling a full symbol history
    into a response body.
    """
    mt5 = _mt5_module()
    connect()
    count = max(1, min(int(count), 5000))
    tf_const = getattr(mt5, resolve_timeframe(timeframe))

    if not mt5.symbol_select(symbol, True):
        raise Mt5Error(f'Symbol {symbol!r} not available: {_last_error(mt5)}')

    rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, count)
    if rates is None:
        raise Mt5Error(f'No rates for {symbol!r}: {_last_error(mt5)}')

    rows = [{
        'time': int(r['time']),
        'time_iso': _iso_or_none(int(r['time'])),
        'open': float(r['open']),
        'high': float(r['high']),
        'low': float(r['low']),
        'close': float(r['close']),
        'tick_volume': int(r['tick_volume']),
        'spread': int(r['spread']),
    } for r in rates]

    out = {'success': True, 'symbol': symbol, 'timeframe': str(timeframe)}
    if summary:
        from normalize import summarize_bars
        out['summary'] = summarize_bars(rows)
    else:
        out['count'] = len(rows)
        out['bars'] = rows
    return out


def deals(from_ts, to_ts, symbol=None):
    """
    Closed deals in a window — the fill history a trade journal reconciles
    against. Note this reports what *was* traded; signals you skipped leave no
    trace here, which is why the scanner has to log them separately.
    """
    mt5 = _mt5_module()
    connect()
    start = datetime.fromtimestamp(int(from_ts), tz=timezone.utc)
    end = datetime.fromtimestamp(int(to_ts), tz=timezone.utc)
    raw = mt5.history_deals_get(start, end, group=symbol) if symbol \
        else mt5.history_deals_get(start, end)
    rows = [_as_dict(d) for d in (raw or [])]
    for row in rows:
        row['time_iso'] = _iso_or_none(row.get('time'))
    return {'success': True, 'count': len(rows), 'deals': rows}


def calendar(path=None):
    """
    Read the calendar dump written by calendar_export.mq5.

    The MetaTrader5 Python package exposes no calendar API — CalendarValueHistory
    is MQL5-only — so the terminal-side script writes JSON into MQL5/Files and
    this reads it back.
    """
    path = path or os.environ.get('MT5_CALENDAR_FILE')
    if not path:
        raise Mt5Error(
            'No calendar file configured. Run calendar_export.mq5 in the '
            'terminal, then set MT5_CALENDAR_FILE to the resulting JSON path '
            '(usually <terminal data>/MQL5/Files/mcp_calendar.json).')
    if not os.path.exists(path):
        raise Mt5Error(
            f'Calendar file not found: {path}. Run calendar_export.mq5 in the '
            'terminal to (re)generate it.')

    with open(path, 'r', encoding='utf-8') as handle:
        payload = json.load(handle)

    rows = payload.get('events', payload) if isinstance(payload, dict) else payload
    normalized = normalize_calendar(rows)
    return {
        'success': True,
        'count': len(normalized),
        'source_file': path,
        'exported_at': payload.get('exported_at') if isinstance(payload, dict) else None,
        'events': normalized,
    }


def _iso_or_none(ts):
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
