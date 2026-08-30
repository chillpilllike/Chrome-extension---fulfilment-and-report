import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import main


class _Cursor:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.rowcount = len(self._rows)

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        return _Cursor()


class OdooHoldTagGuardTests(unittest.TestCase):
    def test_hold_tag_matching_is_exact_and_case_insensitive(self) -> None:
        odoo = Mock()
        odoo.fields_get.return_value = {"tag_ids": {"relation": "crm.tag"}}
        odoo.read.return_value = [
            {"id": 9, "name": "Hold"},
            {"id": 10, "name": "hold for review"},
        ]
        orders = [
            {"id": 24703, "tag_ids": [9]},
            {"id": 24704, "tag_ids": [10]},
            {"id": 24705, "tag_ids": []},
        ]

        self.assertEqual(main.odoo_order_ids_with_tag(odoo, orders, "hold"), {24703})
        odoo.read.assert_called_once_with("crm.tag", [9, 10], ["id", "name"])

    def test_held_order_is_removed_from_pre_submit_queue(self) -> None:
        odoo = Mock()
        odoo.existing_fields.return_value = ["id", "name", "state", "invoice_status", "tag_ids"]
        odoo.fields_get.return_value = {"tag_ids": {"relation": "crm.tag"}}
        odoo.read.side_effect = [
            [{"id": 24703, "name": "NC24703", "state": "sale", "invoice_status": "to invoice", "tag_ids": [9]}],
            [{"id": 9, "name": "HOLD"}],
        ]
        conn = _Connection()
        lines = [{
            "id": 1,
            "store_id": 1,
            "odoo_order_id": 24703,
            "odoo_order_name": "NC24703",
            "state": "submitted",
            "amazon_group_key": "group-NC24703",
        }]

        with (
            patch.object(main, "get_store", return_value=SimpleNamespace(id=1)),
            patch.object(main, "OdooClient", return_value=odoo),
            patch.object(main, "fast_page_cache_clear_matching"),
        ):
            eligible, blocked, messages = main.filter_lines_by_live_odoo_status(conn, 1, lines)

        self.assertEqual(eligible, [])
        self.assertEqual(blocked, 1)
        self.assertIn("NC24703 has the hold tag", messages[0])
        update_sql = next(sql for sql, _params in conn.statements if "UPDATE order_lines" in sql)
        self.assertIn("THEN 'pulled'", update_sql)
        self.assertIn("amazon_group_key=CASE", update_sql)
        self.assertIn("chrome_claimed_by=NULL", update_sql)

    def test_missing_odoo_tag_field_fails_closed(self) -> None:
        odoo = Mock()
        odoo.existing_fields.return_value = ["id", "name", "state", "invoice_status"]
        with (
            patch.object(main, "get_store", return_value=SimpleNamespace(id=1)),
            patch.object(main, "OdooClient", return_value=odoo),
        ):
            with self.assertRaises(main.HTTPException) as raised:
                main.filter_lines_by_live_odoo_status(
                    _Connection(),
                    1,
                    [{"id": 1, "odoo_order_id": 24703}],
                )

        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("hold tags cannot be verified", raised.exception.detail)

    def test_queue_claim_and_final_submit_all_recheck_live_odoo_tags(self) -> None:
        claim_source = inspect.getsource(main.claim_next_chrome_job)
        submit_source = inspect.getsource(main.api_chrome_job_submitted)
        queue_source = inspect.getsource(main.queue_chrome_order_groups_fast)

        self.assertGreaterEqual(claim_source.count("filter_lines_by_live_odoo_status("), 2)
        self.assertIn("filter_lines_by_live_odoo_status(", queue_source)
        self.assertLess(
            submit_source.index("filter_lines_by_live_odoo_status("),
            submit_source.index("SET amazon_status=?"),
        )
        self.assertIn("Amazon Place Order was stopped", submit_source)


if __name__ == "__main__":
    unittest.main()
