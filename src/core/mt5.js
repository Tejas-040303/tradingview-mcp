/**
 * HTTP client for the read-only MT5 bridge (mt5-bridge/bridge.py).
 *
 * MetaTrader 5 has no Node binding — the official package is Python and
 * Windows-only — so this talks to the local bridge process rather than the
 * terminal directly. Transport-agnostic like the rest of src/core: no MCP
 * awareness, so it stays testable with a stubbed fetch.
 *
 * Read-only by construction. The bridge exposes no route that can place,
 * modify or cancel an order, and nothing here attempts one.
 */

const DEFAULT_URL = 'http://127.0.0.1:8765';
const DEFAULT_TIMEOUT_MS = 15000;

function config() {
  return {
    url: (process.env.MT5_BRIDGE_URL || DEFAULT_URL).replace(/\/+$/, ''),
    token: process.env.MT5_BRIDGE_TOKEN || null,
    timeout: Number(process.env.MT5_BRIDGE_TIMEOUT) || DEFAULT_TIMEOUT_MS,
  };
}

/**
 * Build a query string, dropping unset params.
 *
 * Values are encoded, which matters more than it looks: broker symbols carry
 * characters with meaning in a URL. XM names spot gold `GOLD.i#`, and an
 * unencoded `#` starts a fragment — the bridge would only ever receive
 * `GOLD.i`. Encoding here means callers never think about it.
 */
function query(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params || {})) {
    if (value === undefined || value === null || value === '') continue;
    search.append(key, Array.isArray(value) ? value.join(',') : String(value));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : '';
}

/**
 * GET a bridge route.
 *
 * Failure modes are mapped to actionable messages rather than surfaced raw —
 * "ECONNREFUSED" tells the caller nothing, "start bridge.py" tells them
 * everything. The bridge's own 4xx/5xx messages are already actionable, so
 * those are passed through unchanged.
 */
async function get(path, params, deps = null) {
  const doFetch = deps?.fetch || globalThis.fetch;
  const { url, token, timeout } = config();
  const target = `${url}${path}${query(params)}`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);

  let response;
  try {
    response = await doFetch(target, {
      signal: controller.signal,
      headers: token ? { 'X-Bridge-Token': token } : {},
    });
  } catch (err) {
    if (err.name === 'AbortError') {
      throw new Error(
        `MT5 bridge did not respond within ${timeout}ms at ${url}. ` +
        'Is the terminal busy or frozen?');
    }
    throw new Error(
      `MT5 bridge not reachable at ${url}. Start it with: ` +
      'python mt5-bridge/bridge.py');
  } finally {
    clearTimeout(timer);
  }

  let body;
  try {
    body = await response.json();
  } catch {
    throw new Error(
      `MT5 bridge returned a non-JSON response (HTTP ${response.status}) from ${path}`);
  }

  if (!response.ok) {
    // The bridge already explains itself — "run calendar_export.mq5", "MT5 not
    // reachable", a named missing parameter. Don't paper over it.
    const detail = body?.error || `HTTP ${response.status}`;
    if (response.status === 401) {
      throw new Error(`MT5 bridge rejected the token: ${detail}. Check MT5_BRIDGE_TOKEN.`);
    }
    throw new Error(detail);
  }

  return body;
}

export function health(_deps) {
  return get('/health', {}, _deps);
}

export function account(_deps) {
  return get('/account', {}, _deps);
}

export function symbolSearch({ search, limit = 50, _deps } = {}) {
  return get('/symbols', { search, limit }, _deps);
}

export function quote({ symbol, _deps }) {
  return get('/quote', { symbol }, _deps);
}

export function bars({ symbol, timeframe = '5', count = 100, summary = true, _deps }) {
  return get('/bars', { symbol, timeframe, count, summary: summary ? 1 : '' }, _deps);
}

export function positions({ symbol, _deps } = {}) {
  return get('/positions', { symbol }, _deps);
}

export function orders({ symbol, _deps } = {}) {
  return get('/orders', { symbol }, _deps);
}

export function deals({ from, to, symbol, summary = true, limit = 50, offset = 0, _deps } = {}) {
  const now = Math.floor(Date.now() / 1000);
  return get('/deals', {
    from: from ?? now - 30 * 86400,
    to: to ?? now,
    symbol,
    summary: summary ? 1 : '',
    limit,
    offset,
  }, _deps);
}

/**
 * Post-trade excursion analysis.
 *
 * Slower than the other routes because the bridge fetches bar data around every
 * trade, so it is never folded into a general request — you ask for it.
 */
