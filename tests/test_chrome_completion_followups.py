import unittest
from unittest.mock import patch
from types import SimpleNamespace

from app.main import (
    chrome_complete_followups,
    chrome_complete_shopify_followup,
    complete_chrome_job_from_exact_history_match,
)


class ChromeCompletionFollowupTests(unittest.TestCase):
    def test_exact_history_recovery_builds_a_complete_evidence_payload(self) -> None:
        job = {
            "group_key": "chrome-1-23003-example",
            "line_ids": [3316290],
            "items": [{"asin": "b005cd3i5o", "line_ids": [3316290]}],
        }
        history = {
            "amazon_order_id": "111-1835030-4182640",
            "amazon_order_url": "",
            "recipient": "Nutricity NC22983",
            "order_date": "August 20, 2026",
            "asins": ["B005CD3I5O"],
        }
        with patch("app.main.api_chrome_job_complete", return_value={"ok": True}) as complete:
            result = complete_chrome_job_from_exact_history_match(job, history, "chrome-worker")

        self.assertEqual(result, {"ok": True})
        group_key, payload = complete.call_args.args
        self.assertEqual(group_key, "chrome-1-23003-example")
        self.assertEqual(payload.amazon_order_id, "111-1835030-4182640")
        self.assertEqual(payload.amazon_recipient, "Nutricity NC22983")
        self.assertEqual(payload.amazon_asins, ["B005CD3I5O"])
        self.assertEqual(payload.line_ids, [3316290])
        self.assertEqual(payload.worker_id, "chrome-worker")
        self.assertEqual(payload.order_mappings[0]["line_ids"], [3316290])

    def test_odoo_audit_is_independent_from_shopify_enqueue(self) -> None:
        rows = [{
            "store_id": 1,
            "odoo_order_id": 23257,
            "asin": "B0FHMMH8Q4",
            "fulfilment_note": "",
        }]

        with (
            patch("app.main.get_store", return_value=SimpleNamespace(id=1)),
            patch("app.main.enqueue_odoo_chatter_note", return_value={"queued": True, "posted": False}) as enqueue_note,
            patch("app.main.sync_odoo_ordered_tags_for_pairs") as sync_tags,
            patch("app.main.write_report") as write_report,
            patch("app.main.enqueue_shopify_fulfilment_for_rows") as enqueue_shopify,
        ):
            confirmed = chrome_complete_followups(
                rows,
                {},
                "111-6417852-2612223",
                "https://www.amazon.com/your-orders/order-details?orderID=111-6417852-2612223",
                "Sergey",
            )

        enqueue_note.assert_called_once()
        note_body = enqueue_note.call_args.args[4]
        self.assertIn("111-6417852-2612223", note_body)
        self.assertIn("Verified item: B0FHMMH8Q4 × 1", note_body)
        self.assertGreater(note_body.index("Amazon order link:"), note_body.index("Amazon account:"))
        self.assertTrue(note_body.endswith("</a></p>"))
        sync_tags.assert_called_once_with({(1, 23257)})
        write_report.assert_called_once_with(1)
        enqueue_shopify.assert_not_called()
        self.assertTrue(confirmed)

    def test_existing_posted_chatter_note_is_confirmed_idempotently(self) -> None:
        rows = [{
            "store_id": 1,
            "odoo_order_id": 23025,
            "asin": "B074R8XH2Q",
            "fulfilment_note": "",
        }]
        with (
            patch("app.main.get_store", return_value=SimpleNamespace(id=1)),
            patch("app.main.enqueue_odoo_chatter_note", return_value={"queued": True, "posted": True}),
            patch("app.main.sync_odoo_ordered_tags_for_pairs"),
            patch("app.main.write_report"),
        ):
            confirmed = chrome_complete_followups(
                rows,
                {},
                "111-1021200-2894667",
                "https://www.amazon.com/your-orders/order-details?orderID=111-1021200-2894667",
                "Sergey",
                "Nutricity NC23005 500mg",
            )
        self.assertTrue(confirmed)

    def test_shopify_enqueue_runs_in_its_own_followup(self) -> None:
        rows = [{"id": 3321013}]
        with (
            patch("app.main.enqueue_shopify_fulfilment_for_rows", return_value=1) as enqueue,
            patch("app.main.start_shopify_fulfilment_worker") as start_worker,
        ):
            chrome_complete_shopify_followup(rows)

        enqueue.assert_called_once_with(rows)
        start_worker.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
