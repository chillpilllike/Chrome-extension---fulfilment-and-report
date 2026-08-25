import copy
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    ROOT / "app" / "services" / "shopify_scripts" / "dtc_orders_export.py",
    ROOT / "app" / "services" / "shopify_scripts" / "dtb_orders_export.py",
)


def load_script(path: Path):
    spec = importlib.util.spec_from_file_location(f"test_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class RejectPhoneOnceShop:
    name = "test-shop"

    def __init__(self) -> None:
        self.calls = []

    def _request(self, method, url, payload):
        self.calls.append(copy.deepcopy(payload))
        if len(self.calls) == 1:
            raise RuntimeError("HTTP 422 -> {'errors': {'phone': ['has already been taken']}}")
        return {"customer": {"id": 123}}


class ShopifyCustomerPhoneRetryTests(unittest.TestCase):
    def test_customer_phone_collision_retries_without_phone(self) -> None:
        for path in SCRIPTS:
            with self.subTest(script=path.name):
                module = load_script(path)
                shop = RejectPhoneOnceShop()
                payload = {
                    "customer": {
                        "id": 123,
                        "first_name": "Test",
                        "email": "customer@example.com",
                        "phone": "+12125550123",
                        "addresses": [{"address1": "1 Main St", "phone": "+12125550123"}],
                    }
                }

                result = module._request_customer_with_validation_retry(
                    shop, "PUT", "customers/123.json", payload
                )

                self.assertEqual(result["customer"]["id"], 123)
                self.assertEqual(len(shop.calls), 2)
                self.assertIn("phone", shop.calls[0]["customer"])
                self.assertNotIn("phone", shop.calls[1]["customer"])
                self.assertNotIn("phone", shop.calls[1]["customer"]["addresses"][0])
                self.assertEqual(shop.calls[1]["customer"]["email"], "customer@example.com")
                self.assertEqual(shop.calls[1]["customer"]["first_name"], "Test")


if __name__ == "__main__":
    unittest.main()
