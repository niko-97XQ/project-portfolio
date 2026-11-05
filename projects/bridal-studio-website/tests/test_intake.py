from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from bridal_demo.intake import CHINA, public_intake
from bridal_demo.store import IntakeError, WebsiteRequestStore


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'requests.sqlite3'
        self.store = WebsiteRequestStore(self.path)
        self.origin = 'http://example.test'
        self.headers = {'Origin': self.origin, 'Remote-Addr': 'synthetic-client'}

    def form(self, **changes):
        form = dict(customer_name='Synthetic Visitor', customer_phone='+1 (202) 555-0140',
                    preferred_date=(datetime.now(CHINA).date() + timedelta(days=3)).isoformat(),
                    preferred_time='14:30', message='Synthetic fitting request', consent='yes',
                    company='', idempotency_key=str(uuid4()))
        return {**form, **changes}

    def submit(self, form=None, headers=None):
        return public_intake(self.store, form or self.form(), headers or self.headers, self.origin)

    def rejected(self, form, status=422, headers=None):
        with self.assertRaises(IntakeError) as caught:
            self.submit(form, headers)
        self.assertEqual(caught.exception.status, status)
        self.assertEqual(self.store.list_requests('')[1], 0)

    def test_success_is_pending_not_confirmed(self):
        code, raw = self.submit()
        self.assertEqual(code, 201)
        self.assertEqual(json.loads(raw)['status'], 'pending')
        rows, total = self.store.list_requests('new')
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]['customer_phone'], '+12025550140')
        self.assertRegex(rows[0]['id'], r'^DEMO-[A-F0-9]{16}$')

    def test_missing_and_wrong_origin(self):
        for origin in ('', 'http://wrong.example.test', 'http://example.test.attacker.test'):
            with self.subTest(origin=origin):
                self.rejected(self.form(), 403, {'Origin': origin})

    def test_consent_and_honeypot(self):
        self.rejected(self.form(consent='no'))
        self.rejected(self.form(company='automated input'))

    def test_name_bounds_and_controls(self):
        for name in ('', 'x' * 51, 'name\x00'):
            self.rejected(self.form(customer_name=name))

    def test_phone_validation(self):
        for phone in ('not-a-phone', '123', '1' * 17, '+1\n2025550140'):
            self.rejected(self.form(customer_phone=phone))

    def test_date_and_time_validation(self):
        today = datetime.now(CHINA).date()
        for value in ('invalid', (today - timedelta(days=1)).isoformat(), (today + timedelta(days=366)).isoformat()):
            self.rejected(self.form(preferred_date=value))
        self.rejected(self.form(preferred_time='25:00'))

    def test_message_and_uuid_validation(self):
        self.rejected(self.form(message='x' * 501))
        self.rejected(self.form(message='bad\x00'))
        self.rejected(self.form(idempotency_key='invalid'))

    def test_retry_returns_same_reference(self):
        form = self.form()
        first = self.submit(form)
        second = self.submit(form)
        self.assertEqual((first[0], second[0]), (201, 200))
        self.assertEqual(json.loads(first[1])['reference'], json.loads(second[1])['reference'])
        self.assertEqual(self.store.list_requests('')[1], 1)

    def test_changed_payload_conflicts(self):
        form = self.form()
        self.submit(form)
        with self.assertRaises(IntakeError) as caught:
            self.submit({**form, 'message': 'Changed payload'})
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.store.list_requests('')[1], 1)

    def test_retry_survives_store_recreation(self):
        form = self.form()
        first = self.submit(form)
        self.store = WebsiteRequestStore(self.path)
        second = self.submit(form)
        self.assertEqual(second[0], 200)
        self.assertEqual(json.loads(first[1])['reference'], json.loads(second[1])['reference'])

    def test_concurrent_retry_is_single_insert(self):
        form = self.form()
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.submit(form), range(6)))
        self.assertEqual(sum(code == 201 for code, _ in results), 1)
        self.assertEqual(len({json.loads(body)['reference'] for _, body in results}), 1)
        self.assertEqual(self.store.list_requests('')[1], 1)

    def test_phone_rate_limit(self):
        for _ in range(3):
            self.submit()
        with self.assertRaises(IntakeError) as caught:
            self.submit()
        self.assertEqual(caught.exception.status, 429)

    def test_client_rate_limit_and_keyed_digest(self):
        for index in range(10):
            self.submit(self.form(customer_phone=f'+1202555{index:04d}'))
        with self.assertRaises(IntakeError) as caught:
            self.submit(self.form(customer_phone='+12025550999'))
        self.assertEqual(caught.exception.status, 429)
        with closing(sqlite3.connect(self.path)) as database:
            hashes = database.execute('SELECT ip_hash FROM website_rate_events').fetchall()
        self.assertTrue(all(len(row[0]) == 64 and row[0] != 'synthetic-client' for row in hashes))

    def test_version_check_and_audit(self):
        self.submit()
        row = self.store.list_requests('new')[0][0]
        self.store.update_status(row['id'], 'contacted', row['version'], 'demo-staff')
        with self.assertRaises(ValueError):
            self.store.update_status(row['id'], 'closed', row['version'], 'other-demo-staff')
        updated = self.store.list_requests('contacted')[0][0]
        self.assertEqual(updated['version'], 2)
        with closing(sqlite3.connect(self.path)) as database:
            self.assertEqual(database.execute('SELECT count(*) FROM website_request_events').fetchone()[0], 1)

    def test_status_allowlist(self):
        with self.assertRaises(ValueError):
            self.store.update_status('missing', 'confirmed', 1, 'demo-staff')


if __name__ == '__main__':
    unittest.main()
