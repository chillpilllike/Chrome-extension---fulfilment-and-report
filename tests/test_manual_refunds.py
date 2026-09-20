import html
import json
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import HTTPException
from app.services.manual_refunds import ManualRefunds, Completion, SCHEMA, amount
from app.services.alternative_workflow import SCHEMA as ALTERNATIVES
from app.services.care_requests import SCHEMA as REQUESTS
from app.services.after_order import request_fingerprint


class ManualRefundTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        CREATE TABLE after_order_cases(id INTEGER PRIMARY KEY,store_id INTEGER,odoo_order_id INTEGER,
          website_id INTEGER,case_type TEXT,current_decision TEXT,decision_version INTEGER,status TEXT,
          context_json TEXT,affected_items_json TEXT,confirmed_at TEXT,confirmed_by TEXT,updated_at TEXT);
        INSERT INTO after_order_cases VALUES(1,2,3,4,'tracking','refund',1,'needs_attention','{}','[]',NULL,NULL,'');
        CREATE TABLE order_lines(id INTEGER PRIMARY KEY,store_id INTEGER,odoo_order_id INTEGER,odoo_order_date TEXT);
        CREATE TABLE after_order_refund_requests(case_id INTEGER,amount REAL,currency TEXT,status TEXT,last_error TEXT,updated_at TEXT);
        CREATE TABLE after_order_action_links(case_id INTEGER,invalidated_at TEXT,updated_at TEXT);
        CREATE TABLE after_order_execution_jobs(case_id INTEGER,status TEXT,updated_at TEXT);
        CREATE TABLE after_order_messages(id INTEGER PRIMARY KEY,case_id INTEGER,provider TEXT,recipient TEXT,sender TEXT,
          subject TEXT,html_preview TEXT,status TEXT,idempotency_key TEXT UNIQUE,payload_json TEXT,created_at TEXT,
          updated_at TEXT,test_mode INTEGER,request_fingerprint TEXT,template_kind TEXT,related_items_json TEXT,attempt_count INTEGER);
        CREATE TABLE after_order_sms(status TEXT,last_error TEXT,updated_at TEXT);
        CREATE TABLE airwallex_refund_emails(state TEXT,last_error TEXT);
        CREATE TABLE app_settings(key TEXT PRIMARY KEY,value TEXT);
        """)
        self.conn.executescript(SCHEMA + ALTERNATIVES + REQUESTS)
        self.settings = {
            "after_order_refund_mode": "manual",
            "after_order_email_test_mode": "false",
        }
        self.client = Mock()
        self.client.read.return_value = [
            {"currency_id": [1, "USD"], "amount_total": 100, "website_id": [4, "Store"]}
        ]
        self.send = Mock(return_value={"status": "sent", "message": "Email sent."})
        self.events = []
        self.service = ManualRefunds(
            dict(
                db=self.db,
                get_service_settings=lambda: self.settings,
                set_service_settings=self.settings.update,
                after_order_email_test_mode=lambda: self.settings[
                    "after_order_email_test_mode"
                ]
                == "true",
                after_order_case_by_id=self.case,
                require_after_order_case_in_scope=Mock(),
                hydrate_after_order_recipient_and_domain=lambda case, **kw: case,
                OdooClient=lambda _: self.client,
                get_store=lambda _: None,
                many2one_id=lambda v: v[0],
                utc_now=lambda: datetime.now(timezone.utc).isoformat(),
                request_fingerprint=request_fingerprint,
                after_order_sender=lambda _: (
                    "Store <notifications@store.test>",
                    "store.test",
                ),
                record_after_order_event=lambda *a, **kw: self.events.append((a, kw)),
                enqueue_odoo_chatter_note=Mock(),
                html=html,
                clean_error_message=str,
                retry_after_order_email=self.send,
                api_after_order_settings=lambda: self.settings,
            )
        )

    @contextmanager
    def db(self):
        class Adapter:
            def execute(_, sql, params=()):
                if "pg_" in sql:
                    return self.conn.execute("SELECT 1 AS locked")
                return self.conn.execute(sql.replace(" FOR UPDATE", ""), params)

        with self.conn:
            yield Adapter()

    def case(self, _=1):
        row = dict(
            self.conn.execute("SELECT * FROM after_order_cases WHERE id=1").fetchone()
        )
        return {
            **row,
            "customer_email": "customer@store.test",
            "odoo_order_name": "NC-TEST",
            "sender_domain": "store.test",
            "context": {"website_name": "Store"},
            "affected_items": [],
        }

    def body(self, **kw):
        return Completion(
            **dict(
                {
                    "scope": "order",
                    "decision_version": 1,
                    "amount": "10.00",
                    "currency": "USD",
                    "reference": "bank-123",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "recorded_by": "Team",
                    "notes": "",
                    "refunded_outside_app": True,
                    "other_refunds_checked": True,
                },
                **kw
            )
        )

    def test_record_send_once_never_move_money(self):
        first = self.service.complete(1, self.body(), None)
        second = self.service.complete(1, self.body(), None)
        self.assertTrue(first["recorded"])
        self.assertTrue(second["recorded"])
        self.send.assert_called_once()
        self.client.execute.assert_not_called()
        self.assertEqual(self.case()["status"], "resolved")
        msg = dict(self.conn.execute("SELECT * FROM after_order_messages").fetchone())
        self.service.validate_message(msg)
        self.assertIn("NC-TEST", msg["subject"])
        self.assertIn("10.00 USD", msg["html_preview"])
        self.assertIn("24–48", msg["html_preview"])

    def test_test_mode_and_missing_attestation_block_everything(self):
        for changes in (
            {"refunded_outside_app": False},
            {"other_refunds_checked": False},
        ):
            with self.assertRaises(HTTPException):
                self.service.complete(1, self.body(**changes), None)
        self.settings["after_order_email_test_mode"] = "true"
        with self.assertRaises(HTTPException):
            self.service.complete(1, self.body(), None)
        self.client.read.assert_not_called()
        self.send.assert_not_called()

    def test_invalid_amounts(self):
        for x in ["NaN", "Infinity", "-1", "0", "1.001", "bad"]:
            with self.assertRaises(ValueError):
                amount(x)
        self.assertEqual(amount("1.20"), Decimal("1.20"))

    def test_changed_decision_currency_limit_and_future_block_send(self):
        for changes in (
            {"decision_version": 2},
            {"currency": "AUD"},
            {"amount": "100.01"},
            {
                "completed_at": (
                    datetime.now(timezone.utc) + timedelta(days=1)
                ).isoformat()
            },
        ):
            with self.assertRaises(HTTPException):
                self.service.complete(1, self.body(**changes), None)
        self.send.assert_not_called()
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM after_order_manual_refunds"
            ).fetchone()[0],
            0,
        )

    def test_email_failure_preserves_record_and_does_not_repeat_refund(self):
        self.send.side_effect = RuntimeError("unavailable")
        result = self.service.complete(1, self.body(), None)
        self.assertTrue(result["recorded"])
        self.assertEqual(result["email_status"], "needs_attention")
        self.service.complete(1, self.body(), None)
        self.send.assert_called_once()
        self.client.execute.assert_not_called()

    def test_fabricated_or_changed_completion_email_is_rejected(self):
        with self.assertRaises(ValueError):
            self.service.validate_message({"case_id": 1, "payload_json": "{}"})
        self.service.complete(1, self.body(), None)
        msg = dict(self.conn.execute("SELECT * FROM after_order_messages").fetchone())
        data = json.loads(msg["payload_json"])
        data["_care_manual_refund_amount"] = "999"
        with self.assertRaises(ValueError):
            self.service.validate_message({**msg, "payload_json": json.dumps(data)})

    def test_partial_order_stays_in_attention_for_fulfilment_review(self):
        self.conn.execute(
            "UPDATE after_order_cases SET current_decision='exclude_item_and_proceed'"
        )
        self.service.complete(1, self.body(), None)
        self.assertEqual(self.case()["status"], "needs_attention")

    def test_replacement_refund_only_current_selection_after_window(self):
        self.conn.execute(
            "UPDATE after_order_cases SET current_decision='offer_alternatives'"
        )
        fp = request_fingerprint(self.case())
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.conn.execute(
            """INSERT INTO after_order_line_selections
          (case_id,line_id,test_mode,version,product_json,first_selected_at,deadline_at,status,issue_fingerprint,updated_at)
          VALUES(1,5,0,1,?,?,?,'needs_review',?,?)""",
            (json.dumps({"difference": -10, "currency": "USD"}), past, past, fp, past),
        )
        with self.db() as c:
            self.assertEqual(len(self.service.scopes(c, self.case())), 1)
        self.service.complete(1, self.body(scope="replacement:5:1"), None)
        row = self.conn.execute("SELECT * FROM after_order_line_selections").fetchone()
        self.assertEqual(row["refund_status"], "completed")
        self.assertEqual(row["status"], "needs_review")
        self.client.execute.assert_not_called()

    def test_repeat_launch_accepts_postgres_decoded_or_json_setting(self):
        endpoint = self.service.launch_router().routes[0].endpoint
        for value in [
            "2026-09-20T15:00:00+00:00",
            json.dumps("2026-09-20T15:00:00+00:00"),
        ]:
            self.conn.execute(
                "INSERT OR REPLACE INTO app_settings VALUES(?,?)",
                ("after_order_manual_live_started_at", value),
            )
            self.assertTrue(endpoint({})["already_started"])
        self.send.assert_not_called()

    def test_reset_cancels_queue_preserves_sent_and_uncertainty(self):
        from zoneinfo import ZoneInfo

        self.settings["after_order_email_test_mode"] = "true"
        for state in [
            "awaiting_approval",
            "failed",
            "delivery_unknown",
            "sent",
            "delivered",
        ]:
            self.conn.execute(
                "INSERT INTO after_order_messages(case_id,status,payload_json) VALUES(1,?,'{}')",
                (state,),
            )
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
        result = self.service.start_live(
            {"cutoff_date": today, "confirm_cancel_queued": True, "confirm_live": True}
        )
        self.assertEqual(result["cancelled_emails"], 2)
        self.assertEqual(result["uncertain_emails_held"], 1)
        self.assertEqual(
            [
                r[0]
                for r in self.conn.execute(
                    "SELECT status FROM after_order_messages ORDER BY id"
                )
            ],
            ["cancelled", "cancelled", "delivery_unknown", "sent", "delivered"],
        )
        self.assertEqual(self.settings["after_order_email_test_mode"], "false")
        self.assertEqual(self.settings["after_order_manual_live_cutoff"], today)
        self.assertTrue(
            self.service.start_live(
                {
                    "cutoff_date": today,
                    "confirm_cancel_queued": True,
                    "confirm_live": True,
                }
            )["already_started"]
        )
        self.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
