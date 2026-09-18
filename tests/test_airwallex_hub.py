import hashlib
import hmac
import json
import re
import unittest
import xmlrpc.client

from app.services.airwallex_hub import (
    extract_order_reference,
    normalize_prefixes,
    order_prefix,
    verify_webhook_signature,
    webhook_event_details,
    json_text,
)


class AirwallexHubTests(unittest.TestCase):
    def test_json_rpc_transport_preserves_large_timestamps(self):
        payload = {'data': {'created_at': 1789749635251, 'settled_at': 1789749638247}}
        with self.assertRaises(OverflowError):
            xmlrpc.client.dumps((payload,))
        encoded = xmlrpc.client.dumps((json_text(payload),))
        args, _ = xmlrpc.client.loads(encoded)
        self.assertEqual(json.loads(args[0]), payload)

    def test_extracts_supported_store_references_from_customer_text(self):
        for value, expected in {
            "Binder NC20229": "NC20229",
            "NC20225Medicine": "NC20225",
            "payment SG12345 thanks": "SG12345",
            "wk98765": "WK98765",
            "no order": "",
        }.items():
            with self.subTest(value=value):
                self.assertEqual(extract_order_reference(value), expected)
                self.assertEqual(order_prefix(value), re.sub(r"\d.*", "", expected))

    def test_normalizes_store_prefix_configuration(self):
        self.assertEqual(normalize_prefixes("nc, SG | wk;nc"), ["NC", "SG", "WK"])

    def test_validates_airwallex_signature_against_any_registered_secret(self):
        payload = {"id": "evt-1", "name": "deposit.settled", "data": {"reference": "NH10123"}}
        raw = json.dumps(payload).encode()
        timestamp = "1700000000000"
        signature = hmac.new(b"correct", timestamp.encode() + raw, hashlib.sha256).hexdigest()
        self.assertTrue(verify_webhook_signature(
            timestamp=timestamp,
            signature=signature,
            raw_body=raw,
            secrets=["wrong", "correct"],
            now=1700000000,
        ))
        self.assertFalse(verify_webhook_signature(
            timestamp=timestamp,
            signature=signature,
            raw_body=raw,
            secrets=["wrong"],
            now=1700000000,
        ))

    def test_event_details_are_ready_for_routing_and_logging(self):
        details = webhook_event_details({
            "id": "evt-2",
            "name": "deposit.pending",
            "data": {
                "id": "dep-2",
                "reference": "invoice ES45678 medicine",
                "amount": 12.5,
                "currency": "aud",
                "status": "pending",
            },
        })
        self.assertEqual(details["order_reference"], "ES45678")
        self.assertEqual(details["order_prefix"], "ES")
        self.assertEqual(details["currency"], "AUD")


if __name__ == "__main__":
    unittest.main()
