const apiBase = document.querySelector("#apiBase");
const adminToken = document.querySelector("#adminToken");
const cardLast4Preference = document.querySelector("#cardLast4Preference");
const deliveryLimitDays = document.querySelector("#deliveryLimitDays");
const editExistingAddress = document.querySelector("#editExistingAddress");
const fulfilAvailableMixedAsin = document.querySelector("#fulfilAvailableMixedAsin");
const splitMixedAsinOrders = document.querySelector("#splitMixedAsinOrders");
const browserlessOrderMode = document.querySelector("#browserlessOrderMode");
const pauseBeforePlaceOrder = document.querySelector("#pauseBeforePlaceOrder");
const preferRewardedLaterDelivery = document.querySelector("#preferRewardedLaterDelivery");
const connectionNotice = document.querySelector("#connectionNotice");
const modeNotice = document.querySelector("#modeNotice");
const statusBox = document.querySelector("#status");
const orderProgressCounter = document.querySelector("#orderProgressCounter");
const orderProgressBar = document.querySelector("#orderProgressBar");
const orderProgressDetail = document.querySelector("#orderProgressDetail");
const logsBox = document.querySelector("#logs");
const pricingRows = document.querySelector("#pricingRows");
const pricingTotal = document.querySelector("#pricingTotal");
const pauseResume = document.querySelector("#pauseResume");
const skipJob = document.querySelector("#skipJob");
const markMissing = document.querySelector("#markMissing");
const forceStop = document.querySelector("#forceStop");
const forceClearQueue = document.querySelector("#forceClearQueue");
const queuedJobs = document.querySelector("#queuedJobs");
const queueCount = document.querySelector("#queueCount");
const duplicateOrderAlert = document.querySelector("#duplicateOrderAlert");
const duplicateOrderRows = document.querySelector("#duplicateOrderRows");
const resetDuplicateFulfilment = document.querySelector("#resetDuplicateFulfilment");
const lastOrderProcessed = document.querySelector("#lastOrderProcessed");
const lastOrderReason = document.querySelector("#lastOrderReason");
const currentOrderProcessing = document.querySelector("#currentOrderProcessing");
const currentOrderStage = document.querySelector("#currentOrderStage");
const nextOrderProcessing = document.querySelector("#nextOrderProcessing");
const nextOrderStage = document.querySelector("#nextOrderStage");
const diagnosticCount = document.querySelector("#diagnosticCount");
const exportDiagnostics = document.querySelector("#exportDiagnostics");
const clearDiagnostics = document.querySelector("#clearDiagnostics");
const extensionVersion = document.querySelector("#extensionVersion");

if (extensionVersion) {
  extensionVersion.textContent = `v${chrome.runtime.getManifest().version}`;
}

function send(message) {
  return chrome.runtime.sendMessage({ ...message, targetWindowId });
}

const popupParams = new URLSearchParams(location.search);
let targetWindowId = Number(popupParams.get("targetWindowId") || 0) || null;
let controlWindowId = null;
let settingsDirty = false;
let settingsHydrated = false;
let settingsHydratePromise = null;

function hydrateSettingsValues(settings = {}) {
  apiBase.value = settings.apiBase || "http://127.0.0.1:8000";
  adminToken.value = settings.adminToken || "";
  cardLast4Preference.value = settings.cardLast4Preference || "";
  deliveryLimitDays.value = String(Math.min(30, Math.max(1, Math.floor(Number(settings.deliveryLimitDays) || 5))));
  editExistingAddress.checked = settings.editExistingAddress !== false;
  fulfilAvailableMixedAsin.checked = settings.fulfilAvailableMixedAsin === true;
  splitMixedAsinOrders.checked = settings.splitMixedAsinOrders !== false;
  browserlessOrderMode.checked = settings.browserlessOrderMode === true;
  pauseBeforePlaceOrder.checked = settings.pauseBeforePlaceOrder === true;
  preferRewardedLaterDelivery.checked = settings.preferRewardedLaterDelivery === true;
  updateModeNotice();
}

