# TradingView MCP — Claude Instructions

85 tools for reading and controlling a live TradingView Desktop chart via CDP (port 9222).

## Decision Tree — Which Tool When

### "What's on my chart right now?"
1. `chart_get_state` → symbol, timeframe, chart type, list of all indicators with entity IDs
2. `data_get_study_values` → current numeric values from all visible indicators (RSI, MACD, BBands, EMAs, etc.)
3. `quote_get` → real-time price, OHLC, volume for current symbol

### "What levels/lines/labels are showing?"
Custom Pine indicators draw with `line.new()`, `label.new()`, `table.new()`, `box.new()`. These are invisible to normal data tools. Use:

1. `data_get_pine_lines` → horizontal price levels drawn by indicators (deduplicated, sorted high→low)
2. `data_get_pine_labels` → text annotations with prices (e.g., "PDH 24550", "Bias Long ✓")
3. `data_get_pine_tables` → table data formatted as rows (e.g., session stats, analytics dashboards)
4. `data_get_pine_boxes` → price zones / ranges as {high, low} pairs

Use `study_filter` parameter to target a specific indicator by name substring (e.g., `study_filter: "Profiler"`).

### "Give me price data"
- `data_get_ohlcv` with `summary: true` → compact stats (high, low, range, change%, avg volume, last 5 bars)
- `data_get_ohlcv` without summary → all bars (use `count` to limit, default 100)
- `quote_get` → single latest price snapshot

### "Analyze my chart" (full report workflow)
1. `quote_get` → current price
2. `data_get_study_values` → all indicator readings
3. `data_get_pine_lines` → key price levels from custom indicators
4. `data_get_pine_labels` → labeled levels with context (e.g., "Settlement", "ASN O/U")
5. `data_get_pine_tables` → session stats, analytics tables
6. `data_get_ohlcv` with `summary: true` → price action summary
7. `capture_screenshot` → visual confirmation

### "Change the chart"
- `chart_set_symbol` → switch ticker (e.g., "AAPL", "ES1!", "NYMEX:CL1!")
- `chart_set_timeframe` → switch resolution (e.g., "1", "5", "15", "60", "D", "W")
- `chart_set_type` → switch chart style (Candles, HeikinAshi, Line, Area, Renko, etc.)
- `chart_manage_indicator` → add or remove studies (use full name: "Relative Strength Index", not "RSI")
- `chart_scroll_to_date` → jump to a date (ISO format: "2025-01-15")
- `chart_set_visible_range` → zoom to exact date range (unix timestamps)

### "Work on Pine Script"
1. `pine_set_source` → inject code into editor
2. `pine_smart_compile` → compile with auto-detection + error check
3. `pine_get_errors` → read compilation errors
4. `pine_get_console` → read log.info() output
5. `pine_get_source` → read current code back (WARNING: can be very large for complex scripts)
6. `pine_save` → save to TradingView cloud
7. `pine_new` → create blank indicator/strategy/library
8. `pine_open` → load a saved script by name

### "Practice trading with replay"
1. `replay_start` with `date: "2025-03-01"` → enter replay mode
2. `replay_step` → advance one bar
3. `replay_autoplay` → auto-advance (set speed with `speed` param in ms)
4. `replay_trade` with `action: "buy"/"sell"/"close"` → execute trades
5. `replay_status` → check position, P&L, current date
6. `replay_stop` → return to realtime

### "Screen multiple symbols"
- `batch_run` with `symbols: ["ES1!", "NQ1!", "YM1!"]` and `action: "screenshot"` or `"get_ohlcv"`

### "Draw on the chart"
- `draw_shape` → horizontal_line, trend_line, rectangle, text (pass point + optional point2)
- `draw_list` → see what's drawn
- `draw_remove_one` → remove by ID
- `draw_clear` → remove all

### "Show me that trade" / "capture my trade for review"
`capture_trade` — reads the trade from the MT5 bridge, marks it on the chart, screenshots it, files it against `position_id`. Needs the bridge (8765); files with the journal (8766) if it is up.

- `capture_trade` with no args → the most recently closed trade, as an **entry** capture
- `position_id` → a specific trade (get ids from `mt5_trades`)
- `kind: "entry"` (default) → the frame stops at the entry bar. No exit, no P&L, no outcome-derived level. **This is the one to use for reviewing a decision**
- `kind: "review"` → the whole trade including the exit. Use when the question is about the outcome
- `stop` / `target` → prices known at entry. An entry capture draws a stop **only** from these: one read back from the exit fill would exist only for losing trades, so its presence would give the outcome away
- `symbol` → a TradingView symbol, if the broker symbol is not in the mapping table

Do not work around an unmapped symbol by guessing a TradingView ticker into `symbol`. The error names the file to add it to — a wrong mapping produces a plausible chart of the wrong instrument filed against a real position.

### "Manage alerts"
- `alert_create` → set price alert (condition: "crossing", "greater_than", "less_than")
- `alert_list` → view active alerts
- `alert_delete` → remove alerts

