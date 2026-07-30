# MT5 Read-Only Bridge

Reads a running MetaTrader 5 terminal — account, positions, orders, quotes, bars,
closed deals, and the **economic calendar** — and serves it as JSON on localhost.

This is the read-only foundation. There is no order path here: no `order_send`,
no `order_check`, no route that can open, modify or close a position. Execution,
if it is ever added, belongs in a separate module behind its own explicit guards
so that a bug in a data reader can never reach the trade path.

## Why a Python bridge

MetaTrader 5 has no Node binding. The official `MetaTrader5` package is Python,
and Windows-only. So the Node MCP layer talks HTTP to this process instead of
linking against the terminal directly.

## Why the MQL5 script

The `MetaTrader5` Python package exposes **no calendar API** —
`CalendarValueHistory()` and `CalendarEventById()` are MQL5-only. So
`calendar_export.mq5` runs inside the terminal and writes the calendar to JSON,
which the bridge reads back.

That matters more than it sounds: the terminal's calendar gives you scheduled
future events with importance ratings *and* historical `actual` / `forecast` /
`previous` values, already structured. No scraping, nothing to keep repairing.

## Prerequisites

- **Windows** — the `MetaTrader5` package does not exist for macOS or Linux
- **MetaTrader 5 terminal**, logged in to a broker account
- **Python 3.9+**
- `pip install MetaTrader5`

## Setup

### 1. Export the calendar

1. Copy `calendar_export.mq5` into `<terminal data folder>/MQL5/Scripts/`
   (in the terminal: **File → Open Data Folder**)
2. Open it in MetaEditor and compile (**F7**)
3. In the terminal, drag the script onto any chart and run it

It writes `MQL5/Files/mcp_calendar.json` and prints how many events it exported.
Inputs let you set the history and forward windows and filter by currency
(`USD` alone is enough for gold).

Re-run it to refresh — daily is plenty, since scheduled events rarely move.

### 2. Point the bridge at that file

```bat
set MT5_CALENDAR_FILE=C:\Users\<you>\AppData\Roaming\MetaQuotes\Terminal\<hash>\MQL5\Files\mcp_calendar.json
```

### 3. Start the bridge

```bat
python bridge.py
```

Optional:

```bat
python bridge.py --port 9100
set MT5_BRIDGE_TOKEN=some-secret    :: then send X-Bridge-Token on every request
set MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
```

It binds `127.0.0.1` only, and starts even if the terminal isn't reachable so
that `/health` can tell you why.

### 4. Verify

```bat
curl http://127.0.0.1:8765/health
curl "http://127.0.0.1:8765/quote?symbol=XAUUSD"
curl "http://127.0.0.1:8765/blackout?currencies=USD"
```

## Routes

| Route | Purpose |
|---|---|
| `/health` | Terminal connected, account identity, server clock offset |
| `/account` | Balance, equity, margin, leverage, currency |
| `/positions` | Open positions (`?symbol=` to narrow) |
| `/orders` | Pending orders |
| `/quote` | `?symbol=XAUUSD` — bid, ask, spread, digits |
| `/bars` | `?symbol=&timeframe=5&count=100&summary=1` |
| `/deals` | `?from=&to=&symbol=` — closed fills, for journaling |
| `/calendar` | `?currencies=USD&min_importance=high&from=&to=` |
| `/blackout` | `?currencies=USD&before_min=15&after_min=15` |

Timeframes use the same strings as tradingview-mcp: `1`, `5`, `15`, `60`, `240`,
`D`, `W`, `M` (plus aliases like `15m`, `4h`, `daily`).

Anything other than `GET` returns **405**. Bars are capped at 5000.

### `/blackout` is the one that matters

It answers "is it safe to act right now" with one deterministic call — no model
in the loop:

```json
{
  "blackout": true,
  "active": [{"event": "Consumer Price Index", "importance": "high",
              "minutes_until": 7.8, "forecast": 2.9, "previous": 2.8}],
  "next": {"event": "FOMC Statement", "minutes_until": 89.5},
  "window": {"before_min": 15, "after_min": 15}
}
```

`minutes_until` is positive for a pending release, negative for one that already
landed. An event blacks out from `before_min` ahead of it to `after_min` after.
Defaults to high-importance events only; pass `min_importance=moderate` to widen.

## Timestamp caveat — read this before joining data

**Bar timestamps are broker-server time. Calendar times are UTC.** Most brokers
run a server clock offset from UTC (often UTC+2/+3), so joining bars against
calendar events without correcting will silently misalign them by hours — which
would quietly wreck any news-impact study.

`/health` reports `server_utc_offset_sec` for exactly this reason. Apply it
before correlating the two, and sanity-check one known release against the bar
that should contain it.

## Tests

```bash
python -m unittest discover -s mt5-bridge
```

31 tests over `normalize.py` — timeframe resolution, bar summaries, MQL5 value
decoding, calendar filtering, and the blackout window logic including boundary
cases and asymmetric windows. These need no terminal and run in CI on Linux.

`mt5_client.py` needs Windows and a live terminal, so it is **not** covered.
Verify it manually with the `curl` calls above.

## Not here yet

- Node MCP tool layer — comes next, once the response shapes are confirmed
  against a real terminal
- Any execution path
- The signal scanner and trade journal
