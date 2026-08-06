"""
The journal write service — the first thing in this project allowed to write.

Deliberately its own process on its own port. `bridge.py` answers GET and
returns 405 for everything else, and that guarantee is worth more than the
convenience of one server: a reader that cannot be made to write is a reader
nobody has to audit. Adding POST routes to it would spend that guarantee to
save a port number.

What this can write is a **local SQLite file**. It imports no broker client,
and a test asserts it never will. The distinction between "a service that can
write" and "a service that can place an order" is the whole architecture here,
and keeping it structural rather than promised is the point — the execution
service, when it exists, will be a third process with its own boundary.

Run:
    python journal_service.py                    # 127.0.0.1:8766
    python journal_service.py --port 9200
    python journal_service.py --db ./journal/journal.db
    MT5_JOURNAL_TOKEN=secret python journal_service.py

Stdlib only.
"""
import argparse
import json
import os
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import journal

DEFAULT_PORT = 8766
DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'journal', 'journal.db')
# One megabyte of JSON is already far more than any journal post, and an
# unbounded read is how a local service becomes a memory problem.
MAX_BODY = 1_048_576


def _one(params, key, default=None):
    values = params.get(key)
    return values[0] if values else default


def _truthy(value):
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def handle_get(path, params, conn):
    if path == '/health':
        return {'success': True, 'service': 'journal', 'writable': True,
                'can_trade': False,
                'note': 'writes a local SQLite file; no broker client is imported'}

    if path == '/summary':
        return {'success': True,
                **journal.summary(conn,
                                  from_ts=_int(_one(params, 'from')),
                                  to_ts=_int(_one(params, 'to')))}

    if path == '/signals':
        rows = journal.signals_with_decisions(
            conn,
            from_ts=_int(_one(params, 'from')), to_ts=_int(_one(params, 'to')),
            symbol=_one(params, 'symbol'), action=_one(params, 'action'),
            limit=_int(_one(params, 'limit'), 200),
            offset=_int(_one(params, 'offset'), 0))
        return {'success': True, 'count': len(rows), 'rows': rows}

    raise ValueError(f'Unknown route {path}')


def handle_post(path, body, conn):
    if path == '/signals':
        symbol = body.get('symbol')
        if not symbol:
            raise ValueError('symbol is required')
        return {'success': True,
                **journal.record_signals(conn, body.get('signals') or [], symbol)}

    if path == '/decision':
        for field in ('symbol', 'time_utc', 'direction', 'action'):
            if body.get(field) in (None, ''):
                raise ValueError(f'{field} is required')
        return {'success': True, **journal.record_decision(
            conn, body['symbol'], body['time_utc'], body['direction'],
            body['action'], reason=body.get('reason'),
            emotion=body.get('emotion'), position_id=body.get('position_id'))}

    if path == '/trade':
        if not body.get('position_id'):
            raise ValueError('position_id is required')
        return {'success': True, **journal.annotate_trade(
            conn, body['position_id'], strategy=body.get('strategy'),
            exit_kind=body.get('exit_kind'), emotion=body.get('emotion'),
            note=body.get('note'))}

    if path == '/screenshot':
        if not body.get('path') or not body.get('kind'):
            raise ValueError('path and kind are required')
        return {'success': True, **journal.attach_screenshot(
            conn, body['path'], body['kind'],
            signal_id=body.get('signal_id'), position_id=body.get('position_id'))}

    if path == '/prune':
        # Dry run unless explicitly told otherwise. Deleting a week of
        # screenshots is not undoable, and a retention endpoint that deletes on
        # a bare POST is one that eventually deletes by accident.
        return {'success': True, **journal.prune_screenshots(
            conn,
            keep_days=_int(body.get('keep_days'), journal.DEFAULT_KEEP_DAYS),
            apply=_truthy(body.get('apply', False)))}

    raise ValueError(f'Unknown route {path}')


class Handler(BaseHTTPRequestHandler):
    server_version = 'MT5Journal/1.0'
    db_path = DEFAULT_DB
    token = None

    def _authorised(self):
        if not self.token:
            return True
        return self.headers.get('X-Journal-Token') == self.token

    def _send(self, payload, status=200):
        body = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _run(self, fn):
        if not self._authorised():
            return self._send({'success': False, 'error': 'bad or missing token'}, 401)
        conn = journal.connect(self.db_path)
        try:
            self._send(fn(conn))
        except LookupError as exc:
            self._send({'success': False, 'error': str(exc)}, 404)
        except ValueError as exc:
            self._send({'success': False, 'error': str(exc)}, 400)
        except Exception as exc:   # pragma: no cover - defensive
            traceback.print_exc()
            self._send({'success': False,
                        'error': f'{type(exc).__name__}: {exc}'}, 500)
        finally:
            conn.close()

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        self._run(lambda conn: handle_get(parsed.path.rstrip('/') or '/health',
                                          params, conn))

    def do_POST(self):
        length = _int(self.headers.get('Content-Length'), 0)
        if length > MAX_BODY:
            return self._send({'success': False,
                               'error': f'body larger than {MAX_BODY} bytes'}, 413)
        raw = self.rfile.read(length) if length else b'{}'
        try:
            body = json.loads(raw or b'{}')
        except ValueError:
            return self._send({'success': False, 'error': 'body must be JSON'}, 400)
        if not isinstance(body, dict):
            return self._send({'success': False, 'error': 'body must be a JSON object'}, 400)

        path = urlparse(self.path).path.rstrip('/') or '/'
        self._run(lambda conn: handle_post(path, body, conn))

    def do_DELETE(self):
        # Removing journal entries is not offered. A record you can quietly
        # delete after a bad trade is not a record — retention prunes images on
        # a schedule and keeps the row.
        self._send({'success': False,
                    'error': 'the journal does not delete entries; use /prune '
                             'to expire screenshot files, which keeps the row'}, 405)

    def log_message(self, fmt, *args):
        print(f'[journal {time.strftime("%H:%M:%S")}] {fmt % args}')


def main():
    parser = argparse.ArgumentParser(description='MT5 trading journal (write service)')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--db', default=os.environ.get('MT5_JOURNAL_DB', DEFAULT_DB))
    args = parser.parse_args()

    Handler.db_path = args.db
    Handler.token = os.environ.get('MT5_JOURNAL_TOKEN')

    journal.connect(args.db).close()   # fail loudly now rather than on first write

    print(f'Journal on http://127.0.0.1:{args.port}  db: {args.db}')
    print('Routes: GET /health /summary /signals · '
          'POST /signals /decision /trade /screenshot /prune')
    print('Writes a local SQLite file. It cannot place, modify or cancel an order.')
    if not Handler.token:
        print('No MT5_JOURNAL_TOKEN set — bound to 127.0.0.1 only.')

    # 127.0.0.1, never a routable interface. Same rule as the read bridge.
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
