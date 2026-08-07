"""
Read-only HTTP bridge in front of a MetaTrader 5 terminal.

MetaTrader5 is a Windows-only Python package with no Node binding, so the Node
MCP layer talks to this instead. Deliberately narrow:

  * binds 127.0.0.1 only — never a routable interface
  * answers GET and nothing else; any other verb gets 405
  * exposes no route that can place, modify or cancel an order

Run:
    python bridge.py                 # 127.0.0.1:8765
    python bridge.py --port 9100
    MT5_BRIDGE_TOKEN=secret python bridge.py    # require X-Bridge-Token

Stdlib only — no Flask, no FastAPI.
"""
import argparse
import json
import os
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import mt5_client
import excursion
import paper
import reconcile
import simulate
import strategy
import sweep
import sweep_backtest
import sweep_strategy
import sweep_walkforward
import insights as insight_rules
import stops
import stopsize
from advanced import (behaviour, daily_pnl, heatmap, holding_time_analysis,
                      kelly_fraction, monte_carlo, recovery_factor,
                      risk_adjusted, size_analysis)
from analytics import (analyze, analyze_trades, filter_trades, pair_trades,
                       period_bounds, realized_pnl, summarize_trades)
from mt5_client import Mt5Error
from normalize import blackout_status, filter_calendar, iso, paginate

DEFAULT_PORT = 8765
DASHBOARD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dashboard')


def _one(params, key, default=None):
    values = params.get(key)
    return values[0] if values else default


def _csv(params, key):
    raw = _one(params, key)
    if not raw:
        return None
    return [part.strip() for part in raw.split(',') if part.strip()]


def _truthy(value):
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _strategy_config(params):
    """
    A strategy config from query parameters.

    Every field is optional and falls back to `strategy.DEFAULT_CONFIG`, so a
    bare `/backtest?symbol=GOLD.i%23` runs the current best guess and each
    parameter can be varied one at a time from a URL. `trail=none` disables the
    breakeven trail — the control case, and the reason this is spelled rather
    than assumed.
    """
    conditions = _csv(params, 'conditions')
    cfg = {'entry': {}, 'stop': {}, 'size': {}, 'manage': {}, 'target': {}}

    if conditions:
        cfg['entry']['conditions'] = {
            name: {'on': name in conditions} for name in strategy.CONDITIONS}
    for name in _csv(params, 'required') or []:
        cfg['entry'].setdefault('conditions', {}).setdefault(name, {})['required'] = True

    if _one(params, 'mode'):
        cfg['entry']['mode'] = _one(params, 'mode')
    if _one(params, 'min_conditions'):
        cfg['entry']['min_conditions'] = int(_one(params, 'min_conditions'))
    if _one(params, 'confirmation'):
        cfg['entry']['confirmation'] = {'type': _one(params, 'confirmation')}
    if _one(params, 'buffer_pips'):
        cfg['stop']['buffer_pips'] = float(_one(params, 'buffer_pips'))
    if _one(params, 'risk_pct'):
        cfg['size']['risk_pct'] = float(_one(params, 'risk_pct'))
    if _one(params, 'max_risk_pct'):
        cfg['size']['max_risk_pct'] = float(_one(params, 'max_risk_pct'))
    if _one(params, 'target_r'):
        cfg['target']['r'] = float(_one(params, 'target_r'))
    if _one(params, 'partial_pct'):
        cfg['manage']['partial_pct'] = float(_one(params, 'partial_pct'))
    if _one(params, 'partial_at_r'):
        cfg['manage']['partial_at_r'] = float(_one(params, 'partial_at_r'))

    trail = _one(params, 'trail')
    if trail is not None:
        cfg['manage']['trail_to_be_at_r'] = (
            None if trail.strip().lower() in ('none', 'off', '') else float(trail))

    return {k: v for k, v in cfg.items() if v}


def _axis_value(raw):
    """
    One value on a sweep axis.

    'none' has to survive as null rather than becoming the string 'none' or the
    number 0 — it is the no-trail control case, and losing it removes the
    comparison the sweep exists to make.
    """
    text = raw.strip()
    lowered = text.lower()
    if lowered in ('none', 'off', 'null'):
        return None
    if lowered in ('true', 'false'):
        return lowered == 'true'
    try:
        return float(text) if '.' in text else int(text)
    except ValueError:
        return text


