import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export const cn = (...parts) => twMerge(clsx(parts));

export const DAY = 86400;

export const PERIODS = [
  { value: 'today', label: 'Today' },
  { value: '7', label: 'Last 7 days' },
  { value: '30', label: 'Last 30 days' },
  { value: '90', label: 'Last 90 days' },
  { value: 'mtd', label: 'Month to date' },
  { value: 'ytd', label: 'Year to date' },
  { value: 'all', label: 'All time' },
  { value: 'custom', label: 'Custom…' },
];

/** UTC second bounds for a period preset. */
export function periodBounds(period, custom = {}) {
  const now = Math.floor(Date.now() / 1000);
  const utcMidnight = new Date();
  utcMidnight.setUTCHours(0, 0, 0, 0);
  const today = Math.floor(utcMidnight.getTime() / 1000);

  switch (period) {
    case 'today':
      return { from: today, to: now };
    case 'mtd': {
      const d = new Date();
      return { from: Math.floor(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1) / 1000), to: now };
    }
    case 'ytd': {
      const d = new Date();
      return { from: Math.floor(Date.UTC(d.getUTCFullYear(), 0, 1) / 1000), to: now };
    }
    case 'all':
      // The terminal simply returns whatever history it holds.
      return { from: 0, to: now };
    case 'custom':
      return {
        from: custom.from ? Math.floor(Date.parse(`${custom.from}T00:00:00Z`) / 1000) : 0,
        to: custom.to ? Math.floor(Date.parse(`${custom.to}T23:59:59Z`) / 1000) : now,
      };
    default:
      return { from: now - Number(period) * DAY, to: now };
  }
}

/**
 * Build the /history query.
 *
 * Filtering is server-side by design: the API owns win rate, expectancy and
 * drawdown, and recomputing any of them here would eventually disagree with
 * the table beneath it.
 */
export function buildQuery(filters) {
  const { from, to } = periodBounds(filters.period, filters);
  const params = new URLSearchParams({
    from: String(from),
    to: String(to),
    advanced: 'true',
    limit: String(filters.limit ?? 100),
    offset: String(filters.offset ?? 0),
  });

  for (const key of ['filter_symbol', 'direction', 'exit_reason', 'min_net',
    'max_net', 'starting_balance']) {
    const value = filters[key];
    if (value !== undefined && value !== null && String(value).trim() !== '') {
      params.set(key, String(value).trim());
    }
  }
  if (filters.closed_only) params.set('closed_only', 'true');
  if (filters.heatmaps) params.set('heatmaps', filters.heatmaps.join(','));
  return params.toString();
}

export async function fetchHistory(filters, signal) {
  const res = await fetch(`/history?${buildQuery(filters)}`, { cache: 'no-store', signal });
  let body;
  try {
    body = await res.json();
  } catch {
    throw new Error(`Bridge returned ${res.status} with a non-JSON body`);
  }
  if (!res.ok || body.success === false) {
    throw new Error(body.error || `Bridge returned ${res.status}`);
  }
  return body;
}

export async function fetchOverview(signal) {
  const res = await fetch('/overview', { cache: 'no-store', signal });
  if (!res.ok) throw new Error(`Bridge returned ${res.status}`);
  return res.json();
}

export const SEVERITY = {
  critical: { label: 'Critical', dot: 'bg-down', ring: 'ring-down/30', text: 'text-down' },
  warning: { label: 'Warning', dot: 'bg-warn', ring: 'ring-warn/30', text: 'text-warn' },
  suggestion: { label: 'Suggestion', dot: 'bg-info', ring: 'ring-info/30', text: 'text-info' },
  opportunity: { label: 'Opportunity', dot: 'bg-up', ring: 'ring-up/30', text: 'text-up' },
};

export function downloadFile(name, mime, text) {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const link = Object.assign(document.createElement('a'), { href: url, download: name });
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function toCsv(rows, columns) {
  const escape = v => {
    const s = v === null || v === undefined ? '' : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [columns.join(','),
    ...rows.map(r => columns.map(c => escape(r[c])).join(','))].join('\n');
}