function hasSettingsPayload(state) {
  return Boolean(state && typeof state === "object" && (
    Object.hasOwn(state, "apiBase") ||
    Object.hasOwn(state, "adminToken") ||
    Object.hasOwn(state, "cardLast4Preference") ||
    Object.hasOwn(state, "deliveryLimitDays") ||
    Object.hasOwn(state, "editExistingAddress") ||
    Object.hasOwn(state, "fulfilAvailableMixedAsin") ||
    Object.hasOwn(state, "splitMixedAsinOrders") ||
    Object.hasOwn(state, "browserlessOrderMode") ||
    Object.hasOwn(state, "pauseBeforePlaceOrder") ||
    Object.hasOwn(state, "preferRewardedLaterDelivery")
  ));
}

function loadSavedSettings() {
  settingsHydratePromise = new Promise((resolve) => {
    chrome.storage.local.get({
      apiBase: "http://127.0.0.1:8000",
      adminToken: "",
      cardLast4Preference: "",
      deliveryLimitDays: 5,
      editExistingAddress: true,
      fulfilAvailableMixedAsin: false,
      splitMixedAsinOrders: true,
      browserlessOrderMode: false,
      pauseBeforePlaceOrder: false,
      preferRewardedLaterDelivery: false,
    }, (settings) => {
      if (!chrome.runtime.lastError && !settingsHydrated && !settingsDirty && ![apiBase, adminToken, cardLast4Preference, deliveryLimitDays, editExistingAddress, fulfilAvailableMixedAsin, splitMixedAsinOrders, browserlessOrderMode, pauseBeforePlaceOrder, preferRewardedLaterDelivery].includes(document.activeElement)) {
        hydrateSettingsValues(settings);
        settingsHydrated = true;
      }
      resolve(settings);
    });
  });
  return settingsHydratePromise;
}

async function waitForSettingsHydration() {
  if (settingsHydrated || settingsDirty) return;
  if (settingsHydratePromise) await settingsHydratePromise;
}

function registerControlWindow() {
  if (!chrome.windows?.getCurrent) return;
  chrome.windows.getCurrent((windowInfo) => {
    if (chrome.runtime.lastError) return;
    controlWindowId = Number(windowInfo?.id || 0) || null;
    if (!controlWindowId) return;
    chrome.runtime.sendMessage({ type: "REGISTER_CONTROL_WINDOW", controlWindowId, targetWindowId });
  });
}

[apiBase, adminToken, cardLast4Preference, deliveryLimitDays, editExistingAddress, fulfilAvailableMixedAsin, splitMixedAsinOrders, browserlessOrderMode, pauseBeforePlaceOrder, preferRewardedLaterDelivery].forEach((input) => {
  input.addEventListener("input", () => {
    settingsDirty = true;
  });
  input.addEventListener("change", () => {
    settingsDirty = true;
  });
});

function updateModeNotice() {
  if (!modeNotice) return;
  if (browserlessOrderMode.checked) {
    modeNotice.hidden = false;
    modeNotice.textContent = "Background/headless mode is selected. Start will use the separate backend Chrome runner and will not use the Amazon API.";
  } else {
    modeNotice.hidden = false;
    modeNotice.textContent = "Visible Chrome mode is selected. Start will open an Amazon worker window and use the proven extension pipeline.";
  }
}

browserlessOrderMode.addEventListener("change", updateModeNotice);

function syncSettingsInputs(state) {
  if (!hasSettingsPayload(state) || settingsHydrated || settingsDirty || [apiBase, adminToken, cardLast4Preference, deliveryLimitDays, editExistingAddress, fulfilAvailableMixedAsin, splitMixedAsinOrders, browserlessOrderMode, pauseBeforePlaceOrder, preferRewardedLaterDelivery].includes(document.activeElement)) return;
  hydrateSettingsValues(state);
  settingsHydrated = true;
}

