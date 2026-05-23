const DEFAULT_API_BASE = "http://127.0.0.1:8000";
const completionLocks = new Set();
let queueStatusInFlight = null;
let lastReleaseMissingWindowJobsAt = 0;
let releaseMissingWindowJobsInFlight = null;
const recoverSubmittedJobsInFlight = new Map();
let lastEmptyRecoverSubmittedJobAt = 0;
let startNextJobInFlight = null;
const claimNextJobInFlight = new Map();
const MISSING_ASIN_ALARM = "nutricity-missing-asin-availability";
const MISSING_ASIN_CHECK_PERIOD_MINUTES = 24 * 60;
const BROWSERLESS_SWITCH_POLL_MS = 2000;
const BROWSERLESS_SWITCH_TIMEOUT_MS = 10 * 60 * 1000;

async function getSettings() {
  const data = await chrome.storage.local.get({
    apiBase: DEFAULT_API_BASE,
    adminToken: "",
    cardLast4Preference: "",
    editExistingAddress: true,
    fulfilAvailableMixedAsin: false,
    splitMixedAsinOrders: true,
    browserlessOrderMode: false,
    workerId: "",
    activeJob: null,
    activeJobsByWindow: {},
    controlWindowsById: {},
    missingAsinAvailabilityLastRunAt: 0,
    availabilityCheckInFlight: false,
    recentAmazonOrders: [],
    cachedQueueStatus: null,
    orderProgress: null,
    fulfilmentActivity: { last: null },
    forceStop: { active: false, stoppedAt: 0, reason: "" },
    logs: [],
    logsByWindow: {},
  });
  return data;
}

async function getWorkerId() {
  const { workerId } = await chrome.storage.local.get({ workerId: "" });
  if (workerId) return workerId;
  const next = `chrome-${crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
  await chrome.storage.local.set({ workerId: next });
  return next;
}

function normalizeApiBase(value) {
  return String(value || DEFAULT_API_BASE).trim().replace(/\/+$/, "") || DEFAULT_API_BASE;
}

function messageWindowId(message = {}, sender = {}) {
  return Number(message.targetWindowId || sender.tab?.windowId || 0) || null;
}

async function getWindowState(windowId) {
  const state = await getSettings();
  const key = String(windowId || "");
  const windowJob = windowId ? state.activeJobsByWindow?.[key] || null : state.activeJob;
  const fallbackSubmittedJob = windowId && !windowJob && orderSubmitStarted(state.activeJob)
    ? state.activeJob
    : null;
  return {
    ...state,
    targetWindowId: windowId || null,
    activeJob: windowJob || fallbackSubmittedJob,
    logs: windowId ? state.logsByWindow?.[key] || [] : state.logs,
  };
}

async function setWindowJob(windowId, activeJob) {
  if (activeJob && await forceStopActive()) return;
  const state = await getSettings();
  const { activeJobsByWindow } = state;
  const next = { ...(activeJobsByWindow || {}) };
  const key = String(windowId || "");
  const current = windowId ? next[key] || null : state.activeJob || null;
  const sameGroup = current?.job?.group_key && activeJob?.job?.group_key === current.job.group_key;
  if (sameGroup && orderSubmitStarted(current) && !orderSubmitStarted(activeJob)) {
    return;
  }
  if (windowId && activeJob) {
    for (const [otherKey, otherJob] of Object.entries(next)) {
      if (otherKey === key || otherJob?.job?.group_key !== activeJob.job?.group_key) continue;
      if (orderSubmitStarted(otherJob) && !orderSubmitStarted(activeJob)) {
        await log(`Ignored duplicate active window ${key} for ${activeJob.job.group_key}; window ${otherKey} has already submitted.`, windowId);
        return;
      }
      delete next[otherKey];
      await log(`Cleared duplicate active window ${otherKey} for ${activeJob.job.group_key}; keeping ${key}.`, windowId);
    }
    next[key] = activeJob;
  }
  if (windowId && !activeJob) delete next[key];
  await chrome.storage.local.set({ activeJobsByWindow: next, activeJob: activeJob || Object.values(next)[0] || null });
}

async function clearStoredJobGroup(groupKey) {
  if (!groupKey) return;
  const state = await getSettings();
  const next = { ...(state.activeJobsByWindow || {}) };
  for (const [windowKey, activeJob] of Object.entries(next)) {
    if (activeJob?.job?.group_key === groupKey) delete next[windowKey];
  }
  const activeJob = state.activeJob?.job?.group_key === groupKey ? null : state.activeJob || null;
  await chrome.storage.local.set({ activeJobsByWindow: next, activeJob: activeJob || Object.values(next)[0] || null });
}

async function setControlWindow(controlWindowId, targetWindowId) {
  if (!controlWindowId) return;
  const { controlWindowsById } = await getSettings();
  const next = { ...(controlWindowsById || {}) };
  if (targetWindowId) {
    next[String(controlWindowId)] = targetWindowId;
  } else {
    delete next[String(controlWindowId)];
  }
  await chrome.storage.local.set({ controlWindowsById: next });
}

async function log(message, windowId = null) {
  const { logs, logsByWindow } = await getSettings();
  const entry = `${new Date().toLocaleTimeString()} ${message}`;
  if (!windowId) {
    await chrome.storage.local.set({ logs: [entry, ...logs].slice(0, 40) });
    return;
  }
  const key = String(windowId);
  const next = { ...(logsByWindow || {}) };
  next[key] = [entry, ...(next[key] || [])].slice(0, 40);
  await chrome.storage.local.set({ logsByWindow: next, logs: next[key] });
}

function jobLabel(job = {}) {
  const names = (job.order_names || []).map((name) => String(name || "").trim()).filter(Boolean);
  return names.join(", ") || job.group_key || "Unknown order";
}

function jobAsinSummary(job = {}) {
  return (job.items || []).map((item) => item.asin).filter(Boolean).join(", ");
}

function shortReason(value = "", fallback = "") {
  const text = String(value || fallback || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  return text.length > 180 ? `${text.slice(0, 177)}...` : text;
}

async function recordLastProcessed(activeJobOrJob, status, reason = "", extra = {}) {
  const job = activeJobOrJob?.job || activeJobOrJob || {};
  const entry = {
    group_key: job.group_key || "",
    label: jobLabel(job),
    asins: jobAsinSummary(job),
    status: status || "processed",
    reason: shortReason(reason),
    stage: activeJobOrJob?.stage || "",
    at: Date.now(),
    ...extra,
  };
  await chrome.storage.local.set({ fulfilmentActivity: { last: entry } });
  return entry;
}

async function setForceStop(active, reason = "") {
  const forceStop = {
    active: active === true,
    stoppedAt: active === true ? Date.now() : 0,
    reason: active === true ? shortReason(reason, "Force stopped by popup.") : "",
  };
  await chrome.storage.local.set({ forceStop });
  return forceStop;
}

async function forceStopActive() {
  const { forceStop } = await getSettings();
  return forceStop?.active === true;
}

async function setOrderProgress(progress) {
  await chrome.storage.local.set({ orderProgress: progress });
  return progress;
}

async function startOrderProgress(total = 0, message = "") {
  const progress = {
    running: true,
    total: Math.max(0, Math.round(Number(total || 0))),
    processed: 0,
    message: message || "Starting order run.",
    startedAt: Date.now(),
    updatedAt: Date.now(),
  };
  return setOrderProgress(progress);
}

async function incrementOrderProgress(message = "") {
  if (await forceStopActive()) {
    const { orderProgress } = await getSettings();
    return orderProgress || {};
  }
  const { orderProgress } = await getSettings();
  const current = orderProgress || {};
  const processed = Math.max(0, Math.round(Number(current.processed || 0))) + 1;
  const total = Math.max(Math.round(Number(current.total || 0)), processed);
  return setOrderProgress({
    ...current,
    running: processed < total,
    total,
    processed,
    message: message || current.message || "Order progress updated.",
    startedAt: current.startedAt || Date.now(),
    updatedAt: Date.now(),
  });
}

async function updateOrderProgressTotal(total = 0, message = "") {
  const { orderProgress } = await getSettings();
  const current = orderProgress || {};
  const processed = Math.max(0, Math.round(Number(current.processed || 0)));
  const nextTotal = Math.max(processed, Math.round(Number(total || 0)));
  return setOrderProgress({
    ...current,
    running: processed < nextTotal,
    total: nextTotal,
    processed,
    message: message || current.message || "",
    startedAt: current.startedAt || Date.now(),
    updatedAt: Date.now(),
  });
}

function normalizeRecentAmazonOrder(order = {}) {
  const orderId = String(order.amazon_order_id || "").trim();
  if (!/^\d{3}-\d{7}-\d{7}$/.test(orderId)) return null;
  const items = (order.items || [])
    .map((item) => ({
      asin: String(item?.asin || "").trim().toUpperCase(),
      quantity: Math.max(1, Math.round(Number(item?.quantity || 1))),
    }))
    .filter((item) => item.asin);
  const asinQuantities = {};
  for (const item of items) {
    asinQuantities[item.asin] = (asinQuantities[item.asin] || 0) + item.quantity;
  }
  for (const asin of order.asins || []) {
    const normalizedAsin = String(asin || "").trim().toUpperCase();
    if (normalizedAsin && !asinQuantities[normalizedAsin]) asinQuantities[normalizedAsin] = 1;
  }
  return {
    amazon_order_id: orderId,
    amazon_order_url: order.amazon_order_url || `https://www.amazon.com/your-orders/order-details?orderID=${encodeURIComponent(orderId)}`,
    recipient: String(order.recipient || "").replace(/\s+/g, " ").trim(),
    order_date: String(order.order_date || "").replace(/\s+/g, " ").trim(),
    status: String(order.status || "").replace(/\s+/g, " ").trim(),
    asins: Object.keys(asinQuantities),
    items,
    asin_quantities: asinQuantities,
    cancelled: order.cancelled === true,
    captured_at: Number(order.captured_at || Date.now()),
  };
}

async function rememberRecentAmazonOrders(orders = []) {
  const incoming = (orders || []).map(normalizeRecentAmazonOrder).filter(Boolean);
  if (!incoming.length) return { ok: true, remembered: 0, orders: [] };
  const { recentAmazonOrders } = await getSettings();
  const byId = new Map();
  for (const order of [...incoming, ...(recentAmazonOrders || [])]) {
    if (!order?.amazon_order_id || byId.has(order.amazon_order_id)) continue;
    byId.set(order.amazon_order_id, normalizeRecentAmazonOrder(order));
  }
  const next = [...byId.values()].filter(Boolean).sort((a, b) => Number(b.captured_at || 0) - Number(a.captured_at || 0)).slice(0, 10);
  await chrome.storage.local.set({ recentAmazonOrders: next });
  return { ok: true, remembered: incoming.length, orders: next };
}

