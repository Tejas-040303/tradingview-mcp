#!/usr/bin/env node
/**
 * MCP server for a MetaTrader 5 terminal, read-only.
 *
 * Separate from src/server.js on purpose. That one drives TradingView Desktop
 * over CDP; this one reads a broker terminal through the local Python bridge.
 * Different dependencies, different failure modes — closing TradingView must
 * not take the broker tools down with it, and vice versa.
 *
 * Requires mt5-bridge/bridge.py to be running. See mt5-bridge/README.md.
 */
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { registerMt5Tools } from './tools/mt5.js';

const server = new McpServer(
  {
    name: 'mt5',
    version: '1.0.0',
    description: 'Read-only MetaTrader 5 account, market and economic calendar data',
  },
  {
    instructions: `MT5 — 10 read-only tools for a MetaTrader 5 terminal via the local bridge.

READ-ONLY. There is no tool here that can open, modify or close a position.

TOOL SELECTION GUIDE:

Connection and account:
- mt5_health → bridge + terminal state, account identity, broker clock offset. Call first if anything fails.
- mt5_account → balance, equity, margin, leverage

Finding an instrument:
- mt5_symbol_search → broker names are not guessable. Spot gold is "GOLD.i#" on XM; "XAUUSD" may not exist at all. Search before assuming.

Market data:
- mt5_quote → bid, ask, mid, spread. CFDs report last and volume as null; use mid.
- mt5_bars → OHLCV. Summary by default; pass summary=false only when you need individual bars.

Positions and history:
- mt5_positions / mt5_orders → what is open right now
- mt5_deals → closed fills. Summary by default (win rate, net P&L, exit reasons).
  Only shows trades that were TAKEN — skipped setups leave no trace here.

News:
- mt5_blackout → "is it safe to act right now", one deterministic answer. Check before acting on a signal.
- mt5_calendar → scheduled events with importance, forecast, previous, and actual once released.
  actual is null until an event happens; that is correct, not missing data.

TIMESTAMPS — read before joining data:
MetaTrader 5 reports times against the BROKER clock, not UTC. Every timestamp comes
back labelled twice: time_server / time_server_iso (no Z, because it is not UTC) and
time_utc / time_utc_iso. ALWAYS join on time_utc — the calendar is UTC, and mixing the
two misaligns everything by the broker offset while looking perfectly reasonable.
If the offset is unknown, time_utc is null rather than a guess.

CONTEXT RULES:
- Keep summary=true on mt5_deals and mt5_bars unless individual rows are needed
- Filter mt5_calendar by currency and importance; unfiltered runs to hundreds of rows
- mt5_health is cheap — prefer it over guessing why something failed

If a tool reports the bridge is unreachable, the bridge process is not running:
  python mt5-bridge/bridge.py`,
  },
);

registerMt5Tools(server);

process.stderr.write('⚠  mt5-mcp  |  Read-only. No order placement. Requires mt5-bridge/bridge.py running.\n');
process.stderr.write('   Not affiliated with MetaQuotes Software Corp. or any broker.\n\n');

const transport = new StdioServerTransport();
await server.connect(transport);