export function excursions({ from, to, symbol, timeframe = '1', summary = true } = {}) {
  return get('/excursions', {
    ...(from !== undefined ? { from } : {}),
    ...(to !== undefined ? { to } : {}),
    ...(symbol ? { symbol } : {}),
    timeframe,
    summary: summary ? 'true' : 'false',
  });
}

/**
 * Can the stop distance be recovered from order history?
 *
 * One question, one answer per account. Everything R-multiple-shaped depends on
 * it, and it cannot be reasoned about — only measured against a real account.
 */
export function diagnoseStops({ from, to, symbol, sample } = {}) {
  return get('/diagnose/stops', {
    ...(from !== undefined ? { from } : {}),
    ...(to !== undefined ? { to } : {}),
    ...(symbol ? { symbol } : {}),
    ...(sample !== undefined ? { sample } : {}),
  });
}

export function trades({ from, to, symbol, summary = true, closed_only = false,
  limit = 50, offset = 0, _deps } = {}) {
  const now = Math.floor(Date.now() / 1000);
  return get('/trades', {
    from: from ?? now - 30 * 86400,
    to: to ?? now,
    symbol,
    summary: summary ? 1 : '',
    closed_only: closed_only ? 1 : '',
    limit,
    offset,
  }, _deps);
}

export function analytics({ from, to, symbol, starting_balance, group_by, curve = false, _deps } = {}) {
  const now = Math.floor(Date.now() / 1000);
  return get('/analytics', {
    from: from ?? now - 30 * 86400,
    to: to ?? now,
    symbol,
    starting_balance,
    group_by,
    curve: curve ? 1 : '',
  }, _deps);
}

export function calendar({ currencies, min_importance = 'high', from, to, limit = 100, _deps } = {}) {
  return get('/calendar', {
    currencies, min_importance, from, to, limit,
  }, _deps);
}

export function blackout({ currencies = ['USD'], before_min = 15, after_min = 15,
  min_importance = 'high', _deps } = {}) {
  return get('/blackout', {
    currencies, before_min, after_min, min_importance,
  }, _deps);
}

/**
 * Strategy-engine routes.
 *
 * All read-only like everything above: these describe what a strategy would
 * do, and nothing here can place an order.
 *
 * The config knobs are passed through verbatim rather than defaulted here, so
 * the bridge stays the single place a strategy default is defined. One
 * exception is deliberate — `trail` must survive as the string "none" rather
 * than being coerced to 0 or dropped, because turning the breakeven trail off
 * is the control case for any comparison against it.
 */
function strategyParams({ symbol, timeframe, count, balance, conditions, required,
  mode, min_conditions, confirmation, buffer_pips, risk_pct, max_risk_pct,
  target_r, partial_pct, partial_at_r, trail } = {}) {
  return {
    symbol, timeframe, count, balance, conditions, required, mode,
    min_conditions, confirmation, buffer_pips, risk_pct, max_risk_pct,
    target_r, partial_pct, partial_at_r,
    // `query()` drops null and '', so an unset trail must stay undefined while
    // an explicit "none" survives as text the bridge parses back to null.
    ...(trail === undefined || trail === null ? {} : { trail: String(trail) }),
  };
}

/**
 * Detected entry signals, with no simulation attached.
 *
 * The checkpoint before any backtest number means anything: each row carries a
 * timestamp, direction and the levels involved, so it can be checked against a
 * chart and judged as a setup worth taking.
 */
export function setups({ limit, offset, _deps, ...rest } = {}) {
  return get('/setups', { ...strategyParams(rest), limit, offset }, _deps);
}

/** Replay those signals with stops, targets, partials and the trail. */
export function backtest({ trades = false, skipped = false, compound = false,
  _deps, ...rest } = {}) {
  return get('/backtest', {
    ...strategyParams(rest),
    trades: trades ? 1 : '',
    skipped: skipped ? 1 : '',
    compound: compound ? 1 : '',
  }, _deps);
}

/**
 * Every parameter combination, judged on bars it was not chosen on.
 *
 * The slowest route by a wide margin — the default grid is thirty
 * configurations over the whole window — so it is never folded into anything
 * else.
 */
export function sweep({ axes, split, min_trades, _deps, ...rest } = {}) {
  return get('/sweep', {
    ...strategyParams(rest), axes, split, min_trades,
  }, _deps);
}

/** What the strategy would be doing right now. */
export function paper({ recent, _deps, ...rest } = {}) {
  return get('/paper', { ...strategyParams(rest), recent }, _deps);
}

/** Signals against fills: followed, missed, and traded-without-a-signal. */
export function reconcile({ tolerance, detail = false, _deps, ...rest } = {}) {
  return get('/reconcile', {
    ...strategyParams(rest), tolerance, detail: detail ? 1 : '',
  }, _deps);
}
