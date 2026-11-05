import concurrent.futures
from contextlib import closing
from datetime import datetime, timedelta
import http.client
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlencode
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from forthatday_agent.application.staff_access import StaffActionSigner, StaffSessionSigner
from forthatday_agent.application.staff_web import StaffTaskWebApp
from forthatday_agent.application.website_requests import CHINA
from forthatday_agent.infrastructure.catalog_store import CatalogStore
from forthatday_agent.infrastructure.wecom_http import create_wecom_callback_server


class WebsiteRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'test.sqlite3'
        self.catalog = CatalogStore(self.db, catalog_root=self.temp.name)
        self.signer = StaffSessionSigner('isolated-test-key-' * 3)
        self.app = StaffTaskWebApp(object(), StaffActionSigner('isolated-action-key-' * 3),
            catalog_store=self.catalog, session_signer=self.signer, wecom_client=object(),
            corp_id='test-corp', agent_id=1, staff_user_ids=('test-clerk',), app_base_url='https://example.test')
        self.headers = {'Origin':'https://example.test','Remote-Addr':'192.0.2.1'}
        self.form = dict(customer_name='测试顾客',customer_phone='13800000000',
            preferred_date=(datetime.now(CHINA)+timedelta(days=2)).date().isoformat(),
            preferred_time='14:00',message='测试试纱需求',consent='yes',company='',idempotency_key=str(uuid4()))
        token = self.signer.issue('test-clerk')
        self.staff_headers = {'Cookie':f'ftd_staff_session={token}'}
        self.csrf = self.signer.csrf_token(token)

    def tearDown(self):
        self.temp.cleanup()

    def submit(self, form=None, headers=None):
        return self.app.post('/website/appointments',{},self.form if form is None else form,self.headers if headers is None else headers)

    def count(self):
        with closing(sqlite3.connect(self.db)) as db:
            return db.execute('SELECT count(*) FROM website_requests').fetchone()[0]

    def test_valid_request_persisted_without_false_booking(self):
        response=self.submit()
        self.assertEqual(response.status,201)
        data=json.loads(response.body)
        self.assertEqual(data['status'],'pending')
        self.assertNotIn('13800000000',response.body.decode())
        with closing(sqlite3.connect(self.db)) as db:
            row=db.execute('SELECT status,consent_version FROM website_requests').fetchone()
            self.assertEqual(row,('new','website-20261023'))
            self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name='appointments'").fetchone()[0],0)

    def test_retry_returns_same_reference(self):
        first=self.submit(); second=self.submit()
        self.assertEqual(second.status,200)
        self.assertEqual(json.loads(first.body)['reference'],json.loads(second.body)['reference'])
        self.assertEqual(self.count(),1)

    def test_parallel_retry_is_atomic(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            responses=list(pool.map(lambda _: self.submit(),range(6)))
        self.assertEqual(sum(r.status==201 for r in responses),1)
        self.assertEqual(self.count(),1)

    def test_key_reuse_with_changed_payload_rejected(self):
        self.submit()
        self.assertEqual(self.submit({**self.form,'message':'different'}).status,409)
        self.assertEqual(self.count(),1)

    def test_origin_and_consent_required(self):
        for headers in ({},{'Origin':'https://evil.test'},{'Origin':'https://example.test.evil.test'}):
            self.assertEqual(self.submit(headers=headers).status,403)
        self.assertEqual(self.submit({**self.form,'consent':'no'}).status,422)
        self.assertEqual(self.count(),0)

    def test_validation_and_honeypot(self):
        for patch in ({'company':'bot'},{'customer_name':''},{'customer_name':'x\ny'},
                      {'customer_phone':'abc'},{'preferred_date':'2020-01-01'},
                      {'preferred_date':'not-a-date'},{'preferred_time':'25:00'},
                      {'message':'x'*501},{'idempotency_key':'bad'}):
            with self.subTest(patch=patch):
                self.assertEqual(self.submit({**self.form,**patch}).status,422)
        self.assertEqual(self.count(),0)

    def test_phone_rate_limit_survives_new_store(self):
        for _ in range(3):
            self.assertEqual(self.submit({**self.form,'idempotency_key':str(uuid4())}).status,201)
        self.assertEqual(self.submit({**self.form,'idempotency_key':str(uuid4())}).status,429)
        self.assertEqual(self.count(),3)
        self.assertEqual(self.submit().status,429)

    def test_ip_limit_and_no_raw_ip_storage(self):
        for n in range(10):
            response=self.submit({**self.form,'idempotency_key':str(uuid4()),'customer_phone':f'1390000{n:04d}'})
            self.assertEqual(response.status,201)
        self.assertEqual(self.submit().status,429)
        with closing(sqlite3.connect(self.db)) as db:
            self.assertNotEqual(db.execute('SELECT ip_hash FROM website_rate_events LIMIT 1').fetchone()[0],'192.0.2.1')

    def test_staff_auth_csrf_and_stale_update(self):
        reference=json.loads(self.submit().body)['reference']
        self.assertEqual(self.app.get('/staff/inventory/website',{},{}).status,302)
        update=dict(request_id=reference,status='contacted',version='1',csrf=self.csrf)
        self.assertEqual(self.app.post('/staff/inventory/website',{},update,{}).status,403)
        self.assertEqual(self.app.post('/staff/inventory/website',{},dict(update,csrf='bad'),self.staff_headers).status,403)
        self.assertEqual(self.app.post('/staff/inventory/website',{},update,self.staff_headers).status,303)
        self.assertEqual(self.app.post('/staff/inventory/website',{},update,self.staff_headers).status,409)
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute('SELECT status,version FROM website_requests').fetchone(),('contacted',2))
            self.assertEqual(db.execute('SELECT count(*) FROM website_request_events').fetchone()[0],1)

    def test_staff_html_escapes_content(self):
        self.submit({**self.form,'customer_name':'<script>alert(1)</script>','message':'<img src=x onerror=alert(1)>'})
        page=self.app.get('/staff/inventory/website',{},self.staff_headers).body.decode()
        self.assertIn('&lt;script&gt;',page)
        self.assertNotIn('<script>alert',page)
        self.assertIn('网站预约',page)

    def test_pagination_keeps_all_requests_accessible(self):
        store=self.app.website_store
        for n in range(27):
            payload={key:self.form[key] for key in ('customer_name','customer_phone','preferred_date','preferred_time','message')}
            payload['customer_phone']=f'1390000{n:04d}'
            store.submit(payload,str(uuid4()),f'192.0.2.{n}')
        first,total=store.list_requests('',0); second,_=store.list_requests('',1)
        self.assertEqual((len(first),len(second),total),(25,2,27))

    def test_real_http_transport(self):
        server=create_wecom_callback_server('127.0.0.1',0,object(),self.app)
        worker=threading.Thread(target=server.serve_forever,daemon=True); worker.start()
        try:
            connection=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=3)
            connection.request('POST','/website/appointments',body=urlencode(self.form),headers={**self.headers,'Content-Type':'application/x-www-form-urlencoded'})
            response=connection.getresponse(); body=response.read()
            self.assertEqual(response.status,201)
            self.assertEqual(response.getheader('Cache-Control'),'no-store')
            self.assertEqual(json.loads(body)['status'],'pending')
            connection.close()
        finally:
            server.shutdown(); server.server_close(); worker.join(timeout=3)


if __name__=='__main__':
    unittest.main()