### "Navigate the UI"
- `ui_open_panel` → open/close pine-editor, strategy-tester, watchlist, alerts, trading
- `ui_click` → click buttons by aria-label, text, or data-name
- `layout_switch` → load a saved layout by name
- `ui_fullscreen` → toggle fullscreen
- `capture_screenshot` → take a screenshot (regions: "full", "chart", "strategy_tester")

### "TradingView isn't running"
- `tv_launch` → auto-detect and launch TradingView with CDP on Mac/Win/Linux
- `tv_health_check` → verify connection is working

## MT5 Broker Data (separate MCP server)

A second MCP server — `mt5`, 19 read-only tools — exposes a MetaTrader 5 terminal
via the local Python bridge (`mt5-bridge/bridge.py`). It is a **different
process** from this one: TradingView tools need CDP on 9222, MT5 tools need the
bridge on 8765, and neither should fail because the other is closed.

**Read-only.** No tool there can open, modify or close a position.

### "What's my broker account doing?"
1. `mt5_health` → bridge + terminal state, account identity, broker clock offset
2. `mt5_account` → balance, equity, margin, leverage
3. `mt5_positions` / `mt5_orders` → what is open right now

### "Is it safe to trade right now?"
- `mt5_blackout` → one deterministic answer, with the events causing it and the
  next one due. Check before acting on any signal.

### "What's coming up on the calendar?"
- `mt5_calendar` → scheduled events with importance, forecast, previous, and
  `actual` once released. `actual` is null until an event happens — correct, not
  missing. Defaults to high importance; unfiltered runs to hundreds of rows.

Calendar events carry `time_server_iso` and `time_utc_iso` like every other MT5
timestamp — MQL5 reports them on the broker clock, and the exporter resolves
them to UTC. If `mt5_blackout` returns `success: false` with `blackout: null`,
the calendar file predates that conversion and the offset is unknown; re-run
`calendar_export.mq5`. It refuses rather than comparing mismatched clocks.

### "How did my trading go?"
- `mt5_deals` → summary by default: win rate, net P&L, exit reasons
  (`stop_loss` / `take_profit` / `mobile` / …). Pass `summary: false` with
  `limit`/`offset` to page through individual fills.
- **Only shows trades that were TAKEN.** Skipped setups leave no trace in MT5,
  so any journal that needs them must log signals separately.
- `mt5_analytics` → the deeper question: expectancy, payoff ratio, max drawdown,
  win/loss streaks, and performance grouped by exit reason, session, symbol,
  weekday or hour. Answers "when do I lose money", not just "how much".
  Pass `starting_balance` for drawdown percentages; without it they are null
  rather than computed against an invented base.

### "What would my strategy do?"

Five tools drive a strategy engine. All read-only — they describe what a
strategy *would* do; none can place an order.

1. `mt5_setups` → detected entry signals with **no P&L attached**. Check these
   against a chart first: if the detectors find the wrong things, no amount of
   parameter tuning fixes it
2. `mt5_backtest` → those signals replayed with stops, targets, partials and
   the breakeven trail
3. `mt5_sweep` → every parameter combination judged on bars it was not chosen
   on. Slow — thirty configurations by default
4. `mt5_paper` → what the strategy would be doing right now
5. `mt5_reconcile` → signals against actual fills: followed, missed, and
   traded-without-a-signal

All five take the same config knobs (`conditions`, `mode`, `required`,
`confirmation`, `buffer_pips`, `risk_pct`, `target_r`, `partial_pct`,
`partial_at_r`, `trail`). **`trail: "none"` disables the breakeven trail** —
the control case for testing whether trailing early helps or hurts.

Two things the backtest refuses to do, because each flatters a strategy:
entry fills at the *next* bar open rather than the confirmation close, and a
bar containing both stop and target resolves as the **stop** (bar data cannot
order two intrabar touches).

### The journal — the one thing that can write

A **third process** (`mt5-bridge/journal_service.py`, port 8766) records what
MT5 structurally cannot: the setups that were *passed on*, and why. It has no
MCP tools yet; talk to it over HTTP.

It writes a local SQLite file and nothing else — no broker client is imported,
and `bridge.py` still has no `do_POST`. Keep it that way: the read bridge's
value is that it cannot be made to write.

`skip_reasons_claimed` and `exit_kinds_claimed` are named that way on purpose.
A reason given after the outcome is known may be a rationalisation, so never
present a self-report as a measurement. The evidence is the *join* — replay the
skipped signals and see what they would have done.

`POST /prune` is a dry run unless given `{"apply": true}`, and there is no
delete route.

### Execution — the only thing that can move money

A **fourth process** (`mt5-bridge/execution_service.py`, port 8767), started
only with `--exec`. No MCP tools, deliberately: placing orders should be an
explicit human act, not something reachable from a conversation.