def _strategy_bars(params):
    """Bars for a strategy run, with the symbol and config it was asked for."""
    symbol = _one(params, 'symbol')
    if not symbol:
        raise ValueError('symbol is required')

    config = _strategy_config(params)
    problems = strategy.validate(config)
    if problems:
        # Refuse rather than silently running a config that cannot trigger.
        raise ValueError('; '.join(problems))

    data = mt5_client.bars(symbol,
                           timeframe=_one(params, 'timeframe', '5'),
                           count=int(_one(params, 'count', 1000)))
    return symbol, config, data


def _sweep_config(params):
    """
    Strategy 1's config from query parameters.

    Separate from `_strategy_config` because strategy 1 is a different shape —
    two timeframes rather than one, a structural target rather than a fixed R,
    and buffers denominated in price. Sharing one parser would mean one of the
    two silently ignoring half its fields.
    """
    cfg = {'entry': {}, 'stop': {}, 'target': {}, 'manage': {}, 'size': {}}

    if _one(params, 'symbol'):
        cfg['symbol'] = _one(params, 'symbol')
    if _csv(params, 'sweep_timeframes'):
        cfg['sweep_timeframes'] = _csv(params, 'sweep_timeframes')
    if _one(params, 'entry_timeframe'):
        cfg['entry_timeframe'] = _one(params, 'entry_timeframe')

    for key, cast in (('trigger', str), ('wait_bars', int), ('max_uses', int),
                      ('swing_left', int), ('swing_right', int)):
        if _one(params, key):
            cfg['entry'][key] = cast(_one(params, key))
    # 'none' has to survive as null: with no fallback the confirmation candle
    # is the only way in, which is the control case for asking whether MSS
    # adds anything.
    fallback = _one(params, 'fallback')
    if fallback is not None:
        cfg['entry']['fallback'] = (None if fallback.strip().lower() in
                                    ('none', 'off', '') else fallback)

    if _one(params, 'buffer_price'):
        cfg['stop']['buffer_price'] = float(_one(params, 'buffer_price'))
    for key in ('min_r', 'fixed_r'):
        if _one(params, key):
            cfg['target'][key] = float(_one(params, key))
    if _one(params, 'target_mode'):
        cfg['target']['mode'] = _one(params, 'target_mode')
    for key in ('partial_pct', 'partial_at_r', 'trail_at_r', 'trail_buffer_price'):
        if _one(params, key):
            cfg['manage'][key] = float(_one(params, key))
    if _one(params, 'size_mode'):
        cfg['size']['mode'] = _one(params, 'size_mode')
    for key in ('risk_pct', 'max_risk_pct', 'fixed_lot', 'max_lot'):
        if _one(params, key):
            cfg['size'][key] = float(_one(params, key))

    return {k: v for k, v in cfg.items() if v}


# Extra bars fetched on each sweep timeframe beyond the entry window: swing
# detection needs history before the first entry bar, and a level swept an hour
# ago is the whole point of a higher timeframe.
SWEEP_LOOKBACK_BARS = 80


def _sweep_bars(params):
    """
    Bars at every timeframe strategy 1 needs, over one aligned window.

    The entry timeframe sets the window; each sweep timeframe is then fetched
    with just enough bars to cover it plus a lookback. Fetching a fixed count
    per timeframe instead would pull years of 4H bars against days of 3M ones,
    and the ancient sweeps would all trigger against the first entry bars —
    signals generated by a mismatch in the request rather than by the market.
    """
    symbol = _one(params, 'symbol')
    if not symbol:
        raise ValueError('symbol is required')

    config = _sweep_config(params)
    problems = sweep_strategy.validate(config)
    if problems:
        raise ValueError('; '.join(problems))

    cfg = sweep_strategy.merged(config)
    entry_tf = str(cfg['entry_timeframe'])
    count = max(1, min(int(_one(params, 'count', 3000)), 5000))

    entry = mt5_client.bars(symbol, timeframe=entry_tf, count=count)
    rows = entry.get('bars') or []
    if not rows:
        raise ValueError(f'no {entry_tf}-minute bars returned for {symbol}')

    bars_by_tf = {entry_tf: rows}
    span = rows[-1]['time_utc'] - rows[0]['time_utc']
    unavailable = {}

    for timeframe in cfg['sweep_timeframes']:
        timeframe = str(timeframe)
        if timeframe in bars_by_tf:
            continue
        period = sweep_strategy.period(timeframe)
        needed = min(5000, int(span / period) + SWEEP_LOOKBACK_BARS)
        try:
            data = mt5_client.bars(symbol, timeframe=timeframe, count=needed)
            bars_by_tf[timeframe] = data.get('bars') or []
        except Mt5Error as exc:
            # One timeframe missing is a narrower strategy, not a failed
            # request — say which, rather than returning nothing.
            unavailable[timeframe] = str(exc)

    window = {'from': iso(rows[0]['time_utc']), 'to': iso(rows[-1]['time_utc'])}
    return symbol, config, bars_by_tf, window, unavailable or None


