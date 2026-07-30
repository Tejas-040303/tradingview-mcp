import { evaluate as _evaluate } from './connection.js';

const DEFAULT_TIMEOUT = 10000;
const POLL_INTERVAL = 200;
const STABLE_POLLS = 3;

/**
 * Compare a requested symbol against what the chart reports, ignoring the
 * exchange prefix. TradingView resolves bare tickers to a qualified form
 * ("XAUUSD" -> "FX:XAUUSD", "ES1!" -> "CME_MINI:ES1!"), so a strict equality
 * check would never match what the caller asked for.
 */
function symbolMatches(requested, actual) {
  if (!requested || !actual) return true;
  const bare = s => String(s).trim().toUpperCase().split(':').pop();
  return bare(requested) === bare(actual);
}

function resolutionMatches(requested, actual) {
  if (!requested || !actual) return true;
  return String(requested).trim().toUpperCase() === String(actual).trim().toUpperCase();
}

/**
 * Wait until the chart has finished switching symbol / resolution.
 *
 * Readiness is read from TradingView's chart API (symbol, resolution, series
 * bar count) rather than scraped from the DOM. The previous implementation
 * counted `[class*="bar"]` elements, which matches toolbars, sidebars and
 * scrollbars as well as price bars; that count kept changing as the chart
 * re-rendered, so the stability check never settled and this returned false on
 * charts that were perfectly ready.
 *
 * Returns true once symbol/resolution match the request, nothing is loading,
 * and the chart state holds steady across STABLE_POLLS polls. On timeout it
 * returns whether the chart was ever observed in a usable state, so a chart
 * that loaded but kept ticking is not reported as un-ready.
 */
export async function waitForChartReady(expectedSymbol = null, expectedTf = null, timeout = DEFAULT_TIMEOUT, deps = null) {
  const evaluate = deps?.evaluate || _evaluate;
  const start = Date.now();
  let lastSignature = null;
  let stableCount = 0;
  let sawUsableState = false;

  while (Date.now() - start < timeout) {
    const state = await evaluate(`
      (function() {
        var out = { apiReady: false, symbol: '', resolution: '', barCount: -1,
                    canvasWidth: 0, canvasHeight: 0, isLoading: false };
        try {
          var chart = window.TradingViewApi._activeChartWidgetWV.value();
          out.symbol = chart.symbol();
          out.resolution = chart.resolution();
          out.apiReady = true;
        } catch (e) {}
        try {
          var series = window.TradingViewApi._activeChartWidgetWV.value()
            ._chartWidget.model().mainSeries();
          var bars = series.bars && series.bars();
          if (bars && typeof bars.size === 'function') out.barCount = bars.size();
        } catch (e) {}
        try {
          var canvas = document.querySelector('[data-name="pane-canvas"] canvas')
            || document.querySelector('[data-name="pane-canvas"]')
            || document.querySelector('canvas');
          var rect = canvas ? canvas.getBoundingClientRect() : null;
          if (rect) {
            out.canvasWidth = Math.round(rect.width);
            out.canvasHeight = Math.round(rect.height);
          }
        } catch (e) {}
        try {
          var spinner = document.querySelector('[data-name="loading"]')
            || document.querySelector('[class*="loader"]');
          out.isLoading = !!(spinner && spinner.offsetParent !== null);
        } catch (e) {}
        return out;
      })()
    `);

    // Chart API not up yet, or the evaluate call came back empty.
    if (!state || !state.apiReady || state.isLoading
      || !symbolMatches(expectedSymbol, state.symbol)
      || !resolutionMatches(expectedTf, state.resolution)
      || !state.canvasWidth || !state.canvasHeight
      || state.barCount === 0) {
      stableCount = 0;
      lastSignature = null;
      await new Promise(r => setTimeout(r, POLL_INTERVAL));
      continue;
    }

    // Everything the caller asked for is in place — the chart is usable even if
    // the signature below never settles.
    sawUsableState = true;

    // barCount of -1 means "could not read", which is constant and therefore
    // does not block the stability check.
    const signature = [state.symbol, state.resolution, state.barCount,
      state.canvasWidth, state.canvasHeight].join('|');
    if (signature === lastSignature) stableCount++;
    else { stableCount = 0; lastSignature = signature; }

    if (stableCount >= STABLE_POLLS) return true;

    await new Promise(r => setTimeout(r, POLL_INTERVAL));
  }

  return sawUsableState;
}

/**
 * Wait for the chart to finish (re)rendering — used before screenshots so a
 * capture right after chart_set_symbol / chart_set_timeframe doesn't grab a
 * stale frame (issue #144). Waits for any loading spinner to clear, then for
 * the symbol/resolution/canvas signature to hold stable across 3 polls.
 */
export async function waitForChartRender(timeout = 5000, deps = null) {
  const evaluate = deps?.evaluate || _evaluate;
  const start = Date.now();
  let lastSignature = null;
  let stableCount = 0;

  while (Date.now() - start < timeout) {
    const state = await evaluate(`
      (function() {
        var canvas = document.querySelector('[data-name="pane-canvas"] canvas')
          || document.querySelector('[data-name="pane-canvas"]')
          || document.querySelector('canvas');
        var rect = canvas ? canvas.getBoundingClientRect() : null;
        var symbol = '', resolution = '';
        try {
          var chart = window.TradingViewApi._activeChartWidgetWV.value();
          symbol = chart.symbol();
          resolution = chart.resolution();
        } catch(e) {}
        var spinner = document.querySelector('[class*="loader"]')
          || document.querySelector('[class*="loading"]')
          || document.querySelector('[data-name="loading"]');
        return {
          symbol: symbol,
          resolution: resolution,
          isLoading: !!(spinner && spinner.offsetParent !== null),
          canvasWidth: rect ? Math.round(rect.width) : 0,
          canvasHeight: rect ? Math.round(rect.height) : 0
        };
      })()
    `);

    if (!state || state.isLoading || !state.canvasWidth || !state.canvasHeight) {
      stableCount = 0;
      await new Promise(r => setTimeout(r, POLL_INTERVAL));
      continue;
    }

    const signature = [state.symbol, state.resolution, state.canvasWidth, state.canvasHeight].join('|');
    if (signature === lastSignature) stableCount++;
    else { stableCount = 0; lastSignature = signature; }

    if (stableCount >= 3) return true;
    await new Promise(r => setTimeout(r, POLL_INTERVAL));
  }

  return false;
}
