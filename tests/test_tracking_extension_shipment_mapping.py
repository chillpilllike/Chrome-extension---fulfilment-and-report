import json
import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTENT = (ROOT / "tracking-extension" / "content.js").read_text()
MANIFEST = json.loads((ROOT / "tracking-extension" / "manifest.json").read_text())


class TrackingExtensionShipmentMappingTests(unittest.TestCase):
    def test_one_shipment_grid_is_preferred_over_the_shared_shipments_wrapper(self):
        start = CONTENT.index("function shipmentRootForTrackingLink(link)")
        end = CONTENT.index("async function parseOrderDetails()", start)
        helper = CONTENT[start:end]

        self.assertLess(helper.index("if (shipmentGrid) return shipmentGrid;"), helper.index("if (shipmentComponent) return shipmentComponent;"))

    def test_tracking_extension_version_was_bumped(self):
        self.assertEqual(MANIFEST["version"], "0.1.69")

    def test_current_amazon_progress_tracker_links_are_parsed(self):
        self.assertGreaterEqual(CONTENT.count("a[href*='/progress-tracker/package']"), 2)

    def test_weekday_delivery_promises_are_resolved(self):
        start = CONTENT.index("function promiseDetails(text)")
        end = CONTENT.index("function withPromiseDetails", start)
        helper = CONTENT[start:end]

        self.assertRegex(helper, re.compile(r"Sunday\|Monday\|Tuesday\|Wednesday\|Thursday\|Friday\|Saturday"))


if __name__ == "__main__":
    unittest.main()
