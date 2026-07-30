/**
 * Unit tests for waitForChartReady.
 *
 * The real implementation talks to TradingView over CDP; here `evaluate` is
 * injected so each test can script the sequence of chart states it wants.
 */
import { test, describe } from 'node:test';
import assert from 'node:assert';
import { waitForChartReady } from '../src/wait.js';

/** Build a chart state, defaulting to a healthy loaded chart. */
function state(overrides = {}) {
  return {
    apiReady: true,
    symbol: 'FX:XAUUSD',
    resolution: '5',
    barCount: 300,
    canvasWidth: 1200,
    canvasHeight: 600,
    isLoading: false,
    ...overrides,
  };
}

/** Return an evaluate stub that yields each state in turn, repeating the last. */
function scripted(states) {
  let i = 0;
  const calls = [];
  const evaluate = async () => {
    const s = states[Math.min(i, states.length - 1)];
    i++;
    calls.push(s);
    return s;
  };
  return { evaluate, calls: () => calls };
}

describe('waitForChartReady — ready detection', () => {
  test('returns true for a stable loaded chart', async () => {
    const { evaluate } = scripted([state()]);
    assert.equal(await waitForChartReady('XAUUSD', '5', 5000, { evaluate }), true);
  });

  test('matches a bare symbol against the exchange-qualified form', async () => {
    const { evaluate } = scripted([state({ symbol: 'FX:XAUUSD' })]);
    assert.equal(await waitForChartReady('XAUUSD', null, 5000, { evaluate }), true);
  });

  test('matches when the caller supplies its own exchange prefix', async () => {
    const { evaluate } = scripted([state({ symbol: 'CME_MINI:ES1!' })]);
    assert.equal(await waitForChartReady('CME_MINI:ES1!', null, 5000, { evaluate }), true);
  });

  test('becomes ready once the requested symbol arrives', async () => {
    const { evaluate } = scripted([
      state({ symbol: 'NASDAQ:AAPL' }),
      state({ symbol: 'NASDAQ:AAPL' }),
      state({ symbol: 'FX:XAUUSD' }),
    ]);
    assert.equal(await waitForChartReady('XAUUSD', null, 5000, { evaluate }), true);
  });

  test('waits out a loading spinner', async () => {
    const { evaluate } = scripted([
      state({ isLoading: true }),
      state({ isLoading: true }),
      state(),
    ]);
    assert.equal(await waitForChartReady('XAUUSD', '5', 5000, { evaluate }), true);
  });

  test('ignores an unreadable bar count rather than blocking on it', async () => {
    const { evaluate } = scripted([state({ barCount: -1 })]);
    assert.equal(await waitForChartReady('XAUUSD', '5', 5000, { evaluate }), true);
  });

  test('does not require a symbol or timeframe to be specified', async () => {
    const { evaluate } = scripted([state()]);
    assert.equal(await waitForChartReady(null, null, 5000, { evaluate }), true);
  });
});

describe('waitForChartReady — not-ready detection', () => {
  test('returns false when the chart API never comes up', async () => {
    const { evaluate } = scripted([state({ apiReady: false })]);
    assert.equal(await waitForChartReady('XAUUSD', '5', 600, { evaluate }), false);
  });

  test('returns false when evaluate yields nothing', async () => {
    const evaluate = async () => null;
    assert.equal(await waitForChartReady('XAUUSD', '5', 600, { evaluate }), false);
  });

  test('returns false when the symbol never matches', async () => {
    const { evaluate } = scripted([state({ symbol: 'NASDAQ:AAPL' })]);
    assert.equal(await waitForChartReady('XAUUSD', null, 600, { evaluate }), false);
  });

  test('returns false when the resolution never matches', async () => {
    const { evaluate } = scripted([state({ resolution: '1' })]);
    assert.equal(await waitForChartReady(null, '15', 600, { evaluate }), false);
  });

  test('returns false when the spinner never clears', async () => {
    const { evaluate } = scripted([state({ isLoading: true })]);
    assert.equal(await waitForChartReady('XAUUSD', '5', 600, { evaluate }), false);
  });

  test('returns false for an empty series', async () => {
    const { evaluate } = scripted([state({ barCount: 0 })]);
    assert.equal(await waitForChartReady('XAUUSD', '5', 600, { evaluate }), false);
  });

  test('returns false when nothing is rendered', async () => {
    const { evaluate } = scripted([state({ canvasWidth: 0, canvasHeight: 0 })]);
    assert.equal(await waitForChartReady('XAUUSD', '5', 600, { evaluate }), false);
  });
});

describe('waitForChartReady — regression: usable but unsettled chart', () => {
  // The bug this fix addresses: a loaded chart whose observed state keeps
  // changing was reported as un-ready. A chart that matches the request and is
  // rendering is usable, so a timeout without a settled signature must still
  // report ready.
  test('reports ready on timeout if the chart was usable throughout', async () => {
    let n = 0;
    const evaluate = async () => state({ barCount: 300 + (n++) });
    assert.equal(await waitForChartReady('XAUUSD', '5', 700, { evaluate }), true);
  });

  test('reports not-ready on timeout if the chart was never usable', async () => {
    let n = 0;
    const evaluate = async () => state({ isLoading: true, barCount: 300 + (n++) });
    assert.equal(await waitForChartReady('XAUUSD', '5', 700, { evaluate }), false);
  });
});