function settingsPayload() {
  return {
    apiBase: apiBase.value.trim(),
    adminToken: adminToken.value.trim(),
    cardLast4Preference: cardLast4Preference.value.trim(),
    deliveryLimitDays: Math.min(30, Math.max(1, Math.floor(Number(deliveryLimitDays.value) || 5))),
    editExistingAddress: editExistingAddress.checked,
    fulfilAvailableMixedAsin: fulfilAvailableMixedAsin.checked,
    splitMixedAsinOrders: splitMixedAsinOrders.checked,
    browserlessOrderMode: browserlessOrderMode.checked,
    pauseBeforePlaceOrder: pauseBeforePlaceOrder.checked,
    preferRewardedLaterDelivery: preferRewardedLaterDelivery.checked,
  };
}

function setStatus(text) {
  statusBox.textContent = text;
}

function setConnectionNotice(text, success = true) {
  connectionNotice.textContent = text || "";
  connectionNotice.hidden = !text;
  connectionNotice.classList.toggle("error", !success);
}

function money(value) {
  const number = Number(value || 0);
  return number.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

function renderPricing(activeJob) {
  pricingRows.innerHTML = "";
  const items = activeJob?.job?.items || [];
  const pricing = activeJob?.pricing || {};
  let total = 0;
  for (const item of items) {
    const rowPricing = pricing[item.asin] || {};
    const qty = Number(item.quantity || 1);
    const storeTotal = Number(item.store_total_price ?? rowPricing.store_total_price ?? 0);
    const amazonTotal = Number(rowPricing.amazon_total_price || 0);
    const profit = amazonTotal ? storeTotal - amazonTotal : 0;
    total += profit;
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${item.asin}</td>
      <td>${qty}</td>
      <td>${money(storeTotal)}</td>
      <td>${amazonTotal ? money(amazonTotal) : "Pending"}</td>
      <td class="${profit >= 0 ? "profit" : "loss"}">${amazonTotal ? money(profit) : "Pending"}</td>
    `;
    pricingRows.append(row);
  }
  pricingTotal.textContent = money(total);
  pricingTotal.className = total >= 0 ? "profit" : "loss";
}

function renderDuplicateOrder(activeJob) {
  const duplicate = activeJob?.duplicateOrder;
  const orders = Array.isArray(duplicate?.orders) ? duplicate.orders : [];
  duplicateOrderRows.innerHTML = "";
  duplicateOrderAlert.hidden = !orders.length;
  resetDuplicateFulfilment.disabled = !orders.length;
  if (!orders.length) return;
  for (const order of orders) {
    const orderId = String(order.amazon_order_id || "").trim();
    const orderUrl = String(order.amazon_order_url || (orderId ? `https://www.amazon.com/your-orders/order-details?orderID=${encodeURIComponent(orderId)}` : "")).trim();
    const row = document.createElement("div");
    row.className = "duplicate-order-row";
    const label = document.createElement("span");
    label.textContent = `${order.order_name || "App order"}: `;
    const link = document.createElement("a");
    link.href = orderUrl || "#";
    link.textContent = orderId || "Open Amazon order";
    link.title = orderUrl || orderId;
    link.addEventListener("click", (event) => {
      event.preventDefault();
      if (orderUrl) chrome.tabs.create({ url: orderUrl });
    });
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "icon-button";
    copy.title = "Copy Amazon order number";
    copy.textContent = "Copy";
    copy.addEventListener("click", async () => {
      await navigator.clipboard.writeText(orderId);
      copy.textContent = "OK";
      setTimeout(() => {
        copy.textContent = "Copy";
      }, 1200);
    });
    row.append(label, link, copy);
    duplicateOrderRows.append(row);
  }
}

function shortWorkerId(value) {
  const text = String(value || "");
  return text.length > 18 ? `${text.slice(0, 10)}...${text.slice(-5)}` : text;
}

function renderQueuedJobs(jobs, workerId = "") {
  const queue = Array.isArray(jobs) ? jobs : [];
  const locked = queue.filter((job) => job.claimed_by).length;
  queueCount.textContent = locked ? `${queue.length} queued · ${locked} locked` : `${queue.length} waiting`;
  queuedJobs.innerHTML = "";
  if (!queue.length) {
    const empty = document.createElement("div");
    empty.className = "empty-queue";
    empty.textContent = "No queued Chrome orders.";
    queuedJobs.append(empty);
    return;
  }
  for (const job of queue) {
    const itemCount = (job.items || []).reduce((total, item) => total + Number(item.quantity || 0), 0);
    const asins = (job.items || []).map((item) => item.asin).filter(Boolean).join(", ");
    const claimedBy = String(job.claimed_by || "");
    const isLocked = Boolean(claimedBy);
    const isMine = isLocked && claimedBy === workerId;
    const lockText = isLocked
      ? `${isMine ? "Locked by this Chrome" : "Locked by another Chrome"}${job.claim_expires_at ? ` until ${new Date(job.claim_expires_at).toLocaleTimeString()}` : ""}`
      : "Ready";
    const row = document.createElement("div");
    row.className = `queued-job${isLocked ? " locked" : ""}${job.back_in_stock ? " back-in-stock" : ""}`;
    row.innerHTML = `
      <strong>${isLocked ? `<span class="lock-icon" title="${lockText}" aria-label="${lockText}"></span>` : ""}${(job.order_names || []).join(", ") || job.group_key}${job.back_in_stock ? `<span class="queue-badge">Back in stock</span>` : ""}</strong>
      <span>${job.recipient_name || "No recipient"} · ${itemCount || 0} item(s)</span>
      <span>${asins || "No ASINs"}</span>
      <span class="lock-status">${lockText}${isLocked && !isMine ? ` · ${shortWorkerId(claimedBy)}` : ""}</span>
    `;
    queuedJobs.append(row);
  }
}

function orderLabelFromJob(job = {}) {
  const orderNames = (job.order_names || []).map((name) => String(name || "").trim()).filter(Boolean);
  return orderNames.join(", ") || job.group_key || "Unknown order";
}

function orderAsins(job = {}) {
  return (job.items || []).map((item) => item.asin).filter(Boolean).join(", ");
}

function activityReason(text = "", fallback = "") {
  const cleaned = String(text || fallback || "").replace(/\s+/g, " ").trim();
  if (!cleaned) return "";
  return cleaned.length > 140 ? `${cleaned.slice(0, 137)}...` : cleaned;
}

function renderActivitySummary(state = {}, queue = null) {
  const last = state.fulfilmentActivity?.last || null;
  const current = state.activeJob?.job || null;
  const currentGroupKey = current?.group_key || "";
  const next = (queue?.jobs || []).find((job) => job?.group_key && job.group_key !== currentGroupKey && !job.claimed_by)
    || (queue?.jobs || []).find((job) => job?.group_key && job.group_key !== currentGroupKey)
    || null;

  lastOrderProcessed.textContent = last?.label || "None";
  lastOrderReason.textContent = last
    ? `${last.status || "processed"}${last.reason ? ` · ${activityReason(last.reason)}` : ""}`
    : "No processed order yet.";

  currentOrderProcessing.textContent = current ? orderLabelFromJob(current) : "None";
  currentOrderStage.textContent = current
    ? `${state.activeJob?.paused ? "Paused" : "Working"} · ${state.activeJob?.stage || "active"}${orderAsins(current) ? ` · ${orderAsins(current)}` : ""}`
    : state.forceStop?.active
      ? "Force stopped. No active order will continue."
      : "No active order.";

  nextOrderProcessing.textContent = next ? orderLabelFromJob(next) : "None";
  nextOrderStage.textContent = next
    ? `${next.claimed_by ? "Locked" : "Ready"}${orderAsins(next) ? ` · ${orderAsins(next)}` : ""}`
    : "No queued order loaded.";
}

function queueCountDetails(counts = []) {
  const extras = (Array.isArray(counts) ? counts : [])
    .filter((item) => item.state !== "submitted" && Number(item.count || 0) > 0)
    .map((item) => `${item.count} ${item.state}`)
  return extras.length ? ` · ${extras.join(" · ")}` : "";
}

function submittedQueueCount(counts = [], fallback = 0) {
  const submitted = (Array.isArray(counts) ? counts : []).find((item) => item.state === "submitted");
  const count = Number(submitted?.count);
  return Number.isFinite(count) ? count : fallback;
}

function renderQueueStatus(queue, state) {
  renderQueuedJobs(queue.jobs || [], queue.workerId || state.workerId || "");
  const jobCount = Number(queue.job_count);
  const waiting = Number.isFinite(jobCount) ? jobCount : submittedQueueCount(queue.counts || [], (queue.jobs || []).length);
  queueCount.textContent = `${waiting} job${waiting === 1 ? "" : "s"} waiting`;
  queueCount.textContent += queueCountDetails(queue.counts || []);
  if (queue.stale) {
    queueCount.textContent += " · last loaded";
  }
}

function renderOrderProgress(progress = {}, queue = null, activeJob = null) {
  const processed = Math.max(0, Math.round(Number(progress.processed || 0)));
  const jobCount = Number(queue?.job_count);
  const queuedJobs = Number.isFinite(jobCount)
    ? jobCount
    : submittedQueueCount(queue?.counts || [], queue?.jobs?.length || 0);
  const liveTotal = Number.isFinite(queuedJobs) ? processed + queuedJobs + (activeJob?.job ? 1 : 0) : 0;
  const total = Math.max(processed, liveTotal || Math.round(Number(progress.total || 0)));
  const percent = total ? Math.min(100, Math.round((processed / total) * 100)) : 0;
  const activeLabel = activeJob?.job?.group_key ? `Active: ${activeJob.job.group_key}` : "";
  const detail = progress.message || activeLabel || (total ? "Order run in progress." : "No order run started.");
  orderProgressCounter.textContent = `${processed} / ${total} processed`;
  orderProgressBar.style.width = `${percent}%`;
  orderProgressDetail.textContent = detail;
}

function renderDiagnosticCount(diagnosticSessions = {}) {
  const sessions = Array.isArray(diagnosticSessions.sessions) ? diagnosticSessions.sessions : [];
  diagnosticCount.textContent = `${sessions.length} session${sessions.length === 1 ? "" : "s"}`;
}

function formatDiagnosticExport(diagnosticSessions = {}) {
  const sessions = Array.isArray(diagnosticSessions.sessions) ? diagnosticSessions.sessions : [];
  const lines = [
    "Nutricity Fulfilment Diagnostics",
    `Exported: ${new Date().toISOString()}`,
    `Sessions: ${sessions.length}`,
    "",
  ];
  for (const session of sessions) {
    lines.push("=".repeat(80));
    lines.push(`Session: ${session.id || "unknown"}`);
    lines.push(`Group: ${session.groupKey || ""}`);
    lines.push(`Orders: ${(session.orderNames || []).join(", ")}`);
    lines.push(`Window: ${session.windowId || ""}`);
    lines.push(`Started: ${session.startedAt ? new Date(session.startedAt).toISOString() : ""}`);
    lines.push(`Updated: ${session.updatedAt ? new Date(session.updatedAt).toISOString() : ""}`);
    lines.push("");
    for (const entry of session.entries || []) {
      lines.push(`[${entry.time || new Date(entry.at || Date.now()).toISOString()}] ${entry.level || "info"} ${entry.source || ""}`);
      lines.push(`Message: ${entry.message || ""}`);
      if (entry.url || entry.page?.url) lines.push(`URL: ${entry.url || entry.page.url}`);
      if (entry.job) lines.push(`Job: ${JSON.stringify(entry.job)}`);
      if (entry.details) lines.push(`Details: ${JSON.stringify(entry.details)}`);
      lines.push("");
    }
  }
  return lines.join("\n");
}

async function refresh() {
  if (refreshInFlight) {
    refreshRequested = true;
    return;
  }
  refreshInFlight = true;
  try {
    const state = await send({ type: "GET_STATE" });
    if (state?.ok === false) {
      setStatus(state.message || "Could not load extension state.");
      return;
    }
    syncSettingsInputs(state);
    const job = state.activeJob?.job;
    pauseResume.textContent = state.activeJob?.paused ? "Resume" : "Pause";
    updateModeNotice();
    pauseResume.disabled = !job;
    skipJob.disabled = !job;
    markMissing.disabled = !job;
    forceStop.disabled = false;
    setStatus(job ? `${state.activeJob.paused ? "Paused" : "Active"}: ${job.group_key} (${state.activeJob.stage})` : "No active job.");
    renderDuplicateOrder(state.activeJob);
    renderPricing(state.activeJob);
    renderDiagnosticCount(state.diagnosticSessions || {});
    let browserlessProgress = null;
    if ((browserlessOrderMode.checked || state.orderProgress?.source === "browserless") && !job) {
      try {
        const browserless = await send({ type: "GET_BROWSERLESS_STATUS" });
        const progress = browserless?.progress || {};
        browserlessProgress = progress;
        if (progress.message) setStatus(progress.message);
      } catch {
        // Queue status below will still show connection errors if the app is down.
      }
    }
    logsBox.innerHTML = "";
    for (const line of state.logs || []) {
      const item = document.createElement("li");
      item.textContent = line;
      logsBox.append(item);
    }
    try {
      const queue = await send({ type: "GET_QUEUE_STATUS" });
      if (!queue.ok) throw new Error(queue.message || "Could not load queue.");
      renderQueueStatus(queue, state);
      renderActivitySummary(state, queue);
      renderOrderProgress(browserlessProgress || state.orderProgress || {}, queue, state.activeJob);
      if (queue.message && /Connected to app/i.test(queue.message)) setConnectionNotice(queue.message, true);
      if (queue.stale && queue.message) setStatus(queue.message);
    } catch (error) {
      queueCount.textContent = "Not loaded";
      renderActivitySummary(state, null);
      renderOrderProgress(browserlessProgress || state.orderProgress || {}, null, state.activeJob);
      queuedJobs.innerHTML = `<div class="empty-queue">${error.message || "Could not load queue. Check App URL, Admin token, then click Save."}</div>`;
    }
  } finally {
    refreshInFlight = false;
    if (refreshRequested) {
      refreshRequested = false;
      setTimeout(refresh, 0);
    }
  }
}

document.querySelector("#save").addEventListener("click", async () => {
  const result = await send({ type: "SET_API_BASE", ...settingsPayload() });
  if (result.ok) settingsDirty = false;
  setStatus(result.ok ? "Saved." : result.message);
});

document.querySelector("#testConnection").addEventListener("click", async () => {
  await send({ type: "SET_API_BASE", ...settingsPayload() });
  settingsDirty = false;
  const result = await send({ type: "TEST_CONNECTION" });
  if (result.ok) {
    setConnectionNotice("Connection successful.", true);
  } else {
    setConnectionNotice("", false);
  }
  setStatus(result.message || (result.ok ? "Connection ok." : "Connection failed."));
});

document.querySelector("#openHeadlessSession").addEventListener("click", async () => {
  await send({ type: "SET_API_BASE", ...settingsPayload() });
  settingsDirty = false;
  const result = await send({ type: "OPEN_BROWSERLESS_SESSION" });
  setStatus(result.message || (result.ok ? "Opened headless session." : "Could not open headless session."));
});

document.querySelector("#reloadExtension").addEventListener("click", async () => {
  // Reloading the service worker while an Amazon job is running can sever the
  // live checkout tab from its reporting state. Never allow an update reload
  // to interrupt a checkout or the crucial order-ID reporting step.
  const state = await send({ type: "GET_STATE" }).catch(() => null);
  const activeJob = state?.activeJob;
  if (activeJob?.job?.group_key) {
    const stage = activeJob.paused ? (activeJob.pausedStage || activeJob.stage) : activeJob.stage;
    setStatus(`Cannot reload while ${activeJob.job.group_key} is active (${stage || "working"}). Finish or stop the order first.`);
    return;
  }
  setStatus("Reloading extension...");
  setTimeout(() => chrome.runtime.reload(), 50);
});

const startNextButton = document.querySelector("#start");

startNextButton.addEventListener("click", async () => {
  startNextButton.disabled = true;
  setStatus(browserlessOrderMode.checked ? "Starting background order placement..." : "Starting next queued order...");
  try {
    await send({ type: "SET_API_BASE", ...settingsPayload() });
    settingsDirty = false;
    await send({ type: "CLEAR_FORCE_STOP" });
    const result = await send({ type: browserlessOrderMode.checked ? "START_BROWSERLESS" : "START_NEXT" });
    if (result.targetWindowId) {
      targetWindowId = result.targetWindowId;
      registerControlWindow();
    }
    const groupKey = result?.activeJob?.job?.group_key || result?.next_group_key || "";
    setStatus(
      result.message
      || (result.ok && groupKey
        ? `Working on ${groupKey}. The Amazon worker is running in the background.`
        : result.ok ? "Order worker started in the background." : "Could not start."),
    );
    await refresh();
  } catch (error) {
    setStatus(error.message || "Could not start queued order.");
  } finally {
    startNextButton.disabled = false;
  }
});

document.querySelector("#stop").addEventListener("click", async () => {
  const result = await send({ type: "STOP_JOB" });
  setStatus(result.message);
});

forceStop.addEventListener("click", async () => {
  const confirmed = confirm("Force stop all Chrome fulfilment now? This immediately stops local processing, closes worker windows, releases active locks where safe, and ignores late reports. It does not clear or delete the order queue.");
  if (!confirmed) return;
  forceStop.disabled = true;
  setStatus("Force stopping all fulfilment activity...");
  const result = await send({ type: "FORCE_STOP_ALL" });
  setStatus(result.message || (result.ok ? "Force stopped." : "Could not force stop."));
  await refresh();
});

forceClearQueue.addEventListener("click", async () => {
  const confirmed = confirm("Force clear the Chrome order queue now? This stops local processing and removes all unsubmitted Chrome queue jobs from the queue. Amazon-submitted jobs waiting for order-number reporting are preserved.");
  if (!confirmed) return;
  forceClearQueue.disabled = true;
  setStatus("Force clearing Chrome order queue...");
  const result = await send({ type: "FORCE_CLEAR_QUEUE" });
  setStatus(result.message || (result.ok ? "Chrome order queue cleared." : "Could not clear Chrome order queue."));
  forceClearQueue.disabled = false;
  await refresh();
});

pauseResume.addEventListener("click", async () => {
  const result = await send({ type: "TOGGLE_PAUSE" });
  setStatus(result.message || (result.ok ? "Updated." : "Could not pause/resume."));
});

skipJob.addEventListener("click", async () => {
  const confirmed = confirm("Skip this Chrome job without marking it missing or changing the order line?");
  if (!confirmed) return;
  skipJob.disabled = true;
  setStatus("Skipping current order and starting the next one...");
  const result = await send({ type: "SKIP_JOB" });
  setStatus(result.message || (result.ok ? "Skipped current job." : "Could not skip job."));
  await refresh();
});

markMissing.addEventListener("click", async () => {
  const confirmed = confirm("Mark this active Chrome job as Missing ASINs and start the next queued order?");
  if (!confirmed) return;
  markMissing.disabled = true;
  setStatus("Marking current order missing and starting the next one...");
  const result = await send({ type: "MARK_CURRENT_MISSING" });
  setStatus(result.message || (result.ok ? "Marked missing." : "Could not mark missing."));
  await refresh();
});

resetDuplicateFulfilment.addEventListener("click", async () => {
  const confirmed = confirm("Clear the existing Amazon order number from the app for this active Chrome job and resume fulfilment?");
  if (!confirmed) return;
  resetDuplicateFulfilment.disabled = true;
  setStatus("Clearing existing Amazon order from the app...");
  const result = await send({ type: "RESET_DUPLICATE_FULFILMENT" });
  setStatus(result.message || (result.ok ? "Cleared. Continue fulfilment." : "Could not clear existing Amazon order."));
  await refresh();
});

document.querySelector("#clearFailed").addEventListener("click", async () => {
  const confirmed = confirm("Clear failed or stale queued Chrome jobs in the app so they can be queued again?");
  if (!confirmed) return;
  const result = await send({ type: "CLEAR_FAILED_JOBS" });
  setStatus(result.message || (result.ok ? "Stale queue cleared." : "Could not clear stale queue."));
});

exportDiagnostics.addEventListener("click", async () => {
  const result = await send({ type: "GET_DIAGNOSTIC_LOGS" });
  if (!result?.ok) {
    setStatus(result?.message || "Could not load diagnostic logs.");
    return;
  }
  const text = formatDiagnosticExport(result.diagnosticSessions || {});
  const blobUrl = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const link = document.createElement("a");
  link.href = blobUrl;
  link.download = `nutricity-fulfilment-diagnostics-${stamp}.txt`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(blobUrl), 5000);
  setStatus("Diagnostic logs exported.");
});

