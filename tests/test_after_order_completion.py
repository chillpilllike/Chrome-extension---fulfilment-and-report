import ast
import base64
import hashlib
import hmac
import json
import re
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlparse
from fastapi import HTTPException
from app.services.care_delivery import Delivery,SCHEMA as DELIVERY_SCHEMA,verify_receipt
from app.services.care_requests import Requests,SCHEMA as REQUEST_SCHEMA,Approval,ParcelMapping
from app.services.alternative_workflow import SCHEMA as ALTERNATIVE_SCHEMA,Workflow
from app.services.after_order_email_images import with_email_recommendations
from app.services.after_order_email import render_after_order_email


class ReceiptSignatures(unittest.TestCase):
    def test_official_svix_test_vector(self):
        event,payload = verify_receipt(b'{"event_type":"ping","data":{"success":true}}',{
            'svix-id':'msg_loFOjxBNrRLzqYUf','svix-timestamp':'1731705121',
            'svix-signature':'v1,rAvfW3dJ/X/qxhsaXPOyyCGmRKsaKWcsNccKXlIktD0='},
            'whsec_plJ3nmyCDGBKInavdOK15jsl',now=1731705121)
        self.assertTrue(payload['data']['success'])

    def test_modified_expired_future_and_missing_signatures_fail(self):
        headers={'svix-id':'msg_loFOjxBNrRLzqYUf','svix-timestamp':'1731705121',
                 'svix-signature':'v1,rAvfW3dJ/X/qxhsaXPOyyCGmRKsaKWcsNccKXlIktD0='}
        for body,change,now in [(b'{}',{},1731705121),(b'{}',{},1731705522),(b'{}',{},1731704000),(b'{}',{'svix-signature':''},1731705121)]:
            with self.assertRaises(ValueError):
                verify_receipt(body,{**headers,**change},'whsec_plJ3nmyCDGBKInavdOK15jsl',now=now)


