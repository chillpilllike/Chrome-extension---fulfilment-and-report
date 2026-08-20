import json
import pathlib
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
        self.assertEqual(MANIFEST["version"], "0.1.68")


if __name__ == "__main__":
    unittest.main()
