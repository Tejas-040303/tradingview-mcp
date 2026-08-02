/**
 * Unit tests for the MT5 bridge client.
 *
 * fetch is stubbed, so these need no bridge, no terminal and no network.
 * They assert wiring — URL construction, defaults, error mapping — because
 * wiring is where this layer can break, and an untested caller is how a
 * shadowed variable once took out /deals pagination entirely.
 */
import { test, describe, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert';
import * as mt5 from '../src/core/mt5.js';

/** Capture the URL a call produces, and return `body` as its response. */
function stub(body = { success: true }, { ok = true, status = 200, json = true } = {}) {
  const calls = [];
  const fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok,
      status,
      json: async () => {
        if (!json) throw new SyntaxError('not json');
        return body;
      },
    };
  };
  return { fetch, calls, last: () => calls[calls.length - 1] };
}

function params(url) {
  return new URL(url).searchParams;
}

describe('mt5 client — URL construction', () => {
  test('targets the default bridge address', async () => {
    const s = stub();
    await mt5.health({ fetch: s.fetch });
    assert.ok(s.last().url.startsWith('http://127.0.0.1:8765/health'));
  });

  test('encodes symbols containing URL-significant characters', async () => {
    const s = stub();
    await mt5.quote({ symbol: 'GOLD.i#', _deps: { fetch: s.fetch } });
    // Unencoded, the '#' would start a fragment and the bridge would only
    // ever receive 'GOLD.i'.
    assert.ok(s.last().url.includes('GOLD.i%23'));
    assert.equal(params(s.last().url).get('symbol'), 'GOLD.i#');
  });

  test('omits unset optional params rather than sending empty ones', async () => {
    const s = stub();
    await mt5.positions({ _deps: { fetch: s.fetch } });
    assert.equal(params(s.last().url).has('symbol'), false);
  });

  test('joins array params with commas', async () => {
    const s = stub();
    await mt5.blackout({ currencies: ['USD', 'EUR'], _deps: { fetch: s.fetch } });
    assert.equal(params(s.last().url).get('currencies'), 'USD,EUR');
  });
});

describe('mt5 client — defaults', () => {
  test('bars summarise by default', async () => {
    const s = stub();
    await mt5.bars({ symbol: 'GOLD.i#', _deps: { fetch: s.fetch } });
    assert.equal(params(s.last().url).get('summary'), '1');
    assert.equal(params(s.last().url).get('timeframe'), '5');
    assert.equal(params(s.last().url).get('count'), '100');
  });

  test('bars can opt out of summarising', async () => {
    const s = stub();
    await mt5.bars({ symbol: 'GOLD.i#', summary: false, _deps: { fetch: s.fetch } });
    assert.equal(params(s.last().url).has('summary'), false);
  });

  test('deals summarise by default and cover 30 days', async () => {
    const s = stub();
    await mt5.deals({ _deps: { fetch: s.fetch } });
    const p = params(s.last().url);
    assert.equal(p.get('summary'), '1');
    const span = Number(p.get('to')) - Number(p.get('from'));
    assert.equal(span, 30 * 86400);
  });

  test('deals accept an explicit window and page', async () => {
    const s = stub();
    await mt5.deals({ from: 100, to: 200, summary: false, limit: 25, offset: 50,
      _deps: { fetch: s.fetch } });
    const p = params(s.last().url);
    assert.equal(p.get('from'), '100');
    assert.equal(p.get('to'), '200');
    assert.equal(p.get('limit'), '25');
    assert.equal(p.get('offset'), '50');
  });

  test('calendar defaults to high importance', async () => {
    // The HTTP route stays unfiltered; the tool is opinionated because an
    // unfiltered calendar is hundreds of rows of context.
    const s = stub();
    await mt5.calendar({ _deps: { fetch: s.fetch } });
    assert.equal(params(s.last().url).get('min_importance'), 'high');
  });

  test('blackout defaults to USD and a symmetric 15 minute window', async () => {
    const s = stub();
    await mt5.blackout({ _deps: { fetch: s.fetch } });
    const p = params(s.last().url);
    assert.equal(p.get('currencies'), 'USD');
    assert.equal(p.get('before_min'), '15');
    assert.equal(p.get('after_min'), '15');
  });

  test('symbol search caps results', async () => {
    const s = stub();
    await mt5.symbolSearch({ search: 'gold', _deps: { fetch: s.fetch } });
    assert.equal(params(s.last().url).get('limit'), '50');
  });
});

describe('mt5 client — configuration', () => {
  const saved = { ...process.env };
  beforeEach(() => { delete process.env.MT5_BRIDGE_URL; delete process.env.MT5_BRIDGE_TOKEN; });
  afterEach(() => { process.env = { ...saved }; });

  test('honours a custom bridge URL', async () => {
    process.env.MT5_BRIDGE_URL = 'http://127.0.0.1:9100';
    const s = stub();
    await mt5.health({ fetch: s.fetch });
    assert.ok(s.last().url.startsWith('http://127.0.0.1:9100/health'));
  });

  test('strips a trailing slash from the configured URL', async () => {
    process.env.MT5_BRIDGE_URL = 'http://127.0.0.1:8765/';
    const s = stub();
    await mt5.health({ fetch: s.fetch });
    assert.ok(!s.last().url.includes('//health'));
  });

  test('sends the token header only when configured', async () => {
    const bare = stub();
    await mt5.health({ fetch: bare.fetch });
    assert.equal(bare.last().options.headers['X-Bridge-Token'], undefined);

    process.env.MT5_BRIDGE_TOKEN = 'secret';
    const authed = stub();
    await mt5.health({ fetch: authed.fetch });
    assert.equal(authed.last().options.headers['X-Bridge-Token'], 'secret');
  });
});

describe('mt5 client — error mapping', () => {
  test('a refused connection names the fix', async () => {
    const fetch = async () => { throw new TypeError('fetch failed'); };
    await assert.rejects(
      () => mt5.health({ fetch }),
      /not reachable.*python mt5-bridge\/bridge\.py/s,
    );
  });

  test('a timeout says so rather than reporting unreachable', async () => {
    const fetch = async () => {
      const err = new Error('aborted');
      err.name = 'AbortError';
      throw err;
    };
    await assert.rejects(() => mt5.health({ fetch }), /did not respond within/);
  });

  test('bridge errors are passed through, not swallowed', async () => {
    // The bridge already explains itself; rewording would lose the fix.
    const s = stub(
      { success: false, error: 'No calendar file configured. Run calendar_export.mq5' },
      { ok: false, status: 503 },
    );
    await assert.rejects(
      () => mt5.calendar({ _deps: { fetch: s.fetch } }),
      /Run calendar_export\.mq5/,
    );
  });

  test('a 401 points at the token setting', async () => {
    const s = stub({ success: false, error: 'Invalid or missing X-Bridge-Token' },
      { ok: false, status: 401 });
    await assert.rejects(() => mt5.health({ fetch: s.fetch }), /MT5_BRIDGE_TOKEN/);
  });

  test('a non-JSON response is reported as such', async () => {
    const s = stub(null, { ok: true, status: 200, json: false });
    await assert.rejects(() => mt5.health({ fetch: s.fetch }), /non-JSON response/);
  });

  test('a successful body is returned unchanged', async () => {
    const payload = { success: true, blackout: false, next: { event: 'CPI' } };
    const s = stub(payload);
    const out = await mt5.blackout({ _deps: { fetch: s.fetch } });
    assert.deepEqual(out, payload);
  });
});
