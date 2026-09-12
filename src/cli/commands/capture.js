import { register } from '../router.js';
import * as core from '../../core/capture.js';
import * as tradeCapture from '../../core/tradecapture.js';

register('screenshot', {
  description: 'Take a screenshot of the chart',
  options: {
    region: { type: 'string', short: 'r', description: 'Region: full, chart, strategy_tester' },
    output: { type: 'string', short: 'o', description: 'Custom filename (without .png)' },
  },
  handler: (opts) => core.captureScreenshot({
    region: opts.region,
    filename: opts.output,
  }),
});

register('capture-trade', {
  description: 'Screenshot a real MT5 trade marked up on the chart, bound to its position_id',
  options: {
    position: { type: 'string', short: 'p', description: 'MT5 position id (default: the most recently closed trade)' },
    kind: { type: 'string', short: 'k', description: 'entry (nothing after the entry is shown, default) or review' },
    symbol: { type: 'string', short: 's', description: 'TradingView symbol, e.g. OANDA:XAUUSD (default: map the broker symbol)' },
    timeframe: { type: 'string', short: 't', description: 'Chart resolution (default 5 for entry captures)' },
    days: { type: 'string', short: 'd', description: 'How far back to look for the trade (default 30)' },
    stop: { type: 'string', description: 'Stop price known at entry — the only stop an entry capture will draw' },
    target: { type: 'string', description: 'Target price known at entry' },
    'bars-before': { type: 'string', description: 'Bars of context before the entry' },
    'bars-after': { type: 'string', description: 'Bars after the exit (review only)' },
    'no-entry-bar': { type: 'boolean', description: 'End one bar earlier, so every candle shown had closed before the entry' },
    'no-attach': { type: 'boolean', description: 'Do not record the image in the journal' },
    keep: { type: 'boolean', description: 'Leave the markup on the chart afterwards' },
  },
  handler: (opts) => tradeCapture.captureTrade({
    position_id: opts.position,
    kind: opts.kind,
    symbol: opts.symbol,
    timeframe: opts.timeframe,
    days: opts.days ? Number(opts.days) : undefined,
    stop: opts.stop ? Number(opts.stop) : undefined,
    target: opts.target ? Number(opts.target) : undefined,
    bars_before: opts['bars-before'] ? Number(opts['bars-before']) : undefined,
    bars_after: opts['bars-after'] ? Number(opts['bars-after']) : undefined,
    include_entry_bar: !opts['no-entry-bar'],
    attach: !opts['no-attach'],
    keep_drawings: !!opts.keep,
  }),
});
