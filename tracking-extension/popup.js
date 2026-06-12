const apiBase = document.querySelector("#apiBase");
const adminToken = document.querySelector("#adminToken");
const headlessTrackingMode = document.querySelector("#headlessTrackingMode");
const autoTrackingEnabled = document.querySelector("#autoTrackingEnabled");
const autoTrackingHours = document.querySelector("#autoTrackingHours");
const singleAmazonOrderId = document.querySelector("#singleAmazonOrderId");
const amazonOrderBatch = document.querySelector("#amazonOrderBatch");
const trackAllStartPage = document.querySelector("#trackAllStartPage");
const trackAllMaxPages = document.querySelector("#trackAllMaxPages");
const modeNotice = document.querySelector("#modeNotice");
const statusBox = document.querySelector("#status");
const logsBox = document.querySelector("#logs");
const startButton = document.querySelector("#start");
const stopButton = document.querySelector("#stop");
const trackSingleOrderButton = document.querySelector("#trackSingleOrder");
const trackQueuedOrdersButton = document.querySelector("#trackQueuedOrders");
const recheckPaymentFailuresButton = document.querySelector("#recheckPaymentFailures");
const trackAllButton = document.querySelector("#trackAll");
const resumeTrackAllButton = document.querySelector("#resumeTrackAll");
const stopTrackAllButton = document.querySelector("#stopTrackAll");

let targetWindowId = null;
let settingsDirty = false;
let settingsHydrated = false;
let statusFadeTimer = null;
let statusHoldUntil = 0;

const settingsInputs = [apiBase, adminToken, headlessTrackingMode, autoTrackingEnabled, autoTrackingHours, trackAllStartPage, trackAllMaxPages];

settingsInputs.forEach((input) => {
  input.addEventListener("input", () => {
    settingsDirty = true;
  });
  input.addEventListener("change", () => {
    settingsDirty = true;
    updateModeNotice();
  });
});

function syncSettingsInputs(state) {
  if (settingsHydrated || settingsDirty || settingsInputs.includes(document.activeElement)) return;
  apiBase.value = state.apiBase || "http://127.0.0.1:8000";
  adminToken.value = state.adminToken || "";
  headlessTrackingMode.checked = state.headlessTrackingMode === true;
  autoTrackingEnabled.checked = state.autoTrackingEnabled === true;
  autoTrackingHours.value = state.autoTrackingHours ?? 3;
  trackAllStartPage.value = state.trackAllStartPage ?? 1;
  trackAllMaxPages.value = state.trackAllMaxPages ?? 202;
  updateModeNotice();
  settingsHydrated = true;
}

async function resolveTargetWindowId() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  targetWindowId = tabs[0]?.windowId || targetWindowId;
}

function send(message) {
  return chrome.runtime.sendMessage({ ...message, targetWindowId });
}

function setStatus(text, kind = "info", options = {}) {
  if (statusFadeTimer) clearTimeout(statusFadeTimer);
  statusBox.className = "";
  statusBox.classList.add(`status-${kind}`);
  statusBox.textContent = text;
  statusHoldUntil = options.hold === "forever" ? Number.POSITIVE_INFINITY : Date.now() + Number(options.holdMs || 0);
  if (options.fadeAfterMs) {
    statusHoldUntil = Date.now() + Number(options.fadeAfterMs) + 400;
    statusFadeTimer = setTimeout(() => {
      statusBox.classList.add("status-fading");
    }, Number(options.fadeAfterMs));
  }
}

function canResumeTrackAll(tracking = {}) {
  if (tracking.source !== "history" || tracking.running) return false;
  const hasCurrent = Boolean(tracking.currentOrder?.amazon_order_id);
  const hasQueue = Array.isArray(tracking.queue) && tracking.queue.length > 0;
  const hasMorePages = Number(tracking.pagesScanned || 0) < Number(tracking.maxPages || 0);
  return hasCurrent || hasQueue || hasMorePages;
}

function setRunButtons(running, trackAllRunning = false, trackAllResumable = false) {
  startButton.hidden = Boolean(running);
  stopButton.hidden = !running;
  trackAllButton.hidden = Boolean(running);
  resumeTrackAllButton.hidden = Boolean(running) || !trackAllResumable;
  stopTrackAllButton.hidden = !trackAllRunning;
}

function applyStatusFromRefresh(text) {
  if (Date.now() < statusHoldUntil) return;
  setStatus(text, "info");
}

function headlessRunning(progress = {}) {
  return progress.running === true || progress.status === "running" || progress.message?.toLowerCase?.().includes("running");
}

function resultKind(result) {
  return result?.ok === false ? "error" : "success";
}

