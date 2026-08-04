/**
 * Formatting helpers.
 *
 * Every one of these returns an em-dash for null or undefined rather than 0.
 * That distinction is load-bearing across this project: the API deliberately
 * returns null when a value cannot be computed (profit factor with no losses,
 * drawdown % with no starting balance), and rendering those as zero would turn
 * a stated absence back into a fabricated number.
 */
export const DASH = '—';

const isBlank = v => v === null || v === undefined || Number.isNaN(v);

export function money(value, { sign = true, digits = 2 } = {}) {
  if (isBlank(value)) return DASH;
  const n = Number(value);
  return `${sign && n > 0 ? '+' : ''}${n.toFixed(digits)}`;
}

export function num(value, digits = 2) {
  return isBlank(value) ? DASH : Number(value).toFixed(digits);
}

export function pct(value, digits = 1) {
  return isBlank(value) ? DASH : `${Number(value).toFixed(digits)}%`;
}

/** Seconds to a compact duration: 45s, 12.5m, 3.2h, 1.4d. */
export function hold(secs) {
  if (isBlank(secs)) return DASH;
  const s = Number(secs);
  if (s < 90) return `${Math.round(s)}s`;
  const m = s / 60;
  if (m < 90) return `${m.toFixed(1)}m`;
  const h = m / 60;
  if (h < 48) return `${h.toFixed(1)}h`;
  return `${(h / 24).toFixed(1)}d`;
}

/** '2026-08-03T14:22:10Z' -> '2026-08-03 14:22' */
export function stamp(iso) {
  if (!iso) return DASH;
  return String(iso).replace('T', ' ').replace(/:\d\d(\.\d+)?Z?$/, '');
}

export function shortDate(iso) {
  if (!iso) return DASH;
  return String(iso).slice(0, 10);
}

/** Tailwind text colour for a signed number. Zero is neutral, not green. */
export function tone(value) {
  if (isBlank(value) || Number(value) === 0) return 'text-ink';
  return Number(value) > 0 ? 'text-up' : 'text-down';
}

export function compact(value) {
  if (isBlank(value)) return DASH;
  const n = Math.abs(Number(value));
  if (n >= 1000) return `${(Number(value) / 1000).toFixed(1)}k`;
  return Number(value).toFixed(n < 10 ? 2 : 0);
}
