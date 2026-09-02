import json
import pathlib
import re
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTENT = (ROOT / "tracking-extension" / "content.js").read_text()
BACKGROUND = (ROOT / "tracking-extension" / "background.js").read_text()
MANIFEST = json.loads((ROOT / "tracking-extension" / "manifest.json").read_text())


class TrackingExtensionShipmentMappingTests(unittest.TestCase):
    def test_one_shipment_grid_is_preferred_over_the_shared_shipments_wrapper(self):
        start = CONTENT.index("function shipmentRootForTrackingLink(link)")
        end = CONTENT.index("async function parseOrderDetails()", start)
        helper = CONTENT[start:end]

        self.assertLess(helper.index("if (shipmentGrid) return shipmentGrid;"), helper.index("if (shipmentComponent) return shipmentComponent;"))

    def test_tracking_extension_version_was_bumped(self):
        self.assertEqual(MANIFEST["version"], "0.1.72")

    def test_manual_tracking_pages_sync_without_bypassing_active_queue_guard(self):
        # Execute the actual tracking-page branch, not a copy of its condition.
        start = CONTENT.index("async function run() {")
        end = CONTENT.index("  await waitForPageReady();", start)
        run_source = CONTENT[start:end] + "\n}"
        script = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
async function check(state, pageMatches, expected) {
  const calls = [];
  const sandbox = {
    extensionContextAlive: true, location: {hostname:'www.amazon.com', href:'https://www.amazon.com/progress-tracker/package?orderId=111-4064278-0332216', pathname:'/progress-tracker/package'},
    window: {}, isRelevantTrackingPage:()=>true, amazonSignedInAccountName:()=>'',
    showPanel:()=>{}, send:async()=>state, isOrderHistoryPage:()=>false,
    isTrackingPage:()=>true, currentPageMatchesActiveTracking:()=>pageMatches,
    recoverActiveTrackingPage:async()=>calls.push('recover'),
    waitForTrackingPageReady:async()=>{},
    parseTrackingPage:async()=>({amazonOrderId:'111-4064278-0332216',package:{status:'Delivered September 1'}}),
    sendWithTimeout:async(message)=>{calls.push(message.type);return {ok:true};},
    followRecoveryRedirect:()=>false,
  };
  vm.createContext(sandbox);
  await vm.runInContext(SOURCE + '\nrun()', sandbox);
  assert.deepEqual(calls, expected);
}
(async()=>{
  await check({tracking:{running:false}}, false, ['PACKAGE_TRACKING']);
  await check({}, false, ['PACKAGE_TRACKING']);
  await check({tracking:{running:true}}, true, ['PACKAGE_TRACKING']);
  await check({tracking:{running:true}}, false, ['recover']);
  await check({timedOut:true}, false, []);
  await check(null, false, []);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
        subprocess.run(["node", "-e", "const SOURCE = " + json.dumps(run_source) + ";\n" + script], check=True, capture_output=True, text=True)

    def test_current_amazon_progress_tracker_links_are_parsed(self):
        self.assertGreaterEqual(CONTENT.count("a[href*='/progress-tracker/package']"), 2)

    def test_split_shipment_identity_is_captured_and_guarded_before_merge(self):
        self.assertIn("function amazonShipmentIdentity", CONTENT)
        self.assertIn("...shipmentIdentity", CONTENT)
        self.assertIn("function splitShipmentMismatch", BACKGROUND)
        mismatch_index = BACKGROUND.index("const shipmentMismatch = splitShipmentMismatch(queuedPackage, pagePackage);")
        merge_index = BACKGROUND.index("const packageData = { ...queuedPackage, ...pagePackage };", mismatch_index)
        self.assertLess(mismatch_index, merge_index)

    def test_progress_tracker_cancel_links_are_not_packages(self):
        self.assertGreaterEqual(CONTENT.count(r"/\/progress-tracker\/package\/?$/i"), 2)

    def test_weekday_delivery_promises_are_resolved(self):
        start = CONTENT.index("function promiseDetails(text)")
        end = CONTENT.index("function withPromiseDetails", start)
        helper = CONTENT[start:end]

        self.assertRegex(helper, re.compile(r"Sunday\|Monday\|Tuesday\|Wednesday\|Thursday\|Friday\|Saturday"))


if __name__ == "__main__":
    unittest.main()
