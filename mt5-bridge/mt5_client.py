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
import glob
import json
import os
import time
from datetime import datetime, timezone

from normalize import (
    DEAL_ENTRY,
    DEAL_REASON,
    DEAL_TYPE,
    ORDER_TYPE,
    POSITION_TYPE,
    clean_last,
    decode_enums,
    filter_symbols,
    infer_server_offset,
    mid_price,
    msc_fields,
    normalize_calendar,
    paginate,
    resolve_timeframe,
    summarize_deals,
    time_fields,
)

# Imported lazily so this module can be inspected (and normalize.py tested) on
# platforms where the package cannot install.
_mt5 = None

# Liquid majors, tried in order when measuring the server clock offset. The
# newest tick across them is used, so one quiet symbol cannot skew the result.
PROBE_SYMBOLS = ('EURUSD', 'GBPUSD', 'USDJPY', 'XAUUSD', 'GOLD')

# The offset changes only at DST boundaries, so it is worth caching.
_OFFSET_TTL_SEC = 300
_offset_cache = {'value': None, 'source': None, 'at': 0.0}


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


def server_utc_offset(force=False):
    """
    The broker clock's offset from UTC, in seconds.

    MetaTrader 5 reports tick and bar times against the *server* clock, so this
    offset is required to turn any of them into real UTC. Resolution order:

      1. MT5_SERVER_UTC_OFFSET_SEC, if set — authoritative, no probing
      2. inferred from the newest tick across PROBE_SYMBOLS
      3. unknown — reported as None rather than guessed

    Case 3 matters over a weekend: with no ticks arriving, the newest one can be
    days old and would imply a wildly wrong offset. Returning None keeps that
    out of the data.
    """
    override = os.environ.get('MT5_SERVER_UTC_OFFSET_SEC')
    if override:
        try:
            return {'offset_sec': int(override), 'source': 'env'}
        except ValueError:
            pass

    now = time.time()
    if not force and _offset_cache['at'] and (now - _offset_cache['at']) < _OFFSET_TTL_SEC:
        return {'offset_sec': _offset_cache['value'], 'source': _offset_cache['source']}

    mt5 = _mt5_module()
    newest = None
    for symbol in PROBE_SYMBOLS:
        try:
            tick = mt5.symbol_info_tick(symbol)
        except Exception:
            continue
        if tick is not None and getattr(tick, 'time', 0):
            newest = tick.time if newest is None else max(newest, tick.time)

    offset = infer_server_offset(newest, datetime.now(tz=timezone.utc).timestamp())
    source = 'inferred' if offset is not None else 'unknown'
    _offset_cache.update({'value': offset, 'source': source, 'at': now})
    return {'offset_sec': offset, 'source': source}


def health():
    """Connection state plus the clock offset needed to align timestamps."""
    mt5 = _mt5_module()
    connect()
    terminal = _as_dict(mt5.terminal_info())
    account = _as_dict(mt5.account_info())
    offset = server_utc_offset()

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
        'server_utc_offset_sec': offset['offset_sec'],
        'server_utc_offset_source': offset['source'],
        'read_only': True,
    }


