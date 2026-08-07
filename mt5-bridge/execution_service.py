"""
The execution service — the only process that can move money.

Third boundary, third port. `bridge.py` reads and cannot write; the journal
writes a local file and cannot trade; this can trade and does nothing else. The
separation is the safety argument: two of the three processes are incapable of
the failure that matters, and only this one needs the paranoid review.

All policy lives in `execution.py`, which is pure and has thirty-eight tests.
This module is deliberately thin — it fetches the terminal's own view of the
account, asks `execution.check` whether an order is allowed, and either sends
it or does not. There is exactly one line here that can place an order, and it
is the only part of the system that cannot be tested without a live terminal.

**It contains no strategy.** It never decides what to trade. Something outside
hands it an explicit order, and it refuses or forwards. A bug in the detectors
cannot become a bug that places orders, because the detectors are not reachable
from here.

Defaults, all of which must be overridden deliberately:

    disarmed · dry run · demo accounts only · one hour maximum · stop required

An order that cannot be written to the audit log is refused. An order nobody
can reconstruct afterwards should not have happened.

Run:
    python execution_service.py                 # 127.0.0.1:8767, disarmed
    MT5_EXEC_TOKEN=secret python execution_service.py
"""
import argparse
import json
import os
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import execution

DEFAULT_PORT = 8767
AUDIT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'execution', 'audit.jsonl')
MAX_BODY = 65_536

# Guarded because arming, ordering and status all read and write it, and a
# torn read here would be a torn read of "are we allowed to trade".
_lock = threading.Lock()
_state = execution.default_state()


