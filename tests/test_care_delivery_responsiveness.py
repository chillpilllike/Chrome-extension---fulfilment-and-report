import asyncio
import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import sqlite3
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from starlette.requests import Request
from app.services.care_delivery import Delivery, RECONCILE_QUERY, verify_receipt


class QueryEquivalenceTests(unittest.TestCase):
    def test_current_historical_duplicate_unmatched_and_processed_events(self):
        c = sqlite3.connect(':memory:')
        c.executescript('''
            CREATE TABLE after_order_messages(id INTEGER, case_id INTEGER,status TEXT,recipient TEXT,test_mode INTEGER,provider TEXT,provider_message_id TEXT);
            CREATE TABLE after_order_email_attempts(message_id INTEGER,provider_message_id TEXT);
            CREATE TABLE after_order_delivery_events(event_id TEXT,provider_message_id TEXT,processed_at TEXT,created_at TEXT);
            INSERT INTO after_order_messages VALUES(1,1,'sent','test',1,'resend','new'),(2,2,'sent','test',1,'other','other');
            INSERT INTO after_order_email_attempts VALUES(1,'old'),(1,'new'),(1,'old'),(2,'other-old');
            INSERT INTO after_order_delivery_events VALUES('a','new',NULL,'1'),('b','old',NULL,'2'),('c','missing',NULL,'3'),('d','other',NULL,'4'),('e','new','done','5'),('f','other-old',NULL,'6');
        ''')
        rows = c.execute(RECONCILE_QUERY).fetchall()
        self.assertEqual([row[0] for row in rows], ['a', 'b'])
        c.close()


class ReconcileGuardTests(unittest.TestCase):
    def make_delivery(self):
        d = Delivery.__new__(Delivery)
        d._reconcile_lock = threading.Lock()
        d.r = SimpleNamespace(db=Mock())
        return d

    def test_overlapping_reconcile_skips_before_database_checkout(self):
        d = self.make_delivery()
        d._reconcile_lock.acquire()
        try:
            d.reconcile()
            d.r.db.assert_not_called()
        finally:
            d._reconcile_lock.release()

    def test_failure_releases_lock(self):
        d = self.make_delivery()
        d._reconcile = Mock(side_effect=RuntimeError('busy'))
        with self.assertRaises(RuntimeError): d.reconcile()
        d._reconcile = Mock()
        d.reconcile()
        d._reconcile.assert_called_once()

    def test_cross_process_contention_skips_query(self):
        d = self.make_delivery()
        conn = Mock()
        conn.execute.return_value.fetchone.return_value = {'locked': False}
        @contextmanager
        def db(): yield conn
        d.r.db = db
        d.reconcile()
        self.assertEqual(conn.execute.call_count, 1)


class WebhookResponsivenessTests(unittest.IsolatedAsyncioTestCase):
    def setup_delivery(self):
        d = Delivery.__new__(Delivery)
        self.secret = 'whsec_' + base64.b64encode(b'offline-test-key').decode()
        d.r = SimpleNamespace(os=SimpleNamespace(getenv=lambda *args: self.secret), utc_now=lambda: 'now')
        return d

    def request(self, payload, valid=True):
        body = json.dumps(payload).encode()
        stamp = str(int(time.time()))
        signature = base64.b64encode(hmac.new(b'offline-test-key', b'evt.'+stamp.encode()+b'.'+body, hashlib.sha256).digest()).decode()
        headers = [(b'svix-id', b'evt'), (b'svix-timestamp', stamp.encode()), (b'svix-signature', ('v1,'+signature if valid else 'v1,invalid').encode())]
        async def receive(): return {'type': 'http.request','body': body,'more_body': False}
        return Request({'type':'http','method':'POST','path':'/','headers':headers},receive)

    async def test_slow_database_work_keeps_event_loop_responsive(self):
        d = self.setup_delivery()
        release = threading.Event()
        worker_threads = []
        def slow_work(*args):
            worker_threads.append(threading.get_ident())
            if not release.wait(1): raise AssertionError('Event loop blocked by webhook')
        d.accept_receipt = slow_work
        endpoint = d.router().routes[0].endpoint
        task = asyncio.create_task(endpoint(self.request({'type':'email.delivered','data':{'email_id':'offline'}})))
        try:
            await asyncio.sleep(.03)
            release.set()
            self.assertEqual(await task, {'ok': True})
            self.assertTrue(worker_threads)
            self.assertNotEqual(worker_threads[0], threading.get_ident())
        finally:
            release.set()

    async def test_invalid_signature_and_ignored_event_do_not_touch_database(self):
        d = self.setup_delivery()
        d.accept_receipt = Mock()
        endpoint = d.router().routes[0].endpoint
        with self.assertRaises(HTTPException) as caught:
            await endpoint(self.request({'type':'email.delivered','data':{}},valid=False))
        self.assertEqual(caught.exception.status_code,400)
        self.assertEqual(await endpoint(self.request({'type':'unsupported','data':{}})),{'ok':True,'ignored':True})
        d.accept_receipt.assert_not_called()


if __name__ == '__main__': unittest.main()
