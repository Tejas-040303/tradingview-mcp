/**
 * Unit tests for trade chart capture.
 *
 * No TradingView, no bridge, no journal, no network: the chart, drawing,
 * screenshot, bridge and journal modules are all injected.
 *
 * Most of these exist to pin one property — **an entry capture contains
 * nothing that was not knowable at the entry**. That is the whole reason the
 * planner is a pure function returning a value: "the screenshot looked right"
 * is not something a test can assert, and the four ways the future leaks into
 * a frame are each subtle enough to be reintroduced by a reasonable-looking
 * change.
 */
import { test, describe } from 'node:test';
import assert from 'node:assert';
import * as plan from '../src/core/tradecapture.js';
import * as symbolmap from '../src/core/symbolmap.js';

/** A losing trade: stopped out an hour after entry. */
const TRADE = {
  position_id: 998877,
  symbol: 'GOLD.i#',
  direction: 'long',
  opened_utc: 1_700_000_000,
  closed_utc: 1_700_003_600,
  duration_sec: 3600,
  entry_price: 1950.25,
  exit_price: 1944.75,
  volume: 0.1,
  net: -41.25,
  exit_reason: 'stop_loss',
  deals: 2,
  partial_closes: 0,
  open: false,
  entry_missing: false,
};

/** The 5-minute bar containing the entry. */
const ENTRY_BAR = 1_699_999_800;

function shapeRoles(p) {
  return p.shapes.map(s => s.role);
}

function everyShapeTime(p) {
  const times = [];
  for (const shape of p.shapes) {
    times.push(shape.point.time);
    if (shape.point2) times.push(shape.point2.time);
  }
  return times;
}