class CompletionDB(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row
        self.conn.executescript('''CREATE TABLE after_order_cases(id INTEGER PRIMARY KEY,confirmed_at TEXT,current_decision TEXT,status TEXT,decision_fingerprint TEXT,decision_version INTEGER DEFAULT 0,updated_at TEXT);
            CREATE TABLE order_lines(id INTEGER PRIMARY KEY,store_id INTEGER,odoo_order_id INTEGER,odoo_line_id INTEGER,product_name TEXT,quantity REAL,state TEXT);
            CREATE TABLE after_order_messages(id INTEGER PRIMARY KEY,case_id INTEGER,provider TEXT,provider_message_id TEXT,recipient TEXT,test_mode INTEGER,status TEXT,template_kind TEXT,payload_json TEXT,request_fingerprint TEXT,updated_at TEXT);
            CREATE TABLE after_order_email_attempts(message_id INTEGER,provider_message_id TEXT);
            CREATE TABLE after_order_action_links(id INTEGER PRIMARY KEY,case_id INTEGER,invalidated_at TEXT,request_fingerprint TEXT);
            INSERT INTO after_order_cases(id,status) VALUES(1,'needs_attention');
            INSERT INTO order_lines VALUES(11,1,101,201,'Original A',1,'missing');
            INSERT INTO order_lines VALUES(12,1,101,202,'Original B',2,'missing');
            INSERT INTO after_order_action_links VALUES(1,1,NULL,'fp');''')
        self.conn.executescript(ALTERNATIVE_SCHEMA+DELIVERY_SCHEMA+REQUEST_SCHEMA)
        @contextmanager
        def db():
            class Adapter:
                def execute(_,sql,params=()):return self.conn.execute(sql.replace(' FOR UPDATE',''),params)
            with self.conn:yield Adapter()
        self.events=[];self.mode=False;self.partial=True;self.fingerprint='fp';self.blocked=False
        self.base={'id':1,'store_id':1,'odoo_order_id':101,'odoo_order_name':'TEST101','website_id':1,'customer_email':'buyer@example.test',
                   'case_type':'item_unavailable','affected_items':[{'line_id':11,'product_name':'Original A'},{'line_id':12,'product_name':'Original B'}]}
        def case(_):return {**self.base,**dict(self.conn.execute('SELECT * FROM after_order_cases WHERE id=1').fetchone())}
        self.ns={'db':db,'utc_now':lambda:datetime.now(timezone.utc).isoformat(),'record_after_order_event':lambda *a,**kw:self.events.append((a,kw)),
             'after_order_case_by_id':case,'after_order_email_test_mode':lambda:self.mode,'after_order_case_is_in_scope':lambda c:True,
             'request_fingerprint':lambda c:self.fingerprint,'after_order_unavailable_review':lambda c:{'blocked':self.blocked},
             'after_order_removal_allowed':lambda c:self.partial,'require_after_order_case_in_scope':lambda c:None}
        self.delivery=Delivery(self.ns);self.ns['care_delivery']=self.delivery
        self.workflow=Mock(event=Mock(side_effect=lambda *a,**kw:self.events.append((a,kw))))
        self.workflow.case_line.side_effect=lambda c,l:(case(c),dict(self.conn.execute('SELECT * FROM order_lines WHERE id=?',(l,)).fetchone()))
        self.ns['alternative_workflow']=self.workflow
        self.requests=Requests(self.ns);self.ns['care_requests']=self.requests
        self.conn.commit()

    def tearDown(self):self.conn.close()

    def message(self,mode=0,policy=True):
        return {'id':1,'case_id':1,'test_mode':mode,'template_kind':'item_unavailable','request_fingerprint':'fp',
                'payload_json':json.dumps({'_care_policy':'three-day-v1'} if policy else {})}

    def delivered(self,days=0):
        self.requests.delivered(self.message(),(datetime.now(timezone.utc)-timedelta(days=days)).isoformat())

    def test_deadline_exactly_three_days_and_duplicate_does_not_extend(self):
        self.delivered()
        first=dict(self.conn.execute('SELECT * FROM after_order_response_windows LIMIT 1').fetchone())
        self.requests.delivered(self.message(),(datetime.now(timezone.utc)-timedelta(days=1)).isoformat())
        second=dict(self.conn.execute('SELECT * FROM after_order_response_windows LIMIT 1').fetchone())
        self.assertEqual(first['deadline_at'],second['deadline_at'])
        self.assertEqual(datetime.fromisoformat(first['deadline_at'])-datetime.fromisoformat(first['delivered_at']),timedelta(days=3))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM after_order_response_windows').fetchone()[0],2)

    def test_stale_receipts_do_not_starve_newer_deadlines(self):
        stamp=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
        for number in range(1,202):
            self.conn.execute('INSERT INTO after_order_messages(id,case_id,provider_message_id,test_mode,status,template_kind,payload_json,request_fingerprint) VALUES(?,1,?,0,?,?,?,?)',
                (number,str(number),'delivered','item_unavailable',json.dumps({'_care_policy':'three-day-v1'}),'old' if number<=200 else 'fp'))
            self.conn.execute('INSERT INTO after_order_delivery_events(event_id,provider_message_id,event_type,occurred_at,created_at) VALUES(?,?,?,?,?)',
                (str(number),str(number),'email.delivered',stamp,stamp))
        self.conn.commit()
        self.requests.sync_delivered()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM after_order_response_windows').fetchone()[0],0)
        self.requests.sync_delivered()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM after_order_response_windows').fetchone()[0],2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM after_order_deadline_receipt_checks').fetchone()[0],201)

    def test_test_old_policy_changed_issue_and_future_receipts_cannot_start_clock(self):
        now=datetime.now(timezone.utc).isoformat()
        for message in [self.message(mode=1),self.message(policy=False),{**self.message(),'request_fingerprint':'old'}]:self.requests.delivered(message,now)
        self.requests.delivered(self.message(),(datetime.now(timezone.utc)+timedelta(days=1)).isoformat())
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM after_order_response_windows').fetchone()[0],0)

    def test_partial_timeout_records_system_removal_not_fake_customer_choice(self):
        self.delivered(days=4);self.requests.run_due()
        rows=self.requests.rows(1)['removals']
        self.assertEqual({r['origin'] for r in rows},{'no_response'})
        self.assertEqual({r['status'] for r in rows},{'auto_remove_pending'})
        self.requests.run_due()
        self.assertEqual(len(self.requests.rows(1)['removals']),2)

    def test_no_remaining_product_goes_to_cancel_refund_review(self):
        self.partial=False;self.delivered(days=4);self.requests.run_due()
        self.assertEqual({r['status'] for r in self.requests.rows(1)['removals']},{'cancel_review'})
        self.assertFalse(self.conn.execute('SELECT confirmed_at FROM after_order_cases').fetchone()[0])

    def test_existing_customer_cancel_is_preserved(self):
        self.delivered(days=4);self.conn.execute("UPDATE after_order_cases SET current_decision='cancel_order'");self.conn.commit()
        self.requests.run_due();self.assertEqual(self.requests.rows(1)['removals'],[])

    def test_changed_fulfilment_and_test_mode_never_trigger_removal(self):
        self.delivered(days=4);self.mode=True;self.requests.run_due();self.assertEqual(self.requests.rows(1)['removals'],[])
        self.mode=False;self.blocked=True;self.requests.run_due();self.assertEqual(self.requests.rows(1)['removals'],[])

    def test_selected_line_is_protected_while_other_line_times_out(self):
        self.delivered(days=4)
        now=datetime.now(timezone.utc).isoformat()
        self.conn.execute('INSERT INTO after_order_line_selections(case_id,line_id,test_mode,product_json,first_selected_at,deadline_at,issue_fingerprint,updated_at) VALUES(1,11,0,?,?,?,?,?)',('{}',now,now,'fp',now));self.conn.commit()
        self.requests.run_due();self.assertEqual([r['line_id'] for r in self.requests.rows(1)['removals']],[12])

    def test_removal_decision_is_line_scoped_and_versioned(self):
        self.requests.remove(self.base,{'id':1},11)
        self.requests.remove(self.base,{'id':1},11)
        rows=self.requests.rows(1)['removals'];self.assertEqual(len(rows),1);self.assertEqual(rows[0]['version'],2)
        self.assertEqual(rows[0]['line_id'],11)

    def test_approved_removal_cannot_be_changed(self):
        self.requests.remove(self.base,{'id':1},11)
        self.conn.execute("UPDATE after_order_line_removals SET approved_at='approved'");self.conn.commit()
        with self.assertRaises(HTTPException):self.requests.remove(self.base,{'id':1},11)

    def test_expired_initial_response_cannot_remove_using_an_old_link(self):
        self.delivered(days=4)
        with self.assertRaises(HTTPException):self.requests.remove(self.base,{'id':1},11)

    def test_delivery_replay_and_out_of_order_events_preserve_suppression(self):
        self.conn.execute("INSERT INTO after_order_messages(id,case_id,provider,provider_message_id,recipient,test_mode,status) VALUES(1,1,'resend','mail-1','buyer@example.test',0,'sent')")
        for i,kind in enumerate(['email.delivered','email.bounced','email.sent']):
            self.conn.execute('INSERT INTO after_order_delivery_events(event_id,provider_message_id,event_type,occurred_at,created_at) VALUES(?,?,?,?,?)',(str(i),'mail-1',kind,'2026-09-06','2026-09-06'))
        self.conn.commit();self.delivery.reconcile();count=len(self.events);self.delivery.reconcile()
        self.assertEqual(count,len(self.events));self.assertEqual(self.conn.execute('SELECT status FROM after_order_messages').fetchone()[0],'bounced')
        self.assertEqual(self.delivery.suppressed('buyer@example.test',False),'bounced');self.assertFalse(self.delivery.suppressed('buyer@example.test',True))

    def test_unmatched_receipt_waits_for_send_acknowledgement(self):
        self.conn.execute("INSERT INTO after_order_delivery_events(event_id,provider_message_id,event_type,occurred_at,created_at) VALUES('e','late','email.delivered','2026-09-06','2026-09-06')");self.conn.commit()
        self.delivery.reconcile();self.assertIsNone(self.conn.execute('SELECT processed_at FROM after_order_delivery_events').fetchone()[0])
        self.conn.execute("INSERT INTO after_order_messages(id,case_id,provider,provider_message_id,recipient,test_mode,status) VALUES(1,1,'resend','late','buyer@example.test',0,'sent')");self.conn.commit()
        self.delivery.reconcile();self.assertEqual(self.conn.execute('SELECT status FROM after_order_messages').fetchone()[0],'delivered')

    def test_repeated_scheduling_reaches_cases_beyond_first_200(self):
        self.conn.executemany("INSERT INTO after_order_cases(id,status) VALUES(?,'needs_attention')",[(i,) for i in range(2,402)])
        self.conn.execute('CREATE TABLE after_order_execution_jobs(id INTEGER,status TEXT)');self.conn.commit()
        ticks=iter(['2026-09-06T10:00:00+00:00']*200+['2026-09-06T10:01:00+00:00']*200+['2026-09-06T10:02:00+00:00']*200)
        scope={**self.ns,'Any':object,'sync_after_order_cases':Mock(),'clean_text':lambda v:str(v or ''),
            'get_service_settings':lambda:{'after_order_automation_enabled':'true','after_order_public_base_url':'https://app.example'},
            'os':SimpleNamespace(getenv=lambda *a:''),'urlparse':urlparse,'Request':lambda x:x,
            'rows_to_dicts':lambda rows:[dict(row) for row in rows],'after_order_case_by_id':lambda _:None,
            'care_delivery':Mock(),'care_requests':Mock(),'care_reminders':Mock(),'utc_now':lambda:next(ticks),'HTTPException':HTTPException}
        tree=ast.parse((Path(__file__).parents[1]/'app/main.py').read_text())
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_after_order_automation')
        exec(compile(ast.Module(body=[node],type_ignores=[]),'scheduler','exec'),scope)
        for _ in range(3):scope['run_after_order_automation']()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM after_order_case_checks').fetchone()[0],401)

    def test_replayed_old_send_cannot_downgrade_current_delivered_attempt(self):
        self.conn.execute("INSERT INTO after_order_messages(id,case_id,provider,provider_message_id,recipient,test_mode,status) VALUES(1,1,'resend','new','buyer@example.test',0,'delivered')")
        self.conn.execute("INSERT INTO after_order_email_attempts VALUES(1,'old')")
        self.conn.execute("INSERT INTO after_order_delivery_events(event_id,provider_message_id,event_type,occurred_at,created_at) VALUES('old-fail','old','email.failed','2026-09-06','2026-09-06')")
        self.conn.commit();self.delivery.reconcile()
        self.assertEqual(self.conn.execute('SELECT status FROM after_order_messages').fetchone()[0],'delivered')

    def test_new_case_mappings_require_unique_positive_quantities(self):
        with self.assertRaises(ValueError):ParcelMapping(items=[{'line_id':11,'quantity':float('nan')}],verified_by='Team')
        with self.assertRaises(ValueError):ParcelMapping(items=[{'line_id':11,'quantity':0}],verified_by='Team')
        self.base['tracking_code']='PARCEL1'
        endpoint=next(route.endpoint for route in self.requests.router().routes if route.path.endswith('/parcel-items'))
        with self.assertRaises(HTTPException):endpoint(1,ParcelMapping(items=[{'line_id':11,'quantity':1},{'line_id':11,'quantity':1}],verified_by='Team'))

    def test_excessive_or_cross_order_parcel_quantity_is_rejected(self):
        self.base['tracking_code']='PARCEL1'
        endpoint=next(route.endpoint for route in self.requests.router().routes if route.path.endswith('/parcel-items'))
        for item in ({'line_id':11,'quantity':2},{'line_id':999,'quantity':1}):
            with self.assertRaises(HTTPException):endpoint(1,ParcelMapping(items=[item],verified_by='Team'))

    def test_test_mode_approval_never_calls_odoo_or_changes_live_requests(self):
        self.mode=True
        endpoint=next(route.endpoint for route in self.requests.router().routes if route.path.endswith('/approve-removal'))
        result=endpoint(1,11,Approval(version=1,approved_by='Team'))
        self.assertIn('Test preview',result['message'])
        self.assertEqual(self.requests.rows(1)['removals'],[])

    def test_payment_rechecked_before_asin_replacement_and_queue_is_not_duplicated(self):
        for name in ('original_asin','asin','original_product_name','replacement_asin','replacement_product_name','replacement_note','replacement_assigned_at',
                     'missing_asin','amazon_status','amazon_group_key','chrome_claimed_by','chrome_claimed_at','chrome_claim_expires_at','last_error','updated_at','order_engine'):
            self.conn.execute('ALTER TABLE order_lines ADD COLUMN '+name+' TEXT')
        for name in ('confirmed_by','decision_locked_at'):
            self.conn.execute('ALTER TABLE after_order_cases ADD COLUMN '+name+' TEXT')
        self.conn.execute('ALTER TABLE after_order_action_links ADD COLUMN updated_at TEXT')
        now=datetime.now(timezone.utc).isoformat()
        self.conn.execute("UPDATE after_order_cases SET current_decision='offer_alternatives'")
        for line_id in (11,12):
            product={'product_id':line_id+100,'name':'Replacement '+str(line_id),'difference':0,'pricing_signature':'price','currency':'USD'}
            self.conn.execute('''INSERT INTO after_order_line_selections(case_id,line_id,test_mode,product_json,first_selected_at,deadline_at,issue_fingerprint,updated_at,status,result_json)
                VALUES(1,?,0,?,?,?,?,?,'ready_to_release',?)''',(line_id,json.dumps(product),now,now,'fp',now,json.dumps({'asin':'B0000000'+str(line_id)})))
        self.conn.commit()
        paid=False
        native=Mock(execute=Mock(side_effect=lambda *args:{'status':'ready' if paid else 'waiting_payment','payment_verified':paid}))
        queue=Mock(return_value=(2,'Queued'))
        self.ns.update({'get_store':lambda _:object(),'OdooClient':lambda _:native,'fast_page_cache_clear_matching':Mock(),
                        'index_order_line':Mock(),'auto_chrome_ordering_enabled':lambda:True,'auto_queue_ready_missing_order':queue,'clean_error_message':str})
        workflow=Workflow(self.ns);self.ns['alternative_workflow']=workflow
        workflow.release(1)
        self.assertEqual(self.conn.execute('SELECT state FROM order_lines WHERE id=11').fetchone()[0],'missing');queue.assert_not_called()
        paid=True
        # The due-payment worker resolves and stores the ASIN again when payment becomes ready.
        for line_id in (11,12):
            self.conn.execute("UPDATE after_order_line_selections SET status='ready_to_release',result_json=? WHERE line_id=?",(json.dumps({'asin':'B0000000'+str(line_id),'payment_verified':True}),line_id))
        self.conn.commit()
        workflow.release(1)
        for row in self.conn.execute('SELECT * FROM order_lines'):
            self.assertEqual(row['state'],'pulled');self.assertEqual(row['asin'],'B0000000'+str(row['id']))
        queue.assert_called_once();workflow.release(1);queue.assert_called_once()


