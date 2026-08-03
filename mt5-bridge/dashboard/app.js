/**
 * Status dashboard.
 *
 * Reads /overview — one request for the whole screen, so the parts can never
 * disagree with each other. Every section degrades independently: a missing
 * calendar file must not blank out the account card.
 *
 * No framework, no build step, no dependencies.
 */
const $ = id => document.getElementById(id);

const state = { timer: null, intervalMs: 5000 };

const fmt = {
  money(value, currency = '') {
    if (value === null || value === undefined) return '—';
    const sign = value > 0 ? '+' : '';
    return `${sign}${Number(value).toFixed(2)}${currency ? ' ' + currency : ''}`;
  },
  num(value, digits = 2) {
    if (value === null || value === undefined) return '—';
    return Number(value).toFixed(digits);
  },
  ago(iso) {
    if (!iso) return '—';
    const secs = Math.round((Date.now() - Date.parse(iso)) / 1000);
    if (secs < 60) return `${secs}s ago`;
    if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
    return `${Math.round(secs / 3600)}h ago`;
  },
  duration(mins) {
    if (mins === null || mins === undefined) return '—';
    const m = Math.abs(mins);
    if (m < 60) return `${Math.round(m)}m`;
    const h = Math.floor(m / 60);
    return `${h}h ${Math.round(m % 60)}m`;
  },
};

const sign = value => (value > 0 ? 'up' : value < 0 ? 'down' : '');

function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/** A section that failed carries { success: false, error }. */
function sectionError(section) {
  return section && section.success === false ? section.error : null;
}

function pill(label, status, detail) {
  return `<span class="pill"><span class="dot ${status}"></span>${escapeHtml(label)}${
    detail ? ` <span class="muted">${escapeHtml(detail)}</span>` : ''}</span>`;
}

function renderConnection(data) {
  const health = data.health || {};
  const bits = [];

  bits.push(pill('Bridge', 'ok', 'reachable'));

  if (sectionError(health)) {
    bits.push(pill('MT5', 'bad', 'not connected'));
  } else {
    bits.push(pill('MT5', health.connected ? 'ok' : 'bad',
      health.account ? `${health.account.login} · ${health.account.server}` : ''));
    // An unknown broker clock offset means every UTC timestamp downstream is
    // null. Worth surfacing here rather than letting it look like missing data.
    const offset = health.server_utc_offset_sec;
    bits.push(pill('Clock',
      offset === null || offset === undefined ? 'warn' : 'ok',
      offset === null || offset === undefined
        ? 'offset unknown'
        : `UTC${offset >= 0 ? '+' : ''}${offset / 3600}h · ${health.server_utc_offset_source}`));
  }

  bits.push(pill('Updated', 'ok', fmt.ago(data.generated_at)));
  $('connection').innerHTML = bits.join('');

  if (sectionError(health)) {
    $('connection').innerHTML += `<div class="err">${escapeHtml(sectionError(health))}</div>`;
  }
}

function renderNews(blackout) {
  const el = $('news');
  const err = sectionError(blackout);
  if (err) {
    el.innerHTML = `<div class="banner"><strong>News</strong> <span class="muted">unavailable</span>
      <div class="err">${escapeHtml(err)}</div></div>`;
    return;
  }
  if (!blackout || blackout.blackout === null) {
    el.innerHTML = `<div class="banner"><strong>News</strong>
      <span class="muted">${escapeHtml(blackout?.error || 'no calendar loaded')}</span></div>`;
    return;
  }
  if (blackout.blackout) {
    const active = (blackout.active || [])
      .map(e => `${escapeHtml(e.event)} (${fmt.duration(e.minutes_until)} ${e.minutes_until >= 0 ? 'away' : 'ago'})`)
      .join(', ');
    el.innerHTML = `<div class="banner alert"><strong>News blackout</strong> — ${active}</div>`;
    return;
  }
  const next = blackout.next;
  el.innerHTML = `<div class="banner clear"><strong>Clear</strong> <span class="muted">${
    next ? `next: ${escapeHtml(next.event)} in ${fmt.duration(next.minutes_until)}` : 'no upcoming events'
  }</span></div>`;
}

function renderAccount(account) {
  const body = $('account').querySelector('.body');
  const err = sectionError(account);
  if (err) { body.innerHTML = `<div class="err">${escapeHtml(err)}</div>`; return; }

  const cur = account.currency || '';
  // equity - balance is floating P&L on open positions; showing it saves a
  // second lookup and explains any gap between the two numbers.
  const floating = (account.equity ?? 0) - (account.balance ?? 0);
  body.innerHTML = `<dl class="kv">
    <dt>Balance</dt><dd>${fmt.num(account.balance)} ${escapeHtml(cur)}</dd>
    <dt>Equity</dt><dd>${fmt.num(account.equity)} ${escapeHtml(cur)}</dd>
    <dt>Floating</dt><dd class="${sign(floating)}">${fmt.money(floating)}</dd>
    <dt>Margin</dt><dd>${fmt.num(account.margin)}</dd>
    <dt>Free margin</dt><dd>${fmt.num(account.margin_free)}</dd>
    <dt>Margin level</dt><dd>${account.margin_level ? fmt.num(account.margin_level) + '%' : '—'}</dd>
    <dt>Leverage</dt><dd>1:${escapeHtml(account.leverage ?? '—')}</dd>
  </dl>`;
}