def _section(fn):
    """
    Run one part of the overview, capturing failure rather than propagating it.

    The status view must degrade in pieces: a missing calendar file should not
    blank out the account card, and MT5 being unreachable should not hide the
    fact that the bridge itself is fine.
    """
    try:
        return fn()
    except Mt5Error as exc:
        return {'success': False, 'error': str(exc)}
    except Exception as exc:  # pragma: no cover - defensive
        return {'success': False, 'error': f'{type(exc).__name__}: {exc}'}


def overview(params):
    """
    Everything the status view needs, in one request.

    Six separate polls for one screen is wasteful and gives a torn picture when
    the parts disagree; this reads once and stamps a single generated_at.
    """
    now = int(time.time())
    bounds = period_bounds(now)
    month_from, _ = bounds['month']

    deals_rows = []
    deals_error = None
    try:
        deals_rows = mt5_client.deals(
            from_ts=month_from, to_ts=now, summary=False, limit=1_000_000,
        ).get('deals') or []
    except Mt5Error as exc:
        deals_error = str(exc)

    pnl = {name: realized_pnl(deals_rows, start, end)
           for name, (start, end) in bounds.items()}
    if deals_error:
        pnl = {'error': deals_error}

    blackout = _section(lambda: blackout_status(
        mt5_client.calendar(path=_one(params, 'file'))['events'],
        now_ts=now,
        before_min=int(_one(params, 'before_min', 15)),
        after_min=int(_one(params, 'after_min', 15)),
        currencies=_csv(params, 'currencies') or ['USD'],
        min_importance=_one(params, 'min_importance', 'high'),
    ))

    return {
        'success': True,
        'generated_at': iso(now),
        'health': _section(mt5_client.health),
        'account': _section(mt5_client.account),
        'positions': _section(mt5_client.positions),
        'orders': _section(mt5_client.orders),
        'pnl': pnl,
        'blackout': blackout,
    }


def _number(params, key):
    raw = _one(params, key)
    if raw is None or str(raw).strip() == '':
        return None
    return float(raw)


DEFAULT_HEATMAPS = ('weekday:session', 'hour:direction', 'weekday:symbol')


def excursions(trades, timeframe='1'):
    """
    MAE, MFE and post-exit behaviour, fetched one window per symbol.

    Separated from the rest of the advanced blocks because it is the only one
    that goes back to the terminal for more data — everything else works on
    deals already in hand. It is also the only one that can be slow, and the one
    most likely to fail on its own (a symbol delisted, history not downloaded),
    so it reports its failure rather than taking the whole response down.
    """
    windows = excursion.required_window(trades)
    if not windows:
        return {'success': True, 'rows': [], 'summary': None, 'symbols': {}}

    bars_by_symbol = {}
    problems = {}
    for symbol, (start, end) in windows.items():
        try:
            bars_by_symbol[symbol] = mt5_client.bars_range(symbol, start, end,
                                                           timeframe=timeframe)
        except Mt5Error as exc:
            problems[symbol] = str(exc)

    rows = excursion.compute(trades, bars_by_symbol)
    return {
        'success': True,
        'timeframe': str(timeframe),
        'symbols': {s: len(b) for s, b in bars_by_symbol.items()},
        'unavailable': problems or None,
        # The per-trade rows are large; the summary is the point. Callers that
        # want the detail ask for it.
        'summary': excursion.summarize(rows),
        # Rides along because it needs exactly the same bars and the same
        # excursion rows — fetching them twice for one extra question would
        # double the slowest part of the request.
        'stop_size': stopsize.analyze(trades, rows, bars_by_symbol),
        'analysed': len(rows),
        'rows': rows,
    }


