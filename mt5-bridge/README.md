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
curl.exe http://127.0.0.1:8765/health
curl.exe "http://127.0.0.1:8765/symbols?search=gold"
curl.exe "http://127.0.0.1:8765/quote?symbol=GOLD.i%23"
curl.exe "http://127.0.0.1:8765/blackout?currencies=USD"
```

Find your instrument with `/symbols` before querying it — broker names vary and
`XAUUSD` does not exist everywhere.

In PowerShell, call `curl.exe` explicitly (bare `curl` is an alias for
`Invoke-WebRequest` in PowerShell 5.1) and always quote URLs containing `&`,
which PowerShell treats as an operator. `Invoke-RestMethod <url> | ConvertTo-Json
-Depth 6` works well too.

## Routes

| Route | Purpose |
|---|---|
| `/health` | Terminal connected, account identity, server clock offset |
| `/account` | Balance, equity, margin, leverage, currency |
| `/symbols` | `?search=gold&limit=200` — find what your broker calls an instrument |
| `/positions` | Open positions (`?symbol=` to narrow) |
| `/orders` | Pending orders |
| `/quote` | `?symbol=GOLD.i%23` — bid, ask, mid, spread, digits |
| `/bars` | `?symbol=&timeframe=5&count=100&summary=1` |
| `/deals` | `?from=&to=&symbol=&limit=100&offset=0&summary=1` — fills, for journaling |
| `/calendar` | `?currencies=USD&min_importance=high&from=&to=` |
| `/blackout` | `?currencies=USD&before_min=15&after_min=15` |

Timeframes use the same strings as tradingview-mcp: `1`, `5`, `15`, `60`, `240`,
`D`, `W`, `M` (plus aliases like `15m`, `4h`, `daily`).

Anything other than `GET` returns **405**. Bars are capped at 5000.

### Symbol names need URL encoding

Broker symbols routinely contain characters with meaning in a URL. XM names spot
gold `GOLD.i#`, and `#` starts a URL fragment — passed raw, the bridge only ever
receives `GOLD.i` and fails. Encode it:

```
/quote?symbol=GOLD.i%23        correct
/quote?symbol=GOLD.i#          truncated at the #
```

Use `/symbols?search=gold` to discover the exact name first. Do not assume
`XAUUSD` exists — on XM it does not.

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

### Reading the calendar

`/calendar` returns everything unless you filter it. `min_importance` defaults
to `none` — no filter means no filter — so narrow it explicitly:

```powershell
# Upcoming high-impact USD events
(Invoke-RestMethod "http://127.0.0.1:8765/calendar?currencies=USD&min_importance=high").events |
  Select-Object time, event, actual, forecast, previous | Format-Table

# Only events that have already been released, where `actual` is populated
$now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
(Invoke-RestMethod "http://127.0.0.1:8765/calendar?currencies=USD&min_importance=high").events |
  Where-Object { $_.timestamp -lt $now } |
  Select-Object time, event, actual, forecast, previous | Format-Table
```

**`actual` is `null` until an event is released** — that is correct, not missing
data. `forecast` and `previous` are known ahead of time; `actual` only exists
afterwards. Select the column explicitly, or you will not see it.

### Choosing the export window

The script's `DaysBack` / `DaysAhead` inputs serve two different jobs:

| Purpose | Suggested inputs |
|---|---|
| Blackout checks | `DaysBack=7`, `DaysAhead=21` (the default) |
| Impact studies | `DaysBack=365`, `DaysAhead=21`, `Currencies=USD` |

A week of history is enough to know what just happened, but measuring how gold
reacts to CPI needs a year of releases with their `actual` vs `forecast`
values. Filtering to a single currency keeps the longer window small.

## Timestamps — read this before joining data

**MetaTrader 5 reports tick and bar times against the broker's clock, not UTC.**
They look like Unix timestamps but are shifted by the server offset — 3 hours on
XM. Formatting one as UTC and appending `Z` produces a confident lie, so the
bridge never does that. Every timestamp comes back explicitly labelled:

```json
{
  "time_server": 1785406526,
  "time_server_iso": "2026-07-30T10:15:26",
  "time_utc": 1785395726,
  "time_utc_iso": "2026-07-30T07:15:26Z"
}
```

No `Z` on the server value, because it is not UTC. **Join on `time_utc`** —
calendar events are UTC, and mixing the two misaligns every correlation by the
offset while looking perfectly reasonable.

### When the offset is unknown

`time_utc` and `time_utc_iso` come back `null`, and `/health` reports
`server_utc_offset_source: "unknown"`.

