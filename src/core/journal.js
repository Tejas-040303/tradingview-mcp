/**
 * HTTP client for the journal write service (mt5-bridge/journal_service.py).
 *
 * A *second* client to a *second* process, on purpose. `core/mt5.js` talks to
 * the read-only bridge on 8765 and cannot write; this talks to the journal on
 * 8766, which writes a local SQLite file and imports no broker client. Folding
 * them into one client would blur the boundary the two ports exist to hold —
 * see HANDOVER.md.
 *
 * Nothing here can place, modify or cancel an order. The service it talks to
 * has no route that could.
 */

const DEFAULT_URL = 'http://127.0.0.1:8766';
const DEFAULT_TIMEOUT_MS = 10000;

function config() {
  return {
    url: (process.env.MT5_JOURNAL_URL || DEFAULT_URL).replace(/\/+$/, ''),
    token: process.env.MT5_JOURNAL_TOKEN || null,
    timeout: Number(process.env.MT5_JOURNAL_TIMEOUT) || DEFAULT_TIMEOUT_MS,
  };
}

async function request(method, path, body, deps = null) {
  const doFetch = deps?.fetch || globalThis.fetch;
  const { url, token, timeout } = config();

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);

  let response;
  try {
    response = await doFetch(`${url}${path}`, {
      method,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { 'X-Journal-Token': token } : {}),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
  } catch (err) {
    if (err.name === 'AbortError') {
      throw new Error(`Journal service did not respond within ${timeout}ms at ${url}.`);
    }
    throw new Error(
      `Journal service not reachable at ${url}. Start it with: ` +
      'python mt5-bridge/journal_service.py');
  } finally {
    clearTimeout(timer);
  }

  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(
      `Journal service returned a non-JSON response (HTTP ${response.status}) from ${path}`);
  }

  if (!response.ok) {
    const detail = payload?.error || `HTTP ${response.status}`;
    if (response.status === 401) {
      throw new Error(`Journal service rejected the token: ${detail}. Check MT5_JOURNAL_TOKEN.`);
    }
    throw new Error(detail);
  }

  return payload;
}

export function health(_deps) {
  return request('GET', '/health', undefined, _deps);
}

/**
 * Record that an image exists for a trade or a signal.
 *
 * The journal stores the path, not the picture — captures are a cache that
 * retention may delete, and the row outliving the file is the point: losing
 * the image is not the same as losing the fact that one was taken.
 */
export function attachScreenshot({ path, kind, position_id, signal_id, _deps } = {}) {
  if (!path) throw new Error('attachScreenshot needs the file path of the image');
  if (!kind) throw new Error('attachScreenshot needs a kind, e.g. "trade_entry"');
  return request('POST', '/screenshot', {
    path, kind,
    ...(position_id === undefined || position_id === null ? {} : { position_id }),
    ...(signal_id === undefined || signal_id === null ? {} : { signal_id }),
  }, _deps);
}
