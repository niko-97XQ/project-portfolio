"""Loopback-only HTTP adapter for the public demonstration, not a production server."""
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qsl, unquote, urlsplit

from bridal_demo.intake import public_intake
from bridal_demo.store import IntakeError, WebsiteRequestStore


WEB = Path(__file__).resolve().parents[2] / 'web'
MAX_BODY = 16 * 1024


class DemoHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        target = (WEB / path.lstrip('/')).resolve()
        if path == '/':
            target = WEB / 'index.html'
        if not target.is_relative_to(WEB) or not target.is_file():
            return self._json(404, {'message': 'Not found'})
        return super().do_GET()

    def do_HEAD(self):
        path = unquote(urlsplit(self.path).path)
        target = (WEB / path.lstrip('/')).resolve()
        if path == '/':
            target = WEB / 'index.html'
        if not target.is_relative_to(WEB) or not target.is_file():
            self.send_error(404)
            return
        return super().do_HEAD()

    def do_POST(self):
        if self.path != '/website/appointments':
            return self._json(404, {'message': 'Not found'})
        if self.headers.get_content_type() != 'application/x-www-form-urlencoded':
            return self._json(415, {'message': 'URL-encoded forms only'})
        try:
            size = int(self.headers.get('Content-Length', ''))
        except ValueError:
            return self._json(400, {'message': 'Invalid content length'})
        if size < 0 or size > MAX_BODY:
            return self._json(413, {'message': 'Request body too large'})
        self.connection.settimeout(5)
        try:
            raw = self.rfile.read(size)
            if len(raw) != size:
                return self._json(400, {'message': 'Incomplete body'})
            pairs = parse_qsl(raw.decode('utf-8'), keep_blank_values=True,
                              strict_parsing=True, max_num_fields=20, errors='strict')
            form = dict(pairs)
            if len(form) != len(pairs):
                return self._json(422, {'message': 'Duplicate form fields'})
        except (UnicodeDecodeError, ValueError):
            return self._json(422, {'message': 'Invalid form encoding'})
        except TimeoutError:
            return self._json(408, {'message': 'Request timed out'})
        try:
            code, body = public_intake(
                self.server.demo_store, form,
                {'Origin': self.headers.get('Origin', ''), 'Remote-Addr': 'local-demo-client'},
                self.server.demo_origin,
            )
        except IntakeError as error:
            return self._json(error.status, {'message': str(error)})
        return self._json(code, json.loads(body))


@contextmanager
def demo_server(port=0):
    with TemporaryDirectory(prefix='bridal-demo-') as directory:
        store = WebsiteRequestStore(Path(directory) / 'requests.sqlite3')
        handler = partial(DemoHandler, directory=str(WEB))
        server = ThreadingHTTPServer(('localhost', port), handler)
        server.demo_store = store
        server.demo_origin = f'http://localhost:{server.server_port}'
        try:
            yield server
        finally:
            server.server_close()