The offset is measured by comparing the newest tick across several majors
against real UTC, which only works while ticks are arriving. Over a weekend the
newest tick can be days old and would imply an offset like `-169200` — so
anything outside the real timezone range (UTC-12..UTC+14) is reported as unknown
rather than guessed. A missing value is recoverable; a wrong one silently
corrupts everything downstream.

To pin it explicitly — recommended if you collect data across weekends:

```bat
set MT5_SERVER_UTC_OFFSET_SEC=10800    :: UTC+3, e.g. XM
```

`/health` then reports `server_utc_offset_source: "env"` and skips probing.

`/deals` shifts its window too: you pass UTC bounds, and it converts them to
server time before querying, because `history_deals_get` reads server time.

## `/deals` — paginated, summarised, decoded

An active month runs to hundreds of deals — enough to make a single unpaginated
response hundreds of kilobytes of JSON. So `/deals` **paginates by default**
(100 per page) and always includes a summary computed over the whole window,
not just the page:

```json
{
  "summary": {
    "deals": 400, "closed_trades": 200,
    "wins": 90, "losses": 110, "win_rate_pct": 45.0,
    "gross_profit": -120.5, "costs": -3.2, "net_profit": -123.7,
    "best": 40.0, "worst": -55.0, "avg_win": 6.5, "avg_loss": -6.4,
    "closed_by": {"stop_loss": 120, "mobile": 75, "take_profit": 4, "stop_out": 1}
  },
  "page": {"total": 400, "returned": 100, "offset": 0, "limit": 100, "has_more": true}
}
```

`summary=1` returns the summary alone. Walk pages with `offset`.

Only deals that closed exposure count as trades — entries carry no realised
P&L, and balance/credit rows are not trades at all, so both are excluded from
the win/loss counts.

### Enum fields are decoded

MetaTrader 5 reports `type`, `entry` and `reason` as bare integers. `"reason": 4`
is unreadable and not guessable, so each is decoded with the raw value kept
alongside:

```json
{"type": "buy", "type_raw": 0,
 "entry": "out", "entry_raw": 1,
 "reason": "stop_loss", "reason_raw": 4}
```

`reason` is the useful one for a journal — it separates a stop-out from a
take-profit from a manual close, and `mobile` / `web` / `expert` tell you where
the order came from. Unknown codes surface as `unknown_<n>` rather than being
dropped.

## CFD price fields

CFDs have no central exchange, so brokers leave last-trade price and traded
volume empty — MetaTrader 5 returns `0.0`, not null. The bridge normalises both
to `null` and adds `mid`, so a zero is never mistaken for a price:

```json
{"bid": 4047.68, "ask": 4047.94, "mid": 4047.81, "last": null, "volume": null}
```

Anything computing levels should use `mid`. Log `spread` alongside signals — it
widens sharply around news.

## Tests

```bash
python -m unittest discover -s mt5-bridge
```

92 tests, all runnable without a terminal and gated in CI on Linux.

`test_normalize.py` (78) covers the pure logic: timeframe resolution, bar
summaries, MQL5 value decoding, calendar filtering, blackout windows including
boundary cases and asymmetric windows, server-offset inference including the
stale-weekend-tick guard, timestamp labelling, CFD price normalisation, symbol
search, deal enum decoding, deal summaries and pagination.

`test_mt5_client.py` (14) exercises every route end to end against a fake
MetaTrader 5 module. These assert plumbing, not market behaviour — but that is
where a shadowed variable broke `/deals` pagination once, which no amount of
pure-function testing could have caught.

Real terminal behaviour is still unverified by CI. Check it with the `curl`
calls above.

## Using it from Claude Code

The bridge is HTTP, so it works with `curl` alone — but the `mt5` MCP server
(`src/mt5-server.js`) exposes the same routes as 10 tools Claude can call
directly. Register it alongside the TradingView server:

```json
{
  "mcpServers": {
    "tradingview": { "command": "node", "args": ["<path>/src/server.js"] },
    "mt5":         { "command": "node", "args": ["<path>/src/mt5-server.js"] }
  }
}
```

Two servers rather than one, deliberately: the TradingView tools need Desktop
running on CDP, these need the bridge on 8765, and closing one should not take
the other down.

Optional environment for the MCP server:

```
MT5_BRIDGE_URL      default http://127.0.0.1:8765
MT5_BRIDGE_TOKEN    must match MT5_BRIDGE_TOKEN on the bridge
MT5_BRIDGE_TIMEOUT  default 15000 ms
```

Restart Claude Code after editing the config, keep `bridge.py` running, then ask
it to run `mt5_health`. Symbol encoding is handled for you — pass `GOLD.i#`, not
`GOLD.i%23`.

## Not here yet

- Any execution path
- The signal scanner and trade journal
