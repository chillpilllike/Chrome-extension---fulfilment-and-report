"""Offline regression tests: never import main or connect to production."""
import ast
import copy
import json
import uuid
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

from app.services.after_order import request_fingerprint, merge_tracking_events, tracking_risk, unavailable_notice_block_reason

SOURCE = Path(__file__).parents[1] / "app/main.py"


def extract(name, scope):
    node = next(node for node in ast.parse(SOURCE.read_text()).body if isinstance(node, ast.FunctionDef) and node.name == name)
    node.decorator_list = []
    namespace = {"Any": Any, "Request": object, "AfterOrderConfirmPayload": object, **scope}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace[name]


class TrackingRegressionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 5, tzinfo=timezone.utc)

    def test_old_failure_does_not_override_delivered(self):
        events = [{"Event": "Delivery failed", "EventDT": "2026-09-01"}, {"Event": "Delivered", "EventDT": "2026-09-04"}]
        self.assertEqual("delivered", tracking_risk(events, status="Delivered", now=self.now).state)
        self.assertEqual("delivered", tracking_risk(list(reversed(events)), now=self.now).state)

    def test_pending_unknown_and_future_scans_do_not_prove_possession(self):
        for event in ("Pending carrier acceptance", "Awaiting parcel received", "Unknown provider code", "Expected arrival"):
            risk = tracking_risk([{"Event": event, "EventDT": "2026-08-01"}], now=self.now)
            self.assertEqual("awaiting_first_scan", risk.state)
            self.assertFalse(risk.customer_lost_email_allowed)
        risk = tracking_risk([{"Event": "Parcel received", "EventDT": "2027-01-01"}], now=self.now)
        self.assertFalse(risk.customer_lost_email_allowed)

    def test_customs_return_and_failed_attempt_are_not_lost(self):
        for status in ("Customs hold", "Returned to sender", "Delivery attempted", "Not delivered"):
            risk = tracking_risk([], status=status, now=self.now)
            self.assertEqual("carrier_exception", risk.state)
            self.assertFalse(risk.customer_lost_email_allowed)

    def test_long_history_is_valid_complete_and_deduplicated(self):
        events = [{"Event": "Parcel received " + "x" * 120, "EventDT": f"2026-08-{day:02}"} for day in range(1, 30)]
        merged = merge_tracking_events(events, events[-2:] + [{"Event": "Delivered", "EventDT": "2026-09-04"}])
        encoded = json.dumps(merged)
        self.assertGreater(len(encoded), 4000)
        self.assertEqual(30, len(json.loads(encoded)))


class ConsentSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.case = {"store_id": 1, "website_id": 4, "odoo_order_id": 10, "case_type": "tracking", "tracking_code": "EPG1", "context": {"risk_state": "suspected_lost"}, "affected_items": [{"line_id": 3, "asin": "B000000001", "quantity": 2, "original_line_total": 20, "currency": "USD"}]}

    def test_financial_item_site_and_risk_changes_invalidate_consent(self):
        original = request_fingerprint(self.case)
        for key, value in (("quantity", 3), ("original_line_total", 21), ("currency", "CAD"), ("line_id", 4), ("asin", "B000000002")):
            changed = copy.deepcopy(self.case)
            changed["affected_items"][0][key] = value
            self.assertNotEqual(original, request_fingerprint(changed))
        for key, value in (("store_id", 2), ("website_id", 5), ("odoo_order_id", 11), ("tracking_code", "EPG2")):
            self.assertNotEqual(original, request_fingerprint({**self.case, key: value}))
        changed = copy.deepcopy(self.case)
        changed["context"]["risk_state"] = "delivered"
        self.assertNotEqual(original, request_fingerprint(changed))

    def test_logo_refresh_and_customer_choice_do_not_expire_request(self):
        changed = copy.deepcopy(self.case)
        changed["context"]["website_logo_url"] = "https://site/logo?v=2"
        changed["current_decision"] = "refund"
        self.assertEqual(request_fingerprint(self.case), request_fingerprint(changed))