async function lookupAmazonHistoryOrders(orders = []) {
  const normalized = (orders || []).map(normalizeRecentAmazonOrder).filter(Boolean);
  if (!normalized.length) return { ok: true, matches: {}, unmatched: [], not_found_url: "/amazon-order-history-unmatched" };
  const result = await api("/api/chrome/order-history/lookup", {
    method: "POST",
    body: JSON.stringify({ orders: normalized }),
    timeoutMs: 12000,
  });
  const { apiBase } = await getSettings();
  const base = normalizeApiBase(apiBase);
  return { app_base_url: base, ...result, not_found_url: `${base}${result.not_found_url || "/amazon-order-history-unmatched"}` };
}

async function syncAmazonHistoryOrder(order = {}) {
  const normalized = normalizeRecentAmazonOrder(order);
  if (!normalized) return { ok: false, message: "Amazon order ID is not valid." };
  const orderNames = (order.order_names || [])
    .map((name) => String(name || "").trim())
    .filter(Boolean);
  const lineIds = (order.line_ids || [])
    .map((lineId) => Number(lineId || 0))
    .filter((lineId) => Number.isFinite(lineId) && lineId > 0);
  const result = await api("/api/manual-amazon/match", {
    method: "POST",
    body: JSON.stringify({
      amazon_order_id: normalized.amazon_order_id,
      amazon_order_url: normalized.amazon_order_url,
      amazon_account_name: order.amazon_account_name || "Chrome History Matcher",
      order_date: normalized.order_date || order.order_date || "",
      order_names: orderNames,
      line_ids: lineIds,
      source_text: order.source_text || normalized.recipient || "",
      store_id: order.store_id || null,
      replace_existing: order.replace_existing === true,
    }),
    timeoutMs: 120000,
  });
  return result;
}

async function api(path, options = {}) {
  const { apiBase, adminToken } = await getSettings();
  const base = normalizeApiBase(apiBase);
  const requestPath = String(path || "").startsWith("/") ? path : `/${path}`;
  const { timeoutMs = 45000, ...fetchOptions } = options;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), Number(timeoutMs || 45000));
  const response = await fetch(`${base}${requestPath}`, {
    headers: { "Content-Type": "application/json", ...(adminToken ? { "X-Admin-Token": adminToken } : {}), ...(fetchOptions.headers || {}) },
    signal: controller.signal,
    ...fetchOptions,
  }).finally(() => clearTimeout(timeout));
  if (!response.ok) {
    throw new Error((await response.text()) || response.statusText);
  }
  return response.json();
}

function connectionErrorMessage(error, base = "") {
  const raw = String(error?.message || error || "Failed to fetch");
  if (/failed to fetch|networkerror|load failed|abort/i.test(raw)) {
    return `Could not reach the local app at ${base || DEFAULT_API_BASE}. Make sure the app is running on port 8000, then click Save and Check connection again.`;
  }
  return raw;
}

async function testConnection() {
  const { apiBase, adminToken } = await getSettings();
  const base = normalizeApiBase(apiBase);
  try {
    await api("/health", { timeoutMs: 8000 });
    await api("/api/settings/admin-access", { timeoutMs: 8000, headers: adminToken ? { "X-Admin-Token": adminToken } : {} });
  } catch (error) {
    throw new Error(connectionErrorMessage(error, base));
  }
  return { ok: true, message: `Connected to ${base}. Admin token accepted.` };
}

async function getQueueStatus() {
  const workerId = await getWorkerId();
  if (queueStatusInFlight) return queueStatusInFlight;
  queueStatusInFlight = (async () => {
  try {
    const payload = await api("/api/chrome/jobs?claim=false&job_limit=12");
    const snapshot = {
      ok: true,
      jobs: payload.jobs || [],
      counts: payload.counts || [],
      job_count: Number(payload.job_count || 0),
      workerId,
      cached_at: Date.now(),
      stale: false,
    };
    await chrome.storage.local.set({ cachedQueueStatus: snapshot });
    return snapshot;
  } catch (error) {
    const { cachedQueueStatus } = await getSettings();
    if (cachedQueueStatus?.ok) {
      return {
        ...cachedQueueStatus,
        workerId: cachedQueueStatus.workerId || workerId,
        stale: true,
        message: error.message || "Could not refresh queue; showing last loaded queue.",
      };
    }
    throw error;
  }
  })();
  try {
    return await queueStatusInFlight;
  } finally {
    queueStatusInFlight = null;
  }
}

async function getQueueJobsForActiveRefresh() {
  const payload = await api("/api/chrome/jobs?claim=false&job_limit=80");
  return payload.jobs || [];
}

async function refreshActiveJobFromQueue(windowId, force = false) {
  const { activeJob } = await getWindowState(windowId);
  if (!activeJob?.job?.group_key) return activeJob;
  if (activeJob.stage === "cleanup_after_failure" || activeJob.cleanupAfterFailure) return activeJob;
  if (orderSubmitStarted(activeJob) && activeJob.stage !== "reporting_complete") {
    try {
      await heartbeatJob(activeJob, windowId);
      return (await getWindowState(windowId)).activeJob || activeJob;
    } catch (error) {
      const message = String(error.message || "");
      if (/lock is no longer owned|no longer active/i.test(message)) {
        await clearStoredJobGroup(activeJob.job.group_key);
        await log(
          `Cleared stale submitted active job ${activeJob.job.group_key}; the server lock is no longer active: ${message}`,
          windowId,
        );
        return null;
      }
      return activeJob;
    }
  }
  if (!force && Date.now() - Number(activeJob.jobRefreshedAt || 0) < 30000) return activeJob;
  try {
    const jobs = await getQueueJobsForActiveRefresh();
    const freshJob = jobs.find((job) => job.group_key === activeJob.job.group_key);
    if (!freshJob) {
      try {
        await heartbeatJob(activeJob, windowId);
        return (await getWindowState(windowId)).activeJob || activeJob;
      } catch (error) {
        const message = String(error.message || "");
        if (!/lock is no longer owned|no longer active/i.test(message)) {
          await log(`Kept ${activeJob.job.group_key} active after heartbeat refresh failed: ${message}`, windowId);
          return activeJob;
        }
        await setWindowJob(windowId, null);
        await log(
          `Cleared stale active job ${activeJob.job.group_key}; the server lock is no longer active: ${message}`,
          windowId,
        );
        return null;
      }
    }
    const { activeJob: currentJob } = await getWindowState(windowId);
    const next = { ...(currentJob || activeJob), job: freshJob, jobRefreshedAt: Date.now() };
    if (jobWasSubmittedToAmazon(freshJob)) {
      next.stage = next.stage === "reporting_complete" ? "reporting_complete" : "find_order_id";
      next.amazonSubmittedAt = next.amazonSubmittedAt || Date.now();
      if (!next.paused) next.pausedStage = null;
    }
    await setWindowJob(windowId, next);
    return next;
  } catch {
    return activeJob;
  }
}

async function openControlWindow(tab) {
  const targetWindowId = tab?.windowId || null;
  const popupUrl = chrome.runtime.getURL(`popup.html${targetWindowId ? `?targetWindowId=${targetWindowId}` : ""}`);
  const windows = await chrome.windows.getAll({ populate: true, windowTypes: ["popup", "normal"] });
  const existingTab = windows.flatMap((item) => item.tabs || []).find((tab) => tab.url === popupUrl);
  if (existingTab?.id && existingTab.windowId) {
    await setControlWindow(existingTab.windowId, targetWindowId);
    await chrome.windows.update(existingTab.windowId, { focused: true });
    await chrome.tabs.update(existingTab.id, { active: true });
    return;
  }
  const controlWindow = await chrome.windows.create({
    url: popupUrl,
    type: "popup",
    width: 430,
    height: 620,
    focused: true,
  });
  await setControlWindow(controlWindow?.id, targetWindowId);
}

function activeJobFor(job, workerId, targetWindowId) {
  return {
    job,
    itemIndex: 0,
    stage: "clear_cart",
    cartCleared: false,
    paused: false,
    promoAcknowledged: {},
    pricing: {},
    workerId,
    startedAt: Date.now(),
    targetWindowId,
  };
}

function jobWasSubmittedToAmazon(job) {
  if (job?.submitted_to_amazon) return true;
  if (["order_submitted", "reporting_complete"].includes(String(job?.amazon_status || ""))) return true;
  return (job?.items || []).some((item) => ["order_submitted", "reporting_complete"].includes(String(item.amazon_status || "")));
}

function activeJobBlocksNext(activeJob) {
  if (!activeJob?.job?.group_key) return false;
  if (activeJob.stage === "cleanup_after_failure") return false;
  if (
    activeJob.stage === "reporting_complete" &&
    activeJob.reportedOrderId &&
    /lock is no longer owned|no longer active/i.test(String(activeJob.reportError || ""))
  ) {
    return false;
  }
  return true;
}

function isLateCompletedJobUpdate(currentJob, incomingJob) {
  if (!incomingJob?.job?.group_key) return false;
  if (incomingJob.stage !== "reporting_complete") return false;
  if (currentJob?.job?.group_key === incomingJob.job.group_key) return false;
  return Boolean(incomingJob.reportedOrderId || incomingJob.reportAttemptedAt);
}