def symbols(search=None, limit=200):
    """
    Search the broker's instrument list by name or description.

    Broker naming is not guessable — spot gold is 'GOLD.i#' on XM and 'XAUUSD'
    elsewhere — so finding an instrument has to be a search.
    """
    mt5 = _mt5_module()
    connect()
    raw = mt5.symbols_get()
    rows = [{
        'name': s.name,
        'description': getattr(s, 'description', ''),
        'digits': getattr(s, 'digits', None),
        'visible': getattr(s, 'visible', None),
    } for s in (raw or [])]

    kept = filter_symbols(rows, search=search, limit=limit)
    return {
        'success': True,
        'total_available': len(rows),
        'count': len(kept),
        'search': search,
        'symbols': kept,
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
    offset = server_utc_offset()['offset_sec']
    raw = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    rows = [_as_dict(p) for p in (raw or [])]
    for row in rows:
        decode_enums(row, {'type': POSITION_TYPE, 'reason': DEAL_REASON})
        row.update(time_fields(row.pop('time', None), offset))
        row.update(msc_fields(row.pop('time_msc', None), offset))
    return {'success': True, 'count': len(rows), 'positions': rows}


def orders(symbol=None):
    mt5 = _mt5_module()
    connect()
    offset = server_utc_offset()['offset_sec']
    raw = mt5.orders_get(symbol=symbol) if symbol else mt5.orders_get()
    rows = [_as_dict(o) for o in (raw or [])]
    for row in rows:
        decode_enums(row, {'type': ORDER_TYPE, 'reason': DEAL_REASON})
        row.update(time_fields(row.pop('time_setup', None), offset))
        row.update(msc_fields(row.pop('time_setup_msc', None), offset))
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
    digits = int(info.get('digits') or 5)

    spread = None
    if row.get('ask') is not None and row.get('bid') is not None:
        spread = round(row['ask'] - row['bid'], digits)

    return {
        'success': True,
        'symbol': symbol,
        'bid': row.get('bid'),
        'ask': row.get('ask'),
        # CFDs carry no last-trade price or traded volume; both come back as 0.
        # Reporting them as null keeps a zero from being read as a price.
        'mid': mid_price(row.get('bid'), row.get('ask'), digits),
        'last': clean_last(row.get('last')),
        'volume': row.get('volume') or None,
        'spread': spread,
        **time_fields(row.get('time'), server_utc_offset()['offset_sec']),
        'digits': digits,
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

    offset = server_utc_offset()
    rows = [{
        **time_fields(int(r['time']), offset['offset_sec']),
        'open': float(r['open']),
        'high': float(r['high']),
        'low': float(r['low']),
        'close': float(r['close']),
        'tick_volume': int(r['tick_volume']),
        'spread': int(r['spread']),
    } for r in rates]

    out = {
        'success': True,
        'symbol': symbol,
        'timeframe': str(timeframe),
        'server_utc_offset_sec': offset['offset_sec'],
        'server_utc_offset_source': offset['source'],
    }
    if summary:
        from normalize import summarize_bars
        out['summary'] = summarize_bars(rows)
    else:
        out['count'] = len(rows)
        out['bars'] = rows
    return out


def bars_range(symbol, from_ts, to_ts, timeframe='1'):
    """
    Every bar in a UTC window, for excursion analysis.

    copy_rates_range rather than copy_rates_from_pos: excursion needs the bars
    that surround each trade, which sit at an arbitrary point in history rather
    than at the end of it. One call per symbol covering every trade beats one
    call per trade by two orders of magnitude on a few hundred trades.

    Like history_deals_get, the bounds are interpreted on the *server* clock, so
    the UTC window is shifted before querying — otherwise the range is wrong by
    the broker offset and the trades at each edge silently lose their bars.
    """
    mt5 = _mt5_module()
    connect()
    clock = server_utc_offset()
    shift = clock['offset_sec'] or 0
    tf_const = getattr(mt5, resolve_timeframe(timeframe))

    if not mt5.symbol_select(symbol, True):
        raise Mt5Error(f'Symbol {symbol!r} not available: {_last_error(mt5)}')

    start = datetime.fromtimestamp(int(from_ts) + shift, tz=timezone.utc)
    end = datetime.fromtimestamp(int(to_ts) + shift, tz=timezone.utc)
    rates = mt5.copy_rates_range(symbol, tf_const, start, end)
    if rates is None:
        raise Mt5Error(f'No rates for {symbol!r} in that window: {_last_error(mt5)}')

    return [{
        **time_fields(int(r['time']), clock['offset_sec']),
        'open': float(r['open']),
        'high': float(r['high']),
        'low': float(r['low']),
        'close': float(r['close']),
    } for r in rates]


def deals(from_ts, to_ts, symbol=None, limit=100, offset=0, summary=False):
    """
    Closed deals in a window — the fill history a trade journal reconciles
    against. Note this reports what *was* traded; signals you skipped leave no
    trace here, which is why the scanner has to log them separately.
    """
    mt5 = _mt5_module()
    connect()
    # Named `clock`, not `offset` — `offset` is the pagination parameter.
    clock = server_utc_offset()

    # history_deals_get interprets its bounds in *server* time, so a UTC window
    # has to be shifted before querying or the range is wrong by the offset —
    # three hours on a UTC+3 broker, quietly dropping or adding trades at the
    # edges. With an unknown offset the bounds are passed through as given.
    shift = clock['offset_sec'] or 0
    start = datetime.fromtimestamp(int(from_ts) + shift, tz=timezone.utc)
    end = datetime.fromtimestamp(int(to_ts) + shift, tz=timezone.utc)

    raw = mt5.history_deals_get(start, end, group=symbol) if symbol \
        else mt5.history_deals_get(start, end)
    rows = [_as_dict(d) for d in (raw or [])]
    for row in rows:
        decode_enums(row, {'type': DEAL_TYPE, 'entry': DEAL_ENTRY,
                           'reason': DEAL_REASON})
        row.update(time_fields(row.pop('time', None), clock['offset_sec']))
        row.update(msc_fields(row.pop('time_msc', None), clock['offset_sec']))

    out = {
        'success': True,
        'requested_window_utc': {'from': int(from_ts), 'to': int(to_ts)},
        'server_utc_offset_sec': clock['offset_sec'],
        'server_utc_offset_source': clock['source'],
        # Summary is computed over the whole window, not just the page.
        'summary': summarize_deals(rows),
    }

    # An active month is hundreds of deals; returning them all unasked is tens
    # of thousands of lines. Paginate by default and let the caller opt in.
    if not summary:
        window, page = paginate(rows, limit=limit, offset=offset)
        out['page'] = page
        out['deals'] = window
    return out


CALENDAR_FILENAME = 'mcp_calendar.json'


def calendar_search_globs():
    """
    Where to look for the calendar dump when nothing is configured.

    calendar_export.mq5 always writes into the terminal's MQL5/Files directory,
    whose location is derivable — so requiring MT5_CALENDAR_FILE just to state
    the obvious is a papercut, and an environment variable that must be set in
    the same shell that launches the bridge is one that goes missing.
    """
    globs = []
    appdata = os.environ.get('APPDATA')
    if appdata:
        terminal = os.path.join(appdata, 'MetaQuotes', 'Terminal')
        globs.append(os.path.join(terminal, '*', 'MQL5', 'Files', CALENDAR_FILENAME))
        globs.append(os.path.join(terminal, 'Common', 'Files', CALENDAR_FILENAME))
    # Alongside the bridge — convenient for testing and non-Windows setups.
    globs.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), CALENDAR_FILENAME))
    return globs


