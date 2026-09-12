import { z } from 'zod';
import { jsonResult } from './_format.js';
import * as core from '../core/capture.js';
import * as tradeCapture from '../core/tradecapture.js';

export function registerCaptureTools(server) {
  server.tool('capture_screenshot', 'Take a screenshot of the TradingView chart', {
    region: z.string().optional().describe('Region to capture: full, chart, strategy_tester (default full)'),
    filename: z.string().optional().describe('Custom filename (without extension)'),
    method: z.string().optional().describe('Capture method: cdp (Page.captureScreenshot) or api (chartWidgetCollection.takeScreenshot) (default cdp)'),
    wait_for_render: z.boolean().optional().describe('Wait for the chart canvas to stabilize before capturing. Use after chart_set_symbol or chart_set_timeframe to avoid stale frames.'),
  }, async ({ region, filename, method, wait_for_render }) => {
    try { return jsonResult(await core.captureScreenshot({ region, filename, method, waitForRender: wait_for_render })); }
    catch (err) { return jsonResult({ success: false, error: err.message }, true); }
  });

  server.tool('capture_trade',
    'Screenshot a real MT5 trade marked up on the chart, bound to its position_id. ' +
    'kind="entry" (default) shows only what was knowable at entry — the frame ends at the entry bar, ' +
    'and no exit, P&L or outcome-derived level is drawn, so it can be used to review the entry decision. ' +
    'kind="review" shows the whole trade including the exit. ' +
    'Needs the MT5 bridge (8765); files the image with the journal (8766) if it is running.',
    {
      position_id: z.string().optional().describe('MT5 position id (from mt5_trades). Omit for the most recently closed trade.'),
      kind: z.enum(['entry', 'review']).optional().describe('entry = nothing after the entry is visible (default); review = the whole trade'),
      symbol: z.string().optional().describe('TradingView symbol, e.g. "OANDA:XAUUSD". Omit to map the broker symbol through the symbol table.'),
      timeframe: z.string().optional().describe('Chart resolution (1, 5, 15, 60, 240, D, W). Entry captures default to 5 — deriving it from the trade duration would leak the outcome.'),
      days: z.number().optional().describe('How far back to look for the trade (default 30)'),
      stop: z.number().optional().describe('Stop price known at entry. Entry captures draw a stop only from a level supplied here — one inferred from the exit fill would only exist for losing trades.'),
      target: z.number().optional().describe('Target price known at entry, drawn on the same terms as stop'),
      bars_before: z.number().optional().describe('Bars of context before the entry (default 120 for entry, 40 for review)'),
      bars_after: z.number().optional().describe('Bars after the exit, review captures only (default 20)'),
      include_entry_bar: z.boolean().optional().describe('Keep the entry bar as the last candle (default true). false ends one bar earlier, so every candle shown had closed before the entry.'),
      attach: z.boolean().optional().describe('Record the image in the journal against position_id (default true)'),
      keep_drawings: z.boolean().optional().describe('Leave the markup on the chart after the screenshot (default false)'),
    }, async (args) => {
      try { return jsonResult(await tradeCapture.captureTrade(args)); }
      catch (err) { return jsonResult({ success: false, error: err.message }, true); }
    });
}