async function blockingActiveJob(windowId = null) {
  const state = await getSettings();
  const jobs = Object.values(state.activeJobsByWindow || {}).filter(Boolean);
  if (state.activeJob) jobs.push(state.activeJob);
  const seen = new Set();
  for (const job of jobs) {
    const key = `${job?.targetWindowId || ""}:${job?.job?.group_key || ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    if (activeJobBlocksNext(job)) return job;
    const refreshed = await refreshActiveJobFromQueue(job.targetWindowId || windowId || null, true);
    if (activeJobBlocksNext(refreshed)) return refreshed;
  }
  if (windowId) {
    const activeJob = await refreshActiveJobFromQueue(windowId, true);
    if (activeJobBlocksNext(activeJob)) return activeJob;
  }
  return null;
}

async function navigateWindowToCart(windowId) {
  if (!windowId) return;
  const tabs = await chrome.tabs.query({ windowId });
  const tab = tabs.find((item) => item.active) || tabs[0];
  if (tab?.id) {
    await chrome.tabs.update(tab.id, { url: "https://www.amazon.com/cart?ref_=sw_gtc", active: true });
  }
  await chrome.windows.update(windowId, { focused: true });
}

async function navigateWindowToProduct(windowId, asin) {
  const normalizedAsin = String(asin || "").trim().toUpperCase();
  if (!windowId || !normalizedAsin) return;
  const url = `https://www.amazon.com/dp/${encodeURIComponent(normalizedAsin)}`;
  const tabs = await chrome.tabs.query({ windowId });
  const tab = tabs.find((item) => item.active) || tabs[0];
  if (tab?.id) {
    await chrome.tabs.update(tab.id, { url, active: true });
  } else {
    await chrome.tabs.create({ windowId, url, active: true });
  }
  await chrome.windows.update(windowId, { focused: true });
}

async function navigateWindowToOrderHistory(windowId) {
  const url = "https://www.amazon.com/gp/your-account/order-history?ref=ppx_pt2_dt_b_yo_link";
  if (!windowId) {
    const created = await chrome.windows.create({ url, type: "normal", focused: true });
    return created?.id || null;
  }
  const tabs = await chrome.tabs.query({ windowId });
  const tab = tabs.find((item) => item.active) || tabs[0];
  if (tab?.id) {
    await chrome.tabs.update(tab.id, { url, active: true });
  } else {
    await chrome.tabs.create({ windowId, url, active: true });
  }
  await chrome.windows.update(windowId, { focused: true });
  return windowId;
}

async function windowIsIncognito(windowId) {
  if (!windowId) return false;
  try {
    const windowInfo = await chrome.windows.get(windowId);
    return Boolean(windowInfo?.incognito);
  } catch {
    return false;
  }
}

async function createAmazonWorkerWindow(incognito = false) {
  const createData = {
    url: "https://www.amazon.com/cart?ref_=sw_gtc",
    type: "normal",
    focused: true,
    ...(incognito ? { incognito: true } : {}),
  };
  try {
    return await chrome.windows.create(createData);
  } catch (error) {
    await log(`Could not open new ${incognito ? "incognito " : ""}Chrome window: ${error.message}`);
    if (incognito) throw error;
  }
  const createdTab = await chrome.tabs.create({ url: "https://www.amazon.com/cart?ref_=sw_gtc", active: true });
  return createdTab.windowId ? await chrome.windows.get(createdTab.windowId) : null;
}

async function contentScriptLoaded(tabId) {
  if (!chrome.scripting?.executeScript || !tabId) return true;
  try {
    const result = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => Boolean(window.__nutricityContentLoaded),
    });
    return Boolean(result?.[0]?.result);
  } catch {
    return true;
  }
}

async function injectContentScript(tabId) {
  if (!chrome.scripting?.executeScript || !tabId) return false;
  if (await contentScriptLoaded(tabId)) return false;
  try {
    if (chrome.scripting.insertCSS) {
      await chrome.scripting.insertCSS({ target: { tabId }, files: ["content.css"] }).catch(() => undefined);
    }
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
    return true;
  } catch (error) {
    await log(`Could not inject Nutricity content script into Amazon tab ${tabId}: ${error.message}`);
    return false;
  }
}

async function ensureContentScriptsInAmazonTabs(label = "extension recovery") {
  if (!chrome.tabs?.query) return;
  const tabs = await chrome.tabs.query({ url: ["https://www.amazon.com/*", "https://amazon.com/*"] });
  let injected = 0;
  for (const tab of tabs) {
    if (await injectContentScript(tab.id)) injected += 1;
  }
  if (injected) await log(`Recovered Nutricity content script in ${injected} Amazon tab(s) after ${label}.`);
}

async function startNextJob(sourceWindowId = null) {
  if (startNextJobInFlight) return startNextJobInFlight;
  startNextJobInFlight = (async () => {
  await setForceStop(false);
  await releaseMissingWindowJobs();
  try {
    await testConnection();
  } catch (error) {
    const message = error.message || "Could not reach the local fulfilment app.";
    await log(`Cannot start queued order: ${message}`, sourceWindowId);
    await setOrderProgress({
      running: false,
      total: 0,
      processed: 0,
      message,
      startedAt: Date.now(),
      updatedAt: Date.now(),
    });
    return { ok: false, message };
  }
  const browserless = await browserlessOrderStatus().catch(() => null);
  if (browserless?.progress?.running === true) {
    await stopBrowserlessOrderRun(sourceWindowId);
    const stopped = await waitForBrowserlessOrderStop(sourceWindowId);
    if (!stopped) {
      const message = "Browserless ordering is still finishing the current job. Visible Chrome ordering will not start until it reports.";
      await log(message, sourceWindowId);
      return { ok: false, browserless_running: true, message };
    }
    await log("Browserless ordering stopped; starting the next queued order in Chrome UI mode.", sourceWindowId);
  }
  const submittedRecovery = await recoverSubmittedJobInWindow(sourceWindowId);
  if (submittedRecovery?.activeJob) return submittedRecovery;
  const blocking = await blockingActiveJob(sourceWindowId);
  if (blocking) {
    if (orderSubmitStarted(blocking)) {
      await log(`Cannot start a new job; ${blocking.job.group_key} was already submitted and must be reported first.`, sourceWindowId);
      const recovered = await recoverSubmittedJobInWindow(blocking.targetWindowId || sourceWindowId);
      if (recovered?.activeJob) return recovered;
      return {
        ok: false,
        active_job_running: true,
        submitted_pending: true,
        message: `${blocking.job.group_key} was already submitted to Amazon and must be reported before starting another order.`,
      };
    } else {
      await log(`Cannot start a new job; ${blocking.job.group_key} is still active.`, sourceWindowId);
      return { ok: false, active_job_running: true, message: `Finish or stop ${blocking.job.group_key} before starting the next queued order.` };
    }
  }
  const workerId = await getWorkerId();
  const { splitMixedAsinOrders, fulfilAvailableMixedAsin } = await getSettings();
  try {
    const queueBefore = await api("/api/chrome/jobs?claim=false&job_limit=1");
    await startOrderProgress(Number(queueBefore.job_count || queueBefore.jobs?.length || 0), "Visible Chrome order run started.");
  } catch {
    await startOrderProgress(0, "Visible Chrome order run started.");
  }
  const preflight = await preflightSplitQueueHead(workerId, splitMixedAsinOrders, fulfilAvailableMixedAsin, sourceWindowId);
  if (!preflight.ok) {
    await updateOrderProgressTotal(0, preflight.message || "Split-order preflight failed.");
    return { ok: false, message: preflight.message || "Split-order preflight failed." };
  }
  if (await forceStopActive()) return { ok: false, stopped: true, message: "Force stop is active; did not claim a queued order." };
  const payload = await api(`/api/chrome/jobs?worker_id=${encodeURIComponent(workerId)}&claim=true&resume_existing=true&split_mixed_asin=${splitMixedAsinOrders === true ? "true" : "false"}`);
  const job = payload.jobs?.[0];
  if (!job) {
    await updateOrderProgressTotal(0, "No queued Chrome jobs found.");
    await log("No queued Chrome jobs found.");
    return { ok: false, message: "No queued Chrome jobs found." };
  }
  if (jobWasSubmittedToAmazon(job)) {
    await log(`Recovered submitted ${job.group_key}; looking up Amazon order ID instead of opening a new cart.`, sourceWindowId);
    return recoverSubmittedJobInWindow(sourceWindowId);
  }
  const incognito = await windowIsIncognito(sourceWindowId);
  let createdWindow;
  try {
    createdWindow = await createAmazonWorkerWindow(incognito);
  } catch (error) {
    try {
      await api(`/api/chrome/jobs/${encodeURIComponent(job.group_key)}/release`, {
        method: "POST",
        body: JSON.stringify({ worker_id: workerId }),
      });
    } catch (releaseError) {
      await log(`Could not release ${job.group_key} after window open failed: ${releaseError.message}`);
    }
    throw new Error(
      incognito
        ? "Could not open an incognito Amazon window. Check that this extension is allowed in incognito mode."
        : `Could not open Amazon cart window: ${error.message}`,
    );
  }
  const targetWindowId = createdWindow?.id || null;
  if (!targetWindowId) {
    throw new Error("Could not open Amazon cart window for the queued job.");
  }
  const activeJob = activeJobFor(job, workerId, targetWindowId);
  activeJob.incognito = incognito;
  await setWindowJob(targetWindowId, activeJob);
  await log(`Started ${job.group_key} with ${job.items.length} item(s) in ${incognito ? "incognito" : "normal"} window.`, targetWindowId);
  return { ok: true, message: `Started ${job.group_key}.`, targetWindowId };
  })();
  try {
    return await startNextJobInFlight;
  } finally {
    startNextJobInFlight = null;
  }
}

async function startBrowserlessOrderRun(sourceWindowId = null) {
  await setForceStop(false);
  await releaseMissingWindowJobs();
  const blocking = await blockingActiveJob(sourceWindowId);
  if (blocking) {
    const message = `Finish or report ${blocking.job.group_key} before starting browserless ordering.`;
    await log(message, sourceWindowId);
    return { ok: false, active_job_running: true, message };
  }
  const workerId = await getWorkerId();
  const browserlessWorkerId = workerId.startsWith("browserless-") ? workerId : `browserless-${workerId}`;
  const { splitMixedAsinOrders } = await getSettings();
  const result = await api("/api/chrome/browserless/run", {
    method: "POST",
    body: JSON.stringify({
      worker_id: browserlessWorkerId,
      ordering_engine: "chrome_browserless",
      split_mixed_asin: splitMixedAsinOrders === true,
    }),
    timeoutMs: 15000,
  });
  if (result?.progress) {
    await setOrderProgress({
      running: result.progress.running === true,
      total: Number(result.progress.total || 0),
      processed: Number(result.progress.processed || 0),
      message: result.progress.message || result.message || "Browserless ordering started.",
      startedAt: Date.now(),
      updatedAt: Date.now(),
      source: "browserless",
    });
  }
  await log(result.message || "Started browserless background ordering.", sourceWindowId);
  return result;
}

async function browserlessOrderStatus() {
  const result = await api("/api/chrome/browserless/status", { timeoutMs: 10000 });
  const progress = result?.progress || {};
  if (Object.keys(progress).length) {
    await setOrderProgress({
      running: progress.running === true,
      total: Number(progress.total || 0),
      processed: Number(progress.processed || 0),
      message: progress.message || "",
      startedAt: progress.started_at || Date.now(),
      updatedAt: progress.updated_at || Date.now(),
      source: "browserless",
    });
  }
  return result;
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForBrowserlessOrderStop(sourceWindowId = null, timeoutMs = BROWSERLESS_SWITCH_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const result = await browserlessOrderStatus().catch((error) => ({ error }));
    const progress = result?.progress || {};
    if (progress.running !== true) return true;
    await setOrderProgress({
      running: true,
      total: Number(progress.total || 0),
      processed: Number(progress.processed || 0),
      message: progress.stop_requested
        ? "Stopping browserless ordering after the current job is reported. Chrome UI mode will start next."
        : "Waiting for browserless ordering to stop before opening Chrome UI mode.",
      startedAt: progress.started_at || Date.now(),
      updatedAt: progress.updated_at || Date.now(),
      source: "browserless",
    });
    await delay(BROWSERLESS_SWITCH_POLL_MS);
  }
  await log("Timed out waiting for browserless ordering to stop.", sourceWindowId);
  return false;
}