describe('symbol mapping — refuses to guess', () => {
  test('maps a decorated broker symbol through its root', () => {
    const result = symbolmap.mapSymbol('GOLD.i#');
    assert.equal(result.success, true);
    assert.equal(result.root, 'GOLD');
    assert.equal(result.symbol, 'OANDA:XAUUSD');
  });

  test('strips a micro-account suffix before uppercasing destroys the signal', () => {
    assert.equal(symbolmap.brokerRoot('EURUSDm'), 'EURUSD');
    assert.equal(symbolmap.mapSymbol('EURUSDm').symbol, 'OANDA:EURUSD');
    // A root that genuinely ends in those letters is left alone.
    assert.equal(symbolmap.brokerRoot('EURUSD'), 'EURUSD');
  });

  test('stacked broker decorations are stripped', () => {
    assert.equal(symbolmap.brokerRoot('US30.cash'), 'US30');
    assert.equal(symbolmap.brokerRoot('XAUUSD_ecn'), 'XAUUSD');
    assert.equal(symbolmap.brokerRoot('#EURUSD.pro'), 'EURUSD');
  });

  test('an unmapped symbol fails with somewhere to put the answer', () => {
    const result = symbolmap.mapSymbol('WIDGET.x#');
    // The alternative — a near-enough guess — is a plausible chart of the
    // wrong instrument filed against a real position.
    assert.equal(result.success, false);
    assert.equal(result.symbol, null);
    assert.match(result.reason, /WIDGET\.x#/);
    assert.match(result.hint, /symbol-map\.json/);
  });

  test('overrides win, and the exact broker name beats the stripped root', () => {
    const overrides = { GOLD: 'FX:XAUUSD', 'GOLD.i#': 'OANDA:XAUUSD' };
    assert.equal(symbolmap.mapSymbol('GOLD.i#', { overrides }).via, 'override:exact');
    assert.equal(symbolmap.mapSymbol('GOLD.i#', { overrides }).symbol, 'OANDA:XAUUSD');
    assert.equal(symbolmap.mapSymbol('GOLD.pro', { overrides }).symbol, 'FX:XAUUSD');
  });

  test('a malformed override file is an error, not a silent fallback', () => {
    // Falling back to the built-in table would chart whatever it happens to
    // say — which is exactly what the file exists to correct.
    assert.throws(() => symbolmap.loadOverrides({
      env: { TV_SYMBOL_MAP: '/tmp/map.json' },
      exists: () => true,
      readFile: () => '{ not json',
    }), /not valid JSON/);
  });

  test('no override file at all is fine', () => {
    const result = symbolmap.loadOverrides({ env: {}, exists: () => false });
    assert.deepEqual(result, { path: null, overrides: {} });
  });
});

describe('timeframe arithmetic', () => {
  test('a bar is identified by its open time', () => {
    assert.equal(plan.barOpen(TRADE.opened_utc, 300), ENTRY_BAR);
    assert.equal(plan.barOpen(ENTRY_BAR, 300), ENTRY_BAR);
  });

  test('an unknown resolution is refused by name', () => {
    assert.throws(() => plan.timeframeSeconds('7'), /Unknown timeframe "7"/);
  });

  test('a review timeframe keeps the trade at a readable size', () => {
    assert.equal(plan.chooseTimeframe(15 * 60), '1');
    assert.equal(plan.chooseTimeframe(4 * 3600), '5');
    assert.equal(plan.chooseTimeframe(30 * 86400), 'D');
  });
});

describe('entry capture — nothing after the entry', () => {
  test('the frame ends at the entry bar', () => {
    const p = plan.planCapture({ trade: TRADE, kind: 'entry', symbol: 'OANDA:XAUUSD' });
    assert.equal(p.range.to, ENTRY_BAR);
    assert.ok(p.range.from < p.range.to);
    assert.equal(p.range.from, ENTRY_BAR - 120 * 300);
  });

  test('no shape reaches past the right edge', () => {
    const p = plan.planCapture({
      trade: TRADE, kind: 'entry', symbol: 'OANDA:XAUUSD', stop: 1944.75, target: 1970,
    });
    for (const time of everyShapeTime(p)) {
      assert.ok(time <= p.range.to, `a shape sits at ${time}, past the edge at ${p.range.to}`);
    }
  });

  test('the exit is not drawn, whatever the trade did', () => {
    const winner = { ...TRADE, exit_reason: 'take_profit', exit_price: 1990.5, net: 402.5 };
    const p = plan.planCapture({ trade: winner, kind: 'entry', symbol: 'OANDA:XAUUSD' });
    assert.equal(shapeRoles(p).some(r => r.startsWith('exit')), false);
    assert.equal(p.levels.exit, null);
  });

  test('the label carries no outcome either', () => {
    const winner = { ...TRADE, exit_reason: 'take_profit', exit_price: 1990.5, net: 402.5 };
    const p = plan.planCapture({ trade: winner, kind: 'entry', symbol: 'OANDA:XAUUSD' });
    const label = p.shapes.find(s => s.role === 'label').text;
    // The frame is useless for reviewing the decision if the answer is written
    // across it.
    assert.equal(label.includes('1990.5'), false);
    assert.equal(label.includes('402.5'), false);
    assert.equal(label.includes('take_profit'), false);
    assert.match(label, /nothing after the entry/);
  });

  test('the timeframe is not derived from how long the trade lasted', () => {
    // Duration is an outcome. A two-minute trade and a two-week trade must
    // produce the same entry chart, or the reader knows how it went before
    // looking at a candle.
    const quick = plan.planCapture({ trade: { ...TRADE, duration_sec: 120 }, kind: 'entry' });
    const slow = plan.planCapture({ trade: { ...TRADE, duration_sec: 14 * 86400 }, kind: 'entry' });
    assert.equal(quick.timeframe, slow.timeframe);
    assert.equal(quick.timeframe, plan.ENTRY_TIMEFRAME);
    assert.equal(quick.timeframe_source, 'fixed_default');

    // A review is free to, and does.
    const review = plan.planCapture({ trade: { ...TRADE, duration_sec: 14 * 86400 }, kind: 'review' });
    assert.equal(review.timeframe_source, 'derived_from_duration');
    assert.notEqual(review.timeframe, plan.ENTRY_TIMEFRAME);
  });

  test('a stop is never read back from the exit fill', () => {
    // TRADE was stopped out, so its exit price *is* its stop — a sound
    // inference that is still inadmissible here: it only produces a level for
    // losing trades, so drawing one would announce the outcome by existing.
    const p = plan.planCapture({ trade: TRADE, kind: 'entry', symbol: 'OANDA:XAUUSD' });
    assert.equal(p.levels.stop, null);
    assert.equal(shapeRoles(p).includes('stop_zone'), false);
    assert.ok(p.notes.includes(plan.NO_STOP_NOTE));
  });

  test('a stop known at entry is drawn, and says where it came from', () => {
    const p = plan.planCapture({
      trade: TRADE, kind: 'entry', symbol: 'OANDA:XAUUSD', stop: 1944.75, target: 1970,
    });
    assert.equal(p.levels.stop.price, 1944.75);
    assert.equal(p.levels.stop.source, 'supplied');
    assert.equal(p.levels.target.source, 'supplied');
    assert.ok(shapeRoles(p).includes('stop_zone'));
    assert.ok(shapeRoles(p).includes('target_zone'));
  });

  test('a zero-width window is widened rather than obeyed', () => {
    const p = plan.planCapture({
      trade: TRADE, kind: 'entry', symbol: 'OANDA:XAUUSD', barsBefore: 0,
    });
    assert.ok(p.range.from < p.range.to);
  });

  test('the entry bar can be excluded for a strictly knowable frame', () => {
    const p = plan.planCapture({
      trade: TRADE, kind: 'entry', symbol: 'OANDA:XAUUSD', includeEntryBar: false,
    });
    assert.equal(p.range.to, ENTRY_BAR - 1);
    assert.match(p.notes.join(' '), /had closed before the entry/);
  });

  test('every capture is labelled with whose prices these are', () => {
    const p = plan.planCapture({ trade: TRADE, kind: 'entry', symbol: 'OANDA:XAUUSD' });
    assert.ok(p.warnings.some(w => /different feed from your broker/.test(w)));
  });
});

describe('review capture — the outcome is the subject', () => {
  test('the exit is marked and the frame runs past it', () => {
    const p = plan.planCapture({ trade: TRADE, kind: 'review', symbol: 'OANDA:XAUUSD' });
    assert.ok(shapeRoles(p).includes('exit_line'));
    assert.ok(shapeRoles(p).includes('exit_marker'));
    assert.equal(p.levels.exit, 1944.75);
    assert.ok(p.range.to > TRADE.closed_utc);
  });

  test('the stop is recovered from a stop-loss exit and labelled as such', () => {
    const p = plan.planCapture({ trade: TRADE, kind: 'review', symbol: 'OANDA:XAUUSD' });
    assert.equal(p.levels.stop.price, 1944.75);
    assert.equal(p.levels.stop.source, 'exit_fill');
    assert.match(p.notes.join(' '), /read back from the exit fill/);
  });

  test('a still-open position runs to now and marks no exit', () => {
    const open = {
      ...TRADE, closed_utc: null, exit_price: null, exit_reason: null,
      duration_sec: null, open: true,
    };
    const now = TRADE.opened_utc + 7200;
    const p = plan.planCapture({ trade: open, kind: 'review', symbol: 'OANDA:XAUUSD', now });
    assert.equal(shapeRoles(p).some(r => r.startsWith('exit')), false);
    assert.ok(p.range.to >= now);
    assert.match(p.notes.join(' '), /still open/);
  });
});

describe('planCapture — refusals', () => {
  test('a trade whose entry is outside the window is refused, not invented', () => {
    const partial = { ...TRADE, entry_price: null, opened_utc: null, entry_missing: true };
    assert.throws(() => plan.planCapture({ trade: partial, kind: 'review' }), /no entry time/);
  });

  test('an unknown kind is refused', () => {
    assert.throws(() => plan.planCapture({ trade: TRADE, kind: 'postmortem' }), /Unknown capture kind/);
  });
});

/** Record every call the pipeline makes, in order. */
function harness({ trades = [TRADE], journalFails = false } = {}) {
  const calls = [];
  let nextId = 0;
  return {
    calls,
    deps: {
      now: () => TRADE.closed_utc + 60,
      mt5: {
        trades: async (args) => {
          calls.push(['mt5.trades', args]);
          return { success: true, trades };
        },
      },
      chart: {
        setSymbol: async ({ symbol }) => { calls.push(['setSymbol', symbol]); return { success: true }; },
        setTimeframe: async ({ timeframe }) => { calls.push(['setTimeframe', timeframe]); return { success: true }; },
        setVisibleRange: async ({ from, to }) => { calls.push(['setVisibleRange', { from, to }]); return { success: true }; },
      },
      drawing: {
        drawShape: async ({ shape }) => {
          const entity_id = `shape_${++nextId}`;
          calls.push(['drawShape', shape, entity_id]);
          return { success: true, entity_id };
        },
        removeOne: async ({ entity_id }) => { calls.push(['removeOne', entity_id]); return { success: true }; },
      },
      capture: {
        captureScreenshot: async ({ filename, region }) => {
          calls.push(['captureScreenshot', { filename, region }]);
          return { success: true, file_path: `/shots/${filename}.png` };
        },
      },
      journal: {
        attachScreenshot: async (args) => {
          calls.push(['attachScreenshot', args]);
          if (journalFails) throw new Error('Journal service not reachable at http://127.0.0.1:8766.');
          return { success: true, id: 7 };
        },
      },
      loadOverrides: () => ({ path: null, overrides: {} }),
    },
  };
}

describe('captureTrade — wiring', () => {
  test('sets up the chart, draws, shoots, then removes only its own shapes', async () => {
    const h = harness();
    const result = await plan.captureTrade({ position_id: '998877', _deps: h.deps });

    const order = h.calls.map(c => c[0]);
    assert.deepEqual(
      order.filter(name => name !== 'drawShape' && name !== 'removeOne'),
      ['mt5.trades', 'setSymbol', 'setTimeframe', 'setVisibleRange', 'captureScreenshot', 'attachScreenshot']);

    const drawn = h.calls.filter(c => c[0] === 'drawShape').map(c => c[2]);
    const removed = h.calls.filter(c => c[0] === 'removeOne').map(c => c[1]);
    assert.deepEqual(removed, drawn);
    assert.equal(result.drawings.created, drawn.length);
    assert.match(result.file_path, /^\/shots\/trade_998877_entry_\d{8}-\d{6}\.png$/);
  });

  test('maps the broker symbol and files the image against the position', async () => {
    const h = harness();
    const result = await plan.captureTrade({ position_id: '998877', _deps: h.deps });
    assert.equal(result.symbol, 'OANDA:XAUUSD');
    assert.equal(result.broker_symbol, 'GOLD.i#');
    const [, attach] = h.calls.find(c => c[0] === 'attachScreenshot');
    assert.equal(attach.position_id, '998877');
    assert.equal(attach.kind, 'trade_entry');
    assert.equal(result.journal.ok, true);
  });

  test('a journal that is not running costs the index entry, not the capture', async () => {
    const h = harness({ journalFails: true });
    const result = await plan.captureTrade({ position_id: '998877', _deps: h.deps });
    assert.equal(result.success, true);
    assert.ok(result.file_path);
    assert.equal(result.journal.ok, false);
    assert.match(result.journal.error, /not reachable/);
  });

  test('an unmappable broker symbol stops the capture and says what to add', async () => {
    const h = harness({ trades: [{ ...TRADE, symbol: 'WIDGET.x#' }] });
    await assert.rejects(
      () => plan.captureTrade({ position_id: '998877', _deps: h.deps }),
      /symbol-map\.json/);
    assert.equal(h.calls.some(c => c[0] === 'setSymbol'), false);
  });

  test('an explicit TradingView symbol skips the table entirely', async () => {
    const h = harness({ trades: [{ ...TRADE, symbol: 'WIDGET.x#' }] });
    const result = await plan.captureTrade({
      position_id: '998877', symbol: 'FX:XAUUSD', _deps: h.deps,
    });
    assert.equal(result.symbol, 'FX:XAUUSD');
    assert.equal(result.symbol_mapping.via, 'requested');
  });

  test('keep_drawings leaves the markup on the chart', async () => {
    const h = harness();
    const result = await plan.captureTrade({
      position_id: '998877', keep_drawings: true, _deps: h.deps,
    });
    assert.equal(h.calls.some(c => c[0] === 'removeOne'), false);
    assert.equal(result.drawings.removed, 0);
  });
});

describe('findTrade', () => {
  test('with no id, the most recently closed trade is the one you mean', async () => {
    const older = { ...TRADE, position_id: 1, closed_utc: TRADE.closed_utc - 86400 };
    const newer = { ...TRADE, position_id: 2, closed_utc: TRADE.closed_utc };
    const stillOpen = { ...TRADE, position_id: 3, closed_utc: null, open: true };
    const h = harness({ trades: [older, newer, stillOpen] });
    const found = await plan.findTrade({ _deps: h.deps });
    assert.equal(found.position_id, 2);
  });

  test('a position outside the window names the window rather than the id', async () => {
    const h = harness();
    await assert.rejects(
      () => plan.findTrade({ position_id: '123', _deps: h.deps }),
      /Widen the window with days=/);
  });

  test('the window defaults to 30 days back from now', async () => {
    const h = harness();
    await plan.findTrade({ _deps: h.deps });
    const [, args] = h.calls.find(c => c[0] === 'mt5.trades');
    assert.equal(args.to - args.from, 30 * 86400);
    assert.equal(args.summary, false);
  });
});
