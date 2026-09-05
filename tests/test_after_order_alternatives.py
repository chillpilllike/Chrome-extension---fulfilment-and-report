import ast
import json
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from app.services.alternative_selection import selection_change, money_difference, price_fingerprint, resolved_asin
from app.services.alternative_workflow import Workflow, SCHEMA, Recommendations
from app.services.asin import encode_asin, extract_asin_from_notes, decode_asin_reference, normalize_asin


class SelectionRules(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026,9,6,10,tzinfo=timezone.utc)

    def test_first_choice_gets_exactly_24_hours(self):
        value = selection_change(None, {'name':'A'}, self.now)
        self.assertEqual(value['deadline_at'], (self.now+timedelta(hours=24)).isoformat())

    def test_change_replaces_product_without_resetting_deadline(self):
        first = {**selection_change(None, {'name':'A'}, self.now),'status':'choosing'}
        second = selection_change(first, {'name':'B'}, self.now+timedelta(hours=23))
        self.assertEqual(first['deadline_at'],second['deadline_at'])
        self.assertEqual(second['product']['name'],'B')
        self.assertEqual(second['version'],2)

    def test_exact_deadline_is_locked(self):
        first = {**selection_change(None,{},self.now),'status':'choosing'}
        with self.assertRaises(ValueError):
            selection_change(first,{},self.now+timedelta(hours=24))

    def test_processed_cannot_be_changed_even_before_deadline(self):
        first = {**selection_change(None,{},self.now),'status':'processed'}
        with self.assertRaises(ValueError):
            selection_change(first,{},self.now)

    def test_money_currency_rounding(self):
        self.assertEqual(money_difference('19.99','12.50'),-7.49)
        self.assertEqual(money_difference('12.50','19.99'),7.49)
        self.assertEqual(money_difference('10','10'),0)
        for bad in ('NaN','Infinity',-1):
            with self.assertRaises(ValueError):
                money_difference(bad,10)

    def test_asin_conflict_requires_review(self):
        with self.assertRaises(ValueError):
            resolved_asin({'description':'Amazon ASIN: B000000001','default_code':encode_asin('B000000002')},extract_asin_from_notes,decode_asin_reference,normalize_asin)

    def test_asin_decoding_and_manual_fallback(self):
        self.assertEqual(resolved_asin({'default_code':encode_asin('B000000001')},extract_asin_from_notes,decode_asin_reference,normalize_asin),'B000000001')
        self.assertEqual(resolved_asin({'default_code':'SHOP-SKU'},extract_asin_from_notes,decode_asin_reference,normalize_asin),'')

    def test_pricing_fingerprint_changes_on_paid_amount_or_variant(self):
        self.assertNotEqual(price_fingerprint({'product_id':1,'original_total':10}),price_fingerprint({'product_id':1,'original_total':9}))


