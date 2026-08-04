/**
 * History and analysis view.
 *
 * Reads /history, which does the filtering and the arithmetic server-side. The
 * page draws; it does not compute. That split is deliberate — a second
 * implementation of win rate or expectancy in JavaScript would eventually
 * disagree with the Python one, and a table that contradicts the tile above it
 * is worse than no tile.
 *
 * No framework, no build step, no dependencies.
 */
const $ = id => document.getElementById(id);

// 50 rows keeps the page scannable; a few hundred trades in one table turns
// every other panel into something you have to scroll past to reach.
const state = { data: null, offset: 0, limit: 50, loading: false };

const fmt = {
  money(value) {
    if (value === null || value === undefined) return '—';
    const sign = value > 0 ? '+' : '';
    return `${sign}${Number(value).toFixed(2)}`;
  },
  num(value, digits = 2) {
    if (value === null || value === undefined) return '—';
    return Number(value).toFixed(digits);
  },
  pct(value) {
    return value === null || value === undefined ? '—' : `${Number(value).toFixed(1)}%`;
  },
  /** Seconds to a compact human duration. */
  hold(secs) {
    if (secs === null || secs === undefined) return '—';
    if (secs < 90) return `${Math.round(secs)}s`;
    const mins = secs / 60;
    if (mins < 90) return `${mins.toFixed(1)}m`;
    const hours = mins / 60;
    if (hours < 48) return `${hours.toFixed(1)}h`;
    return `${(hours / 24).toFixed(1)}d`;
  },
  /** '2026-08-03T14:22:10Z' -> '2026-08-03 14:22' */
  stamp(iso) {
    return iso ? String(iso).replace('T', ' ').replace(/(:\d\d)Z?$/, '') : '—';
  },
};

const sign = value => (value > 0 ? 'up' : value < 0 ? 'down' : '');

function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

const DAY = 86400;

/** Build the query string from the filter controls. */
function query() {
  const params = new URLSearchParams();
  const now = Math.floor(Date.now() / 1000);
  const period = $('period').value;

  if (period === 'custom') {
    const from = $('from').value, to = $('to').value;
    if (from) params.set('from', String(Math.floor(Date.parse(`${from}T00:00:00Z`) / 1000)));
    if (to) params.set('to', String(Math.floor(Date.parse(`${to}T23:59:59Z`) / 1000)));
  } else if (period === 'all') {
    // MT5 accepts an epoch start; the terminal simply returns what it has.
    params.set('from', '0');
    params.set('to', String(now));
  } else {
    params.set('from', String(now - Number(period) * DAY));
    params.set('to', String(now));
  }

  for (const id of ['filter_symbol', 'direction', 'exit_reason', 'min_net',
                    'max_net', 'starting_balance']) {
    const value = $(id).value.trim();
    if (value) params.set(id, value);
  }
  if ($('closed_only').checked) params.set('closed_only', 'true');

  params.set('limit', String(state.limit));
  params.set('offset', String(state.offset));
  return params.toString();
}

/** Repopulate a select, keeping the current choice if it still exists. */
function fillOptions(id, values, allLabel) {
  const el = $(id);
  const chosen = el.value;
  el.innerHTML = `<option value="">${allLabel}</option>`
    + values.map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join('');
  if (values.includes(chosen)) el.value = chosen;
}

function tile(label, value, cls = '', note = '') {
  return `<div class="tile">
    <div class="tile-label">${escapeHtml(label)}</div>
    <div class="tile-value ${cls}">${value}</div>
    ${note ? `<div class="tile-note">${escapeHtml(note)}</div>` : ''}
  </div>`;
}

function renderStats(data) {
  const h = data.headline;
  if (!h) {
    $('stats').innerHTML = '<div class="card muted">No closed trades in this selection.</div>';
    return;
  }
  const dd = data.drawdown || {};
  const st = data.streaks || {};

  $('stats').innerHTML = [
    tile('Net P&L', fmt.money(h.net), sign(h.net)),
    tile('Trades', h.trades, '', h.open_trades ? `${h.open_trades} still open` : ''),
    tile('Win rate', fmt.pct(h.win_rate_pct), '', `${h.wins}W / ${h.losses}L`),
    tile('Expectancy', fmt.money(h.expectancy), sign(h.expectancy), 'per trade'),
    // Below 1.0 means the losses outweigh the wins, whatever the win rate says.
    tile('Profit factor', fmt.num(h.profit_factor), h.profit_factor >= 1 ? 'up' : 'down',
      'gross win / gross loss'),
    tile('Payoff', fmt.num(h.payoff_ratio), '', 'avg win / avg loss'),
    tile('Max drawdown', fmt.money(-Math.abs(dd.max_drawdown ?? 0)), 'down',
      dd.max_drawdown_pct === null || dd.max_drawdown_pct === undefined
        ? 'set starting balance for %'
        : `${fmt.num(dd.max_drawdown_pct, 1)}%${dd.still_in_drawdown ? ' · not recovered' : ''}`),
    tile('Avg hold', fmt.hold(h.avg_duration_sec), '',
      `win ${fmt.hold(h.avg_win_duration_sec)} · loss ${fmt.hold(h.avg_loss_duration_sec)}`),
    tile('Best / worst', `${fmt.money(h.best)} / ${fmt.money(h.worst)}`),
    tile('Longest streak', `${st.longest_win_streak ?? 0}W / ${st.longest_loss_streak ?? 0}L`, '',
      st.current_streak_kind ? `now ${st.current_streak} ${st.current_streak_kind}` : ''),
  ].join('');
}