def _advanced_blocks(filtered, base, params):
    """
    The inference layer: risk-adjusted returns, grids, behaviour and coaching.

    Kept behind ?advanced=true so the default /history payload — and the plain
    dashboard that reads it — stay exactly as they were. Everything here is
    derived from `filtered`, so it always agrees with the tiles beside it.
    """
    headline = base.get('headline')
    risk = risk_adjusted(filtered)
    monte = monte_carlo(filtered, runs=int(_one(params, 'mc_runs', 1000)))

    grids = {}
    for spec in _csv(params, 'heatmaps') or DEFAULT_HEATMAPS:
        rows, _, cols = spec.partition(':')
        try:
            grids[spec] = heatmap(filtered, rows=rows, cols=cols or 'session')
        except ValueError as exc:
            grids[spec] = {'error': str(exc)}

    blocks = {
        'risk': {
            **risk,
            'recovery_factor': recovery_factor(
                (headline or {}).get('net', 0),
                (base.get('drawdown') or {}).get('max_drawdown')),
            'kelly_fraction': kelly_fraction(
                (headline or {}).get('win_rate_pct'),
                (headline or {}).get('payoff_ratio')),
        },
        'monte_carlo': monte,
        'daily': daily_pnl(filtered),
        'heatmaps': grids,
        'holding': holding_time_analysis(filtered),
        'sizes': size_analysis(filtered),
        'behaviour': behaviour(filtered),
        'insights': insight_rules.generate(
            filtered, headline=headline, groups=base.get('groups'),
            risk=risk, monte=monte),
        'coverage': insight_rules.coverage(headline),
    }
    if _truthy(_one(params, 'excursions', '')):
        # Opt-in: this is the one block that goes back to the terminal for bar
        # data, so it is slower than everything around it and must not be a
        # silent cost on every dashboard refresh.
        blocks['excursions'] = _section(lambda: excursions(
            filtered, timeframe=_one(params, 'excursion_timeframe', '1')))
    return blocks


def history(params):
    """
    The history view, filtered, in one request.

    Filtering happens here rather than in the browser so the trade table and
    every statistic beside it are computed from the same rows by the same
    tested code. A second implementation in JavaScript would drift, and the
    first sign of it would be a win rate that disagrees with the table under it.
    """
    now = int(time.time())
    data = mt5_client.deals(
        from_ts=int(_one(params, 'from', now - 30 * 86400)),
        to_ts=int(_one(params, 'to', now)),
        symbol=_one(params, 'symbol'),
        summary=False,
        limit=1_000_000,
    )
    include_open = not _truthy(_one(params, 'closed_only', ''))
    trades = pair_trades(data.get('deals') or [], include_open=include_open)

    # Offered before filtering, so choosing "short" does not empty the dropdown
    # that would let you choose anything else.
    facets = {
        'symbols': sorted({t['symbol'] for t in trades if t.get('symbol')}),
        'exit_reasons': sorted({t['exit_reason'] for t in trades if t.get('exit_reason')}),
    }

    filtered = filter_trades(
        trades,
        symbol=_one(params, 'filter_symbol'),
        direction=_one(params, 'direction'),
        exit_reason=_one(params, 'exit_reason'),
        min_net=_number(params, 'min_net'),
        max_net=_number(params, 'max_net'),
        include_open=include_open,
    )

    balance = _one(params, 'starting_balance')
    groups = _csv(params, 'group_by') or ['exit_reason', 'session', 'weekday', 'symbol']
    out = analyze_trades(
        filtered,
        starting_balance=float(balance) if balance else None,
        group_by=tuple(groups),
    )
    if not _truthy(_one(params, 'curve', 'true')):
        out['equity_curve_points'] = len(out.pop('equity_curve'))

    window, page = paginate(filtered,
                            limit=int(_one(params, 'limit', 200)),
                            offset=int(_one(params, 'offset', 0)))
    result = {
        'success': True,
        'generated_at': iso(now),
        'requested_window_utc': data['requested_window_utc'],
        'total_trades': len(trades),
        'facets': facets,
        'page': page,
        'trades': window,
        **out,
    }
    if _truthy(_one(params, 'advanced', '')):
        result.update(_advanced_blocks(filtered, out, params))
    return result


