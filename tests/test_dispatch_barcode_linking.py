import json
import unittest
from unittest.mock import patch

from app.main import (
    api_package_pickup_scan,
    dispatch_barcode_kind,
    dispatch_merge_scanned_codes,
    dispatch_scan_code_is_physical,
    normalize_dispatch_scan_code,
)
from app.schemas.payloads import DispatchScanPayload, PackagePickupScanPayload


class DispatchBarcodeLinkingTests(unittest.TestCase):
    def test_tba_linear_barcode_is_physical_amazon_tracking(self):
        code = "TBA333598452175"
        self.assertTrue(dispatch_scan_code_is_physical(code))
        self.assertEqual(dispatch_barcode_kind(code), "amazon_tracking")

    def test_square_label_value_is_internal_not_tracking(self):
        code = "SPqxPzY36T_001_v"
        self.assertFalse(dispatch_scan_code_is_physical(code))
        self.assertEqual(dispatch_barcode_kind(code), "amazon_internal_label")
        self.assertEqual(normalize_dispatch_scan_code(code), "SPQXPZY36T001V")

    def test_printed_routing_token_is_internal_not_tracking(self):
        self.assertEqual(dispatch_barcode_kind("PqxPzY36T/1/804"), "amazon_internal_label")

    def test_internal_label_alias_is_kept_beside_tracking(self):
        row = {
            "scanned_codes_json": json.dumps(["TBA333598452175"]),
            "last_scanned_code": "TBA333598452175",
        }
        merged = json.loads(dispatch_merge_scanned_codes(row, "SPQXPZY36T001V", "SPqxPzY36T_001_v"))
        self.assertEqual(merged[0], "SPqxPzY36T_001_v")
        self.assertIn("TBA333598452175", merged)

    def test_scan_payload_alias_lists_are_not_shared(self):
        first = DispatchScanPayload(scan_code="TBA333598452175")
        second = DispatchScanPayload(scan_code="TBA332841203092")
        first.alias_codes.append("SPqxPzY36T_001_v")
        self.assertEqual(second.alias_codes, [])

    def test_pickup_scan_payload_alias_lists_are_not_shared(self):
        first = PackagePickupScanPayload(scan_code="TBA333598452175")
        second = PackagePickupScanPayload(scan_code="TBA332841203092")
        first.alias_codes.append("SPqxPzY36T_001_v")
        self.assertEqual(second.alias_codes, [])

    @patch("app.main.db")
    def test_pickup_internal_label_is_ignored_without_history_write(self, database):
        result = api_package_pickup_scan(PackagePickupScanPayload(scan_code="SPqxPzY36T_001_v"))
        self.assertTrue(result["ignored"])
        self.assertFalse(result["matched"])
        self.assertNotIn("event_id", result)
        database.assert_not_called()


if __name__ == "__main__":
    unittest.main()
