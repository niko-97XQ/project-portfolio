"""Run a finite offline smoke test, or serve the isolated frontend locally."""
import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from bridal_demo.http_demo import demo_server
from bridal_demo.intake import CHINA


def smoke():
    with demo_server() as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        form = dict(customer_name='Demo Visitor', customer_phone='+12025550140',
                    preferred_date=(datetime.now(CHINA).date() + timedelta(days=7)).isoformat(),
                    consent='yes', company='', idempotency_key=str(uuid4()))
        body = urlencode(form).encode()
        headers = {'Origin': server.demo_origin, 'Content-Type': 'application/x-www-form-urlencoded'}
        try:
            with urlopen(server.demo_origin + '/') as response:
                assert response.status == 200
            references = []
            for expected in (201, 200):
                request = Request(server.demo_origin + '/website/appointments', data=body, headers=headers)
                with urlopen(request) as response:
                    assert response.status == expected
                    references.append(json.load(response)['reference'])
            assert references[0] == references[1]
            assert server.demo_store.list_requests('new')[1] == 1
            try:
                urlopen(Request(server.demo_origin + '/website/appointments', data=body,
                                headers={**headers, 'Origin': 'http://example.test'}))
                raise AssertionError('Wrong origin accepted')
            except HTTPError as error:
                assert error.code == 403
            print('Offline demo passed: real HTTP + SQLite, 201/200 safe retry, one request, wrong Origin 403.')
            print('Only synthetic input was used. Temporary storage is removed on exit; no external service or customer contact.')
        finally:
            server.shutdown()
            thread.join()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    if not args.serve:
        smoke()
        return
    with demo_server(args.port) as server:
        print(json.dumps({'url': server.demo_origin, 'mode': 'local-synthetic-demo'}), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