def route(path, params):
    """Dispatch a GET. Returns a JSON-serialisable dict."""
    if path == '/health':
        return mt5_client.health()

    if path == '/account':
        return mt5_client.account()

    if path == '/positions':
        return mt5_client.positions(symbol=_one(params, 'symbol'))

    if path == '/orders':
        return mt5_client.orders(symbol=_one(params, 'symbol'))

    if path == '/symbols':
        return mt5_client.symbols(
            search=_one(params, 'search'),
            limit=int(_one(params, 'limit', 200)),
        )

    if path == '/quote':
        symbol = _one(params, 'symbol')
        if not symbol:
            raise ValueError('symbol is required')
        return mt5_client.quote(symbol)

    if path == '/bars':
        symbol = _one(params, 'symbol')
        if not symbol:
            raise ValueError('symbol is required')
        return mt5_client.bars(
            symbol,
            timeframe=_one(params, 'timeframe', '5'),
            count=int(_one(params, 'count', 100)),
            summary=_truthy(_one(params, 'summary', '')),
        )

    if path == '/deals':
        now = int(time.time())
        return mt5_client.deals(
            from_ts=int(_one(params, 'from', now - 30 * 86400)),
            to_ts=int(_one(params, 'to', now)),
            symbol=_one(params, 'symbol'),
            limit=int(_one(params, 'limit', 100)),
            offset=int(_one(params, 'offset', 0)),
            summary=_truthy(_one(params, 'summary', '')),
        )

    if path == '/overview':
        return overview(params)

    if path == '/trades':
        now = int(time.time())
        data = mt5_client.deals(
            from_ts=int(_one(params, 'from', now - 30 * 86400)),
            to_ts=int(_one(params, 'to', now)),
            symbol=_one(params, 'symbol'),
            summary=False,
            limit=1_000_000,
        )
        trades = pair_trades(data.get('deals') or [],
                             include_open=not _truthy(_one(params, 'closed_only', '')))
        out = {
            'success': True,
            'requested_window_utc': data['requested_window_utc'],
            'summary': summarize_trades(trades),
        }
        if not _truthy(_one(params, 'summary', '')):
            window, page = paginate(trades,
                                    limit=int(_one(params, 'limit', 100)),
                                    offset=int(_one(params, 'offset', 0)))
            out['page'] = page
            out['trades'] = window
        return out

    if path == '/history':
        return history(params)

    if path == '/diagnose/stops':
        # Answers one question: can the stop distance be recovered from order
        # history, and therefore are R-multiples possible without journalling?
        now = int(time.time())
        from_ts = int(_one(params, 'from', now - 365 * 86400))
        to_ts = int(_one(params, 'to', now))
        orders = mt5_client.orders_history(from_ts, to_ts,
                                           symbol=_one(params, 'symbol'))
        trades = pair_trades(
            mt5_client.deals(from_ts=from_ts, to_ts=to_ts,
                             symbol=_one(params, 'symbol'),
                             summary=False, limit=1_000_000).get('deals') or [],
            include_open=False)
        return {
            'success': True,
            'requested_window_utc': {'from': from_ts, 'to': to_ts},
            **stops.coverage(orders, trades=trades,
                             sample_limit=int(_one(params, 'sample', 200))),
        }

    if path == '/excursions':
        now = int(time.time())
        data = mt5_client.deals(
            from_ts=int(_one(params, 'from', now - 30 * 86400)),
            to_ts=int(_one(params, 'to', now)),
            symbol=_one(params, 'symbol'),
            summary=False, limit=1_000_000,
        )
        trades = pair_trades(data.get('deals') or [], include_open=False)
        out = excursions(trades, timeframe=_one(params, 'timeframe', '1'))
        if _truthy(_one(params, 'summary', 'true')):
            out.pop('rows', None)
        return out

    if path == '/setups':
        # Detection with no simulation attached. This is the sanity check that
        # has to pass before a backtest number means anything: pull a few of
        # these up on a chart and see whether they are setups you would take.
        symbol, config, data = _strategy_bars(params)
        rows = simulate.find_setups(data['bars'], config)
        window, page = paginate(rows,
                                limit=int(_one(params, 'limit', 50)),
                                offset=int(_one(params, 'offset', 0)))
        return {
            'success': True,
            'symbol': symbol,
            'timeframe': data['timeframe'],
            'bars_scanned': len(data['bars']),
            'scanned_from': iso(data['bars'][0]['time_utc']) if data['bars'] else None,
            'scanned_to': iso(data['bars'][-1]['time_utc']) if data['bars'] else None,
            'setups': len(rows),
            'config': strategy.merged(config),
            'page': page,
            'rows': window,
        }

    if path == '/backtest':
        symbol, config, data = _strategy_bars(params)
        balance = float(_one(params, 'balance', 1000))
        out = simulate.simulate(data['bars'], symbol, config, balance=balance,
                                compound=_truthy(_one(params, 'compound', '')))
        result = {
            'success': True,
            'symbol': symbol,
            'timeframe': data['timeframe'],
            'bars_scanned': len(data['bars']),
            'scanned_from': iso(data['bars'][0]['time_utc']) if data['bars'] else None,
            'scanned_to': iso(data['bars'][-1]['time_utc']) if data['bars'] else None,
            'summary': out['summary'],
        }
        # Trades are the bulky part and are rarely wanted on the first look.
        if _truthy(_one(params, 'trades', '')):
            result['trades'] = out['trades']
        if _truthy(_one(params, 'skipped', '')):
            result['skipped'] = out['skipped']
        return result

    if path == '/strategy1/setups':
        # Strategy 1 detection with no simulation attached. The rejections are
        # returned beside the setups because "found nothing" and "the target
        # rule is too strict" produce the same count and are different
        # findings.
        symbol, config, bars_by_tf, window, unavailable = _sweep_bars(params)
        found = sweep_strategy.find_setups(bars_by_tf, config)
        rows, page = paginate(found['setups'],
                              limit=int(_one(params, 'limit', 50)),
                              offset=int(_one(params, 'offset', 0)))
        return {
            'success': True, 'strategy': 'liquidity-sweep', 'symbol': symbol,
            'window': window,
            'bars': {tf: len(b) for tf, b in bars_by_tf.items()},
            'unavailable_timeframes': unavailable,
            'sweeps_found': found['sweeps_found'],
            'setups': len(found['setups']),
            'reject_reasons': found['reject_reasons'],
            'config': sweep_strategy.merged(config),
            'page': page, 'rows': rows,
        }

    if path == '/strategy1/backtest':
        symbol, config, bars_by_tf, window, unavailable = _sweep_bars(params)
        out = sweep_backtest.backtest(
            bars_by_tf, config,
            balance=float(_one(params, 'balance', 1000)),
            spread=float(_one(params, 'spread', sweep_backtest.DEFAULT_SPREAD)))
        if not out['success']:
            raise ValueError('; '.join(out['problems']))

        result = {
            'success': True, 'strategy': 'liquidity-sweep', 'symbol': symbol,
            'window': window,
            'bars': {tf: len(b) for tf, b in bars_by_tf.items()},
            'unavailable_timeframes': unavailable,
            'detection': out['detection'],
            'summary': out['summary'],
        }
        if _truthy(_one(params, 'trades', '')):
            result['trades'] = out['trades']
        if _truthy(_one(params, 'skipped', '')):
            result['skipped'] = out['skipped']
        return result

    if path == '/strategy1/walkforward':
        # Strategy 1 across a chronological split. Slow — the default grid is
        # 24 configurations replayed on both halves — so it is never folded
        # into another request.
        symbol, config, bars_by_tf, window, unavailable = _sweep_bars(params)
        axes = None
        if _one(params, 'axes'):
            axes = {}
            for part in _one(params, 'axes').split('|'):
                name, _, values = part.partition(':')
                axes[name.strip()] = [_axis_value(v) for v in values.split(',')]
        out = sweep_walkforward.walk_forward(
            bars_by_tf, axes=axes, base=config,
            split=float(_one(params, 'split', 0.7)),
            balance=float(_one(params, 'balance', 1000)),
            spread=float(_one(params, 'spread', sweep_backtest.DEFAULT_SPREAD)),
            min_trades=int(_one(params, 'min_trades', 10)))
        return {**out, 'symbol': symbol, 'window': window,
                'bars': {tf: len(b) for tf, b in bars_by_tf.items()},
                'unavailable_timeframes': unavailable}

    if path == '/paper':
        # What the strategy would be doing right now. Recomputed from bars on
        # every call, so a restart changes nothing and two callers agree.
        symbol, config, data = _strategy_bars(params)
        timeframe = _one(params, 'timeframe', '5')
        return paper.status(
            data['bars'], symbol, config,
            balance=float(_one(params, 'balance', 1000)),
            # Bars arrive with time_utc already resolved off the broker clock,
            # so genuine UTC is the right comparison and the local epoch is
            # that. Skew is not symmetric: a slow clock merely drops a bar that
            # had closed, while a fast one keeps a bar still forming, which is
            # the failure this whole module exists to prevent. paper.status
            # reports the boundary it used so that can be checked.
            now=int(time.time()),
            timeframe=timeframe,
            recent=int(_one(params, 'recent', 5)),
        )

    if path == '/reconcile':
        # What the strategy called, against what the account actually did.
        # MT5 records only trades that were taken, so the setups you passed on
        # exist nowhere else — this is the only way to see them.
        symbol, config, data = _strategy_bars(params)
        signals = simulate.simulate(data['bars'], symbol, config,
                                    balance=float(_one(params, 'balance', 1000)))
        if not data['bars']:
            return {'success': True, 'symbol': symbol,
                    'note': 'no bars in the window'}

        window_from = data['bars'][0]['time_utc']
        window_to = data['bars'][-1]['time_utc']
        real_trades = pair_trades(
            mt5_client.deals(from_ts=window_from, to_ts=window_to, symbol=symbol,
                             summary=False, limit=1_000_000).get('deals') or [],
            include_open=False)

        out = reconcile.reconcile(
            signals['trades'], real_trades,
            tolerance_sec=int(_one(params, 'tolerance', reconcile.DEFAULT_TOLERANCE_SEC)))
        result = {
            'success': True, 'symbol': symbol, 'timeframe': data['timeframe'],
            'window': {'from': iso(window_from), 'to': iso(window_to)},
            **out,
        }
        if _truthy(_one(params, 'detail', '')):
            matched, missed, extra = reconcile.match(
                signals['trades'], real_trades,
                tolerance_sec=int(_one(params, 'tolerance',
                                       reconcile.DEFAULT_TOLERANCE_SEC)))
            result['rows'] = {'followed': matched, 'missed': missed,
                              'discretionary': extra}
        return result

    if path == '/sweep':
        # Every parameter combination, judged on bars it was not chosen on.
        symbol, config, data = _strategy_bars(params)
        axes = None
        if _one(params, 'axes'):
            # axes=manage.trail_to_be_at_r:0.5,1.0,none|target.r:2,3
            axes = {}
            for part in _one(params, 'axes').split('|'):
                name, _, values = part.partition(':')
                axes[name.strip()] = [_axis_value(v) for v in values.split(',')]
        out = sweep.sweep(data['bars'], symbol, axes=axes, base=config,
                          split=float(_one(params, 'split', 0.7)),
                          balance=float(_one(params, 'balance', 1000)),
                          min_trades=int(_one(params, 'min_trades', 10)))
        return {**out, 'timeframe': data['timeframe'],
                'scanned_from': iso(data['bars'][0]['time_utc']) if data['bars'] else None,
                'scanned_to': iso(data['bars'][-1]['time_utc']) if data['bars'] else None}

    if path == '/analytics':
        now = int(time.time())
        # Analytics needs every deal in the window, not a page of them.
        data = mt5_client.deals(
            from_ts=int(_one(params, 'from', now - 30 * 86400)),
            to_ts=int(_one(params, 'to', now)),
            symbol=_one(params, 'symbol'),
            summary=False,
            limit=1_000_000,
        )
        deals_rows = data.get('deals') or []
        balance = _one(params, 'starting_balance')
        groups = _csv(params, 'group_by') or ['reason', 'session', 'symbol']
        out = analyze(
            deals_rows,
            starting_balance=float(balance) if balance else None,
            group_by=tuple(groups),
        )
        if not _truthy(_one(params, 'curve', '')):
            # The curve is one point per trade — hundreds of rows. Opt in.
            out['equity_curve_points'] = len(out.pop('equity_curve'))
        return {
            'success': True,
            'requested_window_utc': data['requested_window_utc'],
            'summary': data['summary'],
            **out,
        }

    if path == '/calendar':
        data = mt5_client.calendar(path=_one(params, 'file'))
        events = filter_calendar(
            data['events'],
            currencies=_csv(params, 'currencies'),
            # No filter specified means no filter. Defaulting to 'low' silently
            # dropped every event the terminal rates as 'none', so the count
            # came back short of what the exporter reported.
            min_importance=_one(params, 'min_importance', 'none'),
            from_ts=int(_one(params, 'from')) if _one(params, 'from') else None,
            to_ts=int(_one(params, 'to')) if _one(params, 'to') else None,
        )
        return {**data, 'count': len(events), 'events': events}

    if path == '/blackout':
        data = mt5_client.calendar(path=_one(params, 'file'))
        return blackout_status(
            data['events'],
            now_ts=int(_one(params, 'now', int(time.time()))),
            before_min=int(_one(params, 'before_min', 15)),
            after_min=int(_one(params, 'after_min', 15)),
            currencies=_csv(params, 'currencies'),
            min_importance=_one(params, 'min_importance', 'high'),
        )

    return None


