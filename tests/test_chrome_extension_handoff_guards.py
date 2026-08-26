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
    def test_workers_detect_account_type_before_whole_order_claim(self) -> None:
        self.assertIn('type: "GET_AMAZON_ACCOUNT_EXPERIENCE"', BACKGROUND)
        self.assertIn('if (message.type === "GET_AMAZON_ACCOUNT_EXPERIENCE")', CONTENT)
        self.assertIn('account_experience=${encodeURIComponent(detectedAccount.experience)}', BACKGROUND)
        self.assertIn('split_mixed_asin=false&account_experience=', BACKGROUND)
        self.assertIn('required_account_experience', APP)
        self.assertIn('chrome_account_experience_matches(candidate_rows, account_experience)', APP)

    def test_stateful_extension_api_requests_never_reuse_cached_claims(self) -> None:
        self.assertIn('cache: "no-store"', BACKGROUND)

    def test_auto_ordering_requires_manual_session_confirmation(self) -> None:
        self.assertIn('id="autoOrderingConfirmed"', POPUP_HTML)
        self.assertIn('id="autoOrderingToggle" disabled', POPUP_HTML)
        self.assertIn('"Start Auto Ordering"', POPUP_JS)
        self.assertIn('"Stop Auto Ordering"', POPUP_JS)
        self.assertIn('confirmed: !isRunning && autoOrderingConfirmed.checked', POPUP_JS)
        self.assertIn('const AUTO_ORDER_ALARM = "nutricity-auto-order-queue"', BACKGROUND)
        self.assertIn('chrome.storage.session.get({ autoOrderingRunning: false })', BACKGROUND)
        self.assertIn('setAutoOrderingRunning(false)', BACKGROUND)
        self.assertIn('if (!await autoOrderingIsRunning())', BACKGROUND)
        self.assertIn('if (activeJob && !orderSubmitStarted(activeJob) && !await autoOrderingIsRunning())', BACKGROUND)
        self.assertIn('if (!await autoOrderingIsRunning() && !orderSubmitStarted(activeJob))', BACKGROUND)
        self.assertIn('await activeOrderingInProgress()', BACKGROUND)
        self.assertIn('await startNextJob(null, { automatic: true })', BACKGROUND)
        self.assertNotIn('autoOrderQueue: true', BACKGROUND)
        self.assertNotIn('setupAutoOrderAlarm().catch((error) => log(`Could not schedule automatic ordering', BACKGROUND)

    def test_legacy_multi_asin_split_is_forced_off(self) -> None:
        self.assertIn('splitMixedAsinOrders: false', BACKGROUND)
        self.assertIn('splitMixedAsinOrders: false', POPUP_JS)
        self.assertNotIn('split_mixed_asin=${splitMixedAsinOrders', BACKGROUND)

    def test_consumer_and_business_subscription_cart_flows_stay_separate(self) -> None:
        detector_start = CONTENT.index("function amazonAccountExperience()")
        detector_end = CONTENT.index("function isAmazonBusinessPage()", detector_start)
        detector = CONTENT[detector_start:detector_end]
        self.assertIn('return "business"', detector)
        self.assertIn('return consumerShell ? "consumer" : "unknown"', detector)
        self.assertNotIn("header [aria-label*='Amazon Business' i]", detector)
        self.assertNotIn("#navbar [aria-label*='Amazon Business' i]", detector)
        self.assertIn("#nav-logo a[aria-label*='Amazon Business' i]", detector)

        product_start = CONTENT.index("async function handleProduct(activeJob)")
        product_end = CONTENT.index("async function handleSubscribeCheckout", product_start)
        product = CONTENT[product_start:product_end]
        self.assertIn('accountExperience === "consumer"', product)
        self.assertIn('accountExperience === "business"', product)
        self.assertIn("await waitUntil(findSubscribeAddToCartTarget, 15000, 250)", product)
        self.assertIn("Fulfilment paused without switching to One-time purchase", product)
        self.assertIn("Business multi-ASIN orders must stay in the normal Amazon cart", product)
        self.assertNotIn('accountExperience !== "consumer"', product)
        self.assertNotIn("mixedSnsOneTimeFallbackAsins", product)
        self.assertNotIn("persist_consumer_mixed_sns_one_time_fallback", product)

        resolver_start = CONTENT.index("async function resolvedAmazonAccountExperience")
        resolver_end = CONTENT.index("function isAmazonBusinessPage()", resolver_start)
        resolver = CONTENT[resolver_start:resolver_end]
        self.assertIn('accountExperience === "unknown"', resolver)
        self.assertIn("await waitUntil(() =>", resolver)
        self.assertIn("paused instead of treating this account as Business", resolver)

    def test_subscribe_activation_uses_one_native_click_per_target(self) -> None:
        start = CONTENT.index("async function activateSubscribeAndSaveOption()")
        end = CONTENT.index("function findOneTimePurchaseAccordionTarget", start)
        activation = CONTENT[start:end]
        self.assertIn('clickElement(clickTarget, "Subscribe & Save accordion row"', activation)
        self.assertNotIn("dispatchAmazonClickSequence(clickTarget)", activation)
        self.assertNotIn("dispatchClickAtElementCenter(clickTarget)", activation)
        self.assertNotIn("KeyboardEvent", activation)

    def test_consumer_sns_quantity_supports_amazons_rcx_select(self) -> None:
        accepted_start = CONTENT.index("function quantityValueAccepted(")
        accepted_end = CONTENT.index("function syncSubscribeAndSaveQuantity(", accepted_start)
        accepted = CONTENT[accepted_start:accepted_end]
        self.assertIn("select#rcxsubsQuan", accepted)
        self.assertIn("findSubscribeAddToCartTarget()", accepted)

        sync_start = accepted_end
        sync_end = CONTENT.index("async function clickQuantityUpdateButton", sync_start)
        sync = CONTENT[sync_start:sync_end]
        self.assertIn("select#rcxsubsQuan", sync)
        self.assertIn("select[name='rcxsubsQuan']", sync)

        setter_start = CONTENT.index("async function setQuantity(")
        setter_end = CONTENT.index("async function navigateToNext", setter_start)
        setter = CONTENT[setter_start:setter_end]
        self.assertIn('"#snsAccordionRowMiddle select#rcxsubsQuan"', setter)

    def test_available_amazon_day_weekday_beats_earlier_default(self) -> None:
        helper_start = CONTENT.index("function preferredAmazonDayWeekdayOption()")
        helper_end = CONTENT.index("async function ensureWarehouseOpenDayDelivery", helper_start)
        helper = CONTENT[helper_start:helper_end]
        self.assertIn("isAmazonDayDeliveryContext(context)", helper)
        self.assertIn("!deliveryContextIsNotConsolidated(context)", helper)
        self.assertIn("deliveryContextNamesWarehouseOpenDay(context)", helper)
        reward_start = CONTENT.index("async function ensureRewardedLaterDelivery")
        reward_end = CONTENT.index("function checkoutDeliveryPromiseText", reward_start)
        reward = CONTENT[reward_start:reward_end]
        self.assertIn("ensureWarehouseOpenDayDelivery(activeJob)", reward)
        self.assertLess(
            reward.index("ensureWarehouseOpenDayDelivery(activeJob)"),
            reward.index("ensureFreeNextDayDelivery(activeJob, brooklynDay)"),
        )

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

    def test_business_bundle_evidence_reaches_completion_api(self) -> None:
        self.assertIn("function businessBundleCompletionEvidence(activeJob, orders)", CONTENT)
        self.assertIn("businessBundleExpansion: bundleExpansionEvidence", CONTENT)
        self.assertIn("message.businessBundleExpansion || null", BACKGROUND)
        self.assertIn("business_bundle_expansion: businessBundleExpansion || {}", BACKGROUND)

    def test_failed_completion_is_not_rendered_as_reported(self) -> None:
        self.assertIn("function showReportingCompleteStatus(activeJob)", CONTENT)
        self.assertIn("Nutricity reporting needs attention", CONTENT)
        self.assertIn("activeJob?.paused && activeJob?.reportError", CONTENT)

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
        self.assertEqual(MANIFEST["version"], "0.1.146")

    def test_missing_asins_are_reserved_for_one_check_every_48_hours(self) -> None:
        self.assertIn("const MISSING_ASIN_CHECK_PERIOD_MINUTES = 60", BACKGROUND)
        self.assertIn("MISSING_ASIN_RECHECK_HOURS = 48", APP)
        candidate_start = APP.index("def missing_asin_check_candidates")
        candidate_end = APP.index("def auto_queue_ready_missing_order", candidate_start)
        candidates = APP[candidate_start:candidate_end]
        self.assertIn("availability.last_checked_at <= ?", candidates)
        self.assertIn("'checking'", candidates)
        self.assertIn("_MISSING_ASIN_CHECK_CLAIM_LOCK", candidates)
        self.assertIn('row.get("replacement_asin") or row.get("missing_asin")', candidates)

    def test_ready_missing_lines_requeue_as_one_whole_odoo_order(self) -> None:
        helper_start = APP.index("def auto_queue_ready_missing_order")
        helper_end = APP.index("def auto_queue_all_ready_missing_orders", helper_start)
        helper = APP[helper_start:helper_end]
        self.assertIn("Waiting for the remaining missing ASIN", helper)
        self.assertIn("line_ids = [int(row[\"id\"]) for row in rows]", helper)
        self.assertIn("include_missing_asins=True", helper)
        report_start = APP.index("def api_chrome_missing_asin_report")
        report_end = APP.index("def api_partial_fulfilments", report_start)
        report = APP[report_start:report_end]
        self.assertIn("auto_queue_ready_missing_order(store_id, odoo_order_id)", report)
        self.assertIn('"requires_approval": False', report)

    def test_replacement_assignment_queues_when_auto_ordering_is_enabled(self) -> None:
        assign_start = APP.index("def api_assign_replacement")
        assign_end = APP.index("def original_product_name_for_line", assign_start)
        assign = APP[assign_start:assign_end]
        self.assertIn("if auto_chrome_ordering_enabled()", assign)
        self.assertIn("auto_queue_ready_missing_order", assign)

    def test_delivery_options_click_the_native_radio_before_the_label(self) -> None:
        helper_start = CONTENT.index("async function clickDeliveryRadioContext(context, label)")
        helper_end = CONTENT.index("function checkoutOffersOnePercentDeliveryReward", helper_start)
        helper = CONTENT[helper_start:helper_end]
        self.assertIn("const nativeRadio = currentDeliveryRadio(context);", helper)
        self.assertIn("await clickElement(nativeRadio, label", helper)
        self.assertIn("deliveryRadioSelectionMatches(context)", helper)
        self.assertLess(helper.index("clickElement(nativeRadio"), helper.index("clickElement(fallback"))
        self.assertNotIn('clickElement(option.control, "consolidated Amazon Day weekday delivery option")', CONTENT)
        self.assertNotIn('clickElement(weekdayOption.control, "consolidated Monday-Friday delivery option")', CONTENT)
        self.assertIn('clickDeliveryRadioContext(option, "consolidated Amazon Day weekday delivery option")', CONTENT)
        self.assertIn('clickDeliveryRadioContext(weekdayOption, "consolidated Monday-Friday delivery option")', CONTENT)
        self.assertIn('clickDeliveryRadioContext(nextDay, "free next-day delivery option")', CONTENT)
        self.assertIn('clickDeliveryRadioContext(rewardOption, "later delivery option with 1% reward")', CONTENT)

    def test_only_the_designated_amazon_tab_can_run_a_job(self) -> None:
        self.assertIn("activeJob.targetTabId = targetTabId", BACKGROUND)
        self.assertIn('type: "NUTRICITY_DISABLE_NON_WORKER"', BACKGROUND)
        self.assertIn('message.type === "NUTRICITY_DISABLE_NON_WORKER"', CONTENT)
        self.assertIn("ignored_non_worker_tab: true", BACKGROUND)
        self.assertIn("inactiveWorkerTab: true", BACKGROUND)
        updated_start = BACKGROUND.index("chrome.tabs.onUpdated.addListener")
        updated_end = BACKGROUND.index("chrome.windows.onRemoved.addListener", updated_start)
        updated = BACKGROUND[updated_start:updated_end]
        self.assertIn("activeJob.targetTabId", updated)
        self.assertIn("Number(activeJob.targetTabId) !== Number(tabId)", updated)

    def test_force_stop_aborts_an_inflight_page_action(self) -> None:
        wait_start = CONTENT.index("async function waitIfPaused()")
        wait_end = CONTENT.index("async function waitForPageReady", wait_start)
        self.assertIn("if (fulfilmentForceStopped) throw fulfilmentPausedError();", CONTENT[wait_start:wait_end])
        self.assertIn("function activateContentAutomation()", CONTENT)
        run_message = CONTENT.index('if (message.type === "RUN_ACTIVE_JOB")')
        self.assertIn("activateContentAutomation();", CONTENT[run_message:run_message + 180])

    def test_checkout_enforces_warehouse_delivery_hours_before_delivery_selection(self) -> None:
        self.assertIn('"MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"', CONTENT)
        self.assertIn('const WAREHOUSE_DELIVERY_WEEKENDS = ["SATURDAY", "SUNDAY"]', CONTENT)
        self.assertIn('const WAREHOUSE_DELIVERY_START = "08:00"', CONTENT)
        self.assertIn('const WAREHOUSE_DELIVERY_END = "16:00"', CONTENT)
        preferences_start = CONTENT.index("async function ensureWarehouseDeliveryPreferences(activeJob, retryAttempt = 0)")
        preferences_end = CONTENT.index("async function ensurePreferredAmazonDayWeekdayDelivery", preferences_start)
        preferences = CONTENT[preferences_start:preferences_end]
        self.assertIn('"#edit-delivery-preferences-link, a"', CONTENT)
        self.assertIn('querySelector("#deliveryTimesEditLink")', preferences)
        self.assertIn('querySelector("#businessHoursExpandLink")', preferences)
        self.assertIn("setWarehouseDeliveryClosed(closed, true)", preferences)
        self.assertIn("warehouseDeliveryControlsMatch(dialog)", preferences)
        self.assertIn("adpSubmitButton_", preferences)
        self.assertIn("verifyWarehouseCompactSavedPreferences(dialog)", preferences)
        self.assertIn("candidate.querySelector(\"[aria-busy='true']\")", preferences)
        self.assertIn("}, 30000, 250)", preferences)

    def test_checkout_enforces_complete_delivery_instructions(self) -> None:
        self.assertIn('const WAREHOUSE_DELIVERY_DROP_OFF = "front door";', CONTENT)
        self.assertIn('BUSINESS NAME: OUTSIDE THE BOX SHIPPING\\nYou can drop the package at the front door and i\'ll be here to pick up.', CONTENT)
        self.assertIn('"independence day"', CONTENT)
        self.assertIn('"christmas day"', CONTENT)
        preferences_start = CONTENT.index("async function ensureWarehouseDeliveryPreferences(activeJob, retryAttempt = 0)")
        preferences_end = CONTENT.index("async function ensurePreferredAmazonDayWeekdayDelivery", preferences_start)
        preferences = CONTENT[preferences_start:preferences_end]
        self.assertIn("setWarehouseDeliveryInstructions(dialog)", preferences)
        self.assertIn("clearWarehouseObservedHolidays(dialog)", preferences)
        self.assertIn("warehouseCompleteDeliveryPreferencesMatch(dialog)", preferences)
        self.assertLess(preferences.index("setWarehouseDeliveryInstructions(dialog)"), preferences.index("adpSubmitButton_"))
        self.assertLess(preferences.index("clearWarehouseObservedHolidays(dialog)"), preferences.index("adpSubmitButton_"))

    def test_consumer_checkout_uses_its_business_delivery_instruction_modal(self) -> None:
        helper_start = CONTENT.index("async function ensureConsumerWarehouseDeliveryPreferences")
        helper_end = CONTENT.index("async function ensureWarehouseDeliveryPreferences", helper_start)
        helper = CONTENT[helper_start:helper_end]

        self.assertIn('"add delivery instructions"', CONTENT)
        self.assertIn('"edit delivery instructions"', CONTENT)
        self.assertIn("#ma-business-type-button-input", CONTENT)
        self.assertIn("business_hours_${kind}_${day}_", CONTENT)
        self.assertIn('consumerBusinessTimeControl(dialog, "start", "weekday")', CONTENT)
        self.assertIn('consumerBusinessFlagControl(dialog, "closed", "weekend")', CONTENT)
        self.assertIn("#exceptionDatesOpen-announce", CONTENT)
        self.assertIn("preferredDeliveryLocationBUSINESS", CONTENT)
        self.assertIn("#freeTextInstruction-BUSINESS", CONTENT)
        self.assertIn("prepareConsumerBusinessDeliveryControls(dialog)", helper)
        self.assertIn("revealConsumerBusinessInstructionSections(dialog)", CONTENT)
        self.assertIn('=== "add more instructions"', CONTENT)
        self.assertIn("exposeConsumerBusinessControls(dialog, hoursLabel", CONTENT)
        self.assertIn("exposeConsumerBusinessControls(dialog, locationLabel", CONTENT)
        self.assertIn("exposeConsumerBusinessControls(dialog, securityLabel", CONTENT)
        self.assertIn("exposeConsumerBusinessControls(dialog, additionalLabel", CONTENT)
        accordion_start = CONTENT.index("function consumerBusinessAccordion")
        accordion_end = CONTENT.index("async function expandConsumerBusinessAccordion", accordion_start)
        accordion = CONTENT[accordion_start:accordion_end]
        self.assertIn('querySelectorAll("a, button")', accordion)
        self.assertIn("visible(control)", accordion)
        self.assertIn('"Business property type", { preClickDelayMs: 0, delayMs: CONSUMER_DELIVERY_INSTRUCTION_SETTLE_MS }', helper)
        self.assertIn("find(visible)", CONTENT)
        self.assertIn("const CONSUMER_DELIVERY_INSTRUCTION_SETTLE_MS = 1000;", CONTENT)
        self.assertIn('"Add or edit consumer delivery instructions", { delayMs: CONSUMER_DELIVERY_INSTRUCTION_SETTLE_MS }', helper)
        self.assertIn('"Save consumer delivery instructions", { preClickDelayMs: 100, delayMs: CONSUMER_DELIVERY_INSTRUCTION_SETTLE_MS }', helper)
        self.assertIn('setWarehouseDeliverySelect(controls.weekdayStart, "8:00")', helper)
        self.assertIn("consumerBusinessDeliveryPreferencesMatch(dialog)", helper)
        self.assertIn('"save instructions"', helper)
        self.assertIn("const save = await waitUntil(() =>", helper)
        self.assertIn('findButtonByText(["save instructions"])', helper)
        self.assertIn("candidate && !candidate.disabled", helper)
        self.assertIn("}, 10000, 150);", helper)
        save_click = helper.index('clickElement(save, "Save consumer delivery instructions"')
        reopen = helper.index("const refreshedTrigger = await waitUntil(checkoutDeliveryPreferencesTrigger", save_click)
        save_confirmation = helper[save_click:reopen]
        self.assertIn("await closeDeliveryPreferencesDialog(visibleDeliveryPreferencesDialog());", save_confirmation)
        self.assertIn("if (!await waitUntil(() => !visibleDeliveryPreferencesDialog(), 5000, 200))", save_confirmation)
        self.assertIn("Amazon kept the consumer delivery-instructions dialog open after saving", save_confirmation)
        self.assertIn("consumerBusinessDeliverySavedSummaryMatches(candidate)", save_confirmation)
        self.assertIn('saveOutcome === "verified_summary"', save_confirmation)
        self.assertIn("confirmation summary", save_confirmation)
        summary_start = CONTENT.index("function consumerBusinessDeliverySavedSummaryMatches")
        summary_end = CONTENT.index("async function ensureConsumerWarehouseDeliveryPreferences", summary_start)
        summary = CONTENT[summary_start:summary_end]
        self.assertIn('text.includes("delivery instructions saved")', summary)
        self.assertIn("property type", summary)
        self.assertIn("monday", summary)
        self.assertIn("saturday", summary)
        self.assertIn("holidays", summary)
        self.assertIn('text.includes("front door")', summary)
        self.assertIn('text.includes("business name: outside the box shipping")', summary)
        normalizer_start = CONTENT.index("function normalizedDeliveryInstructionValue")
        normalizer_end = CONTENT.index("function warehouseDeliveryInstructionsMatch", normalizer_start)
        self.assertIn("&#0*39;|&apos;", CONTENT[normalizer_start:normalizer_end])
        self.assertIn("return ensureConsumerWarehouseDeliveryPreferences(activeJob, nextTrigger, nextAttempt);", helper)

    def test_business_delivery_preferences_use_one_loading_and_save_pass(self) -> None:
        preferences_start = CONTENT.index("async function ensureWarehouseDeliveryPreferences(activeJob, retryAttempt = 0)")
        preferences_end = CONTENT.index("async function ensurePreferredAmazonDayWeekdayDelivery", preferences_start)
        preferences = CONTENT[preferences_start:preferences_end]

        self.assertIn("remained busy and did not finish loading", preferences)
        self.assertIn("Verified already-saved checkout delivery preferences without resaving them", preferences)
        self.assertIn("Saved checkout delivery preferences in one pass", preferences)
        self.assertNotIn("Recheck delivery preferences", preferences)
        self.assertNotIn("Retrying checkout delivery preferences automatically", preferences)
        self.assertNotIn("return ensureWarehouseDeliveryPreferences(activeJob, nextAttempt);", preferences)

    def test_delivery_preferences_fall_back_to_all_seven_saved_controls(self) -> None:
        helper_start = CONTENT.index("async function verifyWarehouseCompactSavedPreferences")
        helper_end = CONTENT.index("function setWarehouseDeliverySelect", helper_start)
        helper = CONTENT[helper_start:helper_end]
        self.assertIn("const compactSummaryVerified", helper)
        self.assertIn("deliveryPreferencesSummaryIsWarehouseSchedule(candidate)", helper)
        self.assertIn("const instructionsVerified", helper)
        self.assertIn("const holidaysVerified", helper)
        self.assertIn("if (instructionsVerified && holidaysVerified) return true;", helper)
        self.assertIn('querySelector("#deliveryTimesEditLink")', helper)
        self.assertIn('querySelector("#businessHoursExpandLink")', helper)
        self.assertIn("warehouseCompleteDeliveryPreferencesMatch(candidate) ? candidate : null", helper)
        self.assertIn("}, 10000, 200)", helper)
        self.assertIn("warehouseCompleteDeliveryPreferencesMatch(currentDialog)", helper)
        self.assertIn('expandDeliveryPreferenceSection(currentDialog, "Delivery Instructions"', helper)
        self.assertIn('expandDeliveryPreferenceSection(currentDialog, "Observed Holidays"', helper)

        checkout_start = CONTENT.index("async function handleCheckout(activeJob)")
        checkout_end = CONTENT.index("function extractOrderId()", checkout_start)
        checkout = CONTENT[checkout_start:checkout_end]
        preferences_guard = checkout.index("ensureWarehouseDeliveryPreferences(activeJob)")
        delivery_selection = checkout.index("ensureRewardedLaterDelivery(activeJob)")
        submit_protection = checkout.index('protectBeforeAmazonSubmit(activeJob, "checkout")')
        self.assertLess(preferences_guard, delivery_selection)
        self.assertLess(preferences_guard, submit_protection)

    def test_force_stop_is_silent_and_stops_all_page_work(self) -> None:
        stop_start = CONTENT.index("function stopContentAutomation(")
        stop_end = CONTENT.index("function ensureOrderHistoryAnnotationLoop()", stop_start)
        stop = CONTENT[stop_start:stop_end]

        self.assertNotIn("showPanel(", stop)
        self.assertNotIn("ensureOrderHistoryAnnotationLoop();", stop)
        self.assertNotIn("scheduleOrderHistoryAnnotation(0);", stop)
        self.assertIn('document.querySelector("#nutricity-panel")?.remove();', stop)
        self.assertIn("clearInterval(historyIntervalId)", stop)
        self.assertIn("clearTimeout(orderHistoryScrollTimer)", stop)
        self.assertIn("clearTimeout(orderHistoryAnnotationTimer)", stop)

    def test_force_stop_blocks_order_history_dom_work(self) -> None:
        annotate_start = CONTENT.index("async function annotateAmazonOrderHistory()")
        annotate_end = CONTENT.index("function scheduleOrderHistoryAnnotation", annotate_start)
        annotate = CONTENT[annotate_start:annotate_end]
        schedule_start = annotate_end
        schedule_end = CONTENT.index("function activeJobOrderNames", schedule_start)
        schedule = CONTENT[schedule_start:schedule_end]

        self.assertIn("if (fulfilmentForceStopped", annotate)
        self.assertIn("if (fulfilmentForceStopped || orderHistoryAnnotationScheduled) return;", schedule)
        self.assertIn("if (fulfilmentForceStopped) return;", schedule)

    def test_subscription_frequency_supports_business_and_consumer_doms(self) -> None:
        choose_start = CONTENT.index("async function chooseSubscribeFrequencySixMonths()")
        choose_end = CONTENT.index("function findSixMonthSubscribeFrequencyPopoverOption", choose_start)
        choose = CONTENT[choose_start:choose_end]
        confirm_start = CONTENT.index("function subscribeFrequencyIsSixMonths()")
        confirm_end = CONTENT.index("async function selectNativeSubscribeFrequency", confirm_start)
        confirm = CONTENT[confirm_start:confirm_end]
        self.assertIn("#replenishment-onml-frequency-trigger", choose)
        self.assertIn("nativeFrequencies.find(visible)", choose)
        self.assertIn("findSixMonthBusinessFrequencyOption", choose)
        self.assertIn('select#rcxOrdFreqSns, select#rcxOrdFreqOnml', confirm)
        self.assertIn('/^6M\\|(?:sns|onml)$/i.test(String(select.value || ""))', confirm)
        self.assertNotIn('document.querySelector("#rcxOrdFreqSns")', confirm)

    def test_payment_selection_uses_exact_card_and_native_continue_control(self) -> None:
        self.assertIn("function paymentRadioForDigits(digits)", CONTENT)
        self.assertIn("document.querySelector(`label[for='${CSS.escape(radio.id)}']`)", CONTENT)
        self.assertIn("function nativePaymentContinueControl(element)", CONTENT)
        self.assertIn("candidates.map(nativePaymentContinueControl)", CONTENT)
        self.assertIn("const continueButton = nativePaymentContinueControl(payment?.continueButton);", CONTENT)
        self.assertNotIn("radio.checked = true", CONTENT)

    def test_business_payment_rejects_rewards_instrument_and_requires_stable_native_card(self) -> None:
        self.assertIn("function businessCardPaymentRadio(preferences = [])", CONTENT)
        self.assertIn("cardDigitsForPaymentRadio(radio)", CONTENT)
        self.assertIn("function businessCardIsNativePaymentInstrument(digits)", CONTENT)
        self.assertIn("selectedNativePaymentInstrumentRadio()", CONTENT)
        self.assertIn("async function waitForStableBusinessCardSelection", CONTENT)
        self.assertIn("await selectStableBusinessPaymentCard(payment.radio)", CONTENT)
        self.assertIn('accountExperience === "business"', CONTENT)

    def test_business_payment_uses_primary_continue_and_consumer_keeps_generic_path(self) -> None:
        business_start = CONTENT.index("function findBusinessPaymentSelection(preferences = [])")
        business_end = CONTENT.index("function alternatePaymentContinueButtons", business_start)
        business = CONTENT[business_start:business_end]
        self.assertIn("primary-continue-payselect", business)
        payment_start = CONTENT.index("async function handlePaymentSelection(activeJob)")
        payment_end = CONTENT.index("async function openPaymentSelectionIfAvailable", payment_start)
        payment = CONTENT[payment_start:payment_end]
        self.assertIn('accountExperience === "business"', payment)
        self.assertIn('accountExperience === "consumer"', payment)
        self.assertIn("findBusinessPaymentSelection(cardPreferences)", payment)
        self.assertIn("findPaymentSelection(cardPreferences)", payment)

    def test_business_payment_does_not_accept_card_text_while_still_on_pay_page(self) -> None:
        self.assertIn("function businessPaymentSelectionPageOpen()", CONTENT)
        self.assertIn("async function waitForBusinessPaymentTransition", CONTENT)
        self.assertIn("Boolean(progress?.transitioned)", CONTENT)

    def test_payment_selection_must_remain_checked_after_amazon_rerender(self) -> None:
        self.assertIn("const clicked = await clickPaymentRadio(payment.radio);", CONTENT)
        self.assertIn("const stableSelection = clicked && await waitUntil", CONTENT)
        self.assertIn("const current = paymentRadioForDigits(selectedDigits);", CONTENT)
        self.assertIn("Amazon changed the payment form before the selected card could be confirmed.", CONTENT)

    def test_payment_controls_get_a_slow_render_readiness_window(self) -> None:
        self.assertIn("Waiting for Amazon to finish loading the payment controls.", CONTENT)
        self.assertIn("waitUntil(findAccountPaymentSelection, 12000, 200)", CONTENT)

    def test_textless_native_payment_continue_is_not_discarded(self) -> None:
        selection_start = CONTENT.index("function findPaymentSelection(preferences = [])")
        selection_end = CONTENT.index("function alternatePaymentContinueButtons", selection_start)
        selection = CONTENT[selection_start:selection_end]
        self.assertIn("const continueButton = visiblePaymentContinueButtons()[0];", selection)
        self.assertIn("return { radio, continueButton }", selection)
        self.assertNotIn("textContinue", selection)

    def test_final_review_waits_for_preferred_card_summary_evidence(self) -> None:
        ensure_start = CONTENT.index("async function ensurePreferredCheckoutPayment(activeJob)")
        ensure_end = CONTENT.index("async function openAddressEditorIfAvailable", ensure_start)
        ensure = CONTENT[ensure_start:ensure_end]
        self.assertGreaterEqual(
            ensure.count("await waitForPreferredCheckoutPayment(cardPreferences, 10000)"),
            3,
        )
        self.assertNotIn("waitForCheckoutPaymentProgress(cardPreferences, 1800)", ensure)
        self.assertNotIn("waitForCheckoutPaymentProgress(cardPreferences, 2200)", ensure)

    def test_resume_injects_current_build_and_wakes_worker(self) -> None:
        toggle_start = BACKGROUND.index("async function togglePause(windowId)")
        toggle_end = BACKGROUND.index("async function completeJob", toggle_start)
        toggle = BACKGROUND[toggle_start:toggle_end]
        self.assertIn("if (!nextPaused)", toggle)
        self.assertIn("await injectActiveAmazonTabInWindow(windowId);", toggle)

    def test_order_history_waits_without_reloading_incomplete_cards(self):
        start = CONTENT.index("async function handleOrderHistory(activeJob)")
        end = CONTENT.index("async function reportPostSubmitUnplaced", start)
        handler = CONTENT[start:end]

        self.assertNotIn("location.reload()", handler)
        self.assertNotIn("orderHistoryEmptyReloads", handler)
        self.assertNotIn("if (!historySurface)", handler)
        self.assertIn("await waitUntil(() => extractOrderHistoryOrders().length > 0", handler)

    def test_popup_exposes_the_loaded_extension_version(self) -> None:
        self.assertIn('id="extensionVersion"', POPUP_HTML)
        self.assertIn("chrome.runtime.getManifest().version", POPUP_JS)

    def test_popup_start_button_turns_into_stop_and_greys_out_without_tick(self) -> None:
        self.assertIn('const autoOrderingToggle = document.querySelector("#autoOrderingToggle");', POPUP_JS)
        self.assertIn('autoOrderingToggle.disabled = !autoOrderingRunning && !autoOrderingConfirmed.checked;', POPUP_JS)
        self.assertIn('autoOrderingToggle.textContent = autoOrderingRunning ? "Stop Auto Ordering" : "Start Auto Ordering";', POPUP_JS)
        self.assertIn('type: isRunning ? "STOP_AUTO_ORDERING" : "START_AUTO_ORDERING"', POPUP_JS)
        self.assertIn('autoOrderingConfirmed.checked = false', POPUP_JS)
        self.assertIn("await refresh();", POPUP_JS)

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

    def test_verified_business_bundle_expansion_is_not_misread_as_cart_pollution(self) -> None:
        guard_start = CONTENT.index("async function ensureCheckoutOnlyExpectedUnits")
        guard_end = CONTENT.index("function checkoutLineItemLimitCandidate", guard_start)
        guard = CONTENT[guard_start:guard_end]
        self.assertIn("verifiedBusinessBundleExpansion", guard)
        self.assertIn("actual > expected", guard)
        self.assertIn("isAmazonBusinessPage()", guard)
        self.assertIn("cartVerificationMatches(activeJob)", guard)
        self.assertIn("Accepted verified Amazon Business bundle expansion", guard)
        self.assertIn("activeJob.verifiedBusinessBundleExpansion", guard)
        self.assertIn('reason: "verified_business_bundle_expansion"', guard)

    def test_popup_queue_is_filtered_by_detected_amazon_experience(self) -> None:
        self.assertIn("async function popupAmazonAccountExperience", BACKGROUND)
        self.assertIn("queueStatusRequestPath(workerId, accountExperience)", BACKGROUND)
        self.assertIn("account_experience: accountExperience", BACKGROUND)
        queue_handler = BACKGROUND[BACKGROUND.index('if (message.type === "GET_QUEUE_STATUS")'):]
        self.assertIn("popupAmazonAccountExperience(windowId)", queue_handler)
        self.assertIn("getQueueStatus(accountExperience)", queue_handler)

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

    def test_content_completion_message_cannot_hang_forever(self) -> None:
        report_start = CONTENT.index("async function reportAmazonOrders(activeJob, orders)")
        report_end = CONTENT.index("async function autoResumeResolvedCheckoutPause", report_start)
        report = CONTENT[report_start:report_end]
        self.assertIn('result = await sendWithTimeout({\n        type: "COMPLETE_JOB"', report)
        self.assertIn("}, 55000);", report)

    def test_submitted_history_timeout_holds_queue_instead_of_failing_job(self) -> None:
        history_start = CONTENT.index("async function handleOrderHistory(activeJob)")
        history_end = CONTENT.index("async function reportPostSubmitUnplaced", history_start)
        history = CONTENT[history_start:history_end]
        self.assertNotIn('type: "SUBMIT_UNCERTAIN"', history)
        self.assertIn('activeJob.pausedStage = "find_order_id";', history)
        self.assertIn("the queue will not continue", history)
        self.assertNotIn("location.reload()", history)
        self.assertEqual(history.count("Reset and retry unplaced order"), 2)
        self.assertEqual(history.count('type: "RESET_DUPLICATE_FULFILMENT"'), 2)
        no_match_start = history.index("if (!orders.length && !rememberedOrder)")
        no_match = history[no_match_start:]
        self.assertIn("Reset and retry unplaced order", no_match)
        self.assertIn('type: "RESET_DUPLICATE_FULFILMENT"', no_match)

    def test_unplaced_submit_reset_clears_server_and_local_submit_markers(self) -> None:
        reset_start = BACKGROUND.index("async function resetDuplicateFulfilment(windowId)")
        reset_end = BACKGROUND.index("async function releaseStoredJob", reset_start)
        reset = BACKGROUND[reset_start:reset_end]
        self.assertIn("/reset-fulfilment", reset)
        self.assertIn('amazon_status: ""', reset)
        self.assertIn("submitted_to_amazon: false", reset)
        self.assertIn('activeJob.stage = "product";', reset)
        self.assertIn("activeJob.itemIndex = 0;", reset)
        self.assertIn("activeJob.cartCleared = false;", reset)
        self.assertIn("await navigateWindowToCart(windowId)", reset)
        self.assertIn("allowSubmittedReset: true", reset)
        self.assertIn("activeJob.resetRevision = Date.now();", reset)

    def test_stale_history_page_cannot_overwrite_a_reset_job(self) -> None:
        setter_start = BACKGROUND.index("async function setWindowJob(windowId, activeJob")
        setter_end = BACKGROUND.index("async function clearStoredJobGroup", setter_start)
        setter = BACKGROUND[setter_start:setter_end]
        self.assertIn("Number(current?.resetRevision || 0) > Number(activeJob?.resetRevision || 0)", setter)
        self.assertIn("options.allowSubmittedReset !== true", setter)
        self.assertIn("Ignored stale pre-reset update", setter)

    def test_next_claim_cannot_discard_required_cart_cleanup(self) -> None:
        blocker_start = BACKGROUND.index("function activeJobBlocksNext(activeJob)")
        blocker_end = BACKGROUND.index("function activeJobLineIds", blocker_start)
        blocker = BACKGROUND[blocker_start:blocker_end]
        self.assertNotIn('activeJob.stage === "cleanup_after_failure"', blocker)
        claim_start = BACKGROUND.index("async function claimNextJobInWindow(windowId)")
        claim_end = BACKGROUND.index("async function finishCleanupAndClaimNext", claim_start)
        claim = BACKGROUND[claim_start:claim_end]
        cleanup_branch = claim[claim.index('if (currentJob?.stage === "cleanup_after_failure"'):]
        self.assertNotIn("await setWindowJob(windowId, null);", cleanup_branch.split("if (activeJobBlocksNext", 1)[0])
        self.assertIn("await navigateWindowToCart(windowId);", cleanup_branch)
        self.assertIn("await injectActiveAmazonTabInWindow(windowId);", cleanup_branch)
        self.assertIn("return currentJob;", cleanup_branch)

    def test_account_aware_subscription_matrix_is_explicit(self) -> None:
        product_start = CONTENT.index("async function handleProduct(activeJob)")
        product_end = CONTENT.index("async function handleAddClicked", product_start)
        product = CONTENT[product_start:product_end]
        self.assertIn("const accountExperience = await resolvedAmazonAccountExperience(activeJob);", product)
        self.assertIn('const consumerMixedSubscription = mixedAsinAfterVariant && accountExperience === "consumer";', product)
        self.assertIn('const businessMixedOrder = mixedAsinAfterVariant && accountExperience === "business";', product)
        self.assertIn("snsIsCheaper && !businessMixedOrder", product)
        self.assertIn("{ requireCartAdd: consumerMixedSubscription }", product)
        self.assertIn('consumerMixedSubscription ? "subscribe-save-cart" : "subscribe-save"', product)
        self.assertIn("Business multi-ASIN orders must stay in the normal Amazon cart", product)
        self.assertIn("await waitUntil(findSubscribeAddToCartTarget, 15000, 250)", product)
        self.assertIn("Fulfilment paused without switching to One-time purchase", product)

    def test_single_asin_can_use_direct_subscription_when_cheaper(self) -> None:
        product_start = CONTENT.index("async function handleProduct(activeJob)")
        product_end = CONTENT.index("async function handleAddClicked", product_start)
        product = CONTENT[product_start:product_end]
        self.assertIn("const snsIsCheaper = priceSnapshot.sns && priceSnapshot.regular", product)
        self.assertIn("const useSubscribeAndSave = Boolean(snsIsCheaper", product)
        self.assertIn("applySubscribeAndSaveIfCheaper", product)
        self.assertIn("requireCartAdd: consumerMixedSubscription", product)
        self.assertIn("activateOneTimePurchaseOption()", product)

    def test_regular_cart_add_never_uses_subscription_cart_control(self) -> None:
        finder_start = CONTENT.index("function findRegularAddToCartTarget()")
        finder_end = CONTENT.index("function findSubscribeSubmitTargets()", finder_start)
        finder = CONTENT[finder_start:finder_end]
        self.assertIn('label.includes("add subscription to cart")', finder)
        self.assertIn("!element.closest(subscribeAndSaveRootSelector())", finder)
        product_start = CONTENT.index("async function handleProduct(activeJob)")
        product_end = CONTENT.index("async function handleAddClicked", product_start)
        self.assertIn("await waitUntil(findRegularAddToCartTarget, 18000, 300)", CONTENT[product_start:product_end])

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

    def test_add_clicked_wait_cannot_short_circuit_on_hidden_cart_markup(self) -> None:
        start = CONTENT.index("async function handleAddClicked(activeJob)")
        end = CONTENT.index("async function handleSubscribeCheckout", start)
        handler = CONTENT[start:end]
        self.assertIn("await sleep(remainingWaitMs);", handler)
        self.assertNotIn('document.querySelector("#sc-active-cart', handler)
        self.assertNotIn('findButtonByText(["proceed to checkout"', handler)

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

    def test_amazon_day_does_not_override_free_next_day_on_workdays(self) -> None:
        reward_start = CONTENT.index("async function ensureRewardedLaterDelivery")
        reward_end = CONTENT.index("function checkoutDeliveryPromiseText", reward_start)
        reward = CONTENT[reward_start:reward_end]
        self.assertNotIn("ensurePreferredAmazonDayWeekdayDelivery(activeJob)", reward)
        self.assertIn("if (!shouldPreferReward) return ensureFreeNextDayDelivery(activeJob, brooklynDay);", reward)

    def test_reward_option_requires_its_own_explicit_weekday_reward_label(self) -> None:
        helper_start = CONTENT.index("function fridayRewardDeliveryOption()")
        helper_end = CONTENT.index("function rewardedLaterDeliverySelected()", helper_start)
        helper = CONTENT[helper_start:helper_end]
        self.assertIn("isOnePercentDeliveryRewardText(context.text)", helper)
        self.assertIn("!deliveryContextIsNotConsolidated(context)", helper)
        self.assertIn("deliveryContextNamesWarehouseOpenDay(context)", helper)
        self.assertNotIn("checkoutOffersOnePercentDeliveryReward()", helper)
        self.assertNotIn("isAmazonDayDeliveryContext(context)", helper)

    def test_split_weekend_delivery_is_replaced_before_preferences_apply(self) -> None:
        helper_start = CONTENT.index("function selectedDeliveryRadioContext()")
        reward_start = CONTENT.index("async function ensureRewardedLaterDelivery", helper_start)
        helpers = CONTENT[helper_start:reward_start]
        reward_end = CONTENT.index("function checkoutDeliveryPromiseText", reward_start)
        reward = CONTENT[reward_start:reward_end]
        self.assertIn("sat(?:urday)?|sun(?:day)?", helpers)
        self.assertIn("function deliveryContextHasSplitPromise(context)", helpers)
        self.assertIn("function deliveryContextIsNotConsolidated(context)", helpers)
        self.assertIn("function consolidatedWeekdayDeliveryOption()", helpers)
        self.assertIn("candidates.find(isAmazonDayDeliveryContext)", helpers)
        self.assertLess(
            reward.index("ensureWarehouseOpenDayDelivery(activeJob)"),
            reward.index("state.preferRewardedLaterDelivery === true"),
        )

    def test_checkout_fails_closed_if_weekend_delivery_remains_selected(self) -> None:
        guard_start = CONTENT.index("async function checkoutDeliveryWindowIsAllowed")
        guard_end = CONTENT.index("function checkoutQuantityFromPage", guard_start)
        guard = CONTENT[guard_start:guard_end]
        self.assertIn("selectedDeliveryRadioContext()", guard)
        self.assertIn("deliveryContextIsNotConsolidated(selectedDelivery)", guard)
        self.assertIn("Checkout is blocked because the selected Amazon delivery is split across dates", guard)
        self.assertIn("deliveryTextIncludesWarehouseClosedDay(promise.text)", guard)
        self.assertIn("final Amazon delivery promise falls on Saturday or Sunday", guard)
        self.assertIn("return false;", guard)

    def test_date_only_delivery_promises_are_checked_for_weekends(self) -> None:
        helper_start = CONTENT.index('function deliveryTextCalendarDates(value = "", now = new Date())')
        helper_end = CONTENT.index("function deliveryContextHasSplitPromise", helper_start)
        helpers = CONTENT[helper_start:helper_end]
        self.assertIn("date.getDay()", helpers)
        self.assertIn("[0, 6].includes", helpers)
        self.assertIn("date.getDay() >= 1 && date.getDay() <= 5", helpers)

    def test_checkout_promise_ignores_unselected_delivery_rows(self) -> None:
        promise_start = CONTENT.index("function checkoutDeliveryPromiseText()")
        promise_end = CONTENT.index("function checkoutDeliveryPromise(limitDays", promise_start)
        promise = CONTENT[promise_start:promise_end]
        self.assertIn("selectedDeliveryRadioContext()", promise)
        self.assertIn("!optionRadio || optionRadio.checked", promise)

    def test_checked_delivery_radio_beats_stale_checkout_heading(self) -> None:
        promise_start = CONTENT.index("function checkoutDeliveryPromiseText()")
        promise_end = CONTENT.index("function checkoutDeliveryPromise(limitDays", promise_start)
        promise = CONTENT[promise_start:promise_end]
        selected_return = promise.index("if (selectedOptionText) return selectedOptionText;")
        heading_scan = promise.index("document.querySelectorAll(selectors.join")
        self.assertLess(selected_return, heading_scan)
        self.assertNotIn("[selectedOptionText, ...elementCandidates]", promise)

    def test_final_delivery_selection_must_stay_consolidated_before_submit(self) -> None:
        final_start = CONTENT.index("async function ensureFinalConsolidatedDelivery")
        final_end = CONTENT.index("function checkoutDeliveryPromiseText", final_start)
        final_guard = CONTENT[final_start:final_end]
        self.assertIn("for (let attempt = 1; attempt <= 3; attempt += 1)", final_guard)
        self.assertIn("await sleep(1800);", final_guard)
        self.assertIn("deliveryContextIsNotConsolidated(afterSettle)", final_guard)
        checkout_start = CONTENT.index("async function handleCheckout(activeJob)")
        checkout_end = CONTENT.index("function extractOrderId()", checkout_start)
        checkout = CONTENT[checkout_start:checkout_end]
        final_delivery = checkout.index("ensureFinalConsolidatedDelivery(activeJob)")
        fresh_control = checkout.index("placeOrder = await waitUntil(findPlaceOrderButton, 10000, 250)", final_delivery)
        protection = checkout.index('protectBeforeAmazonSubmit(activeJob, "checkout")')
        self.assertLess(final_delivery, protection)
        self.assertLess(final_delivery, fresh_control)
        self.assertLess(fresh_control, protection)
        self.assertIn("!placeOrder.isConnected", checkout)

    def test_place_order_control_is_requeried_after_submit_protection(self) -> None:
        checkout_start = CONTENT.index("async function handleCheckout(activeJob)")
        checkout_end = CONTENT.index("function extractOrderId()", checkout_start)
        checkout = CONTENT[checkout_start:checkout_end]
        protection = checkout.index('protectBeforeAmazonSubmit(activeJob, "checkout")')
        fresh_after_protection = checkout.index(
            "placeOrder = await waitUntil(findPlaceOrderButton, 10000, 250) || findPlaceOrderButton();",
            protection,
        )
        click = checkout.index('clickElement(placeOrder, "Place your order button")', fresh_after_protection)
        self.assertLess(protection, fresh_after_protection)
        self.assertLess(fresh_after_protection, click)
        self.assertIn("!placeOrder.isConnected", checkout[fresh_after_protection:click])

    def test_cancelled_amazon_history_cannot_reconcile_a_fresh_job(self) -> None:
        matcher_start = APP.index("def exact_amazon_history_match_for_chrome_job")
        matcher_end = APP.index("def complete_chrome_job_from_exact_history_match", matcher_start)
        matcher = APP[matcher_start:matcher_end]
        self.assertIn("known_cancelled_ids", matcher)
        self.assertIn("amazon_order_id in known_cancelled_ids", matcher)
        history_start = APP.index("def upsert_amazon_history_unmatched")
        history_end = APP.index("def asin_product_url", history_start)
        history = APP[history_start:history_end]
        self.assertIn('if record.get("cancelled"):', history)
        self.assertIn("resolved_at=COALESCE(resolved_at, ?)", history)

    def test_reset_fulfilment_preserves_cancelled_amazon_order_id(self) -> None:
        reset_start = APP.index("def api_reset_line_fulfilment")
        reset_end = APP.index('@app.put("/api/lines/{line_id}/spaid")', reset_start)
        reset = APP[reset_start:reset_end]
        self.assertIn("amazon_cancelled_order_id=CASE", reset)
        self.assertIn("THEN amazon_order_id", reset)
        self.assertIn("amazon_cancelled_at=CASE", reset)

    def test_proven_zero_cart_quantity_gets_only_one_controlled_add_retry(self) -> None:
        helper_start = CONTENT.index("async function retryProvenMissingCartItemOnce")
        cart_start = CONTENT.index("async function handleCart(activeJob)", helper_start)
        helper = CONTENT[helper_start:cart_start]
        cart_end = CONTENT.index("async function fillFullName", cart_start)
        cart = CONTENT[cart_start:cart_end]
        self.assertIn("cartQuantityForAsin(normalizedAsin) !== 0", helper)
        self.assertIn("Number(retries[retryKey] || 0) >= 1", helper)
        self.assertIn('activeJob.cartMissingAddRetries = { ...retries, [retryKey]: 1 }', helper)
        self.assertIn('reason: "retry_proven_missing_cart_item_once"', helper)
        self.assertIn("actualQuantity === 0 && await retryProvenMissingCartItemOnce", cart)

    def test_place_order_lookup_returns_a_native_control_not_amazon_wrapper(self) -> None:
        finder_start = CONTENT.index("function findPlaceOrderButton()")
        finder_end = CONTENT.index("function findSnsPaymentConfirmationCheckbox", finder_start)
        finder = CONTENT[finder_start:finder_end]
        self.assertIn("for (const selector of nativeSelectors)", finder)
        self.assertIn('document.querySelectorAll("button")', finder)
        self.assertIn('wrapper.querySelectorAll("input[type=\'submit\'], input[type=\'button\'], button")', finder)
        self.assertIn("function isNativePlaceOrderControl(element)", finder)
        self.assertNotIn('"button",\n    "span.a-button",', finder)

    def test_submit_protection_precedes_native_place_order_click_marker(self) -> None:
        checkout_start = CONTENT.index("async function handleCheckout(activeJob)")
        checkout_end = CONTENT.index("function extractOrderId()", checkout_start)
        checkout = CONTENT[checkout_start:checkout_end]
        protection = checkout.index('protectBeforeAmazonSubmit(activeJob, "checkout")')
        marker = checkout.index("activeJob.placeOrderClickStartedAt = Date.now()", protection)
        click = checkout.index('clickElement(placeOrder, "Place your order button")', marker)
        self.assertLess(protection, marker)
        self.assertLess(marker, click)
        self.assertIn("isNativePlaceOrderControl(placeOrder)", checkout)

    def test_failure_cleanup_can_leave_order_history(self) -> None:
        self.assertIn('activeJob?.stage === "cleanup_after_failure"', CONTENT)
        self.assertIn('activeJob?.cleanupAfterFailure === true', CONTENT)
        cleanup_guard = CONTENT.index('activeJob?.stage === "cleanup_after_failure"', CONTENT.index("async function guardUnexpectedAmazonPage"))
        unexpected_guard = CONTENT.index('reason: "unexpected_order_history_page"', cleanup_guard)
        self.assertLess(cleanup_guard, unexpected_guard)

    def test_submitted_order_history_reaches_safe_matcher_without_self_redirect(self) -> None:
        guard_start = CONTENT.index("async function guardUnexpectedAmazonPage(activeJob)")
        guard_end = CONTENT.index("function submittedStage(activeJob)", guard_start)
        guard = CONTENT[guard_start:guard_end]
        history_passthrough = guard.index("if (isOrderHistoryPage() && submittedEvidence) return false;")
        generic_submitted_guard = guard.index("if (\n    isAmazonThankYouPage()", history_passthrough)
        redirect = guard.index("forceOrderReportingFromSubmittedPage", generic_submitted_guard)
        self.assertLess(history_passthrough, generic_submitted_guard)
        self.assertLess(history_passthrough, redirect)
        self.assertIn("const submittedEvidence = submittedOrPausedStage(activeJob) || activeJobWasSubmittedToAmazon(activeJob);", guard)

    def test_address_selection_and_editor_are_detected_without_serial_six_second_waits(self) -> None:
        verify_start = CONTENT.index("async function verifyCheckoutDeliveryRecipient")
        verify_end = CONTENT.index("async function handleCheckoutLimitPurchase", verify_start)
        verify = CONTENT[verify_start:verify_end]
        render_wait = verify.index("const confirmedAfterRender")
        self.assertIn("&& !checkoutAddressSelectionPageOpen()", verify[:render_wait])
        self.assertIn("&& !findAddressNameInput()", verify[:render_wait])

        open_start = CONTENT.index("async function openAddressEditorIfAvailable")
        open_end = CONTENT.index("async function openNewDeliveryAddressFormIfAvailable", open_start)
        opener = CONTENT[open_start:open_end]
        self.assertIn("let editAddress = findEditAddressTrigger();", opener)
        self.assertIn("let changeAddress = editAddress ? null : findChangeDeliveryAddressButton();", opener)
        self.assertIn("2500,\n      150,", opener)
        self.assertNotIn("await waitUntil(findEditAddressTrigger, 6000", opener)
        self.assertNotIn("await sleep(2000)", opener)

    def test_existing_address_name_fill_avoids_fixed_animation_delays(self) -> None:
        fill_start = CONTENT.index("async function fillFullName(name)")
        fill_end = CONTENT.index("async function setInputValue", fill_start)
        fill = CONTENT[fill_start:fill_end]
        self.assertIn("findAddressNameInput() || await waitUntil", fill)
        self.assertIn("6000, 150", fill)
        self.assertIn('behavior: "auto"', fill)
        self.assertNotIn("sleep(500)", fill)
        self.assertNotIn("sleep(800)", fill)

    def test_slow_checkout_delivery_card_render_recovers_before_manual_pause(self) -> None:
        checkout_start = CONTENT.index("async function handleCheckout(activeJob)")
        checkout_end = CONTENT.index("function extractOrderId()", checkout_start)
        checkout = CONTENT[checkout_start:checkout_end]
        parallel_wait = checkout.index("Amazon paints the final checkout shell before its delivery card")
        edit_freshness = checkout.index("const addressEditIsFresh", parallel_wait)
        self.assertLess(parallel_wait, edit_freshness)
        parallel_block = checkout[parallel_wait:edit_freshness]
        self.assertIn("checkoutRecipientConfirmed(checkoutRecipient)", parallel_block)
        self.assertIn("checkoutAddressSelectionPageOpen()", parallel_block)
        self.assertIn("findChangeDeliveryAddressButton()", parallel_block)
        self.assertIn("findEditAddressTrigger()", parallel_block)
        self.assertIn("8000,\n      150,", parallel_block)

        pause_message = 'Could not find the Change delivery address or Edit address link for the Nutricity address.'
        pause_at = checkout.index(pause_message, edit_freshness)
        late_recovery = checkout.index("const lateAddressEditor = await openAddressEditorIfAvailable(activeJob);", edit_freshness)
        self.assertLess(late_recovery, pause_at)

    def test_paused_checkout_auto_resume_defines_expected_recipient(self) -> None:
        resume_start = CONTENT.index("async function autoResumeResolvedCheckoutPause(activeJob)")
        resume_end = CONTENT.index("async function run()", resume_start)
        resume = CONTENT[resume_start:resume_end]
        declaration = resume.index("const checkoutRecipient = recipientName(activeJob);")
        first_use = resume.index("addressEditedRecipient: checkoutRecipient")
        self.assertLess(declaration, first_use)

    def test_address_list_is_recovered_before_final_recipient_verification(self) -> None:
        self.assertIn("function checkoutAddressSelectionPageOpen()", CONTENT)
        verifier = CONTENT.index("async function verifyCheckoutDeliveryRecipient")
        address_recovery = CONTENT.index("if (checkoutAddressSelectionPageOpen())", verifier)
        missing_recipient_pause = CONTENT.index("if (!deliveredTo)", verifier)
        self.assertLess(address_recovery, missing_recipient_pause)
        self.assertIn("const recipientRow = addressRowForRecipient(checkoutRecipient);", CONTENT[address_recovery:missing_recipient_pause])
        self.assertIn("const editor = await openAddressEditorIfAvailable(activeJob);", CONTENT[address_recovery:missing_recipient_pause])

    def test_exact_consumer_recipient_row_is_selected_before_any_address_edit(self) -> None:
        checkout_start = CONTENT.index("async function handleCheckout(activeJob)")
        checkout_end = CONTENT.index("function extractOrderId()", checkout_start)
        checkout = CONTENT[checkout_start:checkout_end]
        exact_row = checkout.index("checkoutAddressSelectionPageOpen() && addressRowForRecipient(checkoutRecipient)")
        edit_freshness = checkout.index("const addressEditIsFresh", exact_row)
        open_editor = checkout.index("openAddressEditorIfAvailable(activeJob)", edit_freshness)
        self.assertLess(exact_row, edit_freshness)
        self.assertLess(exact_row, open_editor)
        self.assertIn("await verifyCheckoutDeliveryRecipient(activeJob, checkoutRecipient);", checkout[exact_row:edit_freshness])

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

    def test_subscribe_payment_confirmation_clicks_native_checkbox_first(self) -> None:
        helper_start = CONTENT.index("async function ensureSnsPaymentConfirmation(activeJob)")
        helper_end = CONTENT.index("async function recoverBlockedSnsSubmit", helper_start)
        helper = CONTENT[helper_start:helper_end]
        native_click = helper.index('clickElement(checkbox, "Subscribe & Save payment confirmation checkbox")')
        fallback_click = helper.index('clickElement(label, "Subscribe & Save payment confirmation label")')
        self.assertLess(native_click, fallback_click)
        self.assertIn("const freshCheckbox = findSnsPaymentConfirmationCheckbox();", helper)

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

    def test_subscription_checkout_mode_matches_order_and_account(self) -> None:
        product_start = CONTENT.index("async function handleProduct(activeJob)")
        product_end = CONTENT.index("async function handleAddClicked(activeJob)", product_start)
        product = CONTENT[product_start:product_end]
        self.assertIn("applySubscribeAndSaveIfCheaper", product)
        self.assertIn("requireCartAdd: consumerMixedSubscription", product)
        self.assertIn('consumerMixedSubscription ? "subscribe-save-cart" : "subscribe-save"', product)
        self.assertIn('setQuantity(purchaseItem.quantity, "regular")', product)

    def test_subscribe_price_participates_in_price_comparison(self) -> None:
        snapshot_start = CONTENT.index("function productPriceSnapshot()")
        snapshot_end = CONTENT.index("function snsQuantityControlVisible()", snapshot_start)
        snapshot = CONTENT[snapshot_start:snapshot_end]
        self.assertIn("Math.min(sns, regular)", snapshot)

    def test_labelled_one_time_price_wins_over_coupon_savings(self) -> None:
        parser_start = CONTENT.index("function oneTimePurchasePriceFromText(text)")
        parser_end = CONTENT.index("function oneTimePurchaseRootSelector()", parser_start)
        parser = CONTENT[parser_start:parser_end]
        self.assertIn("one[\\s-]*time\\s+purchase", parser)
        snapshot_start = CONTENT.index("function productPriceSnapshot()")
        snapshot_end = CONTENT.index("function snsQuantityControlVisible()", snapshot_start)
        snapshot = CONTENT[snapshot_start:snapshot_end]
        self.assertIn("const labelledRegular = oneTimePurchasePriceFromText(document.body.innerText);", snapshot)
        self.assertIn("const regular = labelledRegular ||", snapshot)

    def test_subscription_cart_button_is_detected_by_meaning_inside_sns_root(self) -> None:
        finder_start = CONTENT.index("function findSubscribeAddToCartTarget()")
        finder_end = CONTENT.index("function findSubscribeSubmitTargets()", finder_start)
        finder = CONTENT[finder_start:finder_end]
        self.assertIn("subscribeAndSaveRootSelector()", finder)
        self.assertIn("input#add-to-cart-button[name='submit.add-to-cart']", finder)
        self.assertIn('label.includes("add subscription to cart")', finder)

        apply_start = CONTENT.index("async function applySubscribeAndSaveIfCheaper")
        apply_end = CONTENT.index("function findSubscribeAddToCartTarget()", apply_start)
        apply_flow = CONTENT[apply_start:apply_end]
        cart_branch = apply_flow.index("if (options.requireCartAdd)")
        direct_checkout = apply_flow.index("const subscribeButtons = findSubscribeSubmitTargets()")
        self.assertLess(cart_branch, direct_checkout)
        self.assertIn('activeJob.stage = "add_clicked"', apply_flow[cart_branch:direct_checkout])
        self.assertIn('activeJob.subscribeAndSave = false', apply_flow[cart_branch:direct_checkout])
        self.assertNotIn('activeJob.stage = "subscribe_checkout"', apply_flow[cart_branch:direct_checkout])

    def test_consumer_subscribe_quantity_select_is_supported(self) -> None:
        quantity_start = CONTENT.index("function snsQuantityControlVisible()")
        quantity_end = CONTENT.index("function productTitleText()", quantity_start)
        quantity_guard = CONTENT[quantity_start:quantity_end]
        self.assertIn('"select#rcxsubsQuan"', quantity_guard)
        self.assertIn('"select[name=\'rcxsubsQuan\']"', quantity_guard)

    def test_consumer_frequency_popover_selects_and_verifies_six_months(self) -> None:
        chooser_start = CONTENT.index("async function chooseSubscribeFrequencySixMonths()")
        chooser_end = CONTENT.index("function findSubscribeFrequencyDropdownButton()", chooser_start)
        chooser = CONTENT[chooser_start:chooser_end]
        self.assertIn('document.querySelector("#replenishment-onml-frequency-trigger")', chooser)
        self.assertIn('"[data-frequency-value^=\'6M|onml\']"', chooser)
        self.assertIn('"#onmlFrequencyAccordionRow-10"', chooser)
        self.assertIn("waitUntil(subscribeFrequencyIsSixMonths", chooser)
        self.assertIn('String(valueInput?.value || "") === "6"', chooser)
        self.assertIn('String(unitInput?.value || "").toUpperCase() === "M"', chooser)

    def test_subscription_cart_add_requires_confirmed_six_month_frequency(self) -> None:
        configure_start = CONTENT.index("async function configureSubscribeAndSaveDelivery()")
        configure_end = CONTENT.index("async function chooseSubscribeSoonerDelivery()", configure_start)
        configure = CONTENT[configure_start:configure_end]
        self.assertIn("const frequencyConfigured = await chooseSubscribeFrequencySixMonths();", configure)
        self.assertIn("if (!frequencyConfigured)", configure)
        self.assertIn("paused before adding the subscription to cart", configure)

    def test_account_detection_does_not_use_consumer_footer_marketing(self) -> None:
        detector_start = CONTENT.index("function amazonAccountExperience()")
        detector_end = CONTENT.index("async function ensureCheckoutOnlyExpectedUnits", detector_start)
        detector = CONTENT[detector_start:detector_end]
        self.assertIn('purchaseProgram === "amazon_business"', detector)
        self.assertIn("#nav-logo a[aria-label*='Amazon Business' i]", detector)
        self.assertNotIn("header [aria-label*='Amazon Business' i]", detector)
        self.assertIn('return consumerShell ? "consumer" : "unknown";', detector)
        self.assertNotIn("document.body", detector)
        self.assertNotIn('text.includes("amazon business")', detector)

    def test_checkout_records_detected_account_experience(self) -> None:
        checkout_start = CONTENT.index("async function handleCheckout(activeJob)")
        checkout_end = CONTENT.index("function extractOrderId()", checkout_start)
        checkout = CONTENT[checkout_start:checkout_end]
        self.assertIn("const accountExperience = amazonAccountExperience();", checkout)
        self.assertIn("activeJob.amazonAccountExperience = accountExperience;", checkout)
        self.assertIn('account_experience: accountExperience', checkout)

    def test_duplicate_order_confirmation_reuses_original_submit_protection(self) -> None:
        handler_start = CONTENT.index("async function handleAmazonDuplicateOrderPage(activeJob)")
        handler_end = CONTENT.index("function orderDetailsUrl", handler_start)
        handler = CONTENT[handler_start:handler_end]
        self.assertIn("activeJob.amazonSubmittedAt", handler)
        self.assertIn("activeJob.placeOrderClickStartedAt", handler)
        self.assertIn("await waitUntil(() =>", handler)
        self.assertIn("activeJob.amazonDuplicateOrderConfirmed = true", handler)
        self.assertIn('clickElement(placeOrder, "duplicate-order Place your order button")', handler)
        executable = handler.replace("// protectBeforeAmazonSubmit", "// original protection")
        self.assertNotIn("await protectBeforeAmazonSubmit", executable)
        run_start = CONTENT.index("async function run()")
        run_end = CONTENT.index("async function runSafely()", run_start)
        run = CONTENT[run_start:run_end]
        duplicate_route = run.index("amazonDuplicateOrderRoute() || amazonDuplicateOrderPage()")
        unexpected_guard = run.index("guardUnexpectedAmazonPage(activeJob)")
        submitted_recovery = run.index("activeJobWasSubmittedToAmazon(activeJob)")
        self.assertLess(duplicate_route, unexpected_guard)
        self.assertLess(duplicate_route, submitted_recovery)

    def test_user_pause_aborts_inflight_flow_and_cannot_auto_resume(self) -> None:
        pause_start = CONTENT.index("async function waitIfPaused()")
        pause_end = CONTENT.index("async function waitForPageReady", pause_start)
        pause = CONTENT[pause_start:pause_end]
        self.assertIn("throw fulfilmentPausedError()", pause)
        self.assertNotIn("while (", pause)
        run_start = CONTENT.index("async function run()")
        run_end = CONTENT.index("async function runSafely()", run_start)
        run = CONTENT[run_start:run_end]
        user_pause = run.index("if (activeJob.pausedByUser)")
        auto_resume = run.index("autoResumeResolvedCheckoutPause(activeJob)")
        self.assertLess(user_pause, auto_resume)
        self.assertIn("activeJob.pausedByUser = true", BACKGROUND)
        self.assertIn("pausedByUser: Boolean(latest.pausedByUser || activeJob.pausedByUser)", CONTENT)
        self.assertIn("const preserveUserPause = sameJob && latest.paused && latest.pausedByUser", CONTENT)
        self.assertIn("throw fulfilmentPausedError()", CONTENT)

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
