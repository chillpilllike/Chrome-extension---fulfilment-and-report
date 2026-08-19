import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

from app.main import shopify_should_check_existing_order


ROOT = Path(__file__).resolve().parents[1]


def load_exporter(filename: str):
    path = ROOT / "app" / "services" / "shopify_scripts" / filename
    spec = importlib.util.spec_from_file_location(f"test_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body
        self.text = "response"
        self.headers = {}
        self.url = "https://example.myshopify.com/admin/api/test/orders.json"
        self.request = SimpleNamespace(method="POST")

    def json(self):
        return self._body

    def raise_for_status(self):
        raise requests.HTTPError(f"HTTP {self.status_code}")


class SequenceSession:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def request(self, *args, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class ShopifyDuplicatePreventionTests(unittest.TestCase):
    def test_existing_order_check_is_never_skipped(self):
        self.assertTrue(shopify_should_check_existing_order({}))
        self.assertTrue(shopify_should_check_existing_order({"order_engine": "third_party"}))

    def test_order_create_timeout_is_not_retried(self):
        for filename in ("dtc_orders_export.py", "dtb_orders_export.py"):
            with self.subTest(filename=filename):
                module = load_exporter(filename)
                client = module.ShopifyClient("test", "example.myshopify.com", "token", "test")
                session = SequenceSession([requests.Timeout("response lost"), FakeResponse(201, {"order": {"id": 2}})])
                client.session = session

                with self.assertRaises(requests.Timeout):
                    client.create_order({"order": {"name": "NC1", "line_items": [{}]}})

                self.assertEqual(session.calls, 1)

    def test_order_create_server_error_is_not_retried(self):
        for filename in ("dtc_orders_export.py", "dtb_orders_export.py"):
            with self.subTest(filename=filename):
                module = load_exporter(filename)
                client = module.ShopifyClient("test", "example.myshopify.com", "token", "test")
                session = SequenceSession([
                    FakeResponse(500, {"errors": "province is not valid; phone is invalid"}),
                    FakeResponse(201, {"order": {"id": 2}}),
                ])
                client.session = session

                with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
                    client.create_order({"order": {"name": "NC1", "line_items": [{}]}})

                self.assertEqual(session.calls, 1)

    def test_rate_limit_rejection_can_still_retry_safely(self):
        module = load_exporter("dtc_orders_export.py")
        client = module.ShopifyClient("test", "example.myshopify.com", "token", "test")
        session = SequenceSession([
            FakeResponse(429, {"errors": "rate limited"}),
            FakeResponse(201, {"order": {"id": 2, "name": "NC1"}}),
        ])
        client.session = session

        with patch.object(module.time, "sleep", return_value=None):
            order_id = client.create_order({"order": {"name": "NC1", "line_items": [{}]}})

        self.assertEqual(order_id, 2)
        self.assertEqual(session.calls, 2)


if __name__ == "__main__":
    unittest.main()