function resultHoldOptions(result) {
  return result?.ok === false ? { hold: "forever" } : { fadeAfterMs: 2000 };
}

function setResultStatus(result, successText, failureText) {
  setStatus(result?.message || (result?.ok === false ? failureText : successText), resultKind(result), resultHoldOptions(result));
}

function setErrorStatus(error) {
  setStatus(error.message || String(error) || "Action failed.", "error", { hold: "forever" });
}

function updateModeNotice() {
  const mode = headlessTrackingMode.checked
    ? "Headless mode starts tracking in the local app's background Chrome profile. It reuses the same Amazon parser as normal mode."
    : "Normal mode opens Amazon tabs in this Chrome window and tracks with the extension.";
  const auto = autoTrackingEnabled.checked
    ? ` Auto mode will check every ${Math.max(1, Math.min(168, Number(autoTrackingHours.value || 3)))} hour(s).`
    : " Auto mode is off.";
  modeNotice.textContent = `${mode}${auto}`;
}

function progressText(progress = {}) {
  const processed = Number(progress.processed || 0);
  const total = Number(progress.total || 0);
  const current = progress.current_order ? ` · ${progress.current_order}` : "";
  return `${progress.message || "Headless tracking status loaded."} ${processed}/${total} processed${current}`;
}

function autoWaitingText(tracking = {}) {
  const completed = tracking.completedOrderIds?.length || 0;
  const failed = tracking.failedOrderIds?.length || 0;
  const skipped = Number(tracking.skippedRecentCount || 0);
  const hours = Math.max(1, Math.min(168, Number(autoTrackingHours.value || 3)));
  const detail = `Checked ${completed} order(s), failed ${failed}${skipped ? `, skipped recent ${skipped}` : ""}.`;
  return `${tracking.lastMessage || "Amazon tracking complete."} Auto mode is enabled; waiting ${hours} hour(s) until the next scheduled tracking run. ${detail}`;
}

function visibleProgressText(tracking = {}) {
  if (tracking.source === "single" || tracking.source === "manual" || tracking.source === "payment_recheck") {
    const current = tracking.orders?.[Number(tracking.index || 0)]?.amazon_order_id || tracking.currentOrderId || tracking.singleOrderId || "";
    const completed = tracking.completedOrderIds?.length || 0;
    const failed = tracking.failedOrderIds?.length || 0;
    const total = tracking.orders?.length || 0;
    const label = tracking.source === "payment_recheck" ? "Rechecking payment orders" : total > 1 || tracking.source === "manual" ? "Tracking queued orders" : "Tracking one order";
    return `${label}${current ? ` · current ${current}` : ""} · checked ${completed}/${total || completed + failed} · failed ${failed}`;
  }
  if (tracking.source === "history") {
    const queue = tracking.queue?.length || 0;
    const completed = tracking.completedOrderIds?.length || 0;
    const failed = tracking.failedOrderIds?.length || 0;
    const page = tracking.currentPage ? ` · page ${tracking.currentPage}` : "";
    const current = tracking.currentOrder?.amazon_order_id ? ` · current ${tracking.currentOrder.amazon_order_id}` : "";
    return `Track all running${page}${current} · queue ${queue} · checked ${completed} · failed ${failed}`;
  }
  const total = tracking.orders?.length || 0;
  const index = Math.max(0, Number(tracking.index || 0));
  const processed = Math.min(index, total);
  const current = tracking.orders?.[index]?.amazon_order_id || tracking.currentOrderId || "";
  const completed = tracking.completedOrderIds?.length || 0;
  const failed = tracking.failedOrderIds?.length || 0;
  const skipped = Number(tracking.skippedRecentCount || 0);
  const currentText = current ? ` · current ${current}` : "";
  const extra = ` · checked ${completed} · failed ${failed}${skipped ? ` · skipped recent ${skipped}` : ""}`;
  return `Running: ${processed}/${total}${currentText}${extra}`;
}

function stoppedTrackAllText(tracking = {}) {
  const queue = tracking.queue?.length || 0;
  const completed = tracking.completedOrderIds?.length || 0;
  const failed = tracking.failedOrderIds?.length || 0;
  const page = tracking.currentPage || tracking.startPage || 1;
  const nextPage = Number(tracking.pagesScanned || 0) < Number(tracking.maxPages || 0) ? ` · next page ${Number(page) + 1}` : "";
  const current = tracking.currentOrder?.amazon_order_id ? ` · current ${tracking.currentOrder.amazon_order_id}` : "";
  return `Stopped · Track all can resume from page ${page}${nextPage}${current} · queue ${queue} · checked ${completed} · failed ${failed}`;
}