clearDiagnostics.addEventListener("click", async () => {
  const confirmed = confirm("Clear all saved diagnostic logs from this extension?");
  if (!confirmed) return;
  const result = await send({ type: "CLEAR_DIAGNOSTIC_LOGS" });
  setStatus(result?.message || (result?.ok ? "Diagnostic logs cleared." : "Could not clear diagnostic logs."));
  await refresh();
});

loadSavedSettings();
registerControlWindow();
let refreshInFlight = false;
let refreshRequested = false;
refresh();
setInterval(refresh, 10000);

async function runPopupRecoveryAction() {
  const action = popupParams.get("action");
  if (!action) return;
  await waitForSettingsHydration();
  if (action === "stop-start") {
    const lockKey = "popupRecoveryAction";
    const lock = await chrome.storage.local.get({ [lockKey]: null });
    const recent = lock[lockKey] || {};
    const sameAction = recent.action === action;
    if (sameAction && Date.now() - Number(recent.startedAt || 0) < 60000) {
      setStatus("Recovery action is already running in another popup.");
      history.replaceState(null, "", location.pathname);
      return;
    }
    await chrome.storage.local.set({ [lockKey]: { action, startedAt: Date.now() } });
    setStatus("Stopping stale active job and starting the next queued order...");
    try {
      await send({ type: "STOP_JOB" }).catch(() => null);
      await send({ type: "CLEAR_FORCE_STOP" }).catch(() => null);
      const result = await send({ type: browserlessOrderMode.checked ? "START_BROWSERLESS" : "START_NEXT" });
      if (result?.targetWindowId) {
        targetWindowId = result.targetWindowId;
        registerControlWindow();
      }
      setStatus(result?.message || (result?.ok ? "Started." : "Could not start."));
      await refresh();
    } finally {
      history.replaceState(null, "", location.pathname);
      await chrome.storage.local.remove(lockKey);
    }
  }
}

setTimeout(() => {
  runPopupRecoveryAction().catch((error) => setStatus(error?.message || "Popup recovery action failed."));
}, 500);
