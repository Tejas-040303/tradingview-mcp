"""
Pure data-shaping logic for the MT5 bridge.

Nothing here imports MetaTrader5, so this module is importable — and testable —
on any platform. All terminal I/O lives in mt5_client.py; keeping the two apart
is what makes the interesting logic (bar summaries, calendar filtering, news
blackout windows) verifiable without a Windows terminal attached.
"""
from datetime import datetime, timezone

# Timeframe names mirror tradingview-mcp's convention ("1", "5", "60", "D") so
# both halves of the stack speak the same language. Values are MetaTrader5
# constant *names*, resolved by mt5_client at call time to avoid importing the
# package here.
TIMEFRAMES = {
    '1': 'TIMEFRAME_M1',
    '2': 'TIMEFRAME_M2',
    '3': 'TIMEFRAME_M3',
    '5': 'TIMEFRAME_M5',
    '10': 'TIMEFRAME_M10',
    '15': 'TIMEFRAME_M15',
    '30': 'TIMEFRAME_M30',
    '60': 'TIMEFRAME_H1',
    '120': 'TIMEFRAME_H2',
    '240': 'TIMEFRAME_H4',
    'D': 'TIMEFRAME_D1',
    'W': 'TIMEFRAME_W1',
    'M': 'TIMEFRAME_MN1',
}

IMPORTANCE_RANK = {'none': 0, 'low': 1, 'moderate': 2, 'high': 3}

# MQL5 ENUM_CALENDAR_EVENT_IMPORTANCE ordinals.
MQL_IMPORTANCE = {0: 'none', 1: 'low', 2: 'moderate', 3: 'high'}


def resolve_timeframe(tf):
    """Map a timeframe string to its MetaTrader5 constant name."""
    key = str(tf).strip().upper()
    if key in TIMEFRAMES:
        return TIMEFRAMES[key]
    # Accept lowercase minute strings and common aliases.
    alias = {'1M': '1', '5M': '5', '15M': '15', '30M': '30', '1H': '60',
             '4H': '240', '1D': 'D', 'DAILY': 'D', 'WEEKLY': 'W'}
    if key in alias:
        return TIMEFRAMES[alias[key]]
    raise ValueError(
        f'Unknown timeframe {tf!r}. Supported: {", ".join(TIMEFRAMES)}')


# A broker server clock can plausibly sit between UTC-12 and UTC+14. Anything
# outside that means the tick we measured against was stale, not that the
# server is in an exotic timezone.
MAX_PLAUSIBLE_OFFSET_SEC = 50400
# Server offsets are whole or half hours; rounding absorbs the sub-minute noise
# of measuring against a tick that arrived a moment ago.
OFFSET_ROUND_SEC = 1800


def iso(ts):
    """Unix seconds -> ISO 8601 UTC string. Only for values already in UTC."""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def iso_naive(ts):
    """
    Format a server-clock timestamp with no timezone suffix.

    MetaTrader 5 reports tick and bar times as epoch seconds computed against
    the *broker's* clock, so they are not true Unix UTC. Rendering them with a
    'Z' would claim UTC and be wrong by the server offset — three hours on a
    UTC+3 broker. No suffix, no false claim.
    """
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')


def infer_server_offset(newest_tick_time, now_utc,
                        max_abs=MAX_PLAUSIBLE_OFFSET_SEC,
                        round_to=OFFSET_ROUND_SEC):
    """
    Infer the broker's clock offset from a tick timestamp, or None if unknowable.

    The measurement is `tick_time - now_utc`, which only holds while ticks are
    arriving. Over a weekend the newest tick can be days old, which would yield
    a large negative number that looks like a real offset. Anything beyond the
    plausible timezone range is therefore reported as unknown rather than
    guessed at — a wrong offset silently corrupts every timestamp downstream.
    """
    if newest_tick_time is None:
        return None
    raw = int(newest_tick_time) - int(now_utc)
    if abs(raw) > max_abs:
        return None
    return int(round(raw / float(round_to)) * round_to)


def time_fields(ts, offset_sec):
    """
    Expand an MT5 timestamp into explicitly-labelled server and UTC forms.

    Emits time_utc as None when the offset is unknown. A missing value is
    recoverable; a confidently wrong one is not.
    """
    if not ts:
        return {}
    out = {'time_server': int(ts), 'time_server_iso': iso_naive(ts)}
    if offset_sec is None:
        out['time_utc'] = None
        out['time_utc_iso'] = None
    else:
        utc = int(ts) - int(offset_sec)
        out['time_utc'] = utc
        out['time_utc_iso'] = iso(utc)
    return out


def mid_price(bid, ask, digits=None):
    """
    Mid price from bid/ask.

    CFDs have no central exchange, so brokers leave last-trade price and traded
    volume empty. Mid is the usable reference for anything computing levels.
    """
    if bid is None or ask is None or not bid or not ask:
        return None
    mid = (bid + ask) / 2.0
    return round(mid, digits) if digits is not None else mid


def clean_last(last):
    """
    Normalise a CFD's empty last-trade price to None.

    MetaTrader 5 reports 0.0 rather than null when a symbol has no last-trade
    price. Passed through unchanged, that reads as a real price of zero.
    """
    if last is None or last == 0:
        return None
    return last


def filter_symbols(symbols, search=None, limit=200):
    """
    Filter broker symbols by a case-insensitive substring of name or description.

    Brokers expose thousands of instruments under non-obvious names — spot gold
    is 'GOLD.i#' on some, 'XAUUSD' on others — so discovery needs to be a
    search, not a guess.
    """
    needle = (search or '').strip().upper()
    kept = []
    for sym in symbols or []:
        name = str(sym.get('name') or '')
        desc = str(sym.get('description') or '')
        if needle and needle not in name.upper() and needle not in desc.upper():
            continue
        kept.append(sym)
    kept.sort(key=lambda s: str(s.get('name') or ''))
    return kept[:max(1, int(limit))]