class DryRunTests(unittest.TestCase):
    def test_live_mode_cannot_be_enabled_without_readiness_approval(self):
        class Blocked(Exception):
            pass
        danger = Mock(side_effect=AssertionError("Changed live setting"))
        function = extract("api_after_order_test_mode", {"AfterOrderTestModePayload": object, "clean_text": lambda v: str(v or ""), "get_service_settings": lambda: {}, "HTTPException": Blocked, "set_service_settings": danger})
        with self.assertRaises(Blocked):
            function(SimpleNamespace(enabled=False))
        danger.assert_not_called()

    def test_confirmation_does_not_touch_database_odoo_or_mail(self):
        danger = Mock(side_effect=AssertionError("Side effect in test mode"))
        confirm = extract("api_after_order_confirm", {"after_order_email_test_mode": lambda: True, "after_order_case_by_id": lambda _: {"id": 1}, "db": danger, "OdooClient": danger, "send_after_order_email": danger})
        self.assertTrue(confirm(1, object(), object())["test_mode"])
        danger.assert_not_called()

    def test_execution_and_refund_queue_do_not_touch_database_in_test_mode(self):
        danger = Mock(side_effect=AssertionError("Side effect in test mode"))
        for name, argument in (("execute_after_order_job", 1), ("queue_after_order_partial_refund", {"id": 1})):
            function = extract(name, {"after_order_email_test_mode": lambda: True, "db": danger})
            result = function(argument, object()) if name == "execute_after_order_job" else function(argument)
            self.assertTrue(result.get("test_mode") or result.get("status") == "test_preview")
        danger.assert_not_called()

    def test_scheduler_refreshes_but_does_not_send_in_test_mode(self):
        refreshed = Mock()
        danger = Mock(side_effect=AssertionError("Side effect in test mode"))
        function = extract("run_after_order_automation", {"sync_after_order_cases": refreshed, "after_order_email_test_mode": lambda: True, "db": danger, "send_after_order_email": danger, "execute_after_order_job": danger})
        self.assertTrue(function()["test_mode"])
        refreshed.assert_called_once()
        danger.assert_not_called()