Defaults: disarmed, dry run, demo only, expiring arm window, symbol allowlist,
stop required. It contains no strategy and cannot import the detectors.

If asked to place a trade, do not try. Point at `POST /arm` then `POST /order`
and let the person run them.

### Comparing TradingView against the broker
The same instrument has different names in each system — `FX:XAUUSD` on
TradingView, `GOLD.i#` on XM. Use `mt5_symbol_search` to find the broker's name;
never assume `XAUUSD` exists.

Useful combinations: read levels from the chart with `data_get_pine_lines`, then
check where fills actually landed with `mt5_deals`; or mark real entries on the
chart by feeding `mt5_deals` prices into `draw_shape`.

`capture_trade` does that second one properly — it maps the symbol through a
table rather than guessing, and it will not put post-entry information into an
entry frame. Prefer it over hand-drawing a trade.

### Timestamps — read before joining anything
MetaTrader 5 reports times against the **broker clock**, not UTC. Every MT5
timestamp is labelled twice: `time_server` / `time_server_iso` (no `Z`, because
it is not UTC) and `time_utc` / `time_utc_iso`. **Always join on `time_utc`** —
the calendar is UTC, and mixing the two misaligns everything by the broker
offset (3 hours on XM) while looking perfectly reasonable. If the offset is
unknown, `time_utc` is null rather than a guess.

### MT5 output sizes
| Tool | Typical Output |
|------|---------------|
| `mt5_health` / `mt5_account` | ~300 bytes |
| `mt5_quote` | ~250 bytes |
| `mt5_blackout` | ~600 bytes |
| `mt5_bars` (summary) | ~600 bytes |
| `mt5_deals` (summary) | ~500 bytes |
| `mt5_calendar` (high, one currency) | ~2-4 KB |
| `mt5_deals` (50 rows) | ~15 KB |
| `mt5_analytics` | ~2-5 KB (curve omitted unless asked) |

If an MT5 tool reports the bridge is unreachable, the bridge is not running:
`python mt5-bridge/bridge.py`

The bridge finds the calendar export itself under the terminal data folder, so
`MT5_CALENDAR_FILE` is only needed for a non-standard location. If no export is
found the error lists every path it searched.

## Context Management Rules

These tools can return large payloads. Follow these rules to avoid context bloat:

1. **Always use `summary: true` on `data_get_ohlcv`** unless you specifically need individual bars
2. **Always use `study_filter`** on pine tools when you know which indicator you want — don't scan all studies unnecessarily
3. **Never use `verbose: true`** on pine tools unless the user specifically asks for raw drawing data with IDs/colors
4. **Avoid calling `pine_get_source`** on complex scripts — it can return 200KB+. Only read if you need to edit the code.
5. **Avoid calling `data_get_indicator`** on protected/encrypted indicators — their inputs are encoded blobs. Use `data_get_study_values` instead for current values.
6. **Use `capture_screenshot`** for visual context instead of pulling large datasets — a screenshot is ~300KB but gives you the full visual picture
7. **Call `chart_get_state` once** at the start to get entity IDs, then reference them — don't re-call repeatedly
8. **Cap your OHLCV requests** — `count: 20` for quick analysis, `count: 100` for deeper work, `count: 500` only when specifically needed

### Output Size Estimates (compact mode)
| Tool | Typical Output |
|------|---------------|
| `quote_get` | ~200 bytes |
| `data_get_study_values` | ~500 bytes (all indicators) |
| `data_get_pine_lines` | ~1-3 KB per study (deduplicated levels) |
| `data_get_pine_labels` | ~2-5 KB per study (capped at 50) |
| `data_get_pine_tables` | ~1-4 KB per study (formatted rows) |
| `data_get_pine_boxes` | ~1-2 KB per study (deduplicated zones) |
| `data_get_ohlcv` (summary) | ~500 bytes |
| `data_get_ohlcv` (100 bars) | ~8 KB |
| `capture_screenshot` | ~300 bytes (returns file path, not image data) |
| `capture_trade` | ~1 KB (file path, levels, range, and the notes explaining what is and is not in the frame) |

## Tool Conventions

- All tools return `{ success: true/false, ... }`
- Entity IDs (from `chart_get_state`) are session-specific — don't cache across sessions
- Pine indicators must be **visible** on chart for pine graphics tools to read their data
- `chart_manage_indicator` requires **full indicator names**: "Relative Strength Index" not "RSI", "Moving Average Exponential" not "EMA", "Bollinger Bands" not "BB"
- Screenshots save to `screenshots/` directory with timestamps
- OHLCV capped at 500 bars, trades at 20 per request
- Pine labels capped at 50 per study by default (pass `max_labels` to override)

## Architecture

```
Claude Code ←→ MCP Server (stdio) ←→ CDP (localhost:9222) ←→ TradingView Desktop (Electron)
```

Pine graphics path: `study._graphics._primitivesCollection.dwglines.get('lines').get(false)._primitivesDataById`
