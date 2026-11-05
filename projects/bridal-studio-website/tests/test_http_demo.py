from contextlib import contextmanager
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from bridal_demo.http_demo import demo_server
from bridal_demo.intake import CHINA


@contextmanager
def running():
    with demo_server() as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join()


class HttpTests(unittest.TestCase):
    def request(self, server, path='/', data=None, **headers):
        request = Request(server.demo_origin + path, data=data, headers=headers)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    def test_static_and_no_private_listing(self):
        with running() as server:
            self.assertEqual(self.request(server)[0], 200)
            self.assertEqual(self.request(server, '/try-on.html')[0], 200)
            for path in ('/assets/', '/.git/config', '/../src/bridal_demo/store.py', '/demo/requests'):
                self.assertEqual(self.request(server, path)[0], 404)

    def test_real_form_transport(self):
        with running() as server:
            form = dict(customer_name='Demo', customer_phone='+12025550141', consent='yes', company='',
                        preferred_date=(datetime.now(CHINA).date() + timedelta(days=1)).isoformat(),
                        idempotency_key=str(uuid4()))
            headers = {'Origin': server.demo_origin, 'Content-Type': 'application/x-www-form-urlencoded'}
            first = self.request(server, '/website/appointments', urlencode(form).encode(), **headers)
            second = self.request(server, '/website/appointments', urlencode(form).encode(), **headers)
            self.assertEqual((first[0], second[0]), (201, 200))
            self.assertEqual(json.loads(first[2])['reference'], json.loads(second[2])['reference'])
            self.assertIsNone(first[1].get('Access-Control-Allow-Origin'))
            self.assertEqual(server.demo_store.list_requests('new')[1], 1)

    def test_transport_rejections_do_not_write(self):
        with running() as server:
            endpoint = '/website/appointments'
            self.assertEqual(self.request(server, endpoint, b'{}', **{'Content-Type': 'application/json'})[0], 415)
            self.assertEqual(self.request(server, endpoint, b'x' * 17000, **{'Content-Type': 'application/x-www-form-urlencoded'})[0], 413)
            self.assertEqual(self.request(server, endpoint, b'consent=yes&consent=no', **{'Content-Type': 'application/x-www-form-urlencoded'})[0], 422)
            self.assertEqual(self.request(server, endpoint, b'consent=yes', **{'Content-Type': 'application/x-www-form-urlencoded'})[0], 403)
            self.assertEqual(server.demo_store.list_requests('')[1], 0)


if __name__ == '__main__':
    unittest.main()
