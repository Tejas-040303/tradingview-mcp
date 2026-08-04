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
import insights as insight_rules
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

    return {
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
        rel = '/index.html' if url_path in ('/', '/dashboard', '/dashboard/') else url_path
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
    print('Routes: /overview /history /health /account /symbols /positions /orders'
          ' /quote /bars /deals /trades /analytics /calendar /blackout')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        mt5_client.shutdown()


if __name__ == '__main__':
    main()
