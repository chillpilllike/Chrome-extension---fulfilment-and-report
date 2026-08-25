const DEFAULT_API_BASE = "http://127.0.0.1:8000";
const LOCAL_ADMIN_TOKEN_FALLBACK = "1284";
const EXPECTED_CONTENT_SCRIPT_BUILD = "2026-08-26-business-payment-native-card-v133";
const ACTIVE_JOB_HEARTBEAT_MS = 60 * 1000;
const completionLocks = new Set();
let queueStatusInFlight = null;
let lastReleaseMissingWindowJobsAt = 0;
let releaseMissingWindowJobsInFlight = null;
const recoverSubmittedJobsInFlight = new Map();
let lastEmptyRecoverSubmittedJobAt = 0;
let startNextJobInFlight = null;
const claimNextJobInFlight = new Map();
const submitProtectionLocks = new Set();
const MISSING_ASIN_ALARM = "nutricity-missing-asin-availability";
const MISSING_ASIN_CHECK_PERIOD_MINUTES = 60;
const FULFILMENT_WATCHDOG_ALARM = "nutricity-fulfilment-watchdog";
const FULFILMENT_WATCHDOG_PERIOD_MINUTES = 1;
const AUTO_ORDER_ALARM = "nutricity-auto-order-queue";
const AUTO_ORDER_PERIOD_MINUTES = 1;
const BROWSERLESS_SWITCH_POLL_MS = 2000;
const BROWSERLESS_SWITCH_TIMEOUT_MS = 10 * 60 * 1000;
const DIAGNOSTIC_SESSION_LIMIT = 4;
const DIAGNOSTIC_ENTRY_LIMIT = 120;
const DIAGNOSTIC_DUPLICATE_SUPPRESS_MS = 3000;
const recentDiagnosticWrites = new Map();

async function getSettings() {
  const data = await chrome.storage.local.get({
    apiBase: DEFAULT_API_BASE,
    adminToken: "",
    cardLast4Preference: "",
    editExistingAddress: true,
    fulfilAvailableMixedAsin: false,
    splitMixedAsinOrders: false,
    autoOrderQueue: false,
    browserlessOrderMode: false,
    pauseBeforePlaceOrder: false,
    preferRewardedLaterDelivery: false,
    deliveryLimitDays: 5,
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
    diagnosticSessions: { currentSessionId: "", sessions: [] },
  });
  const session = await chrome.storage.session.get({ autoOrderingRunning: false });
  return { ...data, autoOrderingRunning: session.autoOrderingRunning === true };
}

async function autoOrderingIsRunning() {
  const session = await chrome.storage.session.get({ autoOrderingRunning: false });
  return session.autoOrderingRunning === true;
}

async function setAutoOrderingRunning(running) {
  await chrome.storage.session.set({ autoOrderingRunning: running === true });
  if (running !== true && chrome.alarms?.clear) {
    await chrome.alarms.clear(AUTO_ORDER_ALARM).catch(() => false);
  }
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

function senderPageInfo(sender = {}, message = {}) {
  const tab = sender.tab || {};
  return {
    url: message.url || tab.url || "",
    title: message.title || tab.title || "",
    tabId: tab.id || null,
    windowId: tab.windowId || message.targetWindowId || null,
  };
}

async function getWindowState(windowId) {
  const state = await getSettings();
  const key = String(windowId || "");
  const windowJob = windowId ? state.activeJobsByWindow?.[key] || null : state.activeJob;
  const fallbackSubmittedJob = windowId
    && !windowJob
    && Number(state.activeJob?.targetWindowId || 0) === Number(windowId)
    && orderSubmitStarted(state.activeJob)
    ? state.activeJob
    : null;
  return {
    ...state,
    targetWindowId: windowId || null,
    activeJob: windowJob || fallbackSubmittedJob,
    logs: windowId ? state.logsByWindow?.[key] || [] : state.logs,
  };
}

// A control popup can be opened from the first Amazon window, while an order
// later continues in a newly-created worker window.  Popup polling must follow
// that live worker; otherwise it writes the same job into the stale window
// slot and continually fights the content script over activeJobsByWindow.
async function liveWorkerWindowId(windowId) {
  const state = await getSettings();
  const globalJob = state.activeJob || null;
  if (globalJob?.job?.group_key && globalJob?.targetWindowId) {
    return Number(globalJob.targetWindowId) || windowId;
  }
  const mappedWindowId = Number(state.controlWindowsById?.[String(windowId)] || 0);
  return mappedWindowId || windowId;
}

async function existingChromeWindowId(windowId) {
  const normalized = Number(windowId || 0) || null;
  if (!normalized) return null;
  try {
    await chrome.windows.get(normalized);
    return normalized;
  } catch {
    return null;
  }
}

async function setWindowJob(windowId, activeJob, options = {}) {
  if (activeJob && await forceStopActive()) return;
  if (activeJob && !orderSubmitStarted(activeJob) && !await autoOrderingIsRunning()) {
    return;
  }
  const state = await getSettings();
  const { activeJobsByWindow } = state;
  const next = { ...(activeJobsByWindow || {}) };
  const key = String(windowId || "");
  const current = windowId ? next[key] || null : state.activeJob || null;
  const sameGroup = current?.job?.group_key && activeJob?.job?.group_key === current.job.group_key;
  if (
    sameGroup
    && Number(current?.resetRevision || 0) > Number(activeJob?.resetRevision || 0)
    && options.allowSubmittedReset !== true
  ) {
    await diagnosticLog(`Ignored stale pre-reset update for ${activeJob.job.group_key}.`, {
      windowId,
      activeJob: current,
      level: "warn",
      details: {
        current_reset_revision: Number(current.resetRevision || 0),
        incoming_reset_revision: Number(activeJob.resetRevision || 0),
        reason: options.reason || "",
      },
    });
    return;
  }
  if (sameGroup && activeJobHasReportedOrderId(current)) {
    const currentAttemptedAt = Number(current.reportAttemptedAt || 0);
    const incomingAttemptedAt = Number(activeJob.reportAttemptedAt || 0);
    const incomingHasOrderId = activeJobHasReportedOrderId(activeJob);
    if (!incomingHasOrderId || incomingAttemptedAt < currentAttemptedAt) {
      activeJob = {
        ...activeJob,
        stage: "reporting_complete",
        reportedOrderId: current.reportedOrderId,
        reportAttemptedAt: current.reportAttemptedAt,
        amazonSubmittedAt: activeJob.amazonSubmittedAt || current.amazonSubmittedAt || Date.now(),
      };
    }
  }
  if (sameGroup && options.allowItemRemoval !== true) {
    const currentItems = Array.isArray(current?.job?.items) ? current.job.items : [];
    const incomingItems = Array.isArray(activeJob?.job?.items) ? activeJob.job.items : [];
    if (currentItems.length > incomingItems.length) {
      await diagnosticLog(
        `Prevented active job ${activeJob.job.group_key} from shrinking from ${currentItems.length} items to ${incomingItems.length}.`,
        {
          windowId,
          activeJob: current,
          level: "warn",
          details: {
            current_asins: currentItems.map((item) => item?.asin || ""),
            incoming_asins: incomingItems.map((item) => item?.asin || ""),
            reason: options.reason || "",
          },
        },
      );
      activeJob = {
        ...activeJob,
        job: {
          ...activeJob.job,
          items: currentItems,
          line_ids: current.job.line_ids || activeJob.job.line_ids || [],
        },
      };
    }
  }
  const allowedSubmittedCleanup = options.allowSubmittedCleanup === true
    && activeJob?.stage === "cleanup_after_failure"
    && activeJob?.cleanupAfterFailure === true;
  const allowedSubmittedReset = options.allowSubmittedReset === true
    && activeJob?.stage === "product"
    && activeJob?.resetUnplacedSubmit === true;
  if (sameGroup && orderSubmitStarted(current) && !orderSubmitStarted(activeJob) && !allowedSubmittedCleanup && !allowedSubmittedReset) {
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

function diagnosticSessionId(activeJob = null, windowId = null) {
  const groupKey = activeJob?.job?.group_key || "";
  if (groupKey) return `${groupKey}:${activeJob?.startedAt || activeJob?.amazonSubmittedAt || "active"}`;
  return `general:${windowId || "global"}`;
}

function diagnosticJobSnapshot(activeJob = null, windowId = null) {
  if (!activeJob?.job) return null;
  const job = activeJob.job || {};
  return {
    group_key: job.group_key || "",
    order_names: job.order_names || [],
    recipient_name: job.recipient_name || "",
    line_ids: job.line_ids || [],
    items: (job.items || []).map((item) => ({
      asin: item.asin || "",
      quantity: item.quantity || 0,
      line_ids: item.line_ids || [],
      amazon_status: item.amazon_status || "",
    })),
    stage: activeJob.stage || "",
    paused: Boolean(activeJob.paused),
    pausedStage: activeJob.pausedStage || "",
    workerId: activeJob.workerId || "",
    targetWindowId: activeJob.targetWindowId || windowId || null,
    amazonSubmittedAt: activeJob.amazonSubmittedAt || null,
    reportedOrderId: activeJob.reportedOrderId || "",
  };
}

async function diagnosticLog(message, options = {}) {
  const windowId = options.windowId || null;
  const state = await chrome.storage.local.get({
    activeJob: null,
    activeJobsByWindow: {},
    diagnosticSessions: { currentSessionId: "", sessions: [] },
  });
  const activeJob = options.activeJob || (windowId ? state.activeJobsByWindow?.[String(windowId)] : state.activeJob) || state.activeJob || null;
  const sessionId = options.sessionId || diagnosticSessionId(activeJob, windowId);
  const duplicateKey = [
    sessionId,
    options.level || "info",
    options.source || "background",
    String(message || ""),
    windowId || "",
  ].join("|");
  const now = Date.now();
  const lastWriteAt = Number(recentDiagnosticWrites.get(duplicateKey) || 0);
  if (now - lastWriteAt < DIAGNOSTIC_DUPLICATE_SUPPRESS_MS) return;
  recentDiagnosticWrites.set(duplicateKey, now);
  if (recentDiagnosticWrites.size > 200) {
    for (const [key, value] of recentDiagnosticWrites.entries()) {
      if (now - Number(value || 0) > 60 * 1000) recentDiagnosticWrites.delete(key);
    }
  }
  const sessions = Array.isArray(state.diagnosticSessions?.sessions) ? [...state.diagnosticSessions.sessions] : [];
  let session = sessions.find((item) => item.id === sessionId);
  if (!session) {
    session = {
      id: sessionId,
      startedAt: Date.now(),
      updatedAt: Date.now(),
      windowId: windowId || activeJob?.targetWindowId || null,
      groupKey: activeJob?.job?.group_key || "",
      orderNames: activeJob?.job?.order_names || [],
      entries: [],
    };
    sessions.unshift(session);
  }
  const entry = {
    at: now,
    time: new Date().toISOString(),
    level: options.level || "info",
    source: options.source || "background",
    message: String(message || ""),
    windowId: windowId || activeJob?.targetWindowId || null,
    url: options.url || options.page?.url || "",
    page: options.page || null,
    job: diagnosticJobSnapshot(activeJob, windowId),
    details: options.details || null,
  };
  session.updatedAt = entry.at;
  session.windowId = session.windowId || entry.windowId;
  session.groupKey = session.groupKey || activeJob?.job?.group_key || "";
  session.orderNames = session.orderNames?.length ? session.orderNames : activeJob?.job?.order_names || [];
  session.entries = [entry, ...(session.entries || [])].slice(0, DIAGNOSTIC_ENTRY_LIMIT);
  const trimmed = sessions
    .sort((a, b) => Number(b.updatedAt || 0) - Number(a.updatedAt || 0))
    .slice(0, DIAGNOSTIC_SESSION_LIMIT);
  await chrome.storage.local.set({
    diagnosticSessions: {
      currentSessionId: sessionId,
      sessions: trimmed,
    },
  });
}

function shouldRecordDiagnosticFromLog(message = "", details = null) {
  if (details) return true;
  return /fail|error|could not|ignored|duplicate|missing|submitted|completed|protected|force|paused|recovered|released|stale|timeout/i.test(String(message || ""));
}

async function log(message, windowId = null, details = null) {
  const { logs, logsByWindow } = await chrome.storage.local.get({ logs: [], logsByWindow: {} });
  const entry = `${new Date().toLocaleTimeString()} ${message}`;
  if (!windowId) {
    await chrome.storage.local.set({ logs: [entry, ...logs].slice(0, 40) });
    if (shouldRecordDiagnosticFromLog(message, details)) await diagnosticLog(message, { windowId, details });
    return;
  }
  const key = String(windowId);
  const next = { ...(logsByWindow || {}) };
  next[key] = [entry, ...(next[key] || [])].slice(0, 40);
  await chrome.storage.local.set({ logsByWindow: next, logs: next[key] });
  if (shouldRecordDiagnosticFromLog(message, details)) await diagnosticLog(message, { windowId, details });
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
  if (active === true && chrome.alarms?.clear) {
    await chrome.alarms.clear(MISSING_ASIN_ALARM).catch(() => undefined);
    await chrome.alarms.clear(FULFILMENT_WATCHDOG_ALARM).catch(() => undefined);
  } else if (active !== true) {
    await setupAvailabilityAlarm().catch((error) => log(`Could not schedule missing ASIN availability checks: ${error.message}`));
    await setupFulfilmentWatchdogAlarm().catch((error) => log(`Could not schedule fulfilment watchdog: ${error.message}`));
  }
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
  const rawItems = (order.items || [])
    .map((item) => ({
      asin: String(item?.asin || "").trim().toUpperCase(),
      quantity: Math.max(1, Math.round(Number(item?.quantity || 1))),
      quantity_verified: item?.quantity_verified === true,
    }))
    .filter((item) => item.asin);
  const itemsByAsin = new Map();
  for (const item of rawItems) {
    const existing = itemsByAsin.get(item.asin);
    if (!existing) {
      itemsByAsin.set(item.asin, item);
      continue;
    }
    if (!existing.quantity_verified || item.quantity_verified) {
      existing.quantity = Math.max(existing.quantity, item.quantity);
    }
    existing.quantity_verified = existing.quantity_verified || item.quantity_verified;
  }
  const items = [...itemsByAsin.values()];
  const asinQuantities = Object.fromEntries(items.map((item) => [item.asin, item.quantity]));
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
    timeoutMs: 18000,
  });
  const { apiBase } = await getSettings();
  const base = normalizeApiBase(apiBase);
  return {
    app_base_url: base,
    ...result,
    odoo_direct: result?.odoo_direct || result?.odooDirect || {},
    odoo_direct_error: result?.odoo_direct_error || result?.odooDirectError || "",
    not_found_url: `${base}${result.not_found_url || "/amazon-order-history-unmatched"}`,
  };
}

async function lookupAmazonHistoryOdooDirect(orders = []) {
  const normalized = (orders || []).map(normalizeRecentAmazonOrder).filter(Boolean);
  if (!normalized.length) return { ok: true, odoo_direct: {} };
  return api("/api/chrome/order-history/odoo-direct", {
    method: "POST",
    body: JSON.stringify({ orders: normalized }),
    timeoutMs: 18000,
  });
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
  const settings = await getSettings();
  const base = normalizeApiBase(options.apiBase || settings.apiBase);
  const adminToken = options.adminToken ?? settings.adminToken;
  const requestPath = String(path || "").startsWith("/") ? path : `/${path}`;
  const { timeoutMs = 45000, apiBase: _apiBase, adminToken: _adminToken, ...fetchOptions } = options;
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
      headers: { "Content-Type": "application/json", ...(authTokens[index] ? { "X-Admin-Token": authTokens[index] } : {}), ...(fetchOptions.headers || {}) },
      signal: controller.signal,
      ...fetchOptions,
      // Chrome fulfilment claims and heartbeats are stateful even though some
      // legacy endpoints use GET. Never let the browser reuse an old empty
      // claim response for a live worker.
      cache: "no-store",
    }).finally(() => clearTimeout(timeout));
    if (response.ok || response.status !== 401 || index === authTokens.length - 1) break;
  }
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
    const queue = await api("/api/chrome/jobs?claim=false&job_limit=12", { timeoutMs: 12000 });
    const jobCount = Number(queue.job_count || queue.jobs?.length || 0);
    const extraCounts = (queue.counts || [])
      .filter((item) => item.state !== "submitted" && Number(item.count || 0) > 0)
      .map((item) => `${item.count} ${item.state}`)
      .join(", ");
    const queueText = ` Chrome fulfilment jobs waiting: ${jobCount}.`;
    const detailText = extraCounts ? ` Other non-queued lines: ${extraCounts}.` : "";
    return { ok: true, message: `Connected to ${base}. Admin token accepted.${queueText}${detailText}` };
  } catch (error) {
    throw new Error(connectionErrorMessage(error, base));
  }
}

