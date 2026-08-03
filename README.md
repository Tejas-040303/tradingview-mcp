# TradingView MCP Bridge

Personal AI assistant for your charts and your broker account. Connects Claude Code to your locally running TradingView Desktop app via Chrome DevTools Protocol for AI-assisted chart analysis, Pine Script development, and workflow automation — and, optionally, to a **MetaTrader 5 terminal** for read-only account, market and economic-calendar data.

Two independent MCP servers:

| Server | Tools | Talks to | Needs |
|---|---|---|---|
| `tradingview` | 84 | TradingView Desktop over CDP | port 9222 |
| `mt5` | 11, read-only | MetaTrader 5 via a local Python bridge | port 8765 |

They are separate processes on purpose: closing TradingView must not take the broker tools down, and either can be registered alone. Everything stays on localhost.

> [!NOTE]
> This is a fork of [tradesdontlie/tradingview-mcp](https://github.com/tradesdontlie/tradingview-mcp). The TradingView side is upstream's work; the `mt5-bridge/` subsystem and the `mt5` MCP server are additions in this fork.

> [!WARNING]
> **This tool is not affiliated with, endorsed by, or associated with TradingView Inc., MetaQuotes Software Corp., or any broker.** It interacts with applications already running on your own machine. Review the [Disclaimer](#disclaimer) before use.

> [!IMPORTANT]
> **Requires a valid TradingView subscription.** This tool does not bypass or circumvent any TradingView paywall or access control. It reads from and controls the TradingView Desktop app already running on your machine.

> [!NOTE]
> **All data processing occurs locally on your machine.** No TradingView data is transmitted, stored, or redistributed externally by this tool.

> [!CAUTION]
> This tool accesses undocumented internal TradingView APIs via the Electron debug interface. These can change or break without notice in any TradingView update. Pin your TradingView Desktop version if stability matters to you.

## How It Works (and why it's safe to run)

This tool does not connect to TradingView's servers, modify any TradingView files, or intercept any network traffic. It communicates exclusively with your locally running TradingView Desktop instance via Chrome DevTools Protocol (CDP) — a standard debugging interface built into all Chromium/Electron applications by Google, including VS Code, Slack, and Discord.

The debug port is disabled by default and must be explicitly enabled by you using a standard Chromium flag (`--remote-debugging-port=9222`). Nothing happens without that deliberate step.

## What This Tool Does Not Do

- Connect to TradingView's servers or APIs
- Store, transmit, or redistribute any market data
- Work without a valid TradingView subscription and installed Desktop app
- Bypass any TradingView paywall or access restriction
- Execute real trades — the TradingView side is chart interaction only, and the MT5 side is read-only with no order-placement code
- Work if TradingView changes their internal Electron structure

## Research Context

This project explores an open research question: **how can LLM-based agents interact with professional trading interfaces to support human decision-making?**

Specifically it investigates:

- How structured tool APIs (MCP) can bridge LLMs and stateful desktop financial applications
- What latency, context, and reliability constraints emerge when an agent operates on live chart data
- How agents handle ambiguous financial UI state (e.g. interpreting Pine Script output, reading indicator tables)
- Whether natural language is an effective interface for chart navigation and Pine Script development
- The failure modes of LLM agents operating in real-time data environments

This is not a trading bot. It is an interface layer that makes a trading application legible to an LLM agent, allowing researchers and developers to study human-AI collaboration in financial workflows.

See [RESEARCH.md](RESEARCH.md) for open questions, findings, and related work.

## Prerequisites

For the TradingView side:

- **TradingView Desktop app** (paid subscription required for real-time data)
- **Node.js 18+**
- **Claude Code** with MCP support (for MCP tools) or any terminal (for CLI)
- **macOS, Windows, or Linux**

For the optional MT5 side:

- **Windows** — the official `MetaTrader5` Python package does not exist for macOS or Linux
- **MetaTrader 5 terminal**, logged in to a broker account
- **Python 3.9+** and `pip install MetaTrader5`

Neither side depends on the other. Run one, the other, or both.

## What It Does

Gives your AI assistant eyes and hands on your own chart:

- **Pine Script development** — write, inject, compile, debug, and iterate on scripts with AI assistance
- **Chart navigation** — change symbols, timeframes, zoom to dates, add/remove indicators
- **Visual analysis** — read your chart's indicator values, price levels, and annotations
- **Draw on charts** — trend lines, horizontal lines, rectangles, text annotations
- **Manage alerts** — create, list, and delete price alerts
- **Replay practice** — step through historical bars, practice entries/exits
- **Screenshots** — capture chart state for AI visual analysis
- **Multi-pane layouts** — set up 2x2, 3x1, etc. grids with different symbols per pane
- **Monitor your chart** — stream JSONL from your locally running chart for local monitoring scripts
- **CLI access** — every MCP tool is also a `tv` CLI command, pipe-friendly with JSON output
- **Launch TradingView** — auto-detect and launch with debug mode from any platform

And, with the optional MT5 bridge running:

- **Broker account state** — balance, equity, margin, open positions and pending orders
- **Broker market data** — bid/ask/mid, spread, and OHLCV straight from your broker's feed
- **Trade history** — closed fills summarised into win rate, net P&L and exit reasons, for journaling
- **Economic calendar** — scheduled events with importance, forecast, previous, and `actual` once released
- **News blackout check** — one deterministic answer to "is it safe to act right now"

## Install with Claude Code

Paste this into Claude Code and it will handle the rest:

> Install the TradingView MCP server. Clone https://github.com/Tejas-040303/tradingview-mcp.git, run npm install, add it to my MCP config at ~/.claude/.mcp.json, and launch TradingView with the debug port. Then verify the connection with tv_health_check.

Or follow the manual steps below.

## Quick Start

### 1. Install

```bash
git clone https://github.com/Tejas-040303/tradingview-mcp.git
cd tradingview-mcp
npm install
```

### 2. Launch TradingView with CDP

TradingView Desktop must be running with Chrome DevTools Protocol enabled on port 9222.

**Mac:**
```bash
./scripts/launch_tv_debug_mac.sh
```

**Windows:**
```bash
scripts\launch_tv_debug.bat
```

**Linux:**
```bash
./scripts/launch_tv_debug_linux.sh
```

**Or launch manually on any platform:**
```bash
/path/to/TradingView --remote-debugging-port=9222
```

**Or use the MCP tool** (auto-detects your install):
> "Use tv_launch to start TradingView in debug mode"

### 3. Add to Claude Code

Add to your Claude Code MCP config (`~/.claude/.mcp.json` or project `.mcp.json`):

```json
{
  "mcpServers": {
    "tradingview": {
      "command": "node",
      "args": ["/path/to/tradingview-mcp/src/server.js"]
    },
    "mt5": {
      "command": "node",
      "args": ["/path/to/tradingview-mcp/src/mt5-server.js"]
    }
  }
}
```

Replace `/path/to/tradingview-mcp` with your actual path. Omit the `mt5` entry if you are not using the broker side.

MCP servers only load at startup, so **fully exit and relaunch Claude Code** after editing this — reloading a session is not enough.

### 4. Verify

Ask Claude: *"Use tv_health_check to verify TradingView is connected"*

And, if you registered the MT5 server: *"Use mt5_health to check the broker connection"* (the bridge must be running — see below).

### 5. One command to start everything

Instead of juggling terminals for TradingView and the bridge:

```bash
npm run start:all      # start whatever is not already running
npm run status         # report what is up, start nothing
```

It probes both services first, starts only what is missing, and leaves anything
already running alone. It then prints the addresses worth knowing — dashboard
URL, API endpoint, CDP endpoint, every bridge route, and the log paths — rather
than just "up" or "down". Child output is streamed with a `[tv]` / `[bridge]`
prefix and mirrored to `logs/`; lines that look like errors are surfaced even
without `--verbose`, and a service that exits non-zero prints the tail of its
log. Ctrl+C stops only the services it started.

```bash
node scripts/start.js --no-tv       # bridge only
node scripts/start.js --no-bridge   # TradingView only
node scripts/start.js --verbose     # stream all child output
```

After the bridge is up it reports whether MT5 is actually connected — a bridge
answering on its port and a terminal being reachable are different things, and
conflating them is how "it's running" turns into a confusing debugging session.

### 6. Status dashboard

With the bridge running, open:

```
http://127.0.0.1:8765/
```

Account, open positions, pending orders, realised P&L by period, and a news
blackout banner. Read-only — the page cannot place, modify or close anything.

### 7. Optional — the MT5 bridge

MetaTrader 5 has no Node binding, so the `mt5` server talks to a small local Python process:

```bash
pip install MetaTrader5
python mt5-bridge/bridge.py
```

It binds `127.0.0.1` only and serves GET requests exclusively — every other verb returns 405, and no route can place, modify or cancel an order.

For the economic calendar, compile and run `mt5-bridge/calendar_export.mq5` inside MetaEditor once. The Python package exposes no calendar API, so the terminal-side script writes the data out; the bridge then finds the file on its own.

Full setup, routes and caveats: **[mt5-bridge/README.md](mt5-bridge/README.md)**.

## CLI

Every MCP tool is also accessible as a `tv` CLI command. All output is JSON for piping with `jq`.

```bash
# Install globally (optional)
npm link

# Or run directly
node src/cli/index.js <command>
```

### Quick Examples

```bash
tv status                          # check connection
tv quote                           # current price
tv symbol AAPL                     # change symbol
tv ohlcv --summary                 # price summary
tv screenshot -r chart             # capture chart
tv pine compile                    # compile Pine Script
tv pane layout 2x2                 # 4-chart grid
tv pane symbol 1 ES1!              # set pane symbol
tv stream quote | jq '.close'      # monitor price changes
```

### All Commands

```
tv status / launch / state / symbol / timeframe / type / info / search
tv quote / ohlcv / values
tv data lines/labels/tables/boxes/strategy/trades/equity/depth/indicator
tv pine get/set/compile/analyze/check/save/new/open/list/errors/console
tv draw shape/list/get/remove/clear
tv alert list/create/delete
tv watchlist get/add
tv indicator add/remove/toggle/set/get
tv layout list/switch
tv pane list/layout/focus/symbol
tv tab list/new/close/switch
tv replay start/step/stop/status/autoplay/trade
tv stream quote/bars/values/lines/labels/tables/all
tv ui click/keyboard/hover/scroll/find/eval/type/panel/fullscreen/mouse
tv screenshot / discover / ui-state / range / scroll
```

## Streaming

The `tv stream` commands poll your locally running TradingView Desktop instance at regular intervals via Chrome DevTools Protocol on localhost.

No connection is made to TradingView's servers. All data stays on your machine.

> [!WARNING]
> Programmatic consumption of TradingView data may conflict with their Terms of Use regardless of the data source. You are solely responsible for ensuring your usage complies.

```bash
tv stream quote                          # price tick monitoring
tv stream bars                           # bar-by-bar updates
tv stream values                         # indicator value monitoring
tv stream lines --filter "NY Levels"     # price level monitoring
tv stream tables --filter Profiler       # table data monitoring
tv stream all                            # all panes at once (multi-symbol)
```

## How Claude Knows Which Tool to Use

Claude reads [`CLAUDE.md`](CLAUDE.md) automatically when working in this project. It contains a complete decision tree:

| You say... | Claude uses... |
|------------|---------------|
| "What's on my chart?" | `chart_get_state` → `data_get_study_values` → `quote_get` |
| "What levels are showing?" | `data_get_pine_lines` → `data_get_pine_labels` |
| "Read the session table" | `data_get_pine_tables` with `study_filter` |
| "Give me a full analysis" | `quote_get` → `data_get_study_values` → `data_get_pine_lines` → `data_get_pine_labels` → `data_get_pine_tables` → `data_get_ohlcv` (summary) → `capture_screenshot` |
| "Switch to AAPL daily" | `chart_set_symbol` → `chart_set_timeframe` |
| "Write a Pine Script for..." | `pine_set_source` → `pine_smart_compile` → `pine_get_errors` |
| "Start replay at March 1st" | `replay_start` → `replay_step` → `replay_trade` |
| "Set up a 4-chart grid" | `pane_set_layout` → `pane_set_symbol` for each pane |
| "Draw a level at 24500" | `draw_shape` (horizontal_line) |
| "Take a screenshot" | `capture_screenshot` |
| "Is it safe to trade right now?" | `mt5_blackout` |
| "How did last month go?" | `mt5_deals` (summary) |
| "What's my account state?" | `mt5_health` → `mt5_account` → `mt5_positions` |
| "What does my broker call gold?" | `mt5_symbol_search` |
| "Mark my real entries on the chart" | `mt5_deals` → `draw_shape` for each fill |
| "Which losses landed near news?" | `mt5_deals` → `mt5_calendar`, joined on `time_utc` |

The last two are the point of running both servers together — neither system can answer them alone.

## Tool Reference — TradingView (84 MCP tools)

### Chart Reading

| Tool | When to use | Output size |
|------|------------|-------------|
| `chart_get_state` | First call — get symbol, timeframe, all indicator names + IDs | ~500B |
| `data_get_study_values` | Read current RSI, MACD, BB, EMA values from all indicators | ~500B |
| `quote_get` | Get latest price, OHLC, volume | ~200B |
| `data_get_ohlcv` | Get price bars. **Use `summary: true`** for compact stats | 500B (summary) / 8KB (100 bars) |

### Custom Indicator Data (Pine Drawings)

Read `line.new()`, `label.new()`, `table.new()`, `box.new()` output from any visible Pine indicator.

| Tool | When to use | Output size |
|------|------------|-------------|
| `data_get_pine_lines` | Read horizontal price levels (support/resistance, session levels) | ~1-3KB |
| `data_get_pine_labels` | Read text annotations + prices ("PDH 24550", "Bias Long") | ~2-5KB |
| `data_get_pine_tables` | Read data tables (session stats, analytics dashboards) | ~1-4KB |
| `data_get_pine_boxes` | Read price zones / ranges as {high, low} pairs | ~1-2KB |

**Always use `study_filter`** to target a specific indicator: `study_filter: "Profiler"`.

### Chart Control

| Tool | What it does |
|------|-------------|
| `chart_set_symbol` | Change ticker (BTCUSD, AAPL, ES1!, NYMEX:CL1!) |
| `chart_set_timeframe` | Change resolution (1, 5, 15, 60, D, W, M) |
| `chart_set_type` | Change style (Candles, HeikinAshi, Line, Area, Renko) |
| `chart_manage_indicator` | Add/remove indicators. **Use full names**: "Relative Strength Index" not "RSI" |
| `chart_scroll_to_date` | Jump to a date (ISO: "2025-01-15") |
| `chart_set_visible_range` | Zoom to exact range (unix timestamps) |
| `symbol_info` / `symbol_search` | Symbol metadata and search |
| `indicator_set_inputs` / `indicator_toggle_visibility` | Change indicator settings, show/hide |

### Multi-Pane Layouts

| Tool | What it does |
|------|-------------|
| `pane_list` | List all panes with symbols and active state |
| `pane_set_layout` | Change grid: `s`, `2h`, `2v`, `2x2`, `4`, `6`, `8` |
| `pane_focus` | Focus a specific pane by index |
| `pane_set_symbol` | Set symbol on any pane |

### Tab Management

| Tool | What it does |
|------|-------------|
| `tab_list` | List open chart tabs |
| `tab_new` / `tab_close` | Open/close tabs |
| `tab_switch` | Switch to a tab by index |

### Pine Script Development

| Tool | Step |
|------|------|
| `pine_set_source` | 1. Inject code into editor |
| `pine_smart_compile` | 2. Compile with auto-detection + error check |
| `pine_get_errors` | 3. Read compilation errors if any |
| `pine_get_console` | 4. Read log.info() output |
| `pine_save` | 5. Save to TradingView cloud |
| `pine_get_source` | Read current script (**warning: can be 200KB+ for complex scripts**) |
| `pine_new` | Create blank indicator/strategy/library |
| `pine_open` / `pine_list_scripts` | Open or list saved scripts |
| `pine_analyze` | Offline static analysis (no chart needed) |
| `pine_check` | Server-side compile check (no chart needed) |

### Replay Mode

| Tool | Step |
|------|------|
| `replay_start` | Enter replay at a date |
| `replay_step` | Advance one bar |
| `replay_autoplay` | Auto-advance (set speed in ms) |
| `replay_trade` | Buy/sell/close positions |
| `replay_status` | Check position, P&L, date |
| `replay_stop` | Return to realtime |

### Drawing, Alerts, UI Automation

| Tool | What it does |
|------|-------------|
| `draw_shape` | Draw horizontal_line, trend_line, rectangle, text |
| `draw_list` / `draw_remove_one` / `draw_clear` | Manage drawings |
| `alert_create` / `alert_list` / `alert_delete` | Manage price alerts |
| `capture_screenshot` | Screenshot (regions: full, chart, strategy_tester) |
| `batch_run` | Run action across multiple symbols/timeframes |
| `watchlist_get` / `watchlist_add` | Read/modify watchlist |
| `layout_list` / `layout_switch` | Manage saved layouts |
| `ui_open_panel` / `ui_click` / `ui_evaluate` | UI automation |
| `tv_launch` / `tv_health_check` / `tv_discover` | Connection management |

## Tool Reference — MT5 (11 read-only tools)

Served by the separate `mt5` server. Requires `mt5-bridge/bridge.py` running. **None of these can open, modify or close a position.**

| Tool | When to use | Output size |
|------|------------|-------------|
| `mt5_health` | First call — bridge and terminal state, account identity, broker clock offset | ~300 B |
| `mt5_account` | Balance, equity, margin, free margin, leverage | ~300 B |
| `mt5_symbol_search` | Find what your broker calls an instrument. **Names are not guessable** — spot gold is `GOLD.i#` on XM, and `XAUUSD` may not exist | ~1 KB |
| `mt5_quote` | Bid, ask, **mid**, spread. CFDs report no last price or volume, so both are null — use `mid` | ~250 B |
| `mt5_bars` | Broker OHLCV. Summary by default | ~600 B (summary) |
| `mt5_positions` / `mt5_orders` | What is open right now, types decoded | varies |
| `mt5_deals` | Closed fills. **Summary by default** — win rate, net P&L, exit reasons | ~500 B / ~15 KB paged |
| `mt5_analytics` | Expectancy, payoff ratio, drawdown, streaks, and performance by session / exit reason / symbol / hour | ~2-5 KB |
| `mt5_calendar` | Scheduled events with importance, forecast, previous, `actual` | ~2-4 KB |
| `mt5_blackout` | **"Is it safe to act right now"** — one deterministic answer | ~600 B |

Two things worth knowing before building on these:

- **`mt5_deals` only shows trades that were taken.** Skipped setups leave no trace in MT5, so any journal that needs them must log signals separately.
- **`actual` is `null` until an event is released.** That is correct, not missing data — `forecast` and `previous` are known ahead of time.

Setup, routes, and the full timestamp contract: **[mt5-bridge/README.md](mt5-bridge/README.md)**.

## Context Management

Tools return compact output by default to minimize context usage. For a typical "analyze my chart" workflow, total context is ~5-10KB instead of ~80KB.

| Feature | How it saves context |
|---------|---------------------|
| Pine lines | Returns deduplicated price levels only, not every line object |
| Pine labels | Capped at 50 per study, text+price only |
| Pine tables | Pre-formatted row strings, no cell metadata |
| Pine boxes | Deduplicated {high, low} zones only |
| OHLCV summary mode | Stats + last 5 bars instead of all bars |
| Indicator inputs | Encrypted/encoded blobs auto-filtered |
| `verbose: true` | Pass on any pine tool to get raw data with IDs/colors when needed |
| `study_filter` | Target one indicator instead of scanning all |

## Finding TradingView on Your System

Launch scripts and `tv_launch` auto-detect TradingView. If auto-detection fails:

| Platform | Common Locations |
|----------|-----------------|
| **Mac** | `/Applications/TradingView.app/Contents/MacOS/TradingView` |
| **Windows** | `%LOCALAPPDATA%\TradingView\TradingView.exe`, `%PROGRAMFILES%\WindowsApps\TradingView*\TradingView.exe` |
| **Linux** | `/opt/TradingView/tradingview`, `~/.local/share/TradingView/TradingView`, `/snap/tradingview/current/tradingview` |

The key flag: `--remote-debugging-port=9222`

## Testing

```bash
npm run test:unit                       # 190 Node tests, no TradingView needed
python -m unittest discover mt5-bridge  # 111 Python tests, no terminal needed
npm test                                # adds e2e — needs TradingView on port 9222
```

`test:unit` covers Pine Script static analysis, server-side compilation, CLI routing, chart-readiness detection, and the MT5 bridge client. The Python suite covers the bridge's pure logic — timeframe resolution, bar and deal summaries, calendar filtering, news blackout windows, broker-clock conversion — plus every route against a faked MetaTrader 5 module.

Both run in CI on Node 20 and 22. Neither needs TradingView, a broker terminal, or a network.

## Architecture

```
Claude Code ─┬─ MCP "tradingview" (stdio) ─→ CDP :9222 ─→ TradingView Desktop (Electron)
             │
             └─ MCP "mt5"        (stdio) ─→ HTTP :8765 ─→ bridge.py ─→ MetaTrader 5 terminal
                                                              ↑
                                          calendar_export.mq5 ┘ (writes MQL5/Files/*.json)
```

- **Transport**: MCP over stdio — 84 TradingView tools + 10 MT5 tools — plus a `tv` CLI (30 commands, 66 subcommands)
- **Connections**: Chrome DevTools Protocol on localhost:9222; read-only HTTP bridge on localhost:8765
- **Streaming**: Poll-and-diff loop with deduplication, JSONL output to stdout
- **No dependencies** beyond `@modelcontextprotocol/sdk` and `chrome-remote-interface` on the Node side, and `MetaTrader5` on the Python side

### Why MT5 needs a Python bridge

MetaTrader 5 has no Node binding — the official package is Python and Windows-only. And its economic calendar is reachable only from MQL5 (`CalendarValueHistory()`), not from the Python package, so a terminal-side script exports it to JSON that the bridge reads back.

The bridge is **read-only by construction**: `order_send` and friends are simply absent, only `GET` is accepted, and no route maps to an order operation. Execution, if ever added, belongs in a separate process.

### Timestamps

MetaTrader 5 reports times against the **broker clock**, not UTC. Every MT5 timestamp is labelled twice — `time_server` (no `Z`, because it is not UTC) and `time_utc` — and consumers join on `time_utc`. Where the offset cannot be established, `time_utc` is `null` rather than a guess, and the blackout check refuses to answer rather than comparing mismatched clocks.

## Attributions

This project is not affiliated with, endorsed by, or associated with:
- **TradingView Inc.** — TradingView is a trademark of TradingView Inc.
- **MetaQuotes Software Corp.** — MetaTrader and MetaTrader 5 are trademarks of MetaQuotes Software Corp.
- **Any broker** — broker names and symbols appearing in documentation are examples only.
- **Anthropic** — Claude and Claude Code are trademarks of Anthropic, PBC.

This tool is an independent MCP server that connects to Claude Code via the standard MCP protocol. It does not contain or modify any Anthropic software.

The TradingView portion originates from [tradesdontlie/tradingview-mcp](https://github.com/tradesdontlie/tradingview-mcp), MIT licensed. The `mt5-bridge/` subsystem and `mt5` MCP server are additions in this fork.

## Disclaimer

This project is provided **for personal, educational, and research purposes only**.

**How this tool works:** This tool uses Chrome DevTools Protocol (CDP), the standard debugging interface built into Chromium-based applications. It does not reverse engineer any proprietary TradingView protocol, connect to TradingView's servers, or bypass any access controls. The debug port must be explicitly enabled by the user via a standard Chromium command-line flag (`--remote-debugging-port=9222`).

By using this software, you acknowledge and agree that:

1. **You are solely responsible** for ensuring your use of this tool complies with [TradingView's Terms of Use](https://www.tradingview.com/policies/) and all applicable laws.
2. TradingView's Terms of Use **restrict automated data collection, scraping, and non-display usage** of their platform and data. This tool uses Chrome DevTools Protocol to programmatically interact with the TradingView Desktop app, which may conflict with those terms.
3. **You assume all risk** associated with using this tool. The authors are not responsible for any account bans, suspensions, legal actions, or other consequences resulting from its use.
4. This tool **must not be used** for, including but not limited to:
   - Redistributing, reselling, or commercially exploiting TradingView's market data
   - Circumventing TradingView's access controls or subscription restrictions
   - Performing automated trading or algorithmic decision-making using extracted data
   - Violating the intellectual property rights of Pine Script indicator authors
   - Connecting to TradingView's servers or infrastructure (all access is via the locally running Desktop app)
5. The streaming functionality monitors your locally running TradingView Desktop instance only. It does not connect to TradingView's servers or extract data from TradingView's infrastructure.
6. Market data accessed through this tool remains subject to exchange and data provider licensing terms. **Do not redistribute, store, or commercially exploit any data obtained through this tool.**
7. This tool accesses internal, undocumented TradingView application interfaces that may change or break at any time without notice.

**Use at your own risk.** If you are unsure whether your intended use complies with TradingView's terms, do not use this tool.

### Additionally, for the MT5 side

8. The MT5 bridge is **read-only**. It cannot open, modify or close a position, and contains no order-placement code. Nothing in this repository trades your account.
9. **You are solely responsible** for ensuring your use complies with your broker's terms and with the financial regulations that apply where you live. Retail leveraged forex and CFD trading is restricted or prohibited in some jurisdictions.
10. Account data, trade history and market data read through the bridge are **your own**, obtained from a terminal you are already logged into. They remain subject to your broker's terms — do not redistribute them.
11. **Nothing here is financial advice.** Summaries such as win rate, net P&L or news-blackout windows are arithmetic over your own data, not a recommendation to act. Historical performance does not predict future results.
12. If you extend this project toward automated execution, note that point 4 above already excludes automated trading on TradingView-extracted data. Deriving signals from your broker's own feed, and using TradingView only for visualization, avoids that conflict.

## License

MIT — see [LICENSE](LICENSE) for details.

The MIT license applies to the source code of this project only. It does not grant any rights to TradingView's software, data, trademarks, or intellectual property.