class Handler(BaseHTTPRequestHandler):
    server_version = 'mt5-readonly-bridge/1.0'

    def _send(self, status, payload):
        body = json.dumps(payload, default=str).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        expected = os.environ.get('MT5_BRIDGE_TOKEN')
        if not expected:
            return True
        return self.headers.get('X-Bridge-Token') == expected

    def _serve_dashboard(self, url_path):
        """
        Serve the status dashboard.

        Hosted by the bridge rather than a separate server so the page is
        same-origin with the API it reads — no CORS, no proxy, and one fewer
        process for the launcher to manage.
        """
        # The root serves the React app when it has been built, and falls back to
        # the plain page when it has not. That fallback is the point: a failed or
        # skipped frontend build must leave a working dashboard rather than a
        # 404, since the bridge and its API are unaffected by it.
        if url_path in ('/', '/dashboard', '/dashboard/'):
            rel = ('/app/index.html'
                   if os.path.isfile(os.path.join(DASHBOARD_DIR, 'app', 'index.html'))
                   else '/index.html')
        else:
            rel = url_path
        rel = rel[len('/dashboard'):] if rel.startswith('/dashboard/') else rel
        target = os.path.normpath(os.path.join(DASHBOARD_DIR, rel.lstrip('/')))

        # Refuse anything that escapes the dashboard directory.
        if not target.startswith(DASHBOARD_DIR):
            return False
        # A directory request serves its index, so /dashboard/app/ works like
        # any other web root rather than 404ing on the folder itself.
        if os.path.isdir(target):
            target = os.path.join(target, 'index.html')
        if not os.path.isfile(target):
            return False

        mime = {'.html': 'text/html', '.js': 'text/javascript',
                '.css': 'text/css', '.json': 'application/json',
                '.svg': 'image/svg+xml'}.get(os.path.splitext(target)[1], 'text/plain')
        with open(target, 'rb') as handle:
            body = handle.read()
        self.send_response(200)
        self.send_header('Content-Type', f'{mime}; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)
        return True

    def do_GET(self):
        if not self._authorized():
            self._send(401, {'success': False, 'error': 'Invalid or missing X-Bridge-Token'})
            return

        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path in ('/', '/dashboard', '/dashboard/') or parsed.path.startswith('/dashboard/'):
            if self._serve_dashboard(parsed.path):
                return
            self._send(404, {'success': False,
                             'error': 'Dashboard files not found',
                             'hint': f'Expected them in {DASHBOARD_DIR}'})
            return
        try:
            result = route(parsed.path.rstrip('/') or '/health', params)
        except ValueError as exc:
            self._send(400, {'success': False, 'error': str(exc)})
            return
        except mt5_client.Mt5Error as exc:
            self._send(503, {'success': False, 'error': str(exc)})
            return
        except Exception as exc:  # unexpected — surface it, don't kill the server
            self._send(500, {'success': False, 'error': str(exc),
                             'trace': traceback.format_exc(limit=3)})
            return

        if result is None:
            self._send(404, {'success': False, 'error': f'No such route: {parsed.path}'})
            return
        self._send(200, result)

    # Any verb that could imply a write is refused outright.
    def _refuse(self):
        self._send(405, {'success': False,
                         'error': 'This bridge is read-only; only GET is supported'})

    do_POST = do_PUT = do_PATCH = do_DELETE = _refuse

    def log_message(self, fmt, *args):
        # Keep stdout clean; the MCP layer surfaces errors in its own responses.
        pass


def main():
    parser = argparse.ArgumentParser(description='Read-only MT5 HTTP bridge')
    parser.add_argument('--port', type=int, default=int(os.environ.get('MT5_BRIDGE_PORT', DEFAULT_PORT)))
    parser.add_argument('--terminal-path', default=os.environ.get('MT5_TERMINAL_PATH'))
    args = parser.parse_args()

    if args.terminal_path:
        os.environ['MT5_TERMINAL_PATH'] = args.terminal_path

    try:
        mt5_client.connect(path=args.terminal_path)
        state = mt5_client.health()
        print(f'MT5 connected: account {state["account"]["login"]} '
              f'on {state["account"]["server"]} ({state["account"]["currency"]})')
    except mt5_client.Mt5Error as exc:
        # Start anyway: /health should be able to report why it is down.
        print(f'WARNING: MT5 not reachable yet — {exc}')

    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Read-only bridge listening on http://127.0.0.1:{args.port}')
    print('Routes: /overview /history /excursions /diagnose/stops /setups /backtest /sweep /paper /reconcile /strategy1/setups /strategy1/backtest /strategy1/walkforward /health'
          ' /account /symbols /positions /orders /quote /bars /deals /trades'
          ' /analytics /calendar /blackout')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        mt5_client.shutdown()


if __name__ == '__main__':
    main()