async function stopBrowserlessOrderRun(sourceWindowId = null) {
  const result = await api("/api/chrome/browserless/stop", {
    method: "POST",
    timeoutMs: 10000,
  });
  const progress = result?.progress || {};
  await setOrderProgress({
    running: progress.running === true,
    total: Number(progress.total || 0),
    processed: Number(progress.processed || 0),
    message: progress.message || result.message || "Browserless ordering stopped.",
    startedAt: progress.started_at || Date.now(),
    updatedAt: progress.updated_at || Date.now(),
    source: "browserless",
  });
  await log(result.message || "Browserless ordering stop requested.", sourceWindowId);
  return result;
}

async function claimNextJobInWindow(windowId) {
  const claimKey = String(windowId || "global");
  if (claimNextJobInFlight.has(claimKey)) return claimNextJobInFlight.get(claimKey);
  const task = (async () => {
  if (await forceStopActive()) {
    await log("Force stop is active; did not claim another queued order.", windowId);
    return null;
  }
  const browserless = await browserlessOrderStatus().catch(() => null);
  if (browserless?.progress?.running === true) {
    await log(browserless.progress.message || "Browserless ordering is running; did not claim another visible Chrome job.", windowId);
    return null;
  }
  const submittedRecovery = await recoverSubmittedJobInWindow(windowId);
  if (submittedRecovery?.activeJob) return submittedRecovery.activeJob;
  const { activeJob: currentJob } = await getWindowState(windowId);
  if (currentJob?.stage === "cleanup_after_failure" || currentJob?.cleanupAfterFailure) {
    await setWindowJob(windowId, null);
  }
  if (activeJobBlocksNext(currentJob)) {
    await log(`Kept ${currentJob.job.group_key}; current job is not closed yet.`, windowId);
    return currentJob;
  }
  const blocking = await blockingActiveJob(windowId);
  if (blocking) {
    if (orderSubmitStarted(blocking)) {
      const recovered = await recoverSubmittedJobInWindow(blocking.targetWindowId || windowId);
      if (recovered?.activeJob) return recovered.activeJob;
      await log(`Did not claim a new job because submitted ${blocking.job.group_key} still needs order-history reporting.`, windowId);
      return null;
    } else {
      await log(`Did not claim a new job because ${blocking.job.group_key} is still active.`, windowId);
      return null;
    }
  }
  const workerId = await getWorkerId();
  const { splitMixedAsinOrders, fulfilAvailableMixedAsin } = await getSettings();
  const preflight = await preflightSplitQueueHead(workerId, splitMixedAsinOrders, fulfilAvailableMixedAsin, windowId);
  if (!preflight.ok) {
    await log(preflight.message || "Split-order preflight failed.", windowId);
    return null;
  }
  if (await forceStopActive()) return null;
  const payload = await api(`/api/chrome/jobs?worker_id=${encodeURIComponent(workerId)}&claim=true&resume_existing=true&split_mixed_asin=${splitMixedAsinOrders === true ? "true" : "false"}`);
  const job = payload.jobs?.[0];
  if (!job) {
    await setWindowJob(windowId, null);
    await log("No more queued Chrome jobs found.", windowId);
    return null;
  }
  if (jobWasSubmittedToAmazon(job)) {
    const recovered = await recoverSubmittedJobInWindow(windowId);
    return recovered.activeJob || null;
  }
  const activeJob = activeJobFor(job, workerId, windowId);
  activeJob.startedAfterPreviousJob = true;
  await setWindowJob(windowId, activeJob);
  await log(`Started next ${job.group_key} with ${job.items.length} item(s); clearing Amazon cart before product add.`, windowId);
  await navigateWindowToCart(windowId);
  return activeJob;
  })();
  claimNextJobInFlight.set(claimKey, task);
  try {
    return await task;
  } finally {
    claimNextJobInFlight.delete(claimKey);
  }
}

async function finishCleanupAndClaimNext(windowId) {
  const { activeJob } = await getWindowState(windowId);
  if (activeJob?.stage === "cleanup_after_failure" || activeJob?.cleanupAfterFailure) {
    await setWindowJob(windowId, null);
  }
  const nextJob = await claimNextJobInWindow(windowId);
  return {
    ok: true,
    next_job_started: Boolean(nextJob),
    next_group_key: nextJob?.job?.group_key || "",
    message: nextJob ? `Started next ${nextJob.job.group_key}.` : "No more queued Chrome jobs found.",
  };
}

async function recoverSubmittedJobInWindow(windowId) {
  const recoverKey = String(windowId || "global");
  if (recoverSubmittedJobsInFlight.has(recoverKey)) {
    return recoverSubmittedJobsInFlight.get(recoverKey);
  }
  if (Date.now() - lastEmptyRecoverSubmittedJobAt < 5000) {
    const { activeJob } = await getWindowState(windowId);
    if (!orderSubmitStarted(activeJob)) {
      return { ok: true, recovered: false, activeJob: null };
    }
  }
  const task = (async () => {
  const workerId = await getWorkerId();
  const result = await api(`/api/chrome/jobs/recover-submitted?worker_id=${encodeURIComponent(workerId)}`);
  const job = result.job || null;
  if (!job?.group_key) {
    lastEmptyRecoverSubmittedJobAt = Date.now();
    const { activeJob } = await getWindowState(windowId);
    if (orderSubmitStarted(activeJob)) {
      try {
        await heartbeatJob(activeJob, windowId);
        await log(`Kept submitted ${activeJob.job.group_key} active; server recovery did not return a submitted job yet.`, windowId);
      } catch (error) {
        const message = String(error.message || "");
        if (/lock is no longer owned|no longer active/i.test(message)) {
          await clearStoredJobGroup(activeJob.job.group_key);
          await log(`Cleared stale submitted ${activeJob.job.group_key}; server recovery and heartbeat both say it is no longer active: ${message}`, windowId);
          return { ok: true, recovered: false, activeJob: null };
        }
        await log(`Kept submitted ${activeJob.job.group_key} active; could not confirm server lock state: ${message}`, windowId);
      }
      return { ok: true, recovered: false, activeJob };
    }
    return { ok: true, recovered: false, activeJob: null };
  }
  const targetWindowId = await navigateWindowToOrderHistory(windowId);
  const activeJob = {
    ...activeJobFor(job, workerId, targetWindowId),
    stage: "find_order_id",
    cartCleared: true,
    amazonSubmittedAt: Date.now(),
    lastHeartbeatAt: Date.now(),
  };
  await setWindowJob(targetWindowId, activeJob);
  await log(`Recovered submitted ${job.group_key}; opened order history to look up Amazon order ID.`, targetWindowId);
  return { ok: true, recovered: true, activeJob, targetWindowId };
  })();
  recoverSubmittedJobsInFlight.set(recoverKey, task);
  try {
    return await task;
  } finally {
    recoverSubmittedJobsInFlight.delete(recoverKey);
  }
}

async function cleanupCartBeforeNextJob(activeJob, windowId, reason = "") {
  const cleanupJob = {
    ...activeJob,
    stage: "cleanup_after_failure",
    cleanupAfterFailure: true,
    cleanupReason: reason || "Cleaning Amazon cart before starting the next order.",
    paused: false,
  };
  await setWindowJob(windowId, cleanupJob);
  await log(`Cleaning cart after ${activeJob.job.group_key} before starting the next order.`, windowId);
  await navigateWindowToCart(windowId);
}

async function stopJob(windowId) {
  const state = await getWindowState(windowId);
  const globalState = await getSettings();
  let { activeJob } = state;
  let targetWindowId = windowId;
  if (!activeJob?.job && globalState.activeJob?.job) {
    activeJob = globalState.activeJob;
    targetWindowId = activeJob.targetWindowId || windowId || null;
  }
  if (!activeJob?.job) {
    const browserless = await browserlessOrderStatus().catch(() => null);
    if (browserless?.progress?.running === true) {
      return stopBrowserlessOrderRun(targetWindowId);
    }
  }
  if (activeJob?.job?.group_key && activeJob?.workerId) {
    if (orderSubmitStarted(activeJob)) {
      await log(`Stopped local automation for ${activeJob.job.group_key}, but kept server submit lock because Amazon submit already started.`, windowId);
    } else {
      try {
        await api(`/api/chrome/jobs/${encodeURIComponent(activeJob.job.group_key)}/release`, {
          method: "POST",
          body: JSON.stringify({ worker_id: activeJob.workerId }),
        });
      } catch (error) {
        await log(`Could not release ${activeJob.job.group_key}: ${error.message}`, targetWindowId);
      }
    }
  }
  if (activeJob?.job) {
    await recordLastProcessed(activeJob, "stopped", "Stopped manually from the popup.");
  }
  await setWindowJob(targetWindowId, null);
  await releaseMissingWindowJobs();
  await log("Stopped active job.", targetWindowId);
  return { ok: true, message: "Stopped active job." };
}

async function forceStopAll(windowId = null) {
  const state = await getSettings();
  await setForceStop(true, "Force stopped from the popup.");
  const activeEntries = [];
  const seen = new Set();
  for (const [key, activeJob] of Object.entries(state.activeJobsByWindow || {})) {
    if (!activeJob?.job?.group_key) continue;
    const id = `${activeJob.job.group_key}:${activeJob.workerId || ""}`;
    if (seen.has(id)) continue;
    seen.add(id);
    activeEntries.push({ windowId: Number(key) || null, activeJob });
  }
  if (state.activeJob?.job?.group_key) {
    const id = `${state.activeJob.job.group_key}:${state.activeJob.workerId || ""}`;
    if (!seen.has(id)) activeEntries.push({ windowId: state.activeJob.targetWindowId || windowId || null, activeJob: state.activeJob });
  }

  for (const { activeJob } of activeEntries) {
    await recordLastProcessed(activeJob, "force stopped", "Force stopped from the popup before the extension could continue.");
    try {
      await api(`/api/chrome/jobs/${encodeURIComponent(activeJob.job.group_key)}/force-release`, {
        method: "POST",
        timeoutMs: 8000,
      });
    } catch (error) {
      await log(`Force stop could not release ${activeJob.job.group_key}: ${error.message}`, windowId);
    }
  }

  try {
    const browserless = await browserlessOrderStatus().catch(() => null);
    if (browserless?.progress?.running === true) await stopBrowserlessOrderRun(windowId);
  } catch (error) {
    await log(`Force stop could not stop browserless ordering: ${error.message}`, windowId);
  }

  await chrome.storage.local.set({
    activeJob: null,
    activeJobsByWindow: {},
    orderProgress: {
      running: false,
      total: Number(state.orderProgress?.total || 0),
      processed: Number(state.orderProgress?.processed || 0),
      message: "Force stopped. Order queue was not cleared; no further extension progress will be made until Start is clicked.",
      startedAt: state.orderProgress?.startedAt || Date.now(),
      updatedAt: Date.now(),
    },
  });

  for (const { windowId: activeWindowId } of activeEntries) {
    if (!activeWindowId) continue;
    try {
      await chrome.windows.remove(activeWindowId);
    } catch {
      // The window may already be gone; force stop still cleared extension state.
    }
  }

  await log("Force stopped all fulfilment activity.", windowId);
  return { ok: true, force_stopped: true, stopped_jobs: activeEntries.length, message: `Force stopped ${activeEntries.length} active job(s). Order queue was not cleared. Click Start next queued order to allow processing again.` };
}