/**
 * Equity curve as inline SVG.
 *
 * Drawn by hand rather than pulled from a charting library: one polyline and a
 * zero line is the whole requirement, and the page has no build step to bundle
 * a dependency into.
 */
function renderCurve(points, hasBalance) {
  const el = $('curve');
  if (!points || points.length < 2) {
    el.innerHTML = '<div class="muted">Not enough closed trades to plot a curve.</div>';
    $('curve-note').textContent = '';
    return;
  }

  const key = hasBalance ? 'balance' : 'cumulative';
  const values = points.map(p => p[key]);
  const W = 1000, H = 260, pad = { top: 12, right: 12, bottom: 22, left: 56 };
  const min = Math.min(...values, hasBalance ? Math.min(...values) : 0);
  const max = Math.max(...values, hasBalance ? Math.max(...values) : 0);
  const span = (max - min) || 1;

  const x = i => pad.left + (i / (points.length - 1)) * (W - pad.left - pad.right);
  const y = v => pad.top + (1 - (v - min) / span) * (H - pad.top - pad.bottom);

  const line = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`).join('');
  const area = `${line}L${x(points.length - 1).toFixed(1)},${y(min).toFixed(1)}`
    + `L${x(0).toFixed(1)},${y(min).toFixed(1)}Z`;
  const last = values[values.length - 1];
  const zero = hasBalance ? null : y(0);

  // Five evenly spaced gridlines; labels sit outside the plot area.
  const ticks = Array.from({ length: 5 }, (_, i) => min + (span * i) / 4);

  $('curve-note').textContent = hasBalance
    ? 'account balance' : 'cumulative realised P&L';

  el.innerHTML = `<div class="scroll"><svg viewBox="0 0 ${W} ${H}" class="chart"
      preserveAspectRatio="none" role="img" aria-label="Equity curve">
    ${ticks.map(v => `<line x1="${pad.left}" x2="${W - pad.right}"
        y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" class="grid"/>
      <text x="${pad.left - 8}" y="${(y(v) + 4).toFixed(1)}" class="axis" text-anchor="end"
        >${v.toFixed(0)}</text>`).join('')}
    ${zero !== null && zero >= pad.top && zero <= H - pad.bottom
      ? `<line x1="${pad.left}" x2="${W - pad.right}" y1="${zero.toFixed(1)}"
           y2="${zero.toFixed(1)}" class="zero"/>` : ''}
    <path d="${area}" class="area ${last >= (hasBalance ? values[0] : 0) ? 'pos' : 'neg'}"/>
    <path d="${line}" class="line ${last >= (hasBalance ? values[0] : 0) ? 'pos' : 'neg'}"/>
  </svg></div>
  <div class="muted">${points.length} closed trades ·
    ${escapeHtml(fmt.stamp(points[0].time))} → ${escapeHtml(fmt.stamp(points[points.length - 1].time))}</div>`;
}

function renderGroup(key, groups) {
  const el = $(`group-${key}`);
  const rows = Object.entries(groups?.[key] || {});
  if (!rows.length) { el.innerHTML = '<div class="muted">No data.</div>'; return; }

  // Worst first: the losing bucket is the one worth looking at.
  rows.sort((a, b) => a[1].net - b[1].net);

  el.innerHTML = `<div class="scroll"><table>
    <thead><tr><th>${escapeHtml(key.replace('_', ' '))}</th><th>N</th><th>Win%</th>
      <th>Net</th><th>Avg</th><th>Hold</th></tr></thead>
    <tbody>${rows.map(([label, s]) => `<tr>
      <td>${escapeHtml(label)}</td>
      <td>${s.trades}</td>
      <td>${fmt.pct(s.win_rate_pct)}</td>
      <td class="${sign(s.net)}">${fmt.money(s.net)}</td>
      <td class="${sign(s.avg)}">${fmt.money(s.avg)}</td>
      <td>${fmt.hold(s.avg_duration_sec)}</td>
    </tr>`).join('')}</tbody></table></div>`;
}

function renderDistribution(buckets) {
  const el = $('distribution');
  if (!buckets || !buckets.length) { el.innerHTML = '<div class="muted">No data.</div>'; return; }

  const peak = Math.max(...buckets.map(b => b.count)) || 1;
  el.innerHTML = `<div class="hist">${buckets.map(b => `
    <div class="hist-col" title="${b.count} trade${b.count === 1 ? '' : 's'} between ${b.from} and ${b.to} (net ${b.net})">
      <div class="hist-count">${b.count || ''}</div>
      <div class="hist-bar ${b.to <= 0 ? 'down' : 'up'}"
           style="height:${Math.max(2, (b.count / peak) * 100)}%"></div>
      <div class="hist-label">${b.from}</div>
    </div>`).join('')}</div>
  <div class="muted">Per-trade net, bucketed. Bars left of zero are losses.</div>`;
}

function renderTrades(data) {
  const el = $('trades');
  const rows = data.trades || [];
  if (!rows.length) { el.innerHTML = '<div class="muted">No trades match these filters.</div>'; return; }

  el.innerHTML = `<div class="scroll"><table>
    <thead><tr><th>Opened (UTC)</th><th>Symbol</th><th>Side</th><th>Vol</th>
      <th>Entry</th><th>Exit</th><th>Hold</th><th>Exit reason</th><th>Net</th></tr></thead>
    <tbody>${rows.map(t => `<tr class="${t.open ? 'open-row' : ''}">
      <td>${escapeHtml(fmt.stamp(t.opened) )}</td>
      <td>${escapeHtml(t.symbol ?? '—')}</td>
      <td>${escapeHtml(t.direction ?? '—')}</td>
      <td>${fmt.num(t.volume)}</td>
      <td>${t.entry_missing ? '<span class="muted">before window</span>' : fmt.num(t.entry_price, 5)}</td>
      <td>${t.open ? '<span class="muted">open</span>' : fmt.num(t.exit_price, 5)}</td>
      <td>${fmt.hold(t.duration_sec)}</td>
      <td>${escapeHtml(t.exit_reason ?? '—')}${t.partial_closes
            ? ` <span class="muted">+${t.partial_closes} partial</span>` : ''}</td>
      <td class="${t.open ? '' : sign(t.net)}">${t.open ? '—' : fmt.money(t.net)}</td>
    </tr>`).join('')}</tbody></table></div>`;
}

function renderPager(page) {
  const el = $('pager');
  if (!page || page.total <= page.limit) { el.innerHTML = ''; return; }
  const shown = `${page.offset + 1}–${Math.min(page.offset + page.limit, page.total)} of ${page.total}`;
  el.innerHTML = `<button type="button" id="prev" ${page.offset <= 0 ? 'disabled' : ''}>← Newer</button>
    <span class="muted">${shown}</span>
    <button type="button" id="next" ${page.offset + page.limit >= page.total ? 'disabled' : ''}>Older →</button>`;
  const step = dir => { state.offset = Math.max(0, state.offset + dir * state.limit); load(); };
  $('prev')?.addEventListener('click', () => step(-1));
  $('next')?.addEventListener('click', () => step(1));
}

async function load() {
  if (state.loading) return;
  state.loading = true;
  $('stamp').textContent = 'loading…';
  try {
    const res = await fetch(`/history?${query()}`, { cache: 'no-store' });
    const data = await res.json();
    if (!res.ok || data.success === false) {
      throw new Error(data.error || `HTTP ${res.status}`);
    }
    state.data = data;

    fillOptions('filter_symbol', data.facets?.symbols || [], 'All');
    fillOptions('exit_reason', data.facets?.exit_reasons || [], 'All');

    const matched = data.page?.total ?? 0;
    $('matched').textContent = `${matched} of ${data.total_trades} trades match`;

    renderStats(data);
    renderCurve(data.equity_curve, Boolean($('starting_balance').value.trim()));
    for (const key of Object.keys(data.groups || {})) renderGroup(key, data.groups);
    renderDistribution(data.distribution);
    renderTrades(data);
    renderPager(data.page);
    $('table-note').textContent = data.headline?.entry_missing
      ? `${data.headline.entry_missing} opened before this window` : '';
    $('stamp').textContent = `updated ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    $('stats').innerHTML = `<div class="card"><div class="err">${escapeHtml(err.message)}
      — is bridge.py running and MT5 connected?</div></div>`;
    $('stamp').textContent = 'failed';
  } finally {
    state.loading = false;
  }
}

/** Any filter change resets paging: page 3 of the old filter is meaningless. */
function reload() { state.offset = 0; load(); }

$('period').addEventListener('change', () => {
  const custom = $('period').value === 'custom';
  $('from-wrap').hidden = !custom;
  $('to-wrap').hidden = !custom;
  if (!custom) reload();
});

for (const id of ['from', 'to', 'filter_symbol', 'direction', 'exit_reason',
                  'min_net', 'max_net', 'starting_balance', 'closed_only']) {
  $(id).addEventListener('change', reload);
}

$('refresh').addEventListener('click', load);
$('reset').addEventListener('click', () => {
  for (const id of ['filter_symbol', 'direction', 'exit_reason', 'min_net',
                    'max_net', 'starting_balance', 'from', 'to']) $(id).value = '';
  $('closed_only').checked = false;
  $('period').value = '30';
  $('from-wrap').hidden = true;
  $('to-wrap').hidden = true;
  reload();
});

load();
