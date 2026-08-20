import json
import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKGROUND = (ROOT / "chrome-extension" / "background.js").read_text()
CONTENT = (ROOT / "chrome-extension" / "content.js").read_text()
MANIFEST = json.loads((ROOT / "chrome-extension" / "manifest.json").read_text())
POPUP_HTML = (ROOT / "chrome-extension" / "popup.html").read_text()
POPUP_JS = (ROOT / "chrome-extension" / "popup.js").read_text()
APP = (ROOT / "app" / "main.py").read_text()


class ChromeExtensionHandoffGuardTests(unittest.TestCase):
    def test_background_and_content_builds_match(self) -> None:
        background_build = re.search(r'EXPECTED_CONTENT_SCRIPT_BUILD = "([^"]+)"', BACKGROUND)
        content_build = re.search(r'CONTENT_SCRIPT_BUILD = "([^"]+)"', CONTENT)
        self.assertIsNotNone(background_build)
        self.assertIsNotNone(content_build)
        self.assertEqual(background_build.group(1), content_build.group(1))

    def test_submit_marker_carries_job_identity(self) -> None:
        self.assertIn('type: "MARK_ORDER_SUBMITTED",\n    groupKey:', CONTENT)
        self.assertIn('markAmazonSubmitted(windowId, String(message.groupKey || ""), String(message.workerId || ""))', BACKGROUND)

    def test_completion_report_carries_job_identity(self) -> None:
        self.assertIn('type: "COMPLETE_JOB",\n        groupKey:', CONTENT)
        self.assertIn('expectedGroupKey = "", expectedWorkerId = ""', BACKGROUND)
        self.assertIn('Ignored stale completion report', BACKGROUND)

    def test_hidden_worker_is_explicitly_woken_after_handoff(self) -> None:
        self.assertIn('chrome.tabs.sendMessage(tabId, { type: "RUN_ACTIVE_JOB" })', BACKGROUND)
        self.assertIn('if (message.type === "RUN_ACTIVE_JOB")', CONTENT)

    def test_checkout_recipient_match_is_exact(self) -> None:
        matcher = re.search(
            r'function checkoutDeliveryRecipientMatches\(name\) \{(?P<body>.*?)\n\}',
            CONTENT,
            re.DOTALL,
        )
        self.assertIsNotNone(matcher)
        body = matcher.group("body")
        self.assertIn('deliveredTo === wanted', body)
        self.assertNotIn('.includes(', body)

    def test_manifest_version_was_bumped(self) -> None:
        self.assertEqual(MANIFEST["version"], "0.1.74")

    def test_popup_exposes_the_loaded_extension_version(self) -> None:
        self.assertIn('id="extensionVersion"', POPUP_HTML)
        self.assertIn("chrome.runtime.getManifest().version", POPUP_JS)

    def test_popup_start_gives_immediate_background_worker_feedback(self) -> None:
        self.assertIn('const startNextButton = document.querySelector("#start");', POPUP_JS)
        self.assertIn("startNextButton.disabled = true;", POPUP_JS)
        self.assertIn("The Amazon worker is running in the background.", POPUP_JS)
        self.assertIn("await refresh();", POPUP_JS)
        self.assertIn("startNextButton.disabled = false;", POPUP_JS)

    def test_reporting_evidence_cannot_regress_during_same_job_refresh(self) -> None:
        set_window_start = BACKGROUND.index("async function setWindowJob")
        clear_group_start = BACKGROUND.index("async function clearStoredJobGroup", set_window_start)
        set_window = BACKGROUND[set_window_start:clear_group_start]
        self.assertIn("activeJobHasReportedOrderId(current)", set_window)
        self.assertIn('stage: "reporting_complete"', set_window)
        self.assertIn("reportedOrderId: current.reportedOrderId", set_window)
        self.assertIn("reportAttemptedAt: current.reportAttemptedAt", set_window)

        set_active_start = CONTENT.index("async function setActiveJob")
        state_start = CONTENT.index("async function getExtensionState", set_active_start)
        set_active = CONTENT[set_active_start:state_start]
        self.assertIn('latest.stage === "reporting_complete"', set_active)
        self.assertIn("reportedOrderId: latestReportedOrderId", set_active)
        self.assertIn("reportAttemptedAt: latest.reportAttemptedAt", set_active)

    def test_queue_refresh_returns_the_persisted_monotonic_job(self) -> None:
        refresh_start = BACKGROUND.index("async function refreshActiveJobFromQueue")
        refresh_end = BACKGROUND.index("async function openControlWindow", refresh_start)
        refresh = BACKGROUND[refresh_start:refresh_end]
        self.assertIn('setWindowJob(windowId, next, { reason: "queue_refresh" })', refresh)
        self.assertIn("return (await getWindowState(windowId)).activeJob || next;", refresh)

    def test_multi_item_cart_rows_get_stabilization_reloads_without_duplicate_add(self) -> None:
        self.assertIn("const MAX_CART_VERIFICATION_RELOADS = 3;", CONTENT)
        cart_start = CONTENT.index("async function handleCart(activeJob)")
        cart_end = CONTENT.index("function extractOrderId()", cart_start)
        cart = CONTENT[cart_start:cart_end]
        self.assertGreaterEqual(cart.count("retryCount < MAX_CART_VERIFICATION_RELOADS"), 2)
        self.assertGreaterEqual(cart.count("location.reload();"), 2)
        self.assertIn("without clicking Add to cart a second time", cart)

    def test_single_asin_checkout_reduction_is_an_actionable_shortage(self) -> None:
        guard_start = CONTENT.index("async function ensureCheckoutOnlyExpectedUnits")
        guard_end = CONTENT.index("function checkoutLineItemLimitCandidate", guard_start)
        guard = CONTENT[guard_start:guard_end]
        self.assertIn("verifiedSingleAsinShortage", guard)
        self.assertIn("expectedAsinList.length === 1", guard)
        self.assertIn("cartVerificationMatches(activeJob)", guard)
        self.assertIn('failureCode: verifiedSingleAsinShortage ? "partial_quantity" : "cart_quantity_mismatch"', guard)
        self.assertIn('missingAsin: verifiedSingleAsinShortage ? expectedAsinList[0] : ""', guard)

    def test_claim_reconciles_one_exact_history_order_before_chrome_checkout(self) -> None:
        self.assertIn("def exact_amazon_history_match_for_chrome_job(", APP)
        self.assertIn("order_names.issubset(recipient_refs)", APP)
        self.assertIn("if set(actual) != set(expected):", APP)
        self.assertIn("return matches[0] if len(matches) == 1 else None", APP)
        jobs_start = APP.index('@app.get("/api/chrome/jobs")')
        jobs_end = APP.index('@app.get("/api/chrome/jobs/recover-submitted")', jobs_start)
        jobs = APP[jobs_start:jobs_end]
        self.assertIn("existing = exact_amazon_history_match_for_chrome_job(job)", jobs)
        self.assertIn("completion = complete_chrome_job_from_exact_history_match(job, existing, worker_id)", jobs)
        self.assertIn('"reconciled_existing": reconciled_existing', jobs)

    def test_submitted_recovery_consumes_an_exact_history_match(self) -> None:
        recover_start = APP.index('@app.get("/api/chrome/jobs/recover-submitted")')
        recover_end = APP.index('@app.post("/api/chrome/jobs/{group_key}/heartbeat")', recover_start)
        recover = APP[recover_start:recover_end]
        self.assertIn("existing = exact_amazon_history_match_for_chrome_job(job)", recover)
        self.assertIn("complete_chrome_job_from_exact_history_match(job, existing, worker_id)", recover)
        self.assertIn('"recovered": True', recover)

        background_start = BACKGROUND.index("async function recoverSubmittedJobInWindow")
        background_end = BACKGROUND.index("async function cleanupCartBeforeNextJob", background_start)
        background = BACKGROUND[background_start:background_end]
        self.assertIn("if (result.recovered && result.group_key)", background)
        self.assertIn("await clearStoredJobGroup(result.group_key);", background)

    def test_history_lookup_server_completes_exact_submissions_and_repairs_chatter(self) -> None:
        lookup_start = APP.index('@app.post("/api/chrome/order-history/lookup")')
        lookup_end = APP.index('@app.post("/api/chrome/order-history/odoo-direct")', lookup_start)
        lookup = APP[lookup_start:lookup_end]
        self.assertIn("reconcile_exact_submitted_chrome_jobs_from_history()", lookup)
        self.assertIn("repair_missing_chrome_order_chatter()", lookup)
        self.assertIn('"submitted_reconciled": submitted_reconciled', lookup)
        self.assertIn('"chatter_repaired": chatter_repaired', lookup)

        self.assertIn("def reconcile_exact_submitted_chrome_jobs_from_history(", APP)
        self.assertIn("completion = complete_chrome_job_from_exact_history_match(job, existing, worker_id)", APP)
        self.assertIn("def repair_missing_chrome_order_chatter(", APP)
        self.assertIn("queued = queue_chrome_complete_odoo_chatter(", APP)

    def test_start_and_submitted_recovery_discard_a_closed_popup_target_window(self) -> None:
        self.assertIn("async function existingChromeWindowId(windowId)", BACKGROUND)
        self.assertIn("await chrome.windows.get(normalized);", BACKGROUND)

        start_begin = BACKGROUND.index("async function startNextJob(sourceWindowId = null)")
        start_end = BACKGROUND.index("async function startBrowserlessOrderRun", start_begin)
        start = BACKGROUND[start_begin:start_end]
        self.assertIn("sourceWindowId = await existingChromeWindowId(sourceWindowId);", start)

        recover_begin = BACKGROUND.index("async function recoverSubmittedJobInWindow(windowId)")
        recover_end = BACKGROUND.index("async function cleanupCartBeforeNextJob", recover_begin)
        recover = BACKGROUND[recover_begin:recover_end]
        self.assertIn("windowId = await existingChromeWindowId(windowId);", recover)

        claim_begin = BACKGROUND.index("async function claimNextJobInWindow(windowId)")
        claim_end = BACKGROUND.index("async function finishCleanupAndClaimNext", claim_begin)
        claim = BACKGROUND[claim_begin:claim_end]
        self.assertIn("windowId = await existingChromeWindowId(windowId);", claim)
        self.assertIn("const createdWindow = await createAmazonWorkerWindow(false);", claim)
        self.assertIn("Could not create a replacement Amazon worker window", claim)
        self.assertLess(
            claim.index("const createdWindow = await createAmazonWorkerWindow(false);"),
            claim.index("const activeJob = activeJobFor(job, workerId, windowId);"),
        )

    def test_empty_order_history_candidate_cannot_complete_reporting(self) -> None:
        report_start = CONTENT.index("async function reportAmazonOrders(activeJob, orders)")
        unsafe_start = CONTENT.index("const unsafeOrders", report_start)
        report_guard = CONTENT[report_start:unsafe_start]
        self.assertIn("if (!uniqueOrders.length)", report_guard)
        self.assertIn('activeJob.stage = "find_order_id";', report_guard)
        self.assertIn('activeJob.reportedOrderId = "";', report_guard)
        self.assertIn('return false;', report_guard)

    def test_reload_repairs_reporting_complete_without_order_id(self) -> None:
        self.assertIn("function activeJobHasReportedOrderId(activeJob)", BACKGROUND)
        self.assertIn('activeJob.stage === "reporting_complete" && !activeJobHasReportedOrderId(activeJob)', BACKGROUND)
        self.assertIn('activeJob.stage = "find_order_id";', BACKGROUND)

    def test_background_rejects_completion_without_valid_amazon_order_id(self) -> None:
        complete_start = BACKGROUND.index("async function completeJob(orderId")
        fail_start = BACKGROUND.index("async function failJob", complete_start)
        complete = BACKGROUND[complete_start:fail_start]
        guard = complete.index("if (!/^\\d{3}-\\d{7}-\\d{7}$/.test(normalizedOrderId))")
        api_call = complete.index("/api/chrome/jobs/${encodeURIComponent(groupKey)}/complete")
        self.assertLess(guard, api_call)
        self.assertIn('invalid_order_id: true', complete)
        self.assertIn('activeJob.stage = "find_order_id";', complete)

    def test_submitted_history_timeout_holds_queue_instead_of_failing_job(self) -> None:
        history_start = CONTENT.index("async function handleOrderHistory(activeJob)")
        history_end = CONTENT.index("async function reportPostSubmitUnplaced", history_start)
        history = CONTENT[history_start:history_end]
        self.assertNotIn('type: "SUBMIT_UNCERTAIN"', history)
        self.assertIn('activeJob.pausedStage = "find_order_id";', history)
        self.assertIn("the queue will not continue", history)
        self.assertIn("emptyReloads < 1", history)

    def test_background_uncertain_submit_never_releases_or_cleans_queue(self) -> None:
        uncertain_start = BACKGROUND.index("async function submitUncertain")
        uncertain_end = BACKGROUND.index("async function markLineMissing", uncertain_start)
        uncertain = BACKGROUND[uncertain_start:uncertain_end]
        self.assertNotIn("/submit-uncertain", uncertain)
        self.assertNotIn("cleanupCartBeforeNextJob", uncertain)
        self.assertIn("held_for_verification: true", uncertain)

    def test_completion_requires_durable_odoo_chatter_queue(self) -> None:
        complete_start = BACKGROUND.index("async function completeJob(orderId")
        fail_start = BACKGROUND.index("async function failJob", complete_start)
        complete = BACKGROUND[complete_start:fail_start]
        self.assertIn("result?.odoo_chatter_queued !== true && result?.odoo_chatter_confirmed !== true", complete)
        self.assertIn("pending_odoo_chatter_queue: true", complete)

    def test_server_rejects_blank_or_synthetic_chrome_order_ids(self) -> None:
        complete_start = APP.index("def api_chrome_job_complete(group_key")
        complete_end = APP.index('@app.post("/api/chrome/jobs/{group_key}/fail")', complete_start)
        complete = APP[complete_start:complete_end]
        self.assertIn('if not re.fullmatch(r"\\d{3}-\\d{7}-\\d{7}", amazon_order_id):', complete)
        self.assertNotIn('f"CHROME-{uuid.uuid4()', complete)

    def test_server_uncertain_submit_keeps_state_and_claim(self) -> None:
        uncertain_start = APP.index("def api_chrome_job_submit_uncertain")
        uncertain_end = APP.index('@app.post("/api/chrome/jobs/{group_key}/preflight-missing")', uncertain_start)
        uncertain = APP[uncertain_start:uncertain_end]
        self.assertNotIn("SET state='error'", uncertain)
        self.assertNotIn("chrome_claimed_by=NULL", uncertain)
        self.assertIn('"held_for_verification": True', uncertain)

    def test_server_queues_odoo_chatter_before_completion_response(self) -> None:
        complete_start = APP.index("def api_chrome_job_complete(group_key")
        complete_end = APP.index('@app.post("/api/chrome/jobs/{group_key}/fail")', complete_start)
        complete = APP[complete_start:complete_end]
        self.assertIn("queue_chrome_complete_odoo_chatter(", complete)
        self.assertIn('"odoo_chatter_queued": True', complete)

    def test_odoo_chatter_outbox_is_idempotent_and_retries(self) -> None:
        self.assertIn("def enqueue_odoo_chatter_note(", APP)
        self.assertIn("UNIQUE(store_id, odoo_order_id, event_type, amazon_order_id)", APP)
        self.assertIn("ON CONFLICT(store_id, odoo_order_id, event_type, amazon_order_id)", APP)
        self.assertIn("def odoo_order_note_already_present(", APP)
        self.assertIn('("body", "ilike", marker)', APP)
        self.assertIn("require_remote_verification=int(row.get(\"attempt_count\") or 0) > 0", APP)
        self.assertIn("retry held to prevent a duplicate", APP)
        self.assertIn("def odoo_chatter_outbox_schedule_loop()", APP)
        self.assertIn("delay_seconds = min(1800", APP)

    def test_completion_recovery_waits_for_saved_data_and_queued_chatter_not_remote_odoo(self) -> None:
        matcher_start = BACKGROUND.index("function completionStatusMatchesOrder")
        matcher_end = BACKGROUND.index("async function clearJobIfBackendCompleted", matcher_start)
        matcher = BACKGROUND[matcher_start:matcher_end]
        self.assertIn("status?.odoo_chatter_queued === true || status?.odoo_chatter_confirmed === true", matcher)
        self.assertIn("status?.data_saved === true", matcher)
        self.assertIn("status?.completed === true && status?.odoo_chatter_confirmed === true", matcher)

    def test_reload_recovery_requires_a_live_content_script(self) -> None:
        self.assertIn('type: "NUTRICITY_CONTENT_PING"', BACKGROUND)
        self.assertIn('response?.build === EXPECTED_CONTENT_SCRIPT_BUILD', BACKGROUND)
        self.assertIn('if (message.type === "NUTRICITY_CONTENT_PING")', CONTENT)
        self.assertIn('sendResponse({ ok: true, build: CONTENT_SCRIPT_BUILD })', CONTENT)
        self.assertIn('await clearStaleContentScriptMarker(tabId);', BACKGROUND)

    def test_stale_address_pause_reaches_checkout_safety_guards(self) -> None:
        resume_start = CONTENT.index("async function autoResumeResolvedCheckoutPause")
        resume_end = CONTENT.index("async function run()", resume_start)
        resume = CONTENT[resume_start:resume_end]
        self.assertIn("placeOrder &&", resume)
        self.assertIn("!findAddressNameInput()", resume)
        self.assertNotIn("checkoutRecipientConfirmed(checkoutRecipient) &&", resume)
        self.assertNotIn("checkoutPaymentConfirmed(cardPreferenceList", resume)
        checkout_start = CONTENT.index("async function handleCheckout(activeJob)")
        checkout_end = CONTENT.index("function extractOrderId()", checkout_start)
        checkout = CONTENT[checkout_start:checkout_end]
        self.assertLess(checkout.index("ensureCheckoutOnlyExpectedUnits"), checkout.index("protectBeforeAmazonSubmit"))

    def test_free_next_day_confirmation_requeries_checked_radio(self) -> None:
        selection_start = CONTENT.index("function freeNextDayDeliverySelected()")
        ensure_start = CONTENT.index("async function ensureFreeNextDayDelivery", selection_start)
        reward_start = CONTENT.index("async function ensureRewardedLaterDelivery", ensure_start)
        selection = CONTENT[selection_start:ensure_start]
        ensure = CONTENT[ensure_start:reward_start]
        self.assertIn('input[type=\'radio\']:checked', selection)
        self.assertIn("freeNextDayDeliverySelected()", ensure)
        self.assertIn("waitUntil(freeNextDayDeliverySelected", ensure)
        self.assertNotIn("nextDay.control?.checked", ensure)

    def test_active_job_heartbeat_reports_pauses_every_minute(self) -> None:
        self.assertIn("const ACTIVE_JOB_HEARTBEAT_MS = 60 * 1000;", BACKGROUND)
        self.assertIn("paused: activeJob.paused === true", BACKGROUND)
        self.assertIn('paused_stage: activeJob.pausedStage || ""', BACKGROUND)
        self.assertIn('last_error: activeJob.lastError || activeJob.pauseReason || ""', BACKGROUND)
        self.assertIn("activeJob.lastError = String(error.message", CONTENT)

    def test_cart_parser_supports_current_amazon_markup(self) -> None:
        roots_start = CONTENT.index("function cartActiveRoots()")
        parser_start = CONTENT.index("function cartItemAsin(item)", roots_start)
        diagnostic_start = CONTENT.index("function cartDiagnosticSummary()", parser_start)
        roots = CONTENT[roots_start:parser_start]
        parser = CONTENT[parser_start:diagnostic_start]
        self.assertIn('"#activeCartViewForm"', roots)
        self.assertIn('"[data-name=\'Active Items\']"', roots)
        self.assertIn('"[data-csa-c-content-id*=\'activeCart\' i]"', roots)
        self.assertIn('node.getAttribute("data-csa-c-asin")', parser)
        self.assertIn("gp\\/aw\\/d", parser)
        self.assertIn("product-reviews", parser)

    def test_cart_verification_error_carries_detected_rows(self) -> None:
        self.assertGreaterEqual(CONTENT.count("${cartDiagnosticSummary()}"), 3)

    def test_next_job_is_published_only_after_leaving_order_history(self) -> None:
        claim_start = BACKGROUND.index("async function claimNextJobInWindow")
        claim_end = BACKGROUND.index("async function finishCleanupAndClaimNext", claim_start)
        claim = BACKGROUND[claim_start:claim_end]
        navigate = claim.rindex("await navigateWindowToCart(windowId);")
        publish = claim.rindex("await setWindowJob(windowId, activeJob);")
        wake = claim.rindex("await injectActiveAmazonTabInWindow(windowId);")
        self.assertLess(navigate, publish)
        self.assertLess(publish, wake)

    def test_place_order_protection_is_single_flight(self) -> None:
        self.assertIn("const submitProtectionLocks = new Set();", BACKGROUND)
        wrapper_start = BACKGROUND.index("async function markAmazonSubmitted(windowId")
        unlocked_start = BACKGROUND.index("async function markAmazonSubmittedUnlocked", wrapper_start)
        wrapper = BACKGROUND[wrapper_start:unlocked_start]
        self.assertIn("submitProtectionLocks.has(lockKey)", wrapper)
        self.assertIn("submitProtectionLocks.add(lockKey)", wrapper)
        self.assertIn("submitProtectionLocks.delete(lockKey)", wrapper)

    def test_resume_does_not_start_a_parallel_runner(self) -> None:
        toggle_start = CONTENT.index("async function togglePanelPause")
        toggle_end = CONTENT.index("async function updatePanelPauseButton", toggle_start)
        toggle = CONTENT[toggle_start:toggle_end]
        self.assertIn("if (!window.__nutricityRunning) setTimeout(runSafely, 250);", toggle)

    def test_warehouse_holiday_delivery_uses_brooklyn_timezone(self) -> None:
        self.assertIn('timeZone: "America/New_York"', CONTENT)
        self.assertIn('return ["Fri", "Sat"].includes(brooklynWeekday(now));', CONTENT)
        reward_start = CONTENT.index("async function ensureRewardedLaterDelivery")
        reward_end = CONTENT.index("function checkoutDeliveryPromiseText", reward_start)
        reward = CONTENT[reward_start:reward_end]
        self.assertIn("state.preferRewardedLaterDelivery === true", reward)
        self.assertIn("brooklynNextDayIsWarehouseHoliday()", reward)
        self.assertIn("return ensureFreeNextDayDelivery(activeJob, brooklynDay);", reward)

    def test_non_holiday_checkout_selects_free_next_day(self) -> None:
        next_day_start = CONTENT.index("function freeNextDayDeliveryOption")
        next_day_end = CONTENT.index("async function ensureRewardedLaterDelivery", next_day_start)
        next_day = CONTENT[next_day_start:next_day_end]
        self.assertIn('/\\btomorrow\\b/', next_day)
        self.assertIn('/\\bnext[ -]?day\\b/', next_day)
        self.assertIn('!isAmazonDayDeliveryContext(context)', next_day)

    def test_failure_cleanup_can_leave_order_history(self) -> None:
        self.assertIn('activeJob?.stage === "cleanup_after_failure"', CONTENT)
        self.assertIn('activeJob?.cleanupAfterFailure === true', CONTENT)
        cleanup_guard = CONTENT.index('activeJob?.stage === "cleanup_after_failure"', CONTENT.index("async function guardUnexpectedAmazonPage"))
        unexpected_guard = CONTENT.index('reason: "unexpected_order_history_page"', cleanup_guard)
        self.assertLess(cleanup_guard, unexpected_guard)

    def test_address_list_is_recovered_before_final_recipient_verification(self) -> None:
        self.assertIn("function checkoutAddressSelectionPageOpen()", CONTENT)
        verifier = CONTENT.index("async function verifyCheckoutDeliveryRecipient")
        address_recovery = CONTENT.index("if (checkoutAddressSelectionPageOpen())", verifier)
        missing_recipient_pause = CONTENT.index("if (!deliveredTo)", verifier)
        self.assertLess(address_recovery, missing_recipient_pause)
        self.assertIn("const recipientRow = addressRowForRecipient(checkoutRecipient);", CONTENT[address_recovery:missing_recipient_pause])
        self.assertIn("const editor = await openAddressEditorIfAvailable(activeJob);", CONTENT[address_recovery:missing_recipient_pause])

    def test_recipient_parser_does_not_concatenate_duplicate_accessible_text(self) -> None:
        parser_start = CONTENT.index("function checkoutDeliveryRecipientText()")
        matcher_start = CONTENT.index("function checkoutDeliveryRecipientMatches", parser_start)
        parser = CONTENT[parser_start:matcher_start]
        self.assertIn("const directText = (direct?.innerText || direct?.textContent", parser)
        self.assertIn("const nestedHeadingText = (nestedHeading?.innerText || nestedHeading?.textContent", parser)
        self.assertIn("const variants = [...new Set([", parser)
        self.assertNotIn("const text = elementReadableText(element)", parser)

    def test_subscribe_payment_confirmation_precedes_submit_protection(self) -> None:
        checkout_start = CONTENT.index("async function handleCheckout(activeJob)")
        checkout_end = CONTENT.index("function extractOrderId()", checkout_start)
        checkout = CONTENT[checkout_start:checkout_end]
        confirmation = checkout.index("if (!await ensureSnsPaymentConfirmation(activeJob)) return;")
        protection = checkout.index('protectBeforeAmazonSubmit(activeJob, "checkout")')
        self.assertLess(confirmation, protection)

    def test_blocked_subscribe_submit_reuses_existing_protection(self) -> None:
        recovery_start = CONTENT.index("async function recoverBlockedSnsSubmit(activeJob)")
        recovery_end = CONTENT.index("function isOnePercentDeliveryRewardText", recovery_start)
        recovery = CONTENT[recovery_start:recovery_end]
        self.assertIn("activeJob?.placeOrderClickStartedAt", recovery)
        self.assertIn("!submittedStage(activeJob)", recovery)
        self.assertIn("await ensureCheckoutOnlyExpectedUnits(activeJob)", recovery)
        self.assertIn("await ensureSubscribeCheckoutQuantity(activeJob)", recovery)
        self.assertIn("await checkoutDeliveryWindowIsAllowed(activeJob)", recovery)
        self.assertIn("await clickElement(placeOrder", recovery)
        self.assertNotIn("protectBeforeAmazonSubmit", recovery.replace("// protectBeforeAmazonSubmit", "// protection"))

    def test_order_history_recipient_collapses_amazon_duplicate_text(self) -> None:
        parser_start = CONTENT.index('function recipientFromOrderHistoryText(text = "")')
        parser_end = CONTENT.index("function orderCardRecipient(card)", parser_start)
        parser = CONTENT[parser_start:parser_end]
        self.assertIn("if (value.length % 2 === 0)", parser)
        self.assertIn("value.slice(0, midpoint) === value.slice(midpoint)", parser)
        recipient_start = parser_end
        recipient_end = CONTENT.index("function orderCardDate(card)", recipient_start)
        recipient_parser = CONTENT[recipient_start:recipient_end]
        self.assertLess(
            recipient_parser.index('".shipToTriggerTextTruncate .a-truncate-cut"'),
            recipient_parser.index('".shipToTriggerTextTruncate .a-truncate-full"'),
        )


if __name__ == "__main__":
    unittest.main()