function finalTrackingText(tracking = {}) {
  const lastMessage = String(tracking.lastMessage || "").trim();
  if (autoTrackingEnabled.checked && tracking.source === "auto") {
    if (/waiting \d+ hour/i.test(lastMessage)) return lastMessage;
    return autoWaitingText(tracking);
  }
  if (/stopped because/i.test(lastMessage)) {
    return lastMessage;
  }
  if (tracking.finishedAt) {
    const completed = tracking.completedOrderIds?.length || 0;
    const failed = tracking.failedOrderIds?.length || 0;
    const skipped = Number(tracking.skippedRecentCount || 0);
    const mode = tracking.source === "history" ? " Track all" : tracking.source === "single" ? " Single order" : tracking.source === "payment_recheck" ? " Payment recheck" : tracking.source === "manual" ? " Queued orders" : "";
    const reason = tracking.source === "history"
      ? "Stopped because Track all finished the selected pages and queue."
      : tracking.source === "single"
        ? `Stopped because Amazon order ${tracking.singleOrderId || ""} finished.`
      : tracking.source === "payment_recheck"
        ? "Stopped because all open payment revision orders were rechecked."
      : tracking.source === "manual"
        ? "Stopped because all queued Amazon orders finished."
      : "Stopped because no more eligible open Amazon orders were left.";
    return `${reason} All tracking codes scanned.${mode} Checked ${completed} order(s), failed ${failed}${skipped ? `, skipped recent ${skipped}` : ""}.`;
  }
  return "Stopped";
}

function settingsPayload() {
  return {
    type: "SET_API_BASE",
    apiBase: apiBase.value.trim(),
    adminToken: adminToken.value.trim(),
    headlessTrackingMode: headlessTrackingMode.checked,
    autoTrackingEnabled: autoTrackingEnabled.checked,
    autoTrackingHours: Number(autoTrackingHours.value || 3),
    trackAllStartPage: Number(trackAllStartPage.value || 1),
    trackAllMaxPages: Number(trackAllMaxPages.value || 202),
  };
}

function parseAmazonOrderIds(value) {
  const matches = String(value || "").match(/\b\d{3}-\d{7}-\d{7}\b/g) || [];
  return Array.from(new Set(matches.map((item) => item.trim()))).filter(Boolean);
}

async function refresh() {
  if (!targetWindowId) await resolveTargetWindowId();
  const state = await send({ type: "GET_STATE" });
  syncSettingsInputs(state);
  const tracking = state.tracking || {};
  const trackAllRunning = tracking.running === true && tracking.source === "history";
  const trackAllResumable = canResumeTrackAll(tracking);
  if (headlessTrackingMode.checked) {
    try {
      const headless = await send({ type: "GET_HEADLESS_TRACKING_STATUS" });
      const running = trackAllRunning || headlessRunning(headless.progress || {});
      setRunButtons(running, trackAllRunning, trackAllResumable);
      applyStatusFromRefresh(trackAllRunning ? visibleProgressText(tracking) : trackAllResumable ? stoppedTrackAllText(tracking) : tracking.finishedAt ? finalTrackingText(tracking) : progressText(headless.progress || {}));
    } catch (error) {
      setRunButtons(trackAllRunning, trackAllRunning, trackAllResumable);
      applyStatusFromRefresh(trackAllResumable ? stoppedTrackAllText(tracking) : error.message || "Could not load headless tracking status.");
    }
  } else {
    setRunButtons(tracking.running, trackAllRunning, trackAllResumable);
    applyStatusFromRefresh(tracking.running ? visibleProgressText(tracking) : trackAllResumable ? stoppedTrackAllText(tracking) : finalTrackingText(tracking));
  }
  logsBox.innerHTML = "";
  for (const line of state.logs || []) {
    const item = document.createElement("li");
    item.textContent = line;
    logsBox.append(item);
  }
}

document.querySelector("#save").addEventListener("click", async () => {
  const result = await send(settingsPayload());
  if (result.ok) settingsDirty = false;
  setResultStatus(result, "Saved.", "Save failed.");
});

document.querySelector("#testConnection").addEventListener("click", async () => {
  try {
    await send(settingsPayload());
    settingsDirty = false;
    const result = await send({ type: "TEST_CONNECTION" });
    setResultStatus(result, "Connection successful.", "Connection failed.");
  } catch (error) {
    setErrorStatus(error);
  }
});

document.querySelector("#openHeadlessSignin").addEventListener("click", async () => {
  await send(settingsPayload());
  settingsDirty = false;
  const result = await send({ type: "OPEN_HEADLESS_SIGNIN" });
  setResultStatus(result, "Opened headless sign-in.", "Could not open headless sign-in.");
});