def audit(event, payload, path=AUDIT_PATH):
    """
    Append-only record of everything this service was asked to do.

    Raises on failure, and the caller turns that into a refusal. An order that
    cannot be recorded is an order nobody can reconstruct afterwards, and the
    right response to "I cannot write the audit log" is to stop trading, not to
    trade unrecorded.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps({'at': int(time.time()), 'event': event, **payload},
                      default=str)
    with open(path, 'a', encoding='utf-8') as handle:
        handle.write(line + '\n')
        handle.flush()
        os.fsync(handle.fileno())


def _terminal_view():
    """
    The account and positions as the terminal reports them.

    Read through mt5_client, which has no order-placing function at all — the
    guards are checked against the broker's own view rather than against
    anything a caller asserted.
    """
    import mt5_client
    account = mt5_client.account()
    positions = mt5_client.positions()
    return (account.get('account') or account,
            positions.get('positions') or [])


def _send(order):
    """
    The one function in this project that can place an order.

    Untestable without a live Windows terminal, so it is kept to the minimum
    that does the job and contains no decisions: everything conditional has
    already happened in `execution.check`.
    """
    import MetaTrader5 as mt5           # noqa: N813 - Windows-only, imported late

    kind = mt5.ORDER_TYPE_BUY if order['side'] == 'buy' else mt5.ORDER_TYPE_SELL
    tick = mt5.symbol_info_tick(order['symbol'])
    if tick is None:
        raise RuntimeError(f'no tick for {order["symbol"]}; is it in Market Watch?')
    price = tick.ask if order['side'] == 'buy' else tick.bid

    request = {
        'action': mt5.TRADE_ACTION_DEAL,
        'symbol': order['symbol'],
        'volume': float(order['lot']),
        'type': kind,
        'price': price,
        'sl': float(order['stop']),
        'deviation': int(order.get('slippage', 20)),
        'magic': int(order.get('magic', 0)),
        'comment': str(order.get('comment', 'mt5-bridge'))[:31],
        'type_time': mt5.ORDER_TIME_GTC,
        'type_filling': mt5.ORDER_FILLING_IOC,
    }
    if order.get('target'):
        request['tp'] = float(order['target'])

    result = mt5.order_send(request)
    if result is None:
        raise RuntimeError(f'order_send returned nothing: {mt5.last_error()}')
    return {'retcode': result.retcode, 'deal': result.deal, 'order': result.order,
            'price': result.price, 'volume': result.volume,
            'comment': result.comment,
            'ok': result.retcode == mt5.TRADE_RETCODE_DONE}


def handle_post(path, body):
    global _state

    if path == '/arm':
        with _lock:
            out = execution.arm(_state, body.get('minutes', 60),
                                body.get('account'), limits=body.get('limits'),
                                dry_run=body.get('dry_run', True) is not False)
            if not out['ok']:
                return {'success': False, 'problems': out['problems']}, 400
            status = execution.describe(out['state'])
            try:
                audit('arm', {'account': body.get('account'), 'status': status})
            except OSError as exc:
                # Arming requires a working audit log. Staying disarmed is the
                # safe failure, and _state is left untouched to guarantee it.
                return {'success': False, 'problems': [
                    f'audit log is not writable ({exc}) — refusing to arm, since '
                    f'nothing that follows could be recorded']}, 503
            _state = out['state']
        return {'success': True, **status}, 200

    if path == '/disarm':
        # Disarming never fails. It is the kill switch, and a kill switch with
        # preconditions is not one — an unwritable audit log must not be able
        # to keep the service armed.
        with _lock:
            _state = execution.disarm(_state)
            status = execution.describe(_state)
        try:
            audit('disarm', {'status': status})
        except OSError as exc:
            status['audit_warning'] = f'disarmed, but not recorded: {exc}'
        return {'success': True, **status}, 200

    if path == '/order':
        order = body.get('order') or body
        account, positions, terminal_error = None, None, None
        try:
            account, positions = _terminal_view()
        except Exception as exc:
            terminal_error = f'{type(exc).__name__}: {exc}'

        with _lock:
            state = _state
            # A missing terminal disables the account and position guards, so
            # it is a refusal on its own. The rest of the guards still run and
            # still report: short-circuiting here would answer "cannot read
            # the terminal" to an order that was also unarmed, oversized and
            # missing a stop, and the caller would fix them one round trip at
            # a time.
            verdict = execution.check(state, order, account=account,
                                      positions=positions,
                                      blackout=body.get('blackout'))
            if terminal_error:
                verdict = {**verdict, 'allow': False, 'would_place': None,
                           'reasons': [*verdict['reasons'],
                                       f'cannot read the terminal ({terminal_error}) — '
                                       f'refusing rather than trading blind']}

            try:
                audit('order_request', {'order': order, 'verdict': verdict})
            except OSError as exc:
                return {'success': False, 'placed': False,
                        'reasons': [f'audit log is not writable ({exc}) — an order '
                                    f'that cannot be recorded is not placed']}, 503

            if not verdict['allow']:
                return {'success': False, 'placed': False, **verdict}, 409
            if verdict['dry_run']:
                return {'success': True, 'placed': False, 'dry_run': True,
                        **verdict,
                        'note': 'armed in dry-run mode — validated, not sent'}, 200
            _state = execution.record_submission(_state, order.get('client_id'))

        try:
            result = _send(order)
        except Exception as exc:
            audit('order_failed', {'order': order, 'error': str(exc)})
            return {'success': False, 'placed': False,
                    'error': f'{type(exc).__name__}: {exc}'}, 502

        audit('order_sent', {'order': order, 'result': result})
        return {'success': result['ok'], 'placed': result['ok'],
                'result': result}, 200

    raise ValueError(f'Unknown route {path}')


class Handler(BaseHTTPRequestHandler):
    server_version = 'MT5Exec/1.0'
    token = None

    def _authorised(self):
        return not self.token or self.headers.get('X-Exec-Token') == self.token

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path.rstrip('/') or '/status'
        if path in ('/status', '/health', '/'):
            with _lock:
                status = execution.describe(_state)
            return self._send_json({'success': True, 'service': 'execution',
                                    'can_trade': True, **status})
        self._send_json({'success': False, 'error': f'Unknown route {path}'}, 404)

    def do_POST(self):
        if not self._authorised():
            return self._send_json({'success': False, 'error': 'bad or missing token'}, 401)

        length = int(self.headers.get('Content-Length') or 0)
        if length > MAX_BODY:
            return self._send_json({'success': False, 'error': 'body too large'}, 413)
        try:
            body = json.loads(self.rfile.read(length) or b'{}')
        except ValueError:
            return self._send_json({'success': False, 'error': 'body must be JSON'}, 400)
        if not isinstance(body, dict):
            return self._send_json({'success': False, 'error': 'body must be an object'}, 400)

        path = urlparse(self.path).path.rstrip('/') or '/'
        try:
            payload, status = handle_post(path, body)
            self._send_json(payload, status)
        except ValueError as exc:
            self._send_json({'success': False, 'error': str(exc)}, 404)
        except Exception as exc:   # pragma: no cover - defensive
            traceback.print_exc()
            self._send_json({'success': False,
                             'error': f'{type(exc).__name__}: {exc}'}, 500)

    def log_message(self, fmt, *args):
        print(f'[exec {time.strftime("%H:%M:%S")}] {fmt % args}')


def main():
    parser = argparse.ArgumentParser(description='MT5 execution service')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    Handler.token = os.environ.get('MT5_EXEC_TOKEN')

    print(f'Execution service on http://127.0.0.1:{args.port}')
    print('Routes: GET /status · POST /arm /disarm /order')
    print('')
    print('  DISARMED. Nothing can be placed until POST /arm, and arming')
    print('  defaults to dry run, demo accounts only, and expires.')
    print(f'  Audit log: {AUDIT_PATH}')
    if not Handler.token:
        print('  No MT5_EXEC_TOKEN set — bound to 127.0.0.1 only.')

    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
