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
from analytics import analyze
from normalize import blackout_status, filter_calendar

DEFAULT_PORT = 8765


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

    def do_GET(self):
        if not self._authorized():
            self._send(401, {'success': False, 'error': 'Invalid or missing X-Bridge-Token'})
            return

        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
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
    print('Routes: /health /account /symbols /positions /orders /quote /bars /deals'
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
