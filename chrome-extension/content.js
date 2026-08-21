(() => {
const CONTENT_SCRIPT_BUILD = "2026-08-22-unplaced-submit-reset-v82";
if (window.__nutricityContentLoaded === CONTENT_SCRIPT_BUILD) return;
if (typeof window.__nutricityContentCleanup === "function") {
  try {
    window.__nutricityContentCleanup();
  } catch (_) {
    // Continue loading this build even if an older content-script cleanup failed.
  }
}
window.__nutricityContentLoaded = CONTENT_SCRIPT_BUILD;
const contentCleanupFns = [];
function registerContentCleanup(cleanup) {
  if (typeof cleanup === "function") contentCleanupFns.push(cleanup);
}
window.__nutricityContentCleanup = () => {
  while (contentCleanupFns.length) {
    try {
      contentCleanupFns.pop()();
    } catch (_) {
      // Best-effort teardown between unpacked-extension reloads.
    }
  }
  if (window.__nutricityContentLoaded === CONTENT_SCRIPT_BUILD) {
    window.__nutricityContentLoaded = false;
  }
};

const rawSleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function fulfilmentPausedError() {
  const error = new Error("Fulfilment paused by user.");
  error.fulfilmentPaused = true;
  return error;
}
async function sleep(ms, options = {}) {
  const pauseAware = options.pauseAware !== false;
  const deadline = Date.now() + Math.max(0, Number(ms || 0));
  do {
    if (pauseAware) await waitIfPaused();
    await rawSleep(Math.min(250, Math.max(0, deadline - Date.now())));
  } while (Date.now() < deadline);
}
const ACTION_DELAY = 900;
const PAGE_READY_TIMEOUT = 12000;
const DEFAULT_NEW_DELIVERY_ADDRESS = {
  countryCode: "US",
  phoneNumber: "9176818556",
  addressLine1: "6614 AVENUE U STE 7",
  addressLine2: "",
  city: "BROOKLYN",
  stateOrRegion: "NY",
  postalCode: "11234-6021",
};

let extensionContextAlive = true;
let panelOrderStatusVersion = 0;
let orderHistoryAnnotationScheduled = false;
let orderHistoryAnnotationInFlight = false;
let orderHistoryAnnotationInFlightAt = 0;
let orderHistoryLastAnnotatedAt = 0;
let orderHistoryScrollTimer = null;
let orderHistoryAnnotationTimer = null;
const orderHistoryLookupCache = new Map();
const orderHistoryOdooDirectInFlight = new Set();
const orderHistorySyncInProgress = new Map();
const orderHistorySyncedConfirmations = new Map();
let fulfilmentForceStopped = false;
let runIntervalId = null;
let panelIntervalId = null;
let historyIntervalId = null;
let lastNoActiveJobCheckAt = 0;
const MAX_ORDER_HISTORY_CARDS_PER_PASS = 12;
const ORDER_HISTORY_CACHE_MS = 2 * 60 * 1000;
const ORDER_HISTORY_NOT_FOUND_CACHE_MS = 15 * 1000;
const IDLE_ACTIVE_JOB_POLL_MS = 30000;
// Product selection, cart stabilization, address editing, and checkout can
// legitimately take well over 25 seconds. Starting a second run while the
// first is still awaiting Amazon duplicates Add-to-cart clicks and quantities.
const CONTENT_RUN_STALE_MS = 3 * 60 * 1000;
const MAX_CART_VERIFICATION_RELOADS = 3;

const DEFAULT_API_BASE = "http://127.0.0.1:8000";
const LOCAL_ADMIN_TOKEN_FALLBACK = "1284";
const DEFAULT_DELIVERY_LIMIT_DAYS = 5;

function normalizedDeliveryLimitDays(value) {
  const parsed = Math.floor(Number(value));
  return Number.isFinite(parsed) && parsed >= 1 ? Math.min(parsed, 30) : DEFAULT_DELIVERY_LIMIT_DAYS;
}

function normalizeContentApiBase(value) {
  return String(value || DEFAULT_API_BASE).trim().replace(/\/+$/, "") || DEFAULT_API_BASE;
}

async function getContentApiSettings() {
  try {
    return await chrome.storage.local.get({ apiBase: DEFAULT_API_BASE, adminToken: "" });
  } catch (_) {
    return { apiBase: DEFAULT_API_BASE, adminToken: "" };
  }
}

async function contentApi(path, options = {}) {
  const { apiBase, adminToken } = await getContentApiSettings();
  const base = normalizeContentApiBase(apiBase);
  const requestPath = String(path || "").startsWith("/") ? path : `/${path}`;
  const { timeoutMs = 45000, ...fetchOptions } = options;
  const isLocalApi = /^https?:\/\/(?:127\.0\.0\.1|localhost)(?::|\/|$)/i.test(base);
  const authTokens = [
    adminToken,
    isLocalApi && adminToken !== LOCAL_ADMIN_TOKEN_FALLBACK ? LOCAL_ADMIN_TOKEN_FALLBACK : "",
  ].filter(Boolean);
  let response = null;
  for (let index = 0; index < Math.max(1, authTokens.length); index += 1) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), Number(timeoutMs || 45000));
    response = await fetch(`${base}${requestPath}`, {
      headers: {
        "Content-Type": "application/json",
        ...(authTokens[index] ? { "X-Admin-Token": authTokens[index] } : {}),
        ...(fetchOptions.headers || {}),
      },
      signal: controller.signal,
      ...fetchOptions,
    }).finally(() => clearTimeout(timeout));
    if (response.ok || response.status !== 401 || index === authTokens.length - 1) break;
  }
  if (!response.ok) {
    throw new Error((await response.text()) || response.statusText);
  }
  return response.json();
}

async function lookupAmazonHistoryOrdersFromContent(orders = []) {
  const normalized = (orders || []).filter((order) => order?.amazon_order_id);
  if (!normalized.length) return { ok: true, matches: {}, unmatched: [], not_found_url: "/amazon-order-history-unmatched" };
  const result = await contentApi("/api/chrome/order-history/lookup", {
    method: "POST",
    body: JSON.stringify({ orders: normalized }),
    timeoutMs: 25000,
  });
  const { apiBase } = await getContentApiSettings();
  const base = normalizeContentApiBase(apiBase);
  return {
    app_base_url: base,
    ...result,
    odoo_direct: result?.odoo_direct || result?.odooDirect || {},
    odoo_direct_error: result?.odoo_direct_error || result?.odooDirectError || "",
    not_found_url: `${base}${result.not_found_url || "/amazon-order-history-unmatched"}`,
  };
}

async function lookupAmazonHistoryOdooDirectFromContent(orders = []) {
  const normalized = (orders || []).filter((order) => order?.amazon_order_id);
  if (!normalized.length) return { ok: true, odoo_direct: {} };
  return contentApi("/api/chrome/order-history/odoo-direct", {
    method: "POST",
    body: JSON.stringify({ orders: normalized }),
    timeoutMs: 45000,
  });
}

async function send(message) {
  if (!extensionContextAlive) return null;
  try {
    return await chrome.runtime.sendMessage(message);
  } catch (error) {
    if (/Extension context invalidated/i.test(String(error?.message || error))) {
      extensionContextAlive = false;
      window.__nutricityContentLoaded = false;
      const key = "nutricity-extension-context-reload-at";
      const lastReloadAt = Number(sessionStorage.getItem(key) || 0);
      if (Date.now() - lastReloadAt > 10000) {
        sessionStorage.setItem(key, String(Date.now()));
        showPanel(
          "Nutricity fulfilment",
          "The Chrome extension context reset while this Amazon page was open. Reloading the page so fulfilment can recover the active order.",
          null,
          null,
        );
        setTimeout(() => location.reload(), 1200);
      }
      return null;
    }
    throw error;
  }
}

async function sendWithTimeout(message, timeoutMs = 20000) {
  let timeoutId = null;
  try {
    return await Promise.race([
      send(message),
      new Promise((resolve) => {
        timeoutId = setTimeout(() => resolve({ ok: false, message: "Chrome extension background request timed out." }), timeoutMs);
      }),
    ]);
  } catch (error) {
    return { ok: false, message: error?.message || String(error || "Chrome extension background request failed.") };
  } finally {
    if (timeoutId) clearTimeout(timeoutId);
  }
}

async function getActiveJob() {
  const data = await send({ type: "GET_ACTIVE_JOB" });
  if (data?.forceStopped || data?.stopped) {
    stopContentAutomation("Force stop is active.");
    return null;
  }
  if (data?.activeJob) return data.activeJob;
  if (!/amazon\.com$/i.test(location.hostname)) return null;
  const recovered = await send({ type: "RECOVER_SUBMITTED_JOB" });
  if (recovered?.forceStopped || recovered?.stopped) {
    stopContentAutomation("Force stop is active.");
    return null;
  }
  return recovered?.activeJob || null;
}

function stopContentAutomation(message = "Force stopped. No further fulfilment steps will run on this page.") {
  fulfilmentForceStopped = true;
  if (runIntervalId) clearInterval(runIntervalId);
  if (panelIntervalId) clearInterval(panelIntervalId);
  if (historyIntervalId) clearInterval(historyIntervalId);
  if (orderHistoryScrollTimer) clearTimeout(orderHistoryScrollTimer);
  if (orderHistoryAnnotationTimer) clearTimeout(orderHistoryAnnotationTimer);
  runIntervalId = null;
  panelIntervalId = null;
  historyIntervalId = null;
  orderHistoryScrollTimer = null;
  orderHistoryAnnotationTimer = null;
  orderHistoryAnnotationScheduled = false;
  window.__nutricityRunning = false;
  window.__nutricityRunningAt = 0;
  // Force stop is a global safety latch, not a page-level error. Amazon tabs
  // that are not running a job must remain untouched and completely idle.
  document.querySelector("#nutricity-panel")?.remove();
}

function ensureOrderHistoryAnnotationLoop() {
  if (fulfilmentForceStopped || historyIntervalId) return;
  historyIntervalId = setInterval(() => {
    if (fulfilmentForceStopped || document.hidden || !/amazon\.com$/i.test(location.hostname)) return;
    if (!isOrderHistoryPage() && !isOrderDetailsPage()) return;
    scheduleOrderHistoryAnnotation(1200);
  }, 15000);
  registerContentCleanup(() => clearInterval(historyIntervalId));
}

const STAGE_PROGRESS = {
  clear_cart: 5,
  cleanup_after_failure: 5,
  product: 10,
  add_clicked: 20,
  cart: 30,
  subscribe_checkout: 35,
  checkout: 40,
  editing_address: 45,
  complete_pending: 60,
  find_order_id: 70,
  reporting_complete: 80,
};

function stageProgress(stage) {
  return STAGE_PROGRESS[String(stage || "")] || 0;
}

function diagnosticPageInfo() {
  return {
    url: location.href,
    title: document.title || "",
    pathname: location.pathname,
    search: location.search,
  };
}

function sendDiagnostic(message, details = {}, level = "info") {
  return send({
    type: "DIAG_LOG",
    source: "content",
    level,
    message,
    page: diagnosticPageInfo(),
    details,
  });
}

async function setActiveJob(activeJob, options = {}) {
  let next = activeJob;
  let latest = null;
  if (activeJob?.job?.group_key) {
    latest = await getActiveJob();
    const latestPauseRevision = Number(latest?.pauseRevision || 0);
    const incomingPauseRevision = Number(activeJob.pauseRevision || 0);
    if (
      latest?.job?.group_key === activeJob.job.group_key
      && latestPauseRevision > incomingPauseRevision
    ) {
      // A run that began before the operator clicked Resume must never write
      // its stale paused=true snapshot back after the background worker has
      // already cleared the pause. That race made every navigation pause the
      // same order again and prevented the queue from advancing unattended.
      next = {
        ...activeJob,
        paused: Boolean(latest.paused),
        pausedStage: latest.pausedStage || null,
        pauseRevision: latestPauseRevision,
      };
      activeJob = next;
    }
  }
  if (activeJob?.job?.group_key) {
    latest = latest || await getActiveJob();
    const sameJob = latest?.job?.group_key === activeJob.job.group_key;
    const preserveUserPause = sameJob && latest.paused && latest.pausedByUser;
    const preserveOrdinaryPause = sameJob && options.allowUnpause !== true && latest.paused && !activeJob.paused;
    if (preserveUserPause || preserveOrdinaryPause) {
      next = {
        ...activeJob,
        paused: true,
        pausedStage: latest.pausedStage || activeJob.pausedStage || activeJob.stage || "product",
        pausedByUser: Boolean(latest.pausedByUser || activeJob.pausedByUser),
      };
    }
  }
  if (activeJob?.job?.group_key && options.allowStageRegression !== true) {
    latest = latest || await getActiveJob();
    if (latest?.job?.group_key && latest.job.group_key !== activeJob.job.group_key) {
      showPanel(
        "Nutricity fulfilment",
        `Ignored stale page update from ${activeJob.job.group_key}; current order is ${latest.job.group_key}.`,
        null,
        null,
      );
      await sendDiagnostic("Ignored stale active-job update from content page.", {
        incoming_group_key: activeJob.job.group_key,
        current_group_key: latest.job.group_key,
        incoming_stage: activeJob.stage || "",
        current_stage: latest.stage || "",
      }, "warn");
      return { ok: false, stale: true };
    }
    if (latest?.job?.group_key === activeJob.job.group_key) {
      const latestItems = Array.isArray(latest?.job?.items) ? latest.job.items : [];
      const incomingItems = Array.isArray(activeJob?.job?.items) ? activeJob.job.items : [];
      if (latestItems.length > incomingItems.length && options.allowItemRemoval !== true) {
        const preservedJob = {
          ...activeJob.job,
          items: latestItems,
          line_ids: latest.job.line_ids || activeJob.job.line_ids || [],
        };
        activeJob.job = preservedJob;
        next = { ...next, job: preservedJob };
        await sendDiagnostic("Prevented the content page from shrinking a multi-item active job.", {
          group_key: activeJob.job.group_key,
          latest_asins: latestItems.map((item) => item?.asin || ""),
          incoming_asins: incomingItems.map((item) => item?.asin || ""),
          reason: options.reason || "",
        }, "warn");
      }
      const latestIndex = Number(latest.itemIndex || 0);
      const nextIndex = Number(activeJob.itemIndex || 0);
      const latestProgress = stageProgress(latest.stage);
      const nextProgress = stageProgress(next.stage);
      const latestSubmitted = submittedStage(latest);
      const nextSubmitted = submittedStage(next);
      const latestReportedOrderId = String(latest.reportedOrderId || "").trim();
      const nextReportedOrderId = String(next.reportedOrderId || "").trim();
      const latestReportAttemptedAt = Number(latest.reportAttemptedAt || 0);
      const nextReportAttemptedAt = Number(next.reportAttemptedAt || 0);
      if (
        latest.stage === "reporting_complete"
        && /^\d{3}-\d{7}-\d{7}(?:\s*,\s*\d{3}-\d{7}-\d{7})*$/.test(latestReportedOrderId)
        && (!nextReportedOrderId || nextReportAttemptedAt < latestReportAttemptedAt)
      ) {
        next = {
          ...next,
          stage: "reporting_complete",
          reportedOrderId: latestReportedOrderId,
          reportAttemptedAt: latest.reportAttemptedAt,
          amazonSubmittedAt: next.amazonSubmittedAt || latest.amazonSubmittedAt || Date.now(),
        };
      }
      if (
        latestIndex >= nextIndex
        && latestProgress > nextProgress
        && !(latestSubmitted && nextSubmitted)
      ) {
        next = {
          ...latest,
          paused: next.paused || latest.paused || false,
          pausedStage: next.pausedStage || latest.pausedStage || null,
        };
      }
      if (latest.pricing && Object.keys(latest.pricing).length && (!next.pricing || !Object.keys(next.pricing).length)) {
        next = { ...next, pricing: latest.pricing };
      }
      if (latest.productDosages?.length && !next.productDosages?.length) {
        next = { ...next, productDosages: latest.productDosages };
      }
      if (latest.dosageByOrder && Object.keys(latest.dosageByOrder).length && (!next.dosageByOrder || !Object.keys(next.dosageByOrder).length)) {
        next = { ...next, dosageByOrder: latest.dosageByOrder };
      }
      if (latest.productPacks?.length && !next.productPacks?.length) {
        next = { ...next, productPacks: latest.productPacks };
      }
      if (latest.packByOrder && Object.keys(latest.packByOrder).length && (!next.packByOrder || !Object.keys(next.packByOrder).length)) {
        next = { ...next, packByOrder: latest.packByOrder };
      }
    }
  }
  if (activeJob?.job?.group_key) {
    latest = latest || await getActiveJob();
    if (latest?.job?.group_key && latest.job.group_key !== activeJob.job.group_key) {
      showPanel(
        "Nutricity fulfilment",
        `Ignored stale page update from ${activeJob.job.group_key}; current order is ${latest.job.group_key}.`,
        null,
        null,
      );
      await sendDiagnostic("Ignored stale active-job update from content page.", {
        incoming_group_key: activeJob.job.group_key,
        current_group_key: latest.job.group_key,
        incoming_stage: activeJob.stage || "",
        current_stage: latest.stage || "",
      }, "warn");
      return { ok: false, stale: true };
    }
  }
  // If Pause was clicked while this flow was awaiting a background response,
  // abort before it can perform the next click or location change. A user pause
  // always wins even over narrowly scoped automatic-unpause recovery paths.
  if (next.paused && next.pausedByUser && !activeJob.pausedByUser) {
    throw fulfilmentPausedError();
  }
  return send({ type: "SET_ACTIVE_JOB", activeJob: next, page: diagnosticPageInfo(), reason: options.reason || "" });
}

async function getExtensionState() {
  const data = await send({ type: "GET_STATE" });
  return data || {};
}

async function isPaused() {
  const activeJob = await getActiveJob();
  return Boolean(activeJob?.paused);
}

async function waitIfPaused() {
  if (fulfilmentForceStopped) throw fulfilmentPausedError();
  if (await isPaused()) {
    const activeJob = await getActiveJob();
    showPanel(
      "Nutricity fulfilment paused",
      "Fulfilment is paused. No page actions will continue until you click Resume.",
      "I did it manually, continue",
      () => continueAfterManualStep(activeJob),
    );
    throw fulfilmentPausedError();
  }
}

async function waitForPageReady(label = "page") {
  showPanel("Nutricity fulfilment", `Waiting for ${label} controls.`, null, null);
  const started = Date.now();
  while (document.readyState === "loading" && Date.now() - started < PAGE_READY_TIMEOUT) {
    await waitIfPaused();
    await sleep(250);
  }
  await waitForStableDom(350, 2500);
  await sleep(200);
}

async function waitForStableDom(quietMs = 1000, timeoutMs = 8000) {
  let lastMutation = Date.now();
  const observer = new MutationObserver(() => {
    lastMutation = Date.now();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true });
  const started = Date.now();
  try {
    while (Date.now() - started < timeoutMs) {
      await waitIfPaused();
      if (Date.now() - lastMutation >= quietMs) return true;
      await sleep(200);
    }
    return false;
  } finally {
    observer.disconnect();
  }
}

async function waitForElement(selectors, timeoutMs = 18000) {
  const list = Array.isArray(selectors) ? selectors : [selectors];
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    await waitIfPaused();
    for (const selector of list) {
      const element = [...document.querySelectorAll(selector)].find(visible);
      if (element) return element;
    }
    await sleep(300);
  }
  return null;
}

function activeJobOrderLabel(activeJob) {
  const names = Array.isArray(activeJob?.job?.order_names)
    ? activeJob.job.order_names.map((name) => String(name || "").trim()).filter(Boolean)
    : [];
  if (names.length) return names.join(", ");
  const recipient = String(activeJob?.job?.recipient_name || "").replace(/\s+/g, " ").trim();
  if (recipient) return recipient;
  return String(activeJob?.job?.group_key || "").replace(/\s+/g, " ").trim();
}

function activeJobStepLabel(activeJob) {
  const stage = String(activeJob?.stage || "").replace(/_/g, " ").trim();
  if (!stage) return "Preparing";
  return stage.replace(/\b\w/g, (letter) => letter.toUpperCase());
}

async function updatePanelOrderStatus(panel = document.querySelector("#nutricity-panel")) {
  if (!panel) return;
  const version = ++panelOrderStatusVersion;
  const orderNode = panel.querySelector(".nutricity-panel-order");
  const orderText = panel.querySelector(".nutricity-panel-order-text");
  const statusText = panel.querySelector(".nutricity-panel-order-status");
  const stepText = panel.querySelector(".nutricity-panel-step-text");
  if (!orderNode || !orderText || !statusText || !stepText) return;
  const activeJob = await getActiveJob();
  if (version !== panelOrderStatusVersion) return;
  const label = activeJobOrderLabel(activeJob);
  if (!activeJob?.job || !label) {
    orderNode.hidden = true;
    orderText.textContent = "";
    stepText.textContent = "";
    statusText.textContent = "";
    return;
  }
  orderNode.hidden = false;
  orderText.textContent = label;
  stepText.textContent = activeJobStepLabel(activeJob);
  statusText.textContent = activeJob.paused ? "Paused" : "In progress";
}

function showPanel(title, message, actionText, action) {
  let panel = document.querySelector("#nutricity-panel");
  if (!panel) {
    panel = document.createElement("div");
    panel.id = "nutricity-panel";
    panel.innerHTML = `<div class="nutricity-panel-order" hidden><span class="nutricity-panel-order-label">Order being processed</span><span class="nutricity-panel-order-status"></span><div class="nutricity-panel-order-text"></div><div class="nutricity-panel-step">Step: <span class="nutricity-panel-step-text"></span></div></div><div class="nutricity-panel-header"><strong></strong><div class="nutricity-panel-controls"><button class="nutricity-pause-toggle" type="button">Pause</button><button class="nutricity-panel-minimize" type="button" title="Minimize fulfilment notice" aria-label="Minimize fulfilment notice">-</button><button class="nutricity-panel-close" type="button" title="Close fulfilment notice" aria-label="Close fulfilment notice">x</button></div></div><div class="nutricity-panel-message"></div><ol class="nutricity-panel-activity"></ol>`;
    panel.querySelector(".nutricity-pause-toggle").addEventListener("click", togglePanelPause);
    panel.querySelector(".nutricity-panel-minimize").addEventListener("click", togglePanelMinimized);
    panel.querySelector(".nutricity-panel-close").addEventListener("click", closePanel);
    document.documentElement.append(panel);
  }
  panel.classList.toggle("is-stopped", /\bstopped\b|force stop/i.test(`${title} ${message}`));
  panel.classList.remove("is-minimized");
  panel.querySelector("strong").textContent = title;
  panel.querySelector(".nutricity-panel-message").textContent = message;
  rememberPanelActivity(panel, title, message);
  panel.querySelector(".nutricity-panel-action")?.remove();
  updatePanelPauseButton(panel);
  updatePanelOrderStatus(panel);
  if (actionText && action) {
    const button = document.createElement("button");
    button.className = "nutricity-panel-action";
    button.textContent = actionText;
    button.addEventListener("click", action);
    panel.append(button);
  }
}

async function waitForCostlyLossOverride(activeJob, item, purchaseItem, storeTotal, amazonTotal) {
  const costlyLineId = itemPrimaryLineId(item);
  const overrideKey = String(costlyLineId || item?.asin || purchaseItem?.asin || "").toUpperCase();
  const existingOverrides = Array.isArray(activeJob?.costlyLossOverrides) ? activeJob.costlyLossOverrides : [];
  if (overrideKey && existingOverrides.includes(overrideKey)) return true;

  const waitSeconds = 10;
  const deadline = Date.now() + waitSeconds * 1000;
  let settled = false;
  let timer = null;
  return new Promise((resolve) => {
    const finish = (approved) => {
      if (settled) return;
      settled = true;
      if (timer) clearInterval(timer);
      resolve(approved);
    };
    const approveLoss = async () => {
      if (settled) return;
      const latest = await getActiveJob();
      const next = latest?.job?.group_key === activeJob?.job?.group_key ? latest : activeJob;
      next.costlyLossOverrides = Array.from(new Set([
        ...(Array.isArray(next.costlyLossOverrides) ? next.costlyLossOverrides : []),
        overrideKey,
      ].filter(Boolean)));
      activeJob.costlyLossOverrides = next.costlyLossOverrides;
      await setActiveJob(next, { reason: "costly_loss_force_approved" });
      showPanel(
        "Loss fulfilment approved",
        `Continuing ${purchaseItem.asin} at Amazon cost $${amazonTotal.toFixed(2)} against store sale value $${storeTotal.toFixed(2)}.`,
        null,
        null,
      );
      finish(true);
    };
    showPanel(
      "Costly fulfilment detected",
      `Amazon cost is $${amazonTotal.toFixed(2)} but store sale value is $${storeTotal.toFixed(2)}. You have 10 seconds to force fulfil this order in loss; otherwise it moves to Costly and the next order starts automatically.`,
      "Force fulfil this order in loss (10s)",
      approveLoss,
    );
    timer = setInterval(() => {
      const remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      const button = document.querySelector("#nutricity-panel .nutricity-panel-action");
      if (button) button.textContent = `Force fulfil this order in loss (${remaining}s)`;
      if (remaining <= 0) {
        button?.remove();
        finish(false);
      }
    }, 250);
  });
}

function togglePanelMinimized(event) {
  event.preventDefault();
  event.stopPropagation();
  const panel = event.currentTarget.closest("#nutricity-panel");
  if (!panel) return;
  const minimized = !panel.classList.contains("is-minimized");
  panel.classList.toggle("is-minimized", minimized);
  event.currentTarget.textContent = minimized ? "+" : "-";
  event.currentTarget.title = minimized ? "Expand fulfilment notice" : "Minimize fulfilment notice";
  event.currentTarget.setAttribute("aria-label", event.currentTarget.title);
}

function closePanel(event) {
  event.preventDefault();
  event.stopPropagation();
  event.currentTarget.closest("#nutricity-panel")?.remove();
}

async function keepPanelAlive() {
  if (fulfilmentForceStopped || !extensionContextAlive || !/amazon\.com$/i.test(location.hostname)) return;
  const activeJob = await getActiveJob();
  if (!activeJob?.job) return;
  if (!document.querySelector("#nutricity-panel")) {
    showPanel(
      activeJob.paused ? "Nutricity fulfilment paused" : "Nutricity fulfilment",
      activeJob.paused
        ? "Fulfilment is paused. Click Resume to retry this step, or continue if you completed it manually."
        : `Working on ${activeJobOrderLabel(activeJob) || activeJob.job.group_key}.`,
      activeJob.paused ? "I did it manually, continue" : null,
      activeJob.paused ? () => continueAfterManualStep(activeJob) : null,
    );
  } else {
    updatePanelPauseButton();
    updatePanelOrderStatus();
  }
}

function rememberPanelActivity(panel, title, message) {
  const activity = panel.querySelector(".nutricity-panel-activity");
  if (!activity || !message) return;
  const text = `${title}: ${message}`.replace(/\s+/g, " ").trim();
  if (activity.firstElementChild?.dataset?.text === text) return;
  const item = document.createElement("li");
  item.dataset.text = text;
  item.textContent = text;
  activity.prepend(item);
  while (activity.children.length > 5) {
    activity.lastElementChild.remove();
  }
}

async function togglePanelPause(event) {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const result = await send({ type: "TOGGLE_PAUSE" });
    const paused = Boolean(result?.paused);
    button.textContent = paused ? "Resume" : "Pause";
    button.classList.toggle("is-paused", paused);
    if (paused) {
      const activeJob = await getActiveJob();
      showPanel(
        "Nutricity fulfilment paused",
        "Fulfilment is paused. Click Resume to retry this step, or continue if you completed it manually.",
        "I did it manually, continue",
        () => continueAfterManualStep(activeJob),
      );
    } else {
      showPanel("Nutricity fulfilment", `Resuming ${result?.stage || "last step"}.`, null, null);
      // Pausing aborts the in-flight page flow. Resume starts a fresh runner so
      // stale variables cannot continue clicking or navigating in the background.
      if (!window.__nutricityRunning) setTimeout(runSafely, 250);
    }
  } finally {
    button.disabled = false;
  }
}

async function updatePanelPauseButton(panel = document.querySelector("#nutricity-panel")) {
  if (!panel) return;
  const button = panel.querySelector(".nutricity-pause-toggle");
  if (!button) return;
  const activeJob = await getActiveJob();
  if (!activeJob?.job) {
    button.hidden = true;
    return;
  }
  button.hidden = false;
  button.textContent = activeJob.paused ? "Resume" : "Pause";
  button.classList.toggle("is-paused", Boolean(activeJob.paused));
}

function visible(element) {
  if (!element) return false;
  const style = getComputedStyle(element);
  const rect = element.getBoundingClientRect();
  return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
}

function clickFirst(selectors) {
  for (const selector of selectors) {
    const element = [...document.querySelectorAll(selector)].find(visible);
    if (element) {
      element.scrollIntoView({ block: "center", behavior: "smooth" });
      element.click();
      return true;
    }
  }
  return false;
}

async function clickElement(element, label = "element", options = {}) {
  if (!element) return false;
  const delayMs = Number.isFinite(options.delayMs) ? Math.max(0, options.delayMs) : ACTION_DELAY;
  const preClickDelayMs = Number.isFinite(options.preClickDelayMs) ? Math.max(0, options.preClickDelayMs) : 250;
  await waitIfPaused();
  element.scrollIntoView({ block: "center", behavior: "smooth" });
  if (preClickDelayMs > 0) await sleep(preClickDelayMs);
  await waitIfPaused();
  element.click();
  if (delayMs > 0) await sleep(delayMs);
  return true;
}

function findButtonByText(texts) {
  const wanted = texts.map((text) => text.toLowerCase());
  const candidates = [...document.querySelectorAll("button, input[type='submit'], input[type='button'], a, span.a-button")];
  return candidates.find((element) => {
    const labelledBy = element.getAttribute?.("aria-labelledby");
    const labelText = labelledBy ? document.getElementById(labelledBy)?.textContent : "";
    const text = (element.value || element.title || element.innerText || element.textContent || labelText || "").replace(/\s+/g, " ").trim().toLowerCase();
    return visible(element) && wanted.some((needle) => text.includes(needle));
  });
}

function findVisibleTextTarget(texts, selectors = "a, button, input[type='submit'], input[type='button'], label, span.a-button, span.a-button-text, [role='button'], [role='option']") {
  const wanted = texts.map((text) => text.toLowerCase());
  const candidates = [...document.querySelectorAll(selectors)];
  return candidates.find((element) => {
    const text = (element.value || element.innerText || element.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
    return visible(element) && wanted.some((needle) => text.includes(needle));
  });
}

function parsePriceFrom(element) {
  if (!element) return null;
  if (isUnitPriceElement(element)) return null;
  const offscreen = element.querySelector(".a-offscreen")?.textContent || "";
  const offscreenPrice = priceFromText(offscreen);
  if (offscreenPrice) return offscreenPrice;
  const whole = element.querySelector(".a-price-whole")?.textContent || "";
  const fraction = element.querySelector(".a-price-fraction")?.textContent || "00";
  const value = Number(`${whole.replace(/[^0-9]/g, "")}.${fraction.replace(/[^0-9]/g, "").slice(0, 2)}`);
  if (Number.isFinite(value) && value > 0) return value;
  return priceFromText(element.textContent || "");
}

function isUnitPriceElement(element) {
  if (!element) return false;
  if (element.closest(".aok-relative")) return true;
  const text = (element.innerText || element.textContent || "").replace(/\s+/g, " ").trim();
  if (/\/\s*(?:count|ct|unit|each|oz|ounce|fl\s*oz|lb|pound|g|gram|mg|ml)\b/i.test(text)) return true;
  for (let node = element.parentElement; node && node !== document.body; node = node.parentElement) {
    const nodeText = (node.innerText || node.textContent || "").replace(/\s+/g, " ").trim();
    if (nodeText.length > 80) break;
    if (/\/\s*(?:count|ct|unit|each|oz|ounce|fl\s*oz|lb|pound|g|gram|mg|ml)\b/i.test(nodeText)) return true;
  }
  return false;
}

function isCouponOrSavingsPriceElement(element) {
  if (!element) return false;
  if (element.closest([
    "#couponFeature",
    "#coupon_feature_div",
    "#promoPriceBlockMessage_feature_div",
    "#promoPriceBlockMessage",
    "[id*='coupon'][id*='Feature']",
    "[id*='coupon'][id*='feature']",
    "[data-csa-c-owner='PromotionsDiscovery']",
    ".promoPriceBlockMessage",
  ].join(", "))) return true;
  for (let node = element.parentElement; node && node !== document.body; node = node.parentElement) {
    const text = (node.innerText || node.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
    if (text.length > 220) break;
    if (/\b(coupon|promo|promotion|saving|savings|save)\b/.test(text) && !/\b(one-time purchase|subscribe\s*&\s*save)\b/.test(text)) return true;
  }
  return false;
}

function priceCandidatesIn(root, selectors = [".a-price"]) {
  if (!root) return null;
  const prices = selectors
    .flatMap((selector) => [...root.querySelectorAll(selector)])
    .filter((element, index, all) => all.indexOf(element) === index && visible(element) && !element.closest(".aok-hidden") && !isCouponOrSavingsPriceElement(element))
    .map(parsePriceFrom)
    .filter((value) => Number(value) > 0);
  return prices;
}

function priceCandidateElementsIn(root, selectors = [".a-price"]) {
  if (!root) return [];
  return selectors
    .flatMap((selector) => [...root.querySelectorAll(selector)])
    .filter((element, index, all) => all.indexOf(element) === index && visible(element) && !element.closest(".aok-hidden") && !isCouponOrSavingsPriceElement(element));
}

function firstPriceIn(root) {
  if (!root) return null;
  const prices = priceCandidatesIn(root);
  return prices[0] || priceFromText(root.innerText || root.textContent || "");
}

function lowestPriceIn(root, selectors = [".a-price"]) {
  const prices = priceCandidatesIn(root, selectors);
  return prices.length ? Math.min(...prices) : null;
}

function moneyText(value) {
  const number = Number(value || 0);
  return number ? `$${number.toFixed(2)}` : "unknown price";
}

function isInSubscribeAndSave(element) {
  return Boolean(element?.closest?.(subscribeAndSaveRootSelector()));
}

function subscribeAndSaveRootSelector() {
  return [
    "#snsAccordionRowMiddle",
    "#snsAccordionRow",
    "#snsAccordionRowContent",
    "#reinvent_price_desktop_snsAccordionRowMiddle",
    "#snsQuantity_feature_div",
    "[data-csa-c-buying-option-type='SNS']",
    "[data-csa-c-slot-id*='sns']",
    "[id*='rcx-subscribe']",
    "[id*='rcxOrdFreqSns']",
    "[id*='snsAccordion']",
    "[id*='snsBuyingOption']",
    "[data-a-accordion-row-name*='sns']",
  ].join(", ");
}

function subscribeAndSavePriceFromText(text) {
  const match = String(text || "")
    .replace(/,/g, "")
    .match(/subscribe\s*&\s*save\s*\$+\s*([0-9]+(?:\.[0-9]{1,2})?)/i);
  if (!match) return null;
  const value = Number(match[1]);
  return Number.isFinite(value) ? value : null;
}

function oneTimePurchaseRootSelector() {
  return [
    "#qualifiedBuybox",
    "#desktop_qualifiedBuyBox",
    "#buybox",
    "#newAccordionRow",
    "#reinvent_price_desktop_newAccordionRow",
    "[data-csa-c-buying-option-type='NEW']",
    "[data-csa-c-buying-option-type='B2B']",
    "[data-a-accordion-row-name*='new']",
  ].join(", ");
}

function oneTimePurchaseRoots() {
  const roots = [...document.querySelectorAll(oneTimePurchaseRootSelector())].filter((root) => visible(root) && !isInSubscribeAndSave(root));
  for (const node of document.querySelectorAll("[id*='new_buyingOption'], [id*='DesktopFfqp_'][id*='new_buyingOption']")) {
    const root = node.closest("[data-csa-c-buying-option-type], [data-a-accordion-row-name], .a-accordion-row, .a-box, #qualifiedBuybox, #desktop_qualifiedBuyBox, #buybox") || node.closest(".a-section") || node;
    if (root && visible(root) && !isInSubscribeAndSave(root) && !roots.includes(root)) roots.push(root);
  }
  for (const node of document.querySelectorAll("button, [role='button'], .a-accordion-row, .a-box, #rightCol, #desktop_buybox")) {
    if (!visible(node) || isInSubscribeAndSave(node) || roots.includes(node)) continue;
    const text = (node.innerText || node.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
    if (!text.includes("one-time purchase") || !/\$\s*\d/.test(text)) continue;
    roots.push(node);
  }
  return roots;
}

function productPriceSnapshot() {
  const regularPriceSelectors = [
    ".a-price[data-a-color='price'][data-a-size='b']",
    ".a-price[data-a-color='price'].header-price",
    ".a-price.a-color-price[data-a-size='b']",
    "#corePrice_feature_div .a-price[data-a-size='b']",
    "#apex_desktop .a-price[data-a-size='b']",
    "#reinvent_price_desktop_newAccordionRow .a-price[data-a-size='b']",
    "#newAccordionRow .a-price[data-a-size='b']",
    "#buybox .a-price[data-a-size='b']",
    "#tp_price_block_total_price_ww .a-price[data-a-size='b']",
  ];
  const regularRoots = oneTimePurchaseRoots();
  const regularPool = regularRoots.length ? regularRoots : [document];
  const regularPrices = regularPool
    .flatMap((root) => priceCandidateElementsIn(root, regularPriceSelectors))
    .filter((element, index, all) => all.indexOf(element) === index && visible(element) && !isInSubscribeAndSave(element) && !element.closest(".aok-hidden"))
    .map(parsePriceFrom)
    .filter((value) => Number(value) > 0);
  const regularFallbackPrices = regularPrices.length ? [] : regularRoots
    .map((root) => firstPriceIn(root))
    .filter((value) => Number(value) > 0);
  const regular = regularPrices.length ? Math.min(...regularPrices) : (regularFallbackPrices.length ? Math.min(...regularFallbackPrices) : null);
  const snsRoots = [...document.querySelectorAll(subscribeAndSaveRootSelector())].filter(visible);
  const snsPriceSelectors = [
    "#sns-tiered-price .a-price[data-a-size='b']",
    "#subscriptionPrice .a-price[data-a-color='secondary'][data-a-size='b']",
    ".a-price[data-a-color='secondary'][data-a-size='b']",
    ".a-price[data-a-color='secondary'].header-price",
    ".a-price.a-color-price[data-a-size='b']",
  ];
  const snsPrices = snsRoots
    .flatMap((root) => priceCandidatesIn(root, snsPriceSelectors))
    .filter((value) => Number(value) > 0);
  const sns = snsPrices.length ? Math.min(...snsPrices) : subscribeAndSavePriceFromText(document.body.innerText);
  const best = sns && regular ? Math.min(sns, regular) : sns || regular || 0;
  return { regular, sns, best };
}

function snsQuantityControlVisible() {
  if (!subscribeAndSaveAccordionIsActive()) return false;
  return Boolean([...document.querySelectorAll(subscribeAndSaveRootSelector())].find((root) => {
    if (!visible(root)) return false;
    const control = root.querySelector([
      "select[id*='sns'][id*='predefinedQuantitiesDropdown']",
      "select#rcxsubsQuan",
      "select[name='rcxsubsQuan']",
      "input#rcxsubsQuan",
      "input[id*='sns'][id$='freeQuantityTextInput']",
    ].join(", "));
    return visible(control) || visible(control?.closest?.("td, table, .a-section, .a-dropdown-container"));
  }));
}

function productTitleText() {
  return (document.querySelector("#productTitle")?.textContent || document.querySelector("#title")?.textContent || "").replace(/\s+/g, " ").trim();
}

function dosageFromProductTitle() {
  const match = productTitleText().match(/\b(\d+(?:\.\d+)?)\s*mg\b/i);
  return match ? `${match[1]}mg` : "";
}

function normalizePackLabel(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return "";
  const clean = Number.isInteger(number) ? String(number) : String(number).replace(/\.0+$/, "");
  return `${clean}Pack`;
}

function packLabelFromText(text) {
  const normalized = String(text || "").replace(/\s+/g, " ").trim();
  if (!normalized) return "";
  const patterns = [
    /\bpack\s*of\s*(\d+(?:\.\d+)?)\b/i,
    /\b(\d+(?:\.\d+)?)\s*[- ]?\s*(?:packs?|pk|pks)\b/i,
    /\b(\d+(?:\.\d+)?)(?:packs?|pk|pks)\b/i,
  ];
  for (const pattern of patterns) {
    const match = normalized.match(pattern);
    const label = match ? normalizePackLabel(match[1]) : "";
    if (label) return label;
  }
  return "";
}

function selectedVariantTextFromPage() {
  const selectedSwatch = [...document.querySelectorAll("li[data-asin], .inline-twister-swatch, .a-button-selected")]
    .find((node) => node.getAttribute?.("data-initiallyselected") === "true" || node.querySelector?.(".a-button-selected, input[aria-checked='true']"));
  return [
    document.querySelector("[id^='inline-twister-expanded-dimension-text']")?.textContent,
    document.querySelector("[aria-label^='Selected Size is'], [aria-label^='Selected Style is'], [aria-label*='Selected' i]")?.getAttribute("aria-label"),
    selectedSwatch ? swatchLabel(selectedSwatch) : "",
    productTitleText(),
  ].map((value) => String(value || "").replace(/\s+/g, " ").trim()).filter(Boolean).join(" ");
}

function packLabelFromSelectedVariantOrTitle() {
  return packLabelFromText(selectedVariantTextFromPage());
}

function appendUniqueCompactLabels(list, labels) {
  const next = Array.isArray(list) ? [...list] : [];
  const seen = new Set(next.map((value) => String(value || "").toLowerCase()));
  for (const label of labels) {
    const normalized = String(label || "").replace(/\s+/g, "").trim();
    if (!normalized || seen.has(normalized.toLowerCase())) continue;
    next.push(normalized);
    seen.add(normalized.toLowerCase());
  }
  return next;
}

function isPackLabel(value) {
  return /^\d+(?:\.\d+)?pack$/i.test(String(value || "").replace(/\s+/g, "").trim());
}

function replaceRememberedPackLabels(list, pack) {
  const normalizedPack = String(pack || "").replace(/\s+/g, "").trim();
  const next = (Array.isArray(list) ? list : [])
    .map((value) => String(value || "").replace(/\s+/g, "").trim())
    .filter((value) => value && !isPackLabel(value));
  return appendUniqueCompactLabels(next, [normalizedPack]);
}

function rememberProductDosage(activeJob, item = null) {
  const dosage = dosageFromProductTitle();
  if (!dosage) return activeJob;
  activeJob.productDosages = appendUniqueCompactLabels(activeJob.productDosages, [dosage]);
  const dosageByOrder = activeJob.dosageByOrder && typeof activeJob.dosageByOrder === "object" ? activeJob.dosageByOrder : {};
  const orderNames = item?.order_names?.length ? item.order_names : activeJob.job?.order_names || [];
  for (const orderName of orderNames) {
    const key = String(orderName || "").trim();
    if (!key) continue;
    dosageByOrder[key] = appendUniqueCompactLabels(dosageByOrder[key], [dosage]);
  }
  activeJob.dosageByOrder = dosageByOrder;
  return activeJob;
}

function rememberProductPack(activeJob, item = null) {
  const selectedLabel = item?.asin ? activeJob?.variantSelections?.[item.asin]?.label : "";
  const selectedPack = packLabelFromText(selectedLabel);
  const pack = selectedPack || packLabelFromSelectedVariantOrTitle();
  if (!pack) return activeJob;
  activeJob.productPacks = selectedPack
    ? replaceRememberedPackLabels(activeJob.productPacks, pack)
    : appendUniqueCompactLabels(activeJob.productPacks, [pack]);
  const packByOrder = activeJob.packByOrder && typeof activeJob.packByOrder === "object" ? activeJob.packByOrder : {};
  const orderNames = item?.order_names?.length ? item.order_names : activeJob.job?.order_names || [];
  for (const orderName of orderNames) {
    const key = String(orderName || "").trim();
    if (!key) continue;
    packByOrder[key] = selectedPack
      ? replaceRememberedPackLabels(packByOrder[key], pack)
      : appendUniqueCompactLabels(packByOrder[key], [pack]);
  }
  activeJob.packByOrder = packByOrder;
  return activeJob;
}

function activeJobUsesReplacementAsin(activeJob) {
  const job = activeJob?.job || {};
  if (job.has_replacement_asin === true || job.uses_replacement_asin === true) return true;
  const items = Array.isArray(job.items) ? job.items : [];
  return items.some((item) => {
    if (!item || typeof item !== "object") return false;
    if (item.uses_replacement_asin === true || item.is_replacement_asin === true) return true;
    const replacementAsins = Array.isArray(item.replacement_asins) ? item.replacement_asins : [];
    if (replacementAsins.some((asin) => String(asin || "").trim())) return true;
    const replacementAsin = String(item.replacement_asin || "").trim();
    const originalAsin = String(item.original_asin || "").trim();
    const asin = String(item.asin || "").trim();
    return Boolean(replacementAsin && (!asin || replacementAsin.toUpperCase() === asin.toUpperCase()) && replacementAsin.toUpperCase() !== originalAsin.toUpperCase());
  });
}

function recipientNameWithReplacementSuffix(name, activeJob) {
  const cleaned = String(name || "").replace(/\s+/g, " ").trim();
  if (!activeJobUsesReplacementAsin(activeJob)) return cleaned;
  if (/(?:^|\s)alt$/i.test(cleaned)) return cleaned;
  return [cleaned, "Alt"].filter(Boolean).join(" ").trim();
}

function recipientName(activeJob) {
  const orderNames = Array.isArray(activeJob?.job?.order_names) ? activeJob.job.order_names : [];
  const mixedAsin = isMixedAsinOrder(activeJob);
  const recipientSuffix = String(activeJob?.job?.recipient_suffix || "").replace(/\s+/g, "").trim();
  const dosageByOrder = activeJob?.dosageByOrder && typeof activeJob.dosageByOrder === "object" ? activeJob.dosageByOrder : {};
  const packByOrder = activeJob?.packByOrder && typeof activeJob.packByOrder === "object" ? activeJob.packByOrder : {};
  if (orderNames.length) {
    const parts = ["Nutricity"];
    const assigned = new Set();
    for (const orderName of orderNames) {
      const name = String(orderName || "").trim();
      if (!name) continue;
      parts.push(name);
      if (mixedAsin || recipientSuffix) continue;
      const dosages = Array.isArray(dosageByOrder[name]) ? dosageByOrder[name] : [];
      for (const dosage of dosages) {
        const normalized = String(dosage || "").replace(/\s+/g, "").trim();
        if (normalized) {
          parts.push(normalized);
          assigned.add(normalized.toLowerCase());
        }
      }
      const packs = Array.isArray(packByOrder[name]) ? packByOrder[name] : [];
      for (const pack of packs) {
        const normalized = String(pack || "").replace(/\s+/g, "").trim();
        if (normalized) {
          parts.push(normalized);
          assigned.add(normalized.toLowerCase());
        }
      }
    }
    if (mixedAsin) parts.push("Multi");
    const globalDosages = (recipientSuffix ? [] : (Array.isArray(activeJob?.productDosages) ? activeJob.productDosages : []))
      .map((item) => String(item || "").replace(/\s+/g, "").trim())
      .filter(Boolean);
    for (const dosage of mixedAsin ? [] : globalDosages) {
      if (!assigned.has(dosage.toLowerCase())) parts.push(dosage);
    }
    const hasOrderSpecificPack = Object.values(packByOrder).some((packs) => Array.isArray(packs) && packs.some(isPackLabel));
    const globalPacks = (recipientSuffix || hasOrderSpecificPack ? [] : (Array.isArray(activeJob?.productPacks) ? activeJob.productPacks : []))
      .map((item) => String(item || "").replace(/\s+/g, "").trim())
      .filter(Boolean);
    for (const pack of mixedAsin ? [] : globalPacks) {
      if (!assigned.has(pack.toLowerCase())) parts.push(pack);
    }
    if (recipientSuffix) parts.push(recipientSuffix);
    return recipientNameWithReplacementSuffix(parts.join(" ").replace(/\s+/g, " ").trim(), activeJob);
  }
  const base = String(activeJob?.job?.recipient_name || "").replace(/\s+/g, " ").trim();
  if (mixedAsin) return recipientNameWithReplacementSuffix(`${base} Multi${recipientSuffix ? ` ${recipientSuffix}` : ""}`.replace(/\s+/g, " ").trim(), activeJob);
  if (recipientSuffix) return recipientNameWithReplacementSuffix([base, recipientSuffix].filter(Boolean).join(" ").trim(), activeJob);
  const dosages = (Array.isArray(activeJob?.productDosages) ? activeJob.productDosages : [])
    .map((item) => String(item || "").replace(/\s+/g, "").trim())
    .filter((dosage) => dosage && !base.toLowerCase().includes(dosage.toLowerCase()));
  const packs = (Array.isArray(activeJob?.productPacks) ? activeJob.productPacks : [])
    .map((item) => String(item || "").replace(/\s+/g, "").trim())
    .filter((pack) => pack && !base.toLowerCase().includes(pack.toLowerCase()));
  return recipientNameWithReplacementSuffix([base, ...dosages, ...packs, recipientSuffix].filter(Boolean).join(" ").trim(), activeJob);
}

function priceFromText(text) {
  const match = String(text || "").replace(/,/g, "").match(/\$\s*([0-9]+(?:\.[0-9]{1,2})?)/);
  if (!match) return null;
  const value = Number(match[1]);
  return Number.isFinite(value) ? value : null;
}

function normalizedPackUnit(unit) {
  const value = String(unit || "").toLowerCase().replace(/\s+/g, " ").trim();
  if (!value) return "";
  if (/^(counts?|ct|capsules?|tablets?|softgels?|gummies?|packs?|pieces?|pcs)$/.test(value)) return "count";
  if (/^(fl oz|fluid ounces?)$/.test(value)) return "fl oz";
  if (/^(ounces?|oz)$/.test(value)) return "oz";
  if (/^(pounds?|lbs?)$/.test(value)) return "lb";
  if (/^(grams?|g)$/.test(value)) return "g";
  if (/^(milligrams?|mg)$/.test(value)) return "mg";
  if (/^(milliliters?|ml)$/.test(value)) return "ml";
  if (/^(liters?|l)$/.test(value)) return "l";
  return value;
}

function parseCountPack(text) {
  const normalized = String(text || "").replace(/\s+/g, " ").trim();
  if (!normalized) return null;
  const countMatch = normalized.match(/\b(\d+(?:\.\d+)?)\s*(?:mini\s*)?(counts?|ct|capsules?|tablets?|softgels?|gummies?|packs?|pieces?|pcs)\b/i);
  const amountMatch = normalized.match(/\b(\d+(?:\.\d+)?)\s*(fl\s*oz|fluid\s*ounces?|ounces?|oz|pounds?|lbs?|grams?|g|milligrams?|mg|milliliters?|ml|liters?|l)\b/i);
  const sizeMatch = countMatch || amountMatch;
  if (!sizeMatch) return null;
  const packMatch = normalized.match(/\bpack\s*of\s*(\d+(?:\.\d+)?)\b/i);
  const count = Number(sizeMatch[1]);
  const pack = packMatch ? Number(packMatch[1]) : 1;
  const units = count * pack;
  if (!Number.isFinite(units) || units <= 0) return null;
  const unit = normalizedPackUnit(countMatch ? "count" : sizeMatch[2]);
  return { count, pack, units, unit, label: normalized };
}

function itemCountPack(item) {
  const names = [
    item.product_name,
    ...(Array.isArray(item.product_names) ? item.product_names : []),
  ].filter(Boolean);
  for (const name of names) {
    const parsed = parseCountPack(name);
    if (parsed) return parsed;
  }
  return null;
}

function selectedVariantFromPage() {
  const text =
    document.querySelector("[id^='inline-twister-expanded-dimension-text']")?.textContent ||
    document.querySelector("[aria-label^='Selected Size is'], [aria-label^='Selected Style is']")?.getAttribute("aria-label") ||
    "";
  return parseCountPack(text);
}

function swatchLabel(swatch) {
  const label =
    swatch.querySelector(".swatch-title-text")?.textContent ||
    swatch.querySelector("img[alt]")?.getAttribute("alt") ||
    swatch.querySelector(".a-button-text")?.textContent ||
    "";
  return label.replace(/\s+/g, " ").trim();
}

function swatchPrice(swatch) {
  return (
    parsePriceFrom(swatch.querySelector(".apex-pricetopay-value")) ||
    parsePriceFrom(swatch.querySelector(".a-price")) ||
    priceFromText(swatch.innerText || swatch.textContent)
  );
}

function variantSwatches() {
  const rows = [
    ...document.querySelectorAll("#twister-plus-inline-twister-card li[data-asin], #twister-plus-inline-twister li[data-asin], li.inline-twister-swatch[data-asin]"),
  ];
  const seen = new Set();
  return rows
    .filter((swatch) => {
      const asin = (swatch.getAttribute("data-asin") || "").toUpperCase();
      if (!asin || seen.has(asin)) return false;
      seen.add(asin);
      return swatch.getAttribute("data-initiallyunavailable") !== "true";
    })
    .map((swatch) => {
      const label = swatchLabel(swatch);
      const parsed = parseCountPack(label);
      const price = swatchPrice(swatch);
      const asin = (swatch.getAttribute("data-asin") || "").toUpperCase();
      const selected =
        swatch.getAttribute("data-initiallyselected") === "true" ||
        Boolean(swatch.querySelector(".a-button-selected, input[aria-checked='true']"));
      const target = swatch.querySelector("input.a-button-input, button, .a-button") || swatch;
      return parsed && price ? { ...parsed, asin, price, selected, target } : null;
    })
    .filter(Boolean);
}

function selectedVariantItem(activeJob, item) {
  const selection = activeJob.variantSelections?.[item.asin];
  if (!selection) return item;
  return {
    ...item,
    asin: selection.asin || item.asin,
    quantity: selection.quantity || item.quantity,
    original_asin: item.asin,
    selected_variant_label: selection.label || "",
    selected_variant_units: selection.units || null,
    requested_total_units: selection.requested_total_units || null,
  };
}

function purchaseAsinForJobItem(activeJob, item) {
  return String(selectedVariantItem(activeJob, item)?.asin || item?.asin || "").toUpperCase();
}

function uniquePurchaseAsins(activeJob) {
  const asins = new Set();
  for (const item of activeJob?.job?.items || []) {
    const asin = purchaseAsinForJobItem(activeJob, item);
    if (asin) asins.add(asin);
  }
  return asins;
}

function isMixedAsinOrder(activeJob) {
  return uniquePurchaseAsins(activeJob).size > 1;
}

function variantSelectionNote(item, purchaseItem) {
  if (!purchaseItem.selected_variant_label || !purchaseItem.original_asin || purchaseItem.original_asin === purchaseItem.asin) return "";
  const originalLabel = itemCountPack(item)?.label || item.product_name || item.asin;
  const orderedQuantity = Number(item.quantity || 1);
  const purchaseQuantity = Number(purchaseItem.quantity || 1);
  const totalUnits = purchaseItem.requested_total_units ? ` (${purchaseItem.requested_total_units} total count)` : "";
  return `Equivalent Amazon pack variant found and used: ordered ${orderedQuantity} x ${originalLabel}${totalUnits}; purchased ${purchaseQuantity} x ${purchaseItem.selected_variant_label} (${purchaseItem.asin}) instead of ${item.asin}.`;
}

function chooseBestCountVariant(item, currentUnitPrice) {
  const ordered = itemCountPack(item) || selectedVariantFromPage();
  if (!ordered || !currentUnitPrice) return null;
  const requestedQuantity = Math.max(1, Math.round(Number(item.quantity || 1)));
  const requestedTotalUnits = ordered.units * requestedQuantity;
  const variants = variantSwatches();
  if (!variants.length) return null;

  const currentAsin = currentAsinFromUrl() || item.asin;
  const currentPageVariant = selectedVariantFromPage();
  if (currentPageVariant && currentUnitPrice) {
    variants.push({
      ...currentPageVariant,
      asin: currentAsin,
      price: currentUnitPrice,
      selected: true,
      target: null,
    });
  }

  const candidates = variants
    .map((variant) => {
      if (ordered.unit && variant.unit && ordered.unit !== variant.unit) return null;
      const purchaseQuantity = requestedTotalUnits / variant.units;
      if (!Number.isInteger(purchaseQuantity) || purchaseQuantity < 1) return null;
      return {
        ...variant,
        quantity: purchaseQuantity,
        total: purchaseQuantity * variant.price,
        requested_total_units: requestedTotalUnits,
      };
    })
    .filter(Boolean)
    .sort((a, b) => a.total - b.total || a.quantity - b.quantity || b.units - a.units);
  if (!candidates.length) return null;

  const currentTotal = Number(currentUnitPrice || 0) * requestedQuantity;
  const best = candidates[0];
  if (best.asin === currentAsin && best.quantity === requestedQuantity) {
    return null;
  }
  if (currentTotal && best.total >= currentTotal - 0.01) return null;
  return best;
}

async function selectCheapestCountVariant(activeJob, item, currentUnitPrice) {
  // Never substitute another ASIN or pack automatically. A lower-priced pack
  // can be a different product, strength or customer-approved alternative.
  // The requested ASIN and quantity must remain intact unless an operator
  // explicitly makes a replacement outside the fulfilment flow.
  return false;
}

async function recordAmazonPrice(activeJob, item, unitPrice, source = "product", purchaseItem = item) {
  if (!unitPrice) return activeJob;
  activeJob.pricing = activeJob.pricing || {};
  const quantity = Number(purchaseItem.quantity || item.quantity || 1);
  const currentPageAsin = currentAsinFromUrl();
  const purchasedAsin = purchaseItem.asin && purchaseItem.asin !== item.asin
    ? purchaseItem.asin
    : (currentPageAsin || purchaseItem.asin || item.asin);
  const storeUnit = Number(item.store_unit_price || 0);
  const storeTotal = Number(item.store_total_price || storeUnit * Number(item.quantity || 1) || 0);
  const amazonTotal = unitPrice * quantity;
  activeJob.pricing[item.asin] = {
    asin: item.asin,
    purchased_asin: purchasedAsin,
    purchased_product_name: productTitleText() || purchaseItem.product_name || item.product_name || "",
    quantity,
    ordered_quantity: Number(item.quantity || 1),
    selected_variant_label: purchaseItem.selected_variant_label || "",
    requested_total_units: purchaseItem.requested_total_units || null,
    fulfilment_note: variantSelectionNote(item, purchaseItem),
    store_unit_price: storeUnit,
    store_total_price: storeTotal,
    amazon_unit_price: unitPrice,
    amazon_total_price: amazonTotal,
    profit_total: storeTotal - amazonTotal,
    source,
  };
  await setActiveJob(activeJob);
  return activeJob;
}

function currentAsinFromUrl() {
  const match = location.pathname.match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})/i);
  return match ? match[1].toUpperCase() : "";
}

function itemPrimaryLineId(item) {
  return item.line_id || item.line_ids?.[0] || null;
}

function comparableText(value) {
  return String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").replace(/\s+/g, " ").trim();
}

function matchCheckoutIssueItem(activeJob, title) {
  const items = activeJob.job?.items || [];
  if (!items.length) return null;
  if (items.length === 1) return items[0];
  const wanted = comparableText(title);
  if (!wanted) return null;
  return items.find((item) => {
    const names = [item.product_name, ...(item.product_names || [])].map(comparableText).filter(Boolean);
    return names.some((name) => name.includes(wanted) || wanted.includes(name));
  }) || null;
}

function checkoutLineItemGroups(panel) {
  return [...panel.querySelectorAll("[id^='line-item-group-display-']")]
    .filter((element) => visible(element) && !element.classList.contains("aok-hidden"));
}

function checkoutLineItemTitle(groupView) {
  const titleNode = groupView?.querySelector?.(".lineitem-title-text, [id^='item-block-product-title-']");
  return String(titleNode?.innerText || titleNode?.textContent || "").replace(/\s+/g, " ").trim();
}

function checkoutLineItemQuantity(groupView) {
  const stepper = groupView?.querySelector?.("[name='stma-checkout-quantity-stepper'], [data-a-component='stepper'], input[name='quantity'], input[data-testid='ItemSelectLineItemQuantityTextField'], input.quantity-input, [role='spinbutton']");
  const rawValue = stepper?.getAttribute?.("data-steppervalue")
    ?? stepper?.getAttribute?.("data-displaystring")
    ?? stepper?.getAttribute?.("aria-valuenow")
    ?? stepper?.value
    ?? stepper?.querySelector?.("[data-a-selector='inner-value']")?.textContent
    ?? (groupView?.innerText || groupView?.textContent || "").match(/Quantity:\s*(\d+)/i)?.[1]
    ?? "";
  const value = Number(String(rawValue).trim());
  return Number.isFinite(value) ? value : null;
}

function checkoutVisibleQuantityValues() {
  return [
    ...document.querySelectorAll("input[name='quantity'], input[data-testid='ItemSelectLineItemQuantityTextField'], input.quantity-input, [role='spinbutton'][name='quantity']"),
  ]
    .filter((element, index, all) => all.indexOf(element) === index && visible(element))
    .map((element) => Number(String(element.value || element.getAttribute("aria-valuenow") || element.textContent || "").replace(/[^\d.]/g, "")))
    .filter((value) => Number.isFinite(value) && value > 0);
}

function expectedCheckoutUnitCount(activeJob) {
  const quantities = Object.values(expectedCartQuantities(activeJob) || {});
  return quantities.reduce((sum, quantity) => sum + Number(quantity || 0), 0);
}

function checkoutSummaryUnitCount() {
  const groups = checkoutLineItemGroups(document);
  if (groups.length) {
    return groups.reduce((sum, group) => sum + (checkoutLineItemQuantity(group) || 1), 0);
  }
  const visibleQuantities = checkoutVisibleQuantityValues();
  if (visibleQuantities.length) {
    return visibleQuantities.reduce((sum, quantity) => sum + quantity, 0);
  }
  const summaryRoots = [
    ...document.querySelectorAll(
      "#subtotals-marketplace-table, #order-summary, #checkout-summary, [data-testid*='order-summary' i], [class*='order-summary' i]",
    ),
  ].filter(visible);
  const summaryTexts = [
    ...summaryRoots.map((element) => element.innerText || element.textContent || ""),
    document.body.innerText || document.body.textContent || "",
  ];
  for (const text of summaryTexts) {
    const summaryMatch =
      String(text).match(/\bItems?\s*\((\d+)\)/i)
      || String(text).match(/\bItems?\s*:\s*(\d+)\b/i)
      || String(text).match(/\bSubtotal\s*\((\d+)\s+items?\)/i);
    if (summaryMatch) return Number(summaryMatch[1]);
  }
  return null;
}

function cartVerificationMatches(activeJob) {
  const verification = activeJob?.cartVerification;
  if (!verification || verification.group_key !== activeJob?.job?.group_key) return false;
  const verifiedAt = Number(verification.verified_at || 0);
  if (!verifiedAt || Date.now() - verifiedAt > 30 * 60 * 1000) return false;
  const expected = expectedCartQuantities(activeJob);
  const verified = verification.quantities && typeof verification.quantities === "object"
    ? verification.quantities
    : {};
  const expectedKeys = Object.keys(expected).sort();
  const verifiedKeys = Object.keys(verified).sort();
  if (expectedKeys.length !== verifiedKeys.length) return false;
  return expectedKeys.every((asin, index) => (
    asin === verifiedKeys[index]
    && Number(expected[asin]) === Number(verified[asin])
  ));
}

function amazonAccountExperience() {
  const url = new URL(location.href);
  const purchaseProgram = normalizedText(url.searchParams.get("purchasePrograms") || "");
  if (
    purchaseProgram === "amazon_business"
    || url.pathname.includes("/checkout/entry/cart/amazon_business")
    || url.searchParams.get("referrer") === "businessCart"
  ) return "business";

  // Restrict visual account detection to Amazon's active header/checkout
  // chrome. Consumer pages advertise Amazon Business in their footer, so a
  // body-text or unrestricted link search produces a dangerous false positive.
  const businessShell = [
    ...document.querySelectorAll(
      [
        "header [aria-label*='Amazon Business' i]",
        "#navbar [aria-label*='Amazon Business' i]",
        "#nav-logo [aria-label*='Amazon Business' i]",
        "a[href*='ref_=ab_checkout_logo']",
        "a[href*='businessprime'][data-csa-c-slot-id]",
      ].join(", "),
    ),
  ].find(visible);
  if (businessShell) return "business";

  const consumerShell = [
    document.querySelector("#navbar"),
    document.querySelector("#nav-main"),
    document.querySelector("#nav-link-accountList"),
    document.querySelector("a[href*='ref_=nav_logo']"),
  ].find(visible);
  return consumerShell ? "consumer" : "unknown";
}

function isAmazonBusinessPage() {
  return amazonAccountExperience() === "business";
}

async function ensureCheckoutOnlyExpectedUnits(activeJob) {
  const expected = expectedCheckoutUnitCount(activeJob);
  const actual = checkoutSummaryUnitCount();
  if (!expected || (Number.isFinite(actual) && actual === expected)) return true;
  if (
    !Number.isFinite(actual)
    && (
      expected === 1
      || (isAmazonBusinessPage() && cartVerificationMatches(activeJob))
    )
  ) {
    return true;
  }
  const expectedAsins = Object.keys(expectedCartQuantities(activeJob) || {}).join(", ");
  const expectedAsinList = Object.keys(expectedCartQuantities(activeJob) || {});
  const verifiedSingleAsinShortage = (
    Number.isFinite(actual)
    && actual > 0
    && actual < expected
    && expectedAsinList.length === 1
    && cartVerificationMatches(activeJob)
  );
  const actualLabel = Number.isFinite(actual)
    ? `${actual < expected ? "only " : ""}${actual} item unit(s)`
    : "an unreadable item count";
  const message = verifiedSingleAsinShortage
    ? `Amazon reduced ASIN ${expectedAsinList[0]} from the cart-verified quantity ${expected} to ${actual} at checkout. The order was stopped before Place Order and moved for quantity-shortage review.`
    : `Amazon checkout shows ${actualLabel}, but this queued job expects exactly ${expected}${expectedAsins ? ` (${expectedAsins})` : ""}. The order was stopped before Place Order because the checkout contents could not be verified against the complete queued order.`;
  showPanel("Checkout quantity mismatch", message, null, null);
  await send({
    type: "FAIL_JOB",
    message,
    missingAsin: verifiedSingleAsinShortage ? expectedAsinList[0] : "",
    missingLineId: verifiedSingleAsinShortage ? lineIdForAsin(activeJob, expectedAsinList[0]) : null,
    failureCode: verifiedSingleAsinShortage ? "partial_quantity" : "cart_quantity_mismatch",
    requestedQuantity: expected,
    fulfilledQuantity: actual,
    availableQuantity: Number.isFinite(actual) && actual < expected ? actual : null,
  });
  return false;
}

function checkoutLineItemLimitCandidate(activeJob, groupView) {
  if (!groupView) return null;
  const groupText = normalizedText(groupView.innerText || groupView.textContent);
  if (
    !groupText.includes("limited purchase quantity") ||
    !groupText.includes("business has reached it")
  ) {
    return null;
  }
  const title = checkoutLineItemTitle(groupView);
  const item = matchCheckoutIssueItem(activeJob, title);
  const currentQuantity = checkoutLineItemQuantity(groupView);
  const removeButton = [...groupView.querySelectorAll("a.js-delete-button, a[aria-label='Remove item'], a, button")]
    .find((element) => visible(element) && normalizedText(element.getAttribute?.("aria-label") || element.innerText || element.textContent).includes("remove item"));
  return {
    item,
    title,
    currentQuantity,
    removeButton,
    message: "Limit purchase: Amazon says this item has limited purchase quantity and the business has already reached it.",
  };
}

function checkoutLimitPurchaseIssue(activeJob) {
  const panel = document.querySelector("#checkout-javaItemSelectPanel");
  if (!panel || !visible(panel)) return null;
  const panelText = normalizedText(panel.innerText || panel.textContent);
  if (
    !panelText.includes("limited purchase quantity") ||
    !panelText.includes("business has reached it")
  ) {
    return null;
  }

  const lineIssue = checkoutLineItemGroups(panel)
    .map((groupView) => checkoutLineItemLimitCandidate(activeJob, groupView))
    .find(Boolean);
  if (lineIssue) return lineIssue;

  const messageNode = [...panel.querySelectorAll("[data-messageid='AmazonBusinessCVMessages'], .line-item-destination-message-groups")]
    .find((element) => normalizedText(element.innerText || element.textContent).includes("limited purchase quantity"));
  const parentView = messageNode?.closest?.("[id^='line-item-parent-view-']");
  const parentId = parentView?.id || "";
  const suffix = parentId.startsWith("line-item-parent-view-") ? parentId.slice("line-item-parent-view-".length) : "";
  const groupView = suffix ? document.getElementById(`line-item-group-display-${suffix}`) : messageNode?.closest?.("[id^='line-item-group-display-']");
  const titleNode = suffix ? document.getElementById(`item-block-product-title-${suffix}`) : groupView?.querySelector?.(".lineitem-title-text, [id^='item-block-product-title-']");
  const title = String(titleNode?.innerText || titleNode?.textContent || "").replace(/\s+/g, " ").trim();
  const item = matchCheckoutIssueItem(activeJob, title);
  const removeButton = [...(groupView || parentView || panel).querySelectorAll?.("a.js-delete-button, a[aria-label='Remove item'], a, button") || []]
    .find((element) => visible(element) && normalizedText(element.getAttribute?.("aria-label") || element.innerText || element.textContent).includes("remove item"));
  const currentQuantity = checkoutLineItemQuantity(groupView);
  return {
    item,
    title,
    currentQuantity,
    removeButton,
    message: "Limit purchase: Amazon says this item has limited purchase quantity and the business has already reached it.",
  };
}

function cartDeleteButtons() {
  const activeCart = document.querySelector("#sc-active-cart");
  if (!activeCart || !visible(activeCart)) return [];
  const deleteSelector = [
    "input[data-action*='delete' i]",
    "button[data-action*='delete' i]",
    "input[name*='delete-active' i]",
    "button[name*='delete-active' i]",
    "input[name*='delete' i]",
    "button[name*='delete' i]",
    "button[aria-label*='Delete' i]",
    "input[aria-label*='Delete' i]",
    "button[title*='Delete' i]",
    "input[value*='Delete' i]",
    "button[value*='Delete' i]",
  ].join(", ");
  const isDeleteControl = (button) => {
    if (!button || !visible(button)) return false;
    const label = normalizedText([
      button.getAttribute?.("aria-label"),
      button.getAttribute?.("title"),
      button.getAttribute?.("value"),
      button.getAttribute?.("name"),
      button.getAttribute?.("data-action"),
      button.innerText,
      button.textContent,
    ].filter(Boolean).join(" "));
    return /\bdelete\b/i.test(label) && !/save for later|saved for later|share/i.test(label);
  };
  return [
    ...activeCart.querySelectorAll(deleteSelector),
  ].filter(isDeleteControl);
}

function cartLooksEmpty() {
  const text = (document.body.innerText || "").replace(/\s+/g, " ").toLowerCase();
  return (
    text.includes("your amazon cart is empty") ||
    text.includes("your shopping cart is empty") ||
    text.includes("cart is empty")
  );
}

function cartItemQuantity(item) {
  const itemQuantity = Number(String(item?.getAttribute?.("data-quantity") || "").replace(/[^\d.]/g, ""));
  if (Number.isFinite(itemQuantity) && itemQuantity > 0) return itemQuantity;
  const stepperValue = [
    "[data-a-selector='inner-value']",
    "[data-a-selector='value'] [data-a-selector='inner-value']",
    "[data-action='a-stepper-spinbutton'] [data-a-selector='value']",
    ".a-stepper [data-a-selector='value']",
    ".a-stepper-value-live",
  ]
    .map((selector) => [...item.querySelectorAll(selector)].find(visible))
    .find(Boolean);
  if (stepperValue) {
    const value = Number((stepperValue.innerText || stepperValue.textContent || "").replace(/[^\d.]/g, ""));
    if (Number.isFinite(value) && value > 0) return value;
  }
  const quantityButton = [...item.querySelectorAll("button[aria-label*='quantity' i], button[aria-label*='Increase quantity' i], button[aria-label*='Decrease quantity' i]")]
    .find((button) => visible(button) && /\b\d+\b/.test(button.getAttribute("aria-label") || ""));
  if (quantityButton) {
    const value = Number((quantityButton.getAttribute("aria-label") || "").match(/\b(\d+)\b/)?.[1]);
    if (Number.isFinite(value) && value > 0) return value;
  }
  const prompt = [...item.querySelectorAll(".a-dropdown-prompt, [data-a-class*='quantity' i] .a-button-text")]
    .find((node) => visible(node) && /^\s*\d+\s*$/.test(node.innerText || node.textContent || ""));
  if (prompt) {
    const value = Number((prompt.innerText || prompt.textContent || "").trim());
    if (Number.isFinite(value) && value > 0) return value;
  }
  const select = item.querySelector("select[name='quantity'], select[id*='quantity']");
  if (select?.value) {
    const value = Number(select.value);
    if (Number.isFinite(value) && value > 0) return value;
  }
  const input = item.querySelector("input[name='quantity'], input[id*='quantity']");
  if (input?.value) {
    const value = Number(input.value);
    if (Number.isFinite(value) && value > 0) return value;
  }
  const text = (item.innerText || item.textContent || "").replace(/\s+/g, " ");
  const match = text.match(/\bQty(?:uantity)?\s*:?\s*(\d+)\b/i);
  return match ? Number(match[1]) : 1;
}

function cartActiveRoots() {
  const selectors = [
    "#sc-active-cart",
    "#activeCartViewForm",
    "form[action*='/cart'] [data-name='Active Items']",
    "[data-name='Active Items']",
    "[data-csa-c-content-id*='activeCart' i]",
  ];
  const roots = selectors
    .flatMap((selector) => [...document.querySelectorAll(selector)])
    .filter((root) => root && visible(root));
  return roots.filter((root, index) => !roots.some((other, otherIndex) => (
    otherIndex !== index && other.contains(root)
  )));
}

function cartActiveItems() {
  const roots = cartActiveRoots();
  if (!roots.length) return [];
  const candidates = [
    ...roots.flatMap((root) => [
      ...root.querySelectorAll(
        "[data-itemtype='active'], .sc-list-item, [data-asin], [data-csa-c-asin], [role='listitem']",
      ),
    ]),
  ];

  const seen = new Set();
  const filtered = candidates
    .filter((item) => {
      if (!item || !visible(item)) return false;
      const text = (item.innerText || item.textContent || "").replace(/\s+/g, " ");
      if (/saved for later|sponsored|discover more|buy it again/i.test(text)) return false;
      if (!cartItemAsin(item)) return false;
      if (!item.querySelector?.("button[aria-label*='Delete' i], input[aria-label*='Delete' i], button[title*='Delete' i], input[value*='Delete' i], button[value*='Delete' i], button[data-action*='delete' i], input[data-action*='delete' i], button[name*='delete' i], input[name*='delete' i], [data-itemtype='active'], .sc-list-item, [data-quantity], [data-a-selector*='stepper' i], select[name='quantity'], input[name='quantity']")) return false;
      if (seen.has(item)) return false;
      seen.add(item);
      return true;
    });
  return filtered.filter((item) => {
    const asin = cartItemAsin(item);
    return !filtered.some((other) => {
      if (other === item || !item.contains(other)) return false;
      const otherAsin = cartItemAsin(other);
      return !asin || !otherAsin || asin === otherAsin;
    });
  });
}

function cartIsVisiblyEmpty() {
  const emptyCart = document.querySelector("#sc-empty-cart");
  const emptyHeading = [...document.querySelectorAll("h1, h2, h3")].find((element) => (
    visible(element) && /your amazon cart is empty/i.test(element.innerText || element.textContent || "")
  ));
  return Boolean((emptyCart && visible(emptyCart)) || emptyHeading);
}

function addedItemMarkerKey(activeJob) {
  return `nutricity-added-${activeJob?.job?.group_key || "active"}`;
}

function checkoutMarkerKey(activeJob) {
  return `nutricity-checkout-${activeJob?.job?.group_key || "active"}`;
}

function markItemAdded(activeJob) {
  try {
    sessionStorage.setItem(addedItemMarkerKey(activeJob), String(Date.now()));
  } catch {
    // Session storage can be blocked on unusual browser profiles; state storage still carries the marker.
  }
}

function markCheckoutStarted(activeJob) {
  activeJob.checkoutStartedAt = Date.now();
  try {
    sessionStorage.setItem(checkoutMarkerKey(activeJob), String(activeJob.checkoutStartedAt));
  } catch {
    // State storage still carries the marker when session storage is unavailable.
  }
}

function clearCheckoutStarted(activeJob) {
  activeJob.checkoutStartedAt = null;
  try {
    sessionStorage.removeItem(checkoutMarkerKey(activeJob));
  } catch {
    // State storage still clears the marker when session storage is unavailable.
  }
}

function itemWasAdded(activeJob) {
  if (activeJob?.addClickedAt || Object.keys(activeJob?.pricing || {}).length) return true;
  try {
    return Boolean(sessionStorage.getItem(addedItemMarkerKey(activeJob)));
  } catch {
    return false;
  }
}

function checkoutWasStarted(activeJob) {
  if (activeJob?.checkoutStartedAt || ["subscribe_checkout", "checkout", "editing_address", "complete_pending", "find_order_id"].includes(String(activeJob?.stage || ""))) return true;
  try {
    return Boolean(sessionStorage.getItem(checkoutMarkerKey(activeJob)));
  } catch {
    return false;
  }
}

function canClearCart(activeJob) {
  return (
    activeJob?.stage === "clear_cart" &&
    !activeJob.cartCleared &&
    !activeJob.addClickedAt &&
    !activeJob.checkoutStartedAt &&
    Number(activeJob.itemIndex || 0) === 0
  );
}

function cartItemAsin(item) {
  const attributeNodes = [item, ...item.querySelectorAll("[data-asin], [data-csa-c-asin]")];
  for (const node of attributeNodes) {
    const direct = String(node.getAttribute("data-asin") || node.getAttribute("data-csa-c-asin") || "").trim().toUpperCase();
    if (/^[A-Z0-9]{10}$/.test(direct)) return direct;
  }
  for (const anchor of item.querySelectorAll("a[href]")) {
    const href = anchor.href || anchor.getAttribute("href") || "";
    const match = href.match(/\/(?:dp|gp\/product|gp\/aw\/d|product-reviews)\/([A-Z0-9]{10})(?:[/?#]|$)/i)
      || href.match(/[?&](?:asin|ASIN)=([A-Z0-9]{10})(?:[&#]|$)/);
    if (match) return match[1].toUpperCase();
  }
  const markup = String(item.outerHTML || "");
  const markupMatch = markup.match(/(?:data-(?:csa-c-)?asin=["']|\/(?:dp|gp\/product|gp\/aw\/d)\/)([A-Z0-9]{10})(?:["'/?#&]|$)/i);
  if (markupMatch) return markupMatch[1].toUpperCase();
  const textMatch = (item.innerText || item.textContent || "").match(/\b(B[A-Z0-9]{9})\b/i);
  return textMatch ? textMatch[1].toUpperCase() : "";
}

function cartDiagnosticSummary() {
  const items = cartActiveItems();
  const parts = items.map((item) => {
    const asin = cartItemAsin(item) || "unreadable-ASIN";
    return `${asin} qty ${cartItemQuantity(item)}`;
  });
  const roots = cartActiveRoots().length;
  return parts.length
    ? `Detected cart rows: ${parts.join(", ")}.`
    : `Detected ${roots} active cart container(s), but no readable active item rows.`;
}

function expectedCartQuantities(activeJob) {
  const expected = {};
  for (const item of activeJob.job?.items || []) {
    const originalAsin = String(item.asin || "").toUpperCase();
    const pricing = activeJob.pricing?.[originalAsin] || activeJob.pricing?.[item.asin] || null;
    const asin = String(pricing?.purchased_asin || originalAsin).toUpperCase();
    if (!asin) continue;
    expected[asin] = (expected[asin] || 0) + Number(pricing?.quantity || item.quantity || 1);
  }
  return expected;
}

function smartWagonAddedCartState(activeJob, expected) {
  const bodyText = (document.body?.innerText || document.body?.textContent || "").replace(/\s+/g, " ");
  const lower = bodyText.toLowerCase();
  const onSmartWagon = /\/cart\/smart-wagon/i.test(location.pathname) || /[?&]ref_=sw_/i.test(location.href);
  const added = lower.includes("added to cart");
  const checkoutButton = findButtonByText(["proceed to checkout", "check out amazon cart"]);
  if (!added || (!onSmartWagon && !checkoutButton)) return null;

  const expectedTotal = Object.values(expected || {}).reduce((sum, quantity) => sum + Number(quantity || 0), 0);
  const buttonText = (checkoutButton?.value || checkoutButton?.title || checkoutButton?.innerText || checkoutButton?.textContent || "").replace(/\s+/g, " ");
  const countMatch =
    buttonText.match(/\((\d+)\s+items?\)/i) ||
    bodyText.match(/cart\s+subtotal\s*:\s*\$?[\d,.]+\s*\((\d+)\s+items?\)/i) ||
    bodyText.match(/subtotal\s*\((\d+)\s+items?\)/i);
  const cartCount = countMatch ? Number(countMatch[1]) : null;
  if (Number.isFinite(cartCount) && expectedTotal && cartCount < expectedTotal) {
    return {
      ok: false,
      message: `Amazon added-to-cart page shows ${cartCount} cart item(s), but expected ${expectedTotal}.`,
      mismatches: Object.entries(expected || {}).map(([asin, quantity]) => ({ asin, expected: Number(quantity), actual: 0 })),
    };
  }
  return {
    ok: true,
    warning: Number.isFinite(cartCount)
      ? `Amazon added-to-cart page shows ${cartCount} item(s). Proceeding to checkout.`
      : "Amazon added-to-cart page is visible. Proceeding to checkout.",
  };
}

function verifyCartQuantities(activeJob) {
  const activeCart = cartActiveRoots()[0] || null;
  const expected = expectedCartQuantities(activeJob);
  if (!Object.keys(expected).length) return { ok: true, exact: false };
  const items = cartActiveItems();
  if ((!activeCart || !visible(activeCart)) && !items.length) {
    const smartWagon = smartWagonAddedCartState(activeJob, expected);
    if (smartWagon) return smartWagon;
    if (cartIsVisiblyEmpty()) {
      const details = Object.entries(expected).map(([asin, quantity]) => ({
        asin,
        expected: Number(quantity),
        actual: 0,
        mismatch_type: "missing_from_cart",
      }));
      return {
        ok: false,
        message: `Amazon cart is empty after Add to cart. ${details.map((item) => `${item.asin} expected ${item.expected}, cart has 0`).join("; ")}`,
        mismatches: details,
      };
    }
    return { ok: false, message: "Could not find Amazon active cart items after Add to cart.", mismatches: [] };
  }
  const actual = {};
  const unknownItems = [];
  for (const item of items) {
    const asin = cartItemAsin(item);
    if (!asin) {
      unknownItems.push((item.innerText || item.textContent || "").replace(/\s+/g, " ").trim().slice(0, 120));
      continue;
    }
    if (!expected[asin]) continue;
    actual[asin] = (actual[asin] || 0) + cartItemQuantity(item);
  }
  if (!Object.keys(actual).length && items.length && unknownItems.length) {
    return { ok: true, exact: false, warning: `Could not read ASIN from Amazon cart markup. Found ${items.length} active cart item(s), so checkout was not blocked.` };
  }
  const mismatches = Object.entries(expected).filter(([asin, quantity]) => Number(actual[asin] || 0) !== Number(quantity));
  if (!mismatches.length) return { ok: true, exact: true, quantities: actual };
  const details = mismatches.map(([asin, quantity]) => {
    const expectedQuantity = Number(quantity);
    const actualQuantity = Number(actual[asin] || 0);
    return {
      asin,
      expected: expectedQuantity,
      actual: actualQuantity,
      mismatch_type: actualQuantity > expectedQuantity ? "over" : "under",
    };
  });
  const message = mismatches
    .map(([asin, quantity]) => `${asin} expected ${quantity}, cart has ${actual[asin] || 0}`)
    .join("; ");
  return { ok: false, message, mismatches: details };
}

function cartQuantityForAsin(asin) {
  const expectedAsin = String(asin || "").trim().toUpperCase();
  if (!expectedAsin) return 0;
  return cartActiveItems().reduce((total, item) => (
    cartItemAsin(item) === expectedAsin ? total + cartItemQuantity(item) : total
  ), 0);
}

function shouldRetryVerifiedCartAfterCheckout(activeJob, cartCheck) {
  return (
    cartCheck?.ok === true
    && cartCheck?.exact === true
    && Number(activeJob?.cartAfterCheckoutRetryCount || 0) < 1
  );
}

function lineIdForAsin(activeJob, asin) {
  const wanted = String(asin || "").toUpperCase();
  const item = (activeJob.job?.items || []).find((entry) => String(entry.asin || "").toUpperCase() === wanted);
  if (item) return itemPrimaryLineId(item);
  const pricingItem = Object.values(activeJob.pricing || {}).find((entry) => {
    const purchasedAsin = String(entry.purchased_asin || "").toUpperCase();
    const originalAsin = String(entry.asin || "").toUpperCase();
    return purchasedAsin === wanted || originalAsin === wanted;
  });
  if (!pricingItem?.asin) return null;
  const originalItem = (activeJob.job?.items || []).find((entry) => String(entry.asin || "").toUpperCase() === String(pricingItem.asin || "").toUpperCase());
  return originalItem ? itemPrimaryLineId(originalItem) : null;
}

function promotionNodes() {
  const roots = [
    ...document.querySelectorAll(
      ".promoPriceBlockMessage, [data-csa-c-owner='PromotionsDiscovery'], #promoPriceBlockMessage_feature_div, #couponFeature, [id*='coupon'][id*='Feature']",
    ),
  ].filter(visible);
  const nodes = roots.filter((root) => {
    const text = (root.innerText || root.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
    if (!text || text.length > 600) return false;
    if (!/\b(coupon|promotion|promo|save)\b/i.test(text)) return false;
    const redeemed = [...root.querySelectorAll("[id^='doneButton'], .a-color-success")].some((node) => {
      const nodeText = (node.innerText || node.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
      return visible(node) && nodeText.includes("redeemed");
    });
    if (redeemed) return false;
    const hasRedeem = [...root.querySelectorAll("span.a-button:not(.a-button-disabled), input.a-button-input[type='submit'], button")].some((candidate) => promoRedeemTarget(candidate));
    const hasCouponCheckbox = [...root.querySelectorAll("input[type='checkbox']:not(:checked), .a-checkbox:not(.aok-hidden) label, .a-icon-checkbox")]
      .some((candidate) => visible(candidate) || visible(candidate.closest?.("label, .a-checkbox, span, div")));
    return hasRedeem || hasCouponCheckbox;
  });
  return nodes.filter(visible).slice(0, 12);
}

function savingsRoots(context = "regular") {
  return context === "sns"
    ? [
        document.querySelector("#snsAccordionRowMiddle"),
        document.querySelector("#snsAccordionRow"),
        document.querySelector("#reinvent_price_desktop_snsAccordionRowMiddle"),
        document.querySelector("#promoPriceBlockMessage_feature_div"),
        document.body,
      ].filter(Boolean)
    : [
        document.querySelector("#reinvent_price_desktop_newAccordionRow"),
        document.querySelector("#promoPriceBlockMessage_feature_div"),
        document.body,
      ].filter(Boolean);
}

function promoRedeemTarget(candidate) {
  const button = candidate.closest?.("span.a-button") || candidate;
  if (button.classList?.contains("a-button-disabled") || button.closest?.(".aok-hidden")) return null;
  const text = (button.innerText || button.textContent || candidate.value || "").replace(/\s+/g, " ").trim().toLowerCase();
  if (text !== "redeem") return null;
  const promoBlock = button.closest?.(".promoPriceBlockMessage, [data-csa-c-owner='PromotionsDiscovery'], #promoPriceBlockMessage_feature_div");
  if (!promoBlock) return null;
  const clickTarget = button.closest(".a-declarative") || candidate;
  return visible(clickTarget) || visible(button) ? clickTarget : null;
}

async function clickVisibleRedeemPromotions(context = "regular") {
  const clicked = new Set();
  let count = 0;
  for (const root of savingsRoots(context)) {
    const candidates = [
      ...root.querySelectorAll("span.a-button:not(.a-button-disabled), input.a-button-input[type='submit']"),
    ];
    for (const candidate of candidates) {
      const target = promoRedeemTarget(candidate);
      if (!target || clicked.has(target)) continue;
      target.scrollIntoView({ block: "center", behavior: "smooth" });
      await sleep(350);
      target.click();
      clicked.add(target);
      count += 1;
      await waitForStableDom(700, 5000);
      await sleep(1300);
    }
  }
  if (count) {
    showPanel("Nutricity fulfilment", `Redeemed ${count} Amazon promotion${count === 1 ? "" : "s"}.`, null, null);
  }
  return count;
}

async function clipVisibleCoupons(context = "regular") {
  const roots = savingsRoots(context);
  const clicked = new Set();
  let count = 0;
  for (const root of roots) {
    const candidates = [
      ...root.querySelectorAll("input[type='checkbox']:not(:checked)"),
      ...root.querySelectorAll("[data-csa-c-action='clipPromotion'] input[type='checkbox']:not(:checked), [data-csa-c-owner='PromotionsDiscovery'] input[type='checkbox']:not(:checked)"),
      ...root.querySelectorAll(".a-checkbox:not(.aok-hidden) label, .a-icon-checkbox"),
    ].filter((element) => visible(element) || visible(element.closest?.("label, .a-checkbox, span, div")));
    for (const candidate of candidates) {
      const target = candidate.matches?.("input[type='checkbox']") ? candidate : candidate.closest?.("label") || candidate;
      if (clicked.has(target)) continue;
      const nearbyText = (target.closest?.(".promoPriceBlockMessage, [data-csa-c-owner='PromotionsDiscovery'], #promoPriceBlockMessage_feature_div")?.innerText || target.parentElement?.innerText || "").toLowerCase();
      if (!nearbyText.includes("coupon") && !nearbyText.includes("save")) continue;
      target.scrollIntoView({ block: "center", behavior: "smooth" });
      await sleep(350);
      target.click();
      clicked.add(target);
      count += 1;
      await sleep(1200);
    }
  }
  if (count) {
    showPanel("Nutricity fulfilment", `Applied ${count} Amazon coupon${count === 1 ? "" : "s"}.`, null, null);
  }
  return count;
}

function unavailableMessage() {
  const bodyText = (document.body?.innerText || document.body?.textContent || "").replace(/\s+/g, " ").trim();
  const loweredBody = bodyText.toLowerCase();
  if (
    loweredBody.includes("not available to business prime") ||
    loweredBody.includes("isn't available to business prime") ||
    loweredBody.includes("is not available to business prime")
  ) {
    return "Amazon shows an offer, but it is not available to Business Prime and has no direct Add to cart option for this account.";
  }
  const allBuyingOptions = [...document.querySelectorAll(
    "a[href*='/gp/offer-listing/'], a[href*='/offer-listing/'], a[title='See All Buying Options'], a.a-button-text",
  )].find((element) => {
    const text = (element.innerText || element.textContent || element.getAttribute("title") || "").replace(/\s+/g, " ").trim().toLowerCase();
    return visible(element) && text.includes("see all buying options");
  });
  if (allBuyingOptions) {
    return "Amazon only shows See All Buying Options, so this ASIN is not directly available to add to cart.";
  }
  const roots = [
    ...document.querySelectorAll("#availability, #outOfStock, #availabilityInsideBuyBox_feature_div, #desktop_qualifiedBuyBox, #buybox, #centerCol"),
  ].filter(Boolean);
  for (const root of roots) {
    if (!visible(root) && root.id !== "centerCol") continue;
    const text = (root.innerText || root.textContent || "").replace(/\s+/g, " ").trim();
    const lowered = text.toLowerCase();
    if (
      lowered.includes("currently unavailable") ||
      lowered.includes("we don't know when or if this item will be back in stock") ||
      lowered.includes("we do not know when or if this item will be back in stock")
    ) {
      return text.match(/currently unavailable[^.]*\./i)?.[0] || "Currently unavailable. We do not know when or if this item will be back in stock.";
    }
  }
  return "";
}

function availabilityPrice() {
  const selectors = [
    "#corePrice_feature_div .a-offscreen",
    "#apex_desktop .a-price .a-offscreen",
    ".a-price .a-offscreen",
    "#priceblock_ourprice",
    "#priceblock_dealprice",
    "#price_inside_buybox",
  ];
  for (const selector of selectors) {
    const element = [...document.querySelectorAll(selector)].find((node) => visible(node));
    const text = (element?.innerText || element?.textContent || "").replace(/\s+/g, " ").trim();
    const match = text.match(/(?:\$|USD\s*)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/i);
    if (match) return Number(match[1].replace(/,/g, ""));
  }
  return 0;
}

function parseAmazonDeliveryPromise(text = "", limitDays = DEFAULT_DELIVERY_LIMIT_DAYS) {
  const normalized = normalizedText(text);
  if (!normalized) return { text: "", earliest: null, latest: null, daysFromToday: null, late: false };
  const monthPattern = "Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?";
  const matches = [...normalized.matchAll(new RegExp(`\\b(${monthPattern})\\.?\\s+(\\d{1,2})(?:,\\s*(\\d{4}))?`, "gi"))];
  const today = new Date();
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const dates = matches.map((match) => {
    const year = Number(match[3] || today.getFullYear());
    const month = new Date(`${match[1]} 1, ${year}`).getMonth();
    const date = new Date(year, month, Number(match[2]));
    if (!match[3] && date < new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1)) date.setFullYear(date.getFullYear() + 1);
    return date;
  }).filter((date) => Number.isFinite(date.getTime()));
  if (!dates.length) {
    const lower = normalized.toLowerCase();
    if (/arriving\s+today|deliver(?:y|ing)?\s+today/.test(lower)) {
      return { text: normalized, earliest: startOfToday, latest: startOfToday, daysFromToday: 0, late: false };
    }
    if (/arriving\s+tomorrow|deliver(?:y|ing)?\s+tomorrow/.test(lower)) {
      const tomorrow = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
      return { text: normalized, earliest: tomorrow, latest: tomorrow, daysFromToday: 1, late: false };
    }
    return { text: normalized, earliest: null, latest: null, daysFromToday: null, late: false };
  }
  const earliest = new Date(Math.min(...dates.map((date) => date.getTime())));
  const latest = new Date(Math.max(...dates.map((date) => date.getTime())));
  const daysFromToday = Math.ceil((earliest.getTime() - startOfToday.getTime()) / 86400000);
  return {
    text: normalized,
    earliest,
    latest,
    daysFromToday,
    late: daysFromToday > normalizedDeliveryLimitDays(limitDays),
  };
}

function productDeliveryPromiseText() {
  const deliveryTime = [...document.querySelectorAll("#deliveryBlockMessage [data-csa-c-delivery-time], #mir-layout-DELIVERY_BLOCK [data-csa-c-delivery-time]")]
    .filter((element) => visible(element))
    .map((element) => normalizedText(element.getAttribute("data-csa-c-delivery-time") || ""))
    .find(Boolean);
  if (deliveryTime) return deliveryTime;
  return [...document.querySelectorAll("#deliveryBlockMessage, #mir-layout-DELIVERY_BLOCK, #delivery-message, #ddmDeliveryMessage")]
    .filter((element) => visible(element))
    .map((element) => normalizedText(element.textContent || ""))
    .find((text) => text && /deliver|arriv/i.test(text)) || "";
}

function productDeliveryPromise(limitDays = DEFAULT_DELIVERY_LIMIT_DAYS) {
  return parseAmazonDeliveryPromise(productDeliveryPromiseText(), limitDays);
}

function checkAsinAvailability(asin = "", deliveryLimitDays = DEFAULT_DELIVERY_LIMIT_DAYS) {
  const pageAsin = currentAsinFromUrl();
  const unavailable = unavailableMessage();
  const addToCart = [...document.querySelectorAll("#add-to-cart-button, input#add-to-cart-button, #submit.add-to-cart")].find((node) => visible(node) && !node.disabled);
  const buyNow = [...document.querySelectorAll("#buy-now-button, input#buy-now-button")].find((node) => visible(node) && !node.disabled);
  const availabilityText = [...document.querySelectorAll("#availability, #availabilityInsideBuyBox_feature_div")]
    .map((node) => (node.innerText || node.textContent || "").replace(/\s+/g, " ").trim())
    .find(Boolean) || "";
  const bodyText = (document.body?.innerText || "").toLowerCase();
  const blockedText = /currently unavailable|temporarily out of stock|see all buying options|not available to business prime|isn't available to business prime|we don't know when or if this item will be back in stock|we do not know when or if this item will be back in stock/i.test(bodyText);
  const directlyOrderable = Boolean(addToCart || buyNow);
  const deliveryPromise = productDeliveryPromise(deliveryLimitDays);
  const inStock = Boolean(!unavailable && !blockedText && directlyOrderable && !deliveryPromise.late);
  const notDirectlyOrderableMessage = /in stock/i.test(`${availabilityText} ${bodyText}`)
    ? "Amazon shows this ASIN as in stock, but no direct Add to cart or Buy Now control is available for this Business account."
    : "Amazon did not show this ASIN as directly available.";
  const deliveryMessage = deliveryPromise.late
    ? `Amazon's earliest estimated delivery is ${deliveryPromise.daysFromToday} days away (${deliveryPromise.text}), beyond the ${normalizedDeliveryLimitDays(deliveryLimitDays)}-day limit.`
    : "";
  return {
    ok: true,
    asin: pageAsin || String(asin || "").trim().toUpperCase(),
    in_stock: inStock,
    message: inStock ? (availabilityText || "Amazon product controls are available.") : (deliveryMessage || unavailable || availabilityText || notDirectlyOrderableMessage),
    price: availabilityPrice(),
    delivery_text: deliveryPromise.text,
    delivery_days: deliveryPromise.daysFromToday,
    delivery_limit_days: normalizedDeliveryLimitDays(deliveryLimitDays),
    url: location.href,
  };
}

function amazonProductPageErrorMessage() {
  const title = String(document.title || "").replace(/\s+/g, " ").trim();
  const bodyText = String(document.body?.innerText || document.body?.textContent || "").replace(/\s+/g, " ").trim();
  const loweredTitle = title.toLowerCase();
  const loweredBody = bodyText.toLowerCase();
  if (
    loweredTitle.includes("page not found") ||
    loweredTitle.includes("404") ||
    loweredBody.includes("sorry! we couldn't find that page") ||
    loweredBody.includes("sorry we couldn't find that page") ||
    loweredBody.includes("looking for something?") ||
    loweredBody.includes("the web address you entered is not a functioning page")
  ) {
    return "Amazon opened a 404 / product page not found page.";
  }
  if (
    loweredBody.includes("no results for") ||
    loweredBody.includes("did not match any products") ||
    loweredBody.includes("couldn't find a match")
  ) {
    return "Amazon could not find a product page for this ASIN.";
  }
  return "";
}

function removeMissingItemFromActiveJob(activeJob, item, purchaseItem = item) {
  if (!item) return activeJob.job?.items || [];
  const missingLineId = itemPrimaryLineId(item);
  const missingAsins = new Set([item?.asin, purchaseItem?.asin].filter(Boolean).map((asin) => String(asin).toUpperCase()));
  const currentIndex = Number(activeJob.itemIndex || 0);
  const remainingItems = (activeJob.job?.items || []).filter((entry) => {
    const lineId = itemPrimaryLineId(entry);
    if (missingLineId && lineId === missingLineId) return false;
    if (missingLineId) return true;
    return !missingAsins.has(String(entry.asin || "").toUpperCase());
  });
  activeJob.job.items = remainingItems;
  activeJob.job.line_ids = (activeJob.job.line_ids || []).filter((lineId) => !missingLineId || lineId !== missingLineId);
  if (activeJob.pricing) {
    for (const asin of missingAsins) delete activeJob.pricing[asin];
  }
  activeJob.itemIndex = currentIndex;
  return remainingItems;
}

async function continueAfterPartialMissing(activeJob, item, purchaseItem, message, failureCode = "unavailable", details = {}) {
  if (!item) return false;
  const lineId = itemPrimaryLineId(item);
  if (!lineId) return false;
  if (!await shouldFulfilAvailableMixedAsin(activeJob)) return false;
  showPanel("Sending line to Missing ASINs", message, null, null);
  const result = await send({
    type: "MARK_LINE_MISSING",
    message,
    missingAsin: item?.asin || purchaseItem?.asin || currentAsinFromUrl(),
    missingLineId: lineId,
    failureCode,
    requestedQuantity: details.requestedQuantity ?? null,
    fulfilledQuantity: details.fulfilledQuantity ?? null,
    availableQuantity: details.availableQuantity ?? null,
  });
  if (!result?.ok) return false;
  if (result.next_job_started) {
    showPanel("Missing ASINs", `${message} ${result.message || "Order moved to Missing ASINs."} Started next order ${result.next_group_key}.`, null, null);
    return true;
  }
  const remainingItems = removeMissingItemFromActiveJob(activeJob, item, purchaseItem);
  if (!remainingItems.length) {
    activeJob.stage = "cleanup_after_failure";
    activeJob.cleanupAfterFailure = true;
    activeJob.cleanupReason = message || "Order was moved to Missing ASINs. Cleaning cart before continuing.";
    activeJob.paused = false;
    await setActiveJob(activeJob, { allowItemRemoval: true, reason: "partial_missing_line_removed" });
    showPanel("Missing ASINs", `${message} ${result.message || "Order moved to Missing ASINs."}`, null, null);
    location.href = "https://www.amazon.com/cart?ref_=sw_gtc";
    return true;
  }
  if (activeJob.itemIndex < remainingItems.length) {
    activeJob.stage = "product";
    await setActiveJob(activeJob, { allowItemRemoval: true, reason: "partial_missing_line_removed" });
    showPanel("Split fulfilment", `${message} ${result.message || ""} Continuing with remaining Amazon item(s).`, null, null);
    location.href = `https://www.amazon.com/dp/${remainingItems[activeJob.itemIndex].asin}`;
  } else {
    activeJob.stage = "cart";
    await setActiveJob(activeJob, { allowItemRemoval: true, reason: "partial_missing_line_removed" });
    showPanel("Split fulfilment", `${message} ${result.message || ""} Proceeding to checkout for remaining Amazon item(s).`, null, null);
    location.href = "https://www.amazon.com/cart?ref_=sw_gtc";
  }
  return true;
}

async function shouldFulfilAvailableMixedAsin(activeJob) {
  if ((activeJob.job?.items || []).length <= 1) return true;
  const state = await getExtensionState();
  return state.fulfilAvailableMixedAsin === true;
}

async function failCurrentItemAsMissing(activeJob, item, purchaseItem, message, failureCode = "unavailable", details = {}) {
  if (await continueAfterPartialMissing(activeJob, item, purchaseItem, message, failureCode, details)) return true;
  showPanel("Sending to Missing ASINs", message, null, null);
  const result = await send({
    type: "FAIL_JOB",
    message,
    missingAsin: item?.asin || purchaseItem?.asin || currentAsinFromUrl(),
    missingLineId: item ? itemPrimaryLineId(item) : null,
    failureCode,
    requestedQuantity: details.requestedQuantity ?? null,
    fulfilledQuantity: details.fulfilledQuantity ?? null,
    availableQuantity: details.availableQuantity ?? null,
  });
  if (result?.ok) {
    if (result.cleanup_required) {
      activeJob.stage = "cleanup_after_failure";
      activeJob.cleanupAfterFailure = true;
      activeJob.cleanupReason = message || "Order was moved to Missing ASINs. Cleaning cart before continuing.";
      activeJob.paused = false;
      await setActiveJob(activeJob);
      location.href = "https://www.amazon.com/cart?ref_=sw_gtc";
    }
    showPanel("Missing ASINs", `${message} ${result.message || "Order moved to Missing ASINs."}`, null, null);
    return true;
  }
  throw new Error(result?.message || "The app did not confirm the Missing ASIN report.");
}

async function failCurrentJobAsChromeError(activeJob, message, failureCode = "chrome_error", details = {}) {
  showPanel("Chrome fulfilment needs review", message, null, null);
  const result = await send({
    type: "FAIL_JOB",
    message,
    missingAsin: "",
    missingLineId: null,
    failureCode,
    requestedQuantity: details.requestedQuantity ?? null,
    fulfilledQuantity: details.fulfilledQuantity ?? null,
    availableQuantity: details.availableQuantity ?? null,
  });
  if (result?.ok) {
    if (result.cleanup_required) {
      activeJob.stage = "cleanup_after_failure";
      activeJob.cleanupAfterFailure = true;
      activeJob.cleanupReason = message || "Chrome fulfilment failed. Cleaning cart before continuing.";
      activeJob.paused = false;
      await setActiveJob(activeJob);
      location.href = "https://www.amazon.com/cart?ref_=sw_gtc";
    }
    showPanel("Chrome fulfilment error", `${message} ${result.message || "Order moved to Chrome error for review."}`, null, null);
    return true;
  }
  throw new Error(result?.message || "The app did not confirm the Chrome error report.");
}

async function applyAdditionalSavings(context = "regular") {
  const redeemed = await clickVisibleRedeemPromotions(context);
  const clipped = await clipVisibleCoupons(context);
  return redeemed + clipped;
}

function markPromotions() {
  const nodes = promotionNodes();
  nodes.forEach((node) => node.classList.add("nutricity-highlight"));
  return nodes.length;
}

async function applySubscribeAndSaveIfCheaper(quantity, activeJob = null, options = {}) {
  await waitForElement(["#corePrice_feature_div .a-price", "#apex_desktop .a-price", "#snsAccordionRowMiddle, #snsAccordionRow, #snsAccordionRowContent, [data-csa-c-slot-id*='sns']"], 12000);
  const { regular: pagePrice, sns: snsPrice } = productPriceSnapshot();
  if (!pagePrice || !snsPrice || snsPrice >= pagePrice) {
    showPanel(
      "Subscribe & Save",
      `Using one-time purchase because Subscribe & Save was not cheaper or could not be read (${moneyText(snsPrice)} vs ${moneyText(pagePrice)}).`,
      null,
      null,
    );
    return false;
  }
  showPanel("Subscribe & Save", `Subscribe & Save is cheaper (${moneyText(snsPrice)} vs ${moneyText(pagePrice)}). Switching to subscription checkout.`, null, null);

  const activated = await activateSubscribeAndSaveOption();
  if (!activated) {
    const error = new Error("Subscribe & Save is cheaper, but Amazon did not activate the Subscribe & Save radio button. Fulfilment paused before touching regular Add to cart quantity.");
    error.failureCode = "";
    throw error;
  }
  const controlsReady = await waitUntil(snsQuantityControlVisible, 8000, 300);
  if (!controlsReady) {
    const error = new Error("Subscribe & Save is cheaper and selected, but Amazon did not show the Subscribe & Save quantity controls. Fulfilment paused before touching regular Add to cart quantity.");
    error.failureCode = "";
    throw error;
  }
  await applyAdditionalSavings("sns");
  const quantitySet = await setQuantity(quantity, "sns");
  if (!quantitySet) {
    const requestedQuantity = Math.max(1, Math.round(Number(quantity || 1)));
    const quantityIssue = window.__nutricityLastQuantityIssue || {};
    const availableQuantity = quantityIssue.availableQuantity || maxPredefinedQuantity("sns") || maxSelectableQuantity("sns");
    const error = new Error(
      availableQuantity > 0 && availableQuantity < requestedQuantity
        ? `Less Subscribe & Save quantity available. Customer ordered ${requestedQuantity}, Amazon only allows ${availableQuantity}. ${quantityIssue.message || ""}`.trim()
        : `Could not set Subscribe & Save quantity ${requestedQuantity}. ${quantityIssue.message || ""}`.trim(),
    );
    error.failureCode = "partial_quantity";
    error.requestedQuantity = requestedQuantity;
    error.fulfilledQuantity = availableQuantity > 0 && availableQuantity < requestedQuantity ? availableQuantity : null;
    error.availableQuantity = availableQuantity > 0 && availableQuantity < requestedQuantity ? availableQuantity : null;
    throw error;
  }
  await configureSubscribeAndSaveDelivery();
  const syncedQuantity = syncSubscribeAndSaveQuantity(quantity);
  if (!syncedQuantity) {
    const requestedQuantity = Math.max(1, Math.round(Number(quantity || 1)));
    const error = new Error(`Subscribe & Save quantity ${requestedQuantity} was visible, but Amazon's checkout form still had a different quantity. Fulfilment paused before checkout.`);
    error.failureCode = "partial_quantity";
    error.requestedQuantity = requestedQuantity;
    throw error;
  }
  if (options.requireCartAdd) {
    const subscriptionCartButton = findSubscribeAddToCartTarget();
    if (!subscriptionCartButton) {
      const error = new Error("Subscribe & Save is cheaper for this multi-item order, but Amazon did not show Add subscription to cart. Fulfilment paused instead of opening a single-item subscription checkout.");
      error.failureCode = "";
      throw error;
    }
    if (activeJob) {
      const asin = currentAsinFromUrl();
      activeJob.stage = "add_clicked";
      activeJob.addClickedAt = Date.now();
      activeJob.subscribeAndSave = false;
      activeJob.subscriptionCartAsins = [...new Set([...(activeJob.subscriptionCartAsins || []), asin].filter(Boolean))];
      markItemAdded(activeJob);
      await setActiveJob(activeJob);
    }
    showPanel("Subscribe & Save", "Adding this subscription to the shared cart before continuing to the next product.", null, null);
    const added = await clickElement(subscriptionCartButton, "Add subscription to cart button");
    if (!added) {
      const error = new Error("Amazon showed Add subscription to cart, but the extension could not click it. Fulfilment paused before continuing to the next product.");
      error.failureCode = "";
      throw error;
    }
    return true;
  }
  const subscribeButtons = findSubscribeSubmitTargets();
  if (subscribeButtons.length) {
    for (const subscribeButton of subscribeButtons) {
      if (activeJob) {
        activeJob.stage = "subscribe_checkout";
        activeJob.addClickedAt = Date.now();
        activeJob.subscribeAndSave = true;
        markCheckoutStarted(activeJob);
        markItemAdded(activeJob);
        await setActiveJob(activeJob);
      }
      showPanel("Subscribe & Save", "Clicking the Subscribe button.", null, null);
      await clickElement(subscribeButton, "Subscribe button");
      dispatchAmazonClickSequence(subscribeButton);
      dispatchClickAtElementCenter(subscribeButton);
      const advanced = await waitUntil(() => /\/checkout/i.test(location.pathname), 2500, 250);
      if (advanced || /\/checkout/i.test(location.pathname)) return true;
    }
    return true;
  }
  return false;
}

function findSubscribeAddToCartTarget() {
  const roots = [...document.querySelectorAll(subscribeAndSaveRootSelector())].filter(visible);
  const selectors = [
    "input#add-to-cart-button[name='submit.add-to-cart']",
    "input[name='submit.add-to-cart']",
    "button#add-to-cart-button",
    "button[name='submit.add-to-cart']",
  ];
  for (const root of roots) {
    for (const selector of selectors) {
      const target = [...root.querySelectorAll(selector)].find((element) => {
        const label = normalizedText([
          element.value,
          element.getAttribute("aria-label"),
          element.getAttribute("title"),
          element.innerText,
          element.textContent,
        ].filter(Boolean).join(" "));
        return visible(element) && !element.disabled && label.includes("add subscription to cart");
      });
      if (target) return target;
    }
  }
  return null;
}

function findSubscribeSubmitTargets() {
  const selectors = [
    "#rcx-subscribe-submit-button input",
    "#rcx-subscribe-submit-button button",
    "#rcx-subscribe-submit-button span.a-button-text",
    "#rcx-subscribe-submit-button span.a-button",
    "#rcx-subscribe-submit-button-announce",
    "button[value='snsText']",
    "input[value='Subscribe']",
    "button[aria-label*='Subscribe' i]",
    "span.a-button:has(input[value='Subscribe'])",
  ];
  const targets = selectors.flatMap((selector) => {
    try {
      return [...document.querySelectorAll(selector)];
    } catch {
      return [];
    }
  });
  const textTarget = findButtonByText(["subscribe"]);
  if (textTarget) targets.push(textTarget);
  return [...new Set(targets)]
    .map((target) => target.closest?.("span.a-button, button, input") || target)
    .filter((target, index, all) => target && all.indexOf(target) === index && visible(target) && !target.disabled);
}

async function activateSubscribeAndSaveOption() {
  if (subscribeAndSaveIsActive()) return true;
  const target = findSubscribeAndSaveAccordionClickTarget();
  if (!target) return false;
  showPanel("Subscribe & Save", "Clicking the Subscribe & Save accordion row.", null, null);
  const clickTargets = [
    target.querySelector?.(".a-accordion-radio.a-icon-radio-inactive")?.closest?.("[data-action='a-accordion'][role='button'], .a-accordion-row[role='button'], .accordion-header[role='button']"),
    target.querySelector?.("[data-action='a-accordion'][role='button']"),
    target.querySelector?.(".a-accordion-row[role='button']"),
    target.querySelector?.(".accordion-header[role='button']"),
    target.matches?.("[data-action='a-accordion'][role='button'], .a-accordion-row[role='button'], .accordion-header[role='button']") ? target : null,
    target,
    target.querySelector?.("h5"),
  ].filter(Boolean);
  for (const clickTarget of [...new Set(clickTargets)]) {
    clickTarget.scrollIntoView({ block: "center", behavior: "smooth" });
    await sleep(250);
    clickTarget.focus?.();
    dispatchAmazonClickSequence(clickTarget);
    clickTarget.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true, cancelable: true }));
    clickTarget.dispatchEvent(new KeyboardEvent("keyup", { key: "Enter", code: "Enter", bubbles: true, cancelable: true }));
    clickTarget.dispatchEvent(new KeyboardEvent("keydown", { key: " ", code: "Space", bubbles: true, cancelable: true }));
    clickTarget.dispatchEvent(new KeyboardEvent("keyup", { key: " ", code: "Space", bubbles: true, cancelable: true }));
    dispatchClickAtElementCenter(clickTarget);
    await waitForStableDom(700, 5000);
    if (subscribeAndSaveIsActive()) return true;
  }
  return await waitUntil(subscribeAndSaveIsActive, 3000, 250);
}

function dispatchAmazonClickSequence(element) {
  const pointerEvent = typeof PointerEvent === "function" ? PointerEvent : MouseEvent;
  element.dispatchEvent(new pointerEvent("pointerdown", { bubbles: true, cancelable: true, pointerType: "mouse", view: window }));
  element.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window }));
  element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: window }));
  element.dispatchEvent(new pointerEvent("pointerup", { bubbles: true, cancelable: true, pointerType: "mouse", view: window }));
  element.click();
}

function dispatchClickAtElementCenter(element) {
  const rect = element?.getBoundingClientRect?.();
  if (!rect?.width || !rect?.height) return false;
  const x = rect.left + Math.min(Math.max(12, rect.width * 0.08), rect.width / 2);
  const y = rect.top + rect.height / 2;
  const target = document.elementFromPoint(x, y) || element;
  dispatchAmazonClickSequence(target);
  return true;
}

function subscribeAndSaveIsActive() {
  return subscribeAndSaveAccordionIsActive();
}

function subscribeAndSaveAccordionIsActive() {
  return Boolean(
    document.querySelector("#snsAccordionRowMiddle .a-accordion-radio-active, #snsAccordionRow .a-accordion-radio-active, #snsAccordionRowContent .a-accordion-radio-active, [data-csa-c-slot-id*='sns'] .a-accordion-radio-active, [data-a-accordion-row-name*='sns'] .a-accordion-radio-active, [data-csa-c-buying-option-type='SNS'] .a-accordion-radio-active") ||
    document.querySelector("#snsAccordionRowMiddle [data-action='a-accordion'][aria-expanded='true'], #snsAccordionRow [data-action='a-accordion'][aria-expanded='true'], #snsAccordionRowContent [data-action='a-accordion'][aria-expanded='true'], [data-csa-c-slot-id*='sns'][data-action='a-accordion'][aria-expanded='true'], [data-a-accordion-row-name*='sns'] [data-action='a-accordion'][aria-expanded='true']") ||
    [...document.querySelectorAll(subscribeAndSaveRootSelector())].some((root) => root.querySelector?.("[role='radio'][aria-checked='true']") || visible(root.querySelector?.("#rcx-subscribe-submit-button, #rcx-subscribe-submit-button-announce, input#rcxsubsQuan"))),
  );
}

function findSubscribeAndSaveAccordionClickTarget() {
  const direct = document.querySelector("#snsAccordionRowMiddle [data-action='a-accordion'][role='button']")
    || document.querySelector("#snsAccordionRowMiddle .a-accordion-row[role='button']")
    || document.querySelector("#snsAccordionRowMiddle")
    || document.querySelector("#snsAccordionRow [data-action='a-accordion'][role='button']")
    || document.querySelector("#snsAccordionRow")
    || document.querySelector("#snsAccordionRowContent [data-action='a-accordion'][role='button']")
    || document.querySelector("#snsAccordionRowContent")
    || document.querySelector("[data-a-accordion-row-name='snsAccordionRowMiddle'] [data-action='a-accordion'][role='button']")
    || document.querySelector("[data-a-accordion-row-name='snsAccordionRowMiddle']")
    || document.querySelector("[data-csa-c-slot-id='snsAccordionRowMiddle'][data-action='a-accordion'][role='button']")
    || document.querySelector("[data-csa-c-slot-id='snsAccordionRowMiddle']")?.closest(".a-accordion-row[role='button'], .a-box, [data-a-accordion-row-name]")
    || findSubscribeAndSaveRadio();
  if (direct) return direct;

  const rows = [
    ...document.querySelectorAll("[data-csa-c-buying-option-type='SNS'], [data-a-accordion-row-name*='sns'], [data-csa-c-slot-id*='sns'], .a-accordion-row, .accordion-header"),
  ].filter((row, index, all) => all.indexOf(row) === index && visible(row));
  for (const row of rows) {
    const text = (row.innerText || row.textContent || "").replace(/\s+/g, " ").toLowerCase();
    const idData = `${row.id || ""} ${row.getAttribute("data-a-accordion-row-name") || ""} ${row.getAttribute("data-csa-c-slot-id") || ""} ${row.getAttribute("data-csa-c-buying-option-type") || ""}`.toLowerCase();
    if (!text.includes("subscribe") && !idData.includes("sns")) continue;
    const header = row.closest?.("[data-a-accordion-row-name]")?.querySelector?.("[data-action='a-accordion'][role='button'], .a-accordion-row[role='button'], .accordion-header[role='button']")
      || row.querySelector?.("[data-action='a-accordion'][role='button'], .a-accordion-row[role='button'], .accordion-header[role='button']")
      || row.closest?.("[data-action='a-accordion'][role='button'], .a-accordion-row[role='button'], .accordion-header[role='button']")
      || row.closest?.("[data-a-accordion-row-name], .a-box")
      || row;
    if (header && visible(header)) return header;
  }
  return null;
}

function findSubscribeAndSaveRadio(root = null) {
  const direct = [
    "#snsAccordionRowMiddle .a-accordion-row",
    "#snsAccordionRow .a-accordion-row",
    "#snsAccordionRowContent .a-accordion-row",
    "[data-csa-c-slot-id='snsAccordionRowMiddle'][role='button']",
    "[data-a-accordion-row-name='snsAccordionRowMiddle'] .a-accordion-row",
  ]
    .flatMap((selector) => [...document.querySelectorAll(selector)])
    .find((item) => item.querySelector(".a-accordion-radio.a-icon-radio-inactive") || /subscribe\s*&\s*save/i.test(item.innerText || item.textContent || ""));
  if (direct) return direct;
  const roots = root ? [root] : [...document.querySelectorAll(subscribeAndSaveRootSelector())];
  for (const item of roots) {
    if (!item) continue;
    const text = (item.innerText || item.textContent || "").replace(/\s+/g, " ").toLowerCase();
    if (!text.includes("subscribe") && !/sns/i.test(item.id || "") && !/sns/i.test(item.getAttribute("data-csa-c-slot-id") || "")) continue;
    const inactive = item.querySelector(".a-accordion-radio.a-icon-radio-inactive, [role='radio'][aria-checked='false'], input[type='radio']:not(:checked)");
    if (inactive) return inactive.closest("[role='button'], .a-accordion-row, label") || inactive;
    const header = item.matches("[role='button'], .a-accordion-row") ? item : item.closest?.("[role='button'], .a-accordion-row");
    if (header && !header.querySelector(".a-accordion-radio-active")) return header;
  }
  return document.querySelector(".a-accordion-row[data-csa-c-slot-id*='sns'] .a-accordion-radio.a-icon-radio-inactive")?.closest("[role='button'], .a-accordion-row") || null;
}

async function configureSubscribeAndSaveDelivery() {
  showPanel("Nutricity fulfilment", "Checking Subscribe & Save delivery preferences.", null, null);
  await chooseSubscribeSoonerDelivery();
  const frequencyConfigured = await chooseSubscribeFrequencySixMonths();
  if (!frequencyConfigured) {
    const error = new Error("Subscribe & Save is selected, but Amazon did not confirm a six-month delivery frequency. Fulfilment paused before adding the subscription to cart.");
    error.failureCode = "";
    throw error;
  }
}

async function chooseSubscribeSoonerDelivery() {
  const changeDateLink = findVisibleTextTarget(["change first delivery date"]);
  if (!changeDateLink) return false;
  await clickElement(changeDateLink, "Change first delivery date");
  const soonerOption = await waitUntil(() => (
    document.querySelector("#onmlDeliveryOpt") ||
    findVisibleTextTarget(["get it sooner"], "#snsDeliveryDateBottomSheet label, .a-popover-inner label, .a-popover-inner span, .a-popover-inner div")
  ), 7000);
  if (!soonerOption) return false;
  const soonerTarget = soonerOption.closest?.("label") || soonerOption;
  await clickElement(soonerTarget, "Get it sooner delivery option");
  const selectDateButton = await waitUntil(() => (
    findVisibleTextTarget(["select date"], "#snsDeliveryDateBottomSheet input, #snsDeliveryDateBottomSheet button, #snsDeliveryDateBottomSheet span.a-button, #snsDeliveryDateBottomSheet span.a-button-text, .a-popover-inner input, .a-popover-inner button, .a-popover-inner span.a-button, .a-popover-inner span.a-button-text")
  ), 5000);
  if (selectDateButton) {
    const buttonTarget = selectDateButton.closest?.("span.a-button, button") || selectDateButton;
    await clickElement(buttonTarget, "Select Subscribe & Save delivery date");
  }
  await waitForStableDom(700, 5000);
  return true;
}

async function chooseSubscribeFrequencySixMonths() {
  showPanel("Subscribe & Save", "Opening delivery every dropdown.", null, null);
  if (subscribeFrequencyIsSixMonths()) return true;
  const popoverTrigger = [
    document.querySelector("#replenishment-onml-frequency-trigger"),
    document.querySelector("#replenishment-sns-frequency-trigger"),
  ].find(visible);
  if (popoverTrigger) {
    await clickElement(popoverTrigger, "Subscribe & Save delivery schedule popover");
    const sixMonthOption = await waitUntil(findSixMonthSubscribeFrequencyPopoverOption, 5000, 150);
    if (!sixMonthOption) return false;
    showPanel("Subscribe & Save", "Selecting delivery every 6 months.", null, null);
    await clickElement(sixMonthOption, "Subscribe & Save 6 months schedule");
    const confirmed = await waitUntil(subscribeFrequencyIsSixMonths, 5000, 150);
    if (confirmed) await waitForStableDom(700, 5000);
    return confirmed;
  }
  const nativeFrequencies = [...document.querySelectorAll("#snsAccordionRowMiddle select, #snsAccordionRow select, #snsAccordionRowContent select, #reinvent_price_desktop_snsAccordionRowMiddle select")]
    .filter((select) => [...select.options || []].some((option) => /\b(weeks?|months?)\b/i.test(option.textContent || "") || /\d+[WM]\|(?:sns|onml)/i.test(String(option.value || ""))));
  // Business pages keep parallel SNS/ONML selects and hide the inactive one.
  // Drive the visible select so Amazon's AUI dropdown state is updated, while
  // retaining a fallback for layouts that visually hide the native control.
  const nativeFrequency = nativeFrequencies.find(visible) || nativeFrequencies[0] || null;
  const nativeContainerButton = nativeFrequency?.closest?.(".a-dropdown-container")?.querySelector?.(".a-button-dropdown, [data-action='a-dropdown-button']");
  const explicitButton = nativeFrequency?.closest?.(".a-dropdown-container")?.querySelector?.("[data-action='a-dropdown-button'], .a-button-dropdown, span.a-button");
  const frequencyButton = findSubscribeFrequencyDropdownButton();
  const dropdownButton = explicitButton || nativeContainerButton || frequencyButton;
  if (!dropdownButton || !visible(dropdownButton)) return selectNativeSubscribeFrequency(nativeFrequency);
  await clickElement(dropdownButton, "Subscribe & Save delivery schedule dropdown");
  const frequencyOption = await waitUntil(findSixMonthBusinessFrequencyOption, 5000);
  if (!frequencyOption) return selectNativeSubscribeFrequency(nativeFrequency);
  const selectedText = (frequencyOption.textContent || "").replace(/\s+/g, " ").trim();
  showPanel("Subscribe & Save", `Selecting delivery every ${selectedText}.`, null, null);
  await clickElement(frequencyOption, `Subscribe & Save ${selectedText} schedule`);
  const confirmed = await waitUntil(subscribeFrequencyIsSixMonths, 5000, 150);
  if (confirmed) await waitForStableDom(700, 5000);
  return confirmed;
}

function findSixMonthSubscribeFrequencyPopoverOption() {
  const candidates = [
    ...document.querySelectorAll(
      [
        "[data-frequency-value^='6M|onml']",
        "[data-frequency-value^='6M|sns']",
        "[data-frequency-label='6 months']",
        "#onmlFrequencyAccordionRow-10",
        "#snsFrequencyAccordionRow-10",
      ].join(", "),
    ),
  ];
  const row = candidates.find((element) => {
    const inOpenPopover = element.closest(".a-popover-wrapper, .a-popover-inner, [role='dialog']");
    return visible(element) && Boolean(inOpenPopover);
  });
  if (!row) return null;
  return row.querySelector("[data-action='a-accordion'][role='button'], a, button") || row;
}

function findSixMonthBusinessFrequencyOption() {
  const popovers = [...document.querySelectorAll(".a-popover-wrapper, .a-popover-inner")]
    .filter((popover) => visible(popover) && /\b6\s*months?\b/i.test(popover.innerText || popover.textContent || ""));
  const options = popovers
    .flatMap((popover) => [...popover.querySelectorAll("a.a-dropdown-link, a[role='option']")])
    .filter((link, index, all) => {
      const text = normalizedText(link.textContent || "");
      const value = String(link.getAttribute("data-value") || "");
      return all.indexOf(link) === index
        && visible(link)
        && (/^6\s*months?$/.test(text) || /^6M\|(?:sns|onml)$/i.test(value));
    });
  return options[0] || null;
}

function subscribeFrequencyIsSixMonths() {
  const consumerTrigger = [
    document.querySelector("#replenishment-onml-frequency-trigger"),
    document.querySelector("#replenishment-sns-frequency-trigger"),
  ].find(visible);
  const consumerText = normalizedText(consumerTrigger?.innerText || consumerTrigger?.textContent || "");
  if (/\b6\s*months?\b/.test(consumerText)) return true;

  const businessPrompt = [
    document.querySelector("#rcxOrdFreqSns-announce"),
    document.querySelector("#rcxOrdFreqOnml-announce"),
  ].find(visible);
  const businessPromptText = normalizedText(businessPrompt?.innerText || businessPrompt?.textContent || "");
  if (/\b6\s*months?\b/.test(businessPromptText)) return true;

  const businessSelects = [...document.querySelectorAll("select#rcxOrdFreqSns, select#rcxOrdFreqOnml")];
  if (businessSelects.some((select) => {
    const selectedText = normalizedText(select.options?.[select.selectedIndex]?.textContent || "");
    return /^6M\|(?:sns|onml)$/i.test(String(select.value || "")) && /^6\s*months?$/.test(selectedText);
  })) return true;

  const valueInput = document.querySelector("input[id*='recurringDelivery'][id*='frequency'][id$='[value]']");
  const unitInput = document.querySelector("input[id*='recurringDelivery'][id*='frequency'][id$='[unit]']");
  if (String(valueInput?.value || "") === "6" && String(unitInput?.value || "").toUpperCase() === "M") return true;

  return [
    document.querySelector("#onmlFrequencySelectedIndex"),
    document.querySelector("#snsFrequencySelectedIndex"),
  ].some((input) => String(input?.value || "") === "10");
}

async function selectNativeSubscribeFrequency(select) {
  if (!select?.options?.length) return false;
  const options = [...select.options].filter((option) => /sns/i.test(String(option.value || "")) || /\b(weeks?|months?)\b/i.test(option.textContent || ""));
  const target = options.find((option) => (
    /^6M\|(?:sns|onml)$/i.test(String(option.value || ""))
    || /^6\s*months?$/.test(normalizedText(option.textContent || ""))
  ));
  if (!target) return false;
  const selectedText = (target.textContent || "").replace(/\s+/g, " ").trim();
  showPanel("Subscribe & Save", `Selecting delivery every ${selectedText}.`, null, null);
  select.value = target.value;
  select.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
  select.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  const confirmed = await waitUntil(subscribeFrequencyIsSixMonths, 5000, 150);
  if (confirmed) await waitForStableDom(700, 5000);
  return confirmed;
}

function findSubscribeFrequencyDropdownButton() {
  const roots = [
    document.querySelector("#snsAccordionRowMiddle"),
    document.querySelector("#snsAccordionRow"),
    document.querySelector("#snsAccordionRowContent"),
    document.querySelector("#reinvent_price_desktop_snsAccordionRowMiddle"),
  ].filter(Boolean);
  const candidates = [
    ...roots.flatMap((root) => [
      ...root.querySelectorAll("[data-action='a-dropdown-button'], .a-button-dropdown, span.a-button"),
    ]),
    ...document.querySelectorAll("[id$='-announce'], span.a-dropdown-prompt"),
  ];
  return candidates.find((element) => {
    const text = (element.innerText || element.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
    const inSns = roots.some((root) => root.contains(element));
    const looksLikeFrequency = /deliver(?:y)?\s*every/.test(text) || /\b\d+\s*(weeks?|months?)\b/.test(text);
    return visible(element) && (inSns || /rcxordfreqsns/i.test(element.id || "")) && looksLikeFrequency;
  })?.closest?.("[data-action='a-dropdown-button'], .a-button-dropdown, span.a-button") || null;
}

function findLastSubscribeFrequencyOption() {
  const popovers = [...document.querySelectorAll(".a-popover-wrapper, .a-popover-inner")]
    .filter((popover) => visible(popover) && /\b(weeks?|months?)\b/i.test(popover.innerText || popover.textContent || ""));
  for (const popover of popovers) {
    const scroller = popover.querySelector(".a-popover-inner") || popover;
    scroller.scrollTop = scroller.scrollHeight;
  }
  const options = popovers
    .flatMap((popover) => [...popover.querySelectorAll("a.a-dropdown-link, a[role='option']")])
    .filter((link, index, all) => {
      const text = (link.textContent || "").replace(/\s+/g, " ").trim();
      const value = link.getAttribute("data-value") || "";
      return all.indexOf(link) === index && visible(link) && (/\b\d+\s*(weeks?|months?)\b/i.test(text) || /\d+[WM]\|sns/i.test(value));
    });
  return options.at(-1) || null;
}

function quantitySelects(context = "regular") {
  const selects = [...document.querySelectorAll("select")].filter((select) => {
    const id = select.id || "";
    if (context === "sns") {
      return (
        /sns/i.test(id) && id.includes("predefinedQuantitiesDropdown") ||
        Boolean(select.closest(subscribeAndSaveRootSelector()))
      );
    }
    return (
      id === "quantity" ||
      id.includes("predefinedQuantitiesDropdown") && !/^sns/i.test(id) ||
      select.name === "quantity"
    );
  });
  return selects;
}

function maxSelectableQuantity(context = "regular") {
  const quantities = [];
  for (const select of quantitySelects(context)) {
    for (const option of [...select.options || []]) {
      const text = clean(option.textContent || option.value);
      if (/10\+/.test(text)) {
        quantities.push(10);
        continue;
      }
      const numeric = Number((option.value || text).match(/\d+/)?.[0] || 0);
      if (numeric > 0) quantities.push(numeric);
    }
  }
  return quantities.length ? Math.max(...quantities) : 0;
}

function maxPredefinedQuantity(context = "regular") {
  const quantities = [];
  const selectors = context === "sns"
    ? ["select[id^='sns'][id*='predefinedQuantitiesDropdown']"]
    : ["select[id*='new_buyingOption'][id*='predefinedQuantitiesDropdown']", "select[id*='DesktopFfqp'][id*='predefinedQuantitiesDropdown']:not([id^='sns'])"];
  for (const selector of selectors) {
    for (const select of document.querySelectorAll(selector)) {
      for (const option of [...select.options || []]) {
        const numeric = Number((option.value || option.textContent || "").match(/\d+/)?.[0] || 0);
        if (numeric > 0) quantities.push(numeric);
      }
    }
  }
  return quantities.length ? Math.max(...quantities) : 0;
}

function quantityFreeFormInput(select, context = "regular") {
  const id = select?.id || "";
  const candidates = [];
  if (id.includes("predefinedQuantitiesDropdown")) {
    candidates.push(`#${CSS.escape(id.replace("predefinedQuantitiesDropdown", "freeQuantityTextInput"))}`);
    candidates.push(`#${CSS.escape(id.replace("predefinedQuantitiesDropdown", "quantityTextInput"))}`);
  }
  if (context === "sns") {
    candidates.push(
      "input[id*='sns'][id$='freeQuantityTextInput']",
      "input[id*='sns'][id$='quantityTextInput']",
      "input[id^='sns'][id$='freeQuantityTextInput']",
      "input[id^='sns'][id$='quantityTextInput']",
      "#snsAccordionRowMiddle input.freeQuantityTextInput",
      "#snsAccordionRowMiddle input.quantity-text-input-with-label",
      "#snsAccordionRow input.freeQuantityTextInput",
      "#snsAccordionRow input.quantity-text-input-with-label",
    );
  } else {
    candidates.push(
      "input[id*='new_buyingOption'][id$='freeQuantityTextInput']",
      "input[id*='new_buyingOption'][id$='quantityTextInput']",
      "input.freeQuantityTextInput:not([id^='sns'])",
      "input.quantity-text-input-with-label:not([id^='sns'])",
      "input#quantity",
    );
  }
  for (const selector of candidates) {
    const input = document.querySelector(selector);
    if (input) return input;
  }
  if (context === "sns") {
    for (const root of document.querySelectorAll(subscribeAndSaveRootSelector())) {
      const input = root.querySelector("input.freeQuantityTextInput, input.quantity-text-input-with-label, input[id$='quantityTextInput'], input[id$='freeQuantityTextInput']");
      if (input) return input;
    }
  }
  return null;
}

function quantityUpdateButton(select, context = "regular") {
  const id = select?.id || "";
  const candidates = [];
  if (id.includes("predefinedQuantitiesDropdown")) {
    candidates.push(`#${CSS.escape(id.replace("predefinedQuantitiesDropdown", "updateButton"))}`);
  }
  if (id.includes("quantityTextInput")) {
    candidates.push(`#${CSS.escape(id.replace("quantityTextInput", "updateButton"))}`);
  }
  if (id.includes("freeQuantityTextInput")) {
    candidates.push(`#${CSS.escape(id.replace("freeQuantityTextInput", "updateButton"))}`);
  }
  if (context === "sns") {
    candidates.push("#snsQuantity_feature_div [id$='-updateButton']", "#snsAccordionRowMiddle [id$='-updateButton']", "#snsAccordionRow [id$='-updateButton']");
  } else {
    candidates.push("[id*='new_buyingOption'][id$='-updateButton']", "#qualifiedBuybox [id$='-updateButton']", "#desktop_qualifiedBuyBox [id$='-updateButton']");
  }
  for (const selector of candidates) {
    const button = document.querySelector(`${selector} button, ${selector} input, ${selector}`);
    if (button && visible(button)) return button;
  }
  return null;
}

function quantityTextInputs(context = "regular") {
  const selectors = context === "sns"
    ? [
        "input[id*='sns'][id$='quantityTextInput']",
        "input[id*='sns'][id$='freeQuantityTextInput']",
        "input[id^='sns'][id$='quantityTextInput']",
        "input[id^='sns'][id$='freeQuantityTextInput']",
        "#snsAccordionRowMiddle input.quantity-text-input-with-label",
        "#snsAccordionRowMiddle input.freeQuantityTextInput",
      ]
    : [
        "input#quantity",
        "input[name='quantity']",
        "input[id*='new_buyingOption'][id$='quantityTextInput']",
        "input[id*='new_buyingOption'][id$='freeQuantityTextInput']",
        "input.quantity-text-input-with-label:not([id^='sns'])",
        "input.freeQuantityTextInput:not([id^='sns'])",
      ];
  const inputs = selectors.flatMap((selector) => [...document.querySelectorAll(selector)]);
  if (context === "sns") {
    for (const root of document.querySelectorAll(subscribeAndSaveRootSelector())) {
      inputs.push(...root.querySelectorAll("input.quantity-text-input-with-label, input.freeQuantityTextInput, input[id$='quantityTextInput'], input[id$='freeQuantityTextInput']"));
    }
  }
  return inputs.filter((input, index, all) => (
    all.indexOf(input) === index && visible(input)
  ));
}

function regularBuyboxQuantityControls() {
  const controls = [
    ...document.querySelectorAll(
      [
        "#addToCart select#quantity",
        "#addToCart input#quantity",
        "#addToCart input[name='quantity']",
        "#buybox select#quantity",
        "#buybox input#quantity",
        "#buybox input[name='quantity']",
        "#desktop_buybox select#quantity",
        "#desktop_buybox input#quantity",
        "#desktop_buybox input[name='quantity']",
        "select#quantity",
        "input#quantity",
        "input[name='quantity']",
      ].join(", "),
    ),
  ];
  return controls.filter((control, index, all) => (
    all.indexOf(control) === index &&
    !control.closest(subscribeAndSaveRootSelector()) &&
    String(control.name || control.id || "").toLowerCase() === "quantity"
  ));
}

function setQuantityControlValue(control, qty) {
  if (!control) return false;
  const requested = Math.max(1, Math.round(Number(qty) || 1));
  if (control.tagName === "SELECT") {
    const option = [...control.options || []].find((item) => String(item.value) === String(requested) || Number(item.value) === requested);
    if (!option) return false;
    control.value = option.value;
  } else {
    control.value = String(requested);
    control.setAttribute("value", String(requested));
  }
  control.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
  control.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  return true;
}

function syncRegularQuantity(qty = 1) {
  const requested = Math.max(1, Math.round(Number(qty) || 1));
  const controls = [
    ...regularBuyboxQuantityControls(),
    ...quantitySelects("regular"),
    ...quantityTextInputs("regular"),
  ].filter((control, index, all) => all.indexOf(control) === index);
  let synced = false;
  for (const control of controls) {
    synced = setQuantityControlValue(control, requested) || synced;
  }
  return synced || requested === 1;
}

function quantityValueAccepted(context = "regular", qty = 1) {
  const requested = Math.max(1, Math.round(Number(qty) || 1));
  if (context !== "sns") {
    const buyboxControls = regularBuyboxQuantityControls();
    if (buyboxControls.length) {
      return buyboxControls.some((control) => {
        const value = Number(String(control.value || "").replace(/[^\d.]/g, ""));
        return Number.isFinite(value) && value === requested;
      });
    }
  }
  const roots = context === "sns"
    ? [...document.querySelectorAll(subscribeAndSaveRootSelector())]
    : [document];
  const controls = roots.flatMap((root) => [
    ...root.querySelectorAll("select[id*='predefinedQuantitiesDropdown'], select#quantity, select[name='quantity'], input#rcxsubsQuan, input[id$='quantityTextInput'], input[id$='freeQuantityTextInput'], input#quantity"),
  ]).filter((control) => context === "sns" || !control.closest(subscribeAndSaveRootSelector()));
  const accepted = controls.some((control) => {
    const value = Number(String(control.value || "").replace(/[^\d.]/g, ""));
    return Number.isFinite(value) && value === requested;
  });
  if (!accepted) return false;
  if (context !== "sns") return true;
  const subscribeButton = document.querySelector("#rcx-subscribe-submit-button-announce, #rcx-subscribe-submit-button button, #rcx-subscribe-submit-button input, button[value='snsText']");
  return Boolean(subscribeButton && visible(subscribeButton) && !subscribeButton.disabled);
}

function syncSubscribeAndSaveQuantity(qty = 1) {
  const requested = Math.max(1, Math.round(Number(qty) || 1));
  const roots = [
    ...document.querySelectorAll("#snsAccordionRowMiddle, #snsAccordionRow, #snsAccordionRowContent, #reinvent_price_desktop_snsAccordionRowMiddle, #snsQuantity_feature_div"),
  ].filter((root, index, all) => root && all.indexOf(root) === index);
  const scopedRoots = roots.length ? roots : [document];
  const controls = scopedRoots.flatMap((root) => [
    ...root.querySelectorAll("input#rcxsubsQuan, form input[name='quantity'], select[id*='sns'][id*='predefinedQuantitiesDropdown'], input[id*='sns'][id$='quantityTextInput'], input[id*='sns'][id$='freeQuantityTextInput']"),
  ]).filter((control, index, all) => all.indexOf(control) === index);

  for (const control of controls) {
    if (control.tagName === "SELECT") {
      const option = [...control.options || []].find((item) => String(item.value) === String(requested) || Number(item.value) === requested);
      if (!option) continue;
      control.value = option.value;
    } else {
      control.value = String(requested);
      control.setAttribute("value", String(requested));
    }
    control.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
    control.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  }

  const hiddenQuantityControls = controls.filter((control) => {
    const type = String(control.type || "").toLowerCase();
    return type === "hidden" || control.id === "rcxsubsQuan" || control.name === "quantity";
  });
  const requiredControls = hiddenQuantityControls.length ? hiddenQuantityControls : controls;
  const synced = requiredControls.some((control) => Number(String(control.value || "").replace(/[^\d.]/g, "")) === requested);
  if (synced && requested > 1) {
    showPanel("Nutricity fulfilment", `Synced Subscribe & Save checkout quantity to ${requested}.`, null, null);
  }
  return synced || requested === 1;
}

async function clickQuantityUpdateButton(select, context = "regular") {
  const button = await waitUntil(() => quantityUpdateButton(select, context), 2500, 250);
  if (!button) return false;
  await clickElement(button, `${context === "sns" ? "Subscribe & Save " : ""}quantity update`);
  await waitForStableDom(500, 4000);
  return true;
}

function quantityAvailabilityIssue(context = "regular", requestedQuantity = 1) {
  const roots = context === "sns"
    ? [
        ...document.querySelectorAll("#snsQuantity_feature_div, #snsAccordionRowMiddle, #snsAccordionRow, #snsAccordionRowContent, [id^='snsABDesktopFfqp_'][id$='-limitedAvailabilityMessage']"),
      ]
    : [
        ...document.querySelectorAll("#qualifiedBuybox, #desktop_qualifiedBuyBox, #buybox, #centerCol, #splitoffer_detailpage_buybox_section, [id*='DesktopFfqp_'][id$='-limitedAvailabilityMessage']:not([id^='sns'])"),
      ];
  const text = roots
    .map((root) => (root.innerText || root.textContent || "").replace(/\s+/g, " ").trim())
    .filter(Boolean)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
  const lowered = text.toLowerCase();
  const hasSingleSellerSplitOffer = lowered.includes("quantity unavailable from a single seller") || lowered.includes("buy from multiple sellers");
  const hasSpecificInventoryLimit = (
    lowered.includes("limited availability at this quantity") ||
    lowered.includes("limited availability") ||
    lowered.includes("not enough inventory") ||
    lowered.includes("not enough available") ||
    lowered.includes("maximum quantity") ||
    lowered.includes("seller does not have") ||
    lowered.includes("seller doesn't have") ||
    lowered.includes("only") && lowered.includes("available")
  );
  if (context === "sns" && hasSingleSellerSplitOffer && !hasSpecificInventoryLimit) {
    return null;
  }
  const hasIssue = (
    hasSpecificInventoryLimit ||
    hasSingleSellerSplitOffer
  );
  const splitOffer = context !== "sns" && document.querySelector("#splitoffer_detailpage_buybox_link[href*='quantity=']");
  if (!hasIssue && !splitOffer) return null;

  const requested = Math.max(1, Math.round(Number(requestedQuantity || 1)));
  const textQuantity =
    Number(lowered.match(/only\s+(\d+)\s+(?:left|available|in stock)/)?.[1] || 0) ||
    Number(lowered.match(/maximum(?: order)? quantity(?: is)?\s+(\d+)/)?.[1] || 0);
  const predefinedQuantity = maxPredefinedQuantity(context);
  const availableQuantity = textQuantity || (predefinedQuantity > 0 && predefinedQuantity < requested ? predefinedQuantity : 0);
  return {
    message: text || (context === "sns" ? "Limited availability at this Subscribe & Save quantity." : "Quantity unavailable from a single seller."),
    requestedQuantity: requested,
    availableQuantity: availableQuantity || null,
    fulfilledQuantity: availableQuantity || null,
  };
}

function partialQuantityMessage(asin, requestedQuantity, availableQuantity, issueMessage = "", context = "regular") {
  const prefix = context === "sns" ? "Less Subscribe & Save quantity available" : "Less quantity available";
  const availableText = availableQuantity
    ? `Amazon only allows ${availableQuantity}`
    : "Amazon showed limited availability";
  return `${prefix} for ASIN ${asin}. Customer ordered ${requestedQuantity}, ${availableText}.${issueMessage ? ` ${issueMessage}` : ""}`.trim();
}

async function pauseAfterMissingAsinReportFailure(activeJob, message) {
  activeJob.pausedStage = activeJob.stage || "product";
  activeJob.paused = true;
  await setActiveJob(activeJob);
  showPanel(
    "Missing ASIN report failed",
    `${message} The order is paused so it does not stay locked silently.`,
    "I did it manually, continue",
    () => continueAfterManualStep(activeJob),
  );
}

async function reportMissingQuantityIssue(activeJob, item, purchaseItem, quantityIssue = {}) {
  const requestedQuantity = Math.max(1, Math.round(Number(purchaseItem?.quantity || item?.quantity || quantityIssue.requestedQuantity || 1)));
  const availableQuantity = quantityIssue.availableQuantity || quantityIssue.fulfilledQuantity || maxPredefinedQuantity(quantityIssue.context || "regular") || maxSelectableQuantity(quantityIssue.context || "regular") || null;
  const message = partialQuantityMessage(purchaseItem?.asin || item?.asin || "", requestedQuantity, availableQuantity, quantityIssue.message || "", quantityIssue.context || "regular");
  showPanel("Sending to Missing ASINs", message, null, null);
  const result = await send({
    type: "FAIL_JOB",
    message,
    missingAsin: item?.asin || purchaseItem?.asin || "",
    missingLineId: item ? itemPrimaryLineId(item) : null,
    failureCode: "partial_quantity",
    requestedQuantity,
    fulfilledQuantity: availableQuantity,
    availableQuantity,
  });
  if (result?.ok) {
    showPanel("Missing ASINs", `${message} ${result.message || "Quantity issue moved to Missing ASINs."}`, null, null);
    return true;
  }
  throw new Error(result?.message || "Could not move quantity issue to Missing ASINs.");
}

async function chooseAmazonDropdownOption(select, qty) {
  return chooseAmazonDropdownText(select, qty > 9 ? "10+" : String(qty));
}

async function chooseAmazonDropdownText(select, optionText) {
  const container = select.closest(".a-dropdown-container") || select.parentElement;
  const dropdownButton = container?.querySelector(".a-button-dropdown, [data-action='a-dropdown-button']");
  if (!dropdownButton || !visible(dropdownButton)) return false;
  dropdownButton.scrollIntoView({ block: "center", behavior: "smooth" });
  await sleep(350);
  dropdownButton.click();
  await sleep(700);
  const wanted = String(optionText || "").replace(/\s+/g, " ").trim();
  const links = [...document.querySelectorAll(".a-popover-wrapper a.a-dropdown-link, .a-popover-inner a.a-dropdown-link, a[role='option']")].filter(visible);
  const option = links.find((link) => (link.textContent || "").replace(/\s+/g, " ").trim() === wanted);
  if (!option) return false;
  option.scrollIntoView({ block: "nearest" });
  await sleep(250);
  option.click();
  await sleep(900);
  return true;
}

async function selectFreeFormQuantityOption(select, qty) {
  const thresholds = qty > 9 ? ["10+", "4+"] : ["4+", "10+"];
  const option = [...select.options || []].find((item) => {
    const text = (item.textContent || item.value || "").replace(/\s+/g, " ").trim();
    return thresholds.includes(text) || item.value === "";
  });
  if (!option) return false;
  const text = (option.textContent || option.value || "").replace(/\s+/g, " ").trim();
  const clicked = await chooseAmazonDropdownText(select, text || thresholds[0]);
  if (clicked) return true;
  select.value = option.value;
  select.dispatchEvent(new Event("input", { bubbles: true }));
  select.dispatchEvent(new Event("change", { bubbles: true }));
  await sleep(900);
  return true;
}

async function setNativeSelectQuantity(select, qty) {
  if (!select) return false;
  const wantedValue = qty > 9 ? "" : String(qty);
  const option = [...select.options].find((item) => String(item.value) === wantedValue || Number(item.value) === qty);
  if (!option) return false;
  select.value = option.value;
  select.dispatchEvent(new Event("input", { bubbles: true }));
  select.dispatchEvent(new Event("change", { bubbles: true }));
  await sleep(1000);
  return true;
}

async function setFreeFormQuantity(select, qty, context = "regular") {
  const input = quantityFreeFormInput(select, context);
  if (!input) return false;
  await selectFreeFormQuantityOption(select, qty);
  await sleep(500);
  input.classList.remove("aok-hidden");
  input.removeAttribute("aria-hidden");
  input.scrollIntoView({ block: "center", behavior: "smooth" });
  await sleep(350);
  input.focus();
  input.select?.();
  input.value = String(qty);
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
  input.blur();
  await sleep(1000);
  const updated = await clickQuantityUpdateButton(select, context);
  await sleep(updated ? 1300 : 700);
  const value = Number(String(input.value || "").replace(/[^\d.]/g, ""));
  return Number.isFinite(value) && value === qty;
}

async function setDirectQuantityInput(input, qty, context = "regular") {
  if (!input) return false;
  showPanel("Nutricity fulfilment", `Typing ${context === "sns" ? "Subscribe & Save " : ""}quantity ${qty}.`, null, null);
  input.classList.remove("aok-hidden");
  input.removeAttribute("aria-hidden");
  input.scrollIntoView({ block: "center", behavior: "smooth" });
  await sleep(350);
  await setInputValue(input, String(qty));
  input.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
  input.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  input.blur();
  await sleep(800);
  const updated = await clickQuantityUpdateButton(input, context);
  await sleep(updated ? 1300 : 700);
  const value = Number(String(input.value || "").replace(/[^\d.]/g, ""));
  return Number.isFinite(value) && value === qty;
}

async function setQuantity(quantity, context = "regular") {
  const quantitySelectors = context === "sns"
    ? [
        "#snsAccordionRowMiddle select[id*='sns'][id*='predefinedQuantitiesDropdown']",
        "#snsAccordionRow select[id*='sns'][id*='predefinedQuantitiesDropdown']",
        "#snsQuantity_feature_div select[id*='sns'][id*='predefinedQuantitiesDropdown']",
        "#snsAccordionRowMiddle input#rcxsubsQuan",
        "#snsAccordionRow input#rcxsubsQuan",
        "#rcx-subscribe-submit-button",
      ]
    : ["select#quantity", "select[name='quantity']", "select[id*='predefinedQuantitiesDropdown']", "input[id$='quantityTextInput']", ".quantity-text-input-with-label", "#add-to-cart-button"];
  await waitForElement(quantitySelectors, 12000);
  const qty = Math.max(1, Math.round(Number(quantity) || 1));
  window.__nutricityLastQuantityIssue = null;
  if (context === "sns" && !subscribeAndSaveAccordionIsActive()) {
    const error = new Error("Subscribe & Save quantity was requested before the Subscribe & Save row became active.");
    error.failureCode = "";
    throw error;
  }
  if (qty === 1) return true;

  const selects = quantitySelects(context);
  for (const select of selects) {
    showPanel("Nutricity fulfilment", `Setting ${context === "sns" ? "Subscribe & Save " : ""}quantity to ${qty}.`, null, null);
    const freeFormInput = quantityFreeFormInput(select, context);
    const freeFormSet = freeFormInput ? await setFreeFormQuantity(select, qty, context) : false;
    const selected = freeFormSet || await chooseAmazonDropdownOption(select, qty) || await setNativeSelectQuantity(select, qty);
    if (!selected) continue;
    if (context === "regular") {
      syncRegularQuantity(qty);
      await sleep(400);
      if (!quantityValueAccepted(context, qty)) continue;
    }
    await sleep(900);
    const issue = quantityAvailabilityIssue(context, qty);
    if (issue) {
      if (quantityValueAccepted(context, qty)) {
        showPanel(
          "Nutricity fulfilment",
          `Amazon accepted the ${context === "sns" ? "Subscribe & Save " : ""}quantity despite a limited availability warning. Continuing.`,
          null,
          null,
        );
        return true;
      }
      issue.context = context;
      window.__nutricityLastQuantityIssue = issue;
      return false;
    }
    return true;
  }
  for (const input of quantityTextInputs(context)) {
    const typed = await setDirectQuantityInput(input, qty, context);
    if (!typed) continue;
    if (context === "regular") {
      syncRegularQuantity(qty);
      await sleep(400);
      if (!quantityValueAccepted(context, qty)) continue;
    }
    const issue = quantityAvailabilityIssue(context, qty);
    if (issue) {
      if (quantityValueAccepted(context, qty)) {
        showPanel(
          "Nutricity fulfilment",
          `Amazon accepted the ${context === "sns" ? "Subscribe & Save " : ""}quantity despite a limited availability warning. Continuing.`,
          null,
          null,
        );
        return true;
      }
      issue.context = context;
      window.__nutricityLastQuantityIssue = issue;
      return false;
    }
    return true;
  }
  const issue = quantityAvailabilityIssue(context, qty);
  if (issue) {
    issue.context = context;
    window.__nutricityLastQuantityIssue = issue;
  }
  return false;
}

async function navigateToNext(activeJob) {
  const nextIndex = activeJob.itemIndex + 1;
  if (nextIndex < activeJob.job.items.length) {
    activeJob.itemIndex = nextIndex;
    activeJob.stage = "product";
    await setActiveJob(activeJob);
    location.href = `https://www.amazon.com/dp/${activeJob.job.items[nextIndex].asin}`;
    return;
  }
  activeJob.stage = "cart";
  await setActiveJob(activeJob);
  location.href = "https://www.amazon.com/cart?ref_=sw_gtc";
}

async function handleClearCart(activeJob) {
  if (!canClearCart(activeJob)) {
    activeJob.stage = "cart";
    activeJob.cartCleared = true;
    await setActiveJob(activeJob);
    showPanel("Nutricity fulfilment", "Cart clear is no longer safe at this stage. Continuing with cart check.", null, null);
    return;
  }
  await waitForElement(["#sc-active-cart", "input[name='proceedToRetailCheckout']", "#sc-buy-box-ptc-button input"], 15000);
  showPanel("Nutricity fulfilment", "Clearing the existing Amazon cart before starting this order.", null, null);
  await waitForElement("#sc-active-cart", 15000);
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const buttons = cartDeleteButtons();
    if (!buttons.length) break;
    showPanel("Nutricity fulfilment", `Clearing Amazon cart item ${attempt + 1}. ${buttons.length} active delete link(s) found.`, null, null);
    await clickElement(buttons[0], "cart delete button");
    await waitForStableDom(800, 6000);
  }
  await sleep(1200);
  if (cartActiveItems().length && !cartIsVisiblyEmpty()) {
    throw new Error("Amazon cart cleanup did not remove the existing cart item(s). Fulfilment stopped before adding this order, so the customer quantity cannot be doubled.");
  }

  activeJob.stage = "product";
  activeJob.itemIndex = 0;
  activeJob.cartCleared = true;
  await setActiveJob(activeJob);
  const first = activeJob.job.items[0];
  location.href = `https://www.amazon.com/dp/${first.asin}`;
}

async function clearCartItems(reason = "Cleaning Amazon cart before starting the next order.") {
  if (/\/cart\/smart-wagon/i.test(location.pathname)) {
    showPanel("Nutricity fulfilment", "Opening full Amazon cart before cleanup.", null, null);
    location.href = "https://www.amazon.com/cart?ref_=sw_gtc";
    return;
  }
  await waitForElement([
    "#sc-active-cart",
    "#sc-empty-cart",
    "input[name='proceedToRetailCheckout']",
    "#sc-buy-box-ptc-button input",
    "[data-name='Active Items']",
  ], 15000);
  showPanel("Nutricity fulfilment", reason, null, null);
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const buttons = cartDeleteButtons();
    if (!buttons.length) break;
    showPanel("Nutricity fulfilment", `Cleaning Amazon cart item ${attempt + 1}. ${buttons.length} active delete link(s) found.`, null, null);
    await clickElement(buttons[0], "cart delete button");
    await waitForStableDom(800, 6000);
  }
}

async function handleFailureCleanup(activeJob) {
  if (!/\/cart/i.test(location.pathname)) {
    showPanel("Nutricity fulfilment", "Order was moved to Missing ASINs. Opening cart cleanup before continuing.", null, null);
    location.href = "https://www.amazon.com/cart?ref_=sw_gtc";
    return;
  }
  if (/\/cart\/smart-wagon/i.test(location.pathname)) {
    showPanel("Nutricity fulfilment", "Opening full Amazon cart before continuing cleanup.", null, null);
    location.href = "https://www.amazon.com/cart?ref_=sw_gtc";
    return;
  }
  await clearCartItems(activeJob.cleanupReason || "Order was moved to Missing ASINs. Cleaning cart before continuing.");
  let result = await send({ type: "FINISH_CLEANUP_AND_CLAIM_NEXT" });
  if (!result?.next_job_started) {
    await sleep(1200);
    result = await send({ type: "FINISH_CLEANUP_AND_CLAIM_NEXT" });
  }
  if (result?.next_job_started) {
    showPanel("Nutricity fulfilment", result.message || `Started next ${result.next_group_key}.`, null, null);
    return;
  }
  showPanel("Nutricity fulfilment", result?.message || "No more queued Chrome jobs found.", null, null);
}

async function handleProduct(activeJob) {
  const item = activeJob.job.items[activeJob.itemIndex];
  if (!item) return;
  const expectedItem = selectedVariantItem(activeJob, item);
  if (!/\/(?:dp|gp\/product)\//i.test(location.pathname)) {
    if (/\/checkout/i.test(location.pathname)) {
      activeJob.stage = "checkout";
      await setActiveJob(activeJob);
      await handleCheckout(activeJob);
      return;
    }
    if (/\/cart/i.test(location.pathname)) {
      if (cartIsVisiblyEmpty()) {
        const attempts = Number(activeJob.emptyCartProductRetryCount || 0);
        if (attempts < 1) {
          activeJob.emptyCartProductRetryCount = attempts + 1;
          activeJob.stage = "product";
          await setActiveJob(activeJob, { allowStageRegression: true });
          showPanel("Nutricity fulfilment", `Amazon returned to an empty cart after trying ${expectedItem.asin}. Retrying the product page once before reporting anything.`, null, null);
          location.href = `https://www.amazon.com/dp/${expectedItem.asin}?th=1`;
          return;
        }
        await pauseForManualCheckout(
          activeJob,
          `Amazon returned to an empty cart after trying ASIN ${expectedItem.asin}. The product page was reachable, so this was not marked Missing.`,
          "product",
        );
        return;
      }
      activeJob.stage = "cart";
      await setActiveJob(activeJob);
      await handleCart(activeJob);
      return;
    }
  }
  const pageError = amazonProductPageErrorMessage();
  if (pageError) {
    await failCurrentItemAsMissing(activeJob, item, expectedItem, `ASIN ${expectedItem.asin} is missing or unavailable on Amazon. ${pageError}`);
    return;
  }
  const productReady = await waitForElement([
    "#productTitle",
    "#add-to-cart-button",
    "input[name='submit.add-to-cart']",
    "#rcx-subscribe-submit-button",
    "#corePrice_feature_div .a-price",
    "#apex_desktop .a-price",
  ], 18000);
  const delayedPageError = amazonProductPageErrorMessage();
  if (delayedPageError) {
    await failCurrentItemAsMissing(
      activeJob,
      item,
      expectedItem,
      `ASIN ${expectedItem.asin} is missing or unavailable on Amazon. ${delayedPageError}`,
    );
    return;
  }
  if (!productReady) {
    await failCurrentJobAsChromeError(
      activeJob,
      `Amazon product page for ASIN ${expectedItem.asin} did not expose product controls after waiting. This may be a page timing, bot-check, modal, or parser issue, so the order was not marked Missing.`,
      "product_controls_unavailable",
    );
    return;
  }
  const asin = currentAsinFromUrl();
  showPanel("Nutricity fulfilment", `Adding ${expectedItem.asin} for ${recipientName(activeJob)}.`, null, null);
  if (asin && asin !== expectedItem.asin) {
    const redirectAttempts = activeJob.asinRedirectAttempts && typeof activeJob.asinRedirectAttempts === "object"
      ? activeJob.asinRedirectAttempts
      : {};
    const key = `${expectedItem.asin}:${asin}`;
    const attempts = Number(redirectAttempts[key] || 0) + 1;
    activeJob.asinRedirectAttempts = { ...redirectAttempts, [key]: attempts };
    await setActiveJob(activeJob);
    if (attempts <= 1) {
      showPanel(
        "Nutricity fulfilment",
        `Amazon opened ASIN ${asin} while ${expectedItem.asin} was requested. Retrying the requested product page once.`,
        null,
        null,
      );
      location.href = `https://www.amazon.com/dp/${expectedItem.asin}?th=1`;
      return;
    }
    await failCurrentItemAsMissing(
      activeJob,
      item,
      expectedItem,
      `ASIN ${expectedItem.asin} redirects to Amazon ASIN ${asin}, so the requested ASIN could not be safely ordered.`,
      "asin_redirect_mismatch",
    );
    return;
  }
  rememberProductDosage(activeJob, item);
  rememberProductPack(activeJob, item);
  await setActiveJob(activeJob);

  const unavailable = unavailableMessage();
  if (unavailable) {
    await failCurrentItemAsMissing(activeJob, item, expectedItem, `ASIN ${expectedItem.asin} is unavailable on Amazon. ${unavailable}`);
    return;
  }

  const extensionState = await getExtensionState();
  const deliveryLimitDays = normalizedDeliveryLimitDays(extensionState.deliveryLimitDays);
  const deliveryPromise = productDeliveryPromise(deliveryLimitDays);
  if (deliveryPromise.late) {
    await failCurrentItemAsMissing(
      activeJob,
      item,
      expectedItem,
      `ASIN ${expectedItem.asin} is marked Missing because Amazon's earliest estimated delivery is ${deliveryPromise.daysFromToday} days away (${deliveryPromise.text}), beyond the ${deliveryLimitDays}-day limit.`,
      "late_delivery",
    );
    return;
  }

  const savingsApplied = await applyAdditionalSavings("regular");
  if (savingsApplied) {
    await waitForStableDom(900, 6000);
  }
  const promoCount = markPromotions();
  if (promoCount && !activeJob.promoAcknowledged[item.asin]) {
    activeJob.promoAcknowledged[item.asin] = true;
    await setActiveJob(activeJob);
    showPanel("Nutricity fulfilment", "Coupon or promotion detected. Continuing price comparison.", null, null);
    await sleep(900);
  }

  const priceSnapshot = productPriceSnapshot();
  const mixedAsinOrder = isMixedAsinOrder(activeJob);
  const priceForVariantDecision = mixedAsinOrder ? (priceSnapshot.regular || priceSnapshot.best) : priceSnapshot.best;
  const switchedVariant = await selectCheapestCountVariant(activeJob, item, priceForVariantDecision);
  if (switchedVariant) return;
  const purchaseItem = selectedVariantItem(activeJob, item);
  const mixedAsinAfterVariant = isMixedAsinOrder(activeJob);
  const selectionNote = variantSelectionNote(item, purchaseItem);
  if (selectionNote) {
    showPanel("Pack variant found", `${selectionNote} Proceeding with this option.`, null, null);
    await sleep(1800);
  }
  const snsIsCheaper = priceSnapshot.sns && priceSnapshot.regular && priceSnapshot.sns < priceSnapshot.regular;
  const useSubscribeAndSave = Boolean(snsIsCheaper);
  const priceForDecision = useSubscribeAndSave ? priceSnapshot.sns : (priceSnapshot.regular || priceSnapshot.best);
  await recordAmazonPrice(
    activeJob,
    item,
    priceForDecision,
    useSubscribeAndSave ? (mixedAsinAfterVariant ? "subscribe-save-cart" : "subscribe-save") : "product",
    purchaseItem,
  );
  const quantity = Number(purchaseItem.quantity || 1);
  const storeTotal = Number(item.store_total_price || Number(item.store_unit_price || 0) * Number(item.quantity || 1) || 0);
  const amazonTotal = Number(priceForDecision || 0) * quantity;
  if (!item.cost_approved && storeTotal > 0 && amazonTotal > storeTotal) {
    const forceApproved = await waitForCostlyLossOverride(activeJob, item, purchaseItem, storeTotal, amazonTotal);
    if (forceApproved) {
      showPanel("Loss fulfilment approved", `Continuing ASIN ${purchaseItem.asin} after manual loss approval.`, null, null);
    } else {
      await send({
        type: "COSTLY_JOB",
        message: `ASIN ${purchaseItem.asin} costs $${amazonTotal.toFixed(2)} on Amazon but store sale value is $${storeTotal.toFixed(2)}. Approval required before fulfilment.`,
        costlyAsin: item.asin,
        costlyLineId: itemPrimaryLineId(item),
        storeTotalPrice: storeTotal,
        amazonTotalPrice: amazonTotal,
      });
      showPanel("Costly fulfilment review", "No loss approval was selected within 10 seconds. Order moved to Costly and the next queued order is starting.", null, null);
      return;
    }
  }

  const subscribed = useSubscribeAndSave
    ? await applySubscribeAndSaveIfCheaper(purchaseItem.quantity, activeJob, { requireCartAdd: mixedAsinAfterVariant })
    : false;
  if (!subscribed) {
    if (useSubscribeAndSave) {
      throw new Error(`Subscribe & Save is cheaper (${moneyText(priceSnapshot.sns)} vs ${moneyText(priceSnapshot.regular)}), but the extension did not complete the Subscribe & Save selection. Fulfilment paused before changing regular quantity.`);
    }
    const quantitySet = await setQuantity(purchaseItem.quantity, "regular");
    if (!quantitySet) {
      const requestedQuantity = Math.max(1, Math.round(Number(purchaseItem.quantity || 1)));
      const quantityIssue = window.__nutricityLastQuantityIssue || {};
      const availableQuantity = quantityIssue.availableQuantity || maxPredefinedQuantity("regular") || maxSelectableQuantity("regular");
      const lessQuantity = availableQuantity > 0 && availableQuantity < requestedQuantity;
      const issueMessage = quantityIssue.message ? ` ${quantityIssue.message}` : "";
      const message = lessQuantity
        ? `Less quantity available for ASIN ${purchaseItem.asin}. Customer ordered ${requestedQuantity}, Amazon only allows ${availableQuantity}.${issueMessage}`
        : `ASIN ${purchaseItem.asin} is missing or unavailable. Could not set quantity ${purchaseItem.quantity || 1}.${issueMessage}`;
      if (await continueAfterPartialMissing(activeJob, item, purchaseItem, message, "partial_quantity", {
        requestedQuantity,
        fulfilledQuantity: availableQuantity || null,
        availableQuantity: availableQuantity || null,
      })) {
        return;
      }
      showPanel("Sending to Missing ASINs", message, null, null);
      await send({
        type: "FAIL_JOB",
        message,
        missingAsin: item.asin,
        missingLineId: itemPrimaryLineId(item),
        failureCode: "partial_quantity",
        requestedQuantity,
        fulfilledQuantity: availableQuantity || null,
        availableQuantity: availableQuantity || null,
      });
      showPanel("Missing ASINs", `${message} Order moved to Missing ASINs.`, null, null);
      return;
    }
    const requestedQuantity = Math.max(1, Math.round(Number(purchaseItem.quantity || 1)));
    if (requestedQuantity > 1 && !quantityValueAccepted("regular", requestedQuantity)) {
      const message = `ASIN ${purchaseItem.asin} is missing or unavailable. Amazon did not verify regular Add to cart quantity ${requestedQuantity}.`;
      if (await continueAfterPartialMissing(activeJob, item, purchaseItem, message, "partial_quantity", {
        requestedQuantity,
        fulfilledQuantity: null,
        availableQuantity: null,
      })) {
        return;
      }
      showPanel("Sending to Missing ASINs", message, null, null);
      await send({
        type: "FAIL_JOB",
        message,
        missingAsin: item.asin,
        missingLineId: itemPrimaryLineId(item),
        failureCode: "partial_quantity",
        requestedQuantity,
        fulfilledQuantity: null,
        availableQuantity: null,
      });
      showPanel("Missing ASINs", `${message} Order moved to Missing ASINs.`, null, null);
      return;
    }
    const addButton = await waitForElement(["#add-to-cart-button", "input[name='submit.add-to-cart']", "#buybox-add-to-cart-button input"], 18000);
    activeJob.stage = "add_clicked";
    activeJob.addClickedAt = Date.now();
    markItemAdded(activeJob);
    await setActiveJob(activeJob);
    const added = await clickElement(addButton, "Add to cart button");
    if (!added) {
      const message = `ASIN ${purchaseItem.asin} is missing or unavailable. Could not find Add to cart button.`;
      showPanel("Sending to Missing ASINs", message, null, null);
      await send({ type: "FAIL_JOB", message, missingAsin: item.asin, missingLineId: itemPrimaryLineId(item) });
      showPanel("Missing ASINs", `${message} Job marked as error.`, null, null);
      return;
    }
  }

  showPanel("Nutricity fulfilment", `Add clicked for ${purchaseItem.asin}. Waiting for Amazon before moving on.`, null, null);
}

async function handleAddClicked(activeJob) {
  const clickedAt = Number(activeJob.addClickedAt || 0);
  const remainingWaitMs = Math.max(0, 4500 - (Date.now() - clickedAt));
  if (remainingWaitMs) {
    showPanel("Nutricity fulfilment", "Amazon add was clicked. Waiting for Amazon to finish updating the cart.", null, null);
    // Amazon product pages can contain hidden cart/checkout markup before the
    // current Add request has finished. Treating that markup as confirmation
    // allowed a multi-item run to navigate away early and cancel item 2's Add
    // request. Keep the page alive for the full post-click settling window.
    // A real Amazon navigation unloads this script and the destination page
    // resumes the saved add_clicked stage normally.
    await sleep(remainingWaitMs);
  }
  if (/\/cart/i.test(location.pathname)) {
    const nextIndex = Number(activeJob.itemIndex || 0) + 1;
    if (nextIndex >= (activeJob.job?.items || []).length) {
      activeJob.stage = "cart";
      await setActiveJob(activeJob);
      await handleCart(activeJob);
      return;
    }
  }
  await navigateToNext(activeJob);
}

async function handleSubscribeCheckout(activeJob) {
  markCheckoutStarted(activeJob);
  activeJob.subscribeAndSave = true;
  if (/\/checkout/i.test(location.pathname) || document.querySelector("#placeOrder, input.place-your-order-button, [data-checkout-view-modal], #checkout-primary-continue-button-id, input[aria-label='Full name']")) {
    activeJob.stage = "checkout";
    await setActiveJob(activeJob);
    await handleCheckout(activeJob);
    return;
  }
  showPanel("Nutricity fulfilment", "Subscribe & Save clicked. Waiting for checkout.", null, null);
  await sleep(2500);
  const latest = await getActiveJob();
  if (latest?.stage === "subscribe_checkout" && !/\/checkout/i.test(location.pathname)) {
    latest.stage = "checkout";
    await setActiveJob(latest);
  }
}

async function retryProvenMissingCartItemOnce(activeJob, itemIndex, asin, expectedQuantity) {
  const normalizedAsin = String(asin || "").toUpperCase();
  if (!normalizedAsin || cartQuantityForAsin(normalizedAsin) !== 0) return false;
  const retries = activeJob.cartMissingAddRetries && typeof activeJob.cartMissingAddRetries === "object"
    ? activeJob.cartMissingAddRetries
    : {};
  const retryKey = `${Number(itemIndex || 0)}:${normalizedAsin}`;
  if (Number(retries[retryKey] || 0) >= 1) return false;

  activeJob.cartMissingAddRetries = { ...retries, [retryKey]: 1 };
  activeJob.itemIndex = Number(itemIndex || 0);
  activeJob.stage = "product";
  activeJob.addClickedAt = null;
  await setActiveJob(activeJob, {
    allowStageRegression: true,
    reason: "retry_proven_missing_cart_item_once",
  });
  await sendDiagnostic("Retrying Add to cart once after the active cart proved the ASIN was absent.", {
    group_key: activeJob?.job?.group_key || "",
    asin: normalizedAsin,
    expected_quantity: expectedQuantity,
    cart_quantity: 0,
    item_index: Number(itemIndex || 0),
    cart: cartDiagnosticSummary(),
  }, "warn");
  showPanel(
    "Retrying missing cart item",
    `The active Amazon cart proves ${normalizedAsin} has quantity 0. Retrying Add to cart once; a second failure will stop the order.`,
    null,
    null,
  );
  await sleep(1000);
  location.href = `https://www.amazon.com/dp/${normalizedAsin}?th=1`;
  return true;
}

async function handleCart(activeJob) {
  const nextIndex = Number(activeJob.itemIndex || 0) + 1;
  const itemCount = Array.isArray(activeJob.job?.items) ? activeJob.job.items.length : 0;
  const shouldVerifyCurrentItemBeforeContinuing = (
    !checkoutWasStarted(activeJob)
    && !["clear_cart", "cleanup_after_failure"].includes(String(activeJob.stage || ""))
    && nextIndex < itemCount
    && itemWasAdded(activeJob)
  );
  if (shouldVerifyCurrentItemBeforeContinuing && /\/cart\/smart-wagon/i.test(location.pathname)) {
    showPanel("Nutricity fulfilment", "Opening the full Amazon cart to verify this line item before adding the next selected item.", null, null);
    location.href = "https://www.amazon.com/cart?ref_=sw_gtc";
    return;
  }
  if (shouldVerifyCurrentItemBeforeContinuing) {
    await waitForElement(["#sc-active-cart", "#sc-empty-cart"], 15000);
    const currentItem = activeJob.job.items[Number(activeJob.itemIndex || 0)];
    const purchaseItem = selectedVariantItem(activeJob, currentItem);
    const expectedAsin = String(purchaseItem?.asin || currentItem?.asin || "").toUpperCase();
    const expectedQuantity = Math.max(1, Math.round(Number(purchaseItem?.quantity || currentItem?.quantity || 1)));
    await waitUntil(() => (
      cartQuantityForAsin(expectedAsin) > 0 || cartIsVisiblyEmpty()
    ), 12000, 300);
    const actualQuantity = cartQuantityForAsin(expectedAsin);
    if (actualQuantity < expectedQuantity) {
      const retryKey = `verify:${Number(activeJob.itemIndex || 0)}:${expectedAsin}`;
      const retries = activeJob.cartAddVerificationRetries && typeof activeJob.cartAddVerificationRetries === "object"
        ? activeJob.cartAddVerificationRetries
        : {};
      const retryCount = Number(retries[retryKey] || 0);
      if (retryCount < MAX_CART_VERIFICATION_RELOADS) {
        activeJob.cartAddVerificationRetries = { ...retries, [retryKey]: retryCount + 1 };
        activeJob.stage = "cart";
        await setActiveJob(activeJob, { reason: "recheck_unverified_cart_add" });
        showPanel(
          "Rechecking Amazon cart",
          `ASIN ${expectedAsin} is not readable in the active cart yet. Stabilization check ${retryCount + 1} of ${MAX_CART_VERIFICATION_RELOADS}; reloading without clicking Add to cart again.`,
          null,
          null,
        );
        await sleep(1500 * (retryCount + 1));
        location.reload();
        return;
      }
      if (actualQuantity === 0 && await retryProvenMissingCartItemOnce(
        activeJob,
        Number(activeJob.itemIndex || 0),
        expectedAsin,
        expectedQuantity,
      )) return;
      const message = `Could not verify ASIN ${expectedAsin} in the Amazon cart after ${MAX_CART_VERIFICATION_RELOADS} stabilization reloads. Expected ${expectedQuantity}, cart has ${actualQuantity}. ${cartDiagnosticSummary()} Checkout was stopped without clicking Add to cart a second time.`;
      showPanel("Cart verification needs review", message, null, null);
      await send({
        type: "FAIL_JOB",
        message,
        missingAsin: "",
        missingLineId: null,
        failureCode: "cart_verification_failed",
        requestedQuantity: expectedQuantity,
        fulfilledQuantity: actualQuantity,
        availableQuantity: null,
      });
      return;
    }
    showPanel(
      "Nutricity fulfilment",
      `Verified ${expectedAsin} in the active cart after item ${Number(activeJob.itemIndex || 0) + 1} of ${itemCount}. Continuing with the next selected line item.`,
      null,
      null,
    );
    await navigateToNext(activeJob);
    return;
  }
  if (/\/cart\/smart-wagon/i.test(location.pathname)) {
    showPanel("Nutricity fulfilment", "Opening full Amazon cart before verifying this order.", null, null);
    location.href = "https://www.amazon.com/cart?ref_=sw_gtc";
    return;
  }
  await waitForElement(["#sc-active-cart", "input[name='proceedToRetailCheckout']", "#sc-buy-box-ptc-button input"], 15000);
  if (cartIsVisiblyEmpty() && activeJob.cartCleared && activeJob.stage === "cart" && !activeJob.addClickedAt && !activeJob.subscribeAndSave) {
    const first = activeJob.job?.items?.[Number(activeJob.itemIndex || 0)] || activeJob.job?.items?.[0] || null;
    const asin = String(first?.asin || "").toUpperCase();
    if (asin) {
      activeJob.stage = "product";
      activeJob.startedAfterEmptyCart = true;
      await setActiveJob(activeJob);
      showPanel("Nutricity fulfilment", `Cart is empty before adding ${asin}. Opening the product page instead of treating it as missing.`, null, null);
      location.href = `https://www.amazon.com/dp/${asin}`;
      return;
    }
  }
  if (activeJob.subscribeAndSave || activeJob.stage === "subscribe_checkout") {
    if (cartIsVisiblyEmpty()) {
      const item = activeJob.job?.items?.[Number(activeJob.itemIndex || 0)] || activeJob.job?.items?.[0] || null;
      const purchaseItem = item ? selectedVariantItem(activeJob, item) : item;
      const asin = purchaseItem?.asin || item?.asin || "unknown";
      const attempts = Number(activeJob.emptySubscribeCartRetryCount || 0);
      if (attempts < 1 && asin !== "unknown") {
        activeJob.emptySubscribeCartRetryCount = attempts + 1;
        activeJob.stage = "product";
        activeJob.subscribeAndSave = false;
        await setActiveJob(activeJob, { allowStageRegression: true });
        showPanel("Nutricity fulfilment", `Subscribe & Save returned to an empty cart for ASIN ${asin}. Retrying the product page once before reporting anything.`, null, null);
        location.href = `https://www.amazon.com/dp/${asin}?th=1`;
        return;
      }
      await pauseForManualCheckout(
        activeJob,
        `Subscribe & Save returned to an empty Amazon cart for ASIN ${asin}. The product page was reachable, so this was not marked Missing.`,
        "product",
      );
      return;
    }
    activeJob.stage = "checkout";
    await setActiveJob(activeJob);
    showPanel("Nutricity fulfilment", "Subscribe & Save is already in checkout flow. Not clearing cart.", null, null);
    return;
  }
  if (activeJob.stage === "clear_cart") {
    if (!canClearCart(activeJob)) {
      activeJob.stage = "cart";
      activeJob.cartCleared = true;
      await setActiveJob(activeJob);
    } else {
      await handleClearCart(activeJob);
      return;
    }
  }
  const cartCheck = verifyCartQuantities(activeJob);
  if (!cartCheck.ok) {
    const mismatch = (cartCheck.mismatches || [])[0];
    const missingAsin = mismatch?.asin || "";
    if (mismatch && Number(mismatch.actual || 0) <= 0 && (activeJob.job?.items || []).length > 1) {
      const missingIndex = (activeJob.job.items || []).findIndex((entry) => {
        const requestedAsin = String(entry.asin || "").toUpperCase();
        const purchasedAsin = String(activeJob.pricing?.[requestedAsin]?.purchased_asin || requestedAsin).toUpperCase();
        return purchasedAsin === String(missingAsin || "").toUpperCase();
      });
      const retryKey = `final-verify:${missingIndex}:${missingAsin}`;
      const retries = activeJob.cartAddVerificationRetries && typeof activeJob.cartAddVerificationRetries === "object"
        ? activeJob.cartAddVerificationRetries
        : {};
      const retryCount = Number(retries[retryKey] || 0);
      if (missingIndex >= 0 && retryCount < MAX_CART_VERIFICATION_RELOADS) {
        activeJob.cartAddVerificationRetries = { ...retries, [retryKey]: retryCount + 1 };
        activeJob.stage = "cart";
        await setActiveJob(activeJob, { reason: "recheck_missing_final_cart_item" });
        showPanel(
          "Rechecking complete cart",
          `ASIN ${missingAsin} is not readable in the complete cart yet. Stabilization check ${retryCount + 1} of ${MAX_CART_VERIFICATION_RELOADS}; reloading without clicking Add to cart again.`,
          null,
          null,
        );
        await sleep(1500 * (retryCount + 1));
        location.reload();
        return;
      }
    }
    if (mismatch?.mismatch_type === "missing_from_cart") {
      const message = `Amazon cart stayed empty after Add to cart for ASIN ${missingAsin}. Customer ordered ${mismatch.expected}, Amazon cart showed 0. ${cartDiagnosticSummary()} This may be a cart timing or parser issue, so the order was paused as an error instead of moved to Missing ASINs.`;
      showPanel("Cart verification needs review", message, null, null);
      await send({
        type: "FAIL_JOB",
        message,
        missingAsin: "",
        missingLineId: null,
        failureCode: "cart_verification_failed",
        requestedQuantity: mismatch.expected,
        fulfilledQuantity: 0,
        availableQuantity: null,
      });
      return;
    }
    if (mismatch && Number(mismatch.actual || 0) <= 0) {
      const message = `Could not verify ASIN ${missingAsin} in the Amazon cart after Add to cart. Customer ordered ${mismatch.expected}, Amazon cart showed ${mismatch.actual}. ${cartDiagnosticSummary()} This may be a cart timing or parser issue, so the order was paused as an error instead of moved to Missing ASINs.`;
      showPanel("Cart verification needs review", message, null, null);
      await send({
        type: "FAIL_JOB",
        message,
        missingAsin: "",
        missingLineId: null,
        failureCode: "cart_verification_failed",
        requestedQuantity: mismatch.expected,
        fulfilledQuantity: mismatch.actual,
        availableQuantity: null,
      });
      return;
    }
    if (mismatch?.mismatch_type === "over" || Number(mismatch?.actual || 0) > Number(mismatch?.expected || 0)) {
      const message = `Amazon cart has too many units for ASIN ${missingAsin}. Customer ordered ${mismatch.expected}, Amazon cart has ${mismatch.actual}. Cart will be cleaned before the next job.`;
      showPanel("Cart quantity mismatch", message, null, null);
      await send({
        type: "FAIL_JOB",
        message,
        missingAsin: "",
        missingLineId: null,
        failureCode: "cart_quantity_mismatch",
        requestedQuantity: mismatch.expected,
        fulfilledQuantity: mismatch.actual,
        availableQuantity: mismatch.actual,
      });
      return;
    }
    const message = mismatch
      ? `Could not add the desired quantity for ASIN ${missingAsin}. Customer ordered ${mismatch.expected}, Amazon cart has ${mismatch.actual}.`
      : `Could not verify the desired Amazon cart quantities. ${cartCheck.message}`;
    const missingItem = (activeJob.job?.items || []).find((entry) => String(entry.asin || "").toUpperCase() === String(missingAsin || "").toUpperCase());
    if (missingItem && (activeJob.job?.items || []).length > 1 && await shouldFulfilAvailableMixedAsin(activeJob)) {
      showPanel("Sending line to Missing ASINs", message, null, null);
      const partialResult = await send({
        type: "MARK_LINE_MISSING",
        message,
        missingAsin,
        missingLineId: itemPrimaryLineId(missingItem),
        failureCode: "partial_quantity",
        requestedQuantity: mismatch?.expected ?? null,
        fulfilledQuantity: mismatch?.actual ?? 0,
        availableQuantity: mismatch?.actual ?? 0,
      });
      if (partialResult?.ok) {
        removeMissingItemFromActiveJob(activeJob, missingItem, selectedVariantItem(activeJob, missingItem));
        activeJob.stage = "cart";
        await setActiveJob(activeJob);
        showPanel("Split fulfilment", `${message} ${partialResult.message || ""} Proceeding to checkout for remaining Amazon item(s).`, null, null);
        return;
      }
    }
    showPanel("Sending to Missing ASINs", message, null, null);
    await send({
      type: "FAIL_JOB",
      message,
      missingAsin,
      missingLineId: lineIdForAsin(activeJob, missingAsin),
      failureCode: "partial_quantity",
      requestedQuantity: mismatch?.expected ?? null,
      fulfilledQuantity: mismatch?.actual ?? 0,
      availableQuantity: mismatch?.actual ?? 0,
    });
    showPanel("Missing ASINs", `${message} Order moved to Missing ASINs.`, null, null);
    return;
  }
  if (cartCheck.warning) {
    showPanel("Nutricity fulfilment", cartCheck.warning, null, null);
    await sleep(1800);
  }
  activeJob.cartVerification = cartCheck.exact === true
    ? {
        group_key: activeJob.job?.group_key || "",
        quantities: { ...(cartCheck.quantities || {}) },
        verified_at: Date.now(),
      }
    : null;
  await setActiveJob(activeJob);
  showPanel("Nutricity fulfilment", "Cart ready. Proceeding to checkout.", null, null);
  let clicked = false;
  const checkoutInput = await waitForElement([
    "input[name='proceedToRetailCheckout']",
    "#sc-buy-box-ptc-button input",
    "input[data-feature-id='proceed-to-checkout-action']",
  ], 18000);
  if (checkoutInput) {
    markCheckoutStarted(activeJob);
    activeJob.stage = "checkout";
    await setActiveJob(activeJob);
    clicked = await clickElement(checkoutInput, "Proceed to checkout button");
  }
  if (!clicked) {
    const button = findButtonByText(["proceed to checkout", "check out amazon cart"]);
    if (button) {
      markCheckoutStarted(activeJob);
      activeJob.stage = "checkout";
      await setActiveJob(activeJob);
      clicked = await clickElement(button, "Proceed to checkout button");
    }
  } else {
    markCheckoutStarted(activeJob);
    activeJob.stage = "checkout";
    await setActiveJob(activeJob);
  }
  if (!clicked) return;
}

async function fillFullName(name) {
  if (window.__nutricityFillingFullName) return false;
  window.__nutricityFillingFullName = true;
  try {
  const input = await waitUntil(() => (
    document.querySelector("input#address-ui-widgets-enterAddressFullName[name='address-ui-widgets-enterAddressFullName']")
    || findAddressNameInput()
  ), 20000);
  if (!input) return false;
  const desired = String(name || "").replace(/\s+/g, " ").trim();
  if (!desired) return false;
  input.scrollIntoView({ block: "center", behavior: "smooth" });
  await sleep(500);
  await setInputValue(input, desired);
  await sleep(800);
  let saved = input.value.replace(/\s+/g, " ").trim() === desired;
  if (!saved) {
    await setInputValue(input, desired);
    await sleep(500);
    saved = input.value.replace(/\s+/g, " ").trim() === desired;
  }
  input.blur();
  return saved;
  } finally {
    window.__nutricityFillingFullName = false;
  }
}

async function setInputValue(input, value) {
  const desired = String(value ?? "");
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  input.focus();
  input.click?.();
  await sleep(100);
  input.select?.();
  setter ? setter.call(input, "") : (input.value = "");
  input.dispatchEvent(new InputEvent("beforeinput", { bubbles: true, composed: true, inputType: "deleteContentBackward", data: null }));
  input.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, inputType: "deleteContentBackward", data: null }));
  await sleep(50);
  input.dispatchEvent(new InputEvent("beforeinput", { bubbles: true, composed: true, inputType: "insertText", data: desired }));
  setter ? setter.call(input, desired) : (input.value = desired);
  input.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, inputType: "insertText", data: desired }));
  input.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  input.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, composed: true, key: "Tab" }));
  input.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true, composed: true, key: "Tab" }));
}

function addressDataForRow(row) {
  const carrier = row?.closest?.("[data-action='select_address_in_list'][data-select_address_in_list]") || row?.querySelector?.("[data-action='select_address_in_list'][data-select_address_in_list]");
  if (!carrier) return {};
  try {
    return JSON.parse(carrier.getAttribute("data-select_address_in_list") || "{}");
  } catch {
    return {};
  }
}

function rowIsNutricityAddress(row) {
  if (!row) return false;
  const data = addressDataForRow(row);
  const dataName = normalizedText(data.fullName);
  const visibleName = normalizedText(row.querySelector?.("[data-testid='address-row-radio-name']")?.textContent || "");
  const rowText = normalizedText(row.innerText || row.textContent || "");
  return dataName.includes("nutricity") || visibleName.includes("nutricity") || rowText.includes("nutricity");
}

function warehouseAddressTokens() {
  const address = DEFAULT_NEW_DELIVERY_ADDRESS;
  return [
    address.addressLine1,
    address.postalCode,
    String(address.postalCode || "").split("-")[0],
  ].map(normalizedText).filter(Boolean);
}

function rowMatchesWarehouseAddress(row) {
  if (!row) return false;
  const data = addressDataForRow(row);
  const rowText = normalizedText([
    row.innerText,
    row.textContent,
    data.addressLine1,
    data.addressLine2,
    data.city,
    data.stateOrRegion,
    data.postalCode,
  ].filter(Boolean).join(" "));
  return warehouseAddressTokens().some((token) => rowText.includes(token));
}

function rowIsSafeNutricityAddress(row) {
  return rowIsNutricityAddress(row) && rowMatchesWarehouseAddress(row);
}

function checkoutAddressRows() {
  const selectorRows = [...document.querySelectorAll(
    [
      "[data-testid='address-row-radio']",
      "[data-testid='address-row-section']",
      "[data-testid^='address-row-'][id^='address-row-']",
      ".address-row-section",
      ".address-row",
      "[data-action='select_address_in_list']",
    ].join(", "),
  )];
  const radioRows = [...document.querySelectorAll("input[type='radio']")]
    .map((radio) => {
      let row = radio.closest("label, .a-row, .a-section, div");
      for (let i = 0; row && i < 6; i += 1) {
        const text = normalizedText(row.innerText || row.textContent || "");
        if (text.includes("edit address") || text.includes("delivery preferences") || text.includes("united states")) return row;
        row = row.parentElement;
      }
      return radio.closest("label") || radio.parentElement;
    })
    .filter(Boolean);
  return [...new Set([...selectorRows, ...radioRows])].filter(visible);
}

function selectedCheckoutAddressRow() {
  const checked = [...document.querySelectorAll("input[type='radio']:checked")]
    .map((radio) => {
      let row = radio.closest("[data-testid='address-row-radio'], [data-testid='address-row-section'], [data-testid^='address-row-'], .address-row-section, .address-row, [data-action='select_address_in_list']");
      if (row) return row;
      row = radio.closest("label, .a-row, .a-section, div");
      for (let index = 0; row && index < 6; index += 1) {
        const text = normalizedText(row.innerText || row.textContent || "");
        if (text.includes("edit address") || text.includes("delivery preferences") || text.includes("united states")) return row;
        row = row.parentElement;
      }
      return radio.closest("label") || radio.parentElement;
    })
    .find((row) => row && visible(row));
  return checked || null;
}

function nutricityAddressRow() {
  const selectedRow = selectedCheckoutAddressRow();
  if (rowIsSafeNutricityAddress(selectedRow)) return selectedRow;
  return checkoutAddressRows().find(rowIsSafeNutricityAddress) || null;
}

function addressRowForRecipient(name) {
  const wanted = normalizedText(name);
  if (!wanted) return null;
  return checkoutAddressRows().find((row) => {
    const data = addressDataForRow(row);
    const recipient = normalizedText(
      data.fullName
      || row.querySelector?.("[data-testid='address-row-radio-name']")?.textContent
      || row.querySelector?.("[data-test-id*='recipient'], [data-testid*='recipient']")?.textContent
      || String(row.innerText || row.textContent || "").split(/\r?\n/).find((line) => line.trim())
      || "",
    );
    return recipient === wanted && rowMatchesWarehouseAddress(row);
  }) || null;
}

function addressSelectionControl(row) {
  if (!row) return null;
  const label = row.closest?.("label");
  const radio = row.matches?.("input[type='radio']") ? row
    : row.querySelector("input[type='radio'][name='addressID'], input[type='radio']")
    || label?.querySelector?.("input[type='radio'][name='addressID']")
    || row.closest?.("[data-testid='address-row-radio']")?.querySelector?.("input[type='radio'][name='addressID']");
  return radio?.closest?.("label") || radio || row;
}

function nutricityAddressSelectionControl() {
  return addressSelectionControl(nutricityAddressRow());
}

function recipientAddressSelectionControl(name) {
  return addressSelectionControl(addressRowForRecipient(name));
}

function addressSelectionControlForElement(element) {
  const row = element?.closest?.(
    "[data-testid='address-row-radio'], [data-testid='address-row-section'], [data-testid^='address-row-'], .address-row-section, .address-row, [data-action='select_address_in_list']",
  );
  if (!row) return null;
  const label = row.closest?.("label");
  const radio = row.querySelector("input[type='radio'][name='addressID']")
    || label?.querySelector?.("input[type='radio'][name='addressID']")
    || row.closest?.("[data-testid='address-row-radio']")?.querySelector?.("input[type='radio'][name='addressID']");
  return radio?.checked ? null : radio?.closest?.("label") || radio || null;
}

async function selectAddressRadioForElement(element) {
  const control = addressSelectionControlForElement(element);
  if (!control) return false;
  showPanel("Nutricity checkout", "Selecting delivery address row.", null, null);
  await clickElement(control, "Address radio button");
  await sleep(700);
  return true;
}

function findEditAddressTrigger() {
  // Amazon's address row can contain several modal actions (including
  // delivery preferences). Always pick the literal Edit address control from
  // the selected safe warehouse row before considering generic modal hosts.
  // Selecting a host's first child can click a non-edit action and leaves the
  // worker paused on the unchanged address list.
  const selectedRow = selectedCheckoutAddressRow();
  const preferredRows = [selectedRow, nutricityAddressRow()]
    .filter((row, index, rows) => row && rowIsSafeNutricityAddress(row) && rows.indexOf(row) === index);
  for (const row of preferredRows) {
    const exactEdit = [...row.querySelectorAll("a, button")].find((element) =>
      visible(element) && normalizedText(element.innerText || element.textContent) === "edit address",
    );
    if (exactEdit) return exactEdit;
  }
  const triggers = [...document.querySelectorAll("[id^='declarativeAction-'][data-action='checkout-view-modal']")].filter((element) => {
    const modalData = element.getAttribute("data-checkout-view-modal") || "";
    const text = (element.innerText || element.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
    return visible(element) && (modalData.includes("editAddressModal") || text.includes("edit address"));
  });
  const selectedRowTrigger = triggers.find((element) => selectedRow && rowIsSafeNutricityAddress(selectedRow) && selectedRow.contains(element));
  if (selectedRowTrigger) return selectedRowTrigger.querySelector("a, button, span") || selectedRowTrigger;

  const nutricityRowTrigger = triggers.find((element) => {
    const row = element.closest("[data-testid='address-row-radio'], [data-testid='address-row-section'], [data-testid^='address-row-'], .address-row-section, .address-row, [data-action='select_address_in_list']");
    return rowIsSafeNutricityAddress(row);
  });
  if (nutricityRowTrigger) return nutricityRowTrigger.querySelector("a, button, span") || nutricityRowTrigger;

  const preferred = triggers.find((element) => {
    const row = element.closest("[data-testid='address-row-radio'], [data-testid='address-row-section'], [data-testid^='address-row-'], .address-row-section, .address-row, [data-action='select_address_in_list']");
    return rowIsSafeNutricityAddress(row);
  });
  const selected = preferred || triggers[1] || triggers[0];
  if (selected) return selected.querySelector("a, button, span") || selected;

  const modalTriggers = [...document.querySelectorAll("[data-checkout-view-modal]")].filter((element) => {
    const modalData = element.getAttribute("data-checkout-view-modal") || "";
    return visible(element) && modalData.includes("editAddressModal");
  });
  const modalTrigger = modalTriggers[1] || modalTriggers[0];
  if (modalTrigger) return modalTrigger.querySelector("a, button, span") || modalTrigger;

  const xpath = "//*[starts-with(@id,'declarativeAction-') and .//*[normalize-space()='Edit address']]";
  const xpathNode = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
  if (xpathNode && visible(xpathNode)) return xpathNode.querySelector?.("a, button, span") || xpathNode;

  const nutricityRow = nutricityAddressRow();
  const rowEditLink = [...(nutricityRow?.querySelectorAll?.("a, button, span") || [])].find((element) => {
    const text = (element.innerText || element.textContent || "").trim().toLowerCase();
    return visible(element) && text === "edit address";
  });
  if (rowEditLink) return rowEditLink;

  return [...document.querySelectorAll("a, button, span")].find((element) => {
    const text = (element.innerText || element.textContent || "").trim().toLowerCase();
    return visible(element) && text === "edit address";
  });
}

function normalizedText(value) {
  return String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
}

function compactMatchText(value) {
  return normalizedText(value).replace(/[^a-z0-9]/g, "");
}

function elementReadableText(element) {
  if (!element) return "";
  return [
    element.innerText,
    element.textContent,
    element.getAttribute?.("aria-label"),
    element.getAttribute?.("title"),
    element.getAttribute?.("data-testid"),
  ].filter(Boolean).join(" ");
}

function checkoutShowsRecipient(name) {
  return checkoutDeliveryRecipientMatches(name) && checkoutShowsWarehouseAddress();
}

function currentCheckoutDeliveryText() {
  const roots = [
    document.querySelector("#change-delivery-link"),
    document.querySelector("#deliver-to-address-text"),
    document.querySelector("#deliver-to-address-text")?.closest?.("a, .a-row, .a-section, div"),
    document.querySelector("#deliver-to-customer-text")?.closest?.("a, .a-row, .a-section, div"),
    ...document.querySelectorAll(
      [
        "a[aria-label*='Delivering to' i]",
        "a[aria-label*='delivery address' i]",
        "a[href*='shipaddressselect']",
        "a[href*='ChangeDelivery']",
      ].join(", "),
    ),
  ].filter(Boolean);
  return roots
    .filter((root) => root && visible(root) && !root.closest?.("#nutricity-panel") && !root.closest?.(".a-popover, .a-popover-preload"))
    .map(elementReadableText)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
}

function checkoutShowsWarehouseAddress() {
  const bodyText = (document.body?.innerText || document.body?.textContent || "").replace(/\s+/g, " ");
  const deliveryStart = bodyText.search(/\bDelivering\s+to\b/i);
  // Amazon's Business checkout varies its delivery-card markup.  Read a
  // bounded section after the visible "Delivering to" heading rather than
  // relying on one particular link or address-card structure.
  const bodyDeliveryText = deliveryStart >= 0 ? bodyText.slice(deliveryStart, deliveryStart + 600) : "";
  const text = normalizedText([currentCheckoutDeliveryText(), bodyDeliveryText].join(" "));
  const tokens = warehouseAddressTokens();
  return tokens.some((token) => text.includes(token));
}

function checkoutPageShowsExpectedDelivery(name) {
  const wanted = normalizedText(name);
  if (!wanted) return false;
  const bodyText = (document.body?.innerText || document.body?.textContent || "").replace(/\s+/g, " ");
  const deliveryStart = bodyText.search(/\bDelivering\s+to\b/i);
  if (deliveryStart < 0) return false;
  const deliveryText = normalizedText(bodyText.slice(deliveryStart, deliveryStart + 600));
  const visibleHeading = normalizedText(document.querySelector("#deliver-to-customer-text")?.textContent || "");
  if (visibleHeading && visibleHeading !== wanted) return false;
  if (!visibleHeading && !checkoutDeliveryRecipientMatches(name)) return false;
  return warehouseAddressTokens().some((token) => deliveryText.includes(token));
}

function checkoutDeliveryRecipientText() {
  const direct = document.querySelector("#deliver-to-customer-text");
  const directText = (direct?.innerText || direct?.textContent || "").replace(/\s+/g, " ").trim();
  if (direct && visible(direct) && directText) {
    return directText.replace(/^delivering\s+to\s+/i, "").trim();
  }
  const candidates = [
    ...document.querySelectorAll(
      [
        "#change-delivery-link",
        "a[aria-label*='Delivering to' i]",
        "a[aria-label*='delivery address' i]",
        "a[href*='shipaddressselect']",
        "a[href*='ChangeDelivery']",
      ].join(", "),
    ),
    document.querySelector("#deliver-to-address-text")?.closest?.(".a-row, .a-section, a, div"),
    ...document.querySelectorAll("h2, [id*='deliver-to'][id*='customer'], a[aria-label='Change delivery address'], a[href*='/checkout/'][href*='/address']"),
  ].filter(Boolean);
  for (const element of candidates) {
    if (!visible(element)) continue;
    const nestedHeading = element.querySelector?.("h1, h2, h3, [id*='deliver-to-customer']");
    const nestedHeadingText = (nestedHeading?.innerText || nestedHeading?.textContent || "").replace(/\s+/g, " ").trim();
    if (nestedHeadingText) {
      return nestedHeadingText.replace(/^delivering\s+to\s+/i, "").trim();
    }
    // Read each source independently. Concatenating innerText, textContent and
    // aria-label repeats the same recipient on Amazon Business checkout and
    // makes a correct exact match look unsafe.
    const variants = [...new Set([
      element.innerText,
      element.textContent,
      element.getAttribute?.("aria-label"),
      element.getAttribute?.("title"),
    ].filter(Boolean).map((value) => String(value).replace(/\s+/g, " ").trim()))];
    for (const text of variants) {
      if (/^delivering\s+to\s+/i.test(text)) {
        const recipient = text.replace(/^delivering\s+to\s+/i, "").trim();
        const beforeAddress = recipient.match(/^(.+?)(?:\s+\d{3,}|\s+Edit delivery preferences|\s+Deliver to multiple addresses|\s+Change delivery address|$)/i);
        if (beforeAddress?.[1]) return beforeAddress[1].trim();
      }
      const embedded = text.match(/\bDelivering\s+to\s+(.+?)(?:\s+\d{3,}|\s+Edit delivery preferences|\s+Deliver to multiple addresses|\s+Change delivery address|$)/i);
      if (embedded?.[1]) return embedded[1].trim();
    }
  }
  const bodyText = (document.body?.innerText || document.body?.textContent || "").replace(/\s+/g, " ").trim();
  const bodyMatch = bodyText.match(/\bDelivering\s+to\s+(.+?)(?:\s+\d{3,}|\s+Edit delivery preferences|\s+Deliver to multiple addresses|\s+Change delivery address|$)/i);
  if (bodyMatch?.[1]) return bodyMatch[1].trim();
  return "";
}

function checkoutDeliveryRecipientMatches(name) {
  const deliveredTo = normalizedText(checkoutDeliveryRecipientText());
  const wanted = normalizedText(name);
  return Boolean(deliveredTo && wanted && deliveredTo === wanted);
}

function checkoutRecipientConfirmed(name) {
  if (checkoutPageShowsExpectedDelivery(name)) return true;
  return checkoutDeliveryRecipientMatches(name) && checkoutShowsWarehouseAddress();
}

async function markCheckoutRecipientConfirmed(activeJob, checkoutRecipient, message = "Verified delivery address. Continuing checkout.") {
  activeJob.stage = "checkout";
  activeJob.editAddressClickedAt = null;
  activeJob.addressEditedRecipient = checkoutRecipient;
  activeJob.addressEditedAt = Date.now();
  activeJob.addressVerifiedRecipient = checkoutRecipient;
  activeJob.addressVerifiedAt = Date.now();
  activeJob.addressVerifyAttempts = 0;
  await setActiveJob(activeJob);
  showPanel("Nutricity checkout", message, null, null);
  return true;
}

function findChangeDeliveryAddressButton() {
  return [...document.querySelectorAll(
    [
      "a[data-csa-c-slot-id='checkout-change-shipaddressselect']",
      "a[data-topage='shipaddressselect']",
      "a[aria-label='Change delivery address']",
      "a#change-delivery-link",
      "a.expand-panel-button",
      "a",
      "button",
    ].join(", "),
  )].find((element) => {
    const href = String(element.getAttribute?.("href") || element.href || "").toLowerCase();
    const text = normalizedText(element.getAttribute?.("aria-label") || element.innerText || element.textContent);
    return visible(element) && !element.disabled && (
      element.getAttribute?.("data-csa-c-slot-id") === "checkout-change-shipaddressselect" ||
      element.getAttribute?.("data-topage") === "shipaddressselect" ||
      (href.includes("/checkout/") && href.includes("/address") && (
        href.includes("changedelivery") ||
        href.includes("shipaddressselect") ||
        href.includes("editdeliveryaddress")
      )) ||
      text === "change delivery address" ||
      text === "change" && href.includes("/checkout/") && href.includes("/address")
    );
  });
}

function findAddressNameInput() {
  return [...document.querySelectorAll(
    [
      "#address-ui-widgets-enterAddressFullName",
      "input[name='address-ui-widgets-enterAddressFullName']",
      "input[aria-label='Full name']",
      "input[aria-label='Full Name']",
      "input[placeholder='Full name']",
      "input[placeholder='Full Name']",
      "input[autocomplete='name']",
      "input[name*='FullName']",
      "input[id*='FullName']",
      "input[name*='fullName']",
      "input[id*='fullName']",
      "input[name*='Name']",
      "input[id*='Name']",
    ].join(", "),
  )].find((element) => visible(element) && !element.disabled);
}

function findAddressField(selectors) {
  return [...document.querySelectorAll(selectors.join(", "))].find((element) => visible(element) && !element.disabled);
}

async function fillAddressInput(selectors, value, required = true) {
  const input = await waitUntil(() => findAddressField(selectors), 8000, 300);
  if (!input) return !required;
  input.scrollIntoView({ block: "center", behavior: "smooth" });
  await sleep(200);
  await setInputValue(input, value);
  await sleep(150);
  input.blur();
  return String(input.value || "").trim() === String(value || "").trim();
}

async function setAddressSelect(selectors, value, required = true) {
  const select = await waitUntil(() => findAddressField(selectors), 8000, 300);
  if (!select) return !required;
  select.scrollIntoView({ block: "center", behavior: "smooth" });
  await sleep(200);
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set;
  setter ? setter.call(select, value) : (select.value = value);
  select.dispatchEvent(new Event("input", { bubbles: true }));
  select.dispatchEvent(new Event("change", { bubbles: true }));
  await sleep(250);
  return select.value === value;
}

async function fillNewDeliveryAddress(checkoutRecipient) {
  const address = DEFAULT_NEW_DELIVERY_ADDRESS;
  const steps = [
    () => setAddressSelect([
      "#address-ui-widgets-countryCode-dropdown-nativeId",
      "select[name='address-ui-widgets-countryCode']",
    ], address.countryCode),
    () => fillAddressInput([
      "#address-ui-widgets-enterAddressFullName",
      "input[name='address-ui-widgets-enterAddressFullName']",
      "input[autocomplete='name']",
    ], checkoutRecipient),
    () => fillAddressInput([
      "#address-ui-widgets-enterAddressPhoneNumber",
      "input[name='address-ui-widgets-enterAddressPhoneNumber']",
      "input[autocomplete='tel']",
    ], address.phoneNumber),
    () => fillAddressInput([
      "#address-ui-widgets-enterAddressLine1",
      "input[name='address-ui-widgets-enterAddressLine1']",
    ], address.addressLine1),
    () => fillAddressInput([
      "#address-ui-widgets-enterAddressLine2",
      "input[name='address-ui-widgets-enterAddressLine2']",
    ], address.addressLine2, false),
    () => fillAddressInput([
      "#address-ui-widgets-enterAddressCity",
      "input[name='address-ui-widgets-enterAddressCity']",
    ], address.city),
    () => setAddressSelect([
      "#address-ui-widgets-enterAddressStateOrRegion-dropdown-nativeId",
      "select[name='address-ui-widgets-enterAddressStateOrRegion']",
    ], address.stateOrRegion),
    () => fillAddressInput([
      "#address-ui-widgets-enterAddressPostalCode",
      "input[name='address-ui-widgets-enterAddressPostalCode']",
    ], address.postalCode),
  ];
  for (const step of steps) {
    if (!await step()) return false;
  }
  return true;
}

async function waitUntil(check, timeoutMs = 12000, intervalMs = 300) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    await waitIfPaused();
    const result = check();
    if (result) return result;
    await sleep(intervalMs);
  }
  return null;
}

function findUseAddressButton() {
  const addressEditorOpen = Boolean(findAddressNameInput());
  const selectors = [
    "input[data-csa-c-slot-id='address-ui-widgets-continue-edit-address-btn-bottom'][data-testid='bottom-continue-button']",
    "input[data-csa-c-slot-id*='continue-edit-address']",
    "button[data-csa-c-slot-id*='continue-edit-address']",
    "input[data-testid='bottom-continue-button']",
    "button[data-testid='bottom-continue-button']",
    "input[data-testid*='continue-button']",
    "button[data-testid*='continue-button']",
    "input[aria-labelledby='checkout-primary-continue-button-id-announce']",
    "#checkout-primary-continue-button-id input",
  ];
  if (addressEditorOpen) {
    selectors.push(
      ".a-popover input[type='submit']",
      ".a-popover button[type='submit']",
      ".a-modal-scroller input[type='submit']",
      ".a-modal-scroller button[type='submit']",
    );
  }
  const direct = [...document.querySelectorAll(selectors.join(", "))].find((element) => visible(element) && !element.disabled);
  if (direct) return direct;

  return [...document.querySelectorAll("input[type='submit'], button, span.a-button")].find((element) => {
    const text = normalizedText(element.value || element.innerText || element.textContent);
    return visible(element) && [
      "use this address",
      "save address",
      "continue to checkout",
      "continue",
    ].some((needle) => text.includes(needle));
  });
}

async function clickUseAddressButton(useAddress) {
  const target = document.querySelector("#checkout-primary-continue-button-id-announce")
    || useAddress?.closest?.("[data-action='checkout-continue-button-click-action']")?.querySelector?.("#checkout-primary-continue-button-id-announce, .a-button-text")
    || useAddress?.closest?.("#checkout-primary-continue-button-id")?.querySelector?.("#checkout-primary-continue-button-id-announce, .a-button-text")
    || useAddress?.closest?.("span.a-button")?.querySelector?.(".a-button-text")
    || useAddress;
  return clickElement(target, "Use this address button");
}

function findDeliverToThisAddressButton() {
  if (findAddressNameInput()) return null;
  const direct = [...document.querySelectorAll(
    "input[data-testid='ab-select-address-continue-button-bottom'], #ab-select-address-continue-button-bottom input, input[aria-labelledby='ab-select-address-continue-button-bottom-announce']",
  )].find((element) => visible(element) && !element.disabled);
  return direct || findButtonByText(["deliver to this address", "use this address"]);
}

function checkoutAddressSelectionPageOpen() {
  return !findAddressNameInput()
    && !findPlaceOrderButton()
    && checkoutAddressRows().length > 0
    && Boolean(findDeliverToThisAddressButton());
}

// Amazon sometimes saves an edited address immediately and navigates straight
// to the review (/spc) page. In that case the old address form's button is
// gone because the save succeeded; treating it as a missing button pauses an
// otherwise valid fulfilment.
function checkoutAdvancedAfterAddressSave() {
  if (findAddressNameInput()) return false;
  const path = String(location.pathname || "").toLowerCase();
  return path.includes("/spc")
    || Boolean(findPaymentSelection())
    || Boolean(findPaymentRadio())
    || Boolean(findPlaceOrderButton());
}

function cardPreferenceList(value) {
  return String(value || "")
    .split(/[\s,;|]+/)
    .map((item) => item.replace(/\D/g, "").slice(-4))
    .filter((item) => item.length === 4);
}

function paymentRowForRadio(radio) {
  if (!radio) return null;
  const label = radio.closest?.("label");
  const labelText = normalizedText(label?.innerText || label?.textContent || "");
  if (labelText.match(/\bending\s+in\s+\d{4}\b/i)) return label;

  let fallback = null;
  let node = radio.parentElement;
  for (let depth = 0; node && depth < 14; depth += 1, node = node.parentElement) {
    const text = normalizedText(node.innerText || node.textContent || "");
    const endings = text.match(/\bending\s+in\s+\d{4}\b/gi) || [];
    if (endings.length === 1) return node;
    if (!fallback && endings.length > 0) fallback = node;
  }
  return fallback || label || radio.parentElement || radio;
}

function paymentRowVisible(row) {
  if (!row) return false;
  if (visible(row)) return true;
  return [...row.querySelectorAll?.("*") || []].some((element) => visible(element));
}

function paymentRadioVisible(radio) {
  if (!radio || radio.disabled) return false;
  if (visible(radio)) return true;
  const row = paymentRowForRadio(radio);
  if (!row) return false;
  const text = normalizedText(row.innerText || row.textContent || "");
  return Boolean(text.match(/\bending\s+in\s+\d{4}\b/i) && paymentRowVisible(row));
}

function findPaymentRadio() {
  return [...document.querySelectorAll("input[type='radio'][name='ppw-instrumentRowSelection']")]
    .find(paymentRadioVisible);
}

function paymentRowText(radio) {
  const label = radio?.closest?.("label");
  const labelledText = label?.innerText || label?.textContent || "";
  const root = paymentRowForRadio(radio);
  return [labelledText, root?.innerText || root?.textContent || "", radio?.value || ""].join(" ");
}

function paymentRadioLocalText(radio) {
  if (!radio) return "";
  const pieces = [
    radio.getAttribute?.("aria-label") || "",
    radio.getAttribute?.("title") || "",
    radio.value || "",
  ];
  const label = radio.closest?.("label");
  if (label) pieces.push(label.innerText || label.textContent || "");
  let node = radio.parentElement;
  for (let depth = 0; node && depth < 5; depth += 1, node = node.parentElement) {
    const text = normalizedText(node.innerText || node.textContent || "");
    const endings = text.match(/\bending\s+in\s+\d{4}\b/gi) || [];
    if (endings.length <= 1) pieces.push(text);
    if (endings.length === 1) break;
  }
  return normalizedText(pieces.join(" "));
}

function paymentTextDigits(text) {
  const value = String(text || "");
  const directMatch = value.match(/(?:ending\s+in|card|visa|mastercard|amex|american\s+express|discover)[^\d]{0,40}(\d{4})\b/i);
  return directMatch?.[1] || "";
}

function cardDigitsForPaymentRadio(radio) {
  const localDigits = paymentTextDigits(paymentRadioLocalText(radio));
  if (localDigits) return localDigits;

  const root = paymentRowForRadio(radio);
  const dataNumber = root?.querySelector?.("[data-number]")?.getAttribute("data-number");
  const text = paymentRowText(radio);
  const endingMatch = text.match(/ending\s+in\s+(\d{4})/i);
  return (dataNumber || endingMatch?.[1] || "").replace(/\D/g, "").slice(-4);
}

function findCheckoutPaymentPanel() {
  return [...document.querySelectorAll(
    [
      "#checkout-paymentOptionPanel",
      "#selected-payment-methods-list-container",
      "#paymentOptionList",
      "#payment-information",
      "[id*='paymentOption']",
      "[id*='selected-payment']",
      "[data-csa-c-slot-id*='payselect']",
      "[data-testid*='payment']",
    ].join(", "),
  )].find((element) => visible(element));
}

function paymentSummaryText(element) {
  return (element?.innerText || element?.textContent || "")
    .replace(/\s+/g, " ")
    .trim();
}

function paymentSummaryLooksUseful(text = "") {
  return text.length <= 350 && /paying\s+with|ending\s+in\s+\d{4}|(?:visa|mastercard|american express|amex|discover|card)[^\d]{0,80}\d{4}/i.test(text);
}

function checkoutPaymentSummaryCandidates() {
  const roots = [
    ...document.querySelectorAll([
      "#selected-payment-methods-list-container",
      "#payment-information",
      "#checkout-paymentOptionPanel",
      "#paymentOptionList",
      "[id*='selected-payment' i]",
      "[id*='payment-option' i]",
      "[id*='paymentOption' i]",
      "[id*='payment' i]",
      "[class*='payment' i]",
      "[data-testid*='payment' i]",
      "[data-csa-c-slot-id*='pay' i]",
      "a[href*='/checkout/'][href*='/pay']",
    ].join(", ")),
  ].filter((element) => element && visible(element) && !element.closest?.("#nutricity-panel, .a-popover, .a-popover-preload"));
  const candidates = [];
  const seen = new Set();
  for (const root of roots.slice(0, 40)) {
    const nodes = [
      root,
      ...root.querySelectorAll?.("h2, h3, a, span, div, p") || [],
    ];
    for (const node of nodes.slice(0, 80)) {
      if (!node || seen.has(node) || !visible(node) || node.closest?.("#nutricity-panel, .a-popover, .a-popover-preload")) continue;
      seen.add(node);
      const text = paymentSummaryText(node);
      if (paymentSummaryLooksUseful(text)) candidates.push(text);
    }
  }
  return candidates.sort((left, right) => left.length - right.length);
}

function checkoutSelectedPaymentText() {
  const scopedPaymentText = checkoutPaymentSummaryCandidates()[0];
  if (scopedPaymentText) return scopedPaymentText;

  const heading = document.querySelector(
    [
      "#selected-payment-methods-list-container h2",
      "#payment-option-text-default",
      "[id^='payment-option-text'][data-testid]",
      ".selected-payment-method-no-art-description-heading",
    ].join(", "),
  );
  if (heading && visible(heading)) {
    const text = paymentSummaryText(heading);
    if (paymentSummaryLooksUseful(text)) return text;
  }

  const panel = findCheckoutPaymentPanel();
  if (panel) {
    const text = paymentSummaryText(panel);
    if (paymentSummaryLooksUseful(text)) return text;
  }
  return "";
}

function checkoutSelectedCardDigits() {
  const text = checkoutSelectedPaymentText();
  const preferredPattern = text.match(/(?:ending\s+in|visa|mastercard|american\s+express|amex|discover|card|paying\s+with)[^\d]{0,80}(\d{4})\b/i);
  if (preferredPattern) return preferredPattern[1];
  return "";
}

function checkoutPaymentConfirmed(preferences = []) {
  const placeOrder = findPlaceOrderButton();
  const text = checkoutSelectedPaymentText();
  const selectedDigits = checkoutSelectedCardDigits();
  if (preferences.length) return Boolean(selectedDigits && preferences.includes(selectedDigits));
  if (placeOrder && !placeOrder.disabled) return true;
  if (!text) return false;
  return /(?:payment method|paying with|ending in|visa|mastercard|american express|amex|discover|card)/i.test(text);
}

function checkoutPaymentProgress(preferences = []) {
  const selectedDigits = checkoutSelectedCardDigits();
  const hasPlaceOrderButton = Boolean(findPlaceOrderButton());
  const hasPaymentRadio = Boolean(findPaymentRadio());
  return {
    selectedDigits,
    hasPlaceOrderButton,
    hasPaymentRadio,
    confirmed: preferences.length
      ? Boolean(selectedDigits && preferences.includes(selectedDigits))
      : Boolean(hasPlaceOrderButton || checkoutPaymentConfirmed(preferences)),
  };
}

async function waitForCheckoutPaymentProgress(preferences = [], timeout = 4500, options = {}) {
  const stopOnTransition = options.stopOnTransition === true;
  return waitUntil(() => {
    const progress = checkoutPaymentProgress(preferences);
    if (progress.confirmed) return progress;
    if (progress.hasPlaceOrderButton) return progress;
    if (stopOnTransition && !progress.hasPaymentRadio) return progress;
    return false;
  }, timeout, 150);
}

async function waitForPreferredCheckoutPayment(preferences = [], timeout = 8000) {
  return waitUntil(() => {
    const selectedDigits = checkoutSelectedCardDigits();
    if (selectedDigits && (!preferences.length || preferences.includes(selectedDigits))) return selectedDigits;
    if (!preferences.length && checkoutPaymentConfirmed(preferences)) return true;
    return false;
  }, timeout, 200);
}

function findPaymentRadioForPreferences(preferences = []) {
  const allRadios = [...document.querySelectorAll("input[type='radio'][name='ppw-instrumentRowSelection']")]
    .filter((radio) => radio && !radio.disabled);
  const radios = allRadios.filter(paymentRadioVisible);
  const searchRadios = radios.length ? radios : allRadios;
  if (!searchRadios.length) return null;
  for (const preferred of preferences) {
    const radio = searchRadios.find((candidate) => {
      const localText = paymentRadioLocalText(candidate);
      return cardDigitsForPaymentRadio(candidate) === preferred
        || localText.includes(`ending in ${preferred}`)
        || new RegExp(`\\b${preferred}\\b`).test(localText);
    });
    if (radio) return radio;
  }
  return radios.find((radio) => radio.checked) || radios[0];
}

function paymentRadioIsSelected(radio) {
  if (!radio) return false;
  if (radio.checked) return true;
  const row = paymentRowForRadio(radio);
  const text = normalizedText(row?.innerText || row?.textContent || "");
  return /\bselected\b/i.test(text) && /\bending\s+in\s+\d{4}\b/i.test(text);
}

function paymentRadioForDigits(digits) {
  if (!digits) return null;
  return [...document.querySelectorAll("input[type='radio'][name='ppw-instrumentRowSelection']")]
    .find((radio) => !radio.disabled && cardDigitsForPaymentRadio(radio) === digits) || null;
}

async function clickPaymentRadio(radio) {
  const row = paymentRowForRadio(radio);
  const preferredDigits = cardDigitsForPaymentRadio(radio);
  const exactLabel = radio.id
    ? document.querySelector(`label[for='${CSS.escape(radio.id)}']`)
    : null;
  const textTargets = preferredDigits
    ? [...row?.querySelectorAll?.("label, [role='radio'], .pmts-instrument-box, .a-radio") || []].filter((element) => {
      const text = normalizedText(element.innerText || element.textContent || element.getAttribute?.("aria-label") || "");
      return text.includes(preferredDigits) || text.match(new RegExp(`ending\\s+in\\s+${preferredDigits}`, "i"));
    })
    : [];
  const targets = [
    exactLabel,
    radio,
    radio.closest?.("label"),
    ...textTargets,
  ].filter(Boolean);

  for (const target of targets) {
    try {
      await clickElement(target, "Payment method row", { preClickDelayMs: 80, delayMs: 120 });
      const selected = await waitUntil(() => {
        const current = paymentRadioForDigits(preferredDigits) || radio;
        return paymentRadioIsSelected(current) ? current : false;
      }, 900, 100);
      if (selected) return true;
    } catch (err) {
      // Try the next candidate; Amazon often hides the real radio and binds the row instead.
    }
  }
  const current = paymentRadioForDigits(preferredDigits) || radio;
  return paymentRadioIsSelected(current);
}

function nativePaymentContinueControl(element) {
  if (!element) return null;
  if (element.matches?.("button, input[type='submit'], input[type='button']")) return element;
  return element.querySelector?.(
    "button, input[type='submit'], input[type='button'], input[data-csa-c-slot-id*='continue-payselect']",
  ) || null;
}

function visiblePaymentContinueButtons() {
  const candidates = [...document.querySelectorAll(
    [
      "input[data-csa-c-slot-id*='continue-payselect']",
      "button[data-csa-c-slot-id*='continue-payselect']",
      "input[data-testid='bottom-continue-button'][data-csa-c-slot-id*='payselect']",
      "input[data-testid='secondary-continue-button'][data-csa-c-slot-id*='payselect']",
      "button[data-testid='bottom-continue-button'][data-csa-c-slot-id*='payselect']",
      "button[data-testid='secondary-continue-button'][data-csa-c-slot-id*='payselect']",
      "input[name='ppw-widgetEvent:SetPaymentPlanSelectContinueEvent']",
      "input[name='ppw-widgetEvent:SetPaymentPlanContinueEvent']",
      "input[aria-labelledby*='continue']",
      "span.a-button",
      "button",
      "input[type='submit']",
      "input[type='button']",
    ].join(", "),
  )];
  return [...new Set(candidates.map(nativePaymentContinueControl).filter(Boolean))]
    .filter((element) => {
      if (!visible(element) || element.disabled) return false;
      const text = normalizedText(element.value || element.innerText || element.textContent || element.getAttribute?.("aria-label") || "");
      const slot = normalizedText(element.getAttribute?.("data-csa-c-slot-id") || "");
      const name = normalizedText(element.getAttribute?.("name") || "");
      return text.includes("use this payment method") ||
        text.includes("use this card") ||
        text === "continue" ||
        text.includes("continue") ||
        slot.includes("continue-payselect") ||
        name.includes("setpaymentplanselectcontinueevent") ||
        name.includes("setpaymentplancontinueevent");
    });
}

function findPaymentSelection(preferences = []) {
  const radio = findPaymentRadioForPreferences(preferences);
  if (!radio) return null;
  // Amazon's native pay-select submit input can be fully clickable while its
  // value and text are both empty. visiblePaymentContinueButtons already
  // validates its continue-payselect slot, so do not discard it here based on
  // a second text-only test.
  const continueButton = visiblePaymentContinueButtons()[0];
  if (continueButton) return { radio, continueButton };
  return null;
}

function alternatePaymentContinueButtons(primary = null) {
  return visiblePaymentContinueButtons().filter((element) => element !== primary);
}

function findChangePaymentButton() {
  return [...document.querySelectorAll(
    [
      "a[data-csa-c-slot-id='checkout-change-payselect']",
      "a[data-topage='payselect']",
      "a[href*='/checkout/'][href*='/pay']",
      "a[href*='ref_=chk_spc_chg_payselect']",
      "button",
      "a",
    ].join(", "),
  )].find((element) => {
    const href = String(element.getAttribute?.("href") || element.href || "").toLowerCase();
    const text = normalizedText(element.getAttribute?.("aria-label") || element.innerText || element.textContent);
    return visible(element) && !element.disabled && (
      element.getAttribute?.("data-csa-c-slot-id") === "checkout-change-payselect" ||
      element.getAttribute?.("data-topage") === "payselect" ||
      href.includes("/pay") && href.includes("checkout") ||
      text.includes("change payment method")
    );
  });
}

function findAddNewDeliveryAddressLink() {
  return [...document.querySelectorAll(
    [
      "#add-new-address-popover-link",
      "a[data-csa-c-slot-id='add-new-address-non-mobile-tango-sasp']",
      "a",
      "button",
      "span.a-button",
    ].join(", "),
  )].find((element) => {
    const text = normalizedText(element.getAttribute?.("aria-label") || element.innerText || element.textContent);
    return visible(element) && !element.disabled && text.includes("add a new delivery address");
  });
}

function findPlaceOrderButton() {
  const nativeSelectors = [
    "input#placeOrder",
    "input[name='placeYourOrder1']",
    "input[data-testid='SPC_selectPlaceOrder']",
    "input[data-csa-c-slot-id='checkout-place-your-order-button']",
    "input.place-your-order-button",
    "input[title='Place your order']",
    "input[value='Place your order']",
  ];
  // querySelectorAll with one combined selector returns document order, not
  // selector priority. Amazon wraps the real submit input in a span.a-button,
  // so the old combined lookup could return and click that inert wrapper while
  // still marking the order submitted. Always resolve a native form control.
  for (const selector of nativeSelectors) {
    const control = [...document.querySelectorAll(selector)]
      .find((element) => visible(element) && !element.disabled);
    if (control) return control;
  }

  const textButtons = [...document.querySelectorAll("button")].filter((element) => {
    const labelledBy = element.getAttribute?.("aria-labelledby");
    const labelText = labelledBy ? document.getElementById(labelledBy)?.textContent : "";
    const text = normalizedText(element.value || element.title || element.innerText || element.textContent || labelText);
    return visible(element) && !element.disabled && text.includes("place your order");
  });
  if (textButtons.length) return textButtons[0];

  for (const wrapper of document.querySelectorAll("span.a-button")) {
    const text = normalizedText(wrapper.innerText || wrapper.textContent);
    if (!visible(wrapper) || !text.includes("place your order")) continue;
    const nested = [...wrapper.querySelectorAll("input[type='submit'], input[type='button'], button")]
      .find((element) => visible(element) && !element.disabled);
    if (nested) return nested;
  }
  return null;
}

function isNativePlaceOrderControl(element) {
  return Boolean(element?.matches?.("input[type='submit'], input[type='button'], button"));
}

function findSnsPaymentConfirmationCheckbox() {
  return document.querySelector(
    "input#SnsPaymentConfirmationImb[name='SnsPaymentConfirmationImb'], [data-client-component='PrimaryActionBlockerCheckbox'] input[name='SnsPaymentConfirmationImb']",
  );
}

function snsPaymentConfirmationIsBlocking() {
  const checkbox = findSnsPaymentConfirmationCheckbox();
  if (!checkbox || checkbox.checked) return false;
  const blocker = checkbox.closest?.("[data-client-component='PrimaryActionBlockerCheckbox'], [data-blocker-message], .a-checkbox");
  const text = normalizedText([
    blocker?.getAttribute?.("data-blocker-message"),
    blocker?.innerText,
    blocker?.textContent,
  ].filter(Boolean).join(" "));
  return text.includes("subscribe and save payment method")
    || text.includes("use this payment method for your subscribe & save subscription")
    || text.includes("use this payment method for your subscribe and save subscription");
}

async function ensureSnsPaymentConfirmation(activeJob) {
  const checkbox = findSnsPaymentConfirmationCheckbox();
  if (!checkbox || checkbox.checked) return true;
  if (!snsPaymentConfirmationIsBlocking()) return true;

  showPanel("Nutricity checkout", "Accepting the selected payment method for Subscribe & Save.", null, null);
  // Amazon's checkout label can be replaced while its blocker hydrates. Click
  // the native checkbox first, then re-query and use the label only as a
  // fallback so a detached label cannot leave the purchase permanently paused.
  await clickElement(checkbox, "Subscribe & Save payment confirmation checkbox");
  let checked = await waitUntil(() => findSnsPaymentConfirmationCheckbox()?.checked === true, 1800, 100);
  if (!checked) {
    const freshCheckbox = findSnsPaymentConfirmationCheckbox();
    const label = freshCheckbox?.id
      ? document.querySelector(`label[for='${CSS.escape(freshCheckbox.id)}']`)
      : freshCheckbox?.closest?.("label");
    if (label) {
      await clickElement(label, "Subscribe & Save payment confirmation label");
      checked = await waitUntil(() => findSnsPaymentConfirmationCheckbox()?.checked === true, 3000, 100);
    }
  }
  if (!checked) {
    await pauseForManualCheckout(activeJob, "Amazon requires confirmation of the Subscribe & Save payment method, but the checkbox did not stay selected.");
    return false;
  }
  activeJob.snsPaymentConfirmationAcceptedAt = Date.now();
  await setActiveJob(activeJob);
  return true;
}

async function recoverBlockedSnsSubmit(activeJob) {
  if (
    !activeJob?.placeOrderClickStartedAt
    || !submittedStage(activeJob)
    || !snsPaymentConfirmationIsBlocking()
  ) return false;

  const checkoutRecipient = recipientName(activeJob);
  if (!checkoutRecipientConfirmed(checkoutRecipient) || !checkoutShowsWarehouseAddress()) {
    await pauseForManualCheckout(activeJob, "Subscribe & Save confirmation appeared after submit, but the final delivery recipient or warehouse address is no longer verified.", "complete_pending");
    return true;
  }
  const extensionState = await getExtensionState();
  const cardPreferences = cardPreferenceList(extensionState.cardLast4Preference);
  if (!checkoutPaymentConfirmed(cardPreferences)) {
    await pauseForManualCheckout(activeJob, "Subscribe & Save confirmation appeared after submit, but the checkout payment method is no longer verified.", "complete_pending");
    return true;
  }
  if (!await ensureCheckoutOnlyExpectedUnits(activeJob)) return true;
  if (!await ensureSubscribeCheckoutQuantity(activeJob)) return true;
  if (!await checkoutDeliveryWindowIsAllowed(activeJob)) return true;
  if (!await ensureSnsPaymentConfirmation(activeJob)) return true;

  const placeOrder = findPlaceOrderButton();
  if (!placeOrder || placeOrder.disabled) {
    await pauseForManualCheckout(activeJob, "Subscribe & Save payment was confirmed, but Amazon no longer shows an enabled Place your order button.", "complete_pending");
    return true;
  }
  // protectBeforeAmazonSubmit already succeeded before the blocked click. Keep
  // that same protected job identity and retry the Amazon control only once;
  // calling protection again would intentionally look like a duplicate submit.
  showPanel("Final step", "Subscribe & Save payment confirmed. Retrying the protected Place Order click once.", null, null);
  activeJob.snsBlockedSubmitRecoveredAt = Date.now();
  activeJob.placeOrderClickStartedAt = Date.now();
  activeJob.stage = "complete_pending";
  await setActiveJob(activeJob);
  await clickElement(placeOrder, "Place your order after Subscribe & Save payment confirmation");
  return true;
}

function isOnePercentDeliveryRewardText(value = "") {
  const text = normalizedText(value);
  if (!text.includes("1%")) return false;
  const rewardWording = /\b(?:back|reward|rewards|discount|saving|savings|earn|earns|earning|additional|extra)\b/i.test(text);
  const deliveryWording = /\b(?:arriv|delivery|deliver|shipping|shipment|amazon day|nominated day)\w*/i.test(text);
  return rewardWording && deliveryWording;
}

function deliveryRadioContext(radio) {
  if (!radio) return { radio: null, control: null, container: null, text: "", optionCount: 0 };
  const explicitLabel = radio.id
    ? document.querySelector(`label[for='${CSS.escape(radio.id)}']`)
    : null;
  const labelledElements = String(radio.getAttribute("aria-labelledby") || "")
    .split(/\s+/)
    .map((id) => document.getElementById(id))
    .filter(Boolean);
  const closestLabel = radio.closest("label");
  const controlRow = radio.closest(".rcx-checkout-delivery-option-a-control-row, .a-radio, label");
  const matchedContainer = explicitLabel || closestLabel || controlRow || radio.parentElement;
  const localParts = [...new Set([
    ...labelledElements,
    explicitLabel,
    closestLabel,
    controlRow,
    matchedContainer,
  ].filter(Boolean))];
  const text = normalizedText(localParts.map((element) => element.innerText || element.textContent || "").join(" "));
  const localLabel = matchedContainer?.querySelector?.(".a-radio-label, label");
  const control = explicitLabel || closestLabel || localLabel || controlRow || radio;
  const sameGroup = radio.name
    ? [...document.querySelectorAll("input[type='radio']")].filter((candidate) => candidate.name === radio.name && !candidate.disabled)
    : [...(matchedContainer?.parentElement?.querySelectorAll?.("input[type='radio']") || [])].filter((candidate) => !candidate.disabled);
  return {
    radio,
    control,
    container: matchedContainer,
    text,
    optionCount: Math.max(1, sameGroup.length),
  };
}

function deliveryRadioSelectionMatches(context) {
  const expectedId = String(context?.radio?.id || "");
  const expectedName = String(context?.radio?.name || "");
  const expectedValue = String(context?.radio?.value || "");
  return [...document.querySelectorAll("input[type='radio']:checked")].some((radio) => (
    expectedId && radio.id === expectedId
  ) || (
    expectedName && expectedValue && radio.name === expectedName && String(radio.value || "") === expectedValue
  ));
}

function currentDeliveryRadio(context) {
  const id = String(context?.radio?.id || "");
  if (id) {
    const byId = document.getElementById(id);
    if (byId?.matches?.("input[type='radio']")) return byId;
  }
  const name = String(context?.radio?.name || "");
  const value = String(context?.radio?.value || "");
  return [...document.querySelectorAll("input[type='radio']")].find((radio) => (
    !radio.disabled && name && value && radio.name === name && String(radio.value || "") === value
  )) || null;
}

async function clickDeliveryRadioContext(context, label) {
  const nativeRadio = currentDeliveryRadio(context);
  if (nativeRadio && visible(nativeRadio)) {
    await clickElement(nativeRadio, label, { preClickDelayMs: 100, delayMs: 350 });
    if (await waitUntil(() => deliveryRadioSelectionMatches(context), 5000, 150)) return true;
  }

  const refreshed = currentDeliveryRadio(context);
  const fallbackContext = refreshed ? deliveryRadioContext(refreshed) : context;
  const fallback = fallbackContext?.control;
  if (fallback && fallback !== refreshed && visible(fallback)) {
    await clickElement(fallback, `${label} label`, { preClickDelayMs: 100, delayMs: 350 });
    if (await waitUntil(() => deliveryRadioSelectionMatches(context), 5000, 150)) return true;
  }
  return deliveryRadioSelectionMatches(context);
}

function checkoutOffersOnePercentDeliveryReward() {
  const text = normalizedText(document.body?.innerText || document.body?.textContent || "");
  return /(?:earn|earning|get|receive|additional|extra)[^.]{0,160}1%[^.]{0,160}(?:back|reward|rewards)/i.test(text)
    || /1%[^.]{0,160}(?:back|reward|rewards)[^.]{0,160}(?:delivery|shipping|shipment|amazon day)/i.test(text);
}

function radioLinkedToDeliveryReference(node) {
  const referenceId = String(node?.id || "").trim();
  if (!referenceId) return null;
  return [...document.querySelectorAll("input[type='radio']")].find((radio) => (
    !radio.disabled && String(radio.getAttribute("aria-labelledby") || "").split(/\s+/).includes(referenceId)
  )) || null;
}

function isAmazonDayDeliveryContext(context) {
  const radioId = String(context?.radio?.id || "").toLowerCase();
  const labelledBy = String(context?.radio?.getAttribute?.("aria-labelledby") || "").toLowerCase();
  const text = normalizedText(context?.text || "").toLowerCase();
  return /amazon day|nominated day/.test(text) || /nominated-day|amazon-day/.test(`${radioId} ${labelledBy}`);
}

function fridayRewardDeliveryOption() {
  const radios = [...document.querySelectorAll("input[type='radio']")].filter((radio) => !radio.disabled);
  for (const radio of radios) {
    const context = deliveryRadioContext(radio);
    if (isOnePercentDeliveryRewardText(context.text)) return context;
  }

  if (!checkoutOffersOnePercentDeliveryReward()) return null;
  const knownRewardNodes = [
    document.querySelector("#second-nominated-dayPromiseReferenceForRadioLabel"),
    document.querySelector("#second-nominated-dayReferenceForRadioLabel"),
    ...document.querySelectorAll("[id*='nominated-day' i], .delivery-promise-text, .delivery-option-text"),
  ].filter(Boolean);
  for (const node of knownRewardNodes) {
    const radio = radioLinkedToDeliveryReference(node);
    if (radio) return deliveryRadioContext(radio);
  }
  for (const radio of radios) {
    const context = deliveryRadioContext(radio);
    if (isAmazonDayDeliveryContext(context)) return context;
  }
  return null;
}

function rewardedLaterDeliverySelected() {
  const selectedRadio = [...document.querySelectorAll("input[type='radio']:checked")][0] || null;
  if (!selectedRadio) return false;
  const context = deliveryRadioContext(selectedRadio);
  return isOnePercentDeliveryRewardText(context.text)
    || checkoutOffersOnePercentDeliveryReward() && isAmazonDayDeliveryContext(context);
}

function selectedDeliveryRadioContext() {
  const selectedRadio = [...document.querySelectorAll("input[type='radio']:checked")]
    .find((radio) => !radio.disabled) || null;
  return selectedRadio ? deliveryRadioContext(selectedRadio) : null;
}

function deliveryTextCalendarDates(value = "", now = new Date()) {
  const text = normalizedText(value || "");
  const monthPattern = "Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?";
  return [...text.matchAll(new RegExp(`\\b(${monthPattern})\\.?\\s+(\\d{1,2})(?:,\\s*(\\d{4}))?`, "gi"))]
    .map((match) => {
      const year = Number(match[3] || now.getFullYear());
      const month = new Date(`${match[1]} 1, ${year}`).getMonth();
      const date = new Date(year, month, Number(match[2]));
      if (!match[3] && date < new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1)) {
        date.setFullYear(date.getFullYear() + 1);
      }
      return date;
    })
    .filter((date) => Number.isFinite(date.getTime()));
}

function deliveryTextIncludesWarehouseClosedDay(value = "") {
  const text = normalizedText(value || "");
  if (/\b(?:sat(?:urday)?|sun(?:day)?)\b/i.test(text)) return true;
  return deliveryTextCalendarDates(text).some((date) => [0, 6].includes(date.getDay()));
}

function deliveryTextNamesWarehouseOpenDay(value = "") {
  const text = normalizedText(value || "");
  if (/\b(?:mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?)\b/i.test(text)) return true;
  return deliveryTextCalendarDates(text).some((date) => date.getDay() >= 1 && date.getDay() <= 5);
}

function deliveryContextIncludesWarehouseClosedDay(context) {
  return deliveryTextIncludesWarehouseClosedDay(context?.text || "");
}

function deliveryContextHasSplitPromise(context) {
  const text = normalizedText(context?.text || "");
  const namedDays = new Set(
    (text.match(/\b(?:mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b/gi) || [])
      .map((value) => value.toLowerCase()),
  );
  const datedPromises = new Set(
    (text.match(/\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+\d{1,2}\b/gi) || [])
      .map((value) => value.toLowerCase().replace(/\s+/g, " ")),
  );
  return namedDays.size > 1 || datedPromises.size > 1;
}

function deliveryContextIsNotConsolidated(context) {
  return deliveryContextIncludesWarehouseClosedDay(context) || deliveryContextHasSplitPromise(context);
}

function deliveryContextNamesWarehouseOpenDay(context) {
  return deliveryTextNamesWarehouseOpenDay(context?.text || "");
}

function consolidatedWeekdayDeliveryOption() {
  const candidates = [...document.querySelectorAll("input[type='radio']")]
    .filter((radio) => !radio.disabled)
    .map(deliveryRadioContext)
    .filter((context) => (
      context.radio
      && context.control
      && !deliveryContextIsNotConsolidated(context)
      && deliveryContextNamesWarehouseOpenDay(context)
    ));
  return candidates.find(isAmazonDayDeliveryContext)
    || candidates.find((context) => isOnePercentDeliveryRewardText(context.text))
    || candidates[0]
    || null;
}

function consolidatedWeekdayDeliverySelected() {
  const selected = selectedDeliveryRadioContext();
  return Boolean(
    selected
    && !deliveryContextIsNotConsolidated(selected)
    && deliveryContextNamesWarehouseOpenDay(selected),
  );
}

function preferredAmazonDayWeekdayOption() {
  return [...document.querySelectorAll("input[type='radio']")]
    .filter((radio) => !radio.disabled)
    .map(deliveryRadioContext)
    .find((context) => (
      context.radio
      && context.control
      && isAmazonDayDeliveryContext(context)
      && !deliveryContextIsNotConsolidated(context)
      && deliveryContextNamesWarehouseOpenDay(context)
    )) || null;
}

function preferredAmazonDayWeekdaySelected() {
  const selected = selectedDeliveryRadioContext();
  return Boolean(
    selected
    && isAmazonDayDeliveryContext(selected)
    && !deliveryContextIsNotConsolidated(selected)
    && deliveryContextNamesWarehouseOpenDay(selected),
  );
}

const WAREHOUSE_DELIVERY_WEEKDAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"];
const WAREHOUSE_DELIVERY_WEEKENDS = ["SATURDAY", "SUNDAY"];
const WAREHOUSE_DELIVERY_START = "10:00";
const WAREHOUSE_DELIVERY_END = "17:00";

function visibleDeliveryPreferencesDialog() {
  const dialogs = [...document.querySelectorAll("[role='dialog'][aria-hidden='false'], .a-popover-modal[role='dialog']")]
    .filter((dialog) => visible(dialog) && /your delivery preferences|edit delivery instructions/i.test(normalizedText(dialog.textContent || "")));
  return [...dialogs].reverse().find((dialog) => dialog.querySelector("#deliveryTimesEditLink, [id^='MONDAYStartTime_']"))
    || dialogs.at(-1)
    || null;
}

function deliveryPreferencesSummaryIsWarehouseSchedule(dialog = visibleDeliveryPreferencesDialog()) {
  const edit = dialog?.querySelector("#deliveryTimesEditLink");
  const section = edit?.closest(".a-expander-inner, .a-expander-content") || edit?.parentElement;
  const text = normalizedText(section?.innerText || section?.textContent || "").toLowerCase();
  return /monday\s*-\s*friday/.test(text)
    && /10:00\s*am\s*-\s*0?5:00\s*pm/.test(text)
    && /saturday\s*-\s*sunday/.test(text)
    && /closed for deliveries/.test(text);
}

function warehouseDeliveryDayControl(dialog, day, kind) {
  return dialog?.querySelector(`[id^='${day}${kind}_']`) || null;
}

function warehouseDeliveryControlsMatch(dialog) {
  return WAREHOUSE_DELIVERY_WEEKDAYS.every((day) => (
    warehouseDeliveryDayControl(dialog, day, "StartTime")?.value === WAREHOUSE_DELIVERY_START
    && warehouseDeliveryDayControl(dialog, day, "EndTime")?.value === WAREHOUSE_DELIVERY_END
    && warehouseDeliveryDayControl(dialog, day, "ClosedCheckbox")?.checked === false
  )) && WAREHOUSE_DELIVERY_WEEKENDS.every((day) => (
    warehouseDeliveryDayControl(dialog, day, "ClosedCheckbox")?.checked === true
  ));
}

async function verifyWarehouseDeliveryControlsFromSummary(dialog = visibleDeliveryPreferencesDialog()) {
  const editDeliveryTimes = dialog?.querySelector("#deliveryTimesEditLink");
  if (!editDeliveryTimes) return false;
  await clickElement(editDeliveryTimes, "Verify saved delivery times", { delayMs: 250 });
  let currentDialog = await waitUntil(visibleDeliveryPreferencesDialog, 5000, 150);
  const expandDays = await waitUntil(
    () => visibleDeliveryPreferencesDialog()?.querySelector("#businessHoursExpandLink") || null,
    5000,
    150,
  );
  if (expandDays && visible(expandDays)) {
    await clickElement(expandDays, "Expand saved delivery days", { preClickDelayMs: 0, delayMs: 250 });
  }
  await waitUntil(
    () => warehouseDeliveryDayControl(visibleDeliveryPreferencesDialog(), "MONDAY", "StartTime"),
    5000,
    150,
  );
  currentDialog = visibleDeliveryPreferencesDialog();
  return Boolean(currentDialog && warehouseDeliveryControlsMatch(currentDialog));
}

function setWarehouseDeliverySelect(select, value) {
  if (!select || ![...select.options || []].some((option) => option.value === value)) return false;
  if (select.value === value) return true;
  select.value = value;
  select.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
  select.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  return select.value === value;
}

function setWarehouseDeliveryClosed(checkbox, closed) {
  if (!checkbox) return false;
  if (checkbox.checked !== closed) checkbox.click();
  return checkbox.checked === closed;
}

async function closeDeliveryPreferencesDialog(dialog = visibleDeliveryPreferencesDialog()) {
  const close = dialog?.querySelector("button[data-action='a-popover-close'], button.a-button-close");
  if (close) await clickElement(close, "Close delivery preferences", { preClickDelayMs: 0, delayMs: 200 });
}

async function pauseForDeliveryPreferences(activeJob, message, dialog = visibleDeliveryPreferencesDialog()) {
  await closeDeliveryPreferencesDialog(dialog);
  await pauseForManualCheckout(activeJob, message, "checkout");
  return false;
}

async function ensureWarehouseDeliveryPreferences(activeJob) {
  const editPreferences = await waitUntil(
    () => [...document.querySelectorAll("#edit-delivery-preferences-link, a")]
      .find((element) => visible(element) && normalizedText(element.textContent || "").toLowerCase() === "edit delivery preferences"),
    6000,
    250,
  );
  if (!editPreferences) {
    return pauseForDeliveryPreferences(
      activeJob,
      "Amazon checkout did not expose Edit delivery preferences. Fulfilment is paused so warehouse delivery hours cannot be skipped.",
    );
  }

  showPanel("Delivery preferences", "Verifying Monday-Friday 10:00 AM-5:00 PM and closing Saturday-Sunday.", null, null);
  await clickElement(editPreferences, "Edit delivery preferences", { delayMs: 300 });
  let dialog = await waitUntil(visibleDeliveryPreferencesDialog, 10000, 200);
  const editDeliveryTimes = await waitUntil(
    () => visibleDeliveryPreferencesDialog()?.querySelector("#deliveryTimesEditLink") || null,
    10000,
    200,
  );
  dialog = visibleDeliveryPreferencesDialog();
  if (!dialog || !editDeliveryTimes) {
    return pauseForDeliveryPreferences(activeJob, "Amazon opened delivery preferences but did not expose the Delivery Times edit control.", dialog);
  }
  const existingSummaryVerified = await waitUntil(
    () => deliveryPreferencesSummaryIsWarehouseSchedule(visibleDeliveryPreferencesDialog()),
    3000,
    150,
  );
  if (existingSummaryVerified) {
    await closeDeliveryPreferencesDialog(dialog);
    await sendDiagnostic("Verified checkout delivery preferences.", {
      group_key: activeJob?.job?.group_key || "",
      weekdays: "Monday-Friday 10:00-17:00",
      weekends: "closed",
      changed: false,
    });
    return true;
  }

  await clickElement(editDeliveryTimes, "Edit delivery times", { delayMs: 250 });
  dialog = await waitUntil(visibleDeliveryPreferencesDialog, 5000, 150);
  const expandDays = await waitUntil(
    () => visibleDeliveryPreferencesDialog()?.querySelector("#businessHoursExpandLink") || null,
    5000,
    150,
  );
  if (expandDays && visible(expandDays)) {
    await clickElement(expandDays, "Expand delivery days", { preClickDelayMs: 0, delayMs: 250 });
  }
  dialog = visibleDeliveryPreferencesDialog();
  const mondayStart = await waitUntil(
    () => warehouseDeliveryDayControl(visibleDeliveryPreferencesDialog(), "MONDAY", "StartTime"),
    5000,
    150,
  );
  dialog = visibleDeliveryPreferencesDialog();
  if (!dialog || !mondayStart) {
    return pauseForDeliveryPreferences(activeJob, "Amazon did not expose the expanded Monday-Sunday delivery-hour controls.", dialog);
  }

  let changed = false;
  let controlsReady = true;
  for (const day of WAREHOUSE_DELIVERY_WEEKDAYS) {
    const start = warehouseDeliveryDayControl(dialog, day, "StartTime");
    const end = warehouseDeliveryDayControl(dialog, day, "EndTime");
    const closed = warehouseDeliveryDayControl(dialog, day, "ClosedCheckbox");
    changed = changed || start?.value !== WAREHOUSE_DELIVERY_START || end?.value !== WAREHOUSE_DELIVERY_END || closed?.checked !== false;
    const openAccepted = setWarehouseDeliveryClosed(closed, false);
    const startAccepted = setWarehouseDeliverySelect(start, WAREHOUSE_DELIVERY_START);
    const endAccepted = setWarehouseDeliverySelect(end, WAREHOUSE_DELIVERY_END);
    controlsReady = openAccepted && startAccepted && endAccepted && controlsReady;
  }
  for (const day of WAREHOUSE_DELIVERY_WEEKENDS) {
    const closed = warehouseDeliveryDayControl(dialog, day, "ClosedCheckbox");
    changed = changed || closed?.checked !== true;
    const closedAccepted = setWarehouseDeliveryClosed(closed, true);
    controlsReady = closedAccepted && controlsReady;
  }
  await sleep(250);
  if (!controlsReady || !warehouseDeliveryControlsMatch(dialog)) {
    return pauseForDeliveryPreferences(activeJob, "Amazon did not accept the required weekday hours and weekend closures.", dialog);
  }

  const save = dialog.querySelector("span[id^='adpSubmitButton_'] input[type='submit'], input[type='submit'][aria-labelledby^='adpSubmitButton_']");
  if (!save || !visible(save)) {
    return pauseForDeliveryPreferences(activeJob, "Amazon delivery preferences did not expose a usable Save button.", dialog);
  }
  await clickElement(save, "Save delivery preferences", { preClickDelayMs: 100, delayMs: 800 });
  const closed = await waitUntil(() => !visibleDeliveryPreferencesDialog(), 10000, 200);
  if (!closed) {
    return pauseForDeliveryPreferences(activeJob, "Amazon did not confirm saving the delivery preferences.", visibleDeliveryPreferencesDialog());
  }

  const refreshedLink = await waitUntil(
    () => [...document.querySelectorAll("#edit-delivery-preferences-link, a")]
      .find((element) => visible(element) && normalizedText(element.textContent || "").toLowerCase() === "edit delivery preferences"),
    10000,
    250,
  );
  if (!refreshedLink) {
    return pauseForDeliveryPreferences(activeJob, "Amazon saved delivery preferences but the checkout did not return for verification.");
  }
  await clickElement(refreshedLink, "Recheck delivery preferences", { delayMs: 300 });
  dialog = await waitUntil(visibleDeliveryPreferencesDialog, 10000, 200);
  await waitUntil(() => visibleDeliveryPreferencesDialog()?.querySelector("#deliveryTimesEditLink") || null, 10000, 200);
  dialog = visibleDeliveryPreferencesDialog();
  // Amazon inserts the Edit link before it hydrates the compact Monday-Friday
  // summary. Give that summary time to settle instead of treating the
  // temporarily empty section as a failed save.
  let verified = Boolean(await waitUntil(
    () => deliveryPreferencesSummaryIsWarehouseSchedule(visibleDeliveryPreferencesDialog()),
    6000,
    200,
  ));
  if (!verified) {
    verified = await verifyWarehouseDeliveryControlsFromSummary(visibleDeliveryPreferencesDialog());
  }
  dialog = visibleDeliveryPreferencesDialog();
  await closeDeliveryPreferencesDialog(dialog);
  if (!verified) {
    return pauseForDeliveryPreferences(activeJob, "Amazon did not retain Monday-Friday 10:00 AM-5:00 PM with Saturday-Sunday closed.");
  }
  await sendDiagnostic("Saved and verified checkout delivery preferences.", {
    group_key: activeJob?.job?.group_key || "",
    weekdays: "Monday-Friday 10:00-17:00",
    weekends: "closed",
    changed,
  });
  return true;
}

async function ensurePreferredAmazonDayWeekdayDelivery(activeJob) {
  const option = preferredAmazonDayWeekdayOption();
  if (!option?.control || preferredAmazonDayWeekdaySelected()) return true;
  showPanel(
    "Consolidated weekday delivery",
    `Selecting Amazon's consolidated Monday-Friday delivery option: ${option.text}`,
    null,
    null,
  );
  await sendDiagnostic("Selecting the consolidated Amazon Day weekday delivery.", {
    selected_option_text: selectedDeliveryRadioContext()?.text || "",
    replacement_option_text: option.text,
  });
  await clickDeliveryRadioContext(option, "consolidated Amazon Day weekday delivery option");
  const confirmed = await waitUntil(preferredAmazonDayWeekdaySelected, 12000, 300);
  if (!confirmed) {
    await pauseForManualCheckout(
      activeJob,
      `Amazon offered a consolidated Monday-Friday Amazon Day delivery, but did not confirm its selection. Attempted option: ${option.text}`,
      "checkout",
    );
    return false;
  }
  await sleep(1200);
  return true;
}

async function ensureWarehouseOpenDayDelivery(activeJob) {
  const selected = selectedDeliveryRadioContext();
  if (!selected || !deliveryContextIsNotConsolidated(selected)) return null;

  const weekdayOption = consolidatedWeekdayDeliveryOption();
  if (!weekdayOption?.control) {
    await pauseForManualCheckout(
      activeJob,
      `Amazon selected a split delivery or a delivery that includes Saturday or Sunday, but no Monday-Friday consolidated option could be selected safely. Selected option: ${selected.text}`,
      "checkout",
    );
    return false;
  }

  showPanel(
    "Weekday delivery required",
    `The warehouse is closed Saturday and Sunday. Selecting the consolidated weekday option: ${weekdayOption.text}`,
    null,
    null,
  );
  await sendDiagnostic("Replacing a split or weekend delivery with a consolidated weekday delivery.", {
    selected_option_text: selected.text,
    replacement_option_text: weekdayOption.text,
    replacement_is_amazon_day: isAmazonDayDeliveryContext(weekdayOption),
  });
  await clickDeliveryRadioContext(weekdayOption, "consolidated Monday-Friday delivery option");
  const confirmed = await waitUntil(consolidatedWeekdayDeliverySelected, 12000, 300);
  if (!confirmed) {
    await pauseForManualCheckout(
      activeJob,
      `Amazon did not confirm the Monday-Friday delivery selection. Attempted option: ${weekdayOption.text}`,
      "checkout",
    );
    return false;
  }
  await sleep(1200);
  return true;
}

function brooklynWeekday(now = new Date()) {
  try {
    return new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      weekday: "short",
    }).format(now);
  } catch {
    return "";
  }
}

function brooklynNextDayIsWarehouseHoliday(now = new Date()) {
  // The warehouse is closed Saturday and Sunday. A Friday/Saturday checkout
  // should therefore use Amazon's rewarded later delivery; every other
  // Brooklyn weekday should keep the free next-day option.
  return ["Fri", "Sat"].includes(brooklynWeekday(now));
}

function freeNextDayDeliveryOption() {
  const contexts = [...document.querySelectorAll("input[type='radio']")]
    .filter((radio) => !radio.disabled)
    .map(deliveryRadioContext);
  return contexts.find((context) => {
    const text = normalizedText(context.text || "").toLowerCase();
    return /\bfree\b/.test(text)
      && (/\btomorrow\b/.test(text) || /\bnext[ -]?day\b/.test(text) || /\bone[ -]?day\b/.test(text))
      && !deliveryContextIsNotConsolidated(context)
      && !isAmazonDayDeliveryContext(context);
  }) || null;
}

function freeNextDayDeliverySelected() {
  return [...document.querySelectorAll("input[type='radio']:checked")]
    .filter((radio) => !radio.disabled)
    .map(deliveryRadioContext)
    .some((context) => {
      const text = normalizedText(context.text || "").toLowerCase();
      return /\bfree\b/.test(text)
        && (/\btomorrow\b/.test(text) || /\bnext[ -]?day\b/.test(text) || /\bone[ -]?day\b/.test(text))
        && !deliveryContextIsNotConsolidated(context)
        && !isAmazonDayDeliveryContext(context);
    });
}

async function ensureFreeNextDayDelivery(activeJob, brooklynDay) {
  const nextDay = freeNextDayDeliveryOption();
  if (!nextDay?.radio || !nextDay?.control || freeNextDayDeliverySelected()) return true;
  showPanel(
    "Free next-day delivery",
    `Brooklyn is ${brooklynDay || "not a warehouse-holiday day"}; selecting ${nextDay.text}.`,
    null,
    null,
  );
  await clickDeliveryRadioContext(nextDay, "free next-day delivery option");
  // Amazon replaces the delivery-option markup after selection.  Re-query the
  // checked radio instead of reading `.checked` from the label/control or a
  // detached pre-click radio node.
  const selected = await waitUntil(freeNextDayDeliverySelected, 12000, 300);
  if (!selected) {
    await pauseForManualCheckout(
      activeJob,
      `Amazon showed a free next-day delivery option, but did not confirm its selection. Option text: ${nextDay.text}`,
      "checkout",
    );
    return false;
  }
  await sleep(1200);
  return true;
}

async function ensureRewardedLaterDelivery(activeJob) {
  // This is a hard warehouse-safety rule, independent of the optional reward
  // preference. A split promise such as Friday + Saturday must be replaced by
  // the available consolidated weekday option (normally Amazon Day Monday).
  const warehouseDaySelection = await ensureWarehouseOpenDayDelivery(activeJob);
  if (warehouseDaySelection !== null) return warehouseDaySelection;

  // Dispatch benefits from one predictable weekday delivery. Whenever Amazon
  // exposes an eligible Amazon Day option, prefer it over an earlier default
  // even when that default is itself Monday-Friday.
  if (!await ensurePreferredAmazonDayWeekdayDelivery(activeJob)) return false;
  if (preferredAmazonDayWeekdaySelected()) return true;

  const state = await getExtensionState();
  const brooklynDay = brooklynWeekday();
  const shouldPreferReward = state.preferRewardedLaterDelivery === true
    && brooklynNextDayIsWarehouseHoliday();
  if (!shouldPreferReward) return ensureFreeNextDayDelivery(activeJob, brooklynDay);
  if (rewardedLaterDeliverySelected()) return true;

  const rewardOption = fridayRewardDeliveryOption();
  if (!rewardOption) return true;
  if (!rewardOption.control) {
    await pauseForManualCheckout(
      activeJob,
      "Amazon offers a later delivery option with an extra 1% reward, but its radio control could not be selected safely.",
      "checkout",
    );
    return false;
  }

  showPanel(
    "Later delivery 1% reward",
    `Selecting the rewarded delivery option from ${rewardOption.optionCount} available option(s): ${rewardOption.text}`,
    null,
    null,
  );
  await sendDiagnostic("Selecting later delivery option with 1% reward.", {
    brooklyn_weekday: brooklynDay,
    brooklyn_timezone: "America/New_York",
    next_day_is_warehouse_holiday: true,
    warehouse_holiday_preference: state.preferRewardedLaterDelivery === true,
    option_count: rewardOption.optionCount,
    option_text: rewardOption.text,
  });
  await clickDeliveryRadioContext(rewardOption, "later delivery option with 1% reward");
  const selected = await waitUntil(rewardedLaterDeliverySelected, 12000, 300);
  if (!selected) {
    await pauseForManualCheckout(
      activeJob,
      `Amazon offered a delivery option with an extra 1% reward, but did not confirm its selection. Option text: ${rewardOption.text}`,
      "checkout",
    );
    return false;
  }
  await sleep(1200);
  return true;
}

async function ensureFinalConsolidatedDelivery(activeJob) {
  // Amazon can rerender the delivery radios after address/payment checks and
  // silently restore its mixed-date default. Revalidate at the last possible
  // point and require the consolidated selection to remain stable before the
  // native Place Order control is clicked.
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    const selected = selectedDeliveryRadioContext();
    if (selected && deliveryContextIsNotConsolidated(selected)) {
      const corrected = await ensureWarehouseOpenDayDelivery(activeJob);
      if (!corrected) return false;
    }

    const stableText = selectedDeliveryRadioContext()?.text || "";
    await sleep(1800);
    const afterSettle = selectedDeliveryRadioContext();
    const finalPromiseText = checkoutDeliveryPromiseText();
    if (
      afterSettle
      && !deliveryContextIsNotConsolidated(afterSettle)
      && deliveryContextNamesWarehouseOpenDay(afterSettle)
      && !deliveryTextIncludesWarehouseClosedDay(finalPromiseText)
      && normalizedText(afterSettle.text || "") === normalizedText(stableText || afterSettle.text || "")
    ) {
      await sendDiagnostic("Final consolidated delivery selection remained stable before Place Order.", {
        group_key: activeJob?.job?.group_key || "",
        option_text: afterSettle.text,
        checkout_promise_text: finalPromiseText,
        stability_attempt: attempt,
      });
      return true;
    }
  }

  const selected = selectedDeliveryRadioContext();
  await pauseForManualCheckout(
    activeJob,
    `Amazon kept changing the final delivery selection. A single Monday-Friday consolidated option must remain selected before Place Order. Current option: ${selected?.text || "not visible"}`,
    "checkout",
  );
  return false;
}

function checkoutDeliveryPromiseText() {
  const selectedOption = selectedDeliveryRadioContext();
  const selectedOptionText = normalizedText(selectedOption?.text || "");
  // The checked radio is the authoritative promise. Amazon can leave the
  // checkout section heading on its old date for several seconds after a
  // delivery-radio change (for example, "Arriving Aug 22" after Monday Aug 24
  // is selected). Mixing that stale heading back into the candidates can make
  // the final guard reject the confirmed weekday selection and never submit.
  if (selectedOptionText) return selectedOptionText;
  const selectors = [
    "h2.address-promise-text",
    ".address-promise-text",
    ".delivery-promise-text",
    ".delivery-option-text",
    "[data-testid*='delivery-promise' i]",
    "[class*='delivery-promise' i]",
  ];
  const elementCandidates = [...document.querySelectorAll(selectors.join(", "))]
    .filter((element) => {
      if (!visible(element) || element.closest?.("#nutricity-panel, .a-popover, .a-popover-preload")) return false;
      const optionRadio = element.closest?.("label, .a-radio, .rcx-checkout-delivery-option-a-control-row")?.querySelector?.("input[type='radio']");
      return !optionRadio || optionRadio.checked;
    })
    .slice(0, 80)
    .map((element) => normalizedText(element.textContent || ""))
    .filter((text) => text && /arriv|deliver|shipping|delivery/i.test(text) && text.length <= 300);
  const candidates = elementCandidates.filter(Boolean);
  const unique = [...new Set(candidates)];
  const month = /\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2}\b/i;
  const dated = unique.filter((text) => month.test(text) || /arriving\s+(?:today|tomorrow)|deliver(?:y|ing)?\s+(?:today|tomorrow)/i.test(text));
  return (dated.length ? dated : unique).sort((left, right) => left.length - right.length)[0] || "";
}

function checkoutDeliveryPromise(limitDays = DEFAULT_DELIVERY_LIMIT_DAYS) {
  const text = checkoutDeliveryPromiseText();
  return parseAmazonDeliveryPromise(text, limitDays);
}

async function rejectLateCheckout(activeJob, promise, deliveryLimitDays) {
  const orderNames = Array.isArray(activeJob?.job?.order_names) ? activeJob.job.order_names.filter(Boolean) : [];
  const orderLabel = orderNames.join(", ") || activeJob?.job?.recipient_name || activeJob?.job?.group_key || "Amazon checkout";
  const earliest = promise.earliest?.toLocaleDateString?.("en-US", { month: "short", day: "numeric", year: "numeric" }) || "a date beyond the allowed window";
  const message = `Skipped Amazon checkout for ${orderLabel}: delivery is promised for ${earliest}, which is ${promise.daysFromToday} day(s) away. The maximum allowed delivery window is ${deliveryLimitDays} days. Amazon promise: ${promise.text}`;
  const notificationKey = `late-delivery:${activeJob?.job?.group_key || orderLabel}:${promise.earliest?.toISOString?.().slice(0, 10) || "unknown"}`;
  try {
    await contentApi("/api/notifications", {
      method: "POST",
      body: JSON.stringify({
        notification_key: notificationKey,
        kind: "late_delivery",
        title: "Late Amazon delivery skipped",
        message,
        odoo_order_name: orderNames.join(", "),
        delivery_date: promise.earliest?.toISOString?.() || "",
      }),
      timeoutMs: 15000,
    });
  } catch (error) {
    sendDiagnostic("Could not save late-delivery notification.", { message: error?.message || String(error || "") }, "warn").catch(() => {});
  }
  showPanel("Late delivery skipped", message, null, null);
  await send({
    type: "FAIL_JOB",
    message,
    missingAsin: "",
    missingLineId: null,
    failureCode: "late_delivery",
  });
  return false;
}

async function checkoutDeliveryWindowIsAllowed(activeJob) {
  const selectedDelivery = selectedDeliveryRadioContext();
  if (selectedDelivery && deliveryContextIsNotConsolidated(selectedDelivery)) {
    await pauseForManualCheckout(
      activeJob,
      `Checkout is blocked because the selected Amazon delivery is split across dates or includes Saturday/Sunday. Select one Monday-Friday consolidated delivery before continuing. Selected option: ${selectedDelivery.text}`,
      "checkout",
    );
    return false;
  }

  const state = await getExtensionState();
  const deliveryLimitDays = normalizedDeliveryLimitDays(state.deliveryLimitDays);
  const promise = await waitUntil(() => {
    const parsed = checkoutDeliveryPromise(deliveryLimitDays);
    return parsed.text || null;
  }, 5000, 500) || checkoutDeliveryPromise(deliveryLimitDays);
  if (deliveryTextIncludesWarehouseClosedDay(promise.text)) {
    await pauseForManualCheckout(
      activeJob,
      `Checkout is blocked because the final Amazon delivery promise falls on Saturday or Sunday. Select a Monday-Friday delivery before continuing. Amazon promise: ${promise.text}`,
      "checkout",
    );
    return false;
  }
  if (!promise.text) {
    sendDiagnostic(`Checkout delivery promise was not visible; continuing without the ${deliveryLimitDays}-day guard.`, {
      order: activeJobOrderLabel(activeJob) || activeJob?.job?.group_key || "",
      url: location.href,
    }, "warn").catch(() => {});
    showPanel("Nutricity checkout", "Delivery date was not visible yet. Continuing checkout.", null, null);
    return true;
  }
  if (promise.daysFromToday === null || promise.daysFromToday === undefined) {
    sendDiagnostic("Checkout delivery promise could not be parsed; continuing checkout.", {
      promise: promise.text,
      order: activeJobOrderLabel(activeJob) || activeJob?.job?.group_key || "",
      url: location.href,
    }, "warn").catch(() => {});
    showPanel("Nutricity checkout", `Delivery text found but date was unreadable: ${promise.text}. Continuing checkout.`, null, null);
    return true;
  }
  if (promise.late) {
    await rejectLateCheckout(activeJob, promise, deliveryLimitDays);
    return false;
  }
  showPanel("Nutricity checkout", `Delivery window accepted: ${promise.text}`, null, null);
  return true;
}

function checkoutQuantityFromPage() {
  const visibleQuantities = checkoutVisibleQuantityValues();
  if (visibleQuantities.length) return visibleQuantities.reduce((sum, quantity) => sum + quantity, 0);
  const text = (document.body.innerText || document.body.textContent || "").replace(/\s+/g, " ");
  const deleteStepperMatch = text.match(/(?:minimum quantity reached,\s*delete item|delete item)\s+(\d+)\s+\1\s+increase item quantity/i);
  if (deleteStepperMatch) return Number(deleteStepperMatch[1]);
  const quantityMatch = text.match(/\bquantity\s*:?\s*(\d+)\b/i);
  return quantityMatch ? Number(quantityMatch[1]) : 0;
}

function checkoutIncreaseQuantityButton() {
  return [...document.querySelectorAll("button, input[type='button'], input[type='submit'], a, span.a-button")]
    .find((element) => {
      const label = normalizedText(element.getAttribute?.("aria-label") || element.value || element.title || element.innerText || element.textContent);
      return visible(element) && !element.disabled && label.includes("increase item quantity");
    });
}

function expectedSubscribeCheckoutQuantity(activeJob) {
  if (!activeJob?.subscribeAndSave) return 0;
  const pricedQuantities = Object.values(activeJob.pricing || {})
    .map((item) => Number(item.quantity || 0))
    .filter((quantity) => quantity > 0);
  if (pricedQuantities.length) return Math.max(...pricedQuantities);
  const activeItem = activeJob.job?.items?.[Number(activeJob.itemIndex || 0)];
  return Number(activeItem?.quantity || 0);
}

async function ensureSubscribeCheckoutQuantity(activeJob) {
  const expected = expectedSubscribeCheckoutQuantity(activeJob);
  if (expected <= 1) return true;
  let current = checkoutQuantityFromPage();
  if (!current || current >= expected) return true;
  showPanel("Nutricity checkout", `Checkout shows Subscribe & Save quantity ${current}; increasing to ${expected}.`, null, null);
  for (let index = current; index < expected; index += 1) {
    const increase = checkoutIncreaseQuantityButton();
    if (!increase) break;
    await clickElement(increase, "Increase checkout item quantity");
    await sleep(1200);
    current = checkoutQuantityFromPage() || current + 1;
    if (current >= expected) break;
  }
  current = checkoutQuantityFromPage() || current;
  if (current >= expected) {
    showPanel("Nutricity checkout", `Verified Subscribe & Save checkout quantity ${current}.`, null, null);
    return true;
  }
  await pauseForManualCheckout(activeJob, `Checkout still shows Subscribe & Save quantity ${current || "unknown"} instead of ${expected}.`);
  return false;
}

function findBusinessContinueCheckoutButton() {
  return [...document.querySelectorAll(
    "a[name='checkout-byg-ptc-button'], a[href*='/checkout/entry/cart/amazon_business'], a[href*='proceedToCheckout=1'], button, input[type='submit'], input[type='button'], span.a-button",
  )].find((element) => {
    const href = String(element.getAttribute?.("href") || element.href || "").toLowerCase();
    const text = normalizedText(element.value || element.innerText || element.textContent);
    return visible(element) && !element.disabled && (
      element.getAttribute?.("name") === "checkout-byg-ptc-button" ||
      href.includes("/checkout/entry/cart/amazon_business") ||
      (href.includes("proceedtocheckout=1") && text.includes("continue to checkout"))
    );
  });
}

async function handleBusinessCheckoutInterstitial() {
  const continueCheckout = findBusinessContinueCheckoutButton();
  if (!continueCheckout) return false;
  showPanel("Nutricity checkout", "Continuing through Amazon Business checkout.", null, null);
  await clickElement(continueCheckout, "Amazon Business continue to checkout button");
  await sleep(1500);
  return true;
}

async function handlePaymentSelection(activeJob) {
  const started = Date.now();
  try {
    const state = await getExtensionState();
    const cardPreferences = cardPreferenceList(state.cardLast4Preference);
    let payment = findPaymentSelection(cardPreferences);
    if (!payment) {
      if (!findPaymentRadio()) return false;
      showPanel("Nutricity checkout", "Waiting for Amazon to finish loading the payment controls.", null, null);
      payment = await waitUntil(() => findPaymentSelection(cardPreferences), 12000, 200);
      if (!payment) {
        await pauseForManualCheckout(activeJob, "Amazon is asking for a payment method, but I could not find the payment Continue button.");
        return true;
      }
    }
    const selectedDigits = cardDigitsForPaymentRadio(payment.radio);
    sendDiagnostic("Payment selection controls detected.", {
      elapsedMs: Date.now() - started,
      selectedDigits,
      hasPreferences: cardPreferences.length > 0,
      radioAlreadySelected: paymentRadioIsSelected(payment.radio),
    }).catch(() => {});
    showPanel("Nutricity checkout", selectedDigits ? `Selecting card ending in ${selectedDigits}.` : "Selecting Amazon payment method.", null, null);
    if (!paymentRadioIsSelected(payment.radio)) {
      const clicked = await clickPaymentRadio(payment.radio);
      const stableSelection = clicked && await waitUntil(() => {
        const current = paymentRadioForDigits(selectedDigits);
        return current && paymentRadioIsSelected(current) ? current : false;
      }, 2200, 150);
      if (!stableSelection) {
        await pauseForManualCheckout(activeJob, `Could not select preferred card ending in ${selectedDigits || cardPreferences.join(" or ")}.`);
        return true;
      }
    }
    if (cardPreferences.length && selectedDigits && !cardPreferences.includes(selectedDigits)) {
      await pauseForManualCheckout(activeJob, `Could not find preferred card ending in ${cardPreferences.join(" or ")}.`);
      return true;
    }
    const currentPreferredRadio = paymentRadioForDigits(selectedDigits);
    if (cardPreferences.length && (!currentPreferredRadio || !paymentRadioIsSelected(currentPreferredRadio))) {
      await pauseForManualCheckout(activeJob, `Could not select preferred card ending in ${cardPreferences.join(" or ")}.`);
      return true;
    }
    payment = findPaymentSelection(cardPreferences);
    const continueButton = nativePaymentContinueControl(payment?.continueButton);
    if (!payment || !continueButton) {
      await pauseForManualCheckout(activeJob, "Amazon changed the payment form before the selected card could be confirmed.");
      return true;
    }
    await clickElement(continueButton, "Use this payment method button", { preClickDelayMs: 80, delayMs: 180 });
    showPanel("Nutricity checkout", "Payment method selected. Waiting for checkout.", null, null);
    let progress = await waitForCheckoutPaymentProgress(cardPreferences, 4500, { stopOnTransition: true });
    let advanced = Boolean(progress && (progress.confirmed || progress.hasPlaceOrderButton || !progress.hasPaymentRadio));
    if (!advanced && findPaymentRadio() && !findPlaceOrderButton()) {
      const alternate = alternatePaymentContinueButtons(continueButton)[0];
      if (alternate) {
        showPanel("Nutricity checkout", "Retrying Amazon payment continue button.", null, null);
        await clickElement(alternate, "alternate Use this payment method button", { preClickDelayMs: 80, delayMs: 180 });
        progress = await waitForCheckoutPaymentProgress(cardPreferences, 3500, { stopOnTransition: true });
        advanced = Boolean(progress && (progress.confirmed || progress.hasPlaceOrderButton || !progress.hasPaymentRadio));
      }
    }
    if (!advanced && findPaymentRadio() && !findPlaceOrderButton()) {
      const currentDigits = checkoutSelectedCardDigits();
      await sendDiagnostic("Payment selection guard paused checkout.", {
        elapsedMs: Date.now() - started,
        selectedDigits,
        currentDigits,
        preferredDigits: cardPreferences,
        hasPaymentRadio: Boolean(findPaymentRadio()),
        hasPlaceOrderButton: Boolean(findPlaceOrderButton()),
      }, "warn").catch(() => {});
      await pauseForManualCheckout(
        activeJob,
        selectedDigits
          ? `Amazon did not leave payment selection after choosing card ending in ${selectedDigits}.`
          : "Amazon did not leave payment selection after choosing the payment method.",
      );
      return true;
    }
    activeJob.stage = "checkout";
    await setActiveJob(activeJob);
    const confirmedDigits = progress?.selectedDigits || checkoutSelectedCardDigits();
    if (confirmedDigits) {
      showPanel("Nutricity checkout", `Verified checkout card ending in ${confirmedDigits}.`, null, null);
    }
    sendDiagnostic("Payment selection completed.", {
      elapsedMs: Date.now() - started,
      selectedDigits,
      confirmedDigits,
      advanced: Boolean(advanced),
      hasPlaceOrderButton: Boolean(findPlaceOrderButton()),
      hasPaymentRadio: Boolean(findPaymentRadio()),
    }).catch(() => {});
    return true;
  } catch (error) {
    await pauseForManualCheckout(activeJob, `Payment selection got stuck: ${error.message || error}`);
    return true;
  }
}

async function openPaymentSelectionIfAvailable() {
  const changePayment = findChangePaymentButton() || await waitUntil(findChangePaymentButton, 2500, 250);
  if (!changePayment) return false;
  showPanel("Nutricity checkout", "Opening payment method selection.", null, null);
  await clickElement(changePayment, "Change payment method button", { preClickDelayMs: 80, delayMs: 180 });
  await waitUntil(findPaymentRadio, 1200, 150);
  return true;
}

async function ensurePreferredCheckoutPayment(activeJob) {
  const state = await getExtensionState();
  const cardPreferences = cardPreferenceList(state.cardLast4Preference);
  if (!cardPreferences.length) {
    if (checkoutPaymentConfirmed(cardPreferences)) {
      showPanel("Nutricity checkout", "Verified checkout payment method.", null, null);
    }
    return true;
  }
  const paymentPanel = findCheckoutPaymentPanel();
  const selectedDigits = checkoutSelectedCardDigits();
  if (!paymentPanel && !selectedDigits && !findPlaceOrderButton() && !findChangePaymentButton()) return true;

  const openPaymentRadio = findPaymentRadio();
  if (openPaymentRadio) {
    showPanel("Nutricity checkout", `Selecting preferred checkout card ending in ${cardPreferences.join(" or ")}.`, null, null);
    await handlePaymentSelection(activeJob);
    if (activeJob.paused) return false;
    // Amazon can expose the final review button before its selected-payment
    // summary hydrates. Wait for the preferred card evidence itself; the final
    // button is not proof that Amazon retained the chosen card.
    const confirmedDigits = await waitForPreferredCheckoutPayment(cardPreferences, 10000);
    if (confirmedDigits && cardPreferences.includes(confirmedDigits)) {
      showPanel("Nutricity checkout", `Verified checkout card ending in ${confirmedDigits}.`, null, null);
      return true;
    }
    await pauseForManualCheckout(
      activeJob,
      confirmedDigits
        ? `Amazon still shows card ending in ${confirmedDigits} after payment selection; expected ${cardPreferences.join(" or ")}.`
        : `Amazon did not confirm the selected card ending in ${cardPreferences.join(" or ")}.`,
    );
    return false;
  }

  if (selectedDigits && cardPreferences.includes(selectedDigits)) {
    showPanel("Nutricity checkout", `Verified checkout card ending in ${selectedDigits}.`, null, null);
    return true;
  }

  if (!selectedDigits && !findChangePaymentButton()) {
    const settledDigits = await waitForPreferredCheckoutPayment(cardPreferences, 1000);
    if (settledDigits && settledDigits !== true) {
      showPanel("Nutricity checkout", `Verified checkout card ending in ${settledDigits}.`, null, null);
      return true;
    }
  }

  const expected = cardPreferences.join(" or ");
  if (selectedDigits) {
    showPanel("Nutricity checkout", `Checkout shows card ending in ${selectedDigits}; switching to ${expected}.`, null, null);
  } else {
    showPanel("Nutricity checkout", `Could not verify checkout card; opening payment selection for ${expected}.`, null, null);
  }

  if (findPaymentRadio() || await waitUntil(findPaymentRadio, 1800, 150)) {
    await handlePaymentSelection(activeJob);
    if (activeJob.paused) return false;
    const confirmedDigits = await waitForPreferredCheckoutPayment(cardPreferences, 10000);
    if (confirmedDigits && cardPreferences.includes(confirmedDigits)) {
      showPanel("Nutricity checkout", `Verified checkout card ending in ${confirmedDigits}.`, null, null);
      return true;
    }
    await pauseForManualCheckout(
      activeJob,
      confirmedDigits
        ? `Amazon still shows card ending in ${confirmedDigits} after payment selection; expected ${expected}.`
        : `Amazon did not confirm the selected card ending in ${expected}.`,
    );
    return false;
  }

  if (!await openPaymentSelectionIfAvailable()) {
    const lateDigits = await waitForPreferredCheckoutPayment(cardPreferences, 1500);
    if (lateDigits && lateDigits !== true) {
      showPanel("Nutricity checkout", `Verified checkout card ending in ${lateDigits}.`, null, null);
      return true;
    }
    await pauseForManualCheckout(activeJob, selectedDigits
      ? `Checkout shows card ending in ${selectedDigits}, but I could not find the Change payment method link to switch to ${expected}.`
      : `Could not verify the checkout card, and I could not find the Change payment method link to select ${expected}.`);
    return false;
  }
  if (findPaymentRadio() || await waitUntil(findPaymentRadio, 2500, 150)) {
    await handlePaymentSelection(activeJob);
    if (activeJob.paused) return false;
    const confirmedDigits = await waitForPreferredCheckoutPayment(cardPreferences, 10000);
    if (confirmedDigits && cardPreferences.includes(confirmedDigits)) {
      showPanel("Nutricity checkout", `Verified checkout card ending in ${confirmedDigits}.`, null, null);
      return true;
    }
    await pauseForManualCheckout(
      activeJob,
      confirmedDigits
        ? `Amazon still shows card ending in ${confirmedDigits} after payment selection; expected ${expected}.`
        : `Amazon did not confirm the selected card ending in ${expected}.`,
    );
    return false;
  }
  await pauseForManualCheckout(activeJob, `Opened payment selection, but Amazon did not show card choices for ${expected}.`);
  return false;
}

async function openAddressEditorIfAvailable(activeJob) {
  const directEditor = findAddressNameInput();
  if (directEditor) return directEditor;

  let editAddress = await waitUntil(findEditAddressTrigger, 6000, 400);
  if (!editAddress) {
    const changeAddress = findChangeDeliveryAddressButton();
    if (changeAddress) {
      showPanel("Nutricity checkout", "Opening delivery address selection.", null, null);
      activeJob.stage = "editing_address";
      activeJob.editAddressClickedAt = Date.now();
      await setActiveJob(activeJob);
      await clickElement(changeAddress, "Change delivery address link");
      await sleep(2000);
      editAddress = await waitUntil(findEditAddressTrigger, 10000, 400);
      if (!editAddress && findAddressNameInput()) return findAddressNameInput();
    }
  }

  if (!editAddress) return null;
  await selectAddressRadioForElement(editAddress);
  showPanel("Nutricity checkout", "Opening Amazon address editor.", null, null);
  activeJob.stage = "editing_address";
  activeJob.editAddressClickedAt = Date.now();
  await setActiveJob(activeJob);
  await clickElement(editAddress, "Edit address link");
  await waitForElement([
    "#address-ui-widgets-enterAddressFullName",
    "input[name='address-ui-widgets-enterAddressFullName']",
    "input[aria-label='Full name']",
    "input[name*='FullName']",
    "input[id*='FullName']",
  ], 8000);
  return findAddressNameInput();
}

async function openNewDeliveryAddressFormIfAvailable(activeJob) {
  const directEditor = findAddressNameInput();
  if (directEditor) return directEditor;

  let addNewAddress = findAddNewDeliveryAddressLink();
  if (!addNewAddress) {
    const changeAddress = findChangeDeliveryAddressButton();
    if (changeAddress) {
      showPanel("Nutricity checkout", "Opening delivery address selection.", null, null);
      activeJob.stage = "editing_address";
      activeJob.editAddressClickedAt = Date.now();
      await setActiveJob(activeJob);
      await clickElement(changeAddress, "Change delivery address link");
      await sleep(2000);
      addNewAddress = await waitUntil(findAddNewDeliveryAddressLink, 10000, 400);
    }
  }
  if (!addNewAddress) return null;

  showPanel("Nutricity checkout", "Opening new delivery address form.", null, null);
  activeJob.stage = "editing_address";
  activeJob.editAddressClickedAt = Date.now();
  await setActiveJob(activeJob);
  await clickElement(addNewAddress, "Add a new delivery address link");
  await waitForElement([
    "#address-ui-widgets-enterAddressFullName",
    "input[name='address-ui-widgets-enterAddressFullName']",
    "#address-ui-widgets-enterAddressPhoneNumber",
    "#address-ui-widgets-enterAddressLine1",
  ], 10000);
  return findAddressNameInput();
}

function manualNextStage(activeJob, requestedStage = "") {
  if (requestedStage) return requestedStage;
  const stage = String(activeJob?.stage || "");
  if (stage === "clear_cart") return "product";
  if (stage === "product") return "add_clicked";
  if (stage === "add_clicked") return "navigate_next";
  if (stage === "subscribe_checkout") return "checkout";
  if (stage === "cart") return "checkout";
  if (stage === "editing_address") return "checkout";
  if (stage === "complete_pending") return "find_order_id";
  return stage || "product";
}

async function continueAfterManualStep(activeJob, nextStage = "") {
  const latest = await getActiveJob();
  const next = { ...(latest || activeJob), paused: false, pausedStage: null };
  const targetStage = manualNextStage(next, nextStage);
  if (targetStage === "navigate_next") {
    showPanel("Nutricity fulfilment", "Manual step done. Moving to the next item.", null, null);
    await navigateToNext(next);
    setTimeout(runSafely, 250);
    return;
  }
  if (targetStage === "product") {
    next.itemIndex = Number(next.itemIndex || 0);
    next.cartCleared = true;
  }
  if (targetStage === "add_clicked") {
    next.addClickedAt = Date.now();
    markItemAdded(next);
  }
  next.stage = targetStage;
  await setActiveJob(next, { allowUnpause: true });
  showPanel("Nutricity fulfilment", `Manual step done. Continuing ${targetStage}.`, null, null);
  setTimeout(runSafely, 250);
}

async function pauseForManualCheckout(activeJob, message, nextStage = "checkout") {
  activeJob.pausedStage = activeJob.stage || "checkout";
  activeJob.stage = activeJob.pausedStage;
  activeJob.paused = true;
  await setActiveJob(activeJob);
  showPanel(
    "Nutricity checkout needs attention",
    `${message} Make the needed Amazon checkout change, then click Resume to retry this step.`,
    "I did it manually, continue",
    () => continueAfterManualStep(activeJob, nextStage),
  );
}

async function pauseBeforeAmazonSubmitIfRequired(activeJob, approvalKey, message, nextStage = "checkout") {
  const state = await getExtensionState();
  if (state.pauseBeforePlaceOrder !== true || activeJob?.[approvalKey]) return false;
  const next = {
    ...activeJob,
    paused: true,
    pausedStage: nextStage,
    stage: nextStage,
    pauseBeforePlaceOrderAt: Date.now(),
  };
  await setActiveJob(next);
  showPanel(
    "Final step needs approval",
    `${message} Review Amazon checkout, then click Place this order only when you are ready to submit the purchase.`,
    "Place this order",
    async () => {
      const latest = await getActiveJob();
      const approved = {
        ...(latest || next),
        [approvalKey]: Date.now(),
        paused: false,
        pausedStage: null,
        stage: nextStage,
      };
      await setActiveJob(approved, { allowUnpause: true, allowStageRegression: true, reason: approvalKey });
      showPanel("Nutricity checkout", "Approval recorded. Continuing to Amazon Place Order.", null, null);
      setTimeout(runSafely, 250);
    },
  );
  return true;
}

async function saveEditedAddress(activeJob, checkoutRecipient) {
  showPanel("Nutricity checkout", "Editing Amazon address name.", null, null);
  const filled = await fillFullName(checkoutRecipient);
  if (!filled) return false;

  showPanel("Nutricity checkout", "Saving Amazon address.", null, null);
  await sleep(1000);
  const useAddress = await waitUntil(findUseAddressButton, 8000) || findButtonByText(["use this address", "save address", "continue"]);
  if (!useAddress) {
    if (checkoutAdvancedAfterAddressSave()) {
      activeJob.stage = "checkout";
      activeJob.editAddressClickedAt = null;
      activeJob.addressEditedRecipient = checkoutRecipient;
      activeJob.addressEditedAt = Date.now();
      await setActiveJob(activeJob);
      showPanel("Nutricity checkout", "Amazon saved the address and advanced to checkout.", null, null);
      return true;
    }
    await pauseForManualCheckout(activeJob, "Could not find the Use this address button.");
    return false;
  }
  await clickUseAddressButton(useAddress);
  await waitUntil(
    () => checkoutRecipientConfirmed(checkoutRecipient) || findPlaceOrderButton() || findPaymentSelection() || !findAddressNameInput(),
    9000,
    250,
  );
  if (checkoutRecipientConfirmed(checkoutRecipient)) {
    return markCheckoutRecipientConfirmed(activeJob, checkoutRecipient, "Verified edited delivery address. Continuing checkout.");
  }
  if (findPlaceOrderButton() && checkoutShowsRecipient(checkoutRecipient)) {
    return markCheckoutRecipientConfirmed(activeJob, checkoutRecipient, "Checkout address panel shows the edited recipient. Continuing checkout.");
  }
  await sleep(1000);

  const recipientAddressControl = await waitUntil(() => !findAddressNameInput() && recipientAddressSelectionControl(checkoutRecipient), 10000, 300);
  if (recipientAddressControl) {
    showPanel("Nutricity checkout", "Selecting the recipient delivery address.", null, null);
    await clickElement(recipientAddressControl, "Recipient address row");
  }

  const deliverToThisAddress = await waitUntil(findDeliverToThisAddressButton, 5000, 300);
  if (deliverToThisAddress) {
    if (!recipientAddressControl && !checkoutShowsRecipient(checkoutRecipient)) {
      const retryCount = Number(activeJob.addressEditSaveRetries || 0) + 1;
      activeJob.addressEditSaveRetries = retryCount;
      if (retryCount <= 2) {
        // Amazon can return to the address list with the previously selected
        // warehouse row while the edited name is still being saved. Retry the
        // same safe edit before escalating this recoverable delay to a pause.
        activeJob.stage = "editing_address";
        activeJob.editAddressClickedAt = Date.now();
        await setActiveJob(activeJob);
        showPanel("Nutricity checkout", `Amazon kept the previous address name. Retrying address save (${retryCount}/2).`, null, null);
        const editor = await openAddressEditorIfAvailable(activeJob);
        if (editor) return saveEditedAddress(activeJob, checkoutRecipient);
      }
      await pauseForManualCheckout(activeJob, `Could not find the edited address row for "${checkoutRecipient}" after saving.`);
      return false;
    }
    showPanel("Nutricity checkout", "Selecting edited delivery address.", null, null);
    await clickElement(deliverToThisAddress, "Deliver to this address button");
    await sleep(1200);
    if (checkoutRecipientConfirmed(checkoutRecipient)) {
      return markCheckoutRecipientConfirmed(activeJob, checkoutRecipient, "Verified edited delivery address. Continuing checkout.");
    }
  }

  showPanel("Nutricity checkout", "Waiting for checkout to update.", null, null);
  await waitUntil(
    () =>
      checkoutLimitPurchaseIssue(activeJob) ||
      checkoutRecipientConfirmed(checkoutRecipient) ||
      findPaymentSelection() ||
      findPlaceOrderButton() ||
      !findAddressNameInput(),
    5000,
    250,
  );
  if (await handleCheckoutLimitPurchase(activeJob)) return true;
  if (checkoutRecipientConfirmed(checkoutRecipient)) {
    return markCheckoutRecipientConfirmed(activeJob, checkoutRecipient, "Verified edited delivery address. Continuing checkout.");
  }
  activeJob.stage = "checkout";
  activeJob.editAddressClickedAt = null;
  activeJob.addressEditedRecipient = checkoutRecipient;
  activeJob.addressEditedAt = Date.now();
  await setActiveJob(activeJob);
  const retryCount = Number(activeJob.addressEditSaveRetries || 0) + 1;
  activeJob.addressEditSaveRetries = retryCount;
  if (retryCount <= 2) {
    activeJob.stage = "editing_address";
    activeJob.editAddressClickedAt = Date.now();
    await setActiveJob(activeJob);
    showPanel("Nutricity checkout", `Amazon has not shown the updated address yet. Retrying address save (${retryCount}/2).`, null, null);
    const editor = await openAddressEditorIfAvailable(activeJob);
    if (editor) return saveEditedAddress(activeJob, checkoutRecipient);
  }
  await pauseForManualCheckout(activeJob, `Amazon did not confirm delivery address "${checkoutRecipient}" after saving twice.`);
  return false;
}

async function saveNewDeliveryAddress(activeJob, checkoutRecipient) {
  if (!findAddressNameInput() && !await openNewDeliveryAddressFormIfAvailable(activeJob)) {
    await pauseForManualCheckout(activeJob, "Could not find the Add a new delivery address link.");
    return false;
  }

  showPanel("Nutricity checkout", "Adding Amazon delivery address.", null, null);
  const filled = await fillNewDeliveryAddress(checkoutRecipient);
  if (!filled) {
    await pauseForManualCheckout(activeJob, "Could not fill the new delivery address form.");
    return false;
  }

  showPanel("Nutricity checkout", "Saving new Amazon address.", null, null);
  await sleep(1000);
  const useAddress = await waitUntil(findUseAddressButton, 8000) || findButtonByText(["use this address", "save address", "continue"]);
  if (!useAddress) {
    if (checkoutAdvancedAfterAddressSave()) {
      activeJob.stage = "checkout";
      activeJob.addressMode = "new";
      activeJob.addressEditedRecipient = checkoutRecipient;
      activeJob.addressEditedAt = Date.now();
      await setActiveJob(activeJob);
      showPanel("Nutricity checkout", "Amazon saved the new address and advanced to checkout.", null, null);
      return true;
    }
    await pauseForManualCheckout(activeJob, "Could not find the Use this address button.");
    return false;
  }
  await clickUseAddressButton(useAddress);
  await sleep(3000);

  const deliverToThisAddress = await waitUntil(findDeliverToThisAddressButton, 5000, 300);
  if (deliverToThisAddress) {
    showPanel("Nutricity checkout", "Selecting new delivery address.", null, null);
    await clickElement(deliverToThisAddress, "Deliver to this address button");
    await sleep(1200);
    if (checkoutRecipientConfirmed(checkoutRecipient)) {
      await markCheckoutRecipientConfirmed(activeJob, checkoutRecipient, "Verified new delivery address. Continuing checkout.");
      activeJob.addressMode = "new";
      await setActiveJob(activeJob);
      return true;
    }
  }

  await waitUntil(
    () => checkoutRecipientConfirmed(checkoutRecipient) || findPaymentSelection() || findPlaceOrderButton() || !findAddressNameInput(),
    5000,
    250,
  );
  if (checkoutRecipientConfirmed(checkoutRecipient)) {
    await markCheckoutRecipientConfirmed(activeJob, checkoutRecipient, "Verified new delivery address. Continuing checkout.");
    activeJob.addressMode = "new";
    await setActiveJob(activeJob);
    return true;
  }
  activeJob.stage = "checkout";
  activeJob.editAddressClickedAt = null;
  activeJob.addressEditedRecipient = checkoutRecipient;
  activeJob.addressEditedAt = Date.now();
  activeJob.addressMode = "new";
  await setActiveJob(activeJob);
  return true;
}

async function verifyCheckoutDeliveryRecipient(activeJob, checkoutRecipient) {
  if (checkoutPageShowsExpectedDelivery(checkoutRecipient)) {
    activeJob.addressVerifiedRecipient = checkoutRecipient;
    activeJob.addressVerifiedAt = Date.now();
    activeJob.addressVerifyAttempts = 0;
    await setActiveJob(activeJob);
    return true;
  }
  // Closing Amazon's address editor often restores the checkout card in two
  // renders: the Place Order button appears first, then the delivery details.
  // Do not pause in the gap when the correct address has simply not painted yet.
  if (!checkoutRecipientConfirmed(checkoutRecipient)) {
    const confirmedAfterRender = await waitUntil(
      () => checkoutRecipientConfirmed(checkoutRecipient),
      6000,
      250,
    );
    if (confirmedAfterRender) {
      activeJob.addressVerifiedRecipient = checkoutRecipient;
      activeJob.addressVerifiedAt = Date.now();
      activeJob.addressVerifyAttempts = 0;
      await setActiveJob(activeJob);
      return true;
    }
  }
  // Amazon's Change-address action navigates to an intermediate address-list
  // page. That page deliberately has no final "Delivering to" summary, so it
  // must be handled as an editable checkout state rather than as an unsafe
  // final checkout. Prefer an already exact warehouse row; otherwise open only
  // the verified Nutricity warehouse row's editor and let the next pass save it.
  if (checkoutAddressSelectionPageOpen()) {
    const recipientRow = addressRowForRecipient(checkoutRecipient);
    if (recipientRow) {
      const selectedRow = selectedCheckoutAddressRow();
      if (selectedRow !== recipientRow && !recipientRow.contains(selectedRow)) {
        const recipientControl = addressSelectionControl(recipientRow);
        if (recipientControl) {
          showPanel("Nutricity checkout", `Selecting delivery address for ${checkoutRecipient}.`, null, null);
          await clickElement(recipientControl, "Recipient address row");
          await sleep(700);
        }
      }
      const selectedAfterClick = selectedCheckoutAddressRow();
      if (selectedAfterClick === recipientRow || recipientRow.contains(selectedAfterClick)) {
        const deliverButton = findDeliverToThisAddressButton();
        if (deliverButton) {
          showPanel("Nutricity checkout", "Exact recipient address selected. Returning to checkout.", null, null);
          await clickElement(deliverButton, "Deliver to this address button");
          return false;
        }
      }
    }

    activeJob.stage = "editing_address";
    activeJob.editAddressClickedAt = Date.now();
    await setActiveJob(activeJob);
    const editor = await openAddressEditorIfAvailable(activeJob);
    if (editor) {
      showPanel("Nutricity checkout", `Editing the verified warehouse address for ${checkoutRecipient}.`, null, null);
      return false;
    }
    await pauseForManualCheckout(
      activeJob,
      `Amazon showed its address list, but no editable Nutricity warehouse row was available for ${checkoutRecipient}.`,
    );
    return false;
  }
  const deliveredTo = checkoutDeliveryRecipientText();
  const placeOrderVisible = Boolean(findPlaceOrderButton());
  if (!deliveredTo) {
    if (checkoutRecipientConfirmed(checkoutRecipient)) {
      activeJob.addressVerifiedRecipient = checkoutRecipient;
      activeJob.addressVerifiedAt = Date.now();
      activeJob.addressVerifyAttempts = 0;
      await setActiveJob(activeJob);
      return true;
    }
    if (placeOrderVisible || !checkoutShowsWarehouseAddress()) {
      await pauseForManualCheckout(
        activeJob,
        `Could not verify the Nutricity warehouse delivery address before placing ${checkoutRecipient}.`,
      );
      return false;
    }
    return true;
  }
  if (checkoutDeliveryRecipientMatches(checkoutRecipient) && checkoutShowsWarehouseAddress()) {
    activeJob.addressVerifiedRecipient = checkoutRecipient;
    activeJob.addressVerifiedAt = Date.now();
    activeJob.addressVerifyAttempts = 0;
    await setActiveJob(activeJob);
    return true;
  }

  const attempts = Number(activeJob.addressVerifyAttempts || 0) + 1;
  activeJob.addressVerifyAttempts = attempts;
  activeJob.addressVerifiedRecipient = "";
  await setActiveJob(activeJob);
  if (attempts > 2) {
    await pauseForManualCheckout(
      activeJob,
      checkoutDeliveryRecipientMatches(checkoutRecipient)
        ? `Delivery address for "${deliveredTo}" is not the Nutricity warehouse address ${DEFAULT_NEW_DELIVERY_ADDRESS.addressLine1}.`
        : `Delivery name still shows "${deliveredTo}" instead of "${checkoutRecipient}".`,
    );
    return false;
  }

  const changeAddress = findChangeDeliveryAddressButton();
  if (!changeAddress) {
    await pauseForManualCheckout(
      activeJob,
      `Delivery name shows "${deliveredTo}" instead of "${checkoutRecipient}", and I could not find the Change delivery address link.`,
    );
    return false;
  }
  showPanel("Nutricity checkout", `Delivery address shows ${deliveredTo}. Reopening address edit.`, null, null);
  activeJob.stage = "editing_address";
  activeJob.editAddressClickedAt = Date.now();
  await setActiveJob(activeJob);
  await clickElement(changeAddress, "Change delivery address link");
  return false;
}

async function handleCheckoutLimitPurchase(activeJob) {
  const issue = checkoutLimitPurchaseIssue(activeJob);
  if (!issue) return false;
  const item = issue.item;
  const asin = item?.asin || "";
  const requestedQuantity = Number(item?.quantity || 0) || null;
  const availableQuantity = Number.isFinite(Number(issue.currentQuantity))
    ? Number(issue.currentQuantity)
    : 0;
  const message = asin
    ? `${issue.message} ASIN ${asin}${issue.title ? ` (${issue.title})` : ""}.${requestedQuantity ? ` Customer ordered ${requestedQuantity}, Amazon checkout allows ${availableQuantity}.` : ""}`
    : `${issue.message}${issue.title ? ` ${issue.title}.` : ""}`;

  if (item && (activeJob.job?.items || []).length > 1 && await shouldFulfilAvailableMixedAsin(activeJob)) {
    showPanel("Limit purchase", message, null, null);
    const result = await send({
      type: "MARK_LINE_MISSING",
      message,
      missingAsin: asin,
      missingLineId: itemPrimaryLineId(item),
      failureCode: "partial_quantity",
      requestedQuantity,
      fulfilledQuantity: availableQuantity,
      availableQuantity,
    });
    if (!result?.ok) {
      await pauseForManualCheckout(activeJob, `${message} I could not report this line to the app.`);
      return true;
    }
    removeMissingItemFromActiveJob(activeJob, item, item);
    activeJob.stage = "checkout";
    await setActiveJob(activeJob);
    if (issue.removeButton) {
      await clickElement(issue.removeButton, "Remove limited purchase item");
      await sleep(1800);
    }
    const continueButton = await waitUntil(findUseAddressButton, 3000, 300)
      || findButtonByText(["continue"]);
    if (continueButton) {
      await clickElement(continueButton, "Continue after removing limited purchase item");
      await sleep(2500);
    }
    showPanel("Split fulfilment", `${message} Remaining item(s) will continue.`, null, null);
    return true;
  }

  showPanel("Limit purchase", message, null, null);
  await send({
    type: "FAIL_JOB",
    message,
    missingAsin: asin,
    missingLineId: item ? itemPrimaryLineId(item) : null,
    failureCode: "partial_quantity",
    requestedQuantity,
    fulfilledQuantity: availableQuantity,
    availableQuantity,
  });
  showPanel("Missing ASINs", `${message} Order moved to Missing ASINs.`, null, null);
  return true;
}

async function handleCheckout(activeJob) {
  await waitForElement([
    "#placeOrder",
    "input.place-your-order-button",
    "#checkout-javaItemSelectPanel",
    "[data-checkout-view-modal]",
    "#checkout-primary-continue-button-id",
    "input[aria-label='Full name']",
    "input[type='radio'][name='addressID']",
    "input[type='radio'][aria-label*='Nutricity' i]",
    "input[type='radio'][aria-label*='United States' i]",
    "#ab-select-address-continue-button-bottom",
    "input[data-testid='ab-select-address-continue-button-bottom']",
    "input[type='radio'][name='ppw-instrumentRowSelection']",
    "input[data-csa-c-slot-id*='continue-payselect']",
    "a[name='checkout-byg-ptc-button']",
    "a[href*='/checkout/entry/cart/amazon_business']",
    "a[href*='proceedToCheckout=1']",
  ], 18000);
  const accountExperience = amazonAccountExperience();
  if (activeJob.amazonAccountExperience !== accountExperience) {
    activeJob.amazonAccountExperience = accountExperience;
    await setActiveJob(activeJob);
    await sendDiagnostic("Detected Amazon checkout account experience.", {
      group_key: activeJob.job?.group_key || "",
      account_experience: accountExperience,
      checkout_url: location.href,
    });
  }
  if (await handleBusinessCheckoutInterstitial()) return;
  if (await handleCheckoutLimitPurchase(activeJob)) return;
  const checkoutRecipient = recipientName(activeJob);
  const extensionState = await getExtensionState();
  const shouldEditExistingAddress = extensionState.editExistingAddress !== false;
  showPanel("Nutricity checkout", `Using recipient name: ${checkoutRecipient}`, null, null);
  const readyPlaceOrder = findPlaceOrderButton();
  if (
    readyPlaceOrder &&
    !readyPlaceOrder.disabled &&
    !findAddressNameInput() &&
    checkoutRecipientConfirmed(checkoutRecipient) &&
    checkoutPaymentConfirmed(cardPreferenceList(extensionState.cardLast4Preference))
  ) {
    activeJob.stage = "checkout";
    activeJob.paused = false;
    activeJob.pausedStage = null;
    activeJob.editAddressClickedAt = null;
    activeJob.addressEditedRecipient = checkoutRecipient;
    activeJob.addressEditedAt = Date.now();
    activeJob.addressVerifiedRecipient = checkoutRecipient;
    activeJob.addressVerifiedAt = Date.now();
    await setActiveJob(activeJob, { allowUnpause: true, allowStageRegression: true });
    showPanel("Nutricity checkout", "Checkout is ready. Placing the order.", null, null);
  }
  if (activeJob.stage === "editing_address") {
    if (!findAddressNameInput() && checkoutRecipientConfirmed(checkoutRecipient)) {
      await markCheckoutRecipientConfirmed(activeJob, checkoutRecipient, `Delivery address already shows ${checkoutRecipient}. Continuing checkout.`);
    } else
    if (shouldEditExistingAddress && !findAddressNameInput()) {
      await openAddressEditorIfAvailable(activeJob);
    } else if (!shouldEditExistingAddress && !findAddressNameInput()) {
      await openNewDeliveryAddressFormIfAvailable(activeJob);
    }
    if (checkoutRecipientConfirmed(checkoutRecipient)) {
      await markCheckoutRecipientConfirmed(activeJob, checkoutRecipient, `Delivery address shows ${checkoutRecipient}. Continuing checkout.`);
    } else if (shouldEditExistingAddress ? await saveEditedAddress(activeJob, checkoutRecipient) : await saveNewDeliveryAddress(activeJob, checkoutRecipient)) {
      // Continue in this same pass so checkout can place the order without waiting for the next interval.
    } else if (Date.now() - Number(activeJob.editAddressClickedAt || 0) > 45000) {
      activeJob.stage = "checkout";
      activeJob.editAddressClickedAt = null;
      await setActiveJob(activeJob);
    } else {
      showPanel("Nutricity checkout", "Waiting for Amazon address editor.", null, null);
      return;
    }
  }
  const continueCheckout = findBusinessContinueCheckoutButton() || findButtonByText(["continue to checkout"]);
  if (continueCheckout) {
    await clickElement(continueCheckout, "Continue to checkout button");
    return;
  }

  let handledPayment = false;
  if (findPaymentRadio()) {
    handledPayment = await handlePaymentSelection(activeJob);
    if (handledPayment) {
      showPanel("Nutricity checkout", "Payment confirmed. Changing address after payment.", null, null);
    }
    if (activeJob.paused) return;
  }

  const addressAlreadyConfirmed = checkoutRecipientConfirmed(checkoutRecipient);
  if (addressAlreadyConfirmed) {
    await markCheckoutRecipientConfirmed(activeJob, checkoutRecipient, `Verified delivery address for ${checkoutRecipient}.`);
  }
  const addressEditIsFresh = addressAlreadyConfirmed || activeJob.addressEditedRecipient === checkoutRecipient && Date.now() - Number(activeJob.addressEditedAt || 0) < 30000;
  if (!addressEditIsFresh) {
    if (shouldEditExistingAddress) {
      const addressRow = nutricityAddressRow();
      if (addressRow) {
        addressRow.scrollIntoView({ block: "center", behavior: "smooth" });
        await sleep(250);
      }
      const addressEditor = await openAddressEditorIfAvailable(activeJob);
      if (addressEditor) {
        if (await saveEditedAddress(activeJob, checkoutRecipient)) {
          // Continue below to place the order immediately when Amazon has returned to checkout.
        } else {
          return;
        }
      } else {
        await sleep(750);
        if (checkoutRecipientConfirmed(checkoutRecipient)) {
          await markCheckoutRecipientConfirmed(activeJob, checkoutRecipient, `Delivery address shows ${checkoutRecipient}. Continuing checkout.`);
        } else {
        await pauseForManualCheckout(activeJob, "Could not find the Change delivery address or Edit address link for the Nutricity address.");
        return;
        }
      }
    } else {
      const addressEditor = await openNewDeliveryAddressFormIfAvailable(activeJob);
      if (!addressEditor) {
        await pauseForManualCheckout(activeJob, "Could not find the Add a new delivery address link.");
        return;
      }
      if (!await saveNewDeliveryAddress(activeJob, checkoutRecipient)) return;
    }
  } else {
    showPanel("Nutricity checkout", "Recipient address edit saved. Continuing checkout.", null, null);
  }

  if (!await verifyCheckoutDeliveryRecipient(activeJob, checkoutRecipient)) return;

  if (!handledPayment && !checkoutRecipientConfirmed(checkoutRecipient) && !findPlaceOrderButton() && await fillFullName(checkoutRecipient)) {
    await sleep(1200);
    const useAddress = await waitUntil(findUseAddressButton, 8000) || findButtonByText(["use this address", "save address", "deliver to this address", "continue"]);
    if (useAddress) {
      await sleep(1000);
      await clickUseAddressButton(useAddress);
      await sleep(3000);
      await waitUntil(() => checkoutRecipientConfirmed(checkoutRecipient) || findPaymentSelection() || findPlaceOrderButton(), 10000);
    } else if (checkoutAdvancedAfterAddressSave()) {
      showPanel("Nutricity checkout", "Amazon saved the address and advanced to checkout.", null, null);
    } else {
      await pauseForManualCheckout(activeJob, "Could not find the Use this address button.");
      return;
    }
  }

  if (!handledPayment && findPaymentRadio() && await handlePaymentSelection(activeJob)) {
    handledPayment = true;
    // Continue below to place the order if Amazon exposed the final button.
  }
  if (activeJob.paused) return;

  if (!await verifyCheckoutDeliveryRecipient(activeJob, checkoutRecipient)) return;
  const deliverToThisAddress = findDeliverToThisAddressButton();
  if (deliverToThisAddress && !findPaymentRadio() && !findPlaceOrderButton() && !findChangePaymentButton()) {
    const recipientRow = addressRowForRecipient(checkoutRecipient);
    const selectedRow = selectedCheckoutAddressRow();
    if (recipientRow && selectedRow !== recipientRow && !recipientRow.contains(selectedRow)) {
      const recipientControl = addressSelectionControl(recipientRow);
      if (recipientControl) {
        showPanel("Nutricity checkout", "Selecting the recipient delivery address before continuing.", null, null);
        await clickElement(recipientControl, "Recipient address row");
        await sleep(900);
      }
    }
    const selectedAfterClick = selectedCheckoutAddressRow();
    if (recipientRow && selectedAfterClick !== recipientRow && !recipientRow.contains(selectedAfterClick)) {
      await pauseForManualCheckout(activeJob, `Could not select delivery address "${checkoutRecipient}" before continuing.`);
      return;
    }
    if (!recipientRow && selectedAfterClick && !rowIsSafeNutricityAddress(selectedAfterClick)) {
      await pauseForManualCheckout(activeJob, `Could not find delivery address "${checkoutRecipient}" before continuing.`);
      return;
    }
    showPanel("Nutricity checkout", "Delivery address is selected. Continuing to payment review.", null, null);
    await clickElement(deliverToThisAddress, "Deliver to this address button");
    return;
  }
  if (!await ensurePreferredCheckoutPayment(activeJob)) return;
  if (!await ensureWarehouseDeliveryPreferences(activeJob)) return;
  if (!await ensureCheckoutOnlyExpectedUnits(activeJob)) return;
  if (!await ensureSubscribeCheckoutQuantity(activeJob)) return;
  if (!await ensureRewardedLaterDelivery(activeJob)) return;
  if (!await ensureSnsPaymentConfirmation(activeJob)) return;

  let placeOrder = await waitUntil(findPlaceOrderButton, 20000, 500)
    || await waitForElement([
      "input#placeOrder:not([disabled])",
      "input[name='placeYourOrder1']:not([disabled])",
      "input[data-testid='SPC_selectPlaceOrder']:not([disabled])",
      "input[data-csa-c-slot-id='checkout-place-your-order-button']:not([disabled])",
      "input.place-your-order-button:not([disabled])",
    ], 5000)
    || findButtonByText(["place your order"]);
  if (placeOrder && !placeOrder.disabled) {
    if (!await ensureFinalConsolidatedDelivery(activeJob)) return;
    // Selecting a delivery option replaces Amazon's final action block. Never
    // click the pre-selection node, which may now be detached and inert.
    placeOrder = await waitUntil(findPlaceOrderButton, 10000, 250) || findPlaceOrderButton();
    if (!placeOrder || placeOrder.disabled || !placeOrder.isConnected) {
      await pauseForManualCheckout(
        activeJob,
        "Amazon replaced the final checkout controls after delivery selection, but a fresh enabled Place your order control did not appear.",
      );
      return;
    }
    if (!isNativePlaceOrderControl(placeOrder)) {
      await pauseForManualCheckout(
        activeJob,
        "Amazon showed a Place your order wrapper, but the actual submit control could not be verified safely.",
      );
      return;
    }
    if (!await checkoutDeliveryWindowIsAllowed(activeJob)) return;
    if (!checkoutDeliveryRecipientMatches(checkoutRecipient) && !checkoutRecipientConfirmed(checkoutRecipient)) {
      const deliveredTo = checkoutDeliveryRecipientText() || "unknown recipient";
      await pauseForManualCheckout(
        activeJob,
        `Final checkout still shows delivery to "${deliveredTo}" instead of "${checkoutRecipient}".`,
      );
      return;
    }
    if (!checkoutShowsWarehouseAddress()) {
      await pauseForManualCheckout(
        activeJob,
        `Final checkout does not show the Nutricity warehouse address ${DEFAULT_NEW_DELIVERY_ADDRESS.addressLine1}.`,
      );
      return;
    }
    const finalCardPreferences = cardPreferenceList((await getExtensionState()).cardLast4Preference);
    const finalDigits = checkoutSelectedCardDigits();
    if (finalCardPreferences.length && (!finalDigits || !finalCardPreferences.includes(finalDigits))) {
      await pauseForManualCheckout(
        activeJob,
        finalDigits
          ? `Final checkout still shows card ending in ${finalDigits}; expected ${finalCardPreferences.join(" or ")}.`
          : `Final checkout did not show the preferred card ending in ${finalCardPreferences.join(" or ")}.`,
      );
      return;
    }
    const duplicateCheck = await send({ type: "CHECK_EXISTING_AMAZON_ORDER" });
    if (!duplicateCheck?.ok) {
      activeJob.paused = true;
      activeJob.pausedStage = "checkout";
      await setActiveJob(activeJob);
      showPanel(
        "Duplicate check failed",
        duplicateCheck?.message || "Could not confirm whether an Amazon order already exists in the app. Fulfilment is paused before placing the order.",
        "Retry",
        () => continueAfterManualStep(activeJob, "checkout"),
      );
      return;
    }
    if (duplicateCheck?.duplicate) {
      const orders = (duplicateCheck.orders || []).map((item) => item.amazon_order_id).filter(Boolean).join(", ");
      showPanel(
        "Amazon order already exists",
        `${orders || "An Amazon order"} is already saved in the app. Open the extension popup to review it, copy the number, or clear it after confirming the Amazon order was cancelled.`,
        null,
        null,
      );
      return;
    }
    const rememberedOrder = await findRememberedDuplicateOrder(activeJob);
    if (rememberedOrder) {
      showPanel(
        "Amazon order already found",
        `Order history already shows ${rememberedOrder.amazon_order_id} for ${activeJob.job.recipient_name}. Reporting that order instead of placing again.`,
        null,
        null,
      );
      await reportAmazonOrders(activeJob, [rememberedOrder]);
      return;
    }
    if (await pauseBeforeAmazonSubmitIfRequired(
      activeJob,
      "finalPlaceOrderApprovedAt",
      `Amazon checkout is ready for ${activeJobOrderLabel(activeJob) || activeJob.job.group_key}.`,
      "checkout",
    )) return;
    showPanel("Final step", "Clicking Place your order now.", null, null);
    if (!await protectBeforeAmazonSubmit(activeJob, "checkout")) return;
    // The durable pre-submit API call can take long enough for Amazon to
    // replace its final action block. Never click the pre-protection node: a
    // detached input accepts `.click()` without submitting anything, leaving
    // the protected job stuck in order history with no Amazon order.
    placeOrder = await waitUntil(findPlaceOrderButton, 10000, 250) || findPlaceOrderButton();
    if (!placeOrder || placeOrder.disabled || !placeOrder.isConnected || !isNativePlaceOrderControl(placeOrder)) {
      await pauseForManualCheckout(
        activeJob,
        "Amazon replaced the Place your order control while the submission guard was being saved. The protected order remains held and was not clicked.",
        "complete_pending",
      );
      return;
    }
    activeJob.placeOrderClickStartedAt = Date.now();
    activeJob.placeOrderControl = {
      tag: String(placeOrder.tagName || "").toLowerCase(),
      id: String(placeOrder.id || ""),
      name: String(placeOrder.getAttribute?.("name") || ""),
      testid: String(placeOrder.getAttribute?.("data-testid") || ""),
    };
    await setActiveJob(activeJob);
    await sendDiagnostic("Clicking verified native Amazon Place Order control.", {
      group_key: activeJob?.job?.group_key || "",
      control: activeJob.placeOrderControl,
    });
    await clickElement(placeOrder, "Place your order button");
  } else {
    await pauseForManualCheckout(activeJob, "Could not find the payment or Place your order control.", "complete_pending");
  }
}

function extractOrderId() {
  for (const key of ["orderID", "orderId"]) {
    const value = String(new URLSearchParams(location.search).get(key) || "").trim();
    if (/^\d{3}-\d{7}-\d{7}$/.test(value)) return value;
  }
  const text = document.body.innerText || "";
  const patterns = [
    /order(?:\s*#|\s*number|\s*id)?\s*[:#]?\s*([0-9]{3}-[0-9]{7}-[0-9]{7})/i,
    /\b([0-9]{3}-[0-9]{7}-[0-9]{7})\b/,
  ];
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match) return match[1];
  }
  return "";
}

function confirmationSaysPlaced() {
  const text = (document.body.innerText || "").replace(/\s+/g, " ").toLowerCase();
  return text.includes("has been placed") || text.includes("your order") && text.includes("has been placed");
}

function recentOrdersLink() {
  return [...document.querySelectorAll("a")].find((link) => {
    const text = (link.innerText || link.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
    const href = link.getAttribute("href") || "";
    return visible(link) && (text.includes("review or edit your recent orders") || href.includes("order-history"));
  });
}

function orderHistoryUrl() {
  return "https://www.amazon.com/gp/your-account/order-history?ref_=chk_spc_place_order_recovery";
}

function isOrderHistoryPage() {
  const historyUrl = /\/(?:gp\/your-account\/order-history|gp\/css\/order-history|your-orders(?:\/orders?)?)(?:[/?#]|$)/i.test(location.href);
  if (historyUrl) return true;
  if (/\/(?:dp|gp\/product)\//i.test(location.pathname)) return false;
  const hasHistoryLandmark = Boolean(document.querySelector("#yourOrderHistorySection, #yoOrdersTabination"));
  const hasOrderCard = Boolean(document.querySelector("[class*='order-card'], [data-test-id='order-card'], [data-test-id='order-card-header']"));
  const text = normalizedText(document.body?.innerText || document.body?.textContent || "");
  return (hasHistoryLandmark || hasOrderCard) && text.includes("order placed") && text.includes("order #");
}

function isOrderDetailsPage() {
  return /\/(?:your-orders\/order-details|gp\/your-account\/order-details)/i.test(location.pathname)
    || Boolean(new URLSearchParams(location.search).get("orderID") && document.querySelector("#orderDetails"));
}

function isAmazonThankYouPage() {
  return /\/gp\/buy\/thankyou\/handlers\/display\.html/i.test(location.pathname)
    || Boolean(new URLSearchParams(location.search).get("purchaseId") && /\/gp\/buy\/thankyou/i.test(location.pathname));
}

function pageLooksAfterAmazonSubmit() {
  return isAmazonThankYouPage()
    || confirmationSaysPlaced()
    || ((isOrderHistoryPage() || isOrderDetailsPage()) && Boolean(extractOrderId()));
}

async function forceOrderReportingFromSubmittedPage(activeJob, reason = "") {
  const orderId = extractOrderId();
  await sendDiagnostic("Submitted/confirmation page guard took control of the active job.", {
    group_key: activeJob?.job?.group_key || "",
    stage: activeJob?.stage || "",
    paused: Boolean(activeJob?.paused),
    paused_stage: activeJob?.pausedStage || "",
    order_id_on_page: orderId,
    reason,
  }, "warn");
  if (!submittedOrPausedStage(activeJob) && !activeJobWasSubmittedToAmazon(activeJob)) {
    await send({
      type: "MARK_ORDER_SUBMITTED",
      groupKey: activeJob?.job?.group_key || "",
      workerId: activeJob?.workerId || "",
    }).catch((error) => {
      sendDiagnostic("Could not mark job submitted during submitted-page guard.", {
        message: error?.message || String(error || ""),
      }, "warn");
    });
  }
  const next = {
    ...activeJob,
    stage: orderId ? "reporting_complete" : "find_order_id",
    paused: false,
    pausedStage: null,
    amazonSubmittedAt: activeJob.amazonSubmittedAt || Date.now(),
    amazonConfirmationUrl: activeJob.amazonConfirmationUrl || location.href,
  };
  await setActiveJob(next, { allowUnpause: true, reason: "submitted_page_guard" });
  if (orderId) {
    next.stage = "find_order_id";
    next.orderHistoryLookupStartedAt = next.orderHistoryLookupStartedAt || Date.now();
    await setActiveJob(next, { allowUnpause: true, reason: "submitted_page_history_verification" });
    showPanel("Nutricity fulfilment", `Found Amazon order ${orderId}. Verifying recipient and ASINs in order history before reporting.`, null, null);
    location.href = orderHistoryUrl();
    return true;
  }
  showPanel("Nutricity fulfilment", "Amazon shows the order was submitted. Opening order history to capture the order number.", null, null);
  await handleOrderHistory(next);
  return true;
}

async function guardUnexpectedAmazonPage(activeJob) {
  if (
    isAmazonThankYouPage() ||
    confirmationSaysPlaced() ||
    ((submittedOrPausedStage(activeJob) || activeJobWasSubmittedToAmazon(activeJob)) && pageLooksAfterAmazonSubmit())
  ) {
    await forceOrderReportingFromSubmittedPage(activeJob, "Amazon confirmation URL or placed-order text detected.");
    return true;
  }
  // A failed job is intentionally sent through order history before its cart
  // cleanup starts. Let that narrowly scoped recovery state reach
  // handleFailureCleanup; all normal pre-submit jobs remain protected below.
  if (
    (isOrderHistoryPage() || isOrderDetailsPage())
    && activeJob?.stage === "cleanup_after_failure"
    && activeJob?.cleanupAfterFailure === true
  ) {
    return false;
  }
  if ((isOrderHistoryPage() || isOrderDetailsPage()) && !submittedOrPausedStage(activeJob) && !activeJobWasSubmittedToAmazon(activeJob)) {
    activeJob.paused = true;
    activeJob.pausedStage = activeJob.stage || "product";
    activeJob.pageGuardPausedAt = Date.now();
    await setActiveJob(activeJob, { reason: "unexpected_order_history_page" });
    await sendDiagnostic("Paused because active job is on order-history/details page before Amazon submit was protected.", {
      group_key: activeJob?.job?.group_key || "",
      stage: activeJob.stage || "",
    }, "warn");
    showPanel(
      "Nutricity fulfilment paused",
      "This Amazon page is order history/details, but the active job is not marked submitted. The extension will not clear cart, add products, or start another order from this page.",
      null,
      null,
    );
    return true;
  }
  if ((submittedOrPausedStage(activeJob) || activeJobWasSubmittedToAmazon(activeJob)) && /\/cart/i.test(location.pathname)) {
    await sendDiagnostic("Submitted job landed on cart; redirecting to order history instead of clearing cart.", {
      group_key: activeJob?.job?.group_key || "",
      stage: activeJob.stage || "",
    }, "warn");
    activeJob.stage = "find_order_id";
    activeJob.amazonSubmittedAt = activeJob.amazonSubmittedAt || Date.now();
    await setActiveJob(activeJob, { allowUnpause: true, reason: "submitted_cart_guard" });
    location.href = orderHistoryUrl();
    return true;
  }
  return false;
}

function submittedStage(activeJob) {
  return ["complete_pending", "find_order_id", "reporting_complete"].includes(String(activeJob?.stage || ""));
}

function submittedOrPausedStage(activeJob) {
  return submittedStage(activeJob) || ["complete_pending", "find_order_id", "reporting_complete"].includes(String(activeJob?.pausedStage || ""));
}

function activeJobWasSubmittedToAmazon(activeJob) {
  const job = activeJob?.job || {};
  if (job.submitted_to_amazon || ["order_submitted", "reporting_complete"].includes(String(job.amazon_status || ""))) return true;
  return (job.items || []).some((item) => ["order_submitted", "reporting_complete"].includes(String(item.amazon_status || "")));
}

function amazonOrderSubmitErrorPage() {
  const text = normalizedText(document.body?.innerText || document.body?.textContent || "");
  const title = normalizedText(document.title || "");
  return /\/checkout\/.*\/place-order/i.test(location.pathname)
    && (
      title.includes("500") ||
      title.includes("error occurred") ||
      text.includes("500") ||
      text.includes("an error occurred") ||
      text.includes("something went wrong") ||
      text.includes("please go back and try again")
    );
}

function amazonPostSubmitUnplacedIssue() {
  if (!/\/checkout\/.*\/place-order/i.test(location.pathname)) return "";
  const text = normalizedText(document.body?.innerText || document.body?.textContent || "");
  if (!text) return "";
  if (
    text.includes("you cannot buy this item because it's out of stock")
    || text.includes("you cannot buy this item because it is out of stock")
    || (text.includes("out of stock") && text.includes("another seller might have a comparable offer"))
    || text.includes("item is no longer available")
    || text.includes("this item is no longer available")
  ) {
    return "Amazon showed the item is out of stock after Place Order. No Amazon order was placed.";
  }
  return "";
}

function amazonDuplicateOrderRoute() {
  return /\/checkout\/.*\/duplicateOrder/i.test(location.pathname);
}

function amazonDuplicateOrderPage() {
  const text = normalizedText(document.body?.innerText || document.body?.textContent || "");
  return amazonDuplicateOrderRoute()
    || (text.includes("this is a pending order") && text.includes("do you want to place the same order again"));
}

async function protectBeforeAmazonSubmit(activeJob, retryStage = "checkout") {
  const result = await send({
    type: "MARK_ORDER_SUBMITTED",
    groupKey: activeJob?.job?.group_key || "",
    workerId: activeJob?.workerId || "",
  });
  if (!result?.ok) {
    if (result?.duplicate_submit_blocked && result?.protected_in_current_window) {
      // The previous click in this same worker was already protected.  It may
      // have navigated while the content script was reloaded, so verify Amazon
      // order history instead of turning our own duplicate guard into a pause.
      const latest = await getActiveJob();
      const recovery = {
        ...(latest || activeJob),
        stage: "find_order_id",
        paused: false,
        pausedStage: null,
        amazonSubmittedAt: (latest || activeJob)?.amazonSubmittedAt || Date.now(),
      };
      await setActiveJob(recovery, { allowUnpause: true, reason: "protected_submit_history_recovery" });
      showPanel("Nutricity fulfilment", "A protected Place Order attempt already exists. Checking recent Amazon orders instead of submitting again.", null, null);
      await handleOrderHistory(recovery);
      return false;
    }
    activeJob.paused = true;
    activeJob.pausedStage = retryStage;
    await setActiveJob(activeJob);
    showPanel(
      "Nutricity fulfilment paused",
      result?.message || "Could not protect this order before clicking Amazon Place your order. Fulfilment is paused to prevent a duplicate order.",
      "Retry",
      () => continueAfterManualStep(activeJob, retryStage),
    );
    return false;
  }
  activeJob.amazonSubmittedAt = activeJob.amazonSubmittedAt || Date.now();
  activeJob.stage = "complete_pending";
  activeJob.paused = false;
  activeJob.pausedStage = null;
  await setActiveJob(activeJob);
  return true;
}

async function handleAmazonDuplicateOrderPage(activeJob) {
  if (activeJob.amazonDuplicateOrderConfirmed) {
    await pauseForManualCheckout(
      activeJob,
      "Amazon is still showing the duplicate pending-order screen after it was already confirmed once.",
      "complete_pending",
    );
    return;
  }
  const duplicatePageIsAfterCheckoutSubmit = Boolean(
    activeJob.amazonSubmittedAt
    || activeJob.placeOrderClickStartedAt
    || submittedStage(activeJob)
  );
  if (!duplicatePageIsAfterCheckoutSubmit) {
    activeJob.paused = true;
    activeJob.pausedStage = activeJob.stage || "checkout";
    await setActiveJob(activeJob);
    showPanel(
      "Nutricity fulfilment paused",
      "Amazon showed the duplicate pending-order screen before the final order stage. Review it manually before continuing.",
      "I did it manually, continue",
      () => continueAfterManualStep(activeJob, "complete_pending"),
    );
    return;
  }
  activeJob.paused = false;
  activeJob.pausedStage = null;
  const duplicateCheck = await send({ type: "CHECK_EXISTING_AMAZON_ORDER" });
  if (duplicateCheck?.duplicate) {
    const orders = (duplicateCheck.orders || []).map((item) => item.amazon_order_id).filter(Boolean).join(", ");
    showPanel(
      "Amazon order already exists",
      `${orders || "An Amazon order"} is already saved in the app. Open the extension popup to review or clear it before continuing.`,
      null,
      null,
    );
    return;
  }
  const rememberedOrder = await findRememberedDuplicateOrder(activeJob);
  if (rememberedOrder) {
    showPanel(
      "Amazon order already found",
      `Order history already shows ${rememberedOrder.amazon_order_id} for ${activeJob.job.recipient_name}. Reporting that order instead of confirming the duplicate-order page.`,
      null,
      null,
    );
    await reportAmazonOrders(activeJob, [rememberedOrder]);
    return;
  }
  showPanel("Final step", "Amazon opened a pending duplicate-order confirmation. Waiting for its final Place your order button.", null, null);
  const placeOrder = await waitUntil(() => {
    if (!amazonDuplicateOrderRoute() && !amazonDuplicateOrderPage()) return null;
    const control = findPlaceOrderButton() || findButtonByText(["place your order"]);
    return control && !control.disabled ? control : null;
  }, 15000, 250);
  if (!placeOrder || placeOrder.disabled) {
    await pauseForManualCheckout(activeJob, "Amazon duplicate pending-order screen did not show a usable Place your order button.", "complete_pending");
    return;
  }
  if (await pauseBeforeAmazonSubmitIfRequired(
    activeJob,
    "duplicatePlaceOrderApprovedAt",
    `Amazon is asking to confirm a duplicate pending order for ${activeJobOrderLabel(activeJob) || activeJob.job.group_key}.`,
    "complete_pending",
  )) return;
  showPanel("Final step", "Amazon asked for duplicate-order confirmation. Clicking Place your order once.", null, null);
  // The original checkout Place Order click was protected already. This page is
  // Amazon's continuation of that same submission; protecting it again invokes
  // our duplicate-submit guard and incorrectly diverts the worker to history.
  activeJob.amazonDuplicateOrderConfirmed = true;
  activeJob.placeOrderClickStartedAt = Date.now();
  activeJob.stage = "complete_pending";
  await setActiveJob(activeJob);
  await clickElement(placeOrder, "duplicate-order Place your order button");
}

function orderDetailsUrl(orderId) {
  return `https://www.amazon.com/your-orders/order-details?orderID=${encodeURIComponent(orderId)}`;
}

function amazonSignedInAccountName() {
  const candidates = [
    "#nav-link-accountList-nav-line-1",
    "#nav-link-accountList .nav-line-1",
    "span.nav-line-1",
  ];
  for (const selector of candidates) {
    const element = [...document.querySelectorAll(selector)].find((node) => visible(node) && /hello,/i.test(node.textContent || ""));
    const text = (element?.textContent || "").replace(/\s+/g, " ").trim();
    const match = text.match(/^hello,\s*(.+)$/i);
    if (match?.[1]) return match[1].trim();
  }
  return "";
}

function extractRecentOrderId(activeJob) {
  return extractRecentOrders(activeJob)[0]?.amazon_order_id || "";
}

function orderCardOrderId(card) {
  const dataId = String(card?.getAttribute?.("data-order-id") || card?.dataset?.orderId || "").trim();
  if (/^\d{3}-\d{7}-\d{7}$/.test(dataId)) return dataId;
  const orderLink = [...(card?.querySelectorAll?.("a[href*='orderID='], a[href*='order-details']") || [])]
    .map((link) => orderIdFromAmazonOrderHref(link.href || link.getAttribute("href") || ""))
    .find(Boolean);
  if (orderLink) return orderLink;
  const headerText = (card.querySelector("#orderCardHeader, [id*='orderCardHeader'], [data-test-id*='order-header']")?.innerText || card.innerText || card.textContent || "").replace(/\s+/g, " ");
  const match = headerText.match(/\b\d{3}-\d{7}-\d{7}\b/);
  return match ? match[0] : "";
}

function nearViewport(element, margin = 900) {
  if (!element) return false;
  const rect = element.getBoundingClientRect();
  return rect.bottom >= -margin
    && rect.right >= -margin
    && rect.top <= (window.innerHeight || document.documentElement.clientHeight || 0) + margin
    && rect.left <= (window.innerWidth || document.documentElement.clientWidth || 0) + margin;
}

function orderIdsFromOrderHistoryText(text = "") {
  return [...new Set((String(text || "").match(/\b\d{3}-\d{7}-\d{7}\b/g) || []).map((value) => value.trim()))];
}

function orderIdFromAmazonOrderHref(href = "") {
  const value = String(href || "");
  try {
    const parsed = new URL(value, location.href);
    const fromQuery = parsed.searchParams.get("orderID") || parsed.searchParams.get("orderId") || "";
    if (/^\d{3}-\d{7}-\d{7}$/.test(fromQuery)) return fromQuery;
  } catch (_) {
    // Fall back to the regexp below for Amazon's relative or escaped URLs.
  }
  return value.match(/\b\d{3}-\d{7}-\d{7}\b/)?.[0] || "";
}

function orderCardAsins(card) {
  return orderCardItems(card).map((item) => item.asin);
}

function asinFromAmazonHref(href = "") {
  const match = String(href || "").match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})/i);
  return match ? match[1].toUpperCase() : "";
}

function orderCardItems(card) {
  const quantitiesByAsin = {};
  const itemRows = [...card.querySelectorAll("#itemDetail, .itemDetails")].filter((row) => row.querySelector("a[href*='/dp/'], a[href*='/gp/product/']"));
  if (!itemRows.length) {
    for (const link of card.querySelectorAll("a[href*='/dp/'], a[href*='/gp/product/']")) {
      const asin = asinFromAmazonHref(link.href || link.getAttribute("href") || "");
      if (asin && !quantitiesByAsin[asin]) quantitiesByAsin[asin] = 1;
    }
    return Object.entries(quantitiesByAsin).map(([asin, quantity]) => ({ asin, quantity, quantity_verified: true }));
  }
  const rows = itemRows;
  for (const row of rows) {
    const link = row.matches?.("a[href*='/dp/'], a[href*='/gp/product/']")
      ? row
      : row.querySelector("a[href*='/dp/'], a[href*='/gp/product/']");
    const asin = asinFromAmazonHref(link?.href || link?.getAttribute?.("href") || "");
    if (!asin) continue;
    const quantityInfo = quantityFromItemElement(itemContainerForQuantity(link, asin));
    const existing = quantitiesByAsin[asin] || { quantity: 0, quantity_verified: false };
    if (!existing.quantity_verified || quantityInfo.quantity_verified) {
      existing.quantity = Math.max(Number(existing.quantity || 0), quantityInfo.quantity);
    }
    existing.quantity_verified = existing.quantity_verified || quantityInfo.quantity_verified;
    quantitiesByAsin[asin] = existing;
  }
  return Object.entries(quantitiesByAsin).map(([asin, value]) => ({ asin, ...value }));
}

function quantityFromText(text = "") {
  const match = String(text || "").match(/\b(?:qty|quantity)\s*:?\s*(\d+)\b/i);
  return Math.max(1, Math.round(Number(match?.[1] || 1)));
}

function itemContainerForQuantity(link, targetAsin = "") {
  const normalizedTarget = String(targetAsin || asinFromAmazonHref(link?.href || link?.getAttribute?.("href") || "")).toUpperCase();
  let node = link?.parentElement || null;
  for (let depth = 0; node && depth < 10; depth += 1, node = node.parentElement) {
    const quantities = node.querySelectorAll?.(".od-item-view-qty, .itemQuantity, [data-quantity]") || [];
    const productAsins = new Set(
      [...(node.querySelectorAll?.("a[href*='/dp/'], a[href*='/gp/product/']") || [])]
        .map((candidate) => asinFromAmazonHref(candidate.href || candidate.getAttribute?.("href") || ""))
        .filter(Boolean),
    );
    if (quantities.length === 1 && productAsins.size === 1 && productAsins.has(normalizedTarget)) return node;
    // Never climb into a shipment/order wrapper containing sibling ASINs. A
    // quantity bubble there may belong to a different product.
    if (productAsins.size > 1) return null;
  }
  return null;
}

function quantityFromItemElement(element) {
  if (!element) return { quantity: 1, quantity_verified: false };
  const quantityNode = element?.querySelector?.(".od-item-view-qty, .itemQuantity, [data-quantity]");
  const attributeValue = quantityNode?.getAttribute?.("data-quantity") || element?.getAttribute?.("data-quantity") || "";
  const visibleValue = quantityNode?.textContent || "";
  const explicit = String(attributeValue || visibleValue).match(/\d+/)?.[0];
  if (explicit) {
    return { quantity: Math.max(1, Math.round(Number(explicit) || 1)), quantity_verified: true };
  }
  // Amazon omits the quantity badge for a single unit. Once this product row
  // is isolated to one ASIN, absence of the badge is an explicit quantity 1.
  return { quantity: 1, quantity_verified: true };
}

function orderDetailsPageOrderId() {
  const fromUrl = String(new URLSearchParams(location.search).get("orderID") || "").trim();
  if (/^\d{3}-\d{7}-\d{7}$/.test(fromUrl)) return fromUrl;
  const text = (document.querySelector("#orderDetails")?.innerText || document.body?.innerText || "").replace(/\s+/g, " ");
  return text.match(/\b\d{3}-\d{7}-\d{7}\b/)?.[0] || "";
}

function orderDetailsPageRoot() {
  return document.querySelector("#orderDetails") || document.body;
}

function orderDetailsPageRecipient() {
  const text = (orderDetailsPageRoot()?.innerText || "").replace(/\s+/g, " ").trim();
  const refMatch = text.match(/\bNutricity\s+[A-Z]{2,5}\d{2,}(?:\s+[A-Za-z0-9]+){0,3}/i);
  if (refMatch?.[0]) return refMatch[0].replace(/\s+/g, " ").trim();
  const shipMatch = text.match(/\bShip to\s+(.+?)\s+\d{2,}/i);
  return shipMatch?.[1]?.replace(/\s+/g, " ").trim() || "";
}

function orderDetailsPageStatus() {
  const root = orderDetailsPageRoot();
  const statuses = [...root.querySelectorAll("#shipment-top-row, [id*='shipment'] .a-size-medium, .a-size-medium .a-text-bold, .a-size-medium")]
    .map((node) => (node.textContent || "").replace(/\s+/g, " ").trim())
    .filter(Boolean)
    .filter((text) => !/order details|order summary|invoice|payment method/i.test(text));
  return statuses.find((text) => /arriv|deliver|ship|cancel/i.test(text)) || statuses[0] || "";
}

function orderDetailsPageDate() {
  const text = (orderDetailsPageRoot()?.innerText || "").replace(/\s+/g, " ").trim();
  return text.match(/Order placed\s+([A-Z][a-z]+ \d{1,2}, \d{4})/)?.[1] || "";
}

function orderDetailsPageItems() {
  const root = orderDetailsPageRoot();
  const quantitiesByAsin = {};
  const purchasedLinks = [...root.querySelectorAll("a[href*='/dp/'], a[href*='/gp/product/']")]
    .filter((link) => {
      const href = String(link.href || link.getAttribute("href") || "");
      return asinFromAmazonHref(href) && /ppx_hzod|ppx_yo_dt|fed_asin_title/i.test(href);
    });
  for (const link of purchasedLinks) {
    const asin = asinFromAmazonHref(link.href || link.getAttribute("href") || "");
    if (!asin) continue;
    const row = itemContainerForQuantity(link, asin);
    const quantityInfo = quantityFromItemElement(row);
    const existing = quantitiesByAsin[asin] || { quantity: 0, quantity_verified: false };
    if (!existing.quantity_verified || quantityInfo.quantity_verified) {
      existing.quantity = Math.max(Number(existing.quantity || 0), quantityInfo.quantity);
    }
    existing.quantity_verified = existing.quantity_verified || quantityInfo.quantity_verified;
    quantitiesByAsin[asin] = existing;
  }
  return Object.entries(quantitiesByAsin).map(([asin, value]) => ({ asin, ...value }));
}

function orderDetailsPageDetails() {
  const orderId = orderDetailsPageOrderId();
  if (!orderId) return null;
  const items = orderDetailsPageItems();
  return {
    amazon_order_id: orderId,
    amazon_order_url: orderDetailsUrl(orderId),
    recipient: orderDetailsPageRecipient(),
    order_date: orderDetailsPageDate(),
    status: orderDetailsPageStatus(),
    asins: items.map((item) => item.asin),
    items,
    cancelled: /cancelled/i.test(orderDetailsPageStatus()),
    captured_at: Date.now(),
  };
}

function recipientFromOrderHistoryText(text = "") {
  let value = String(text || "").replace(/\s+/g, " ").trim();
  // Amazon's a-truncate-full node can contain both its visible and hidden
  // recipient copies without whitespace (for example
  // "Nutricity NC23225Nutricity NC23225"). Collapse an exact repeated half
  // before parsing so a correct newest order card remains matchable.
  if (value.length % 2 === 0) {
    const midpoint = value.length / 2;
    if (value.slice(0, midpoint) === value.slice(midpoint)) value = value.slice(0, midpoint);
  }
  // Recipient suffixes can contain decimal product strengths such as
  // "2.5mg". Preserve punctuation here: collapsing "NC18106 2" into
  // "NC181062" breaks the exact-recipient guard and leaves a correct
  // submitted order stuck in history recovery.
  const nutricityMatch = value.match(/\bNutricity\s+[A-Z]{2,5}\d{2,}(?:\s+[A-Za-z0-9][A-Za-z0-9._-]*){0,4}/i);
  if (nutricityMatch?.[0]) {
    return nutricityMatch[0]
      .replace(/\b(?:Order|Placed|Total|Ship|To|View|Buy|Again|Invoice|Details)\b.*$/i, "")
      .replace(/\s+/g, " ")
      .trim();
  }
  const shipMatch = value.match(/\bShip\s+to\s+(.+?)(?:\s+\b(?:Order placed|Order #|Total|Buy it again|View order details|Invoice|Delivered|Arriving|Cancelled)\b|$)/i);
  return shipMatch?.[1]?.replace(/\s+/g, " ").trim() || "";
}

function orderCardRecipient(card) {
  const orderId = orderCardOrderId(card);
  const selectors = [
    ".shipToTriggerTextTruncate .a-truncate-cut",
    ".shipToTriggerTextTruncate .a-truncate-full",
    "[data-a-popover*='PreloadedContent_'] .shipToTriggerTextTruncate .a-truncate-full",
    "[data-a-popover*='PreloadedContent_'] .shipToTriggerTextTruncate .a-truncate-cut",
    ".shipToTriggerTextTruncate",
    "[id^='a-popover-PreloadedContent_'] .a-text-bold",
    "[data-test-id*='recipient']",
    "[data-test-id*='ship']",
  ];
  for (const selector of selectors) {
    const text = [...card.querySelectorAll(selector)]
      .map((node) => (node.textContent || "").replace(/\s+/g, " ").trim())
      .map((text) => recipientFromOrderHistoryText(text) || text)
      .find(Boolean);
    if (text) return text;
  }
  if (orderId) {
    const preloaded = document.getElementById(`a-popover-PreloadedContent_${orderId}`);
    const popoverText = [...(preloaded?.querySelectorAll?.(".a-text-bold") || [])]
      .map((node) => recipientFromOrderHistoryText(node.textContent || "") || (node.textContent || "").replace(/\s+/g, " ").trim())
      .find(Boolean);
    if (popoverText) return popoverText;
  }
  return recipientFromOrderHistoryText(card.innerText || card.textContent || "");
}

function orderCardDate(card) {
  const headerText = (card.querySelector("#orderCardHeader, [id*='orderCardHeader'], [data-test-id*='order-header']")?.innerText || card.innerText || card.textContent || "").replace(/\s+/g, " ").trim();
  const match = headerText.match(/Order placed\s+([A-Z][a-z]+ \d{1,2}, \d{4})/);
  return match?.[1] || "";
}

function orderCardStatus(card) {
  const statuses = [...card.querySelectorAll("#orderCardDeliveryBox .a-size-medium .a-text-bold, #orderCardDeliveryBox .a-size-medium, [data-test-id*='status'], [class*='delivery'] .a-size-medium, .a-size-medium .a-text-bold, .a-size-medium")]
    .map((node) => (node.textContent || "").replace(/\s+/g, " ").trim())
    .filter(Boolean)
    .filter((text) => !/order placed|order #|total|ship to|buy it again|view order details|invoice/i.test(text));
  return statuses.find((text) => /arriv|deliver|ship|cancel|refund|return/i.test(text)) || statuses[0] || "";
}

function isCancelledOrderCard(card) {
  return /^cancelled$/i.test(orderCardStatus(card)) || /\bCancelled\b/.test(orderCardStatus(card));
}

function orderHistoryCardCandidates() {
  const linkCards = orderHistoryCardsFromOrderLinks();
  if (linkCards.length) return linkCards.slice(0, MAX_ORDER_HISTORY_CARDS_PER_PASS);
  const exactSelectors = [
    "#yourOrderHistorySection #orderCard",
    "#orderCard",
    ".order-card",
    ".js-order-card",
    "[data-order-id]",
    "[data-test-id*='order-card']",
  ];
  const root = document.querySelector("#yourOrderHistorySection") || document;
  const exact = dedupeOrderHistoryCards([...root.querySelectorAll(exactSelectors.join(", "))])
    .filter((element) => visible(element) && orderCardOrderId(element));
  if (exact.length) return exact.slice(0, MAX_ORDER_HISTORY_CARDS_PER_PASS);
  const headers = [...root.querySelectorAll("#orderCardHeader, [data-test-id*='order-header']")]
    .filter(visible);
  const cards = [];
  const seen = new Set();
  for (const header of headers) {
    let card = header.closest(".a-box-group, .order-card, [class*='order-card'], [id*='orderCard']");
    let parent = card || header.parentElement;
    for (let depth = 0; parent && depth < 5; depth += 1, parent = parent.parentElement) {
      const text = (parent.innerText || parent.textContent || "").replace(/\s+/g, " ");
      if (/\bOrder placed\b/i.test(text) && /\b\d{3}-\d{7}-\d{7}\b/.test(text) && /\b(?:Arriving|Delivered|Cancelled|Buy it again)\b/i.test(text)) {
        card = parent;
      }
    }
    card = card || header;
    if (!seen.has(card) && visible(card)) {
      seen.add(card);
      cards.push(card);
      if (cards.length >= MAX_ORDER_HISTORY_CARDS_PER_PASS) break;
    }
  }
  if (cards.length) return cards;
  return [];
}

function dedupeOrderHistoryCards(cards = []) {
  const result = [];
  const seen = new Set();
  for (const card of cards) {
    if (!card || seen.has(card)) continue;
    if (result.some((existing) => existing.contains(card) && orderCardOrderId(existing) === orderCardOrderId(card))) continue;
    for (let index = result.length - 1; index >= 0; index -= 1) {
      const existing = result[index];
      if (card.contains(existing) && orderCardOrderId(existing) === orderCardOrderId(card)) {
        seen.delete(existing);
        result.splice(index, 1);
      }
    }
    seen.add(card);
    result.push(card);
  }
  return result;
}

function orderHistoryCardsFromOrderLinks() {
  const links = [...document.querySelectorAll("a[href*='orderID='], a[href*='order-details']")]
    .filter((link) => visible(link) && orderIdFromAmazonOrderHref(link.href || link.getAttribute("href") || ""))
    .slice(0, MAX_ORDER_HISTORY_CARDS_PER_PASS * 3);
  const cards = [];
  const seenIds = new Set();
  for (const link of links) {
    const orderId = orderIdFromAmazonOrderHref(link.href || link.getAttribute("href") || "");
    if (!orderId || seenIds.has(orderId)) continue;
    let best = link.closest(".a-box-group, .order-card, .js-order-card, [class*='order-card'], [data-order-id], [data-test-id*='order-card']");
    let parent = best || link.parentElement;
    for (let depth = 0; parent && depth < 8; depth += 1, parent = parent.parentElement) {
      const text = (parent.innerText || parent.textContent || "").replace(/\s+/g, " ").trim();
      const ids = orderIdsFromOrderHistoryText(text);
      if (
        ids.includes(orderId)
        && ids.length === 1
        && text.length < 12000
        && (
          /\bOrder placed\b/i.test(text)
          || /\bOrder #\b/i.test(text)
          || /\b(?:Arriving|Delivered|Cancelled|Buy it again|View order details)\b/i.test(text)
        )
      ) {
        best = parent;
      }
      if (
        ids.includes(orderId)
        && ids.length === 1
        && text.length < 16000
        && (/\bShip\s+to\b/i.test(text) || /\bNutricity\s+NC\d+\b/i.test(text))
      ) {
        best = parent;
      }
    }
    best = best || link.parentElement || link;
    if (visible(best)) {
      seenIds.add(orderId);
      cards.push(best);
      if (cards.length >= MAX_ORDER_HISTORY_CARDS_PER_PASS) break;
    }
  }
  return dedupeOrderHistoryCards(cards);
}

function extractOrderHistoryOrders() {
  const candidates = orderHistoryCardCandidates();
  const seen = new Set();
  const orders = [];
  for (const [historyRank, card] of candidates.entries()) {
    const orderId = orderCardOrderId(card);
    if (!orderId || seen.has(orderId) || isCancelledOrderCard(card)) continue;
    seen.add(orderId);
    orders.push({
      amazon_order_id: orderId,
      amazon_order_url: orderDetailsUrl(orderId),
      recipient: orderCardRecipient(card),
      order_date: orderCardDate(card),
      status: orderCardStatus(card),
      asins: orderCardAsins(card),
      items: orderCardItems(card),
      // Amazon lists newest cards first. Keep this evidence so a re-placed
      // order can be distinguished from an earlier order for the same
      // recipient and ASIN without ever falling back to an unrelated card.
      history_rank: historyRank,
      captured_at: Date.now(),
    });
    if (orders.length >= 10) break;
  }
  return orders;
}

function visibleOrderHistoryCards() {
  return orderHistoryCardCandidates();
}

function orderHistoryAnnotationIsComplete(card) {
  const annotations = [...(card?.querySelectorAll?.(".nutricity-order-history-annotation") || [])];
  if (!annotations.length) return false;
  const text = annotations.map((annotation) => annotation.textContent || "").join(" ");
  if (isTransientOrderHistoryLookupError(text)) return false;
  if (/not found in app/i.test(text)) {
    const newestLookupAt = Math.max(
      ...annotations.map((annotation) => Number(annotation.dataset.lookupAt || 0)),
      0,
    );
    return newestLookupAt > 0 && Date.now() - newestLookupAt < ORDER_HISTORY_NOT_FOUND_CACHE_MS;
  }
  return !/checking app match/i.test(text);
}

function orderHistoryLookupHasTarget(result = {}) {
  return Boolean(
    (result.match?.orders || []).length
    || (result.suggestions || []).length
    || (result.conflicts || []).length
    || (result.odooDirect || []).length
  );
}

function orderHistoryLookupCacheFresh(result = {}) {
  const age = Date.now() - Number(result.cachedAt || 0);
  const ttl = result.unmatched && !orderHistoryLookupHasTarget(result)
    ? ORDER_HISTORY_NOT_FOUND_CACHE_MS
    : ORDER_HISTORY_CACHE_MS;
  return age >= 0 && age < ttl;
}

function orderHistoryNeedsMoreAnnotation() {
  if (document.hidden || !isOrderHistoryPage()) return false;
  return visibleOrderHistoryCards().some((card) => {
    const details = orderHistoryCardDetails(card);
    if (!details?.amazon_order_id) return false;
    const cached = orderHistoryLookupCache.get(details.amazon_order_id);
    if (cached && orderHistoryLookupCacheFresh(cached) && cached.odooDirectLoaded) return false;
    return !orderHistoryAnnotationIsComplete(card);
  });
}

function isTransientOrderHistoryLookupError(errorOrMessage) {
  const message = String(errorOrMessage?.message || errorOrMessage || "");
  return /abort|timed out|receiving end does not exist|extension context|could not establish connection|message channel closed|asynchronous response|networkerror|failed to fetch/i.test(message);
}

function orderHistoryCardDetails(card) {
  const orderId = orderCardOrderId(card);
  if (!orderId) return null;
  return {
    amazon_order_id: orderId,
    amazon_order_url: orderDetailsUrl(orderId),
    recipient: orderCardRecipient(card),
    order_date: orderCardDate(card),
    status: orderCardStatus(card),
    asins: orderCardAsins(card),
    items: orderCardItems(card),
    cancelled: isCancelledOrderCard(card),
    captured_at: Date.now(),
  };
}

function orderHistoryAnnotationContainer(card) {
  if (card?.dataset?.nutricityAnnotationHost === "true") return card;
  return card.querySelector("#orderCardHeader .a-box-inner") || card.querySelector("#orderCardHeader") || card;
}

function renderOrderHistoryPendingAnnotation(card, details) {
  if (!card) return;
  const existing = [...card.querySelectorAll(".nutricity-order-history-annotation")];
  if (existing.length) {
    const existingText = existing.map((annotation) => annotation.textContent || "").join(" ");
    if (!isTransientOrderHistoryLookupError(existingText) && !/checking app match/i.test(existingText)) return;
    for (const annotation of existing) annotation.remove();
  }
  const marker = document.createElement("div");
  marker.className = "nutricity-order-history-annotation is-suggestion";
  const label = document.createElement("span");
  label.className = "nutricity-order-history-label";
  label.textContent = "Nutricity";
  const text = document.createElement("span");
  text.className = "nutricity-order-history-links";
  text.textContent = `Checking app match for ${details?.amazon_order_id || "Amazon order"}...`;
  marker.append(label, text);
  orderHistoryAnnotationContainer(card).appendChild(marker);
}

function renderOrderHistoryLookupError(card, details, message = "") {
  if (!card) return;
  for (const existing of card.querySelectorAll(".nutricity-order-history-annotation")) existing.remove();
  const marker = document.createElement("div");
  marker.className = "nutricity-order-history-annotation is-conflict";
  const label = document.createElement("span");
  label.className = "nutricity-order-history-label";
  label.textContent = "Lookup failed";
  const text = document.createElement("span");
  text.className = "nutricity-order-history-warning";
  text.textContent = message || `Could not check ${details?.amazon_order_id || "this Amazon order"} in the app.`;
  marker.append(label, text);
  orderHistoryAnnotationContainer(card).appendChild(marker);
}

function orderDetailsAnnotationHost() {
  const root = orderDetailsPageRoot();
  let anchor = root.querySelector(".nutricity-order-details-anchor");
  if (!anchor) {
    anchor = document.createElement("div");
    anchor.className = "nutricity-order-details-anchor";
    const detailsBox = root.querySelector("[data-component='shippingAddress']")?.closest(".a-box-inner")
      || root.querySelector("[data-component='chargeSummary']")?.closest(".a-box-inner");
    const header = [...root.querySelectorAll(".a-section, .a-row, .a-box")]
      .find((node) => {
        const text = (node.innerText || node.textContent || "").replace(/\s+/g, " ").trim();
        return /\bOrder placed\b/i.test(text) && /\bOrder #\b/i.test(text);
      });
    const target = detailsBox || header || root.firstElementChild;
    if (target?.parentElement) {
      target.parentElement.insertBefore(anchor, detailsBox ? target : target.nextSibling);
    } else {
      root.insertBefore(anchor, root.firstChild);
    }
  }
  anchor.dataset.nutricityAnnotationHost = "true";
  anchor.dataset.nutricityOrderDetailsHost = "true";
  return anchor;
}

function renderOrderHistoryAnnotation(card, result) {
  for (const existing of card.querySelectorAll(".nutricity-order-history-annotation")) existing.remove();
  if (!result) return;
  const displayResult = filterOrderHistoryResultForCard(result);
  const orders = displayResult.match?.orders || [];
  const suggestions = displayResult.suggestions || [];
  const conflicts = displayResult.conflicts || [];
  const directOdoo = displayResult.odooDirect || [];
  const cancelled = result.cancelled === true;
  if (cancelled && !orders.length) return;
  if (!orders.length && !suggestions.length && !conflicts.length && !directOdoo.length && !displayResult.unmatched) return;
  const syncedConfirmation = orderHistorySyncedConfirmations.get(displayResult.orderId);
  const detailsClass = card?.dataset?.nutricityOrderDetailsHost === "true" ? " is-order-details" : "";
  if (syncedConfirmation && Date.now() - Number(syncedConfirmation.syncedAt || 0) < 30000) {
    const marker = document.createElement("div");
    marker.className = `nutricity-order-history-annotation is-synced${detailsClass}`;
    const label = document.createElement("span");
    label.className = "nutricity-order-history-label";
    label.textContent = "Synced";
    const text = document.createElement("span");
    text.className = "nutricity-order-history-links";
    text.textContent = syncedConfirmation.message || `Amazon ${displayResult.orderId} synced`;
    marker.append(label, text);
    orderHistoryAnnotationContainer(card).appendChild(marker);
    return;
  }
  const syncStartedAt = orderHistorySyncInProgress.get(displayResult.orderId);
  let syncInProgress = false;
  if (syncStartedAt) {
    syncInProgress = Date.now() - Number(syncStartedAt) < 120000;
    if (!syncInProgress) orderHistorySyncInProgress.delete(displayResult.orderId);
  }
  const marker = document.createElement("div");
  marker.className = `nutricity-order-history-annotation ${cancelled && orders.length ? "is-cancelled-sync" : conflicts.length ? "is-conflict" : orders.length ? "is-match" : suggestions.length ? "is-suggestion" : "is-miss"}${detailsClass}`;
  marker.dataset.lookupAt = String(result.cachedAt || Date.now());
  const label = document.createElement("span");
  label.className = "nutricity-order-history-label";
  label.textContent = cancelled && orders.length ? "Cancelled warning" : conflicts.length ? "Warning" : orders.length ? "Odoo order" : suggestions.length ? "Odoo order found" : "Not found in app";
  marker.appendChild(label);
  if (conflicts.length) {
    const warning = document.createElement("span");
    warning.className = "nutricity-order-history-warning";
    const first = conflicts[0];
    warning.textContent = `${first.odoo_order_name || "Odoo order"} ${first.asin || "ASIN"} already has Amazon ${first.existing_amazon_order_id}`;
    marker.appendChild(warning);
  }
  if (orders.length) {
    const links = document.createElement("span");
    links.className = "nutricity-order-history-links";
    if (cancelled) {
      links.appendChild(document.createTextNode("Cancelled here but synced to Odoo order ID: "));
    }
    orders.slice(0, 4).forEach((order, index) => {
      if (index) links.appendChild(document.createTextNode(", "));
      const link = document.createElement("a");
      link.href = order.odoo_order_url || "#";
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = order.odoo_order_name || `Odoo ${order.odoo_order_id}`;
      links.appendChild(link);
      links.appendChild(orderHistoryCopyButton(order.odoo_order_name || order.odoo_order_id || ""));
    });
    if (orders.length > 4) {
      links.appendChild(document.createTextNode(` +${orders.length - 4}`));
    }
    marker.appendChild(links);
    appendOrderHistoryQuantitySummary(marker, orders);
    appendOrderHistoryDateRepair(marker, displayResult, orders, syncInProgress);
  } else if (suggestions.length) {
    const links = document.createElement("span");
    links.className = "nutricity-order-history-links";
    suggestions.slice(0, 3).forEach((order, index) => {
      if (index) links.appendChild(document.createTextNode(", "));
      const link = document.createElement("a");
      link.href = order.odoo_order_url || "#";
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = order.odoo_order_name || `Odoo ${order.odoo_order_id}`;
      links.appendChild(link);
      links.appendChild(orderHistoryCopyButton(order.odoo_order_name || order.odoo_order_id || ""));
    });
    const resultAsins = new Set((displayResult.asins || []).map((asin) => String(asin || "").toUpperCase()).filter(Boolean));
    const existingIds = [...new Set(suggestions
      .flatMap((order) => (order.lines || [])
        .filter((line) => !resultAsins.size || resultAsins.has(String(line.asin || "").toUpperCase()))
        .map((line) => line.current_amazon_order_id)
        .filter(Boolean)))];
    if (existingIds.length) {
      links.appendChild(document.createTextNode(` currently ${existingIds.slice(0, 2).join(", ")}`));
    }
    marker.appendChild(links);
    appendOrderHistoryQuantitySummary(marker, suggestions);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "nutricity-order-history-sync";
    if (syncInProgress) {
      button.classList.add("is-syncing");
      button.disabled = true;
      button.textContent = "Syncing...";
    } else {
      button.textContent = "Sync Amazon ID";
    }
    button.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (orderHistorySyncInProgress.has(displayResult.orderId)) return;
      orderHistorySyncInProgress.set(displayResult.orderId, Date.now());
      button.disabled = true;
      button.classList.add("is-syncing");
      button.textContent = "Syncing...";
      let synced = null;
      try {
        synced = await syncSuggestedAmazonHistoryOrder(displayResult);
      } catch (error) {
        synced = { ok: false, message: error?.message || String(error || "Could not sync this Amazon order.") };
      }
      if (synced?.ok && Number(synced.matched || 0) > 0) {
        orderHistorySyncInProgress.delete(displayResult.orderId);
        button.classList.remove("is-syncing");
        button.classList.add("is-synced");
        button.disabled = true;
        button.textContent = "Synced";
        orderHistorySyncedConfirmations.set(displayResult.orderId, {
          syncedAt: Date.now(),
          message: synced.message || `Synced ${displayResult.orderId}`,
        });
        orderHistoryLookupCache.delete(displayResult.orderId);
        const annotation = button.closest(".nutricity-order-history-annotation");
        if (annotation) annotation.classList.add("is-synced");
      } else {
        orderHistorySyncInProgress.delete(displayResult.orderId);
        button.classList.remove("is-syncing");
        button.disabled = false;
        button.textContent = "Sync failed";
        button.title = synced?.message || "Could not sync this Amazon order.";
      }
    });
    marker.appendChild(button);
  } else if (!displayResult.cancelled) {
    const link = document.createElement("a");
    link.className = "nutricity-order-history-not-found";
    link.href = displayResult.notFoundUrl || "#";
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = displayResult.orderId;
    marker.appendChild(link);
  }
  const container = orderHistoryAnnotationContainer(card);
  container.appendChild(marker);
  appendDirectOdooHistoryRows(container, directOdoo, detailsClass, displayResult);
}

function orderHistoryResultAsinSet(result = {}) {
  const asins = new Set();
  for (const asin of result.asins || []) {
    const normalized = String(asin || "").toUpperCase().trim();
    if (normalized) asins.add(normalized);
  }
  for (const item of result.items || []) {
    const normalized = String(item?.asin || "").toUpperCase().trim();
    if (normalized) asins.add(normalized);
  }
  return asins;
}

function orderHistoryCandidateAsins(candidate = {}) {
  const asins = new Set();
  const addAsin = (value) => {
    const normalized = String(value || "").toUpperCase().trim();
    if (!normalized || normalized === "SUPPLEMENT") return;
    asins.add(normalized);
  };
  for (const asin of candidate.asins || []) {
    addAsin(asin);
  }
  for (const line of candidate.lines || []) {
    addAsin(line?.asin);
    addAsin(line?.replacement_asin);
  }
  for (const check of candidate.quantity_checks || []) {
    addAsin(check?.asin);
  }
  for (const asin of Object.keys(candidate.asin_quantities || {})) {
    addAsin(asin);
  }
  if (candidate.asin) {
    addAsin(candidate.asin);
  }
  if (candidate.replacement_asin) {
    addAsin(candidate.replacement_asin);
  }
  return asins;
}

function orderHistoryCandidateBelongsOnCard(candidate = {}, cardAsins = new Set()) {
  if (!cardAsins.size) return true;
  const candidateAsins = orderHistoryCandidateAsins(candidate);
  if (!candidateAsins.size) return true;
  return [...candidateAsins].some((asin) => cardAsins.has(asin));
}

function filterOrderHistoryCandidatesForCard(candidates = [], cardAsins = new Set()) {
  return (candidates || []).filter((candidate) => orderHistoryCandidateBelongsOnCard(candidate, cardAsins));
}

function orderHistoryTextMatchesOrderName(text = "", orderName = "") {
  const extractRefs = (value) => {
    const upper = String(value || "").toUpperCase();
    // Nutricity references always use a two-letter prefix plus five digits.
    // Amazon can concatenate a pack/variant suffix (for example NC204942
    // pack); taking the canonical seven-character reference avoids treating
    // that suffix as part of the order number.
    const canonical = upper.match(/\b(?:NC|ES)\d{5}/g) || [];
    const generic = (upper.match(/\b[A-Z]{1,8}\d{2,}\b/g) || [])
      .filter((ref) => !/^(?:NC|ES)\d{5}/.test(ref));
    return [...new Set([...canonical, ...generic])];
  };
  const textRefs = new Set(extractRefs(text));
  const orderRefs = extractRefs(orderName);
  return orderRefs.length > 0 && orderRefs.every((ref) => textRefs.has(ref));
}

function filterOrderHistoryCandidatesForCardAndRecipient(candidates = [], cardAsins = new Set(), recipient = "") {
  // Recipient references decide ownership. A shared ASIN must never introduce
  // another order, and an Amazon split shipment must not hide a valid order
  // reference merely because that card exposes only some of the cart ASINs.
  // ASINs are used later only to select the relevant lines within the orders
  // whose references are present in the recipient name.
  return (candidates || []).filter((candidate) =>
    orderHistoryTextMatchesOrderName(recipient, candidate.odoo_order_name)
  );
}

function filterOrderHistoryResultForCard(result = {}) {
  const cardAsins = orderHistoryResultAsinSet(result);
  const recipient = result.recipient || "";
  if (!cardAsins.size && !recipient) return result;
  const matchOrders = filterOrderHistoryCandidatesForCardAndRecipient(result.match?.orders || [], cardAsins, recipient);
  const suggestions = filterOrderHistoryCandidatesForCardAndRecipient(result.suggestions || [], cardAsins, recipient);
  const conflicts = filterOrderHistoryCandidatesForCardAndRecipient(result.conflicts || [], cardAsins, recipient);
  // Keep a direct Odoo recipient lookup visible even when its ASIN conflicts
  // with Amazon. It is diagnostic evidence, but it must never make the main
  // Odoo-order match appear green.
  const directOdoo = (result.odooDirect || []).filter((candidate) =>
    orderHistoryTextMatchesOrderName(recipient, candidate.odoo_order_name)
    || orderHistoryCandidateBelongsOnCard(candidate, cardAsins),
  );
  return {
    ...result,
    match: result.match ? { ...result.match, orders: matchOrders } : result.match,
    suggestions,
    conflicts,
    odooDirect: directOdoo,
    unmatched: Boolean(result.unmatched && !matchOrders.length && !suggestions.length && !conflicts.length && !directOdoo.length),
  };
}

function compactQuantity(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return "0";
  return Number.isInteger(number) ? String(number) : String(Math.round(number * 100) / 100);
}

async function copyOrderHistoryText(value) {
  const text = String(value || "").trim();
  if (!text) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (_) {
    const input = document.createElement("textarea");
    input.value = text;
    input.setAttribute("readonly", "readonly");
    input.style.position = "fixed";
    input.style.left = "-9999px";
    input.style.top = "0";
    document.body.appendChild(input);
    input.select();
    let copied = false;
    try {
      copied = document.execCommand("copy");
    } catch (error) {
      copied = false;
    }
    input.remove();
    return copied;
  }
}

function orderHistoryCopyButton(value) {
  const text = String(value || "").trim();
  const button = document.createElement("button");
  button.type = "button";
  button.className = "nutricity-order-history-copy";
  button.setAttribute("aria-label", text ? `Copy ${text}` : "Copy Odoo order ID");
  button.title = text ? `Copy ${text}` : "Copy Odoo order ID";
  button.disabled = !text;
  button.addEventListener("click", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (!text) return;
    const copied = await copyOrderHistoryText(text);
    button.classList.toggle("is-copied", copied);
    button.title = copied ? `Copied ${text}` : `Could not copy ${text}`;
    window.setTimeout(() => {
      button.classList.remove("is-copied");
      button.title = `Copy ${text}`;
    }, 1200);
  });
  return button;
}

function appendOrderHistoryQuantitySummary(marker, orders = []) {
  const checks = [];
  for (const order of orders || []) {
    for (const check of order.quantity_checks || []) {
      checks.push({
        orderName: order.odoo_order_name || "",
        asin: check.asin || "",
        expected: Number(check.expected || 0),
        actual: Number(check.actual || 0),
        matches: check.matches === true,
      });
    }
  }
  if (!checks.length) return;
  const summary = document.createElement("span");
  const mismatches = checks.filter((check) => !check.matches);
  summary.className = `nutricity-order-history-quantity ${mismatches.length ? "is-warning" : "is-match"}`;
  if (mismatches.length) {
    summary.textContent = `Qty warning: ${mismatches
      .slice(0, 3)
      .map((check) => `${check.asin} expected ${compactQuantity(check.expected)}, Amazon ${compactQuantity(check.actual)}`)
      .join("; ")}`;
  } else {
    summary.textContent = `Qty matches: ${checks
      .slice(0, 4)
      .map((check) => `${check.asin} x${compactQuantity(check.actual)}`)
      .join(", ")}`;
  }
  marker.appendChild(summary);
}

function syncedHistoryResultVerifiesAsin(result = {}) {
  const candidates = [
    ...(result.match?.orders || []),
    ...(result.suggestions || []),
  ];
  return candidates.some((candidate) => (candidate.quantity_checks || []).some((check) => check?.matches === true));
}

function appendDirectOdooHistoryRows(container, directOdoo = [], detailsClass = "", historyResult = {}) {
  const rows = (directOdoo || []).filter(Boolean);
  if (!rows.length) return;
  const syncedAsinVerification = syncedHistoryResultVerifiesAsin(historyResult);
  const amazonAsins = orderHistoryResultAsinSet(historyResult);
  for (const order of rows.slice(0, 4)) {
    const checks = order.quantity_checks || [];
    const warnings = checks.filter((check) => !check.matches);
    const odooAsins = orderHistoryCandidateAsins(order);
    const asinConflict = !checks.length && amazonAsins.size && odooAsins.size
      && ![...odooAsins].some((asin) => amazonAsins.has(asin));
    const marker = document.createElement("div");
    marker.className = `nutricity-order-history-annotation nutricity-order-history-direct ${order.error || !order.found || warnings.length || asinConflict ? "is-conflict" : "is-match"}${detailsClass}`;

    const label = document.createElement("span");
    label.className = "nutricity-order-history-label";
    label.textContent = "Odoo direct";
    marker.appendChild(label);

    const links = document.createElement("span");
    links.className = "nutricity-order-history-links";
    if (order.odoo_order_url) {
      const link = document.createElement("a");
      link.href = order.odoo_order_url;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = order.odoo_order_name || `Odoo ${order.odoo_order_id || ""}`.trim();
      links.appendChild(link);
      links.appendChild(orderHistoryCopyButton(order.odoo_order_name || order.odoo_order_id || ""));
    } else {
      links.textContent = order.odoo_order_name || "Odoo order";
    }
    if (order.store_name) {
      links.appendChild(document.createTextNode(` (${order.store_name})`));
    }
    marker.appendChild(links);

    if (order.error) {
      const warning = document.createElement("span");
      warning.className = "nutricity-order-history-warning";
      warning.textContent = `Direct check failed: ${order.error}`;
      marker.appendChild(warning);
    } else if (!order.found) {
      const warning = document.createElement("span");
      warning.className = "nutricity-order-history-warning";
      warning.textContent = "Not found directly in Odoo";
      marker.appendChild(warning);
    } else {
      appendOrderHistoryQuantitySummary(marker, [order]);
      if (asinConflict) {
        const warning = document.createElement("span");
        warning.className = "nutricity-order-history-warning";
        warning.textContent = `ASIN conflict: Odoo ${[...odooAsins].join(", ")}; Amazon ${[...amazonAsins].join(", ")}`;
        marker.appendChild(warning);
      } else if (!checks.length && !syncedAsinVerification) {
        const warning = document.createElement("span");
        warning.className = "nutricity-order-history-warning";
        warning.textContent = "No ASIN lines found directly in Odoo";
        marker.appendChild(warning);
      } else if (!checks.length) {
        const verified = document.createElement("span");
        verified.className = "nutricity-order-history-quantity is-match";
        verified.textContent = "ASIN verified from synced fulfilment record";
        marker.appendChild(verified);
      }
    }
    container.appendChild(marker);
  }
}

function directOdooRowsForOrder(result, orderId) {
  if (!result?.ok) return null;
  const direct = result.odoo_direct || result.odooDirect || {};
  if (Array.isArray(direct)) return direct;
  if (!direct || typeof direct !== "object") return undefined;
  const keys = [orderId, String(orderId || "").trim()].filter(Boolean);
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(direct, key)) {
      return Array.isArray(direct[key]) ? direct[key] : [];
    }
  }
  return undefined;
}

function orderHistoryHasDirectOdooTarget(result) {
  return Boolean(
    (result?.match?.orders || []).length
    || (result?.suggestions || []).length
    || (result?.conflicts || []).length
  );
}

async function lookupDirectOdooForHistoryOrder(card, details) {
  const orderId = details?.amazon_order_id || "";
  if (!orderId) return;
  try {
    const result = await send({ type: "LOOKUP_AMAZON_HISTORY_ODOO_DIRECT", orders: [details] });
    const directRows = directOdooRowsForOrder(result, orderId);
    const cached = orderHistoryLookupCache.get(orderId);
    if (!cached) return;
    if (directRows === null || (directRows === undefined && orderHistoryHasDirectOdooTarget(cached))) {
      cached.odooDirectLoaded = false;
    } else {
      cached.odooDirect = Array.isArray(directRows) ? directRows : [];
      cached.odooDirectLoaded = true;
      cached.cachedAt = Date.now();
    }
    orderHistoryLookupCache.set(orderId, cached);
    try {
      renderOrderHistoryAnnotation(card, cached);
    } catch (_) {
      cached.odooDirectLoaded = false;
      orderHistoryLookupCache.set(orderId, cached);
    }
  } catch (_) {
    const cached = orderHistoryLookupCache.get(orderId);
    if (cached) {
      cached.odooDirectLoaded = false;
      orderHistoryLookupCache.set(orderId, cached);
    }
  } finally {
    orderHistoryOdooDirectInFlight.delete(orderId);
  }
}

function orderDateMismatches(orders = []) {
  return (orders || []).flatMap((order) =>
    (order.order_date_checks || [])
      .filter((check) => check && check.matches === false)
      .map((check) => ({
        orderName: order.odoo_order_name || "",
        lineId: Number(check.line_id || 0),
        asin: check.asin || "",
        amazonDate: check.amazon_order_date || "",
        appDate: check.app_ordered_date || "",
      })),
  );
}

function appendOrderHistoryDateRepair(marker, result, orders = [], syncInProgress = false) {
  const mismatches = orderDateMismatches(orders);
  if (!mismatches.length) return;
  const warning = document.createElement("span");
  warning.className = "nutricity-order-history-warning";
  warning.textContent = `Date mismatch: Amazon ${mismatches[0].amazonDate || result.orderDate || "order date"}, app ${mismatches[0].appDate || "stored date"}`;
  marker.appendChild(warning);

  const button = document.createElement("button");
  button.type = "button";
  button.className = "nutricity-order-history-sync";
  if (syncInProgress) {
    button.classList.add("is-syncing");
    button.disabled = true;
    button.textContent = "Syncing...";
  } else {
    button.textContent = "Sync date";
  }
  button.addEventListener("click", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (orderHistorySyncInProgress.has(result.orderId)) return;
    orderHistorySyncInProgress.set(result.orderId, Date.now());
    button.disabled = true;
    button.classList.add("is-syncing");
    button.textContent = "Syncing...";
    let synced = null;
    try {
      synced = await syncMatchedAmazonHistoryDate(result);
    } catch (error) {
      synced = { ok: false, message: error?.message || String(error || "Could not sync this Amazon order date.") };
    }
    if (synced?.ok && Number(synced.matched || 0) > 0) {
      orderHistorySyncInProgress.delete(result.orderId);
      button.classList.remove("is-syncing");
      button.classList.add("is-synced");
      button.disabled = true;
      button.textContent = "Date synced";
      orderHistorySyncedConfirmations.set(result.orderId, {
        syncedAt: Date.now(),
        message: synced.message || `Synced date for ${result.orderId}`,
      });
      orderHistoryLookupCache.delete(result.orderId);
      const annotation = button.closest(".nutricity-order-history-annotation");
      if (annotation) annotation.classList.add("is-synced");
    } else {
      orderHistorySyncInProgress.delete(result.orderId);
      button.classList.remove("is-syncing");
      button.disabled = false;
      button.textContent = "Sync failed";
      button.title = synced?.message || "Could not sync this Amazon order date.";
    }
  });
  marker.appendChild(button);
}

async function syncSuggestedAmazonHistoryOrder(result) {
  const suggestions = result.suggestions || [];
  const orderNames = [...new Set(suggestions.map((order) => order.odoo_order_name).filter(Boolean))];
  if (!result.orderId || !orderNames.length) return { ok: false, message: "No Odoo order suggestion is available." };
  const amazonAsins = new Set((result.asins || []).map((asin) => String(asin || "").toUpperCase()).filter(Boolean));
  const lineMatchesAmazonAsin = (line = {}) => {
    if (!amazonAsins.size) return true;
    return [line.asin, line.replacement_asin]
      .map((asin) => String(asin || "").toUpperCase().trim())
      .filter(Boolean)
      .some((asin) => amazonAsins.has(asin));
  };
  const lineIdsForSync = [
    ...new Set(
      suggestions.flatMap((order) => {
        const matchingIds = (order.lines || [])
          .filter(lineMatchesAmazonAsin)
          .map((line) => Number(line.id || 0))
          .filter(Boolean);
        // Evaluate each recipient order independently. A card containing two
        // Odoo refs must not lose the second order merely because only the
        // first order exposed a matching ASIN in the Amazon history DOM.
        return matchingIds.length
          ? matchingIds
          : (order.line_ids || []).map(Number).filter(Boolean);
      }),
    ),
  ];
  if (!lineIdsForSync.length) {
    return {
      ok: false,
      message: amazonAsins.size
        ? "No matching Odoo line ASIN or app line id was found for this Amazon order card."
        : "No app line id is available for this Amazon order card.",
    };
  }
  return sendWithTimeout({
    type: "SYNC_AMAZON_HISTORY_ORDER",
    order: {
      amazon_order_id: result.orderId,
      amazon_order_url: orderDetailsUrl(result.orderId),
      order_date: result.orderDate || "",
      recipient: result.recipient || "",
      source_text: result.recipient || orderNames.join(" "),
      order_names: orderNames,
      line_ids: lineIdsForSync,
      store_id: suggestions.length === 1 ? suggestions[0].store_id : null,
      replace_existing: true,
    },
  }, 25000);
}

async function syncMatchedAmazonHistoryDate(result) {
  const orders = result.match?.orders || [];
  const orderNames = [...new Set(orders.map((order) => order.odoo_order_name).filter(Boolean))];
  const lineIds = [...new Set(orders.flatMap((order) => order.line_ids || []).map(Number).filter(Boolean))];
  if (!result.orderId || !orderNames.length || !lineIds.length) return { ok: false, message: "No matched app lines are available for date sync." };
  return sendWithTimeout({
    type: "SYNC_AMAZON_HISTORY_ORDER",
    order: {
      amazon_order_id: result.orderId,
      amazon_order_url: orderDetailsUrl(result.orderId),
      order_date: result.orderDate || "",
      recipient: result.recipient || "",
      source_text: result.recipient || orderNames.join(" "),
      order_names: orderNames,
      line_ids: lineIds,
      store_id: orders.length === 1 ? orders[0].store_id : null,
      replace_existing: true,
    },
  }, 25000);
}

async function annotateAmazonOrderHistoryDirectOdoo(pairs = []) {
  const pending = pairs
    .filter(({ details }) => {
      const orderId = details?.amazon_order_id || "";
      if (!orderId || orderHistoryOdooDirectInFlight.has(orderId)) return false;
      const cached = orderHistoryLookupCache.get(orderId);
      return cached && !cached.odooDirectLoaded;
    });
  if (!pending.length) return;
  const queue = pending.slice(0, 12);
  for (const { details } of queue) {
    orderHistoryOdooDirectInFlight.add(details.amazon_order_id);
  }
  try {
    let result = await sendWithTimeout({
      type: "LOOKUP_AMAZON_HISTORY_ODOO_DIRECT",
      orders: queue.map(({ details }) => details),
    }, 20000);
    if (!result?.ok) {
      result = await lookupAmazonHistoryOdooDirectFromContent(queue.map(({ details }) => details));
    }
    for (const { card, details } of queue) {
      const orderId = details?.amazon_order_id || "";
      const cached = orderHistoryLookupCache.get(orderId);
      if (!cached) continue;
      const directRows = directOdooRowsForOrder(result, orderId);
      cached.odooDirect = Array.isArray(directRows) ? directRows : [];
      cached.odooDirectLoaded = result?.ok !== false;
      cached.cachedAt = Date.now();
      orderHistoryLookupCache.set(orderId, cached);
      renderOrderHistoryAnnotation(card, cached);
    }
  } catch (_) {
    for (const { details } of queue) {
      const orderId = details?.amazon_order_id || "";
      const cached = orderHistoryLookupCache.get(orderId);
      if (cached) {
        cached.odooDirectLoaded = false;
        orderHistoryLookupCache.set(orderId, cached);
      }
    }
  } finally {
    for (const { details } of queue) {
      orderHistoryOdooDirectInFlight.delete(details?.amazon_order_id || "");
    }
  }
}

async function annotateAmazonOrderHistory() {
  if (fulfilmentForceStopped || !extensionContextAlive || !/amazon\.com$/i.test(location.hostname) || (!isOrderHistoryPage() && !isOrderDetailsPage())) return;
  orderHistoryLastAnnotatedAt = Date.now();
  const pairs = [];
  const unknown = [];
  const directCandidates = [];
  const seen = new Set();
  if (isOrderDetailsPage()) {
    const details = orderDetailsPageDetails();
    if (details?.amazon_order_id) {
      seen.add(details.amazon_order_id);
      pairs.push({ card: orderDetailsAnnotationHost(), details });
    }
  }
  if (isOrderHistoryPage()) {
    for (const card of visibleOrderHistoryCards()) {
      const details = orderHistoryCardDetails(card);
      if (!details || seen.has(details.amazon_order_id)) continue;
      seen.add(details.amazon_order_id);
      pairs.push({ card, details });
    }
  }
  for (const { card, details } of pairs) {
    const cached = orderHistoryLookupCache.get(details.amazon_order_id);
    if (cached && orderHistoryLookupCacheFresh(cached)) {
      renderOrderHistoryAnnotation(card, cached);
      if (!cached.odooDirectLoaded) directCandidates.push({ card, details });
    } else {
      if (cached) orderHistoryLookupCache.delete(details.amazon_order_id);
      renderOrderHistoryPendingAnnotation(card, details);
      unknown.push(details);
    }
  }
  if (!unknown.length) {
    annotateAmazonOrderHistoryDirectOdoo(directCandidates).catch(() => {});
    return;
  }
  if (orderHistoryAnnotationInFlight && Date.now() - Number(orderHistoryAnnotationInFlightAt || 0) < 25000) return;
  orderHistoryAnnotationInFlight = true;
  orderHistoryAnnotationInFlightAt = Date.now();
  try {
    let result = await lookupAmazonHistoryOrdersFromContent(unknown).catch((error) => ({
      ok: false,
      message: error?.message || String(error || "The local app lookup failed."),
    }));
    if (fulfilmentForceStopped) return;
    if (!result?.ok) {
      const message = result?.message || result?.error || "The app lookup did not return a usable response.";
      if (isTransientOrderHistoryLookupError(message)) {
        for (const { card, details } of pairs) {
          if (unknown.some((order) => order.amazon_order_id === details.amazon_order_id)) {
            renderOrderHistoryPendingAnnotation(card, details);
          }
        }
        scheduleOrderHistoryAnnotation(2500);
        return;
      }
      for (const { card, details } of pairs) {
        if (unknown.some((order) => order.amazon_order_id === details.amazon_order_id)) {
          renderOrderHistoryLookupError(card, details, message);
        }
      }
      return;
    }
    const matches = result.matches || {};
    const suggestions = result.suggestions || {};
    const conflicts = result.conflicts || {};
    const directRows = result.odoo_direct || result.odooDirect || {};
    const directLookupFailed = Boolean(result.odoo_direct_error || result.odooDirectError);
    const unmatched = new Set(result.unmatched || []);
    for (const details of unknown) {
      const directForOrder = directOdooRowsForOrder({ ok: true, odoo_direct: directRows }, details.amazon_order_id);
      const match = matches[details.amazon_order_id] || null;
      const orderSuggestions = suggestions[details.amazon_order_id] || [];
      const orderConflicts = conflicts[details.amazon_order_id] || [];
      const hasDirectTarget = Boolean((match?.orders || []).length || orderSuggestions.length || orderConflicts.length);
      const cached = {
        orderId: details.amazon_order_id,
        recipient: details.recipient,
        match,
        suggestions: orderSuggestions,
        conflicts: orderConflicts,
        odooDirect: Array.isArray(directForOrder) ? directForOrder : [],
        odooDirectLoaded: !directLookupFailed && (Array.isArray(directForOrder) || !hasDirectTarget),
        unmatched: unmatched.has(details.amazon_order_id),
        orderDate: details.order_date || "",
        asins: details.asins || [],
        items: details.items || [],
        cancelled: details.cancelled,
        notFoundUrl: result.not_found_url || "",
        cachedAt: Date.now(),
      };
      orderHistoryLookupCache.set(details.amazon_order_id, cached);
    }
    annotateAmazonOrderHistoryDirectOdoo(pairs).catch(() => {});
    for (const { card, details } of pairs) {
      try {
        renderOrderHistoryAnnotation(card, orderHistoryLookupCache.get(details.amazon_order_id));
      } catch (_) {
        const cached = orderHistoryLookupCache.get(details.amazon_order_id);
        if (cached) {
          cached.cachedAt = 0;
          orderHistoryLookupCache.set(details.amazon_order_id, cached);
        }
      }
    }
  } catch (error) {
    const message = error?.message || String(error || "The app lookup failed.");
    if (isTransientOrderHistoryLookupError(message)) {
      for (const { card, details } of pairs) {
        if (unknown.some((order) => order.amazon_order_id === details.amazon_order_id)) {
          renderOrderHistoryPendingAnnotation(card, details);
        }
      }
      scheduleOrderHistoryAnnotation(2500);
      return;
    }
    for (const { card, details } of pairs) {
      if (unknown.some((order) => order.amazon_order_id === details.amazon_order_id)) {
        renderOrderHistoryLookupError(card, details, message);
      }
    }
  } finally {
    orderHistoryAnnotationInFlight = false;
    orderHistoryAnnotationInFlightAt = 0;
    if (!fulfilmentForceStopped && orderHistoryNeedsMoreAnnotation()) {
      scheduleOrderHistoryAnnotation(1200);
    }
  }
}

function scheduleOrderHistoryAnnotation(delay = 350) {
  if (fulfilmentForceStopped || orderHistoryAnnotationScheduled) return;
  if (document.hidden || !/amazon\.com$/i.test(location.hostname)) return;
  if (!isOrderHistoryPage() && !isOrderDetailsPage()) return;
  const sinceLastRun = Date.now() - Number(orderHistoryLastAnnotatedAt || 0);
  const safeDelay = sinceLastRun < 1000 ? Math.max(delay, 1000 - sinceLastRun) : delay;
  orderHistoryAnnotationScheduled = true;
  orderHistoryAnnotationTimer = setTimeout(() => {
    orderHistoryAnnotationTimer = null;
    orderHistoryAnnotationScheduled = false;
    if (fulfilmentForceStopped) return;
    annotateAmazonOrderHistory().catch(() => {});
  }, safeDelay);
}

function activeJobOrderNames(activeJob) {
  return (activeJob.job?.order_names || []).map((name) => String(name || "").trim()).filter(Boolean);
}

function activeJobAsins(activeJob) {
  const asins = [];
  for (const item of activeJob.job?.items || []) {
    const requested = String(item.asin || "").toUpperCase();
    const purchased = String(activeJob.pricing?.[requested]?.purchased_asin || "").toUpperCase();
    if (requested) asins.push(requested);
    if (purchased) asins.push(purchased);
  }
  return [...new Set(asins)];
}

function recentOrderMatchesActiveJob(order, activeJob) {
  if (order?.cancelled === true) return false;
  const orderId = String(order?.amazon_order_id || "").trim();
  const cancelledOrderIds = new Set([
    ...(activeJob?.job?.cancelled_amazon_order_ids || []),
    ...(activeJob?.job?.items || []).flatMap((item) => item.cancelled_amazon_order_ids || []),
  ].map((value) => String(value || "").trim()).filter(Boolean));
  if (orderId && cancelledOrderIds.has(orderId)) return false;
  const haystack = `${order.recipient || ""} ${order.status || ""}`.toLowerCase();
  const compactHaystack = compactMatchText(haystack);
  const names = activeJobOrderNames(activeJob).map((name) => name.toLowerCase());
  const compactNames = names.map(compactMatchText).filter(Boolean);
  const recipient = String(activeJob.job?.recipient_name || "").toLowerCase();
  const fullRecipient = recipientName(activeJob).toLowerCase();
  const recipientSuffix = String(activeJob?.job?.recipient_suffix || "").replace(/\s+/g, "").trim();
  if (
    recipientSuffix
    && fullRecipient
    && !haystack.includes(fullRecipient)
    && !compactHaystack.includes(compactMatchText(fullRecipient))
  ) return false;
  const nameMatches = names.some((name) => name && (haystack.includes(name) || compactHaystack.includes(compactMatchText(name))))
    || (recipient && (haystack.includes(recipient) || compactHaystack.includes(compactMatchText(recipient))));
  if (!nameMatches) return false;
  const exactOrderNameMatch = compactNames.some((name) => name.length >= 5 && compactHaystack.includes(name));
  const cachedAsins = new Set((order.asins || []).map((asin) => String(asin || "").toUpperCase()).filter(Boolean));
  const jobAsins = activeJobAsins(activeJob);
  if (!cachedAsins.size || !jobAsins.length) return true;
  if (recipientSuffix) {
    const jobAsinSet = new Set(jobAsins);
    const extraAsins = [...cachedAsins].filter((asin) => !jobAsinSet.has(asin));
    if (extraAsins.length) return false;
  }
  if (exactOrderNameMatch && (activeJob.job?.items || []).length === 1 && cachedAsins.size > 1) {
    return true;
  }
  return jobAsins.some((asin) => cachedAsins.has(asin));
}

async function findRememberedDuplicateOrder(activeJob) {
  const result = await send({ type: "GET_RECENT_AMAZON_ORDERS" });
  if (!result?.ok) return null;
  const orders = (result.orders || []).filter((order) => recentOrderMatchesActiveJob(order, activeJob));
  return orders[0] || null;
}

function splitHistoryOrdersMapEveryActiveLine(activeJob, orders = []) {
  const items = activeJob?.job?.items || [];
  if (items.length < 2 || orders.length < 2) return false;
  for (const item of items) {
    const requestedAsin = String(item?.asin || "").toUpperCase();
    const purchasedAsin = String(activeJob?.pricing?.[requestedAsin]?.purchased_asin || "").toUpperCase();
    const itemAsins = new Set([requestedAsin, purchasedAsin].filter(Boolean));
    if (!itemAsins.size) return false;
    const matchingOrders = orders.filter((order) => {
      const asins = (order?.asins || []).map((asin) => String(asin || "").toUpperCase()).filter(Boolean);
      return asins.some((asin) => itemAsins.has(asin));
    });
    // Mapping must be exact: if an ASIN appears on two history cards, leave
    // the job for review rather than assigning either Amazon order ID.
    if (matchingOrders.length !== 1) return false;
  }
  return true;
}

async function selectUnambiguousSubmittedHistoryOrders(activeJob, candidates = []) {
  const unique = [];
  const seen = new Set();
  for (const candidate of candidates || []) {
    const orderId = String(candidate?.amazon_order_id || "").trim();
    if (!/^\d{3}-\d{7}-\d{7}$/.test(orderId) || seen.has(orderId)) continue;
    seen.add(orderId);
    unique.push(candidate);
  }
  if (unique.length <= 1) return { orders: unique, ambiguous: false };

  // Multiple cards can share the same Nutricity recipient and ASIN. Never
  // report the first matching card: first exclude Amazon IDs that the app has
  // already recorded, then continue only if one new candidate remains.
  let lookup = null;
  try {
    lookup = await send({ type: "LOOKUP_AMAZON_HISTORY_ORDERS", orders: unique });
  } catch (_) {
    lookup = null;
  }
  if (!lookup?.ok) {
    await sendDiagnostic("Did not report ambiguous Amazon history cards because the app lookup was unavailable.", {
      group_key: activeJob?.job?.group_key || "",
      candidate_order_ids: unique.map((order) => order.amazon_order_id),
    }, "warn");
    return { orders: [], ambiguous: true };
  }

  const previouslyReported = new Set(String(activeJob?.reportedOrderId || "").match(/\b\d{3}-\d{7}-\d{7}\b/g) || []);
  const matches = lookup.matches || {};
  const unrecorded = unique.filter((order) => {
    const orderId = String(order.amazon_order_id || "").trim();
    return !previouslyReported.has(orderId) && !((matches[orderId]?.orders || []).length);
  });
  if (unrecorded.length === 1) return { orders: unrecorded, ambiguous: false };
  if (splitHistoryOrdersMapEveryActiveLine(activeJob, unrecorded)) {
    await sendDiagnostic("Amazon split the submitted job into distinct ASIN order cards; reporting a per-line mapping.", {
      group_key: activeJob?.job?.group_key || "",
      split_order_ids: unrecorded.map((order) => order.amazon_order_id),
      split_order_asins: unrecorded.map((order) => ({
        amazon_order_id: order.amazon_order_id,
        asins: order.asins || [],
      })),
    });
    return { orders: unrecorded, ambiguous: false, split_by_asin: true };
  }
  if (previouslyReported.size) {
    const prior = unique.filter((order) => previouslyReported.has(String(order.amazon_order_id || "").trim()));
    if (prior.length === 1) return { orders: prior, ambiguous: false };
  }

  // A reset/re-placement can legitimately leave an earlier Amazon card with
  // the exact same recipient and ASIN in history. During the short post-submit
  // verification window, Amazon's newest *matching* card is the only safe
  // choice: recipient and ASIN have already been checked by
  // recentOrderMatchesActiveJob, and the rank is taken from Amazon's newest
  // first history layout. Do not use this escape hatch for stale jobs or for
  // a match that is not the newest visible Amazon card.
  const submittedAt = Number(activeJob?.placeOrderClickStartedAt || activeJob?.amazonSubmittedAt || 0);
  const submitAge = submittedAt ? Date.now() - submittedAt : Number.POSITIVE_INFINITY;
  const newestMatch = [...unrecorded].sort((a, b) => Number(a.history_rank ?? Number.MAX_SAFE_INTEGER) - Number(b.history_rank ?? Number.MAX_SAFE_INTEGER))[0];
  if (submitAge >= 0 && submitAge <= 30 * 60 * 1000 && Number(newestMatch?.history_rank) === 0) {
    await sendDiagnostic("Reported the newest matching Amazon history card after a re-placement.", {
      group_key: activeJob?.job?.group_key || "",
      selected_order_id: newestMatch.amazon_order_id,
      duplicate_matching_order_ids: unrecorded.map((order) => order.amazon_order_id),
      submit_age_seconds: Math.round(submitAge / 1000),
    });
    return { orders: [newestMatch], ambiguous: false, newest_matching_replacement: true };
  }
  await sendDiagnostic("Refused to report an ambiguous Amazon history match.", {
    group_key: activeJob?.job?.group_key || "",
    candidate_order_ids: unique.map((order) => order.amazon_order_id),
    unrecorded_order_ids: unrecorded.map((order) => order.amazon_order_id),
    already_recorded_order_ids: unique
      .filter((order) => (matches[String(order.amazon_order_id || "").trim()]?.orders || []).length)
      .map((order) => order.amazon_order_id),
  }, "error");
  return { orders: [], ambiguous: true };
}

function extractRecentOrders(activeJob) {
  const recipient = String(activeJob.job.recipient_name || "").toLowerCase();
  const compactRecipient = compactMatchText(recipient);
  const names = activeJobOrderNames(activeJob).map((name) => name.toLowerCase());
  const compactNames = names.map(compactMatchText).filter(Boolean);
  const historyOrders = extractOrderHistoryOrders();
  const historyMatches = historyOrders.filter((order) => recentOrderMatchesActiveJob(order, activeJob));
  if (historyMatches.length) return historyMatches;
  const cards = [
    ...document.querySelectorAll("#orderCardHeader, .order-card, [id*='orderCard'], .order, .a-box-group, .a-box"),
  ]
    .filter(visible)
    .sort((a, b) => (a.innerText || a.textContent || "").length - (b.innerText || b.textContent || "").length);
  const candidates = cards.length ? cards : [...document.querySelectorAll("body")];
  const seen = new Set();
  const orders = [];
  for (const card of candidates) {
    if (isCancelledOrderCard(card)) continue;
    const text = (card.innerText || card.textContent || "").replace(/\s+/g, " ");
    const lower = text.toLowerCase();
    const compactLower = compactMatchText(lower);
    const matchesRecipient = recipient && (lower.includes(recipient) || compactLower.includes(compactRecipient));
    const matchesOrderName = names.some((name) => name && lower.includes(name))
      || compactNames.some((name) => name && compactLower.includes(name));
    const orderMatch = text.match(/\b\d{3}-\d{7}-\d{7}\b/);
    // A single visible card is not proof that it belongs to this job. Amazon
    // often shows one unrelated recent order while the newly submitted order
    // is still loading. Never use a card unless its recipient/order label
    // actually identifies the active job.
    if (orderMatch && (matchesRecipient || matchesOrderName)) {
      const orderId = orderMatch[0];
      if (seen.has(orderId)) continue;
      seen.add(orderId);
      orders.push({
        amazon_order_id: orderId,
        amazon_order_url: orderDetailsUrl(orderId),
        recipient: orderCardRecipient(card),
        order_date: orderCardDate(card),
        status: orderCardStatus(card),
        asins: orderCardAsins(card),
      });
    }
  }
  return orders;
}

async function handleCompletion(activeJob) {
  await waitForElement(["body"], 8000);
  if (isOrderHistoryPage()) {
    await handleOrderHistory(activeJob);
    return;
  }
  const orderId = extractOrderId();
  if (orderId) {
    // A confirmation URL proves Amazon accepted a click, but it does not
    // contain enough recipient/ASIN evidence to attach that number safely.
    // Always verify it against the history card before reporting to the app.
    activeJob.stage = "find_order_id";
    activeJob.orderHistoryLookupStartedAt = activeJob.orderHistoryLookupStartedAt || Date.now();
    await setActiveJob(activeJob);
    showPanel("Nutricity fulfilment", `Amazon confirmation showed ${orderId}. Verifying recipient and ASINs in order history before reporting.`, null, null);
    location.href = orderHistoryUrl();
    return;
  }
  // Amazon normally replaces checkout with its confirmation page immediately.
  // If that did not happen after a protected click, do not retry Place Order:
  // first look in order history.  This handles a content-script/extension reload
  // between the click and navigation without risking a duplicate purchase.
  const clickAge = Date.now() - Number(activeJob.placeOrderClickStartedAt || activeJob.amazonSubmittedAt || Date.now());
  if (/\/checkout/i.test(location.pathname) && findPlaceOrderButton() && clickAge >= 45000) {
    showPanel("Nutricity fulfilment", "Amazon did not show a confirmation after Place Order. Opening recent orders to verify the result without submitting again.", null, null);
    activeJob.stage = "find_order_id";
    await setActiveJob(activeJob);
    location.href = orderHistoryUrl();
    return;
  }
  if (/\/cart|\/dp\/|\/gp\/product/i.test(location.pathname)) {
    showPanel("Nutricity fulfilment", "Amazon submit already started. Opening order history to capture the order number.", null, null);
    activeJob.stage = "find_order_id";
    await setActiveJob(activeJob);
    location.href = orderHistoryUrl();
    return;
  }
  if (amazonOrderSubmitErrorPage()) {
    showPanel("Nutricity fulfilment", "Amazon showed an error after order submit. Opening recent orders to verify whether the order was placed.", null, null);
    activeJob.stage = "find_order_id";
    await setActiveJob(activeJob);
    location.href = orderHistoryUrl();
    return;
  }
  if (!confirmationSaysPlaced()) return;
  const link = recentOrdersLink();
  if (link) {
    showPanel("Nutricity fulfilment", "Order placed. Opening recent orders to capture the Amazon order number.", null, null);
    activeJob.stage = "find_order_id";
    await setActiveJob(activeJob);
    await clickElement(link, "Review recent orders link");
    return;
  }
  showPanel("Nutricity fulfilment", "Order placed. Opening order history to capture the Amazon order number.", null, null);
  activeJob.stage = "find_order_id";
  await setActiveJob(activeJob);
  location.href = orderHistoryUrl();
}

async function handleOrderHistory(activeJob) {
  if (!isOrderHistoryPage()) {
    showPanel("Nutricity fulfilment", "Opening order history to capture the Amazon order number.", null, null);
    activeJob.stage = "find_order_id";
    activeJob.orderHistoryLookupStartedAt = activeJob.orderHistoryLookupStartedAt || Date.now();
    await setActiveJob(activeJob);
    location.href = orderHistoryUrl();
    return;
  }
  activeJob.orderHistoryLookupStartedAt = activeJob.orderHistoryLookupStartedAt || Date.now();
  await setActiveJob(activeJob);
  // Amazon's Business order-history wrapper changes independently of the
  // actual order-card content. Wait on the parser itself, then always run it;
  // a missing legacy wrapper must never strand a submitted order as stale.
  await waitUntil(() => extractOrderHistoryOrders().length > 0, 12000, 300);
  const historyOrders = extractOrderHistoryOrders();
  if (historyOrders.length) {
    await send({ type: "REMEMBER_RECENT_AMAZON_ORDERS", orders: historyOrders });
  }
  const historyCandidates = extractRecentOrders(activeJob);
  const selection = await selectUnambiguousSubmittedHistoryOrders(activeJob, historyCandidates);
  const orders = selection.orders;
  const rememberedOrder = orders.length || selection.ambiguous ? null : await findRememberedDuplicateOrder(activeJob);
  if (selection.ambiguous) {
    const lookupAge = Date.now() - Number(activeJob.orderHistoryLookupStartedAt || Date.now());
    if (lookupAge > 120000) {
      const message = `Amazon history still has multiple matching cards for ${activeJobOrderLabel(activeJob) || activeJob.job.group_key} after ${Math.round(lookupAge / 1000)} seconds. This submitted order remains locked; the queue will not continue until one Amazon order ID is verified and reported to Odoo.`;
      activeJob.paused = true;
      activeJob.pausedStage = "find_order_id";
      activeJob.lastError = message;
      await setActiveJob(activeJob);
      showPanel(
        "Chrome fulfilment held for verification",
        message,
        "Reset and retry unplaced order",
        async () => {
          const result = await send({ type: "RESET_DUPLICATE_FULFILMENT" });
          if (!result?.ok) {
            showPanel("Reset failed", result?.message || "Could not reset the protected unplaced order.", null, null);
          }
        },
      );
      return;
    }
    showPanel(
      "Nutricity fulfilment",
      "More than one matching Amazon history order was found. Verifying for up to two minutes instead of writing the wrong Amazon order ID.",
      null,
      null,
    );
    return;
  }
  if (!orders.length && !rememberedOrder) {
    const lookupAge = Date.now() - Number(activeJob.orderHistoryLookupStartedAt || Date.now());
    if (lookupAge > 120000) {
      const message = `Amazon Place Order was submitted for ${activeJobOrderLabel(activeJob) || activeJob.job.group_key}, but no matching Amazon order appeared after ${Math.round(lookupAge / 1000)} seconds. This order remains locked; the queue will not continue until its Amazon order ID is verified and reported to Odoo.`;
      activeJob.paused = true;
      activeJob.pausedStage = "find_order_id";
      activeJob.lastError = message;
      await setActiveJob(activeJob);
      showPanel(
        "Chrome fulfilment held for verification",
        message,
        "Reset and retry unplaced order",
        async () => {
          const result = await send({ type: "RESET_DUPLICATE_FULFILMENT" });
          if (!result?.ok) {
            showPanel("Reset failed", result?.message || "Could not reset the protected unplaced order.", null, null);
          }
        },
      );
      return;
    }
    showPanel("Nutricity fulfilment", `Looking for recent Amazon order for ${activeJob.job.recipient_name}.`, null, null);
    return;
  }
  await reportAmazonOrders(activeJob, orders.length ? orders : [rememberedOrder]);
}

async function reportPostSubmitUnplaced(activeJob, message) {
  const item = activeJob.job?.items?.[Number(activeJob.itemIndex || 0)] || activeJob.job?.items?.[0] || null;
  const purchased = item ? selectedVariantItem(activeJob, item) : null;
  const missingAsin = String(purchased?.asin || item?.asin || "").toUpperCase();
  const result = await send({
    type: "POST_SUBMIT_UNPLACED",
    message,
    missingAsin,
    missingLineId: item ? itemPrimaryLineId(item) : null,
    failureCode: "post_submit_out_of_stock",
  });
  showPanel("Missing ASINs", result?.message || message, null, null);
  return result;
}

async function reportAmazonOrder(activeJob, orderId) {
  return reportAmazonOrders(activeJob, [{ amazon_order_id: orderId, amazon_order_url: orderDetailsUrl(orderId), asins: [] }]);
}

function buildOrderMappings(activeJob, orders) {
  if (!Array.isArray(orders) || !orders.length) return [];
  const items = activeJob.job?.items || [];
  const mappings = [];
  const singleOrder = orders.length === 1 ? orders[0] : null;
  for (const item of items) {
    const requestedAsin = String(item.asin || "").toUpperCase();
    const purchasedAsin = String(activeJob.pricing?.[requestedAsin]?.purchased_asin || "").toUpperCase();
    const itemAsins = [requestedAsin, purchasedAsin].filter(Boolean);
    let order = orders.find((candidate) => {
      const candidateAsins = (candidate.asins || []).map((asin) => String(asin || "").toUpperCase()).filter(Boolean);
      return candidateAsins.length && itemAsins.some((asin) => candidateAsins.includes(asin));
    });
    if (!order && singleOrder) {
      order = singleOrder;
    }
    if (!order) continue;
    mappings.push({
      asin: requestedAsin,
      line_ids: item.line_ids || [],
      amazon_order_id: order.amazon_order_id,
      amazon_order_url: order.amazon_order_url || orderDetailsUrl(order.amazon_order_id),
    });
  }
  return mappings;
}

function requiresAsinMappedReporting(activeJob, orders) {
  const items = activeJob.job?.items || [];
  const expectedLineIds = new Set(items.flatMap((item) => item.line_ids || []).map(Number).filter(Boolean));
  const ordersWithAsins = (orders || []).filter((order) => (order.asins || []).length);
  return expectedLineIds.size > 1 && ordersWithAsins.length > 0;
}

function unmappedReportingLineIds(activeJob, orderMappings) {
  const expectedLineIds = new Set((activeJob.job?.items || []).flatMap((item) => item.line_ids || []).map(Number).filter(Boolean));
  for (const mapping of orderMappings || []) {
    for (const lineId of mapping.line_ids || []) {
      expectedLineIds.delete(Number(lineId || 0));
    }
  }
  return [...expectedLineIds].filter(Boolean);
}

async function reportAmazonOrders(activeJob, orders) {
  const uniqueOrders = [];
  const seen = new Set();
  for (const order of orders || []) {
    const orderId = String(order.amazon_order_id || "").trim();
    if (!orderId || seen.has(orderId)) continue;
    seen.add(orderId);
    uniqueOrders.push({
      amazon_order_id: orderId,
      amazon_order_url: order.amazon_order_url || orderDetailsUrl(orderId),
      order_date: order.order_date || "",
      recipient: String(order.recipient || "").replace(/\s+/g, " ").trim(),
      asins: (order.asins || []).map((asin) => String(asin || "").toUpperCase()).filter(Boolean),
    });
  }
  if (!uniqueOrders.length) {
    activeJob.stage = "find_order_id";
    activeJob.reportedOrderId = "";
    activeJob.reportAttemptedAt = null;
    activeJob.reportError = "Amazon order history did not provide a valid order ID.";
    await setActiveJob(activeJob, {
      allowUnpause: true,
      allowStageRegression: true,
      reason: "invalid_order_history_report_candidate",
    });
    await sendDiagnostic("Blocked an empty Amazon order completion report.", {
      group_key: activeJob?.job?.group_key || "",
      candidates: orders || [],
    }, "error");
    showPanel(
      "Nutricity fulfilment",
      `Amazon has not exposed a valid order number for ${activeJobOrderLabel(activeJob) || activeJob.job.group_key} yet. Continuing safe order-history verification.`,
      null,
      null,
    );
    return false;
  }
  const unsafeOrders = uniqueOrders.filter((order) => !recentOrderMatchesActiveJob(order, activeJob));
  if (unsafeOrders.length) {
    const message = `Refused to report Amazon order ${unsafeOrders.map((order) => order.amazon_order_id).join(", ")}: its history recipient or ASINs do not match ${recipientName(activeJob)}.`;
    activeJob.paused = true;
    activeJob.pausedStage = "reporting_complete";
    activeJob.reportError = message;
    await setActiveJob(activeJob);
    await sendDiagnostic("Blocked unsafe Amazon history completion report.", {
      group_key: activeJob?.job?.group_key || "",
      expected_recipient: recipientName(activeJob),
      expected_asins: activeJobAsins(activeJob),
      candidates: unsafeOrders,
    }, "error");
    showPanel("Nutricity reporting needs attention", `${message} The order was not marked placed in the app.`, null, null);
    return false;
  }
  const orderId = uniqueOrders[0]?.amazon_order_id || "";
  const orderLabel = uniqueOrders.map((order) => order.amazon_order_id).join(", ");
  const orderMappings = buildOrderMappings(activeJob, uniqueOrders);
  if (requiresAsinMappedReporting(activeJob, uniqueOrders)) {
    const unmappedLineIds = unmappedReportingLineIds(activeJob, orderMappings);
    if (unmappedLineIds.length) {
      const expectedAsins = activeJobAsins(activeJob).join(", ");
      const foundAsins = [...new Set(uniqueOrders.flatMap((order) => order.asins || []))].join(", ");
      const message = `Amazon order ${orderLabel || orderId} was found, but ASINs did not map to every app line. Expected ${expectedAsins || "app ASINs"}, found ${foundAsins || "no Amazon ASINs"}.`;
      activeJob.paused = true;
      activeJob.pausedStage = "reporting_complete";
      activeJob.reportError = message;
      await setActiveJob(activeJob);
      showPanel(
        "Nutricity reporting needs attention",
        `${message} Reporting paused so the wrong Amazon order number is not written to unrelated rows.`,
        "Retry reporting",
        () => continueAfterManualStep(activeJob, "reporting_complete"),
      );
      return false;
    }
  }
  const reportKey = `${activeJob.job?.group_key || "job"}:${orderLabel || orderId}`;
  window.__nutricityOrderReportLocks = window.__nutricityOrderReportLocks || {};
  window.__nutricityOrderReportCompleted = window.__nutricityOrderReportCompleted || {};
  const now = Date.now();
  if (window.__nutricityOrderReportCompleted[reportKey] && now - window.__nutricityOrderReportCompleted[reportKey] < 120000) {
    showPanel("Nutricity fulfilment", `Amazon order ${orderLabel || orderId} was already reported. Waiting for the next queued order.`, null, null);
    return true;
  }
  if (window.__nutricityOrderReportLocks[reportKey] && now - window.__nutricityOrderReportLocks[reportKey] < 60000) {
    showPanel("Nutricity fulfilment", `Amazon order ${orderLabel || orderId} is already being reported. Waiting for the app response.`, null, null);
    return false;
  }
  window.__nutricityOrderReportLocks[reportKey] = now;
  showPanel("Nutricity fulfilment", `Found Amazon order ${orderLabel || orderId}. Reporting back to the app.`, null, null);
  await sendDiagnostic("Reporting Amazon order back to the app.", {
    group_key: activeJob?.job?.group_key || "",
    order_ids: uniqueOrders.map((order) => order.amazon_order_id),
    order_mappings: orderMappings,
  });
  activeJob.stage = "reporting_complete";
  activeJob.reportedOrderId = orderLabel || orderId;
  activeJob.reportAttemptedAt = Date.now();
  await setActiveJob(activeJob, { reason: "reporting_amazon_order" });
  let result = null;
  try {
    for (let attempt = 1; attempt <= 4; attempt += 1) {
      result = await send({
        type: "COMPLETE_JOB",
        groupKey: activeJob?.job?.group_key || "",
        workerId: activeJob?.workerId || "",
        orderId,
        orderUrl: uniqueOrders[0]?.amazon_order_url || orderDetailsUrl(orderId),
        orderDate: uniqueOrders[0]?.order_date || "",
        orderMappings,
        amazonAccountName: amazonSignedInAccountName(),
        amazonRecipient: uniqueOrders[0]?.recipient || "",
        amazonAsins: uniqueOrders[0]?.asins || [],
        page: diagnosticPageInfo(),
      });
      if (result?.ok) break;
      const message = normalizedText(result?.message || "");
      const transient = /internal server error|pool|timeout|failed to fetch|network|temporarily/.test(message);
      if (!transient || attempt === 4) break;
      showPanel("Nutricity fulfilment", `The app was busy reporting ${orderId}. Retrying ${attempt + 1}/4.`, null, null);
      await sleep(1500 * attempt);
    }
    if (!result?.ok) {
      const message = result?.message || "Could not report Amazon order back to the app.";
      activeJob.paused = true;
      activeJob.pausedStage = "reporting_complete";
      activeJob.reportError = message;
      await setActiveJob(activeJob);
      showPanel(
        "Nutricity reporting needs attention",
        `${message} Amazon order ${orderLabel || orderId} was found, but the app did not confirm completion.`,
        "Retry reporting",
        () => continueAfterManualStep(activeJob, "reporting_complete"),
      );
      delete window.__nutricityOrderReportLocks[reportKey];
      return false;
    }
    const returnedOrderId = String(result.amazon_order_id || "").trim();
    const reportedOrderIds = new Set(uniqueOrders.map((order) => order.amazon_order_id));
    if (returnedOrderId && reportedOrderIds.size && !reportedOrderIds.has(returnedOrderId)) {
      const message = `The app returned Amazon order ${returnedOrderId}, but the extension reported ${orderLabel || orderId}.`;
      activeJob.paused = true;
      activeJob.pausedStage = "reporting_complete";
      activeJob.reportError = message;
      await setActiveJob(activeJob);
      showPanel(
        "Nutricity reporting needs attention",
        `${message} Reporting paused so the wrong Amazon order number is not treated as completed.`,
        "Retry reporting",
        () => continueAfterManualStep(activeJob, "reporting_complete"),
      );
      await sendDiagnostic("App returned a different Amazon order ID than the one reported.", {
        group_key: activeJob?.job?.group_key || "",
        reported_order_ids: [...reportedOrderIds],
        returned_order_id: returnedOrderId,
        response: result,
      }, "error");
      delete window.__nutricityOrderReportLocks[reportKey];
      return false;
    }
    window.__nutricityOrderReportCompleted[reportKey] = Date.now();
    for (const order of uniqueOrders) {
      orderHistoryLookupCache.delete(order.amazon_order_id);
      orderHistorySyncedConfirmations.set(order.amazon_order_id, {
        syncedAt: Date.now(),
        message: result.message || `Reported Amazon order ${order.amazon_order_id}.`,
      });
    }
    showPanel("Nutricity fulfilment", result.message || `Reported Amazon order ${orderId}.`, null, null);
    return true;
  } finally {
    if (!window.__nutricityOrderReportCompleted[reportKey]) {
      delete window.__nutricityOrderReportLocks[reportKey];
    }
  }
}

async function autoResumeResolvedCheckoutPause(activeJob) {
  if (
    submittedOrPausedStage(activeJob) &&
    (
      confirmationSaysPlaced() ||
      extractOrderId() ||
      isOrderHistoryPage() ||
      activeJobWasSubmittedToAmazon(activeJob)
    )
  ) {
    const next = {
      ...activeJob,
      stage: extractOrderId() ? "reporting_complete" : "find_order_id",
      paused: false,
      pausedStage: null,
      amazonSubmittedAt: activeJob.amazonSubmittedAt || Date.now(),
    };
    await setActiveJob(next, { allowUnpause: true, reason: "auto_resume_submitted_pause" });
    showPanel("Nutricity fulfilment", "Amazon order was submitted. Continuing to order history to capture the order number.", null, null);
    return true;
  }
  if (!/\/checkout/i.test(location.pathname)) return false;
  const pausedStage = String(activeJob?.pausedStage || activeJob?.stage || "");
  if (!["checkout", "editing_address"].includes(pausedStage)) return false;
  const placeOrder = findPlaceOrderButton();
  if (
    placeOrder &&
    !placeOrder.disabled &&
    !findAddressNameInput()
  ) {
    // A reload can preserve a manual editing_address pause after Amazon has
    // already returned to the final checkout page. Do not require the stale
    // address/payment parser result to clear that pause: the normal checkout
    // path immediately re-verifies recipient, warehouse, payment, exact unit
    // count, delivery window, and SNS confirmation before Place Order. This
    // lets quantity mismatches (for example Amazon reducing 7 units to 5) be
    // reported instead of leaving the worker frozen behind an obsolete pause.
    const next = {
      ...activeJob,
      stage: "checkout",
      paused: false,
      pausedStage: null,
      editAddressClickedAt: null,
      addressEditedRecipient: checkoutRecipient,
      addressEditedAt: Date.now(),
      addressVerifiedRecipient: checkoutRecipient,
      addressVerifiedAt: Date.now(),
    };
    await setActiveJob(next, { allowUnpause: true, allowStageRegression: true, reason: "auto_resume_checkout_ready" });
    showPanel("Nutricity checkout", "Final checkout is visible. Re-running all recipient, payment, quantity, and delivery guards.", null, null);
    return true;
  }
  return false;
}

async function run() {
  if (!extensionContextAlive || fulfilmentForceStopped) return;
  const now = Date.now();
  if (!window.__nutricityHadActiveJob) {
    if (document.hidden && !lastNoActiveJobCheckAt) {
      lastNoActiveJobCheckAt = now;
      return;
    }
    if (lastNoActiveJobCheckAt && now - lastNoActiveJobCheckAt < IDLE_ACTIVE_JOB_POLL_MS) return;
  }
  const activeJob = await getActiveJob();
  if (!activeJob?.job || !/amazon\.com$/i.test(location.hostname)) {
    window.__nutricityHadActiveJob = false;
    lastNoActiveJobCheckAt = now;
    return;
  }
  window.__nutricityHadActiveJob = true;
  lastNoActiveJobCheckAt = 0;
  const runDiagnosticKey = `${activeJob.job.group_key}:${location.pathname}:${activeJob.stage || ""}`;
  if (window.__nutricityLastRunDiagnosticKey !== runDiagnosticKey) {
    window.__nutricityLastRunDiagnosticKey = runDiagnosticKey;
    await sendDiagnostic("Content script is running with an active fulfilment job.", {
      group_key: activeJob.job.group_key,
      stage: activeJob.stage || "",
      item_index: activeJob.itemIndex || 0,
      build: CONTENT_SCRIPT_BUILD,
    });
  }
  if (activeJob.paused) {
    if (activeJob.pausedByUser) {
      showPanel(
        "Nutricity fulfilment paused",
        "Fulfilment is paused. No page actions will continue until you click Resume.",
        "I did it manually, continue",
        () => continueAfterManualStep(activeJob),
      );
      return;
    }
    if (await autoResumeResolvedCheckoutPause(activeJob)) {
      setTimeout(runSafely, 250);
      return;
    }
    showPanel(
      "Nutricity fulfilment paused",
      "Fulfilment is paused. Click Resume to retry this step, or continue if you completed it manually.",
      "I did it manually, continue",
      () => continueAfterManualStep(activeJob),
    );
    return;
  }
  // Handle this location before generic page guards and submitted-order history
  // recovery. The DOM can be blank briefly while Amazon renders the button.
  if (amazonDuplicateOrderRoute() || amazonDuplicateOrderPage()) {
    try {
      return await handleAmazonDuplicateOrderPage(activeJob);
    } catch (error) {
      showPanel("Nutricity fulfilment error", error.message || String(error), null, null);
      return;
    }
  }
  if (await guardUnexpectedAmazonPage(activeJob)) return;
  const postSubmitUnplaced = amazonPostSubmitUnplacedIssue();
  if (postSubmitUnplaced && submittedOrPausedStage(activeJob)) {
    await reportPostSubmitUnplaced(activeJob, postSubmitUnplaced);
    return;
  }
  if (await recoverBlockedSnsSubmit(activeJob)) return;
  if (
    /\/checkout/i.test(location.pathname)
    && findPlaceOrderButton()
    && activeJob.placeOrderClickStartedAt
    && Date.now() - Number(activeJob.placeOrderClickStartedAt || 0) < 45000
  ) {
    showPanel("Final step", "Place Order click is in progress. Waiting for Amazon confirmation.", null, null);
    return;
  }
  if (activeJobWasSubmittedToAmazon(activeJob)) {
    activeJob.stage = activeJob.stage === "reporting_complete" ? "reporting_complete" : "find_order_id";
    activeJob.amazonSubmittedAt = activeJob.amazonSubmittedAt || Date.now();
    await setActiveJob(activeJob);
    await handleOrderHistory(activeJob);
    return;
  }
  if (/\/checkout/i.test(location.pathname) && findPlaceOrderButton() && !submittedStage(activeJob)) {
    activeJob.stage = "checkout";
    await setActiveJob(activeJob);
    const checkoutRecipient = recipientName(activeJob);
    if (!await verifyCheckoutDeliveryRecipient(activeJob, checkoutRecipient)) return;
    if (!await ensurePreferredCheckoutPayment(activeJob)) return;
    if (!await ensureCheckoutOnlyExpectedUnits(activeJob)) return;
    if (!await ensureSubscribeCheckoutQuantity(activeJob)) return;
    await handleCheckout(activeJob);
    return;
  }
  showPanel(
    "Nutricity fulfilment",
    `Working on ${activeJobOrderLabel(activeJob) || activeJob.job.group_key}.`,
    null,
    null,
  );
  try {
    if (amazonOrderSubmitErrorPage() && submittedOrPausedStage(activeJob)) {
      showPanel("Nutricity fulfilment", "Amazon showed an error after order submit. Opening recent orders to verify whether the order was placed.", null, null);
      activeJob.stage = "find_order_id";
      await setActiveJob(activeJob);
      location.href = orderHistoryUrl();
      return;
    }
    const postSubmitIssue = amazonPostSubmitUnplacedIssue();
    if (postSubmitIssue && submittedOrPausedStage(activeJob)) {
      await reportPostSubmitUnplaced(activeJob, postSubmitIssue);
      return;
    }
    if (activeJobWasSubmittedToAmazon(activeJob)) {
      activeJob.stage = activeJob.stage === "reporting_complete" ? "reporting_complete" : "find_order_id";
      activeJob.amazonSubmittedAt = activeJob.amazonSubmittedAt || Date.now();
      await setActiveJob(activeJob);
      await handleOrderHistory(activeJob);
      return;
    }
    if (submittedStage(activeJob)) {
      if (activeJob.stage === "reporting_complete") {
        showPanel("Nutricity fulfilment", "Amazon order was reported. Waiting for the extension to start the next queued order.", null, null);
      } else if (activeJob.stage === "complete_pending") {
        await handleCompletion(activeJob);
      } else {
        activeJob.stage = "find_order_id";
        await setActiveJob(activeJob);
        await handleOrderHistory(activeJob);
      }
    } else if (/\/checkout/i.test(location.pathname) && await handleCheckoutLimitPurchase(activeJob)) return;
    else if (activeJob.stage === "cleanup_after_failure") {
      await handleFailureCleanup(activeJob);
    } else if (activeJob.stage === "reporting_complete") {
      showPanel("Nutricity fulfilment", "Amazon order was reported. Waiting for the extension to start the next queued order.", null, null);
    } else if (activeJob.stage === "duplicate_order") {
      const duplicateCheck = await send({ type: "CHECK_EXISTING_AMAZON_ORDER" });
      if (duplicateCheck?.ok && !duplicateCheck.duplicate) {
        activeJob.stage = "checkout";
        activeJob.duplicateOrder = null;
        await setActiveJob(activeJob);
        await handleCheckout(activeJob);
        return;
      }
      const orders = (activeJob.duplicateOrder?.orders || []).map((item) => item.amazon_order_id).filter(Boolean).join(", ");
      showPanel(
        "Amazon order already exists",
        `${orders || "An Amazon order"} is already saved in the app. Open the extension popup to review or clear it before continuing.`,
        null,
        null,
      );
    } else if (activeJob.stage === "complete_pending") {
      await handleCompletion(activeJob);
    } else if (activeJob.stage === "subscribe_checkout") {
      await handleSubscribeCheckout(activeJob);
    } else if (activeJob.stage === "add_clicked") {
      await handleAddClicked(activeJob);
    } else if (activeJob.stage === "find_order_id") {
      activeJob.stage = "find_order_id";
      await setActiveJob(activeJob);
      await handleOrderHistory(activeJob);
    } else if (/\/cart/i.test(location.pathname)) {
      if (checkoutWasStarted(activeJob) && !["clear_cart", "cleanup_after_failure"].includes(activeJob.stage)) {
        const cartCheck = verifyCartQuantities(activeJob);
        const retryCount = Number(activeJob.cartAfterCheckoutRetryCount || 0);
        if (shouldRetryVerifiedCartAfterCheckout(activeJob, cartCheck)) {
          activeJob.cartAfterCheckoutRetryCount = retryCount + 1;
          activeJob.stage = "cart";
          activeJob.paused = false;
          activeJob.pausedStage = null;
          clearCheckoutStarted(activeJob);
          await setActiveJob(activeJob, {
            allowUnpause: true,
            allowStageRegression: true,
            reason: "retry_verified_cart_after_checkout_return",
          });
          await sendDiagnostic("Amazon Business returned to a verified cart during checkout; retrying checkout once.", {
            group_key: activeJob.job?.group_key || "",
            quantities: cartCheck.quantities || {},
            retry_count: activeJob.cartAfterCheckoutRetryCount,
          }, "warn");
          showPanel(
            "Retrying checkout",
            "Amazon returned to the cart, but the complete expected order is still present. Retrying checkout once.",
            null,
            null,
          );
          await handleCart(activeJob);
          return;
        }
        const message = `Amazon returned to the cart after checkout had already started for ${activeJobOrderLabel(activeJob) || activeJob.job.group_key}. The extension stopped this job as a Chrome error instead of marking it Missing so the next order cannot inherit a stale cart page.`;
        showPanel("Checkout returned to cart", message, null, null);
        await send({
          type: "FAIL_JOB",
          message,
          missingAsin: "",
          missingLineId: null,
          failureCode: "cart_after_checkout",
        });
        return;
      }
      if (!["clear_cart", "cleanup_after_failure"].includes(activeJob.stage)) {
        activeJob.stage = "cart";
      }
      await setActiveJob(activeJob);
      await handleCart(activeJob);
    } else if (/\/checkout/i.test(location.pathname)) {
      if (activeJob.stage !== "editing_address") {
        activeJob.stage = "checkout";
      }
      await setActiveJob(activeJob);
      await handleCheckout(activeJob);
    } else {
      activeJob.stage = "product";
      await setActiveJob(activeJob);
      await handleProduct(activeJob);
    }
  } catch (error) {
    showPanel("Nutricity fulfilment error", error.message, null, null);
    const item = activeJob.job.items?.[activeJob.itemIndex];
    const purchaseItem = item ? selectedVariantItem(activeJob, item) : item;
    const shouldMarkItemMissing = Boolean(error.failureCode || error.missingAsin);
    if (!shouldMarkItemMissing) {
      activeJob.pausedStage = activeJob.stage || "product";
      activeJob.paused = true;
      activeJob.lastError = String(error.message || error || "Unknown fulfilment error").slice(0, 500);
      await setActiveJob(activeJob);
      showPanel(
        "Nutricity fulfilment needs attention",
        `${error.message} Complete the stuck Amazon step manually, then continue.`,
        "I did it manually, continue",
        () => continueAfterManualStep(activeJob),
      );
      return;
    }
    if (await continueAfterPartialMissing(activeJob, item, purchaseItem, error.message, error.failureCode || "unavailable", {
      requestedQuantity: error.requestedQuantity ?? null,
      fulfilledQuantity: error.fulfilledQuantity ?? null,
      availableQuantity: error.availableQuantity ?? null,
    })) {
      return;
    }
    let failResult = null;
    try {
      failResult = await send({
        type: "FAIL_JOB",
        message: error.message,
        missingAsin: error.missingAsin || item?.asin || purchaseItem?.asin || "",
        missingLineId: item ? itemPrimaryLineId(item) : null,
        failureCode: error.failureCode || "",
        requestedQuantity: error.requestedQuantity ?? null,
        fulfilledQuantity: error.fulfilledQuantity ?? null,
        availableQuantity: error.availableQuantity ?? null,
      });
    } catch (reportError) {
      await pauseAfterMissingAsinReportFailure(activeJob, reportError.message || "Could not send the Missing ASIN report to the app.");
      return;
    }
    if (!failResult?.ok) {
      await pauseAfterMissingAsinReportFailure(activeJob, failResult?.message || "The app did not confirm the Missing ASIN report.");
      return;
    }
    const reason = error.message || "No reason supplied.";
    const nextMessage = failResult.next_job_started && failResult.next_group_key
      ? `Reason: ${reason} Order moved to Missing ASINs. Starting next order ${failResult.next_group_key}.`
      : `Reason: ${reason} Order moved to Missing ASINs.`;
    showPanel("Missing ASINs", nextMessage, null, null);
  }
}

async function runSafely() {
  if (fulfilmentForceStopped) return;
  if (window.__nutricityRunning) {
    if (Date.now() - Number(window.__nutricityRunningAt || 0) < CONTENT_RUN_STALE_MS) return;
    console.warn("Nutricity fulfilment: recovering from a stale content-script run.");
  }
  window.__nutricityRunning = true;
  window.__nutricityRunningAt = Date.now();
  try {
    await run();
  } catch (error) {
    if (!error?.fulfilmentPaused) throw error;
  } finally {
    window.__nutricityRunning = false;
    window.__nutricityRunningAt = 0;
  }
}

function startContentAutomationLoops() {
  if (!runIntervalId) {
    runIntervalId = setInterval(runSafely, 5000);
  }
  if (!panelIntervalId) {
    panelIntervalId = setInterval(() => {
      if (!fulfilmentForceStopped && window.__nutricityHadActiveJob) keepPanelAlive().catch(() => undefined);
    }, 1000);
  }
  ensureOrderHistoryAnnotationLoop();
}

function activateContentAutomation() {
  fulfilmentForceStopped = false;
  lastNoActiveJobCheckAt = 0;
  startContentAutomationLoops();
  scheduleOrderHistoryAnnotation(250);
  if (!window.__nutricityRunning) setTimeout(runSafely, 0);
}

if (document.hidden) {
  lastNoActiveJobCheckAt = Date.now();
} else {
  setTimeout(runSafely, 250);
}
scheduleOrderHistoryAnnotation(250);
registerContentCleanup(() => clearTimeout(orderHistoryAnnotationTimer));
startContentAutomationLoops();
registerContentCleanup(() => clearInterval(runIntervalId));
registerContentCleanup(() => clearInterval(panelIntervalId));

const onPageShow = () => {
  lastNoActiveJobCheckAt = 0;
  if (!fulfilmentForceStopped) setTimeout(runSafely, 250);
  scheduleOrderHistoryAnnotation(250);
};
window.addEventListener("pageshow", onPageShow);
registerContentCleanup(() => window.removeEventListener("pageshow", onPageShow));

const onFocus = () => {
  lastNoActiveJobCheckAt = 0;
  if (!fulfilmentForceStopped) setTimeout(runSafely, 250);
  scheduleOrderHistoryAnnotation(250);
};
window.addEventListener("focus", onFocus);
registerContentCleanup(() => window.removeEventListener("focus", onFocus));

const onHashChange = () => {
  scheduleOrderHistoryAnnotation(500);
};
window.addEventListener("hashchange", onHashChange);
registerContentCleanup(() => window.removeEventListener("hashchange", onHashChange));

const onPopState = () => {
  scheduleOrderHistoryAnnotation(500);
};
window.addEventListener("popstate", onPopState);
registerContentCleanup(() => window.removeEventListener("popstate", onPopState));

const onScroll = () => {
  if (!/amazon\.com$/i.test(location.hostname) || (!isOrderHistoryPage() && !isOrderDetailsPage())) return;
  clearTimeout(orderHistoryScrollTimer);
  orderHistoryScrollTimer = setTimeout(() => scheduleOrderHistoryAnnotation(0), 900);
};
window.addEventListener("scroll", onScroll, { passive: true });
registerContentCleanup(() => {
  clearTimeout(orderHistoryScrollTimer);
  window.removeEventListener("scroll", onScroll);
});

const onVisibilityChange = () => {
  if (!document.hidden) {
    lastNoActiveJobCheckAt = 0;
    if (!fulfilmentForceStopped) setTimeout(runSafely, 250);
    scheduleOrderHistoryAnnotation(250);
  }
};
document.addEventListener("visibilitychange", onVisibilityChange);
registerContentCleanup(() => document.removeEventListener("visibilitychange", onVisibilityChange));

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === "NUTRICITY_CONTENT_PING") {
    sendResponse({ ok: true, build: CONTENT_SCRIPT_BUILD });
    return true;
  }
  if (message.type === "RUN_ACTIVE_JOB") {
    activateContentAutomation();
    sendResponse({ ok: true });
    return true;
  }
  if (message.type === "NUTRICITY_DISABLE_NON_WORKER") {
    stopContentAutomation("This Amazon tab is not the designated fulfilment worker.");
    sendResponse({ ok: true, disabled: true });
    return true;
  }
  if (message.type !== "CHECK_ASIN_AVAILABILITY") return false;
  try {
    sendResponse(checkAsinAvailability(message.asin, message.deliveryLimitDays));
  } catch (error) {
    sendResponse({
      ok: false,
      in_stock: false,
      message: error.message || String(error),
      url: location.href,
    });
  }
  return true;
});
})();