async function skipJob(windowId) {
  const { activeJob } = await getWindowState(windowId);
  if (!activeJob?.job) return { ok: false, message: "No active job to skip." };
  const groupKey = activeJob.job.group_key;
  const released = await releaseStoredJob(activeJob, windowId, "after manual skip");
  if (!released) return { ok: false, message: `Could not release ${groupKey} to skip it.` };
  await recordLastProcessed(activeJob, "skipped", "Skipped manually from the popup.");
  await incrementOrderProgress(`Processed ${groupKey}: skipped.`);
  const nextJob = await claimNextJobInWindow(windowId);
  return {
    ok: true,
    message: nextJob ? `Skipped ${groupKey}. Started ${nextJob.job.group_key}.` : `Skipped ${groupKey}. No more queued Chrome jobs found.`,
    next_job_started: Boolean(nextJob),
    next_group_key: nextJob?.job?.group_key || "",
  };
}

async function markCurrentJobMissing(windowId) {
  const { activeJob } = await getWindowState(windowId);
  if (!activeJob?.job) return { ok: false, message: "No active job to mark missing." };
  const groupKey = activeJob.job.group_key;
  const result = await failJob("Marked missing from Chrome progress popup.", { failureCode: "manual_missing" }, windowId);
  return {
    ...result,
    message: result.next_job_started
      ? `Marked ${groupKey} as Missing ASINs. Started ${result.next_group_key}.`
      : `Marked ${groupKey} as Missing ASINs. No more queued Chrome jobs found.`,
  };
}

async function checkExistingAmazonOrder(windowId) {
  const { activeJob } = await getWindowState(windowId);
  if (!activeJob?.job?.group_key) return { ok: true, duplicate: false, orders: [] };
  try {
    await heartbeatJob(activeJob, windowId);
  } catch (error) {
    await log(`Continuing duplicate check after heartbeat failed for ${activeJob.job.group_key}: ${error.message}`, windowId);
  }
  let result;
  let lastError = null;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      result = await api(`/api/chrome/jobs/${encodeURIComponent(activeJob.job.group_key)}/duplicate-check`, {
        method: "POST",
        timeoutMs: attempt === 1 ? 45000 : 90000,
        body: JSON.stringify({
          worker_id: activeJob.workerId || "",
          line_ids: activeJob.job.line_ids || [],
        }),
      });
      break;
    } catch (error) {
      lastError = error;
      const message = String(error?.message || error || "");
      const transientAbort = /abort|signal|timeout|network/i.test(message);
      if (!transientAbort || attempt === 3) break;
      await log(`Duplicate check retry ${attempt + 1} for ${activeJob.job.group_key} after transient error: ${message}`, windowId);
      await new Promise((resolve) => setTimeout(resolve, 1200 * attempt));
    }
  }
  if (!result) {
    throw lastError || new Error("Duplicate check failed.");
  }
  activeJob.duplicateOrder = result.duplicate ? result : null;
  if (result.duplicate) {
    activeJob.paused = true;
    activeJob.pausedStage = activeJob.stage || "checkout";
    activeJob.stage = "duplicate_order";
    await log(result.message || `Amazon order already exists for ${activeJob.job.group_key}.`, windowId);
  }
  await setWindowJob(windowId, activeJob);
  return result;
}

async function resetDuplicateFulfilment(windowId) {
  const { activeJob } = await getWindowState(windowId);
  if (!activeJob?.job?.group_key) return { ok: false, message: "No active job to reset." };
  const result = await api(`/api/chrome/jobs/${encodeURIComponent(activeJob.job.group_key)}/reset-fulfilment`, {
    method: "POST",
    body: JSON.stringify({
      worker_id: activeJob.workerId || "",
      line_ids: activeJob.job.line_ids || [],
    }),
  });
  activeJob.duplicateOrder = null;
  activeJob.paused = false;
  activeJob.pausedStage = null;
  activeJob.stage = "checkout";
  await setWindowJob(windowId, activeJob);
  await log(result.message || `Cleared existing Amazon order for ${activeJob.job.group_key}.`, windowId);
  return result;
}

async function releaseStoredJob(activeJob, windowId = null, label = "Chrome job") {
  if (!activeJob?.job?.group_key || !activeJob?.workerId) return false;
  if (orderSubmitStarted(activeJob)) {
    try {
      await heartbeatJob(activeJob, windowId);
      await log(`Kept ${activeJob.job.group_key} locked ${label}; Amazon submit already started.`, windowId);
    } catch (error) {
      const message = String(error.message || "");
      if (/lock is no longer owned|no longer active/i.test(message)) {
        await log(`Released stale submitted ${activeJob.job.group_key} ${label}; server lock is no longer active: ${message}`, windowId);
        return true;
      }
      await log(`Kept ${activeJob.job.group_key} locked ${label}; could not confirm submitted lock state: ${message}`, windowId);
    }
    return false;
  }
  try {
    await api(`/api/chrome/jobs/${encodeURIComponent(activeJob.job.group_key)}/release`, {
      method: "POST",
      body: JSON.stringify({ worker_id: activeJob.workerId }),
    });
    await log(`Released ${activeJob.job.group_key} ${label}.`, windowId);
    return true;
  } catch (error) {
    await log(`Could not release ${activeJob.job.group_key}: ${error.message}`, windowId);
    return false;
  }
}

function orderSubmitStarted(activeJob) {
  const stage = String(activeJob?.stage || "");
  const pausedStage = String(activeJob?.pausedStage || "");
  return Boolean(
    jobWasSubmittedToAmazon(activeJob?.job) ||
      activeJob?.amazonSubmittedAt ||
      activeJob?.amazonDuplicateOrderConfirmed ||
      ["complete_pending", "find_order_id", "reporting_complete"].includes(stage) ||
      ["complete_pending", "find_order_id", "reporting_complete"].includes(pausedStage),
  );
}

async function markAmazonSubmitted(windowId) {
  const { activeJob } = await getWindowState(windowId);
  if (!activeJob?.job?.group_key || !activeJob?.workerId) {
    return { ok: false, message: "No active job to protect before Amazon submit." };
  }
  const result = await api(`/api/chrome/jobs/${encodeURIComponent(activeJob.job.group_key)}/submitted`, {
    method: "POST",
    body: JSON.stringify({ worker_id: activeJob.workerId }),
  });
  const latest = (await getWindowState(windowId)).activeJob || activeJob;
  latest.amazonSubmittedAt = latest.amazonSubmittedAt || Date.now();
  latest.stage = "complete_pending";
  latest.paused = false;
  latest.pausedStage = null;
  latest.lastHeartbeatAt = Date.now();
  await setWindowJob(windowId, latest);
  await log(`Protected ${latest.job.group_key} after Amazon submit started.`, windowId);
  return result;
}

async function releaseMissingWindowJobs() {
  if (releaseMissingWindowJobsInFlight) return releaseMissingWindowJobsInFlight;
  if (Date.now() - lastReleaseMissingWindowJobsAt < 15000) return;
  releaseMissingWindowJobsInFlight = (async () => {
    lastReleaseMissingWindowJobsAt = Date.now();
    const state = await getSettings();
    const activeJobsByWindow = { ...(state.activeJobsByWindow || {}) };
    const windows = await chrome.windows.getAll({ windowTypes: ["normal", "popup"] });
    const openWindowIds = new Set(windows.map((item) => String(item.id)));
    let changed = false;
    for (const [windowId, activeJob] of Object.entries(activeJobsByWindow)) {
      if (openWindowIds.has(windowId)) continue;
      const released = await releaseStoredJob(activeJob, Number(windowId) || null, "because its Chrome window is closed");
      if (released) {
        delete activeJobsByWindow[windowId];
        changed = true;
      }
    }
    if (changed) {
      await chrome.storage.local.set({ activeJobsByWindow, activeJob: Object.values(activeJobsByWindow)[0] || null });
    }
  })();
  try {
    await releaseMissingWindowJobsInFlight;
  } finally {
    releaseMissingWindowJobsInFlight = null;
  }
}