document.querySelector("#checkHeadlessSignin").addEventListener("click", async () => {
  await send(settingsPayload());
  settingsDirty = false;
  setStatus("Checking headless Amazon session...", "info");
  const result = await send({ type: "CHECK_HEADLESS_TRACKING_READINESS" });
  setResultStatus({ ...result, ok: result.ready !== false && result.ok !== false }, "Headless Amazon session is ready.", "Headless Amazon session is not ready.");
});

document.querySelector("#reloadExtension").addEventListener("click", () => {
  setStatus("Reloading extension...", "info");
  setTimeout(() => chrome.runtime.reload(), 50);
});

document.querySelector("#start").addEventListener("click", async () => {
  await send(settingsPayload());
  settingsDirty = false;
  const result = await send({
    type: headlessTrackingMode.checked
        ? "START_HEADLESS_TRACKING"
        : "START_TRACKING",
  });
  if (result.ok !== false) setRunButtons(true, false, false);
  setResultStatus(result, "Started.", "Could not start.");
});

trackSingleOrderButton.addEventListener("click", async () => {
  await send(settingsPayload());
  settingsDirty = false;
  const orderId = singleAmazonOrderId.value.trim();
  if (!/^\d{3}-\d{7}-\d{7}$/.test(orderId)) {
    setStatus("Enter a valid Amazon order number like 113-0000000-0000000.", "error", { hold: "forever" });
    return;
  }
  const result = await send({ type: "START_SINGLE_ORDER_TRACKING", amazonOrderId: orderId });
  if (result.ok !== false) setRunButtons(true);
  setResultStatus(result, `Started tracking ${orderId}.`, "Could not start single-order tracking.");
});

trackQueuedOrdersButton.addEventListener("click", async () => {
  await send(settingsPayload());
  settingsDirty = false;
  const orderIds = parseAmazonOrderIds(`${singleAmazonOrderId.value}\n${amazonOrderBatch.value}`);
  if (!orderIds.length) {
    setStatus("Paste at least one valid Amazon order number like 113-0000000-0000000.", "error", { hold: "forever" });
    return;
  }
  const result = await send({ type: "START_MANUAL_ORDER_QUEUE_TRACKING", amazonOrderIds: orderIds });
  if (result.ok !== false) setRunButtons(true);
  setResultStatus(result, `Started tracking ${orderIds.length} queued order(s).`, "Could not start queued-order tracking.");
});

recheckPaymentFailuresButton.addEventListener("click", async () => {
  await send(settingsPayload());
  settingsDirty = false;
  setStatus("Loading open payment revision orders from the app...", "info");
  const result = await send({ type: "START_PAYMENT_FAILURE_RECHECK" });
  if (result.ok !== false && result.count) setRunButtons(true);
  setResultStatus(result, `Started rechecking ${result.count || 0} payment revision order(s).`, "Could not start payment recheck.");
});

document.querySelector("#trackAll").addEventListener("click", async () => {
  await send(settingsPayload());
  settingsDirty = false;
  const result = await send({
    type: "START_TRACK_ALL",
    startPage: Number(trackAllStartPage.value || 1),
    maxPages: Number(trackAllMaxPages.value || 202),
  });
  if (result.ok !== false) setRunButtons(true, true, false);
  setResultStatus(result, "Track all started.", "Could not start Track all.");
});

document.querySelector("#resumeTrackAll").addEventListener("click", async () => {
  const result = await send({ type: "RESUME_TRACK_ALL" });
  if (result.ok !== false) setRunButtons(true, true, false);
  setResultStatus(result, "Track all resumed.", "Could not resume Track all.");
});

document.querySelector("#stopTrackAll").addEventListener("click", async () => {
  const result = await send({ type: "STOP_TRACKING" });
  if (result.ok !== false) setRunButtons(false, false, true);
  setResultStatus(result, "Track all stopped.", "Could not stop Track all.");
});

document.querySelector("#stop").addEventListener("click", async () => {
  const state = await send({ type: "GET_STATE" });
  const isTrackAll = state?.tracking?.running && state.tracking.source === "history";
  const result = await send({ type: headlessTrackingMode.checked && !isTrackAll ? "STOP_HEADLESS_TRACKING" : "STOP_TRACKING" });
  if (result.ok !== false) setRunButtons(false);
  setResultStatus(result, "Stopped.", "Could not stop.");
});

updateModeNotice();
setRunButtons(false, false, false);
refresh();
setInterval(refresh, 3000);
