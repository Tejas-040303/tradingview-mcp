import { z } from 'zod';
import { jsonResult } from './_format.js';
import * as core from '../core/mt5.js';

/**
 * MCP tools for the read-only MT5 bridge.
 *
 * Registered on their own server (src/mt5-server.js), not alongside the
 * TradingView tools: those need TradingView Desktop on CDP, these need the
 * Python bridge, and neither should fail because the other is closed.
 *
 * Defaults here are more opinionated than the HTTP routes they call. The
 * bridge is a data API and stays unfiltered; a tool that hands back a thousand
 * calendar rows or four hundred deals burns the context window, so summaries
 * lead and detail is opt-in.
 */
function guard(fn) {
  return async (args) => {
    try {
      return jsonResult(await fn(args));
    } catch (err) {
      return jsonResult({ success: false, error: err.message }, true);
    }
  };
}

export function registerMt5Tools(server) {
  server.tool(
    'mt5_health',
    'Check the MT5 bridge and terminal connection. Returns account identity and the broker clock offset from UTC. Call this first if anything else fails.',
    {},
    guard(() => core.health()),
  );

  server.tool(
    'mt5_account',
    'Account balance, equity, margin, free margin, leverage and currency.',
    {},
    guard(() => core.account()),
  );

  server.tool(
    'mt5_symbol_search',
    'Find what this broker calls an instrument, searching name and description. Broker naming is not guessable — spot gold is "GOLD.i#" on XM and "XAUUSD" elsewhere. Use this before any symbol-taking tool.',
    {
      search: z.string().describe('Substring to match, e.g. "gold", "eur", "oil"'),
      limit: z.coerce.number().optional().describe('Max results (default 50)'),
    },
    guard(({ search, limit }) => core.symbolSearch({ search, limit })),
  );

  server.tool(
    'mt5_quote',
    'Current bid, ask, mid and spread for a symbol. CFDs report no last-trade price or traded volume, so both come back null — use mid.',
    {
      symbol: z.string().describe('Broker symbol, e.g. "GOLD.i#" (find it with mt5_symbol_search)'),
    },
    guard(({ symbol }) => core.quote({ symbol })),
  );

  server.tool(
    'mt5_bars',
    'OHLCV bars from the broker feed. Returns a compact summary by default; pass summary=false for individual bars.',
    {
      symbol: z.string().describe('Broker symbol, e.g. "GOLD.i#"'),
      timeframe: z.string().optional().describe('1, 5, 15, 30, 60, 240, D, W, M (default 5)'),
      count: z.coerce.number().optional().describe('Bars to fetch, max 5000 (default 100)'),
      summary: z.coerce.boolean().optional().describe('Compact stats instead of every bar (default true)'),
    },
    guard(({ symbol, timeframe, count, summary }) =>
      core.bars({ symbol, timeframe, count, summary: summary !== false })),
  );

  server.tool(
    'mt5_positions',
    'Currently open positions, with type decoded to buy/sell.',
    {
      symbol: z.string().optional().describe('Narrow to one symbol'),
    },
    guard(({ symbol }) => core.positions({ symbol })),
  );

  server.tool(
    'mt5_orders',
    'Pending orders, with type decoded (buy_limit, sell_stop, and so on).',
    {
      symbol: z.string().optional().describe('Narrow to one symbol'),
    },
    guard(({ symbol }) => core.orders({ symbol })),
  );

  server.tool(
    'mt5_deals',
    'Closed deal history — the fill record a trade journal is built from. Returns a summary by default (win rate, net P&L, exit reasons); pass summary=false with limit/offset to page through individual deals. Note this only shows trades that were TAKEN; setups you skipped leave no trace here.',
    {
      from: z.coerce.number().optional().describe('Window start, unix seconds UTC (default 30 days ago)'),
      to: z.coerce.number().optional().describe('Window end, unix seconds UTC (default now)'),
      symbol: z.string().optional().describe('Narrow to one symbol'),
      summary: z.coerce.boolean().optional().describe('Summary only, no individual deals (default true)'),
      limit: z.coerce.number().optional().describe('Deals per page when summary=false (default 50)'),
      offset: z.coerce.number().optional().describe('Page offset when summary=false (default 0)'),
    },
    guard(({ from, to, symbol, summary, limit, offset }) =>
      core.deals({ from, to, symbol, summary: summary !== false, limit, offset })),
  );

  server.tool(
    'mt5_trades',
    'Fills paired into actual trades by position_id — entry and exit price, duration, partial closes, exit reason. MT5 reports deals, not trades, so this is the only way to see how long a position was held. The summary includes average hold time for winners versus losers. Returns a summary by default; pass summary=false with limit/offset to page through individual trades.',
    {
      from: z.coerce.number().optional().describe('Window start, unix seconds UTC (default 30 days ago)'),
      to: z.coerce.number().optional().describe('Window end, unix seconds UTC (default now)'),
      symbol: z.string().optional().describe('Narrow to one symbol'),
      summary: z.coerce.boolean().optional().describe('Summary only (default true)'),
      closed_only: z.coerce.boolean().optional().describe('Exclude positions still open (default false)'),
      limit: z.coerce.number().optional().describe('Trades per page when summary=false (default 50)'),
      offset: z.coerce.number().optional().describe('Page offset when summary=false (default 0)'),
    },
    guard(({ from, to, symbol, summary, closed_only, limit, offset }) =>
      core.trades({ from, to, symbol, summary: summary !== false,
        closed_only: closed_only === true, limit, offset })),
  );

  server.tool(
    'mt5_excursions',
    'What price did during each trade and after you left it: maximum adverse and favourable excursion, how much of the available move you captured, and — for losing exits only — whether price came back to your entry within 5, 15 or 60 minutes. That last figure is the direct test of "my stop sat inside normal noise" versus "my entries were wrong", which the exit distribution alone cannot separate. Needs bar history, so it is slower than the other tools; returns a summary by default.',
    {
      from: z.coerce.number().optional().describe('Window start, unix seconds UTC (default 30 days ago)'),
      to: z.coerce.number().optional().describe('Window end, unix seconds UTC (default now)'),
      symbol: z.string().optional().describe('Narrow to one symbol'),
      timeframe: z.string().optional().describe('Bar timeframe for the analysis (default "1" — one minute)'),
      summary: z.coerce.boolean().optional().describe('Summary only, no per-trade rows (default true)'),
    },
    guard(({ from, to, symbol, timeframe, summary }) =>
      core.excursions({ from, to, symbol, timeframe, summary: summary !== false })),
  );

  server.tool(
    'mt5_analytics',
    'Deep analysis of closed trade history: expectancy, payoff ratio, win/loss streaks, max drawdown, and performance broken down by exit reason, session, symbol, weekday or hour. Answers "when do I lose money" rather than just "how much". Pass starting_balance to get drawdown as a percentage.',
    {
      from: z.coerce.number().optional().describe('Window start, unix seconds UTC (default 30 days ago)'),
      to: z.coerce.number().optional().describe('Window end, unix seconds UTC (default now)'),
      symbol: z.string().optional().describe('Narrow to one symbol'),
      starting_balance: z.coerce.number().optional().describe('Account balance at the window start — required for drawdown percentages'),
      group_by: z.string().optional().describe('Comma-separated: reason, session, symbol, weekday, hour, type (default reason,session,symbol)'),
      curve: z.coerce.boolean().optional().describe('Include the full equity curve, one point per trade (default false — it is long)'),
    },
    guard(({ from, to, symbol, starting_balance, group_by, curve }) =>
      core.analytics({ from, to, symbol, starting_balance, group_by, curve: curve === true })),
  );

  server.tool(
    'mt5_calendar',
    'Scheduled economic events from the terminal calendar, with importance, forecast, previous and — for events already released — actual. Defaults to high-importance only; widen deliberately, since an unfiltered calendar runs to hundreds of rows.',
    {
      currencies: z.string().optional().describe('Comma-separated, e.g. "USD" or "USD,EUR"'),
      min_importance: z.string().optional().describe('none, low, moderate, high (default high)'),
      from: z.coerce.number().optional().describe('Window start, unix seconds UTC'),
      to: z.coerce.number().optional().describe('Window end, unix seconds UTC'),
      limit: z.coerce.number().optional().describe('Max events (default 100)'),
    },
    guard(({ currencies, min_importance, from, to, limit }) =>
      core.calendar({ currencies, min_importance, from, to, limit })),
  );

  server.tool(
    'mt5_blackout',
    'Whether now falls inside a news blackout window — one deterministic answer to "is it safe to act right now". Returns the events causing it and the next one due. Check this before acting on any signal.',
    {
      currencies: z.string().optional().describe('Comma-separated (default USD)'),
      before_min: z.coerce.number().optional().describe('Minutes before a release to block (default 15)'),
      after_min: z.coerce.number().optional().describe('Minutes after a release to block (default 15)'),
      min_importance: z.string().optional().describe('Importance floor (default high)'),
    },
    guard(({ currencies, before_min, after_min, min_importance }) =>
      core.blackout({
        currencies: currencies || 'USD',
        before_min, after_min, min_importance,
      })),
  );
}