async function releaseAllStoredJobs(label = "from the previous Chrome session") {
  const state = await getSettings();
  const activeJobsByWindow = { ...(state.activeJobsByWindow || {}) };
  const seen = new Set();
  const releasedKeys = new Set();
  let changed = false;
  for (const [windowId, activeJob] of Object.entries(activeJobsByWindow)) {
    const groupKey = activeJob?.job?.group_key || "";
    const workerId = activeJob?.workerId || "";
    if (!groupKey || !workerId) continue;
    const key = `${groupKey}:${workerId}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const released = await releaseStoredJob(activeJob, Number(windowId) || null, label);
    if (released) {
      releasedKeys.add(key);
      delete activeJobsByWindow[windowId];
      changed = true;
    }
  }
  let activeJob = state.activeJob || null;
  if (activeJob?.job?.group_key && activeJob?.workerId) {
    const key = `${state.activeJob.job.group_key}:${state.activeJob.workerId}`;
    if (releasedKeys.has(key)) {
      activeJob = null;
      changed = true;
    } else if (!seen.has(key)) {
      const released = await releaseStoredJob(state.activeJob, state.activeJob.targetWindowId || null, label);
      if (released) {
        activeJob = null;
        changed = true;
      }
    }
  }
  if (changed) {
    await chrome.storage.local.set({ activeJobsByWindow, activeJob: activeJob || Object.values(activeJobsByWindow)[0] || null, controlWindowsById: {} });
  }
}

async function heartbeatJob(activeJob, windowId) {
  if (!activeJob?.job?.group_key || !activeJob?.workerId) return;
  await api(`/api/chrome/jobs/${encodeURIComponent(activeJob.job.group_key)}/heartbeat`, {
    method: "POST",
    body: JSON.stringify({ worker_id: activeJob.workerId }),
  });
  activeJob.lastHeartbeatAt = Date.now();
  await setWindowJob(windowId, activeJob);
}

async function togglePause(windowId) {
  const { activeJob } = await getWindowState(windowId);
  if (!activeJob?.job) return { ok: false, message: "No active job." };
  const nextPaused = !activeJob.paused;
  if (nextPaused) {
    activeJob.pausedStage = activeJob.stage || activeJob.pausedStage || "product";
  } else if (activeJob.pausedStage) {
    activeJob.stage = activeJob.pausedStage;
    activeJob.pausedStage = null;
  }
  activeJob.paused = nextPaused;
  await setWindowJob(windowId, activeJob);
  await log(`${activeJob.paused ? "Paused" : "Resumed"} ${activeJob.job.group_key}.`, windowId);
  return { ok: true, paused: activeJob.paused, stage: activeJob.stage || "", message: activeJob.paused ? "Paused fulfilment." : `Resumed ${activeJob.stage || "fulfilment"}.` };
}

async function completeJob(orderId, orderUrl, amazonAccountName, windowId, orderMappings = [], orderDate = "") {
  if (await forceStopActive()) {
    return { ok: false, stopped: true, message: "Force stop is active; ignored late order completion report." };
  }
  const { activeJob } = await getWindowState(windowId);
  if (!activeJob?.job) return { ok: false, message: "No active job." };
  const groupKey = activeJob.job.group_key;
  const lockKey = `${windowId || "global"}:${groupKey}`;
  if (completionLocks.has(lockKey)) {
    await log(`Ignored duplicate completion report for ${groupKey}.`, windowId);
    return { ok: true, duplicate_ignored: true, message: `Completion for ${groupKey} is already being reported.` };
  }
  completionLocks.add(lockKey);
  try {
    const body = {
      amazon_order_id: orderId || "",
      amazon_order_url: orderUrl || "",
      amazon_account_name: amazonAccountName || "",
      order_date: orderDate || "",
      line_ids: activeJob.job.line_ids || [],
      order_mappings: orderMappings || [],
      pricing_summary: Object.values(activeJob.pricing || {}),
      worker_id: activeJob.workerId || "",
    };
    try {
      await heartbeatJob(activeJob, windowId);
    } catch (error) {
      if (!/lock is no longer owned|no longer active/i.test(String(error.message || "")) || !orderId) throw error;
      await log(`Chrome job lock for ${groupKey} was released before completion; reporting found Amazon order ${orderId} anyway.`, windowId);
    }
    let result;
    try {
      result = await api(`/api/chrome/jobs/${encodeURIComponent(groupKey)}/complete`, {
        method: "POST",
        body: JSON.stringify(body),
      });
    } catch (error) {
      if (/lock is no longer owned|no longer active/i.test(String(error.message || "")) && orderId) {
        await log(`Treating late completion for ${groupKey} as already reported after released lock.`, windowId);
        await recordLastProcessed(activeJob, "placed", `Placed ${orderId}.`, { amazon_order_id: orderId });
        await clearStoredJobGroup(groupKey);
        const nextJob = await claimNextJobInWindow(windowId);
        return {
          ok: true,
          already_reported: true,
          amazon_order_id: orderId,
          message: `Order ${orderId} was already reported; continuing queue.`,
          next_job_started: Boolean(nextJob),
          next_group_key: nextJob?.job?.group_key || "",
        };
      }
      throw error;
    }
    await log(`Completed ${groupKey} as ${result.amazon_order_id}.`, windowId);
    await recordLastProcessed(activeJob, "placed", result.amazon_order_id ? `Placed ${result.amazon_order_id}.` : "Placed on Amazon.", {
      amazon_order_id: result.amazon_order_id || orderId || "",
    });
    await incrementOrderProgress(`Processed ${groupKey}: ordered.`);
    const latest = await getWindowState(windowId);
    if (latest.activeJob?.job && latest.activeJob.job.group_key !== groupKey) {
      return { ...result, next_job_started: false, next_group_key: "" };
    }
    await clearStoredJobGroup(groupKey);
    const nextJob = await claimNextJobInWindow(windowId);
    return { ...result, next_job_started: Boolean(nextJob), next_group_key: nextJob?.job?.group_key || "" };
  } finally {
    completionLocks.delete(lockKey);
  }
}

async function failJob(message, details = {}, windowId) {
  if (await forceStopActive()) {
    return { ok: false, stopped: true, message: "Force stop is active; ignored late failure report." };
  }
  const { activeJob } = await getWindowState(windowId);
  if (!activeJob?.job) return { ok: false, message: "No active job." };
  if (orderSubmitStarted(activeJob) || jobWasSubmittedToAmazon(activeJob.job)) {
    await log(`Ignored failure report for submitted ${activeJob.job.group_key}; reopening order history instead.`, windowId);
    await recoverSubmittedJobInWindow(activeJob.targetWindowId || windowId);
    return {
      ok: false,
      submitted_pending: true,
      message: `${activeJob.job.group_key} was already submitted to Amazon; ignored late failure and reopened order history for reporting.`,
    };
  }
  try {
    await heartbeatJob(activeJob, windowId);
  } catch (error) {
    await log(`Continuing fail report after heartbeat failed for ${activeJob.job.group_key}: ${error.message}`, windowId);
  }
  const result = await api(`/api/chrome/jobs/${encodeURIComponent(activeJob.job.group_key)}/fail`, {
    method: "POST",
    body: JSON.stringify({
      message,
      line_ids: activeJob.job.line_ids || [],
      missing_asin: details.missingAsin || "",
      missing_line_id: details.missingLineId || null,
      failure_code: details.failureCode || "",
      requested_quantity: details.requestedQuantity ?? null,
      fulfilled_quantity: details.fulfilledQuantity ?? null,
      available_quantity: details.availableQuantity ?? null,
      worker_id: activeJob.workerId || "",
    }),
  });
  await log(`Failed ${activeJob.job.group_key}: ${message}`, windowId);
  const failureCode = String(details.failureCode || "").toLowerCase();
  const status = failureCode.includes("missing") || failureCode.includes("unavailable") || result?.state === "missing"
    ? "missing"
    : "failed";
  await recordLastProcessed(activeJob, status, message);
  await incrementOrderProgress(`Processed ${activeJob.job.group_key}: failed or missing.`);
  await cleanupCartBeforeNextJob(activeJob, windowId, message);
  return { ...result, next_job_started: false, cleanup_required: true, next_group_key: "" };
}

async function postSubmitUnplaced(message, details = {}, windowId) {
  if (await forceStopActive()) {
    return { ok: false, stopped: true, message: "Force stop is active; ignored post-submit unavailable report." };
  }
  const { activeJob } = await getWindowState(windowId);
  if (!activeJob?.job) return { ok: false, message: "No active job." };
  try {
    await heartbeatJob(activeJob, windowId);
  } catch (error) {
    await log(`Continuing post-submit unavailable report after heartbeat failed for ${activeJob.job.group_key}: ${error.message}`, windowId);
  }
  const result = await api(`/api/chrome/jobs/${encodeURIComponent(activeJob.job.group_key)}/post-submit-unplaced`, {
    method: "POST",
    body: JSON.stringify({
      message,
      line_ids: activeJob.job.line_ids || [],
      missing_asin: details.missingAsin || "",
      missing_line_id: details.missingLineId || null,
      failure_code: details.failureCode || "post_submit_unplaced",
      worker_id: activeJob.workerId || "",
    }),
  });
  await log(`Post-submit unavailable for ${activeJob.job.group_key}: ${message}`, windowId);
  await recordLastProcessed(activeJob, "missing", result?.message || message);
  await incrementOrderProgress(`Processed ${activeJob.job.group_key}: post-submit unavailable.`);
  await cleanupCartBeforeNextJob(activeJob, windowId, result?.message || message);
  return { ...result, next_job_started: false, cleanup_required: true, next_group_key: "" };
}

async function submitUncertain(message, details = {}, windowId) {
  if (await forceStopActive()) {
    return { ok: false, stopped: true, message: "Force stop is active; ignored submitted-order uncertainty report." };
  }
  const { activeJob } = await getWindowState(windowId);
  if (!activeJob?.job) return { ok: false, message: "No active job." };
  try {
    await heartbeatJob(activeJob, windowId);
  } catch (error) {
    await log(`Continuing submitted-order uncertainty report after heartbeat failed for ${activeJob.job.group_key}: ${error.message}`, windowId);
  }
  const result = await api(`/api/chrome/jobs/${encodeURIComponent(activeJob.job.group_key)}/submit-uncertain`, {
    method: "POST",
    body: JSON.stringify({
      message,
      line_ids: activeJob.job.line_ids || [],
      failure_code: details.failureCode || "submitted_order_not_found",
      worker_id: activeJob.workerId || "",
    }),
  });
  await log(`Submit uncertain for ${activeJob.job.group_key}: ${message}`, windowId);
  await recordLastProcessed(activeJob, "chrome_error", result?.message || message);
  await incrementOrderProgress(`Processed ${activeJob.job.group_key}: submitted order not found.`);
  await cleanupCartBeforeNextJob(activeJob, windowId, result?.message || message);
  return { ...result, next_job_started: false, cleanup_required: true, next_group_key: "" };
}

async function markLineMissing(message, details = {}, windowId) {
  if (await forceStopActive()) {
    return { ok: false, stopped: true, message: "Force stop is active; ignored late missing-line report." };
  }
  const { activeJob } = await getWindowState(windowId);
  if (!activeJob?.job) return { ok: false, message: "No active job." };
  if (orderSubmitStarted(activeJob) || jobWasSubmittedToAmazon(activeJob.job)) {
    await log(`Ignored missing-line report for submitted ${activeJob.job.group_key}; reopening order history instead.`, windowId);
    await recoverSubmittedJobInWindow(activeJob.targetWindowId || windowId);
    return {
      ok: false,
      submitted_pending: true,
      message: `${activeJob.job.group_key} was already submitted to Amazon; ignored late missing report and reopened order history for reporting.`,
    };
  }
  try {
    await heartbeatJob(activeJob, windowId);
  } catch (error) {
    await log(`Continuing partial missing report after heartbeat failed for ${activeJob.job.group_key}: ${error.message}`, windowId);
  }
  const result = await api(`/api/chrome/jobs/${encodeURIComponent(activeJob.job.group_key)}/missing-line`, {
    method: "POST",
    body: JSON.stringify({
      message,
      line_ids: activeJob.job.line_ids || [],
      missing_asin: details.missingAsin || "",
      missing_line_id: details.missingLineId || null,
      failure_code: details.failureCode || "",
      requested_quantity: details.requestedQuantity ?? null,
      fulfilled_quantity: details.fulfilledQuantity ?? null,
      available_quantity: details.availableQuantity ?? null,
      worker_id: activeJob.workerId || "",
    }),
  });
  await log(`Partially marked missing in ${activeJob.job.group_key}: ${message}`, windowId);
  if (result?.ok && Number(result.remaining_count || 0) === 0) {
    await recordLastProcessed(activeJob, "missing", message);
    await incrementOrderProgress(`Processed ${activeJob.job.group_key}: missing.`);
    await setWindowJob(windowId, null);
    await navigateWindowToCart(windowId);
    const nextJob = await claimNextJobInWindow(windowId);
    return {
      ...result,
      next_job_started: Boolean(nextJob),
      cleanup_required: false,
      next_group_key: nextJob?.job?.group_key || "",
    };
  }
  return { ...result, next_job_started: false, cleanup_required: result?.ok && Number(result.remaining_count || 0) === 0, next_group_key: "" };
}

async function costlyJob(message, details = {}, windowId) {
  if (await forceStopActive()) {
    return { ok: false, stopped: true, message: "Force stop is active; ignored late costly-review report." };
  }
  const { activeJob } = await getWindowState(windowId);
  if (!activeJob?.job) return { ok: false, message: "No active job." };
  if (orderSubmitStarted(activeJob) || jobWasSubmittedToAmazon(activeJob.job)) {
    await log(`Ignored costly-review report for submitted ${activeJob.job.group_key}; reopening order history instead.`, windowId);
    await recoverSubmittedJobInWindow(activeJob.targetWindowId || windowId);
    return {
      ok: false,
      submitted_pending: true,
      message: `${activeJob.job.group_key} was already submitted to Amazon; ignored late costly-review report and reopened order history for reporting.`,
    };
  }
  await heartbeatJob(activeJob, windowId);
  const result = await api(`/api/chrome/jobs/${encodeURIComponent(activeJob.job.group_key)}/costly`, {
    method: "POST",
    body: JSON.stringify({
      message,
      line_ids: activeJob.job.line_ids || [],
      costly_asin: details.costlyAsin || "",
      costly_line_id: details.costlyLineId || null,
      store_total_price: details.storeTotalPrice || 0,
      amazon_total_price: details.amazonTotalPrice || 0,
      worker_id: activeJob.workerId || "",
    }),
  });
  await setWindowJob(windowId, null);
  await log(`Costly review ${activeJob.job.group_key}: ${message}`, windowId);
  await recordLastProcessed(activeJob, "costly", message);
  await incrementOrderProgress(`Processed ${activeJob.job.group_key}: costly review.`);
  return result;
}

async function clearFailedJobs() {
  const result = await api("/api/chrome/failed-jobs/clear", { method: "POST", body: JSON.stringify({}) });
  await chrome.storage.local.set({ activeJob: null, cachedQueueStatus: null });
  await log(result.message || `Cleared ${result.cleared || 0} failed Chrome job line(s).`);
  return result;
}

async function forceClearQueue(windowId = null) {
  await forceStopAll(windowId);
  const result = await api("/api/chrome/queue/force-clear", { method: "POST", body: JSON.stringify({}) });
  await chrome.storage.local.set({
    activeJob: null,
    activeJobsByWindow: {},
    cachedQueueStatus: null,
    controlWindowsById: {},
    orderProgress: {
      running: false,
      total: 0,
      processed: 0,
      message: result.message || "Chrome order queue force cleared.",
      updatedAt: Date.now(),
      source: "force-clear",
    },
  });
  await log(result.message || `Force cleared ${result.cleared || 0} Chrome queue line(s).`, windowId);
  return result;
}

async function activeOrderingInProgress() {
  const state = await getSettings();
  if (state.activeJob?.job) return true;
  return Object.values(state.activeJobsByWindow || {}).some((activeJob) => activeJob?.job);
}

async function setupAvailabilityAlarm() {
  if (!chrome.alarms?.create) return;
  await chrome.alarms.create(MISSING_ASIN_ALARM, {
    delayInMinutes: 10,
    periodInMinutes: MISSING_ASIN_CHECK_PERIOD_MINUTES,
  });
}

function waitForTabComplete(tabId, timeoutMs = 30000) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(listener);
      resolve();
    };
    const listener = (updatedTabId, changeInfo) => {
      if (updatedTabId === tabId && changeInfo.status === "complete") finish();
    };
    const timer = setTimeout(finish, timeoutMs);
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tabId).then((tab) => {
      if (tab?.status === "complete") finish();
    }).catch(() => finish());
  });
}

async function checkAmazonAvailabilityCandidate(candidate) {
  const amazonUrl = candidate.amazon_url || `https://www.amazon.com/dp/${encodeURIComponent(candidate.asin || "")}`;
  const tab = await chrome.tabs.create({ url: amazonUrl, active: false });
  try {
    await waitForTabComplete(tab.id, 30000);
    await new Promise((resolve) => setTimeout(resolve, 2500));
    try {
      return await chrome.tabs.sendMessage(tab.id, { type: "CHECK_ASIN_AVAILABILITY", asin: candidate.asin });
    } catch (error) {
      return {
        ok: false,
        in_stock: false,
        message: `Could not inspect Amazon product page: ${error.message || error}`,
        url: amazonUrl,
      };
    }
  } finally {
    if (tab?.id) chrome.tabs.remove(tab.id).catch(() => undefined);
  }
}

function uniquePreflightCandidates(job = {}) {
  const seen = new Set();
  return (job.items || [])
    .map((item) => ({
      asin: String(item.asin || "").trim().toUpperCase(),
      line_id: item.line_id || item.line_ids?.[0] || null,
      product_name: item.product_name || "",
      amazon_url: item.amazon_url || "",
      quantity: Number(item.quantity || 1),
    }))
    .filter((item) => {
      if (!item.asin || seen.has(item.asin)) return false;
      seen.add(item.asin);
      return true;
    });
}

function splitPartInfo(groupKey = "") {
  const match = String(groupKey || "").match(/^(.*)-part(\d+)of(\d+)$/i);
  if (!match) return null;
  return {
    base: match[1],
    index: Number(match[2] || 0),
    total: Number(match[3] || 0),
  };
}

function sameSplitOrder(left = {}, right = {}) {
  const leftInfo = splitPartInfo(left.group_key);
  const rightInfo = splitPartInfo(right.group_key);
  if (leftInfo?.base && rightInfo?.base) return leftInfo.base === rightInfo.base;
  const leftNames = (left.order_names || []).join("|");
  const rightNames = (right.order_names || []).join("|");
  return Boolean(leftNames && leftNames === rightNames);
}

function preflightCandidatesFromJobs(jobs = []) {
  const seen = new Set();
  const candidates = [];
  for (const job of jobs) {
    for (const candidate of uniquePreflightCandidates(job)) {
      if (seen.has(candidate.asin)) continue;
      seen.add(candidate.asin);
      candidates.push({ ...candidate, group_key: job.group_key, order_names: job.order_names || [] });
    }
  }
  return candidates;
}

async function preflightSplitQueueHead(workerId, splitMixedAsinOrders, fulfilAvailableMixedAsin, sourceWindowId = null) {
  if (splitMixedAsinOrders !== true) return { ok: true };
  for (let attempt = 0; attempt < 10; attempt += 1) {
    if (await forceStopActive()) return { ok: false, stopped: true, message: "Force stop is active; split-order preflight stopped." };
    const snapshot = await api(`/api/chrome/jobs?claim=false&job_limit=${fulfilAvailableMixedAsin === true ? 1 : 250}`, { timeoutMs: 15000 });
    const job = snapshot.jobs?.[0] || null;
    if (!job) return { ok: true, empty: true };
    const partInfo = splitPartInfo(job.group_key);
    const jobsToCheck = fulfilAvailableMixedAsin === true
      ? [job]
      : (snapshot.jobs || []).filter((candidateJob) => sameSplitOrder(job, candidateJob));
    const candidates = preflightCandidatesFromJobs(jobsToCheck.length ? jobsToCheck : [job]);
    if (candidates.length <= 1) return { ok: true };
    if (job.claimed_by) return { ok: true };

    await log(`Preflight checking ${candidates.length} ASIN(s) before ${partInfo ? `split order ${partInfo.base}` : `splitting ${job.group_key}`}.`, sourceWindowId);
    const results = [];
    for (const candidate of candidates) {
      const result = await checkAmazonAvailabilityCandidate(candidate);
      results.push({ candidate, result });
      if (result?.ok === false) {
        const message = `Could not preflight ${candidate.asin} before splitting ${job.group_key}: ${result.message || "Amazon page could not be inspected."}`;
        await log(message, sourceWindowId);
        return { ok: false, message };
      }
      if (result?.in_stock !== true) break;
    }

    const unavailable = results.find((entry) => entry.result?.in_stock !== true);
    if (!unavailable) {
      await log(`Preflight passed for ${job.group_key}; all ASINs are available before split ordering.`, sourceWindowId);
      return { ok: true };
    }

    const candidate = unavailable.candidate;
    const result = unavailable.result || {};
    const targetLabel = partInfo ? `split order ${partInfo.base}` : job.group_key;
    const message = `Preflight blocked ${targetLabel}: ASIN ${candidate.asin} is not available on Amazon. ${result.message || ""}`.trim();
    if (fulfilAvailableMixedAsin !== true) {
      const blockedMessage = `${message} Checkout was not started because Process available items in mixed-ASIN orders is off.`;
      for (const blockedJob of jobsToCheck.length ? jobsToCheck : [job]) {
        await api(`/api/chrome/jobs/${encodeURIComponent(blockedJob.group_key)}/preflight-missing`, {
          method: "POST",
          body: JSON.stringify({
            message: blockedMessage,
            missing_asin: candidate.asin,
            missing_line_id: candidate.group_key === blockedJob.group_key ? candidate.line_id || null : null,
            checked_url: result.url || candidate.amazon_url || "",
          }),
          timeoutMs: 20000,
        });
      }
      await log(`${blockedMessage} Moved ${jobsToCheck.length || 1} split part(s) to Missing ASINs and continuing queue.`, sourceWindowId);
      await recordLastProcessed(job, "missing", blockedMessage);
      await incrementOrderProgress(`Processed ${targetLabel}: missing.`);
      continue;
    }
    const response = await api(`/api/chrome/jobs/${encodeURIComponent(candidate.group_key || job.group_key)}/missing-line`, {
      method: "POST",
      body: JSON.stringify({
        worker_id: workerId,
        message,
        missing_asin: candidate.asin,
        missing_line_id: candidate.line_id || null,
        checked_url: result.url || candidate.amazon_url || "",
      }),
      timeoutMs: 20000,
    });
    await log(response.message || message, sourceWindowId);
    await recordLastProcessed(job, "missing", message);
    await incrementOrderProgress(`Processed ${job.group_key}: preflight missing.`);
  }
  return { ok: false, message: "Stopped after too many split-order preflight failures. Please review the queue." };
}

async function runMissingAsinAvailabilityCheck(force = false) {
  const state = await getSettings();
  if (state.availabilityCheckInFlight) return { ok: false, message: "Availability check already running." };
  if (!force && await activeOrderingInProgress()) return { ok: false, message: "Skipped because Chrome fulfilment is active." };
  await chrome.storage.local.set({ availabilityCheckInFlight: true });
  let checked = 0;
  let backInStock = 0;
  let queued = 0;
  try {
    const payload = await api("/api/chrome/missing-asin-checks?limit=40", { timeoutMs: 15000 });
    const candidates = payload.candidates || [];
    for (const candidate of candidates) {
      if (!force && await activeOrderingInProgress()) {
        await log("Paused missing ASIN availability check because a fulfilment job started.");
        break;
      }
      const result = await checkAmazonAvailabilityCandidate(candidate);
      checked += 1;
      const report = await api("/api/chrome/missing-asin-checks/report", {
        method: "POST",
        body: JSON.stringify({
          line_id: candidate.line_id,
          asin: candidate.asin,
          in_stock: Boolean(result?.in_stock),
          message: result?.message || "",
          price: result?.price || 0,
          checked_url: result?.url || candidate.amazon_url || "",
          checked_by: await getWorkerId(),
        }),
        timeoutMs: 20000,
      });
      if (result?.in_stock) backInStock += 1;
      if (report?.queued) queued += Number(report.queued || 0);
      await new Promise((resolve) => setTimeout(resolve, 3000));
    }
    await chrome.storage.local.set({ missingAsinAvailabilityLastRunAt: Date.now() });
    await log(`Missing ASIN availability check complete: checked ${checked}, back in stock ${backInStock}, queued ${queued}.`);
    return { ok: true, checked, back_in_stock: backInStock, queued };
  } finally {
    await chrome.storage.local.set({ availabilityCheckInFlight: false });
  }
}

chrome.action.onClicked.addListener((tab) => {
  openControlWindow(tab);
});

chrome.runtime.onStartup.addListener(() => {
  releaseAllStoredJobs().catch((error) => log(`Could not release previous Chrome session jobs: ${error.message}`));
  ensureContentScriptsInAmazonTabs("Chrome startup").catch((error) => log(`Could not recover Amazon tabs after startup: ${error.message}`));
  setupAvailabilityAlarm().catch((error) => log(`Could not schedule missing ASIN availability checks: ${error.message}`));
});

chrome.runtime.onInstalled.addListener(() => {
  ensureContentScriptsInAmazonTabs("extension reload").catch((error) => log(`Could not recover Amazon tabs after extension reload: ${error.message}`));
  setupAvailabilityAlarm().catch((error) => log(`Could not schedule missing ASIN availability checks: ${error.message}`));
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== MISSING_ASIN_ALARM) return;
  runMissingAsinAvailabilityCheck(false).catch((error) => log(`Missing ASIN availability check failed: ${error.message}`));
});