function renderPnl(pnl) {
  const body = $('pnl').querySelector('.body');
  if (!pnl || pnl.error) {
    body.innerHTML = `<div class="err">${escapeHtml(pnl?.error || 'unavailable')}</div>`;
    return;
  }
  const row = (label, entry) => `<div class="pnl-row">
      <span>${label} <span class="muted">${entry.trades} trade${entry.trades === 1 ? '' : 's'}</span></span>
      <span class="v ${sign(entry.net)}">${fmt.money(entry.net)}</span>
    </div>`;
  body.innerHTML = row('Today', pnl.today) + row('This week', pnl.week) + row('This month', pnl.month)
    + `<div class="muted" style="margin-top:.5rem">Realised only — floating sits in the account card. UTC periods.</div>`;
}

function renderPositions(positions) {
  const body = $('positions').querySelector('.body');
  const err = sectionError(positions);
  if (err) { body.innerHTML = `<div class="err">${escapeHtml(err)}</div>`; return; }

  const rows = positions.positions || [];
  if (!rows.length) { body.innerHTML = '<div class="muted">No open positions.</div>'; return; }

  body.innerHTML = `<div class="scroll"><table>
    <thead><tr><th>Symbol</th><th>Side</th><th>Volume</th><th>Open</th><th>Current</th>
      <th>SL</th><th>TP</th><th>Profit</th><th>Opened (UTC)</th></tr></thead>
    <tbody>${rows.map(p => `<tr>
      <td>${escapeHtml(p.symbol)}</td>
      <td>${escapeHtml(p.type)}</td>
      <td>${fmt.num(p.volume)}</td>
      <td>${fmt.num(p.price_open, 5)}</td>
      <td>${fmt.num(p.price_current, 5)}</td>
      <td>${p.sl ? fmt.num(p.sl, 5) : '—'}</td>
      <td>${p.tp ? fmt.num(p.tp, 5) : '—'}</td>
      <td class="${sign(p.profit)}">${fmt.money(p.profit)}</td>
      <td>${escapeHtml(p.time_utc_iso || p.time_server_iso || '—')}</td>
    </tr>`).join('')}</tbody></table></div>`;
}

function renderOrders(orders) {
  const body = $('orders').querySelector('.body');
  const err = sectionError(orders);
  if (err) { body.innerHTML = `<div class="err">${escapeHtml(err)}</div>`; return; }

  const rows = orders.orders || [];
  if (!rows.length) { body.innerHTML = '<div class="muted">No pending orders.</div>'; return; }

  body.innerHTML = `<div class="scroll"><table>
    <thead><tr><th>Symbol</th><th>Type</th><th>Volume</th><th>Price</th>
      <th>SL</th><th>TP</th><th>Placed (UTC)</th></tr></thead>
    <tbody>${rows.map(o => `<tr>
      <td>${escapeHtml(o.symbol)}</td>
      <td>${escapeHtml(o.type)}</td>
      <td>${fmt.num(o.volume_initial ?? o.volume_current)}</td>
      <td>${fmt.num(o.price_open, 5)}</td>
      <td>${o.sl ? fmt.num(o.sl, 5) : '—'}</td>
      <td>${o.tp ? fmt.num(o.tp, 5) : '—'}</td>
      <td>${escapeHtml(o.time_utc_iso || o.time_server_iso || '—')}</td>
    </tr>`).join('')}</tbody></table></div>`;
}

async function refresh() {
  try {
    const res = await fetch('/overview', { cache: 'no-store' });
    const data = await res.json();
    renderConnection(data);
    renderNews(data.blackout);
    renderAccount(data.account);
    renderPnl(data.pnl);
    renderPositions(data.positions);
    renderOrders(data.orders);
    $('stamp').textContent = `updated ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    // The bridge serves this page, so a failure here means it died mid-session.
    $('connection').innerHTML = pill('Bridge', 'bad', 'not responding')
      + `<div class="err">${escapeHtml(err.message)} — is bridge.py still running?</div>`;
  }
}

function applyInterval() {
  if (state.timer) clearInterval(state.timer);
  state.intervalMs = Number($('interval').value);
  if (state.intervalMs > 0) state.timer = setInterval(refresh, state.intervalMs);
}

$('refresh').addEventListener('click', refresh);
$('interval').addEventListener('change', applyInterval);

refresh();
applyInterval();