def _bar_time_labels(bar):
    """
    Server and UTC labels for a bar, whichever timestamp shape it carries.

    Rows from mt5_client arrive pre-expanded by time_fields(); a bare 'time'
    key is accepted too, and treated as server time since that is what
    MetaTrader 5 reports.
    """
    if 'time_server_iso' in bar:
        return bar.get('time_server_iso'), bar.get('time_utc_iso')
    if 'time' in bar:
        return iso_naive(bar['time']), None
    return None, None


def summarize_bars(bars):
    """
    Compact stats for a bar series, mirroring data_get_ohlcv's summary mode.

    Returns None for an empty series rather than raising — an empty result is a
    normal outcome for a symbol with no history in the requested window.
    """
    if not bars:
        return None

    highs = [b['high'] for b in bars]
    lows = [b['low'] for b in bars]
    first, last = bars[0], bars[-1]
    opened, closed = first['open'], last['close']
    volumes = [b.get('tick_volume', 0) for b in bars]

    from_server, from_utc = _bar_time_labels(first)
    to_server, to_utc = _bar_time_labels(last)

    return {
        'count': len(bars),
        # Server and UTC kept separate and named. A single ambiguous field is
        # how a three-hour broker offset silently corrupts a correlation.
        'from_server': from_server,
        'to_server': to_server,
        'from_utc': from_utc,
        'to_utc': to_utc,
        'open': opened,
        'close': closed,
        'high': max(highs),
        'low': min(lows),
        'range': round(max(highs) - min(lows), 6),
        'change': round(closed - opened, 6),
        'change_pct': round((closed - opened) / opened * 100, 3) if opened else None,
        'avg_volume': round(sum(volumes) / len(volumes), 2) if volumes else 0,
        'last_5': bars[-5:],
    }


def scaled(value, digits=0):
    """
    Decode an MQL5 calendar value.

    MqlCalendarValue stores actual/forecast/previous as longs scaled by 1e6,
    with LONG_MIN standing in for "no value". The exporter writes LONG_MIN
    through as null, so anything arriving as None stays None.
    """
    if value is None:
        return None
    out = value / 1_000_000.0
    return round(out, digits) if digits else out


def normalize_calendar(rows):
    """
    Normalise exported calendar rows into a stable shape, sorted by time.

    Accepts rows as written by calendar_export.mq5. Unparseable rows are
    skipped rather than aborting the batch — a single malformed event should
    not blind the blackout check.
    """
    out = []
    for row in rows or []:
        try:
            ts = int(row['time'])
        except (KeyError, TypeError, ValueError):
            continue

        importance = row.get('importance')
        if isinstance(importance, int):
            importance = MQL_IMPORTANCE.get(importance, 'none')
        importance = str(importance or 'none').strip().lower()
        if importance not in IMPORTANCE_RANK:
            importance = 'none'

        digits = int(row.get('digits') or 0)
        out.append({
            'timestamp': ts,
            'time': iso(ts),
            'currency': str(row.get('currency') or '').upper(),
            'country': row.get('country') or '',
            'event': row.get('event') or '',
            'importance': importance,
            'actual': scaled(row.get('actual'), digits),
            'forecast': scaled(row.get('forecast'), digits),
            'previous': scaled(row.get('previous'), digits),
            'unit': row.get('unit') or '',
        })

    out.sort(key=lambda r: r['timestamp'])
    return out


def filter_calendar(rows, currencies=None, min_importance='low',
                    from_ts=None, to_ts=None):
    """Filter normalised rows by currency, importance floor and time window."""
    floor = IMPORTANCE_RANK.get(str(min_importance).lower(), 0)
    wanted = {c.upper() for c in currencies} if currencies else None

    kept = []
    for row in rows:
        if IMPORTANCE_RANK[row['importance']] < floor:
            continue
        if wanted and row['currency'] not in wanted:
            continue
        if from_ts is not None and row['timestamp'] < from_ts:
            continue
        if to_ts is not None and row['timestamp'] > to_ts:
            continue
        kept.append(row)
    return kept


def blackout_status(rows, now_ts, before_min=15, after_min=15,
                    currencies=None, min_importance='high'):
    """
    Decide whether `now_ts` falls inside a news blackout window.

    An event blacks out the window from `before_min` minutes ahead of its
    release to `after_min` minutes after it. This is the check an automated
    strategy calls before acting: one deterministic answer, no model involved.

    `minutes_until` is positive for a pending release and negative for one that
    has already landed.
    """
    matching = filter_calendar(rows, currencies=currencies,
                               min_importance=min_importance)

    active, upcoming = [], []
    for row in matching:
        minutes_until = (row['timestamp'] - now_ts) / 60.0
        entry = dict(row, minutes_until=round(minutes_until, 1))
        if -abs(after_min) <= minutes_until <= abs(before_min):
            active.append(entry)
        elif minutes_until > 0:
            upcoming.append(entry)

    # Soonest first, so the caller reads the binding constraint at index 0.
    active.sort(key=lambda r: abs(r['minutes_until']))
    upcoming.sort(key=lambda r: r['minutes_until'])

    return {
        'blackout': bool(active),
        'now': iso(now_ts),
        'window': {'before_min': abs(before_min), 'after_min': abs(after_min)},
        'min_importance': str(min_importance).lower(),
        'currencies': sorted({c.upper() for c in currencies}) if currencies else None,
        'active': active,
        'next': upcoming[0] if upcoming else None,
        'minutes_until_next': upcoming[0]['minutes_until'] if upcoming else None,
    }
