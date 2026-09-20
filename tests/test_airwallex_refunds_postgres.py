"""Opt-in PostgreSQL tests in a uniquely named, disposable schema; no Airwallex calls."""
import json
import os
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal

import psycopg2
from psycopg2.extras import RealDictCursor
from app.services.airwallex_refunds import AirwallexRefunds


@unittest.skipUnless(os.getenv('REFUND_TEST_POSTGRES_URL'), 'Isolated PostgreSQL test URL not supplied')
class PostgresConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.url=os.environ['REFUND_TEST_POSTGRES_URL']
        self.schema='airwallex_test_'+uuid.uuid4().hex
        c=psycopg2.connect(self.url)
        with c:
            with c.cursor() as cur:cur.execute('CREATE SCHEMA '+self.schema)
        c.close()
        self.cfg={'client_id':'test-client','api_key':'test-only-secret','state':'enabled'}
        self.service=AirwallexRefunds(db=self.db,get_store=None,client_factory=None,configuration=lambda:self.cfg,staff_check=lambda r:True)
        self.service.init_db();self.created=[]
        self.service.call=self.call
        def snapshot(*args):
            with self.db() as c:
                rows=c.execute('SELECT amount FROM airwallex_refund_payouts').fetchall()
            return {'order_key':'same-order','order_name':'TEST','currency':'CAD','rounding':'.01',
                    'remaining':str(Decimal('100')-sum((r['amount'] for r in rows),Decimal(0)))}
        self.service.snapshot=snapshot
    def tearDown(self):
        c=psycopg2.connect(self.url)
        with c:
            with c.cursor() as cur:cur.execute('DROP SCHEMA '+self.schema+' CASCADE')
        c.close()
    @contextmanager
    def db(self):
        c=psycopg2.connect(self.url)
        class Adapter:
            def execute(self,q,p=()):
                cur=c.cursor(cursor_factory=RealDictCursor);cur.execute(q.replace('?','%s'),p);return cur
        try:
            with c.cursor() as cur:cur.execute('SET search_path TO '+self.schema)
            yield Adapter();c.commit()
        except:c.rollback();raise
        finally:c.close()
    def call(self,method,path,**kw):
        if path=='/api/v1/transfers':return {'items':[]}
        if path.endswith('balances/current'):return [{'currency':'CAD','available_amount':500}]
        if path.endswith('/create'):
            # Reservation must be visible from a separate DB connection before transmission.
            with self.db() as c:
                row=c.execute('SELECT status FROM airwallex_refund_payouts WHERE request_id=?',(kw['data']['request_id'],)).fetchone()
            assert row['status']=='SUBMITTING'
            self.created.append(kw['data']['request_id'])
            return {**kw['data'],'id':str(uuid.uuid4()),'status':'PROCESSING'}
        return {}
    def token(self,amount='100'):
        t={'request_id':str(uuid.uuid4()),'transfer_amount':float(amount),'transfer_currency':'CAD',
           'beneficiary':{'bank_details':{'account_name':'Test recipient'}}}
        p={'store_id':1,'order_id':1,'order_key':'same-order','order_name':'TEST','amount':amount,
           'account_key':self.service.account_key(self.cfg),'transfer':t,'edit_reason':'test'}
        return self.service.cipher(self.cfg).encrypt(json.dumps(p).encode()).decode()
    def test_concurrent_distinct_reviews_cannot_over_refund(self):
        tokens=[self.token('70'),self.token('70')];gate=threading.Barrier(2)
        def send(t):
            gate.wait()
            try:return self.service.submit(t)['status']
            except ValueError:return 'BLOCKED'
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(send,tokens))
        self.assertEqual(sorted(results),['BLOCKED','PROCESSING']);self.assertEqual(len(self.created),1)
    def test_concurrent_repeated_token_creates_once(self):
        token=self.token();gate=threading.Barrier(2)
        def send(_):gate.wait();return self.service.submit(token)
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(send,range(2)))
        self.assertEqual(len(self.created),1);self.assertEqual(results[0]['request_id'],results[1]['request_id'])

    def test_global_daily_cap_serializes_different_orders(self):
        self.service.snapshot=lambda store,order:{'order_key':f'order-{order}','order_name':f'TEST-{order}',
            'currency':'CAD','rounding':'.01','remaining':'100'}
        tokens=[]
        for i in range(8):
            payload=json.loads(self.service.cipher(self.cfg).decrypt(self.token('1').encode()))
            payload.update(order_id=i,order_key=f'order-{i}')
            tokens.append(self.service.cipher(self.cfg).encrypt(json.dumps(payload).encode()).decode())
        gate=threading.Barrier(8)
        def send(token):
            gate.wait()
            try:return self.service.submit(token)['status']
            except ValueError as e:
                self.assertIn('daily limit',str(e));return 'BLOCKED'
        with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(send,tokens))
        self.assertEqual(results.count('PROCESSING'),5)
        self.assertEqual(results.count('BLOCKED'),3)
        self.assertEqual(len(self.created),5)
        # An already-submitted review can still be checked without spending another slot.
        for token in tokens:
            rid=json.loads(self.service.cipher(self.cfg).decrypt(token.encode()))['transfer']['request_id']
            if rid in self.created:
                self.service.submit(token)
                break
        self.assertEqual(len(self.created),5)

if __name__=='__main__':unittest.main()
