import unittest
from pathlib import Path


APP = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")


class PaymentFailureCancellationTests(unittest.TestCase):
    def test_open_payment_failure_page_excludes_cancelled_amazon_orders(self) -> None:
        start = APP.index("def payment_failure_rows(")
        end = APP.index("def tracking_rows(", start)
        source = APP[start:end]

        self.assertIn("ol.amazon_cancelled_order_id=amazon_payment_failures.amazon_order_id", source)
        self.assertIn('if status == "open":', source)

    def test_stale_payment_failure_cleanup_resolves_cancelled_amazon_orders(self) -> None:
        start = APP.index("def cleanup_stale_payment_failures(")
        end = APP.index("def iter_nested_strings(", start)
        source = APP[start:end]

        self.assertIn("ol.amazon_cancelled_order_id=f.amazon_order_id", source)
        self.assertIn("resolve_payment_failure_and_clear_dispatch", source)
        self.assertIn('"payment-failures"', source)

    def test_tracking_cancellation_resolves_the_payment_failure_immediately(self) -> None:
        start = APP.index('@app.post("/api/tracking/update")')
        end = APP.index('@app.get("/api/tracking/payment-failures")', start)
        source = APP[start:end]

        self.assertGreaterEqual(source.count("resolve_payment_failure_and_clear_dispatch"), 2)
        self.assertGreaterEqual(source.count('"payment-failures"'), 2)

    def test_status_only_recheck_resolves_and_invalidates_payment_failure(self) -> None:
        start = APP.index("if status_only_payload and not payload.order_cancelled")
        end = APP.index("        rows = []", start)
        source = APP[start:end]

        self.assertIn("resolve_payment_failure_for_order(conn, amazon_order_id, now)", source)
        self.assertIn('"payment-failures"', source)
        self.assertIn('"payment_failure_resolved": bool(resolved_payment_failure)', source)

    def test_regular_tracking_resolution_invalidates_payment_failure_page(self) -> None:
        start = APP.index("resolved_payment_failure = resolve_payment_failure_for_order(conn, amazon_order_id, utc_now())")
        end = APP.index("        updated = 0", start)
        source = APP[start:end]

        self.assertIn('"payment-failures"', source)


if __name__ == "__main__":
    unittest.main()