chrome.windows.onRemoved.addListener((windowId) => {
  (async () => {
    const { controlWindowsById, activeJobsByWindow } = await getSettings();
    const targetWindowId = controlWindowsById?.[String(windowId)] || null;
    if (targetWindowId) {
      await setControlWindow(windowId, null);
      return;
    }
    if (activeJobsByWindow?.[String(windowId)]) {
      await stopJob(windowId);
    }
  })().catch((error) => log(`Could not release Chrome job after window closed: ${error.message}`));
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    const windowId = messageWindowId(message, sender);
    if (message.type === "FORCE_STOP_ALL") return forceStopAll(windowId);
    if (message.type === "FORCE_CLEAR_QUEUE") return forceClearQueue(windowId);
    if (message.type === "CLEAR_FORCE_STOP") {
      await setForceStop(false);
      return { ok: true };
    }
    if (message.type === "START_NEXT") return startNextJob(windowId);
    if (message.type === "START_BROWSERLESS") return startBrowserlessOrderRun(windowId);
    if (message.type === "GET_BROWSERLESS_STATUS") return browserlessOrderStatus();
    if (message.type === "REGISTER_CONTROL_WINDOW") {
      await setControlWindow(Number(message.controlWindowId || 0) || null, Number(message.targetWindowId || 0) || null);
      return { ok: true };
    }
    if (message.type === "STOP_JOB") return stopJob(windowId);
    if (message.type === "SKIP_JOB") return skipJob(windowId);
    if (message.type === "MARK_CURRENT_MISSING") return markCurrentJobMissing(windowId);
    if (message.type === "CHECK_EXISTING_AMAZON_ORDER") return checkExistingAmazonOrder(windowId);
    if (message.type === "RESET_DUPLICATE_FULFILMENT") return resetDuplicateFulfilment(windowId);
    if (message.type === "TOGGLE_PAUSE") return togglePause(windowId);
    if (message.type === "GET_STATE") {
      await releaseMissingWindowJobs();
      await refreshActiveJobFromQueue(windowId);
      const { activeJob } = await getWindowState(windowId);
      if (!await forceStopActive() && (!activeJob?.job || !orderSubmitStarted(activeJob))) {
        const recovered = await recoverSubmittedJobInWindow(windowId);
        if (recovered?.activeJob) return getWindowState(recovered.targetWindowId || windowId);
      }
      return getWindowState(windowId);
    }
    if (message.type === "GET_QUEUE_STATUS") {
      await releaseMissingWindowJobs();
      return getQueueStatus();
    }
    if (message.type === "REMEMBER_RECENT_AMAZON_ORDERS") return rememberRecentAmazonOrders(message.orders || []);
    if (message.type === "LOOKUP_AMAZON_HISTORY_ORDERS") return lookupAmazonHistoryOrders(message.orders || []);
    if (message.type === "SYNC_AMAZON_HISTORY_ORDER") return syncAmazonHistoryOrder(message.order || {});
    if (message.type === "GET_RECENT_AMAZON_ORDERS") {
      const { recentAmazonOrders } = await getSettings();
      return { ok: true, orders: recentAmazonOrders || [] };
    }
    if (message.type === "CLAIM_NEXT_IN_WINDOW") {
      const nextJob = await claimNextJobInWindow(windowId);
      return {
        ok: true,
        next_job_started: Boolean(nextJob),
        next_group_key: nextJob?.job?.group_key || "",
        message: nextJob ? `Started next ${nextJob.job.group_key}.` : "No more queued Chrome jobs found.",
      };
    }
    if (message.type === "FINISH_CLEANUP_AND_CLAIM_NEXT") return finishCleanupAndClaimNext(windowId);
    if (message.type === "RECOVER_SUBMITTED_JOB") return recoverSubmittedJobInWindow(windowId);
    if (message.type === "RUN_MISSING_ASIN_AVAILABILITY_CHECK") return runMissingAsinAvailabilityCheck(true);
    if (message.type === "TEST_CONNECTION") return testConnection();
    if (message.type === "GET_ACTIVE_JOB") {
      if (await forceStopActive()) return { ok: true, activeJob: null, forceStopped: true };
      let { activeJob } = await getWindowState(windowId);
      if (!activeJob?.job && windowId) {
        const state = await getSettings();
        const globalJob = state.activeJob?.job?.group_key ? state.activeJob : null;
        const groupAlreadyAttached = globalJob?.job?.group_key && Object.values(state.activeJobsByWindow || {}).some((job) => (
          job?.job?.group_key === globalJob.job.group_key
        ));
        if (globalJob?.job && !groupAlreadyAttached) {
          globalJob.targetWindowId = windowId;
          await setWindowJob(windowId, globalJob);
          activeJob = globalJob;
          await log(`Reattached ${globalJob.job.group_key} to Amazon window ${windowId}.`, windowId);
        }
      }
      if (!activeJob?.job || !orderSubmitStarted(activeJob)) {
        const recovered = await recoverSubmittedJobInWindow(windowId);
        if (recovered?.activeJob) {
          activeJob = recovered.activeJob;
          return { ok: true, activeJob };
        }
      }
      if (activeJob?.job && activeJob?.workerId && Date.now() - Number(activeJob.lastHeartbeatAt || 0) > 5 * 60 * 1000) {
        try {
          await heartbeatJob(activeJob, windowId);
        } catch (error) {
          const message = String(error.message || "");
          if (/lock is no longer owned|no longer active/i.test(message)) {
            await clearStoredJobGroup(activeJob.job.group_key);
            await log(
              `Cleared stale active job ${activeJob.job.group_key}; heartbeat says the server lock is no longer active: ${message}`,
              windowId,
            );
            return { ok: true, activeJob: null };
          }
          await log(`Chrome job lock heartbeat failed: ${message}`, windowId);
        }
      }
      return { ok: true, activeJob: (await getWindowState(windowId)).activeJob };
    }
    if (message.type === "HEARTBEAT_JOB") {
      if (await forceStopActive()) return { ok: false, stopped: true, message: "Force stop is active." };
      const { activeJob } = await getWindowState(windowId);
      if (!activeJob?.job) return { ok: false, message: "No active job." };
      await heartbeatJob(activeJob, windowId);
      return { ok: true };
    }
    if (message.type === "MARK_ORDER_SUBMITTED") {
      if (await forceStopActive()) return { ok: false, stopped: true, message: "Force stop is active; ignored submit marker." };
      return markAmazonSubmitted(windowId);
    }
    if (message.type === "SET_ACTIVE_JOB") {
      if (await forceStopActive()) return { ok: false, stopped: true, message: "Force stop is active; ignored active job update." };
      const incomingJob = message.activeJob || null;
      const { activeJob: currentJob } = await getWindowState(windowId);
      if (isLateCompletedJobUpdate(currentJob, incomingJob)) {
        await log(`Ignored late completed-state update for ${incomingJob.job.group_key}.`, windowId);
        return { ok: true, ignored_late_completed_update: true };
      }
      await setWindowJob(windowId, incomingJob);
      return { ok: true };
    }
    if (message.type === "SET_API_BASE") {
      await chrome.storage.local.set({
        apiBase: normalizeApiBase(message.apiBase),
        adminToken: message.adminToken || "",
        cardLast4Preference: message.cardLast4Preference || "",
        editExistingAddress: message.editExistingAddress !== false,
        fulfilAvailableMixedAsin: message.fulfilAvailableMixedAsin === true,
        splitMixedAsinOrders: message.splitMixedAsinOrders === true,
        browserlessOrderMode: message.browserlessOrderMode === true,
      });
      return { ok: true };
    }
    if (message.type === "COMPLETE_JOB") return completeJob(message.orderId, message.orderUrl, message.amazonAccountName || "", windowId, message.orderMappings || [], message.orderDate || "");
    if (message.type === "MARK_LINE_MISSING") return markLineMissing(message.message || "Chrome extension line is missing.", message, windowId);
    if (message.type === "POST_SUBMIT_UNPLACED") return postSubmitUnplaced(message.message || "Amazon did not place the order after submit.", message, windowId);
    if (message.type === "SUBMIT_UNCERTAIN") return submitUncertain(message.message || "Amazon Place Order was submitted, but no matching Amazon order ID was found.", message, windowId);
    if (message.type === "FAIL_JOB") return failJob(message.message || "Chrome extension job failed.", message, windowId);
    if (message.type === "COSTLY_JOB") return costlyJob(message.message || "Chrome extension job needs costly approval.", message, windowId);
    if (message.type === "CLEAR_FAILED_JOBS") return clearFailedJobs();
    return { ok: false, message: "Unknown message." };
  })()
    .then((result) => sendResponse(result))
    .catch((error) => sendResponse({ ok: false, message: connectionErrorMessage(error) }));
  return true;
});