class RecommendationEmailTests(unittest.TestCase):
    def test_line_and_product_focus_does_not_confirm_from_email_get(self):
        case={'case_type':'item_unavailable','sender_domain':'shop.example','affected_items':[{'line_id':11,'product_name':'Original'}]}
        enriched=with_email_recommendations(case,[{'line_id':11,'recommendations':[{'product_tmpl_id':9,'name':'New <Product>','website_url':'/shop/product-name-9'}]}],'https://shop.example/order-update/token?access_token=secret')
        p=enriched['affected_items'][0]['recommendations'][0]
        self.assertIn('line_id=11',p['select_url']);self.assertIn('selected_product_id=9',p['select_url'])
        self.assertNotIn('/decision',p['select_url']);self.assertNotIn('secret',p['details_url'])
        _,markup,plain=render_after_order_email(enriched,'https://shop.example/order-update/token',actions=[],labels={})
        self.assertIn('New &lt;Product&gt;',markup);self.assertIn('target="_blank"',markup);self.assertIn('Review and confirm',plain)

    def test_product_detail_url_cannot_escape_website(self):
        case={'sender_domain':'shop.example','affected_items':[{'line_id':1}]}
        enriched=with_email_recommendations(case,[{'line_id':1,'recommendations':[{'product_tmpl_id':9,'name':'Item','website_url':'//evil.example'}]}],'https://shop.example/order-update/token')
        self.assertEqual(enriched['affected_items'][0]['recommendations'][0]['details_url'],'https://shop.example/shop/9')
