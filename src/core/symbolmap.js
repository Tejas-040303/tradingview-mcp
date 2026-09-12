/**
 * Broker symbol → TradingView symbol.
 *
 * The two systems do not agree on names and never will. XM calls spot gold
 * `GOLD.i#`; TradingView has no such ticker. This matters more than a naming
 * annoyance because of how it fails: a wrong mapping does not throw, it
 * produces a perfectly plausible chart of a *different instrument*, filed
 * against your trade. Nothing downstream can tell.
 *
 * That is the same failure class as an invented number, so it gets the same
 * rule — map from a table, or say you cannot.
 *
 * Two steps, and only the second can produce a symbol:
 *
 *   1. Strip the broker's decorations to find the root. `GOLD.i#` → `GOLD`.
 *      This is a guess about *formatting*, and it is reported back so a human
 *      can see what was stripped.
 *   2. Look the root up in a table. No heuristic ever promotes itself into a
 *      mapping here: an unrecognised root is an error with a hint, never a
 *      symbol that looks about right.
 *
 * The table below is a starting point, not an authority. Broker naming is
 * per-broker and TradingView's provider prefixes are a matter of which data
 * you have — so overrides always win, and the unmapped case tells the user
 * exactly where to add one.
 */
import { readFileSync, existsSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const REPO_ROOT = dirname(dirname(dirname(fileURLToPath(import.meta.url))));

/** Where a user's own mappings live, unless TV_SYMBOL_MAP points elsewhere. */
export const OVERRIDE_FILE = join(REPO_ROOT, 'symbol-map.json');

/**
 * Broker root → TradingView symbol.
 *
 * Entries are limited to instruments whose TradingView ticker is unambiguous.
 * Anything uncertain is deliberately absent: an unmapped symbol produces an
 * actionable error, and a wrong one produces a wrong picture.
 *
 * Index and oil entries point at TradingView's own index/CFD feeds, which are
 * *not* your broker's CFD. They track the same underlying and will not print
 * the same prices — see PRICE_SOURCE_NOTE.
 */
export const DEFAULT_TABLE = {
  // FX majors and the crosses brokers actually quote.
  EURUSD: 'OANDA:EURUSD', GBPUSD: 'OANDA:GBPUSD', USDJPY: 'OANDA:USDJPY',
  USDCHF: 'OANDA:USDCHF', AUDUSD: 'OANDA:AUDUSD', USDCAD: 'OANDA:USDCAD',
  NZDUSD: 'OANDA:NZDUSD', EURGBP: 'OANDA:EURGBP', EURJPY: 'OANDA:EURJPY',
  GBPJPY: 'OANDA:GBPJPY', AUDJPY: 'OANDA:AUDJPY', CHFJPY: 'OANDA:CHFJPY',
  CADJPY: 'OANDA:CADJPY', NZDJPY: 'OANDA:NZDJPY', EURAUD: 'OANDA:EURAUD',
  EURCAD: 'OANDA:EURCAD', EURCHF: 'OANDA:EURCHF', EURNZD: 'OANDA:EURNZD',
  GBPAUD: 'OANDA:GBPAUD', GBPCAD: 'OANDA:GBPCAD', GBPCHF: 'OANDA:GBPCHF',
  GBPNZD: 'OANDA:GBPNZD', AUDCAD: 'OANDA:AUDCAD', AUDCHF: 'OANDA:AUDCHF',
  AUDNZD: 'OANDA:AUDNZD', CADCHF: 'OANDA:CADCHF', NZDCAD: 'OANDA:NZDCAD',
  NZDCHF: 'OANDA:NZDCHF', USDSGD: 'OANDA:USDSGD', USDZAR: 'OANDA:USDZAR',
  USDMXN: 'OANDA:USDMXN', USDTRY: 'OANDA:USDTRY', USDSEK: 'OANDA:USDSEK',
  USDNOK: 'OANDA:USDNOK', USDPLN: 'OANDA:USDPLN', USDHKD: 'OANDA:USDHKD',

  // Metals. The roadmap's own example is GOLD.i# → OANDA:XAUUSD.
  XAUUSD: 'OANDA:XAUUSD', GOLD: 'OANDA:XAUUSD', SPOTGOLD: 'OANDA:XAUUSD',
  XAGUSD: 'OANDA:XAGUSD', SILVER: 'OANDA:XAGUSD', SPOTSILVER: 'OANDA:XAGUSD',
  XAUEUR: 'OANDA:XAUEUR', XAUGBP: 'OANDA:XAUGBP',

  // Energy.
  USOIL: 'TVC:USOIL', WTI: 'TVC:USOIL', CRUDE: 'TVC:USOIL',
  CRUDEOIL: 'TVC:USOIL', WTIUSD: 'TVC:USOIL',
  UKOIL: 'TVC:UKOIL', BRENT: 'TVC:UKOIL', BRENTUSD: 'TVC:UKOIL',

  // Indices. Broker CFDs track these; they are not the same instrument.
  US30: 'TVC:DJI', DJ30: 'TVC:DJI', WALL30: 'TVC:DJI', DOW: 'TVC:DJI',
  US500: 'TVC:SPX', SPX500: 'TVC:SPX', SP500: 'TVC:SPX',
  US100: 'TVC:NDX', NAS100: 'TVC:NDX', USTEC: 'TVC:NDX', NASDAQ100: 'TVC:NDX',
  GER40: 'TVC:DAX', DE40: 'TVC:DAX', GER30: 'TVC:DAX', DAX: 'TVC:DAX',
  UK100: 'TVC:UKX', FTSE100: 'TVC:UKX',
  JP225: 'TVC:NI225', JPN225: 'TVC:NI225',
  DXY: 'TVC:DXY', USDX: 'TVC:DXY',

  // Crypto CFDs.
  BTCUSD: 'BITSTAMP:BTCUSD', ETHUSD: 'BITSTAMP:ETHUSD',
};

/**
 * Why every capture is labelled, even a correctly mapped one.
 *
 * TradingView's feed is a different venue from your broker's. Levels drawn at
 * broker prices will sit slightly off the TradingView candles, and on index
 * and oil CFDs the gap is structural rather than noise. Read the picture for
 * structure — where the entry sat relative to the swing — not to check a fill.
 */
export const PRICE_SOURCE_NOTE =
  'TradingView prices are a different feed from your broker. Levels are drawn at ' +
  'broker prices and will not sit exactly on TradingView candles.';

/**
 * Strip a broker's decorations down to the instrument root.
 *
 * Brokers append account-type and feed markers to a common root: `GOLD.i#`,
 * `EURUSD.pro`, `XAUUSD_ecn`, `US30.cash`, `EURUSDm`. None of that changes
 * which instrument it is.
 *
 * The micro/cent suffix is checked *before* uppercasing, because lowercase
 * against an uppercase root is the only reliable signal that the trailing
 * letter is a suffix at all — `EURUSDm` is micro EURUSD, and `EURUSD` ends in
 * a letter too.
 */
export function brokerRoot(raw) {
  if (raw === null || raw === undefined) return null;
  let s = String(raw).trim();
  if (!s) return null;

  // A few brokers decorate the front instead: #EURUSD, .US30.
  s = s.replace(/^[#._-]+/, '');

  // Micro / cent account suffix, lowercase against an uppercase root.
  s = s.replace(/^([A-Z0-9]{3,})(?:micro|m|c)$/, '$1');

  s = s.toUpperCase();

  // Trailing markers, which stack: `.i#`, `_ECN.RAW`, `-5`.
  let previous;
  do {
    previous = s;
    s = s.replace(/#+$/, '');
    s = s.replace(/[._-][A-Z0-9]{0,5}$/, '');
  } while (s !== previous && s.length > 0);

  return s || null;
}

/** Keys are compared as upper-case, so a user's file need not shout. */
function upperKeys(table) {
  const out = {};
  for (const [key, value] of Object.entries(table || {})) {
    if (typeof value !== 'string' || !value.trim()) continue;
    out[String(key).trim().toUpperCase()] = value.trim();
  }
  return out;
}

/**
 * Map one broker symbol to a TradingView symbol.
 *
 * Returns `{ success: false, symbol: null }` with a reason and a hint when the
 * root is not in the table. That is the important path: the alternative — a
 * near-enough guess — is a chart of the wrong instrument labelled with your
 * position id, and nothing downstream could detect it.
 *
 * Overrides may be keyed on the exact broker symbol or on the stripped root.
 * The exact name wins, being the more specific statement: a broker with two
 * feeds of the same root can point them at different charts.
 */
export function mapSymbol(brokerSymbol, { table, overrides } = {}) {
  const broker = brokerSymbol === null || brokerSymbol === undefined
    ? null
    : String(brokerSymbol).trim();

  if (!broker) {
    return {
      success: false, broker: null, root: null, symbol: null, via: null,
      reason: 'No broker symbol given.',
      hint: 'Pass the symbol from the trade, or set symbol= to a TradingView ticker directly.',
    };
  }

  const root = brokerRoot(broker);
  const base = upperKeys(table || DEFAULT_TABLE);
  const user = upperKeys(overrides);
  const exact = broker.toUpperCase();

  if (user[exact]) {
    return { success: true, broker, root, symbol: user[exact], via: 'override:exact' };
  }
  if (root && user[root]) {
    return { success: true, broker, root, symbol: user[root], via: 'override:root' };
  }
  if (base[exact]) {
    return { success: true, broker, root, symbol: base[exact], via: 'table:exact' };
  }
  if (root && base[root]) {
    return { success: true, broker, root, symbol: base[root], via: 'table:root' };
  }

  return {
    success: false, broker, root, symbol: null, via: null,
    reason: `No TradingView symbol is mapped for broker symbol "${broker}"` +
      (root && root !== exact ? ` (root "${root}")` : '') + '.',
    hint: `Pass symbol="EXCHANGE:TICKER" for this capture, or add {"${root || broker}": ` +
      `"EXCHANGE:TICKER"} to ${OVERRIDE_FILE} (or the file TV_SYMBOL_MAP points at). ` +
      'Guessing is deliberately not attempted — a wrong mapping produces a ' +
      'plausible chart of the wrong instrument.',
  };
}

/**
 * Read the user's own mappings, if they keep any.
 *
 * Absent is fine — the file is optional. Present but unreadable is not: a
 * malformed override file that silently fell back to the shipped table would
 * mean charting whatever the table happened to say, which is the exact
 * scenario the overrides exist to correct.
 */
export function loadOverrides({ env = process.env, readFile = readFileSync,
  exists = existsSync } = {}) {
  const path = env.TV_SYMBOL_MAP || OVERRIDE_FILE;
  if (!exists(path)) return { path: null, overrides: {} };

  let parsed;
  try {
    parsed = JSON.parse(readFile(path, 'utf8'));
  } catch (err) {
    throw new Error(
      `Symbol map at ${path} is not valid JSON (${err.message}). ` +
      'Fix or remove it — ignoring it would silently chart whatever the ' +
      'built-in table says, which is what the file exists to override.');
  }

  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(
      `Symbol map at ${path} must be a JSON object of {"BROKER_SYMBOL": "EXCHANGE:TICKER"}.`);
  }

  return { path, overrides: parsed };
}