class ConfirmationTransactionTests(unittest.TestCase):
    def test_confirmation_and_job_are_rolled_back_together(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript("""
          CREATE TABLE after_order_cases (id INTEGER PRIMARY KEY, store_id INTEGER, website_id INTEGER, odoo_order_id INTEGER,
          case_type TEXT, affected_items_json TEXT, context_json TEXT, current_decision TEXT, decision_version INTEGER,
          decision_fingerprint TEXT, status TEXT, decision_locked_at TEXT, confirmed_at TEXT, confirmed_by TEXT, updated_at TEXT);
          CREATE TABLE after_order_action_links (case_id INTEGER, invalidated_at TEXT, updated_at TEXT);
          INSERT INTO after_order_cases (id,store_id,website_id,odoo_order_id,case_type,affected_items_json,context_json,current_decision,decision_version,status)
          VALUES (1,1,4,10,'expected_dispatch','[]','{}','proceed',1,'needs_confirmation');
        """)
        case = dict(connection.execute("SELECT * FROM after_order_cases").fetchone())
        case.update(affected_items=[], context={})
        case["decision_fingerprint"] = request_fingerprint(case)
        connection.execute("UPDATE after_order_cases SET decision_fingerprint=?", (case["decision_fingerprint"],))
        connection.commit()

        class Adapter:
            def execute(self, sql, params=()):
                return connection.execute(sql.replace(" FOR UPDATE", ""), params)

        @contextmanager
        def db():
            with connection:
                yield Adapter()

        confirm = extract("api_after_order_confirm", {
            "after_order_email_test_mode": lambda: False, "after_order_case_by_id": lambda _: case,
            "utc_now": lambda: "2026-09-05", "require_after_order_case_in_scope": lambda _: None,
            "request_fingerprint": request_fingerprint, "after_order_allowed_actions": lambda _: ["proceed"],
            "db": db, "json": json, "after_order_json_list": json.loads,
            "clean_text": lambda value: str(value or ""), "record_after_order_event": lambda *a, **kw: None,
        })
        # Missing execution table simulates a failed atomic queue write.
        with self.assertRaises(sqlite3.OperationalError):
            confirm(1, SimpleNamespace(decision_version=1, confirmed_by="Team"), object())
        self.assertIsNone(connection.execute("SELECT confirmed_at FROM after_order_cases").fetchone()[0])
        connection.close()


class CaseMaterializationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        tree = ast.parse(SOURCE.read_text())
        init = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "init_db")
        for node in ast.walk(init):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "executescript" and isinstance(node.args[0], ast.Constant):
                self.conn.executescript(node.args[0].value)
        for table, columns in {
            "order_lines": {"odoo_order_date": "TEXT", "tracking_payload": "TEXT", "order_engine": "TEXT", "odoo_status_label": "TEXT", "missing_asin": "TEXT", "store_total_native": "REAL", "store_currency": "TEXT"},
            "after_order_cases": {"decision_fingerprint": "TEXT"},
            "after_order_action_links": {"request_fingerprint": "TEXT"},
        }.items():
            existing = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, kind in columns.items():
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
        self.conn.execute("INSERT INTO stores (id,name,odoo_url,odoo_db,odoo_user,odoo_password,created_at,updated_at) VALUES (1,'Test','https://example.test','test','test','test','2026-09-05','2026-09-05')")
        self.conn.execute("""INSERT INTO order_lines (id,store_id,odoo_order_id,odoo_order_name,odoo_line_id,odoo_order_date,asin,quantity,state,order_engine,created_at,updated_at,store_total_native,store_currency)
            VALUES (1,1,10,'TEST10',100,'2026-09-01','B000000001',1,'missing','chrome','2026-09-05','2026-09-05',25,'USD')""")
        @contextmanager
        def db():
            yield self.conn
        self.sync = extract("sync_after_order_cases", {
            "Optional": __import__('typing').Optional, "db": db, "utc_now": lambda: "2026-09-05",
            "after_order_cutoff_date": lambda: "2026-08-01", "rows_to_dicts": lambda rows: [dict(row) for row in rows],
            "unavailable_notice_block_reason": unavailable_notice_block_reason, "clean_text": lambda v: str(v or ""),
            "normalize_asin": lambda v: str(v or ""), "after_order_json_list": lambda v: json.loads(v or "[]"),
            "json": json, "uuid": uuid, "datetime": datetime, "timezone": timezone, "timedelta": timedelta,
            "request_fingerprint": request_fingerprint, "tracking_risk": tracking_risk,
            "quote_plus": lambda s: s, "parse_tracking_packages": lambda v: json.loads(v or "[]"),
            "record_after_order_event": lambda *a, **kw: None, "get_service_settings": lambda: {},
        })

    def tearDown(self):
        self.conn.close()

    def test_missing_snapshot_is_preserved_and_fulfilment_resolves_case(self):
        self.sync()
        self.conn.execute("UPDATE order_lines SET store_total_native=0")
        self.sync()
        row = self.conn.execute("SELECT * FROM after_order_cases").fetchone()
        self.assertEqual(25, json.loads(row["affected_items_json"])[0]["original_line_total"])
        self.conn.execute("UPDATE order_lines SET order_engine='third_party', amazon_order_id='supplier-placed'")
        self.sync()
        self.assertEqual("resolved", self.conn.execute("SELECT status FROM after_order_cases").fetchone()[0])

    def test_quantity_change_clears_old_decision_and_invalidates_link(self):
        self.sync()
        row = dict(self.conn.execute("SELECT * FROM after_order_cases").fetchone())
        row.update(affected_items=json.loads(row["affected_items_json"]), context=json.loads(row["context_json"]))
        signature = request_fingerprint(row)
        self.conn.execute("UPDATE after_order_cases SET current_decision='cancel_order',decision_fingerprint=?", (signature,))
        self.conn.execute("INSERT INTO after_order_action_links (case_id,token_hash,created_at,updated_at,request_fingerprint) VALUES (?,'test','2026-09-05','2026-09-05',?)", (row["id"],signature))
        self.conn.execute("UPDATE order_lines SET quantity=2")
        self.sync()
        self.assertIsNone(self.conn.execute("SELECT current_decision FROM after_order_cases").fetchone()[0])
        self.assertIsNotNone(self.conn.execute("SELECT invalidated_at FROM after_order_action_links").fetchone()[0])

    def test_new_missing_issue_after_confirmation_has_a_new_case(self):
        self.sync()
        self.conn.execute("UPDATE after_order_cases SET confirmed_at='2026-09-05'")
        self.conn.execute("UPDATE order_lines SET quantity=2")
        self.sync()
        self.sync()
        rows = self.conn.execute("SELECT confirmed_at FROM after_order_cases ORDER BY id").fetchall()
        self.assertEqual(2, len(rows))
        self.assertIsNotNone(rows[0][0])
        self.assertIsNone(rows[1][0])

    def test_tracking_update_preserves_history_and_rejects_ambiguous_store(self):
        @contextmanager
        def db():
            yield self.conn
        function = extract("api_epost_update", {
            "EpostTrackingUpdatePayload": object, "db": db, "rows_to_dicts": lambda rows: [dict(row) for row in rows],
            "clean_text": lambda v: str(v or ""), "merge_tracking_events": merge_tracking_events,
            "after_order_json_list": lambda v: json.loads(v or "[]"), "json": json,
            "utc_now": lambda: "2026-09-05", "epost_status_from_update": lambda *a: "checked",
            "parse_any_datetime": lambda value: datetime.fromisoformat(value) if value else None,
        })
        self.conn.execute("INSERT INTO epost_global_tracking (store_id,tracking_code,events_json,created_at,updated_at) VALUES (1,'EPG1','[]','2026-09-01','2026-09-01')")
        self.conn.execute("INSERT INTO epost_global_tracking (store_id,tracking_code,events_json,created_at,updated_at) VALUES (2,'EPG1','[]','2026-09-01','2026-09-01')")
        payload = {"tracking_code": "EPG1", "status": "Delivered", "date": "2026-09-04", "events": [{"Event": "Delivered", "EventDT": "2026-09-04"}]}
        self.assertEqual(0, function(SimpleNamespace(results=[payload]))["updated"])
        self.assertEqual(1, function(SimpleNamespace(results=[{**payload,"store_id":1}]))["updated"])
        stale = {**payload,"store_id":1,"date":"2026-09-01","status":"Delivery failed","events":[{"Event":"Delivery failed","EventDT":"2026-09-01"}]}
        function(SimpleNamespace(results=[stale]))
        row = self.conn.execute("SELECT status,events_json FROM epost_global_tracking WHERE store_id=1").fetchone()
        self.assertEqual("Delivered", row["status"])
        self.assertEqual(2, len(json.loads(row["events_json"])))
        self.assertIsNone(self.conn.execute("SELECT status FROM epost_global_tracking WHERE store_id=2").fetchone()[0])