def discover_calendar_file(globs=None):
    """
    Newest calendar file across the candidate locations, or None.

    Newest rather than first: a machine can have several terminal instances,
    and the one exported most recently is the one being worked with.
    """
    candidates = []
    for pattern in (globs if globs is not None else calendar_search_globs()):
        for match in glob.glob(pattern):
            try:
                candidates.append((os.path.getmtime(match), match))
            except OSError:
                continue
    if not candidates:
        return None
    return max(candidates)[1]


def calendar(path=None):
    """
    Read the calendar dump written by calendar_export.mq5.

    The MetaTrader5 Python package exposes no calendar API — CalendarValueHistory
    is MQL5-only — so the terminal-side script writes JSON into MQL5/Files and
    this reads it back.

    Resolution order: explicit argument, MT5_CALENDAR_FILE, then autodiscovery
    of the terminal's MQL5/Files directory.
    """
    file_source = 'param'
    if not path:
        path = os.environ.get('MT5_CALENDAR_FILE')
        file_source = 'env' if path else None
    if not path:
        path = discover_calendar_file()
        file_source = 'discovered' if path else None

    if not path:
        searched = '\n  '.join(calendar_search_globs())
        raise Mt5Error(
            'No calendar file found. Run calendar_export.mq5 in the terminal '
            '(File > Open Data Folder > MQL5/Scripts, compile with F7, run it '
            'on any chart).\nSearched:\n  ' + searched +
            '\nSet MT5_CALENDAR_FILE to override.')
    if not os.path.exists(path):
        raise Mt5Error(
            f'Calendar file not found: {path}. Run calendar_export.mq5 in the '
            'terminal to (re)generate it.')

    with open(path, 'r', encoding='utf-8') as handle:
        payload = json.load(handle)

    is_doc = isinstance(payload, dict)
    rows = payload.get('events', payload) if is_doc else payload

    # Event times are broker-clock. Resolve them to UTC using, in order: the
    # offset the exporter recorded (authoritative — the terminal knows its own
    # clock, and it works on a weekend when nothing can be inferred), then the
    # live offset for files written before the format carried it.
    file_offset = payload.get('offset_sec') if is_doc else None
    if file_offset is None:
        try:
            file_offset = server_utc_offset()['offset_sec']
            offset_source = 'live' if file_offset is not None else 'unknown'
        except Mt5Error:
            file_offset, offset_source = None, 'unknown'
    else:
        offset_source = 'file'

    normalized = normalize_calendar(rows, offset_sec=file_offset)
    return {
        'success': True,
        'count': len(normalized),
        'source_file': path,
        'file_source': file_source,
        'exported_at': payload.get('exported_at') if is_doc else None,
        'format': payload.get('format', 1) if is_doc else 1,
        'offset_sec': file_offset,
        'offset_source': offset_source,
        'events': normalized,
    }