class TestIsolation(unittest.TestCase):
    def test_odoo_quote_email_log_is_idempotent_and_tracks_status(self):
        conn=sqlite3.connect(':memory:'); conn.row_factory=sqlite3.Row
        conn.executescript('''CREATE TABLE after_order_messages(id INTEGER PRIMARY KEY,case_id INTEGER,provider TEXT,
            recipient TEXT,sender TEXT,subject TEXT,html_preview TEXT,status TEXT,idempotency_key TEXT UNIQUE,
            provider_message_id TEXT,payload_json TEXT,created_at TEXT,updated_at TEXT,test_mode INTEGER,
            request_fingerprint TEXT,template_kind TEXT,related_items_json TEXT,last_error TEXT,attempt_count INTEGER DEFAULT 1);
            CREATE TABLE after_order_email_attempts(message_id INTEGER,attempt_number INTEGER,status TEXT,error TEXT,
                provider_message_id TEXT,created_at TEXT,updated_at TEXT,UNIQUE(message_id,attempt_number));''')
        workflow=Workflow({'utc_now':lambda:'now','request_fingerprint':lambda case:'signature'})
        case={'id':1,'store_id':2,'affected_items':[{'line_id':3,'product_name':'Original'}]}
        result={'quote_id':40,'mail':{'id':50,'recipient':'test@example.com','sender':'notifications@example.com',
            'subject':'TEST-1 — alternative payment','state':'outgoing','error':''}}
        workflow.log_quote_email(conn,case,3,result)
        result['mail']['state']='sent'
        workflow.log_quote_email(conn,case,3,result)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM after_order_messages').fetchone()[0],1)
        self.assertEqual(conn.execute('SELECT status FROM after_order_messages').fetchone()[0],'sent')
        self.assertEqual(conn.execute('SELECT status FROM after_order_email_attempts').fetchone()[0],'sent')
        conn.close()

    def test_native_quote_retry_is_disabled_in_test_mode(self):
        from app.services.email_log import retry_block_reason
        message={'provider':'odoo','status':'failed','attempt_count':1,'request_fingerprint':'x'}
        self.assertTrue(retry_block_reason(message,test_mode=True,test_recipient='test@example.com'))
        self.assertFalse(retry_block_reason(message,test_mode=False,test_recipient='test@example.com'))
        with self.assertRaises(HTTPException):
            Workflow({'after_order_email_test_mode':lambda:True}).retry_quote_email(message)

    def test_worker_never_opens_database_in_test_mode(self):
        workflow = Workflow({'after_order_email_test_mode':lambda:True})
        workflow.run_due()
        workflow.release(1)
        self.assertEqual(workflow.process(1,2)['status'],'test_mode')

    def test_schema_is_idempotent_and_enforces_line_and_mode_isolation(self):
        conn = sqlite3.connect(':memory:')
        conn.executescript('CREATE TABLE after_order_cases(id INTEGER PRIMARY KEY); CREATE TABLE order_lines(id INTEGER PRIMARY KEY);')
        conn.executescript(SCHEMA); conn.executescript(SCHEMA)
        sql = '''INSERT INTO after_order_line_selections (case_id,line_id,test_mode,product_json,first_selected_at,deadline_at,issue_fingerprint,updated_at) VALUES(?,?,?,?,?,?,?,?)'''
        for line_id,mode in ((10,0),(11,0),(10,1)):
            conn.execute(sql,(1,line_id,mode,'{}','a','b','x','a'))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(sql,(1,10,0,'{}','a','b','x','a'))
        conn.close()

    def test_ready_requires_every_affected_line_and_current_issue(self):
        workflow = Workflow({'request_fingerprint':lambda case:'current'})
        case = {'id':1,'affected_items':[{'line_id':10},{'line_id':11}]}
        offer = {'line_id':10,'issue_fingerprint':'current','recommendations':[{'name':'A'}]}
        workflow.rows = lambda _: [offer]
        self.assertFalse(workflow.ready(case))
        workflow.rows = lambda _: [offer,{**offer,'line_id':11}]
        self.assertTrue(workflow.ready(case))
        workflow.rows = lambda _: [{**offer,'issue_fingerprint':'old'},{**offer,'line_id':11}]
        self.assertFalse(workflow.ready(case))

    def test_odoo_bridge_has_privilege_test_deadline_and_payment_guards(self):
        source = (Path(__file__).parents[1]/'odoo-addons/after_order_portal/models/alternatives.py').read_text()
        ast.parse(source)
        for fragment in ('base.group_system','live_alternatives_enabled','deadline > datetime.now',
                         "tx.state == 'done'",'unique(after_order_operation_key)','force_send=False',
                         'compare_amounts(quote.amount_total',"payment_state != 'paid'"):
            self.assertIn(fragment,source)

    def test_portal_submits_line_and_has_no_get_mutation(self):
        root = Path(__file__).parents[1]/'odoo-addons/after_order_portal'
        import xml.etree.ElementTree as ET
        tree = ET.parse(root/'views/order_preview.xml')
        self.assertTrue(tree.findall(".//input[@name='line_id']"))
        self.assertTrue(all(form.get('method') == 'post' for form in tree.findall('.//form')))
        for text in ('Best alternatives','More alternatives to choose from','Processed','Selection closed'):
            self.assertIn(text,(root/'views/order_preview.xml').read_text())
        for file in root.rglob('*.xml'):
            ET.parse(file)
        manifest=ast.literal_eval((root/'__manifest__.py').read_text())
        self.assertTrue(all((root/file).exists() for file in manifest['data']))


