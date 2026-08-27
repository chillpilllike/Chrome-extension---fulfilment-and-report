import inspect
import unittest
from pathlib import Path

from app import main


class _Cursor:
    def __init__(self, rows=None, row=None):
        self._rows = rows or []
        self._row = row

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class _ProtectionConnection:
    def __init__(self, shopify_rows=None, completed_job=False, completed_local=False):
        self.shopify_rows = shopify_rows or []
        self.completed_job = completed_job
        self.completed_local = completed_local

    def execute(self, sql, _params=()):
        if "FROM order_lines" in sql:
            return _Cursor(row={"exists": 1} if self.completed_local else None)
        if "FROM shopify_order_status_cache" in sql:
            return _Cursor(rows=self.shopify_rows)
        if "FROM shopify_fulfilment_jobs" in sql:
            return _Cursor(row={"exists": 1} if self.completed_job else None)
        raise AssertionError(f"Unexpected SQL: {sql}")


def _row(state="ordered"):
    return {
        "id": 11,
        "store_id": 2,
        "odoo_order_id": 3,
        "odoo_order_name": "NC12345",
        "state": state,
    }


class TrackingCancellationResetTests(unittest.TestCase):
    def test_delivered_or_inventory_order_is_protected(self) -> None:
        for state in ("delivered", "inventory"):
            with self.subTest(state=state):
                reasons = main.tracking_cancellation_downstream_reasons(
                    _ProtectionConnection(completed_local=True),
                    [_row(state)],
                )
                self.assertIn((2, 3), reasons)
                self.assertIn("delivered/inventory", reasons[(2, 3)][0])

    def test_fulfilled_shopify_order_is_protected(self) -> None:
        reasons = main.tracking_cancellation_downstream_reasons(
            _ProtectionConnection(
                shopify_rows=[
                    {
                        "fulfillment_status": "FULFILLED",
                        "shopify_order_name": "#1001",
                        "shopify_order_id": "1001",
                    }
                ]
            ),
            [_row()],
        )
        self.assertIn("Shopify order #1001 is already fulfilled", reasons[(2, 3)])

    def test_completed_shopify_job_is_protected(self) -> None:
        reasons = main.tracking_cancellation_downstream_reasons(
            _ProtectionConnection(completed_job=True),
            [_row()],
        )
        self.assertIn("a Shopify fulfilment job has already completed", reasons[(2, 3)])

    def test_safe_reset_clears_active_fulfilment_but_keeps_cancellation_audit(self) -> None:
        source = inspect.getsource(main.reset_cancelled_amazon_fulfilment)

        self.assertIn("amazon_cancelled_order_id=?", source)
        self.assertIn("amazon_cancelled_at=?", source)
        self.assertIn("amazon_order_id=NULL", source)
        self.assertIn("amazon_account_name=NULL", source)
        self.assertIn("amazon_unit_price=NULL", source)
        self.assertIn("amazon_total_price=NULL", source)
        self.assertIn("chrome_profit_total=NULL", source)
        self.assertIn("state='pulled'", source)
        self.assertIn("clear_stale_downstream_fulfilment_for_order", source)
        self.assertIn("dispatch_clear_packages_for_amazon_order", source)

    def test_completed_downstream_path_stays_linked_for_manual_review(self) -> None:
        source = inspect.getsource(main.reset_cancelled_amazon_fulfilment)
        protected_start = source.index("if reasons:")
        reset_start = source.index("amazon_order_id=NULL")
        protected_source = source[protected_start:reset_start]

        self.assertIn("amazon_status='cancelled_review'", protected_source)
        self.assertIn("Amazon cancelled - manual review", protected_source)
        self.assertIn("Review Odoo/Shopify fulfilment manually", protected_source)
        self.assertNotIn("amazon_order_id=NULL", protected_source)

    def test_tracking_endpoint_includes_inventory_and_reports_both_outcomes(self) -> None:
        source = inspect.getsource(main.api_tracking_update_impl)

        self.assertIn("state IN ('ordered', 'dispatched', 'delivered', 'inventory')", source)
        self.assertIn("reset_cancelled_amazon_fulfilment", source)
        self.assertIn('"manual_review": review_count', source)
        self.assertIn("sync_odoo_ordered_tags_after_commit", source)

    def test_extension_reports_manual_review_instead_of_claiming_reset(self) -> None:
        background = (
            Path(__file__).resolve().parents[1] / "tracking-extension" / "background.js"
        ).read_text(encoding="utf-8")

        self.assertIn("cancellationResult?.manual_review", background)
        self.assertIn("completed downstream fulfilment was kept and flagged for manual review", background)


if __name__ == "__main__":
    unittest.main()
