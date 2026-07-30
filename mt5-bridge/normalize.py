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


def iso(ts):
    """Unix seconds -> ISO 8601 UTC string."""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


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

    return {
        'count': len(bars),
        'from': iso(first['time']),
        'to': iso(last['time']),
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