class StoredSelections(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''CREATE TABLE after_order_cases(id INTEGER PRIMARY KEY,confirmed_at TEXT,
            current_decision TEXT,decision_version INTEGER DEFAULT 0,decision_updated_at TEXT,status TEXT,decision_fingerprint TEXT,updated_at TEXT);
            CREATE TABLE order_lines(id INTEGER PRIMARY KEY);
            CREATE TABLE after_order_action_links(id INTEGER PRIMARY KEY,invalidated_at TEXT);
            INSERT INTO after_order_cases(id) VALUES(1);
            INSERT INTO after_order_action_links(id) VALUES(1);''')
        self.conn.executescript(SCHEMA)
        self.case = {'id':1,'case_type':'item_unavailable','store_id':1,'website_id':3,'odoo_order_id':42,
                     'odoo_order_name':'TEST-42','affected_items':[{'line_id':10},{'line_id':11}]}
        for line_id in (10,11):
            self.conn.execute('INSERT INTO after_order_line_offers VALUES(?,?,?,?,?)',(1,line_id,'[]','current','2026-09-06'))
        class Connection:
            def __init__(inner,conn): inner.conn=conn
            def execute(inner,sql,args=()): return inner.conn.execute(sql.replace(' FOR UPDATE',''),args)
        @contextmanager
        def db(): yield Connection(self.conn)
        self.events=[]
        self.namespace={'db':db,'after_order_email_test_mode':lambda:False,
            'request_fingerprint':lambda case:'current','utc_now':lambda:'2026-09-06T10:00:00+00:00',
            'record_after_order_event':lambda *args,**kw:self.events.append((args,kw))}
        self.workflow=Workflow(self.namespace)
        self.workflow.case_line=lambda case_id,line_id:(self.case,{'id':line_id,'odoo_line_id':line_id})
        self.workflow.ready=lambda case:True
        self.workflow.product=lambda case,line,template_id=0,reference='':{
            'product_id':template_id,'product_tmpl_id':template_id,'name':f'Product {template_id}',
            'default_code':f'SKU-{template_id}','difference':5,'currency':'USD'}

    def tearDown(self): self.conn.close()

    def choose(self,line_id,product_id,now,test=False):
        with patch('app.services.alternative_workflow.datetime') as clock:
            clock.now.return_value=now
            return self.workflow.choose(self.case,{'id':1,'test_mode':test},line_id,product_id)

    def test_two_lines_and_reselection_persist_independently(self):
        now=datetime(2026,9,6,10,tzinfo=timezone.utc)
        self.choose(10,100,now)
        self.choose(11,200,now+timedelta(hours=2))
        self.choose(10,300,now+timedelta(hours=20))
        rows=self.conn.execute('SELECT * FROM after_order_line_selections ORDER BY line_id').fetchall()
        self.assertEqual([json.loads(r['product_json'])['product_id'] for r in rows],[300,200])
        self.assertEqual(rows[0]['version'],2)
        self.assertEqual(rows[0]['deadline_at'],(now+timedelta(hours=24)).isoformat())
        self.assertEqual(rows[1]['deadline_at'],(now+timedelta(hours=26)).isoformat())
        self.assertEqual(len(self.events),3)

    def test_test_clicks_are_recorded_without_modifying_live_choice(self):
        now=datetime.now(timezone.utc)
        self.choose(10,100,now)
        self.choose(10,200,now,test=True)
        rows=self.conn.execute('SELECT * FROM after_order_line_selections ORDER BY test_mode').fetchall()
        self.assertEqual([json.loads(r['product_json'])['product_id'] for r in rows],[100,200])
        self.assertEqual(self.conn.execute('SELECT decision_version FROM after_order_cases').fetchone()[0],1)

    def test_deadline_rejection_does_not_replace_saved_product(self):
        now=datetime.now(timezone.utc)
        self.choose(10,100,now)
        with self.assertRaises(HTTPException) as error:
            self.choose(10,200,now+timedelta(hours=24))
        self.assertEqual(error.exception.status_code,409)
        row=self.conn.execute('SELECT product_json FROM after_order_line_selections').fetchone()
        self.assertEqual(json.loads(row[0])['product_id'],100)

    def test_invalidated_link_rejected_under_case_lock(self):
        self.conn.execute("UPDATE after_order_action_links SET invalidated_at='expired'")
        with self.assertRaises(HTTPException) as error:
            self.choose(10,100,datetime.now(timezone.utc))
        self.assertEqual(error.exception.status_code,410)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM after_order_line_selections').fetchone()[0],0)

    def test_team_confirmation_rejected_under_lock(self):
        self.conn.execute("UPDATE after_order_cases SET confirmed_at='confirmed'")
        with self.assertRaises(HTTPException):
            self.choose(10,100,datetime.now(timezone.utc))


if __name__ == '__main__':
    unittest.main()
