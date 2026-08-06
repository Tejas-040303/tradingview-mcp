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

/**
 * Strategy configuration, accepted identically by all five strategy tools.
 *
 * Every field is optional and falls back to the bridge's default strategy, so
 * one knob can be varied at a time. `trail` takes "none" as well as a number
 * because switching the breakeven trail off is the control case for any
 * comparison against it — a parameter that cannot express "off" quietly
 * removes the comparison.
 */
const STRATEGY_PARAMS = {
  symbol: z.string().describe('Broker symbol, e.g. "GOLD.i#" (find it with mt5_symbol_search)'),
  timeframe: z.string().optional().describe('Bar timeframe: 1, 5, 15, 30, 60, 240, D, W (default 5)'),
  count: z.coerce.number().optional().describe('Bars to scan, max 5000 (default 1000)'),
  balance: z.coerce.number().optional().describe('Account balance to size positions against (default 1000)'),
  conditions: z.string().optional().describe('Comma-separated entry conditions to enable: fvg, liquidity_sweep, order_block, fib. Naming any switches the others OFF'),
  required: z.string().optional().describe('Comma-separated conditions that MUST be present regardless of mode — expresses "sweep is mandatory, FVG is a bonus"'),
  mode: z.string().optional().describe('any (each level is its own setup), all (confluence), or at_least (default any)'),
  min_conditions: z.coerce.number().optional().describe('How many conditions must fire in at_least mode'),
  confirmation: z.string().optional().describe('close_beyond, engulfing or rejection (default close_beyond)'),
  buffer_pips: z.coerce.number().optional().describe('Stop distance beyond the confirmation candle, in pips (default 7)'),
  risk_pct: z.coerce.number().optional().describe('Percent of balance risked per trade (default 1.0)'),
  max_risk_pct: z.coerce.number().optional().describe('Refuse a trade whose minimum lot would risk more than this (default 2.0)'),
  target_r: z.coerce.number().optional().describe('Target as a multiple of the stop distance (default 2.0)'),
  partial_pct: z.coerce.number().optional().describe('Percent of the position closed at partial_at_r (default 50). A minimum-lot position cannot be split and is carried to the target instead'),
  partial_at_r: z.coerce.number().optional().describe('R multiple at which the partial is taken (default 1.0)'),
  trail: z.string().optional().describe('R multiple at which the stop moves to breakeven, or "none" to disable it (default 1.0). "none" is the control case for testing whether trailing helps'),
};

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
    'mt5_diagnose_stops',
    'Answers whether the stop-loss distance can be recovered from order history. Deal history carries no SL, which is why risk %, R-multiples and average RR are unavailable; order history does carry it, but whether the field is populated depends on the broker and on how the stop was attached. Returns coverage over sampled entry orders, coverage over closed trades, the median risk distance, and a plain verdict on whether R-multiple analysis is viable without manual journalling. Defaults to a one-year window.',
    {
      from: z.coerce.number().optional().describe('Window start, unix seconds UTC (default one year ago)'),
      to: z.coerce.number().optional().describe('Window end, unix seconds UTC (default now)'),
      symbol: z.string().optional().describe('Narrow to one symbol'),
      sample: z.coerce.number().optional().describe('How many recent entry orders to sample (default 200)'),
    },
    guard(({ from, to, symbol, sample }) => core.diagnoseStops({ from, to, symbol, sample })),
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
  // ---------------------------------------------------------------------
  // Strategy engine. Read-only like everything above: these describe what a
  // strategy would do, and none of them can place an order.
  //
  // The knobs repeat across all five because a comparison is only meaningful
  // when both runs are configured the same way, and threading one shared
  // object through would hide which are actually accepted.
  // ---------------------------------------------------------------------

  server.tool(
    'mt5_setups',
    'Entry signals the strategy detects, with NO profit-and-loss attached — timestamps, direction, the levels that fired and which conditions matched. This is the checkpoint before any backtest number is worth quoting: pull a few up on a chart and judge whether they are setups worth taking. If the detectors find the wrong things, no amount of parameter tuning fixes that.',
    { ...STRATEGY_PARAMS,
      limit: z.coerce.number().optional().describe('Rows per page (default 50)'),
      offset: z.coerce.number().optional().describe('Page offset (default 0)'),
    },
    guard((args) => core.setups(args)),
  );

  server.tool(
    'mt5_backtest',
    'Replay the detected signals with stops, targets, partial closes and the breakeven trail, returning expectancy, win rate, average R and where the trades exited. Three rules keep it honest: entry fills at the NEXT bar open rather than the confirmation close, a bar containing both stop and target resolves as the STOP since bar data cannot order two intrabar touches, and sizing goes through the same position sizer as everything else so an unaffordable setup is refused with a reason rather than taken at a fractional lot.',
    { ...STRATEGY_PARAMS,
      trades: z.coerce.boolean().optional().describe('Include every trade, not just the summary (default false — it is long)'),
      skipped: z.coerce.boolean().optional().describe('Include the signals that were not traded, with reasons (default false)'),
      compound: z.coerce.boolean().optional().describe('Size off the running balance instead of the starting one (default false — it makes two parameter sets incomparable)'),
    },
    guard((args) => core.backtest(args)),
  );

  server.tool(
    'mt5_sweep',
    'Run every parameter combination across a chronological split — the first 70% of bars chooses, the last 30% judges — and report what the result is actually entitled to claim. Returns the best configuration alongside the MEDIAN one, the rank correlation between the two halves, and the in-sample-to-out-of-sample gap that measures overfitting. It refuses to call a result trustworthy unless the winner is profitable out of sample AND the ordering survives the split AND the winner is clear of the median. Slow: the default grid is thirty configurations over the whole window.',
    { ...STRATEGY_PARAMS,
      axes: z.string().optional().describe('Grid to sweep, e.g. "manage.trail_to_be_at_r:0.5,1.0,none|target.r:2,3". Omit for the default grid of trail x partial x target'),
      split: z.coerce.number().optional().describe('Fraction of bars used to choose rather than judge (default 0.7)'),
      min_trades: z.coerce.number().optional().describe('Closed trades needed in BOTH halves before a configuration is ranked (default 10)'),
    },
    guard((args) => core.sweep(args)),
  );

  server.tool(
    'mt5_paper',
    'What the strategy would be doing right now: the open paper position with its stop, target and lot, any confirmation still waiting on an entry bar, and recent closed trades. Reconstructed from bars on every call rather than kept in a state file, so a restart changes nothing and two calls agree. The bar still forming is always dropped — MT5 returns the current incomplete candle looking exactly like a finished one, and acting on it is the live equivalent of lookahead.',
    { ...STRATEGY_PARAMS,
      recent: z.coerce.number().optional().describe('How many recent closed trades to include (default 5)'),
    },
    guard((args) => core.paper(args)),
  );

  server.tool(
    'mt5_reconcile',
    'Match the strategy\'s signals against what the account actually did, in three buckets: followed, missed (signalled but not traded) and discretionary (traded with no signal behind it). MT5 records only trades that were TAKEN, so this is the only way to see the setups that were passed on. Compares taken-versus-skipped simulated-to-simulated, since simulated fills assume no spread or slippage and real ones do not; the gap between a followed signal\'s simulated and real result is reported separately as the execution cost. No finding is claimed unless both sides clear ten trades.',
    { ...STRATEGY_PARAMS,
      tolerance: z.coerce.number().optional().describe('Seconds between a signal and a fill for them to count as the same trade (default 900)'),
      detail: z.coerce.boolean().optional().describe('Include the individual matched, missed and discretionary rows (default false)'),
    },
    guard((args) => core.reconcile(args)),
  );
}