function tabOrigin(url = "") {
  try {
    const parsed = new URL(url);
    if (!["http:", "https:"].includes(parsed.protocol)) return "";
    if (/amazon\./i.test(parsed.hostname)) return "";
    return parsed.origin;
  } catch (_) {
    return "";
  }
}

function isLikelyAppTab(tab = {}, origin = "") {
  if (/^https?:\/\/(?:127\.0\.0\.1|localhost)(?::|\/|$)/i.test(origin)) return true;
  const haystack = `${tab.url || ""} ${tab.title || ""}`;
  return /nutricity|fulfilment|fulfillment|chrome[_-]?queue|amazon[_-]?accounts|stores|inventory/i.test(haystack);
}

async function readAppTokenFromTab(tabId) {
  if (!chrome.scripting?.executeScript || !tabId) return "";
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => (
        window.localStorage.getItem("admin_access_token")
        || window.sessionStorage.getItem("admin_access_token")
        || ""
      ),
    });
    return String(results?.[0]?.result || "").trim();
  } catch (_) {
    return "";
  }
}

async function probeAppOrigin(origin, adminToken = "") {
  await api("/api/settings/admin-access", {
    apiBase: origin,
    adminToken,
    timeoutMs: 8000,
  });
  return api("/api/chrome/jobs?claim=false&job_limit=12", {
    apiBase: origin,
    adminToken,
    timeoutMs: 12000,
  });
}

async function discoverAppQueue(currentSnapshot = null) {
  if (!chrome.tabs?.query) return null;
  const tabs = await chrome.tabs.query({});
  const seen = new Set();
  const candidates = [];
  for (const tab of tabs) {
    const origin = tabOrigin(tab.url || "");
    if (!origin || seen.has(origin)) continue;
    if (!isLikelyAppTab(tab, origin)) continue;
    seen.add(origin);
    candidates.push({ tabId: tab.id, origin, active: tab.active, windowId: tab.windowId });
  }
  candidates.sort((a, b) => Number(b.active) - Number(a.active));
  for (const candidate of candidates) {
    const adminToken = await readAppTokenFromTab(candidate.tabId);
    try {
      const payload = await probeAppOrigin(candidate.origin, adminToken);
      const jobs = payload.jobs || [];
      const jobCount = Number(payload.job_count || jobs.length || 0);
      if (jobCount <= 0 && currentSnapshot?.job_count > 0) continue;
      await chrome.storage.local.set({
        apiBase: normalizeApiBase(candidate.origin),
        ...(adminToken ? { adminToken } : {}),
        cachedQueueStatus: null,
      });
      await log(`Connected popup to app at ${candidate.origin}.`);
      return { origin: candidate.origin, adminToken, payload };
    } catch (_) {
      // Try the next open app-looking tab.
    }
  }
  return null;
}

