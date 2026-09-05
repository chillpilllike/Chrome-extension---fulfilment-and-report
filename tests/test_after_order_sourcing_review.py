import ast
import hashlib
import json
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.services.after_order import unavailable_notice_block_reason


class SourcingApprovalTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
          CREATE TABLE order_lines (id INTEGER, store_id INTEGER, odoo_order_id INTEGER,
            asin TEXT, missing_asin TEXT, quantity REAL, state TEXT, order_engine TEXT,
            amazon_status TEXT, amazon_order_id TEXT, amazon_order_url TEXT, odoo_status_label TEXT);
          CREATE TABLE after_order_case_events (id INTEGER PRIMARY KEY, case_id INTEGER, event_type TEXT,
            details_json TEXT, created_at TEXT);
          INSERT INTO order_lines VALUES (1,1,10,'B000000001','B000000001',1,'missing','chrome','missing',NULL,NULL,'invoiced');
        ''')
        @contextmanager
        def db():
            yield self.conn
        tree = ast.parse((Path(__file__).parents[1] / 'app/main.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'after_order_unavailable_review')
        scope = dict(Any=Any, db=db, rows_to_dicts=lambda rows: [dict(r) for r in rows],
                     unavailable_notice_block_reason=unavailable_notice_block_reason,
                     hashlib=hashlib, json=json, datetime=datetime, timedelta=timedelta, timezone=timezone,
                     parse_any_datetime=datetime.fromisoformat)
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'review', 'exec'), scope)
        self.review = scope['after_order_unavailable_review']
        self.case = {'id': 1, 'store_id': 1, 'odoo_order_id': 10, 'affected_items': [{'line_id': 1}]}

    def tearDown(self):
        self.conn.close()

    def approve(self, hours_ago=0):
        result = self.review(self.case)
        self.conn.execute('INSERT INTO after_order_case_events (case_id,event_type,details_json,created_at) VALUES (?,?,?,?)',
                          (1, 'unavailable_sourcing_review_approved', json.dumps({'signature': result['signature']}),
                           (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()))

    def test_missing_status_alone_does_not_approve_email(self):
        result = self.review(self.case)
        self.assertFalse(result['blocked'])
        self.assertFalse(result['approved'])

    def test_matching_team_approval_permits_notice(self):
        self.approve()
        self.assertTrue(self.review(self.case)['approved'])

    def test_send_approval_expires_but_customer_can_still_reply(self):
        self.approve(hours_ago=25)
        self.assertFalse(self.review(self.case, for_send=True)['approved'])
        self.assertTrue(self.review(self.case)['approved'])

    def test_fulfilment_change_invalidates_approval(self):
        self.approve()
        self.conn.execute("UPDATE order_lines SET quantity=2")
        self.assertFalse(self.review(self.case)['approved'])

    def test_third_party_placement_overrides_existing_approval(self):
        self.approve()
        self.conn.execute("UPDATE order_lines SET order_engine='third_party', amazon_order_id='placed'")
        result = self.review(self.case)
        self.assertTrue(result['blocked'])
        self.assertFalse(result['approved'])

    def test_resolved_case_cannot_use_approval(self):
        self.approve()
        self.case['status'] = 'resolved'
        self.assertTrue(self.review(self.case)['blocked'])

    def test_live_email_is_stopped_before_provider_without_review(self):
        tree = ast.parse((Path(__file__).parents[1] / 'app/main.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'send_after_order_email')
        class Blocked(Exception):
            pass
        @contextmanager
        def db():
            yield self.conn
        events = []
        scope = dict(Any=Any, Request=object, HTTPException=Blocked,
                     after_order_case_by_id=lambda case_id: {**self.case, 'case_type': 'item_unavailable'},
                     require_after_order_case_in_scope=lambda case: None,
                     after_order_tracking_is_current=lambda case: True,
                     after_order_unavailable_review=self.review,
                     after_order_email_test_mode=lambda: False,
                     db=db, record_after_order_event=lambda *args, **kwargs: events.append(args[2]))
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'send', 'exec'), scope)
        with self.assertRaises(Blocked):
            scope['send_after_order_email'](1, object())
        self.assertEqual(['unavailable_email_blocked'], events)
