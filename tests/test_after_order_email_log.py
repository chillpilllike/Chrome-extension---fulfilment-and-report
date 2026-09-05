"""Offline email log/retry tests: providers are fake and the DB is in-memory."""
import ast
import json
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from unittest.mock import Mock, patch
import requests

from app.services.after_order import EmailRejected, ResendEmailProvider, request_fingerprint
from app.services.email_log import retry_block_reason, STATUS_LABELS


class RetryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.row = {"status": "failed", "attempt_count": 1, "request_fingerprint": "snapshot", "recipient": "test@example.test", "test_mode": 1, "payload_json": json.dumps({"to": ["test@example.test"], "html": "<p>Test</p>"})}

    def reason(self, row=None, test_mode=True):
        return retry_block_reason(row or self.row, test_mode=test_mode, test_recipient="test@example.test")

    def test_confirmed_failure_can_retry(self):
        self.assertEqual("", self.reason())

    def test_sent_unknown_pending_and_previews_cannot_retry(self):
        for status in ("sent", "sent_test", "delivery_unknown", "sending", "retrying", "test_preview"):
            self.assertTrue(self.reason({**self.row, "status": status}))

    def test_legacy_payload_mismatch_limit_and_live_retry_in_test_mode_blocked(self):
        for change in ({"request_fingerprint": None}, {"payload_json": "{}"}, {"payload_json": "bad"}, {"attempt_count": 5}, {"recipient": "someone@example.test"}, {"test_mode": 0}):
            self.assertTrue(self.reason({**self.row, **change}))

    def test_test_message_stays_test_even_when_global_mode_is_live(self):
        self.assertEqual("", self.reason(test_mode=False))
        self.assertTrue(self.reason({**self.row, "recipient": "other@example.test"}, test_mode=False))

    def test_provider_distinguishes_rejections_from_uncertain_responses(self):
        provider = ResendEmailProvider("fake")
        for code, exception in ((422, EmailRejected), (429, EmailRejected), (500, RuntimeError), (409, RuntimeError)):
            with patch("app.services.after_order.requests.post", return_value=Mock(status_code=code, json=lambda: {"message": "Test failure"})):
                with self.assertRaises(exception):
                    provider.send({}, idempotency_key="test")
        with patch("app.services.after_order.requests.post", return_value=Mock(status_code=200, json=lambda: {})):
            with self.assertRaises(RuntimeError):
                provider.send({}, idempotency_key="test")


class LogHandlersTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        CREATE TABLE stores (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE after_order_cases (id INTEGER PRIMARY KEY,store_id INTEGER,website_id INTEGER,odoo_order_name TEXT);
        CREATE TABLE after_order_messages (id INTEGER PRIMARY KEY,case_id INTEGER,provider TEXT,recipient TEXT,sender TEXT,subject TEXT,status TEXT,last_error TEXT,provider_message_id TEXT,test_mode INTEGER,attempt_count INTEGER,created_at TEXT,updated_at TEXT,template_kind TEXT,payload_json TEXT,request_fingerprint TEXT,idempotency_key TEXT,html_preview TEXT);
        CREATE TABLE after_order_email_attempts (id INTEGER PRIMARY KEY,message_id INTEGER,attempt_number INTEGER,status TEXT,error TEXT,provider_message_id TEXT,created_at TEXT,updated_at TEXT,UNIQUE(message_id,attempt_number));
        INSERT INTO stores VALUES (1,'Demo Australia'),(2,'Demo Canada');
        INSERT INTO after_order_cases VALUES (1,1,4,'DEMO-100'),(2,2,5,'DEMO-200');
        """)
        now = datetime.now(timezone.utc).isoformat()
        for identifier, case_id in ((1,1),(2,2)):
            self.conn.execute("""INSERT INTO after_order_messages (id,case_id,provider,recipient,sender,subject,status,test_mode,attempt_count,created_at,updated_at,template_kind,payload_json,request_fingerprint,idempotency_key,html_preview)
                VALUES (?,?,'resend','test@example.test','notifications@example.test','Test email','failed',1,1,?,?,'item_unavailable',?,'snapshot','secret-link-key','<p>Preview</p>')""", (identifier,case_id,now,now,json.dumps({"to":["test@example.test"],"html":"<p>Saved email</p>"})))
        @contextmanager
        def db():
            class Adapter:
                def execute(_, sql, params=()):
                    return self.conn.execute(sql.replace(" FOR UPDATE", ""), params)
            with self.conn:
                yield Adapter()
        class HTTPError(Exception):
            def __init__(self, code, detail):
                self.status_code, self.detail = code, detail
        self.HTTPError = HTTPError
        self.provider = Mock(send=Mock(return_value={"id":"provider-demo"}))
        self.scope = {"Any":Any,"Optional":Optional,"Request":object,"db":db,"json":json,"requests":requests,
            "datetime":datetime,"timedelta":timedelta,"timezone":timezone,"HTTPException":HTTPError,
            "row_to_dict":lambda row: dict(row) if row else None,"rows_to_dicts":lambda rows:[dict(row) for row in rows],
            "after_order_email_test_mode":lambda:True,"after_order_test_recipient":lambda:"test@example.test",
            "retry_block_reason":retry_block_reason,"EMAIL_STATUS_LABELS":STATUS_LABELS,
            "clean_text":lambda value:str(value or ""),"clean_error_message":str,
            "pagination_bounds":lambda page,per_page:(max(1,page),per_page,(max(1,page)-1)*per_page),
            "after_order_case_by_id":lambda case_id:{"id":case_id,"odoo_order_date":"2026-09-01"},
            "require_after_order_case_in_scope":lambda case:None,"parse_any_datetime":datetime.fromisoformat,
            "utc_now":lambda:datetime.now(timezone.utc).isoformat(),"record_after_order_event":lambda *a,**kw:None,
            "create_email_provider":lambda *a:self.provider,"EmailRejected":EmailRejected,"os":Mock(getenv=lambda *a: "fake")}
        tree = ast.parse((Path(__file__).parents[1]/"app/main.py").read_text())
        for name in ("email_log_row","api_after_order_email_log","api_after_order_email_detail","api_after_order_email_retry"):
            node = next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name==name)
            node.decorator_list=[]
            exec(compile(ast.Module(body=[node],type_ignores=[]),"email-log","exec"),self.scope)

    def tearDown(self):
        self.conn.close()

    def test_list_filters_and_detail_enforce_store_scope_without_payload_leak(self):
        result=self.scope["api_after_order_email_log"](store_id=1,status="failed")
        self.assertEqual(1,result["total"])
        self.assertEqual(1,result["summary"]["failed"])
        self.assertNotIn("payload_json",result["rows"][0])
        self.assertNotIn("idempotency_key",result["rows"][0])
        self.assertEqual(0,self.scope["api_after_order_email_log"](store_id=1,q="DEMO-200")["total"])
        with self.assertRaises(self.HTTPError):
            self.scope["api_after_order_email_detail"](2,store_id=1)

    def test_invalid_dates_rejected(self):
        with self.assertRaises(self.HTTPError):
            self.scope["api_after_order_email_log"](date_from="not-a-date")

    def test_retry_uses_saved_payload_and_records_attempt_without_duplicate_send(self):
        response=self.scope["api_after_order_email_retry"](1,object())
        self.assertTrue(response["ok"])
        self.assertEqual("sent_test",response["status"])
        self.assertEqual(["test@example.test"],self.provider.send.call_args.args[0]["to"])
        self.assertEqual(2,self.conn.execute("SELECT attempt_count FROM after_order_messages WHERE id=1").fetchone()[0])
        self.assertEqual("sent_test",self.conn.execute("SELECT status FROM after_order_email_attempts").fetchone()[0])
        with self.assertRaises(self.HTTPError):
            self.scope["api_after_order_email_retry"](1,object())
        self.provider.send.assert_called_once()

    def test_timeout_marks_uncertain_and_disables_retry(self):
        self.provider.send.side_effect=requests.Timeout("Test timeout")
        self.assertEqual("delivery_unknown",self.scope["api_after_order_email_retry"](1,object())["status"])
        with self.assertRaises(self.HTTPError):
            self.scope["api_after_order_email_retry"](1,object())
        self.provider.send.assert_called_once()

    def test_rejection_remains_failed_and_preserves_error(self):
        self.provider.send.side_effect=EmailRejected("Invalid sender")
        self.assertEqual("failed",self.scope["api_after_order_email_retry"](1,object())["status"])
        self.assertEqual("Invalid sender",self.conn.execute("SELECT error FROM after_order_email_attempts").fetchone()[0])

    def test_live_email_in_test_mode_never_reaches_provider(self):
        self.conn.execute("UPDATE after_order_messages SET test_mode=0 WHERE id=1")
        with self.assertRaises(self.HTTPError):
            self.scope["api_after_order_email_retry"](1,object())
        self.provider.send.assert_not_called()