async function getQueueStatus() {
  const workerId = await getWorkerId();
  if (queueStatusInFlight) return queueStatusInFlight;
  queueStatusInFlight = (async () => {
  try {
    const payload = await api(`/api/chrome/jobs?claim=false&job_limit=12&worker_id=${encodeURIComponent(workerId)}`);
    const empty = Number(payload.job_count || payload.jobs?.length || 0) <= 0;
    if (empty) {
      const discovered = await discoverAppQueue(payload);
      if (discovered?.payload) {
        const discoveredPayload = discovered.payload;
        const snapshot = {
          ok: true,
          jobs: discoveredPayload.jobs || [],
          counts: discoveredPayload.counts || [],
          job_count: Number(discoveredPayload.job_count || 0),
          workerId,
          cached_at: Date.now(),
          stale: false,
          message: `Connected to app at ${discovered.origin}.`,
        };
        await chrome.storage.local.set({ cachedQueueStatus: snapshot });
        return snapshot;
      }
    }
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
    const discovered = await discoverAppQueue();
    if (discovered?.payload) {
      const payload = discovered.payload;
      const snapshot = {
        ok: true,
        jobs: payload.jobs || [],
        counts: payload.counts || [],
        job_count: Number(payload.job_count || 0),
        workerId,
        cached_at: Date.now(),
        stale: false,
        message: `Connected to app at ${discovered.origin}.`,
      };
      await chrome.storage.local.set({ cachedQueueStatus: snapshot });
      return snapshot;
    }
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
        const verified = await clearJobIfBackendCompleted(activeJob, windowId, activeJob.reportedOrderId || "", "Verified submitted refresh after lock loss for");
        if (verified.cleared) return verified.nextJob || null;
        await log(
          `Kept submitted active job ${activeJob.job.group_key}; lock was gone but app completion is not confirmed: ${message}`,
          windowId,
        );
        return activeJob;
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
    if (activeJobServerLockReleased(currentJob || activeJob, freshJob)) {
      await clearStoredJobGroup(activeJob.job.group_key);
      await log(
        `Cleared stale active job ${activeJob.job.group_key}; the server queue no longer has a lock for this worker.`,
        windowId,
      );
      return null;
    }
    const next = { ...(currentJob || activeJob), job: freshJob, jobRefreshedAt: Date.now() };
    if (jobWasSubmittedToAmazon(freshJob)) {
      next.stage = next.stage === "reporting_complete" && activeJobHasReportedOrderId(next)
        ? "reporting_complete"
        : "find_order_id";
      next.amazonSubmittedAt = next.amazonSubmittedAt || Date.now();
      if (!next.paused) next.pausedStage = null;
    }
    await setWindowJob(windowId, next, { reason: "queue_refresh" });
    return (await getWindowState(windowId)).activeJob || next;
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
  return true;
}

function activeJobLineIds(activeJob) {
  return (activeJob?.job?.line_ids || [])
    .map((lineId) => Number(lineId || 0))
    .filter((lineId) => Number.isFinite(lineId) && lineId > 0);
}

async function getChromeJobCompletionStatus(activeJob) {
  const groupKey = activeJob?.job?.group_key || "";
  if (!groupKey) return null;
  const lineIds = activeJobLineIds(activeJob);
  const query = lineIds.length ? `?line_ids=${encodeURIComponent(lineIds.join(","))}` : "";
  return api(`/api/chrome/jobs/${encodeURIComponent(groupKey)}/completion-status${query}`, { timeoutMs: 12000 });
}

function completionStatusMatchesOrder(status, orderId = "") {
  const chatterAccepted = status?.odoo_chatter_queued === true || status?.odoo_chatter_confirmed === true;
  const dataSaved = status?.data_saved === true || (status?.completed === true && status?.odoo_chatter_confirmed === true);
  if (!status?.completed || !dataSaved || !chatterAccepted) return false;
  const expectedOrderIds = String(orderId || "").match(/\b\d{3}-\d{7}-\d{7}\b/g) || [];
  const orderIds = (status.amazon_order_ids || []).map((value) => String(value || "").trim()).filter(Boolean);
  return !expectedOrderIds.length || !orderIds.length || expectedOrderIds.some((expected) => orderIds.includes(expected));
}

async function clearJobIfBackendCompleted(activeJob, windowId, orderId = "", reason = "") {
  try {
    const status = await getChromeJobCompletionStatus(activeJob);
    if (!completionStatusMatchesOrder(status, orderId)) return { cleared: false, status };
    await clearStoredJobGroup(activeJob.job.group_key);
    await log(
      `${reason || "Cleared completed Chrome job"} ${activeJob.job.group_key}; app has Amazon order ${status.amazon_order_ids?.join(", ") || orderId || "recorded"}.`,
      windowId,
    );
    const nextJob = await claimNextJobInWindow(windowId);
    return { cleared: true, status, nextJob };
  } catch (error) {
    await log(`Could not verify completion status for ${activeJob?.job?.group_key || "Chrome job"}: ${error.message}`, windowId);
    return { cleared: false, error };
  }
}

function activeJobServerLockReleased(activeJob, freshJob) {
  if (!activeJob?.job?.group_key || !freshJob?.group_key) return false;
  if (orderSubmitStarted(activeJob) || jobWasSubmittedToAmazon(freshJob)) return false;
  const claimedBy = String(freshJob.claimed_by || "").trim();
  if (!claimedBy) return true;
  return Boolean(activeJob.workerId && claimedBy !== activeJob.workerId);
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
  if (!windowId) return null;
  const tabs = await chrome.tabs.query({ windowId });
  const tab = tabs.find((item) => item.active) || tabs[0];
  if (tab?.id) {
    const updated = await chrome.tabs.update(tab.id, { url: "https://www.amazon.com/cart?ref_=sw_gtc", active: true });
    await injectContentScriptWhenReady(updated?.id || tab.id);
    return updated?.id || tab.id;
  }
  return null;
}

async function navigateWindowToProduct(windowId, asin) {
  const normalizedAsin = String(asin || "").trim().toUpperCase();
  if (!windowId || !normalizedAsin) return;
  const url = `https://www.amazon.com/dp/${encodeURIComponent(normalizedAsin)}`;
  const tabs = await chrome.tabs.query({ windowId });
  const tab = tabs.find((item) => item.active) || tabs[0];
  if (tab?.id) {
    const updated = await chrome.tabs.update(tab.id, { url, active: true });
    await injectContentScriptWhenReady(updated?.id || tab.id);
  } else {
    const created = await chrome.tabs.create({ windowId, url, active: true });
    await injectContentScriptWhenReady(created?.id);
  }
}

async function navigateWindowToOrderHistory(windowId) {
  const url = "https://www.amazon.com/gp/your-account/order-history?ref=ppx_pt2_dt_b_yo_link";
  if (!windowId) {
    const created = await chrome.windows.create({ url, type: "normal", focused: false });
    await injectActiveAmazonTabInWindow(created?.id);
    return created?.id || null;
  }
  const tabs = await chrome.tabs.query({ windowId });
  const tab = tabs.find((item) => item.active) || tabs[0];
  if (tab?.id) {
    const updated = await chrome.tabs.update(tab.id, { url, active: true });
    await injectContentScriptWhenReady(updated?.id || tab.id);
  } else {
    const created = await chrome.tabs.create({ windowId, url, active: true });
    await injectContentScriptWhenReady(created?.id);
  }
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
    // A fulfilment worker must never take focus away from the user's work.
    // Chrome can drive a non-focused window and its active worker tab normally.
    focused: false,
    ...(incognito ? { incognito: true } : {}),
  };
  try {
    return await chrome.windows.create(createData);
  } catch (error) {
    await log(`Could not open new ${incognito ? "incognito " : ""}Chrome window: ${error.message}`);
    if (incognito) throw error;
  }
  try {
    return await chrome.windows.create({
      url: "https://www.amazon.com/cart?ref_=sw_gtc",
      type: "normal",
      focused: false,
    });
  } catch (fallbackError) {
    await log(`Could not create a background Amazon worker window: ${fallbackError.message}`);
    return null;
  }
}

async function contentScriptLoaded(tabId) {
  if (!chrome.scripting?.executeScript || !tabId) return true;
  try {
    // A marker in the isolated world can survive an unpacked-extension reload
    // even though the old content script's runtime port/listeners are dead.
    // Require a live round trip to the current background worker; otherwise
    // the recovery path would mistake that stale marker for a usable worker
    // and leave a submitted Amazon order stuck on order history forever.
    const response = await chrome.tabs.sendMessage(tabId, {
      type: "NUTRICITY_CONTENT_PING",
      expectedBuild: EXPECTED_CONTENT_SCRIPT_BUILD,
    });
    return Boolean(response?.ok && response?.build === EXPECTED_CONTENT_SCRIPT_BUILD);
  } catch {
    return false;
  }
}

async function clearStaleContentScriptMarker(tabId) {
  if (!chrome.scripting?.executeScript || !tabId) return;
  await chrome.scripting.executeScript({
    target: { tabId },
    func: () => {
      if (typeof window.__nutricityContentCleanup === "function") {
        try {
          window.__nutricityContentCleanup();
        } catch (_) {
          // The previous extension context is already invalid. Continue with
          // a clean marker so the newly injected build can install listeners.
        }
      }
      window.__nutricityContentLoaded = false;
      window.__nutricityRunning = false;
      window.__nutricityRunningAt = 0;
    },
  }).catch(() => undefined);
}

async function injectContentScript(tabId) {
  if (!chrome.scripting?.executeScript || !tabId) return false;
  if (await contentScriptLoaded(tabId)) return false;
  try {
    await clearStaleContentScriptMarker(tabId);
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

async function injectContentScriptWhenReady(tabId, timeoutMs = 30000) {
  if (!tabId) return false;
  await waitForTabComplete(tabId, timeoutMs).catch(() => undefined);
  let loaded = await injectContentScript(tabId) || await contentScriptLoaded(tabId);
  if (!loaded) {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    loaded = await injectContentScript(tabId) || await contentScriptLoaded(tabId);
  }
  if (loaded) {
    // A newly claimed worker tab is normally backgrounded. The content script
    // deliberately polls idle hidden tabs slowly, so explicitly wake it after
    // a queue handoff instead of leaving the next server lock idle.
    await chrome.tabs.sendMessage(tabId, { type: "RUN_ACTIVE_JOB" }).catch(() => undefined);
  }
  return loaded;
}

async function injectActiveAmazonTabInWindow(windowId) {
  if (!windowId || !chrome.tabs?.query) return false;
  const tabs = await chrome.tabs.query({
    windowId,
    url: ["https://www.amazon.com/*", "https://amazon.com/*", "https://*.amazon.com/*"],
  });
  const { activeJob } = await getWindowState(windowId);
  const preferredTabId = Number(activeJob?.targetTabId || 0) || null;
  const tab = tabs.find((item) => item.id === preferredTabId) || tabs.find((item) => item.active) || tabs[0];
  if (!tab?.id) {
    await diagnosticLog("Could not find an Amazon tab in the active worker window.", {
      windowId,
      level: "warn",
      details: { window_id: windowId },
    });
    return false;
  }
  if (activeJob?.job?.group_key && activeJob.targetTabId !== tab.id) {
    activeJob.targetTabId = tab.id;
    await setWindowJob(windowId, activeJob, { reason: "bind_designated_worker_tab" });
  }
  for (const otherTab of tabs) {
    if (!otherTab?.id || otherTab.id === tab.id) continue;
    await injectContentScript(otherTab.id).catch(() => false);
    await chrome.tabs.sendMessage(otherTab.id, { type: "NUTRICITY_DISABLE_NON_WORKER" }).catch(() => undefined);
  }
  const injected = await injectContentScriptWhenReady(tab.id);
  if (!injected) {
    await diagnosticLog("Could not verify Nutricity content script in the Amazon worker tab.", {
      windowId,
      level: "warn",
      page: { url: tab.url || "", title: tab.title || "", tabId: tab.id, windowId },
      details: { tab_id: tab.id, status: tab.status || "" },
    });
  }
  return injected;
}

async function amazonTabsInWindow(windowId) {
  if (!windowId || !chrome.tabs?.query) return [];
  try {
    return await chrome.tabs.query({
      windowId,
      url: ["https://www.amazon.com/*", "https://amazon.com/*", "https://*.amazon.com/*"],
    });
  } catch {
    return [];
  }
}

// ACCOUNT-TYPE CLAIM INVARIANT: profile names are irrelevant. Every extension
// must positively detect its live Amazon experience before claiming. The API
// then atomically assigns only a compatible whole Odoo order to the first idle
// consumer or Business worker that reaches the queue.
async function detectAmazonAccountExperience(windowId = null, createIfMissing = false) {
  let tabs = windowId ? await amazonTabsInWindow(windowId) : [];
  if (!tabs.length && chrome.tabs?.query) {
    tabs = await chrome.tabs.query({
      url: ["https://www.amazon.com/*", "https://amazon.com/*", "https://*.amazon.com/*"],
    }).catch(() => []);
  }
  if (!tabs.length && createIfMissing) {
    const created = await createAmazonWorkerWindow(false);
    const tab = created?.tabs?.[0];
    if (tab?.id) tabs = [tab];
  }
  for (const tab of tabs) {
    if (!tab?.id) continue;
    await injectContentScriptWhenReady(tab.id).catch(() => false);
    const detected = await chrome.tabs.sendMessage(tab.id, { type: "GET_AMAZON_ACCOUNT_EXPERIENCE" }).catch(() => null);
    if (["consumer", "business"].includes(detected?.experience)) {
      return { experience: detected.experience, windowId: tab.windowId || windowId, tabId: tab.id };
    }
  }
  return { experience: "unknown", windowId, tabId: null };
}

async function ensureContentScriptsInAmazonTabs(label = "extension recovery", options = {}) {
  if (!chrome.tabs?.query) return;
  const tabs = await chrome.tabs.query({
    url: ["https://www.amazon.com/*", "https://amazon.com/*", "https://*.amazon.com/*"],
    ...(options.activeOnly ? { active: true } : {}),
  });
  let injected = 0;
  for (const tab of tabs) {
    if (await injectContentScript(tab.id)) injected += 1;
  }
  if (injected) await log(`Recovered Nutricity content script in ${injected} Amazon tab(s) after ${label}.`);
}

async function ensureStoredActiveJobContentScripts(label = "active job recovery") {
  const state = await chrome.storage.local.get({ activeJob: null, activeJobsByWindow: {} });
  const windowIds = new Set();
  for (const activeJob of [state.activeJob, ...Object.values(state.activeJobsByWindow || {})]) {
    const windowId = Number(activeJob?.targetWindowId || 0) || null;
    if (activeJob?.job?.group_key && windowId) windowIds.add(windowId);
  }
  let injected = 0;
  for (const windowId of windowIds) {
    if (await injectActiveAmazonTabInWindow(windowId)) injected += 1;
  }
  if (injected) await log(`Recovered Nutricity content script in ${injected} active job window(s) after ${label}.`);
}

async function startNextJob(sourceWindowId = null) {
  const options = arguments[1] || {};
  if (!await autoOrderingIsRunning()) {
    return { ok: false, auto_ordering_stopped: true, message: "Auto ordering is stopped. Tick the confirmation and click Start Auto Ordering." };
  }
  if (startNextJobInFlight) return startNextJobInFlight;
  startNextJobInFlight = (async () => {
  sourceWindowId = await existingChromeWindowId(sourceWindowId);
  if (options.automatic === true) {
    if (await forceStopActive()) return { ok: false, stopped: true, message: "Force stop is active." };
  } else {
    await setForceStop(false);
  }
  await releaseMissingWindowJobs({ force: true });
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
  const { browserlessOrderMode } = await getSettings();
  if (browserlessOrderMode === true) {
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
  }
  const detectedAccount = await detectAmazonAccountExperience(sourceWindowId, true);
  if (!["consumer", "business"].includes(detectedAccount.experience)) {
    const message = "Amazon account type could not be positively detected, so no queued order was claimed.";
    await log(message, sourceWindowId);
    return { ok: false, message };
  }
  sourceWindowId = detectedAccount.windowId || sourceWindowId;
  let queueBefore = null;
  const { cachedQueueStatus } = await getSettings();
  if (
    cachedQueueStatus?.ok &&
    Array.isArray(cachedQueueStatus.jobs) &&
    Date.now() - Number(cachedQueueStatus.cached_at || 0) < 15000
  ) {
    queueBefore = cachedQueueStatus;
    await startOrderProgress(Number(queueBefore.job_count || queueBefore.jobs?.length || 0), "Visible Chrome order run started.");
  }
  try {
    if (!queueBefore) {
      queueBefore = await api("/api/chrome/jobs?claim=false&job_limit=12");
      await startOrderProgress(Number(queueBefore.job_count || queueBefore.jobs?.length || 0), "Visible Chrome order run started.");
    }
  } catch (error) {
    const message = connectionErrorMessage(error);
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
  if (await forceStopActive()) return { ok: false, stopped: true, message: "Force stop is active; did not claim a queued order." };
  const payload = await api(`/api/chrome/jobs?worker_id=${encodeURIComponent(workerId)}&claim=true&resume_existing=true&split_mixed_asin=false&account_experience=${encodeURIComponent(detectedAccount.experience)}`);
  const job = payload.jobs?.[0];
  if (!job) {
    await updateOrderProgressTotal(0, "No queued Chrome jobs found.");
    const message = `No queued whole orders compatible with this ${detectedAccount.experience} Amazon account.`;
    await log(message);
    return { ok: false, message };
  }
  if (jobWasSubmittedToAmazon(job)) {
    await log(`Recovered submitted ${job.group_key}; looking up Amazon order ID instead of opening a new cart.`, sourceWindowId);
    return recoverSubmittedJobInWindow(sourceWindowId);
  }
  const incognito = await windowIsIncognito(sourceWindowId);
  let createdWindow;
  let targetWindowId = null;
  let targetTabId = null;
  const reusableAmazonTabs = sourceWindowId ? await amazonTabsInWindow(sourceWindowId) : [];
  try {
    if (reusableAmazonTabs.length) {
      targetWindowId = sourceWindowId;
      targetTabId = await navigateWindowToCart(targetWindowId);
      createdWindow = await chrome.windows.get(targetWindowId).catch(() => null);
      await log(`Reusing Amazon worker window ${targetWindowId} for ${job.group_key}.`, targetWindowId);
    } else {
      createdWindow = await createAmazonWorkerWindow(incognito);
      targetWindowId = createdWindow?.id || null;
      targetTabId = createdWindow?.tabs?.[0]?.id || null;
    }
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
  if (!targetWindowId) {
    throw new Error("Could not open Amazon cart window for the queued job.");
  }
  const activeJob = activeJobFor(job, workerId, targetWindowId);
  activeJob.incognito = incognito;
  activeJob.targetTabId = targetTabId;
  await setWindowJob(targetWindowId, activeJob);
  const injected = await injectActiveAmazonTabInWindow(targetWindowId);
  if (!injected) {
    await log(`Started ${job.group_key}, but could not verify the content script in the Amazon worker tab. Reload the Amazon tab if fulfilment does not continue.`, targetWindowId);
  }
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
  const result = await api("/api/chrome/browserless/run", {
    method: "POST",
    body: JSON.stringify({
      worker_id: browserlessWorkerId,
      ordering_engine: "chrome_browserless",
      split_mixed_asin: false,
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

async function openBrowserlessSession() {
  const result = await api("/api/chrome/browserless/open-session", {
    method: "POST",
    body: JSON.stringify({}),
    timeoutMs: 10000,
  });
  await log(result.message || "Opened shared headless Chrome session.");
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
  if (!await autoOrderingIsRunning()) return null;
  windowId = await existingChromeWindowId(windowId);
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
    await navigateWindowToCart(windowId);
    await injectActiveAmazonTabInWindow(windowId);
    await log(`Kept ${currentJob.job.group_key} active until its failed-order cart cleanup finishes.`, windowId);
    return currentJob;
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
  const detectedAccount = await detectAmazonAccountExperience(windowId, false);
  if (!["consumer", "business"].includes(detectedAccount.experience)) {
    await log("Amazon account type could not be positively detected; did not claim another order.", windowId);
    return null;
  }
  if (await forceStopActive()) return null;
  const payload = await api(`/api/chrome/jobs?worker_id=${encodeURIComponent(workerId)}&claim=true&resume_existing=true&split_mixed_asin=false&account_experience=${encodeURIComponent(detectedAccount.experience)}`);
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
  if (!windowId) {
    const createdWindow = await createAmazonWorkerWindow(false);
    windowId = Number(createdWindow?.id || 0) || null;
    if (!windowId) {
      try {
        await api(`/api/chrome/jobs/${encodeURIComponent(job.group_key)}/release`, {
          method: "POST",
          body: JSON.stringify({ worker_id: workerId }),
        });
      } catch (releaseError) {
        await log(`Could not release ${job.group_key} after replacement worker creation failed: ${releaseError.message}`);
      }
      throw new Error("Could not create a replacement Amazon worker window for the next queued order.");
    }
  }
  await log(`Started next ${job.group_key} with ${job.items.length} item(s); clearing Amazon cart before product add.`, windowId);
  // Do not publish the new job while the previous Amazon order-history page is
  // still alive. Its safety guard would correctly pause an unsubmitted job on
  // history, racing the navigation below and leaving the new server claim
  // stranded. Navigate first, then expose and explicitly wake the new job.
  const targetTabId = await navigateWindowToCart(windowId);
  const activeJob = activeJobFor(job, workerId, windowId);
  activeJob.targetTabId = targetTabId;
  activeJob.startedAfterPreviousJob = true;
  await setWindowJob(windowId, activeJob);
  await injectActiveAmazonTabInWindow(windowId);
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
  windowId = await existingChromeWindowId(windowId);
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
  if (result.recovered && result.group_key) {
    await clearStoredJobGroup(result.group_key);
    lastEmptyRecoverSubmittedJobAt = Date.now();
    await log(
      `Recovered submitted ${result.group_key} as Amazon ${result.amazon_order_id || "order"} from exact order-history evidence.`,
      windowId,
    );
    return {
      ok: true,
      recovered: true,
      serverRecovered: true,
      activeJob: null,
      amazonOrderId: result.amazon_order_id || "",
    };
  }
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
          const verified = await clearJobIfBackendCompleted(activeJob, windowId, activeJob.reportedOrderId || "", "Verified submitted recovery after lock loss for");
          if (verified.cleared) {
            return { ok: true, recovered: false, activeJob: verified.nextJob || null };
          }
          await log(`Kept submitted ${activeJob.job.group_key}; server recovery and heartbeat lost the lock, but app completion is not confirmed: ${message}`, windowId);
          return { ok: true, recovered: false, activeJob };
        }
        await log(`Kept submitted ${activeJob.job.group_key} active; could not confirm server lock state: ${message}`, windowId);
      }
      return { ok: true, recovered: false, activeJob };
    }
    return { ok: true, recovered: false, activeJob: null };
  }
  // The recovery endpoint intentionally keeps a submitted job available until
  // its Amazon order ID is saved. Do not rebuild that exact same local job on
  // every content-script heartbeat: doing so reopens order history, resets the
  // submission time and makes the history matcher loop forever.
  const { activeJob: existingJob } = await getWindowState(windowId);
  // The popup is a separate Chrome window.  Its periodic GET_STATE requests
  // used to look only in that popup's window slot, miss the already-running
  // Amazon history worker, and then recreate the same submitted job again.
  // Look across all persisted worker windows before opening/navigating any
  // history tab.  A submitted job must retain its original timestamp and
  // history page until that one worker reports a verified order ID.
  const storedState = await getSettings();
  const existingSubmittedJob = [
    existingJob,
    storedState.activeJob,
    ...Object.values(storedState.activeJobsByWindow || {}),
  ].find((candidate, index, candidates) => (
    candidate?.job?.group_key === job.group_key
    && orderSubmitStarted(candidate)
    && candidates.findIndex((other) => other === candidate) === index
  ));
  if (
    existingSubmittedJob?.job?.group_key === job.group_key
    && orderSubmitStarted(existingSubmittedJob)
  ) {
    return {
      ok: true,
      recovered: false,
      activeJob: existingSubmittedJob,
      targetWindowId: existingSubmittedJob.targetWindowId || windowId,
    };
  }
  const targetWindowId = await navigateWindowToOrderHistory(windowId);
  const activeJob = {
    ...activeJobFor(job, workerId, targetWindowId),
    stage: "find_order_id",
    cartCleared: true,
    amazonSubmittedAt: Date.now(),
    lastHeartbeatAt: Date.now(),
  };
  const historyTabs = await amazonTabsInWindow(targetWindowId);
  activeJob.targetTabId = historyTabs.find((tab) => tab.active)?.id || historyTabs[0]?.id || null;
  await setWindowJob(targetWindowId, activeJob);
  await injectActiveAmazonTabInWindow(targetWindowId);
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
  // The terminal API call has already moved this group to error/missing and
  // released its server lock. Permit only this explicit cleanup transition;
  // the general submitted-order regression guard remains in force so stale
  // tabs still cannot overwrite or abandon a genuinely submitted order.
  await setWindowJob(windowId, cleanupJob, {
    allowSubmittedCleanup: true,
    reason: "terminal_failure_cart_cleanup",
  });
  await log(`Cleaning cart after ${activeJob.job.group_key} before starting the next order.`, windowId);
  await navigateWindowToCart(windowId);
}

async function stopJob(windowId, options = {}) {
  const reason = options.reason || "Stopped manually from the popup.";
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
    await recordLastProcessed(activeJob, "stopped", reason);
  }
  await setWindowJob(targetWindowId, null);
  await releaseMissingWindowJobs();
  await log(`Stopped active job: ${reason}`, targetWindowId);
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
    (async () => {
      await api(`/api/chrome/jobs/${encodeURIComponent(activeJob.job.group_key)}/force-release`, {
        method: "POST",
        timeoutMs: 8000,
      });
    })().catch(async (error) => {
      await log(`Force stop could not release ${activeJob.job.group_key}: ${error.message}`, windowId);
    });
  }

  await chrome.storage.local.set({
    activeJob: null,
    activeJobsByWindow: {},
    availabilityCheckInFlight: false,
    orderProgress: {
      running: false,
      total: Number(state.orderProgress?.total || 0),
      processed: Number(state.orderProgress?.processed || 0),
      message: "Force stopped. Order queue was not cleared; no further extension progress will be made until Start is clicked.",
      startedAt: state.orderProgress?.startedAt || Date.now(),
      updatedAt: Date.now(),
    },
  });

  try {
    const browserless = await browserlessOrderStatus().catch(() => null);
    if (browserless?.progress?.running === true) {
      stopBrowserlessOrderRun(windowId).catch((error) => log(`Force stop could not stop browserless ordering: ${error.message}`, windowId));
    }
  } catch (error) {
    await log(`Force stop could not inspect browserless ordering: ${error.message}`, windowId);
  }

  const windowsToClose = new Set();
  for (const { windowId: activeWindowId } of activeEntries) {
    if (activeWindowId) windowsToClose.add(Number(activeWindowId));
  }
  for (const controlWindowId of Object.keys(state.controlWindowsById || {})) {
    if (Number(controlWindowId)) windowsToClose.add(Number(controlWindowId));
  }
  if (windowId) windowsToClose.add(Number(windowId));
  for (const activeWindowId of windowsToClose) {
    try {
      await chrome.windows.remove(activeWindowId);
    } catch {
      // The window may already be gone; force stop still cleared extension state.
    }
  }

  await chrome.storage.local.set({
    activeJob: null,
    activeJobsByWindow: {},
    controlWindowsById: {},
  });

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
  activeJob.job = {
    ...activeJob.job,
    amazon_status: "",
    submitted_to_amazon: false,
    items: (activeJob.job.items || []).map((item) => ({
      ...item,
      amazon_status: "",
      submitted_to_amazon: false,
    })),
  };
  activeJob.duplicateOrder = null;
  activeJob.paused = false;
  activeJob.pausedStage = null;
  activeJob.stage = "product";
  activeJob.itemIndex = 0;
  activeJob.cartCleared = false;
  activeJob.resetUnplacedSubmit = true;
  activeJob.resetRevision = Date.now();
  for (const key of [
    "amazonSubmittedAt",
    "placeOrderClickStartedAt",
    "orderHistoryLookupStartedAt",
    "amazonConfirmationUrl",
    "reportedOrderId",
    "reportAttemptedAt",
    "lastError",
  ]) delete activeJob[key];
  await setWindowJob(windowId, activeJob, { allowSubmittedReset: true, reason: "reset_unplaced_submit" });
  const targetTabId = await navigateWindowToCart(windowId);
  activeJob.targetTabId = targetTabId;
  delete activeJob.resetUnplacedSubmit;
  await setWindowJob(windowId, activeJob, { reason: "restart_reset_unplaced_submit" });
  await injectActiveAmazonTabInWindow(windowId);
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

function activeJobHasReportedOrderId(activeJob) {
  return /^\d{3}-\d{7}-\d{7}(?:\s*,\s*\d{3}-\d{7}-\d{7})*$/.test(
    String(activeJob?.reportedOrderId || "").trim(),
  );
}

function isAmazonThankYouUrl(url = "") {
  return /^https:\/\/(?:www\.)?amazon\.com\/gp\/buy\/thankyou\/handlers\/display\.html/i.test(String(url || ""));
}

function amazonOrderIdFromUrl(url = "") {
  try {
    const parsed = new URL(String(url || ""));
    for (const key of ["orderID", "orderId"]) {
      const value = String(parsed.searchParams.get(key) || "").trim();
      if (/^\d{3}-\d{7}-\d{7}$/.test(value)) return value;
    }
  } catch {
    // A malformed or partial URL cannot provide a trusted Amazon order ID.
  }
  return "";
}

async function transferSubmittedJobToThankYouWindow(tab = {}) {
  const windowId = Number(tab.windowId || 0) || null;
  if (!windowId || !isAmazonThankYouUrl(tab.url || "")) return null;
  const state = await getSettings();
  const current = state.activeJobsByWindow?.[String(windowId)] || null;
  const submittedJobs = [
    ...Object.values(state.activeJobsByWindow || {}),
    state.activeJob,
  ].filter((job, index, jobs) => (
    job?.job?.group_key
    && orderSubmitStarted(job)
    && jobs.findIndex((candidate) => candidate?.job?.group_key === job.job.group_key) === index
  ));
  const source = current && orderSubmitStarted(current)
    ? current
    : submittedJobs.length === 1
      ? submittedJobs[0]
      : null;
  if (!source?.job?.group_key) return null;

  const orderId = amazonOrderIdFromUrl(tab.url || "");
  const transferred = {
    ...source,
    targetWindowId: windowId,
    targetTabId: tab.id || source.targetTabId || null,
    stage: orderId ? "complete_pending" : "find_order_id",
    paused: false,
    pausedStage: null,
    amazonSubmittedAt: source.amazonSubmittedAt || Date.now(),
    amazonConfirmationUrl: tab.url || source.amazonConfirmationUrl || "",
    confirmationOrderId: orderId || source.confirmationOrderId || "",
  };
  await setWindowJob(windowId, transferred);
  await diagnosticLog("Attached submitted job to Amazon thank-you window.", {
    windowId,
    activeJob: transferred,
    source: "background",
    level: "warn",
    page: { url: tab.url || "", title: tab.title || "", tabId: tab.id || null, windowId },
    details: {
      group_key: transferred.job.group_key,
      confirmation_order_id: orderId,
      previous_window_id: source.targetWindowId || null,
    },
  });
  return transferred;
}

async function recoverBlankThankYouTab(tab = {}) {
  const windowId = Number(tab.windowId || 0) || null;
  const { activeJob } = await getWindowState(windowId);
  if (!activeJob?.job?.group_key || activeJob.stage === "reporting_complete") return false;
  if (Date.now() - Number(activeJob.thankYouUrlRecoveredAt || 0) < 15000) return false;

  if (!orderSubmitStarted(activeJob)) {
    const protectedResult = await markAmazonSubmitted(
      windowId,
      activeJob.job.group_key,
      activeJob.workerId || "",
    );
    if (protectedResult?.stale_job || protectedResult?.stale_local_update) return false;
    if (!protectedResult?.ok && !protectedResult?.duplicate_submit_blocked) {
      await log(
        `Saw Amazon thank-you URL for ${activeJob.job.group_key}, but could not protect the submit before recovery: ${protectedResult?.message || "unknown error"}`,
        windowId,
      );
      return false;
    }
  }

  const latest = (await getWindowState(windowId)).activeJob || activeJob;
  if (
    latest?.job?.group_key !== activeJob.job.group_key
    || String(latest?.workerId || "") !== String(activeJob.workerId || "")
  ) {
    await log(`Thank-you recovery for ${activeJob.job.group_key} finished after the window moved to ${latest?.job?.group_key || "no active job"}; ignored the stale recovery.`, windowId);
    return false;
  }
  latest.stage = "find_order_id";
  latest.paused = false;
  latest.pausedStage = null;
  latest.amazonSubmittedAt = latest.amazonSubmittedAt || Date.now();
  latest.thankYouUrlRecoveredAt = Date.now();
  latest.amazonConfirmationUrl = latest.amazonConfirmationUrl || tab.url || "";
  latest.targetTabId = tab.id || latest.targetTabId || null;
  await setWindowJob(windowId, latest);
  await diagnosticLog("Background thank-you URL guard opened order history.", {
    windowId,
    activeJob: latest,
    source: "background",
    level: "warn",
    page: { url: tab.url || "", title: tab.title || "", tabId: tab.id || null, windowId },
    details: { group_key: latest.job.group_key, stage: latest.stage || "" },
  });
  await navigateWindowToOrderHistory(windowId);
  return true;
}

function submittedWindowForGroup(state, groupKey, windowId) {
  const expectedWindow = String(windowId || "");
  for (const [key, activeJob] of Object.entries(state.activeJobsByWindow || {})) {
    if (key === expectedWindow) continue;
    if (activeJob?.job?.group_key === groupKey && orderSubmitStarted(activeJob)) return key;
  }
  const globalJob = state.activeJob || null;
  if (
    globalJob?.job?.group_key === groupKey &&
    orderSubmitStarted(globalJob) &&
    String(globalJob.targetWindowId || "") !== expectedWindow
  ) {
    return String(globalJob.targetWindowId || "global");
  }
  return "";
}

async function markAmazonSubmitted(windowId, expectedGroupKey = "", expectedWorkerId = "") {
  const lockKey = `${windowId || "global"}:${expectedGroupKey || "unknown"}`;
  if (submitProtectionLocks.has(lockKey)) {
    await log(`Blocked concurrent Place Order protection for ${expectedGroupKey || "the active job"}.`, windowId);
    return {
      ok: false,
      duplicate_submit_blocked: true,
      protected_in_current_window: true,
      group_key: expectedGroupKey || "",
      message: `${expectedGroupKey || "This job"} already has a Place Order protection request in progress.`,
    };
  }
  submitProtectionLocks.add(lockKey);
  try {
    return await markAmazonSubmittedUnlocked(windowId, expectedGroupKey, expectedWorkerId);
  } finally {
    submitProtectionLocks.delete(lockKey);
  }
}

async function markAmazonSubmittedUnlocked(windowId, expectedGroupKey = "", expectedWorkerId = "") {
  const { activeJob } = await getWindowState(windowId);
  if (!activeJob?.job?.group_key || !activeJob?.workerId) {
    return { ok: false, message: "No active job to protect before Amazon submit." };
  }
  const groupKey = String(activeJob.job.group_key || "");
  const workerId = String(activeJob.workerId || "");
  if ((expectedGroupKey && expectedGroupKey !== groupKey) || (expectedWorkerId && expectedWorkerId !== workerId)) {
    await log(`Ignored stale submit marker for ${expectedGroupKey || "unknown job"}; current job is ${groupKey}.`, windowId);
    return { ok: false, stale_job: true, group_key: groupKey, message: `Submit marker belongs to ${expectedGroupKey || "another job"}, not ${groupKey}.` };
  }
  if (orderSubmitStarted(activeJob)) {
    await log(`Blocked repeat Place Order click for ${activeJob.job.group_key}; this Chrome window already protected the submit.`, windowId);
    return {
      ok: false,
      duplicate_submit_blocked: true,
      protected_in_current_window: true,
      group_key: activeJob.job.group_key,
      message: `${activeJob.job.group_key} is already protected for Amazon submit. Fulfilment paused before a repeat Place Order click.`,
    };
  }
  const state = await getSettings();
  const submittedWindow = submittedWindowForGroup(state, activeJob.job.group_key, windowId);
  if (submittedWindow) {
    await log(
      `Blocked duplicate Place Order click for ${activeJob.job.group_key}; window ${submittedWindow} already protected this Amazon submit.`,
      windowId,
    );
    return {
      ok: false,
      duplicate_submit_blocked: true,
      protected_in_current_window: false,
      group_key: activeJob.job.group_key,
      message: `${activeJob.job.group_key} is already protected in another Chrome window. Fulfilment paused before a duplicate Place Order click.`,
    };
  }
  const result = await api(`/api/chrome/jobs/${encodeURIComponent(groupKey)}/submitted`, {
    method: "POST",
    body: JSON.stringify({ worker_id: workerId }),
  });
  const latest = (await getWindowState(windowId)).activeJob;
  if (latest?.job?.group_key !== groupKey || String(latest?.workerId || "") !== workerId) {
    await log(`Submit protection completed for ${groupKey}, but the window has moved to ${latest?.job?.group_key || "no active job"}; ignored the stale local update.`, windowId);
    return { ...result, ok: false, stale_local_update: true, protected_original_job: true, group_key: groupKey };
  }
  latest.amazonSubmittedAt = latest.amazonSubmittedAt || Date.now();
  latest.stage = "complete_pending";
  latest.paused = false;
  latest.pausedStage = null;
  latest.lastHeartbeatAt = Date.now();
  await setWindowJob(windowId, latest);
  await log(`Protected ${latest.job.group_key} after Amazon submit started.`, windowId);
  return { ...result, group_key: groupKey };
}

async function releaseMissingWindowJobs(options = {}) {
  if (releaseMissingWindowJobsInFlight) return releaseMissingWindowJobsInFlight;
  if (!options.force && Date.now() - lastReleaseMissingWindowJobsAt < 15000) return;
  releaseMissingWindowJobsInFlight = (async () => {
    lastReleaseMissingWindowJobsAt = Date.now();
    const state = await getSettings();
    const activeJobsByWindow = { ...(state.activeJobsByWindow || {}) };
    const windows = await chrome.windows.getAll({ windowTypes: ["normal", "popup"] });
    const openWindowIds = new Set(windows.map((item) => String(item.id)));
    let changed = false;
    for (const [windowId, activeJob] of Object.entries(activeJobsByWindow)) {
      const numericWindowId = Number(windowId) || null;
      let reason = "";
      if (!openWindowIds.has(windowId)) {
        reason = "because its Chrome window is closed";
      } else if (!orderSubmitStarted(activeJob) && !(await amazonTabsInWindow(numericWindowId)).length) {
        reason = "because its Chrome worker window no longer has an Amazon tab";
      }
      if (!reason) continue;
      const released = await releaseStoredJob(activeJob, numericWindowId, reason);
      if (released) {
        delete activeJobsByWindow[windowId];
        changed = true;
      }
    }
    let activeJob = state.activeJob || null;
    if (activeJob?.job?.group_key && activeJob?.targetWindowId) {
      const windowId = String(activeJob.targetWindowId);
      const numericWindowId = Number(activeJob.targetWindowId) || null;
      let reason = "";
      if (!openWindowIds.has(windowId)) {
        reason = "because its Chrome window is closed";
      } else if (!orderSubmitStarted(activeJob) && !(await amazonTabsInWindow(numericWindowId)).length) {
        reason = "because its Chrome worker window no longer has an Amazon tab";
      }
      if (reason && await releaseStoredJob(activeJob, numericWindowId, reason)) {
        activeJob = null;
        changed = true;
      }
    }
    if (changed) {
      await chrome.storage.local.set({ activeJobsByWindow, activeJob: activeJob || Object.values(activeJobsByWindow)[0] || null });
    }
  })();
  try {
    await releaseMissingWindowJobsInFlight;
  } finally {
    releaseMissingWindowJobsInFlight = null;
  }
}

async function runFulfilmentWatchdog(label = "scheduled watchdog") {
  if (await forceStopActive()) return { ok: true, stopped: true };
  if (!await autoOrderingIsRunning()) return { ok: true, stopped: true, auto_ordering_stopped: true };

  await releaseMissingWindowJobs({ force: true });
  await ensureStoredActiveJobContentScripts(label);

  const state = await getSettings();
  const progress = state.orderProgress || {};
  const visibleRunWasActive = progress.running === true && progress.source !== "browserless";
  if (!visibleRunWasActive || await activeOrderingInProgress()) {
    return { ok: true, restarted: false };
  }

  const browserless = await browserlessOrderStatus().catch(() => null);
  if (browserless?.progress?.running === true) return { ok: true, restarted: false, browserless: true };

  await log("Fulfilment watchdog found a running queue without an Amazon worker. Starting a replacement worker window.");
  const result = await startNextJob(null);
  if (result?.ok) {
    await setOrderProgress({
      ...progress,
      running: true,
      message: "Recovered a missing Amazon worker window and resumed the order run.",
      startedAt: progress.startedAt || Date.now(),
      updatedAt: Date.now(),
      source: "visible",
    });
  }
  return { ok: true, restarted: Boolean(result?.ok), result };
}

// Reloading an extension tears down its service worker and every injected
// content script, even though Amazon can remain on the same checkout/history
// page.  Keep the persisted job as the source of truth and explicitly put the
// script back into its worker tab before deciding that the queue has stopped.
// Submitted jobs get an additional server-side recovery attempt so that an
// Amazon order can still be reported after an unavoidable Chrome restart.
let runtimeRecoveryInFlight = null;
async function recoverAfterRuntimeRestart(label = "extension restart", releasePreviousSessionJobs = false) {
  if (runtimeRecoveryInFlight) return runtimeRecoveryInFlight;
  runtimeRecoveryInFlight = (async () => {
    if (await forceStopActive()) return { ok: true, stopped: true };

    if (releasePreviousSessionJobs) {
      // A full Chrome restart cannot safely continue a pre-submit checkout in
      // a closed window.  Release only those jobs; submitted jobs remain
      // protected by releaseStoredJob and are recovered below.
      await releaseAllStoredJobs("from the previous Chrome session");
    }

    await ensureStoredActiveJobContentScripts(label);
    const state = await getSettings();
    const activeJobs = [state.activeJob, ...Object.values(state.activeJobsByWindow || {})]
      .filter((job, index, jobs) => job?.job?.group_key && jobs.findIndex((candidate) => (
        candidate?.job?.group_key === job.job.group_key
      )) === index);
    const submittedJob = activeJobs.find(orderSubmitStarted) || null;

    if (submittedJob) {
      // Re-open history only when the server says that this submitted order is
      // still awaiting its Amazon order ID.  This prevents a restart from
      // silently abandoning the reporting step.
      await recoverSubmittedJobInWindow(submittedJob.targetWindowId || null)
        .catch((error) => log(`Submitted-job recovery after ${label} could not complete yet: ${error.message}`, submittedJob.targetWindowId || null));
    } else if (!activeJobs.length) {
      // Storage can be empty if the old service worker stopped at exactly the
      // same time as Amazon redirected to its thank-you page.  The backend is
      // authoritative for submitted locks, so ask it before starting another
      // queued order and risking a duplicate checkout.
      await recoverSubmittedJobInWindow(null)
        .catch((error) => log(`Server recovery after ${label} could not complete yet: ${error.message}`));
    }

    return runFulfilmentWatchdog(label);
  })();
  try {
    return await runtimeRecoveryInFlight;
  } finally {
    runtimeRecoveryInFlight = null;
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
  const items = Array.isArray(activeJob.job.items) ? activeJob.job.items : [];
  await api(`/api/chrome/jobs/${encodeURIComponent(activeJob.job.group_key)}/heartbeat`, {
    method: "POST",
    body: JSON.stringify({
      worker_id: activeJob.workerId,
      stage: activeJob.stage || "",
      item_index: Number(activeJob.itemIndex || 0),
      item_count: items.length,
      asins: items.map((item) => item?.asin || "").filter(Boolean),
      target_window_id: windowId || activeJob.targetWindowId || null,
      extension_build: EXPECTED_CONTENT_SCRIPT_BUILD,
      paused: activeJob.paused === true,
      paused_stage: activeJob.pausedStage || "",
      last_error: activeJob.lastError || activeJob.pauseReason || "",
    }),
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
    activeJob.pausedByUser = true;
  } else if (activeJob.pausedStage) {
    activeJob.stage = activeJob.pausedStage;
    activeJob.pausedStage = null;
  }
  if (!nextPaused) activeJob.pausedByUser = false;
  activeJob.paused = nextPaused;
  activeJob.pauseRevision = Date.now();
  await setWindowJob(windowId, activeJob);
  if (!nextPaused) {
    // Resume must always wake the active Amazon tab through the current build.
    // This also replaces a stale content script after an unpacked-extension
    // reload and avoids waiting for a background-tab polling interval.
    await injectActiveAmazonTabInWindow(windowId);
  }
  await log(`${activeJob.paused ? "Paused" : "Resumed"} ${activeJob.job.group_key}.`, windowId);
  return { ok: true, paused: activeJob.paused, stage: activeJob.stage || "", message: activeJob.paused ? "Paused fulfilment." : `Resumed ${activeJob.stage || "fulfilment"}.` };
}

async function completeJob(orderId, orderUrl, amazonAccountName, windowId, orderMappings = [], orderDate = "", page = null, amazonRecipient = "", amazonAsins = [], expectedGroupKey = "", expectedWorkerId = "") {
  if (await forceStopActive()) {
    return { ok: false, stopped: true, message: "Force stop is active; ignored late order completion report." };
  }
  const { activeJob } = await getWindowState(windowId);
  if (!activeJob?.job) return { ok: false, message: "No active job." };
  const groupKey = activeJob.job.group_key;
  if (
    (expectedGroupKey && expectedGroupKey !== groupKey)
    || (expectedWorkerId && expectedWorkerId !== String(activeJob.workerId || ""))
  ) {
    await log(`Ignored stale completion report for ${expectedGroupKey || "unknown job"}; current job is ${groupKey}.`, windowId);
    return { ok: false, stale_job: true, message: `Completion belongs to ${expectedGroupKey || "another job"}, not ${groupKey}.` };
  }
  const normalizedOrderId = String(orderId || "").trim();
  if (!/^\d{3}-\d{7}-\d{7}$/.test(normalizedOrderId)) {
    activeJob.stage = "find_order_id";
    activeJob.paused = false;
    activeJob.pausedStage = null;
    activeJob.reportedOrderId = "";
    activeJob.reportAttemptedAt = null;
    activeJob.reportError = "Blocked completion because Amazon did not provide a valid order ID.";
    await setWindowJob(windowId, activeJob);
    await diagnosticLog("Blocked completion report without a valid Amazon order ID.", {
      windowId,
      source: "background",
      activeJob,
      page,
      level: "error",
      details: { supplied_order_id: orderId || "", group_key: groupKey },
    });
    await navigateWindowToOrderHistory(windowId);
    return {
      ok: false,
      invalid_order_id: true,
      message: `Amazon has not exposed a valid order number for ${groupKey}; continuing safe order-history verification.`,
    };
  }
  const lockKey = `${windowId || "global"}:${groupKey}`;
  await diagnosticLog(`Completion report received for ${groupKey}.`, {
    windowId,
    source: "content",
    activeJob,
    page,
    details: {
      amazon_order_id: orderId || "",
      amazon_order_url: orderUrl || "",
      amazon_account_name: amazonAccountName || "",
      order_date: orderDate || "",
      amazon_recipient: String(amazonRecipient || "").replace(/\s+/g, " ").trim(),
      amazon_asins: (amazonAsins || []).map((asin) => String(asin || "").trim().toUpperCase()).filter(Boolean),
      order_mappings: orderMappings || [],
    },
  });
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
      amazon_recipient: String(amazonRecipient || "").replace(/\s+/g, " ").trim(),
      amazon_asins: (amazonAsins || []).map((asin) => String(asin || "").trim().toUpperCase()).filter(Boolean),
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
        const verified = await clearJobIfBackendCompleted(activeJob, windowId, orderId, "Verified late completion after released lock for");
        if (verified.cleared) {
          await recordLastProcessed(activeJob, "placed", `Placed ${orderId}.`, { amazon_order_id: orderId });
          return {
            ok: true,
            already_reported: true,
            amazon_order_id: orderId,
            message: `Order ${orderId} was already recorded in the app; continuing queue.`,
            next_job_started: Boolean(verified.nextJob),
            next_group_key: verified.nextJob?.job?.group_key || "",
          };
        }
        const message = `Chrome found Amazon order ${orderId}, but the app did not confirm it was saved for ${groupKey}. Retry reporting before continuing.`;
        activeJob.paused = true;
        activeJob.pausedStage = "reporting_complete";
        activeJob.reportError = message;
        await setWindowJob(windowId, activeJob);
        await diagnosticLog("Late completion was not cleared because the app did not confirm saved order lines.", {
          windowId,
          source: "background",
          activeJob,
          details: { amazon_order_id: orderId, group_key: groupKey, completion_status: verified.status || null },
        });
        return { ok: false, message, amazon_order_id: orderId, pending_completion: true };
      }
      throw error;
    }
    if (result?.odoo_chatter_queued !== true && result?.odoo_chatter_confirmed !== true) {
      const message = `Amazon order ${orderId} was saved, but the app did not confirm its durable Odoo chatter outbox entry. The queue remains held before starting another order.`;
      activeJob.paused = true;
      activeJob.pausedStage = "reporting_complete";
      activeJob.reportError = message;
      await setWindowJob(windowId, activeJob);
      await log(message, windowId, { amazon_order_id: orderId || "", order_url: orderUrl || "" });
      return {
        ok: false,
        pending_odoo_chatter_queue: true,
        amazon_order_id: result?.amazon_order_id || orderId || "",
        message,
      };
    }
    await log(`Completed ${groupKey} as ${result.amazon_order_id}.`, windowId, { amazon_order_id: result.amazon_order_id || orderId || "", order_url: orderUrl || "" });
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
  activeJob.stage = "find_order_id";
  activeJob.paused = true;
  activeJob.pausedStage = "find_order_id";
  activeJob.lastError = message;
  await setWindowJob(windowId, activeJob);
  try {
    await heartbeatJob(activeJob, windowId);
  } catch (error) {
    await log(`Submitted order ${activeJob.job.group_key} remains locally held after heartbeat failed: ${error.message}`, windowId);
  }
  await log(`Held submitted ${activeJob.job.group_key} for Amazon ID verification; the next queue job was not claimed: ${message}`, windowId);
  return {
    ok: false,
    held_for_verification: true,
    message,
    next_job_started: false,
    cleanup_required: false,
    next_group_key: "",
  };
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
  await navigateWindowToCart(windowId);
  const nextJob = await claimNextJobInWindow(windowId);
  return {
    ...result,
    next_job_started: Boolean(nextJob),
    next_group_key: nextJob?.job?.group_key || "",
    message: nextJob
      ? `${result.message || "Order moved to Costly."} Started next ${nextJob.job.group_key}.`
      : result.message || "Order moved to Costly. No more queued Chrome jobs found.",
  };
}

async function clearFailedJobs() {
  const result = await api("/api/chrome/failed-jobs/clear", { method: "POST", body: JSON.stringify({}) });
  await chrome.storage.local.set({ activeJob: null, cachedQueueStatus: null });
  await log(result.message || `Cleared ${result.cleared || 0} failed Chrome job line(s).`);
  return result;
}

async function forceClearQueue(windowId = null) {
  await setForceStop(true, "Force cleared Chrome queue from the popup.");
  await chrome.storage.local.set({ cachedQueueStatus: null });
  const result = await api("/api/chrome/queue/force-clear", { method: "POST", body: JSON.stringify({}) });
  try {
    const browserless = await browserlessOrderStatus().catch(() => null);
    if (browserless?.progress?.running === true) {
      stopBrowserlessOrderRun(windowId).catch((error) => log(`Force clear could not stop browserless ordering: ${error.message}`, windowId));
    }
  } catch (error) {
    await log(`Force clear could not inspect browserless ordering: ${error.message}`, windowId);
  }
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

async function setupFulfilmentWatchdogAlarm() {
  if (!chrome.alarms?.create) return;
  await chrome.alarms.create(FULFILMENT_WATCHDOG_ALARM, {
    delayInMinutes: FULFILMENT_WATCHDOG_PERIOD_MINUTES,
    periodInMinutes: FULFILMENT_WATCHDOG_PERIOD_MINUTES,
  });
}

async function setupAutoOrderAlarm() {
  if (!chrome.alarms?.create) return;
  if (!await autoOrderingIsRunning()) {
    await chrome.alarms.clear(AUTO_ORDER_ALARM).catch(() => false);
    return;
  }
  await chrome.alarms.create(AUTO_ORDER_ALARM, {
    delayInMinutes: 0.1,
    periodInMinutes: AUTO_ORDER_PERIOD_MINUTES,
  });
}

async function runAutoOrderQueue() {
  if (!await autoOrderingIsRunning() || await forceStopActive() || await activeOrderingInProgress()) return;
  const queue = await api("/api/chrome/jobs?claim=false&job_limit=1", { timeoutMs: 15000 }).catch(() => null);
  if (!queue?.job_count) return;
  await startNextJob(null, { automatic: true });
}

async function startAutoOrdering(windowId, confirmed = false) {
  if (confirmed !== true) {
    return { ok: false, confirmation_required: true, message: "Tick the confirmation before starting auto ordering." };
  }
  await setForceStop(false);
  await setAutoOrderingRunning(true);
  await setupAutoOrderAlarm();
  const { browserlessOrderMode } = await getSettings();
  const result = browserlessOrderMode === true
    ? await startBrowserlessOrderRun(windowId)
    : await startNextJob(windowId, { automatic: true });
  if (result?.ok === false && !result?.active_job_running) {
    return {
      ...result,
      ok: true,
      auto_ordering_running: true,
      message: `${result.message || "No compatible order is ready."} Auto ordering is running and will keep checking.`,
    };
  }
  return { ...result, ok: true, auto_ordering_running: true };
}

async function stopAutoOrdering(windowId) {
  await setAutoOrderingRunning(false);
  const result = await stopJob(windowId, { reason: "Auto ordering stopped manually from the popup." });
  return {
    ...result,
    ok: true,
    auto_ordering_running: false,
    message: result?.message === "Stopped active job."
      ? "Auto ordering stopped and the active pre-submit job was released safely."
      : "Auto ordering stopped. No new orders will be claimed until you confirm and start it again.",
  };
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
  const { deliveryLimitDays } = await getSettings();
  const amazonUrl = candidate.amazon_url || `https://www.amazon.com/dp/${encodeURIComponent(candidate.asin || "")}`;
  const tab = await chrome.tabs.create({ url: amazonUrl, active: false });
  try {
    await waitForTabComplete(tab.id, 30000);
    await injectContentScript(tab.id);
    await new Promise((resolve) => setTimeout(resolve, 2500));
    try {
      return await chrome.tabs.sendMessage(tab.id, {
        type: "CHECK_ASIN_AVAILABILITY",
        asin: candidate.asin,
        deliveryLimitDays,
      });
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

async function preflightSplitQueueHead(workerId, splitMixedAsinOrders, fulfilAvailableMixedAsin, sourceWindowId = null, initialSnapshot = null) {
  if (splitMixedAsinOrders !== true) return { ok: true };
  const blockedGroups = new Set();
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (await forceStopActive()) return { ok: false, stopped: true, message: "Force stop is active; split-order preflight stopped." };
    const snapshot = attempt === 0 && initialSnapshot
      ? initialSnapshot
      : await api(`/api/chrome/jobs?claim=false&job_limit=${fulfilAvailableMixedAsin === true ? 1 : 12}`, { timeoutMs: 15000 });
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
    const blockedKey = `${targetLabel}:${candidate.asin}`;
    if (blockedGroups.has(blockedKey)) {
      return {
        ok: false,
        message: `Stopped split-order preflight because ${targetLabel} kept returning after being moved to Missing ASINs. Please review ${candidate.asin}.`,
      };
    }
    blockedGroups.add(blockedKey);
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
  return { ok: false, message: "Stopped after 100 split-order preflight blocks. Please review the queue for repeated unavailable ASINs." };
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
  setAutoOrderingRunning(false).catch((error) => log(`Could not reset automatic ordering at Chrome startup: ${error.message}`));
  forceStopActive().then((stopped) => {
    if (stopped) return;
    recoverAfterRuntimeRestart("Chrome startup", true)
      .catch((error) => log(`Could not recover previous Chrome session jobs: ${error.message}`));
    setupAvailabilityAlarm().catch((error) => log(`Could not schedule missing ASIN availability checks: ${error.message}`));
    setupFulfilmentWatchdogAlarm().catch((error) => log(`Could not schedule fulfilment watchdog: ${error.message}`));
  });
});

chrome.runtime.onInstalled.addListener(() => {
  setAutoOrderingRunning(false).catch((error) => log(`Could not reset automatic ordering after extension install: ${error.message}`));
  forceStopActive().then((stopped) => {
    if (stopped) return;
    recoverAfterRuntimeRestart("extension reload").catch((error) => log(`Could not recover fulfilment after extension reload: ${error.message}`));
    setupAvailabilityAlarm().catch((error) => log(`Could not schedule missing ASIN availability checks: ${error.message}`));
    setupFulfilmentWatchdogAlarm().catch((error) => log(`Could not schedule fulfilment watchdog: ${error.message}`));
  });
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === AUTO_ORDER_ALARM) {
    runAutoOrderQueue().catch((error) => log(`Automatic order queue failed: ${error.message}`));
    return;
  }
  if (alarm.name === FULFILMENT_WATCHDOG_ALARM) {
    runFulfilmentWatchdog("background watchdog").catch((error) => log(`Fulfilment watchdog failed: ${error.message}`));
    return;
  }
  if (alarm.name !== MISSING_ASIN_ALARM) return;
  forceStopActive().then((stopped) => {
    if (stopped) return;
    runMissingAsinAvailabilityCheck(false).catch((error) => log(`Missing ASIN availability check failed: ${error.message}`));
  });
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  const url = changeInfo.url || tab?.url || "";
  if (!url || !/^https:\/\/(?:www\.)?amazon\.com\//i.test(url)) return;
  (async () => {
    if (isAmazonThankYouUrl(url)) {
      const transferred = await transferSubmittedJobToThankYouWindow({ ...tab, id: tabId, url });
      if (transferred) {
        if (changeInfo.status === "complete" || amazonOrderIdFromUrl(url)) {
          await injectContentScriptWhenReady(tabId);
        }
        return;
      }
      if (await recoverBlankThankYouTab({ ...tab, id: tabId, url })) return;
    }
    if (changeInfo.status !== "complete") return;
    const state = await getSettings();
    const activeJob = tab.windowId ? state.activeJobsByWindow?.[String(tab.windowId)] : null;
    if (!activeJob?.job?.group_key) return;
    if (activeJob.targetTabId && Number(activeJob.targetTabId) !== Number(tabId)) {
      await injectContentScript(tabId);
      await chrome.tabs.sendMessage(tabId, { type: "NUTRICITY_DISABLE_NON_WORKER" }).catch(() => undefined);
      return;
    }
    if (!activeJob.targetTabId) {
      activeJob.targetTabId = tabId;
      await setWindowJob(tab.windowId, activeJob, { reason: "bind_worker_tab_after_navigation" });
    }
    await injectContentScript(tabId);
  })().catch((error) => log(`Could not inject Nutricity content script after Amazon navigation: ${error.message}`, tab?.windowId || null));
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
      await stopJob(windowId, { reason: "Chrome worker window was closed before the job completed." });
    }
  })().catch((error) => log(`Could not release Chrome job after window closed: ${error.message}`));
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    const windowId = messageWindowId(message, sender);
    const senderTabId = Number(sender.tab?.id || 0) || null;
    const senderIsAmazon = /^https:\/\/(?:www\.)?amazon\.com\//i.test(String(sender.tab?.url || ""));
    const workerOnlyMessages = new Set([
      "GET_ACTIVE_JOB", "RECOVER_SUBMITTED_JOB", "SET_ACTIVE_JOB", "HEARTBEAT_JOB",
      "MARK_ORDER_SUBMITTED", "COMPLETE_JOB", "MARK_LINE_MISSING", "POST_SUBMIT_UNPLACED",
      "SUBMIT_UNCERTAIN", "FAIL_JOB", "COSTLY_JOB", "CHECK_EXISTING_AMAZON_ORDER",
      "CLAIM_NEXT_IN_WINDOW", "FINISH_CLEANUP_AND_CLAIM_NEXT",
    ]);
    if (senderIsAmazon && senderTabId && workerOnlyMessages.has(message.type)) {
      const { activeJob: designatedJob } = await getWindowState(windowId);
      if (designatedJob?.targetTabId && Number(designatedJob.targetTabId) !== senderTabId) {
        if (["GET_ACTIVE_JOB", "RECOVER_SUBMITTED_JOB"].includes(message.type)) {
          return { ok: true, activeJob: null, inactiveWorkerTab: true };
        }
        return { ok: false, ignored_non_worker_tab: true, message: "Ignored fulfilment message from a non-worker Amazon tab." };
      }
    }
    if (message.type === "FORCE_STOP_ALL") return forceStopAll(windowId);
    if (message.type === "FORCE_CLEAR_QUEUE") return forceClearQueue(windowId);
    if (message.type === "CLEAR_FORCE_STOP") {
      await setForceStop(false);
      return { ok: true };
    }
    if (message.type === "START_AUTO_ORDERING") return startAutoOrdering(windowId, message.confirmed === true);
    if (message.type === "STOP_AUTO_ORDERING") return stopAutoOrdering(windowId);
    if (message.type === "START_NEXT" || message.type === "START_BROWSERLESS") {
      return { ok: false, confirmation_required: true, message: "Use Start Auto Ordering with the confirmation ticked." };
    }
    if (message.type === "GET_BROWSERLESS_STATUS") return browserlessOrderStatus();
    if (message.type === "OPEN_BROWSERLESS_SESSION") return openBrowserlessSession();
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
      if (await forceStopActive()) return { ...(await getWindowState(windowId)), activeJob: null };
      const workerWindowId = await liveWorkerWindowId(windowId);
      await releaseMissingWindowJobs();
      // The popup polls for state while a job is active.  Use that heartbeat to
      // repair a content script that was removed by an extension reload while
      // Amazon stayed on the same checkout URL (so tabs.onUpdated never fires).
      // injectContentScript is build-idempotent, so this is safe on every poll.
      await ensureStoredActiveJobContentScripts("popup state refresh");
      await refreshActiveJobFromQueue(workerWindowId);
      const { activeJob } = await getWindowState(workerWindowId);
      if (!await forceStopActive() && (!activeJob?.job || !orderSubmitStarted(activeJob))) {
        const recovered = await recoverSubmittedJobInWindow(workerWindowId);
        if (recovered?.activeJob) return getWindowState(recovered.targetWindowId || workerWindowId);
      }
      return getWindowState(workerWindowId);
    }
    if (message.type === "GET_QUEUE_STATUS") {
      const forceStopped = await forceStopActive();
      if (!forceStopped) await releaseMissingWindowJobs();
      const queue = await getQueueStatus();
      return {
        ...queue,
        forceStopped,
        message: forceStopped
          ? "Force stop is active. Queue is visible, but no orders will start until Start next queued order clears Force stop."
          : queue.message,
      };
    }
    if (message.type === "REMEMBER_RECENT_AMAZON_ORDERS") return rememberRecentAmazonOrders(message.orders || []);
    if (message.type === "LOOKUP_AMAZON_HISTORY_ORDERS") return lookupAmazonHistoryOrders(message.orders || []);
    if (message.type === "LOOKUP_AMAZON_HISTORY_ODOO_DIRECT") return lookupAmazonHistoryOdooDirect(message.orders || []);
    if (message.type === "SYNC_AMAZON_HISTORY_ORDER") return syncAmazonHistoryOrder(message.order || {});
    if (message.type === "GET_RECENT_AMAZON_ORDERS") {
      const { recentAmazonOrders } = await getSettings();
      return { ok: true, orders: recentAmazonOrders || [] };
    }
    if (message.type === "CLAIM_NEXT_IN_WINDOW") {
      if (await forceStopActive()) return { ok: false, stopped: true, message: "Force stop is active; did not claim another queued order." };
      const nextJob = await claimNextJobInWindow(windowId);
      return {
        ok: true,
        next_job_started: Boolean(nextJob),
        next_group_key: nextJob?.job?.group_key || "",
        message: nextJob ? `Started next ${nextJob.job.group_key}.` : "No more queued Chrome jobs found.",
      };
    }
    if (message.type === "FINISH_CLEANUP_AND_CLAIM_NEXT") {
      if (await forceStopActive()) return { ok: false, stopped: true, message: "Force stop is active; cleanup/claim stopped." };
      return finishCleanupAndClaimNext(windowId);
    }
    if (message.type === "RECOVER_SUBMITTED_JOB") {
      if (await forceStopActive()) return { ok: true, recovered: false, activeJob: null, forceStopped: true };
      const recovered = await recoverSubmittedJobInWindow(windowId);
      if (senderIsAmazon && senderTabId && recovered?.activeJob?.targetTabId && Number(recovered.activeJob.targetTabId) !== senderTabId) {
        return { ok: true, recovered: false, activeJob: null, inactiveWorkerTab: true };
      }
      return recovered;
    }
    if (message.type === "RUN_MISSING_ASIN_AVAILABILITY_CHECK") {
      if (await forceStopActive()) return { ok: false, stopped: true, message: "Force stop is active; availability check stopped." };
      return runMissingAsinAvailabilityCheck(true);
    }
    if (message.type === "TEST_CONNECTION") return testConnection();
    if (message.type === "GET_ACTIVE_JOB") {
      if (await forceStopActive()) return { ok: true, activeJob: null, forceStopped: true };
      let { activeJob } = await getWindowState(windowId);
      if (!await autoOrderingIsRunning() && !orderSubmitStarted(activeJob)) {
        const recovered = await recoverSubmittedJobInWindow(windowId);
        return {
          ok: true,
          activeJob: recovered?.activeJob || null,
          autoOrderingRunning: false,
          autoOrderingStopped: true,
        };
      }
      if (activeJob?.job && senderIsAmazon && senderTabId && !activeJob.targetTabId) {
        activeJob.targetTabId = senderTabId;
        await setWindowJob(windowId, activeJob, { reason: "bind_worker_tab_from_active_job_request" });
      }
      if (!activeJob?.job && windowId) {
        const state = await getSettings();
        const globalJob = state.activeJob?.job?.group_key ? state.activeJob : null;
        const groupAlreadyAttached = globalJob?.job?.group_key && Object.values(state.activeJobsByWindow || {}).some((job) => (
          job?.job?.group_key === globalJob.job.group_key
        ));
        if (globalJob?.job && !groupAlreadyAttached) {
          globalJob.targetWindowId = windowId;
          globalJob.targetTabId = senderIsAmazon ? senderTabId : null;
          await setWindowJob(windowId, globalJob);
          activeJob = globalJob;
          await log(`Reattached ${globalJob.job.group_key} to Amazon window ${windowId}.`, windowId);
        }
      }
      if (!activeJob?.job || !orderSubmitStarted(activeJob)) {
        const recovered = await recoverSubmittedJobInWindow(windowId);
        if (recovered?.activeJob) {
          activeJob = recovered.activeJob;
          if (senderIsAmazon && senderTabId && activeJob.targetTabId && Number(activeJob.targetTabId) !== senderTabId) {
            return { ok: true, activeJob: null, inactiveWorkerTab: true };
          }
          return { ok: true, activeJob };
        }
      }
      if (activeJob?.job && activeJob.stage === "reporting_complete" && !activeJobHasReportedOrderId(activeJob)) {
        activeJob.stage = "find_order_id";
        activeJob.paused = false;
        activeJob.pausedStage = null;
        activeJob.reportAttemptedAt = null;
        activeJob.reportError = "Recovered an invalid completed-report state with no Amazon order ID.";
        await setWindowJob(windowId, activeJob);
        await log(`Recovered ${activeJob.job.group_key} from reporting_complete without an Amazon order ID; resuming order-history verification.`, windowId);
      }
      if (
        activeJob?.job &&
        (activeJob.stage === "reporting_complete" || activeJob.reportedOrderId) &&
        Date.now() - Number(activeJob.lastReportedLockCheckAt || 0) > 10000
      ) {
        activeJob.lastReportedLockCheckAt = Date.now();
        await setWindowJob(windowId, activeJob);
        try {
          await heartbeatJob(activeJob, windowId);
        } catch (error) {
          const message = String(error.message || "");
          if (/lock is no longer owned|no longer active|not found/i.test(message)) {
            const verified = await clearJobIfBackendCompleted(activeJob, windowId, activeJob.reportedOrderId || "", "Verified reported job after heartbeat loss for");
            if (verified.cleared) return { ok: true, activeJob: verified.nextJob || null };
            await log(
              `Kept reported active job ${activeJob.job.group_key}; heartbeat was gone but the app did not confirm saved order lines: ${message}`,
              windowId,
            );
            return { ok: true, activeJob: (await getWindowState(windowId)).activeJob };
          }
          await log(`Completed-job lock check failed for ${activeJob.job.group_key}: ${message}`, windowId);
        }
      }
      if (activeJob?.job && activeJob?.workerId && Date.now() - Number(activeJob.lastHeartbeatAt || 0) > ACTIVE_JOB_HEARTBEAT_MS) {
        try {
          await heartbeatJob(activeJob, windowId);
        } catch (error) {
          const message = String(error.message || "");
          if (/lock is no longer owned|no longer active/i.test(message)) {
            if (orderSubmitStarted(activeJob)) {
              const verified = await clearJobIfBackendCompleted(activeJob, windowId, activeJob.reportedOrderId || "", "Verified submitted job after heartbeat loss for");
              if (verified.cleared) return { ok: true, activeJob: verified.nextJob || null };
              await log(
                `Kept submitted active job ${activeJob.job.group_key}; heartbeat was gone but completion is not confirmed: ${message}`,
                windowId,
              );
              return { ok: true, activeJob: (await getWindowState(windowId)).activeJob };
            }
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
      return markAmazonSubmitted(windowId, String(message.groupKey || ""), String(message.workerId || ""));
    }
    if (message.type === "SET_ACTIVE_JOB") {
      if (await forceStopActive()) return { ok: false, stopped: true, message: "Force stop is active; ignored active job update." };
      const incomingJob = message.activeJob || null;
      const { activeJob: currentJob } = await getWindowState(windowId);
      if (isLateCompletedJobUpdate(currentJob, incomingJob)) {
        await log(`Ignored late completed-state update for ${incomingJob.job.group_key}.`, windowId);
        return { ok: true, ignored_late_completed_update: true };
      }
      if (incomingJob?.job?.group_key) {
        const previousStage = currentJob?.job?.group_key === incomingJob.job.group_key ? currentJob.stage || "" : "";
        const nextStage = incomingJob.stage || "";
        const previousPaused = Boolean(currentJob?.paused);
        const nextPaused = Boolean(incomingJob.paused);
        if (previousStage !== nextStage || previousPaused !== nextPaused || message.reason) {
          await diagnosticLog(
            `Active job update ${incomingJob.job.group_key}: ${previousStage || "none"}${previousPaused ? " paused" : ""} -> ${nextStage || "none"}${nextPaused ? " paused" : ""}.`,
            {
              windowId,
              source: "content",
              page: senderPageInfo(sender, message.page || {}),
              activeJob: incomingJob,
              details: {
                reason: message.reason || "",
                previous_stage: previousStage,
                next_stage: nextStage,
                previous_paused: previousPaused,
                next_paused: nextPaused,
              },
            },
          );
        }
      }
      await setWindowJob(windowId, incomingJob, {
        allowItemRemoval: message.reason === "partial_missing_line_removed",
        reason: message.reason || "",
      });
      return { ok: true };
    }
    if (message.type === "DIAG_LOG") {
      await diagnosticLog(message.message || "Diagnostic event.", {
        windowId,
        source: message.source || "content",
        level: message.level || "info",
        page: senderPageInfo(sender, message.page || {}),
        details: message.details || null,
      });
      return { ok: true };
    }
    if (message.type === "SET_API_BASE") {
      await chrome.storage.local.set({
        apiBase: normalizeApiBase(message.apiBase),
        adminToken: message.adminToken || "",
        cardLast4Preference: message.cardLast4Preference || "",
        editExistingAddress: message.editExistingAddress !== false,
        fulfilAvailableMixedAsin: message.fulfilAvailableMixedAsin === true,
        splitMixedAsinOrders: false,
        autoOrderQueue: false,
        browserlessOrderMode: message.browserlessOrderMode === true,
        pauseBeforePlaceOrder: message.pauseBeforePlaceOrder === true,
        preferRewardedLaterDelivery: message.preferRewardedLaterDelivery === true,
        deliveryLimitDays: Math.min(30, Math.max(1, Math.floor(Number(message.deliveryLimitDays) || 5))),
      });
      return { ok: true };
    }
    if (message.type === "COMPLETE_JOB") return completeJob(message.orderId, message.orderUrl, message.amazonAccountName || "", windowId, message.orderMappings || [], message.orderDate || "", senderPageInfo(sender, message.page || {}), message.amazonRecipient || "", message.amazonAsins || [], String(message.groupKey || ""), String(message.workerId || ""));
    if (message.type === "MARK_LINE_MISSING") return markLineMissing(message.message || "Chrome extension line is missing.", message, windowId);
    if (message.type === "POST_SUBMIT_UNPLACED") return postSubmitUnplaced(message.message || "Amazon did not place the order after submit.", message, windowId);
    if (message.type === "SUBMIT_UNCERTAIN") return submitUncertain(message.message || "Amazon Place Order was submitted, but no matching Amazon order ID was found.", message, windowId);
    if (message.type === "FAIL_JOB") return failJob(message.message || "Chrome extension job failed.", message, windowId);
    if (message.type === "COSTLY_JOB") return costlyJob(message.message || "Chrome extension job needs costly approval.", message, windowId);
    if (message.type === "CLEAR_FAILED_JOBS") return clearFailedJobs();
    if (message.type === "GET_DIAGNOSTIC_LOGS") {
      const { diagnosticSessions } = await getSettings();
      return { ok: true, diagnosticSessions: diagnosticSessions || { currentSessionId: "", sessions: [] } };
    }
    if (message.type === "CLEAR_DIAGNOSTIC_LOGS") {
      await chrome.storage.local.set({ diagnosticSessions: { currentSessionId: "", sessions: [] }, logs: [], logsByWindow: {} });
      return { ok: true, message: "Diagnostic logs cleared." };
    }
    return { ok: false, message: "Unknown message." };
  })()
    .then((result) => sendResponse(result))
    .catch((error) => sendResponse({ ok: false, message: connectionErrorMessage(error) }));
  return true;
});
