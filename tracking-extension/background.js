const DEFAULT_API_BASE = "http://127.0.0.1:8000";
const DEFAULT_REQUEST_TIMEOUT_MS = 45000;
const AUTO_TRACKING_ALARM = "amazonTrackingAuto";
const TRACKING_WATCHDOG_ALARM = "amazonTrackingWatchdog";
const DEFAULT_AUTO_TRACKING_HOURS = 3;
const TRACKING_STEP_TIMEOUT_MS = 90000;
const RECENT_TRACKING_CACHE_TTL_MS = 6 * 60 * 60 * 1000;
const ORDER_HISTORY_URL = "https://www.amazon.com/gp/css/order-history?ref_=abn_yadd_ad_your_orders";
const ORDER_HISTORY_PATH_RE = /\/(gp\/css\/order-history|gp\/your-account\/order-history|your-orders(?:\/orders?)?)(?:\/ref=[^/]+)?\/?$/i;
const AMAZON_ORDER_ID_RE = /\b\d{3}-\d{7}-\d{7}\b/g;

async function getState() {
  return chrome.storage.local.get({
    apiBase: DEFAULT_API_BASE,
    adminToken: "",
    headlessTrackingMode: false,
    autoTrackingEnabled: false,
    autoTrackingHours: DEFAULT_AUTO_TRACKING_HOURS,
    autoTrackingWindowId: 0,
    autoTrackingTabId: 0,
    trackAllStartPage: 1,
    trackAllMaxPages: 202,
    tracking: { running: false, orders: [], index: 0, packages: [], packageIndex: 0 },
    recentTrackingChecks: [],
    amazonAccountName: "",
    trackingByWindow: {},
    logs: [],
    logsByWindow: {},
  });
}

function emptyTrackingRun() {
  return { running: false, orders: [], index: 0, packages: [], packageIndex: 0 };
}

function messageWindowId(message = {}, sender = {}) {
  return Number(message.targetWindowId || sender.tab?.windowId || 0) || null;
}

function trackingRunHasState(run = {}) {
  return Boolean(run && (run.running || run.source || run.startedAt || run.finishedAt));
}

function shouldUseRootTracking(rootRun = {}, windowRun = {}) {
  if (!trackingRunHasState(rootRun)) return false;
  if (rootRun.running && !windowRun?.running) return true;
  return !trackingRunHasState(windowRun);
}

async function getWindowState(windowId) {
  const state = await getState();
  const key = String(windowId || "");
  const windowTracking = windowId ? state.trackingByWindow?.[key] || null : null;
  const rootTracking = state.tracking || emptyTrackingRun();
  return {
    ...state,
    targetWindowId: windowId || null,
    tracking: windowId && !shouldUseRootTracking(rootTracking, windowTracking)
      ? windowTracking || emptyTrackingRun()
      : rootTracking,
    logs: windowId ? state.logsByWindow?.[key] || [] : state.logs,
  };
}

async function saveTracking(tracking, windowId) {
  const state = await getState();
  const rootTracking = state.tracking || {};
  const key = String(windowId || "");
  const windowTracking = windowId ? state.trackingByWindow?.[key] || null : null;
  const saveRoot = !windowId || (
    rootTracking?.startedAt
    && Number(rootTracking.startedAt || 0) === Number(tracking.startedAt || 0)
    && String(rootTracking.source || "") === String(tracking.source || "")
    && shouldUseRootTracking(rootTracking, windowTracking)
  );
  if (saveRoot) {
    const current = rootTracking;
    const staleRunningSave = current.running === false
      && tracking.running !== false
      && current.source === tracking.source
      && Number(current.startedAt || 0) === Number(tracking.startedAt || 0)
      && Number(current.updatedAt || 0) > Number(tracking.updatedAt || 0);
    if (staleRunningSave) return;
    tracking.updatedAt = Date.now();
    await chrome.storage.local.set({ tracking });
    return;
  }
  const { trackingByWindow } = state;
  const current = windowTracking || {};
  const staleRunningSave = current.running === false
    && tracking.running !== false
    && current.source === tracking.source
    && Number(current.startedAt || 0) === Number(tracking.startedAt || 0)
    && Number(current.updatedAt || 0) > Number(tracking.updatedAt || 0);
  if (staleRunningSave) return;
  tracking.updatedAt = Date.now();
  await chrome.storage.local.set({ trackingByWindow: { ...(trackingByWindow || {}), [key]: tracking }, tracking });
}

async function saveFreshTrackAllRun(tracking, windowId) {
  tracking.updatedAt = Date.now();
  const state = await getState();
  const updates = { tracking };
  if (windowId) {
    updates.trackingByWindow = {
      ...(state.trackingByWindow || {}),
      [String(windowId)]: tracking,
    };
  }
  await chrome.storage.local.set(updates);
}

function stoppedTrackingRun(run = {}, message = "Amazon tracking stopped.") {
  return {
    ...(run || {}),
    running: false,
    lastMessage: message,
    stoppedAt: Date.now(),
    updatedAt: Date.now(),
  };
}

async function stopAutoTrackingRuns(message = "Amazon auto tracking is off; saved auto run stopped.") {
  const state = await getState();
  const updates = {};
  if (state.tracking?.running && state.tracking.source === "auto") {
    updates.tracking = stoppedTrackingRun(state.tracking, message);
  }
  const nextByWindow = { ...(state.trackingByWindow || {}) };
  let changedByWindow = false;
  for (const [key, run] of Object.entries(nextByWindow)) {
    if (run?.running && run.source === "auto") {
      nextByWindow[key] = stoppedTrackingRun(run, message);
      changedByWindow = true;
    }
  }
  if (changedByWindow) updates.trackingByWindow = nextByWindow;
  if (Object.keys(updates).length) await chrome.storage.local.set(updates);
  await clearWatchdogIfIdle();
}

async function stopHistoryTrackingRuns(message = "Track all stopped so Amazon auto tracking can run app-known orders only.") {
  const state = await getState();
  const updates = {};
  if (state.tracking?.running && state.tracking.source === "history") {
    updates.tracking = stoppedTrackingRun(state.tracking, message);
  }
  const nextByWindow = { ...(state.trackingByWindow || {}) };
  let changedByWindow = false;
  for (const [key, run] of Object.entries(nextByWindow)) {
    if (run?.running && run.source === "history") {
      nextByWindow[key] = stoppedTrackingRun(run, message);
      changedByWindow = true;
    }
  }
  if (changedByWindow) updates.trackingByWindow = nextByWindow;
  if (Object.keys(updates).length) await chrome.storage.local.set(updates);
  await clearWatchdogIfIdle();
}

function trackingProgress(tracking = {}) {
  if (tracking.source === "history") {
    return {
      total: Number(tracking.queue?.length || 0) + (tracking.currentOrder ? 1 : 0) + Number(tracking.completedOrderIds?.length || 0),
      processed: Number(tracking.completedOrderIds?.length || 0),
      current_order: tracking.currentOrder?.amazon_order_id || "",
      completed: tracking.completedOrderIds?.length || 0,
      failed: tracking.failedOrderIds?.length || 0,
      page: tracking.currentPage || tracking.startPage || 1,
      pages_scanned: tracking.pagesScanned || 0,
      started_at: tracking.startedAt || null,
      last_activity_at: tracking.lastActivityAt || tracking.updatedAt || null,
      message: tracking.running ? "Track all is running." : "Track all is stopped.",
    };
  }
  const total = tracking.orders?.length || 0;
  const index = Math.max(0, Number(tracking.index || 0));
  return {
    total,
    processed: Math.min(index, total),
    current_order: tracking.orders?.[index]?.amazon_order_id || "",
    completed: tracking.completedOrderIds?.length || 0,
    failed: tracking.failedOrderIds?.length || 0,
    skipped_recent: tracking.skippedRecentCount || 0,
    started_at: tracking.startedAt || null,
    last_activity_at: tracking.lastActivityAt || tracking.updatedAt || null,
    message: tracking.running
      ? tracking.source === "payment_recheck" ? "Payment revision recheck is running." : tracking.source === "manual" ? "Queued-order tracking is running." : "Tracking is running."
      : tracking.source === "payment_recheck" ? "Payment revision recheck is stopped." : tracking.source === "manual" ? "Queued-order tracking is stopped." : "Tracking is stopped.",
  };
}

function autoWaitMessage(label, hours, checked = 0, failed = 0, extra = "") {
  const interval = clampAutoHours(hours);
  const detail = `Checked ${Number(checked || 0)} order(s), failed ${Number(failed || 0)}${extra ? `, ${extra}` : ""}.`;
  return `${label} All progress is done. Auto mode is enabled; waiting ${interval} hour(s) until the next scheduled tracking run. ${detail}`;
}

async function rememberRecentCheck(orderId, status = "checked") {
  const cleanId = String(orderId || "").trim();
  if (!cleanId) return;
  const { recentTrackingChecks } = await getState();
  const cutoff = Date.now() - RECENT_TRACKING_CACHE_TTL_MS;
  const next = [{ amazon_order_id: cleanId, checkedAt: Date.now(), status }]
    .concat((recentTrackingChecks || []).filter((item) => item.amazon_order_id !== cleanId && Number(item.checkedAt || 0) >= cutoff))
    .slice(0, 800);
  await chrome.storage.local.set({ recentTrackingChecks: next });
}

function recentCheckSet(recentTrackingChecks = []) {
  const cutoff = Date.now() - RECENT_TRACKING_CACHE_TTL_MS;
  return new Set(
    (recentTrackingChecks || [])
      .filter((item) => Number(item.checkedAt || 0) >= cutoff)
      .filter((item) => !["failed", "unmatched"].includes(String(item.status || "").trim().toLowerCase()))
      .map((item) => String(item.amazon_order_id || "").trim())
      .filter(Boolean),
  );
}

function trackingStatusLooksDelivered(value = "") {
  const text = String(value || "").toLowerCase();
  if (!text) return false;
  if (/\b(not delivered|not yet delivered|undelivered|arriving|out for delivery|delivery attempted|running late|delayed|in transit)\b/.test(text)) return false;
  return /\bdelivered\b/.test(text);
}

function trackingPayloadLooksDelivered(value = "") {
  if (!value) return false;
  try {
    const packages = JSON.parse(String(value));
    if (Array.isArray(packages) && packages.length) {
      return packages.every((pkg) => trackingStatusLooksDelivered([
        pkg?.status,
        pkg?.order_status,
        pkg?.delivery_status,
        pkg?.promise,
        ...(Array.isArray(pkg?.events) ? pkg.events.map((event) => typeof event === "string" ? event : event?.message) : []),
      ].filter(Boolean).join(" ")));
    }
  } catch (_) {
    // Fall through to text inspection for older payloads.
  }
  return trackingStatusLooksDelivered(value);
}

function trackingLineAlreadyDelivered(line = {}) {
  return String(line.state || "").trim().toLowerCase() === "delivered"
    || trackingStatusLooksDelivered(line.tracking_status)
    || trackingPayloadLooksDelivered(line.tracking_payload);
}

function trackingOrderAlreadyDelivered(order = {}) {
  const lines = Array.isArray(order.lines) ? order.lines : [];
  if (lines.length) return lines.every(trackingLineAlreadyDelivered);
  return String(order.state || "").trim().toLowerCase() === "delivered"
    || trackingStatusLooksDelivered(order.tracking_status)
    || trackingPayloadLooksDelivered(order.tracking_payload);
}

function normalizeAmazonOrderId(value = "") {
  const match = String(value || "").match(/\b\d{3}-\d{7}-\d{7}\b/);
  return match ? match[0] : "";
}

function amazonOrderIdsFromValue(value = "") {
  const text = String(value || "");
  if (!text) return [];
  const ids = new Set(text.match(AMAZON_ORDER_ID_RE) || []);
  try {
    const parsed = new URL(text);
    for (const key of ["orderID", "orderId", "order_id", "order"]) {
      const orderId = normalizeAmazonOrderId(parsed.searchParams.get(key) || "");
      if (orderId) ids.add(orderId);
    }
  } catch (_) {
    // Plain text values can still contain Amazon order ids.
  }
  return [...ids];
}

function packageAmazonOrderIds(packageData = {}) {
  const ids = new Set();
  for (const key of ["amazon_order_id", "amazonOrderId", "order_id", "orderId", "orderID"]) {
    for (const orderId of amazonOrderIdsFromValue(packageData?.[key])) ids.add(orderId);
  }
  for (const key of ["tracking_url", "trackingUrl", "amazon_order_url", "amazonOrderUrl", "url"]) {
    for (const orderId of amazonOrderIdsFromValue(packageData?.[key])) ids.add(orderId);
  }
  return [...ids];
}

async function assertPackagePayloadBelongsToOrder(amazonOrderId, packages = [], windowId = null) {
  const expected = normalizeAmazonOrderId(amazonOrderId);
  if (!expected) throw new Error("Missing Amazon order id for tracking guard.");
  for (const packageData of packages || []) {
    if (!packageData || typeof packageData !== "object") continue;
    const ids = packageAmazonOrderIds(packageData);
    const mismatched = ids.filter((orderId) => orderId && orderId !== expected);
    if (mismatched.length) {
      const message = `Blocked tracking payload for ${expected}; package belongs to ${[...new Set(mismatched)].join(", ")}.`;
      await log(message, windowId);
      throw new Error(message);
    }
    packageData.amazon_order_id = expected;
  }
  return packages;
}

async function postGuardedTrackingUpdate(amazonOrderId, payload = {}, options = {}, windowId = null) {
  const expected = normalizeAmazonOrderId(amazonOrderId);
  const packages = await assertPackagePayloadBelongsToOrder(expected, Array.isArray(payload.packages) ? payload.packages : [], windowId);
  const state = await getState();
  return api("/api/tracking/update", {
    method: "POST",
    ...options,
    body: JSON.stringify({
      ...payload,
      amazon_order_id: expected,
      amazon_account_name: String(payload.amazon_account_name || state.amazonAccountName || "").trim(),
      packages,
    }),
  });
}

function isTrackedOrderNotFoundError(error) {
  return /Tracked Amazon order not found/i.test(String(error?.message || error || ""));
}

async function logUnmatchedTrackingOrder(amazonOrderId, windowId = null, context = "tracking update") {
  const orderId = normalizeAmazonOrderId(amazonOrderId);
  if (orderId) await rememberRecentCheck(orderId, "unmatched");
  await log(`Skipped unmatched Amazon order ${orderId || amazonOrderId || "unknown"} during ${context}; app has no linked/tracked order for it.`, windowId);
}

async function clearWatchdogIfIdle() {
  const state = await getState();
  const anyWindowRunning = Object.values(state.trackingByWindow || {}).some((item) => item?.running);
  if (!state.tracking?.running && !anyWindowRunning) {
    await chrome.alarms.clear(TRACKING_WATCHDOG_ALARM);
  }
}

async function ensureWatchdog() {
  chrome.alarms.create(TRACKING_WATCHDOG_ALARM, { periodInMinutes: 2 });
}

async function log(message, windowId = null) {
  const { logs, logsByWindow } = await getState();
  const entry = `${new Date().toLocaleTimeString()} ${message}`;
  if (!windowId) {
    await chrome.storage.local.set({ logs: [entry, ...logs].slice(0, 80) });
    return;
  }
  const key = String(windowId);
  const next = { ...(logsByWindow || {}) };
  next[key] = [entry, ...(next[key] || [])].slice(0, 80);
  await chrome.storage.local.set({ logsByWindow: next, logs: next[key] });
}

async function api(path, options = {}) {
  const { apiBase, adminToken } = await getState();
  const base = normalizeApiBase(apiBase);
  const requestPath = String(path || "").startsWith("/") ? path : `/${path}`;
  const { timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS, headers = {}, retries = 1, ...fetchOptions } = options;
  const maxRetries = Math.max(Number(retries || 0), requestPath === "/api/tracking/update" ? 2 : 0);
  let lastError = null;
  for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), Number(timeoutMs || DEFAULT_REQUEST_TIMEOUT_MS));
    try {
      const response = await fetch(`${base}${requestPath}`, {
        ...fetchOptions,
        headers: { "Content-Type": "application/json", ...(adminToken ? { "X-Admin-Token": adminToken } : {}), ...headers },
        signal: controller.signal,
      });
      if (!response.ok) {
        const responseText = await response.text();
        const detail = responseText || response.statusText || "Request failed";
        const message = `${response.status} ${response.statusText || "HTTP error"} at ${base}${requestPath}: ${detail}`;
        if (response.status >= 500 && attempt < maxRetries) {
          lastError = new Error(message);
          await new Promise((resolve) => setTimeout(resolve, 1200));
          continue;
        }
        throw new Error(message);
      }
      return response.json();
    } catch (error) {
      lastError = error?.name === "AbortError"
        ? new Error(`Local app request timed out after ${Math.round(Number(timeoutMs || DEFAULT_REQUEST_TIMEOUT_MS) / 1000)}s.`)
        : error;
      if (error?.name === "AbortError") break;
      if (!isConnectionError(error) || attempt >= maxRetries) break;
      await new Promise((resolve) => setTimeout(resolve, 1200));
    } finally {
      clearTimeout(timeout);
    }
  }
  throw new Error(connectionErrorMessage(lastError, base));
}

function normalizeApiBase(apiBase) {
  return String(apiBase || DEFAULT_API_BASE).trim().replace(/\/+$/, "") || DEFAULT_API_BASE;
}

function connectionErrorMessage(error, base = "") {
  const raw = String(error?.message || error || "Failed to fetch");
  if (isConnectionError(error)) {
    return `Could not reach the local app at ${base || DEFAULT_API_BASE}. Make sure it is running on port 8000, then save and check the connection again.`;
  }
  return raw;
}

function isConnectionError(error) {
  if (error?.name === "AbortError") return false;
  return /failed to fetch|networkerror|load failed|could not reach/i.test(String(error?.message || error || ""));
}

function clampAutoHours(value, fallback = DEFAULT_AUTO_TRACKING_HOURS) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(1, Math.min(168, Math.round(parsed)));
}

async function existingTab(tabId) {
  const id = Number(tabId || 0);
  if (!id) return null;
  return chrome.tabs.get(id).catch(() => null);
}

async function existingWindow(windowId) {
  const id = Number(windowId || 0);
  if (!id) return null;
  return chrome.windows.get(id).catch(() => null);
}

async function rememberAutoTrackingTarget(windowId = null, tabId = null) {
  const updates = {};
  if (windowId !== null && windowId !== undefined) updates.autoTrackingWindowId = Number(windowId || 0) || 0;
  if (tabId !== null && tabId !== undefined) updates.autoTrackingTabId = Number(tabId || 0) || 0;
  if (Object.keys(updates).length) await chrome.storage.local.set(updates);
}

async function resolveAutoTrackingTarget(preferredWindowId = null) {
  const state = await getState();
  const preferredId = Number(preferredWindowId || 0) || 0;
  const storedTab = await existingTab(state.autoTrackingTabId);
  if (storedTab?.id) {
    return { windowId: Number(storedTab.windowId || 0) || preferredId || null, tabId: storedTab.id };
  }
  if (state.autoTrackingTabId) await rememberAutoTrackingTarget(undefined, 0);
  if (preferredId) {
    const preferredWindow = await existingWindow(preferredId);
    if (preferredWindow?.id) return { windowId: preferredWindow.id, tabId: null };
  }
  const storedWindow = await existingWindow(state.autoTrackingWindowId);
  if (storedWindow?.id) return { windowId: storedWindow.id, tabId: null };
  if (state.autoTrackingWindowId) await rememberAutoTrackingTarget(0, undefined);
  return { windowId: null, tabId: null };
}

async function scheduleAutoTracking(enabled, hours) {
  await chrome.alarms.clear(AUTO_TRACKING_ALARM);
  if (!enabled) return;
  const autoTrackingHours = clampAutoHours(hours);
  chrome.alarms.create(AUTO_TRACKING_ALARM, {
    delayInMinutes: Math.max(1, autoTrackingHours * 60),
    periodInMinutes: Math.max(1, autoTrackingHours * 60),
  });
}

async function restoreAutoTrackingAlarm() {
  const state = await getState();
  await scheduleAutoTracking(state.autoTrackingEnabled === true, state.autoTrackingHours);
}

async function startAutoTracking(windowId = null) {
  const state = await getState();
  if (state.autoTrackingEnabled !== true) {
    await stopAutoTrackingRuns();
    return { ok: false, message: "Amazon auto tracking is disabled." };
  }
  await stopHistoryTrackingRuns();
  const target = await resolveAutoTrackingTarget(windowId);
  const targetWindowId = target.windowId || windowId || null;
  const targetTabId = target.tabId || null;
  if (targetWindowId || targetTabId) await rememberAutoTrackingTarget(targetWindowId, targetTabId);
  const latestState = await getState();
  const windowTracking = targetWindowId ? latestState.trackingByWindow?.[String(targetWindowId)] || null : null;
  if (windowTracking?.running) {
    await log("Scheduled Amazon tracking found an existing window run; resuming from the saved position.", targetWindowId);
    await ensureWatchdog();
    await openCurrentOrder(targetWindowId);
    return { ok: true, resumed: true, progress: trackingProgress(windowTracking) };
  }
  if (latestState.tracking?.running && latestState.tracking.source !== "history") {
    const runWindowId = Number(latestState.tracking.autoTrackingWindowId || targetWindowId || 0) || null;
    await log("Scheduled Amazon tracking found an existing run; resuming from the saved position.", runWindowId);
    await ensureWatchdog();
    await openCurrentOrder(runWindowId);
    return { ok: true, resumed: true, progress: trackingProgress(latestState.tracking) };
  }
  await log(`Scheduled Amazon tracking started for app-known orders only; interval is every ${clampAutoHours(latestState.autoTrackingHours)} hour(s).`, targetWindowId);
  return startTracking(targetWindowId, { source: "auto", targetTabId });
}

async function testConnection() {
  const { apiBase, adminToken, headlessTrackingMode } = await getState();
  const base = normalizeApiBase(apiBase);
  try {
    await api("/health", { timeoutMs: 8000 });
    await api("/api/settings/admin-access", { timeoutMs: 8000, headers: adminToken ? { "X-Admin-Token": adminToken } : {} });
    let activeText = "";
    try {
      const tracking = await api("/api/tracking/orders?status=active&page=1&per_page=100", { timeoutMs: 30000 });
      const count = Number(tracking.total || tracking.orders?.length || 0);
      activeText = ` Active tracking orders: ${count}.`;
    } catch (error) {
      activeText = ` Could not read active tracking count: ${error.message}`;
    }
    let headlessText = "";
    if (headlessTrackingMode) {
      try {
        const readiness = await api("/api/tracking/browserless/readiness", { timeoutMs: 30000 });
        headlessText = readiness.ready === false
          ? ` Headless profile not ready: ${readiness.message || "check Amazon sign-in and delivery address."}`
          : " Headless profile ready.";
      } catch (error) {
        headlessText = ` Headless readiness check failed: ${error.message}`;
      }
    }
    return { ok: true, message: `Connected to ${base}. Admin token accepted.${activeText}${headlessText}` };
  } catch (error) {
    throw new Error(connectionErrorMessage(error, base));
  }
}

function isAmazonUrl(url = "") {
  try {
    const { hostname } = new URL(url);
    return /(^|\.)amazon\.com$/i.test(hostname);
  } catch {
    return false;
  }
}

function isAmazonTrackingExtensionUrl(url = "") {
  if (!isAmazonUrl(url)) return false;
  try {
    const parsed = new URL(url);
    return /\/(gp\/your-account\/ship-track|progress-tracker\/package|gp\/your-account\/order-details|your-orders\/order-details|gp\/css\/order-history)/i.test(parsed.pathname);
  } catch {
    return false;
  }
}

function waitForTabReadyForInjection(tabId, timeoutMs = 3500) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (tab = null) => {
      if (done) return;
      done = true;
      chrome.tabs.onUpdated.removeListener(listener);
      clearTimeout(timer);
      resolve(tab);
    };
    const listener = (updatedTabId, changeInfo, tab) => {
      if (updatedTabId === tabId && changeInfo.status === "complete") finish(tab);
    };
    const timer = setTimeout(() => {
      chrome.tabs.get(tabId).then((tab) => finish(tab)).catch(() => finish(null));
    }, timeoutMs);
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tabId).then((tab) => {
      if (tab?.status === "complete") finish(tab);
    }).catch(() => {});
  });
}

async function ensureAmazonContentScript(tabId, windowId) {
  if (!tabId) return;
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (!isAmazonUrl(tab?.url || "")) return;
  try {
    await chrome.scripting.insertCSS({ target: { tabId }, files: ["content.css"] });
  } catch (_) {
    // CSS is helpful for the status panel but not required for tracking.
  }
  try {
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
    return;
  } catch (error) {
    await log(`Could not inject Amazon tracking content script: ${error.message}`, windowId);
  }
  try {
    await chrome.tabs.sendMessage(tabId, { type: "NUTRICITY_RUN_CONTENT" });
  } catch (error) {
    await log(`Could not request Amazon content script run: ${error.message}`, windowId);
  }
}

function scheduleAmazonContentInjection(tabId, windowId) {
  if (!tabId) return;
  for (const delayMs of [1200, 5000, 12000]) {
    setTimeout(() => {
      ensureAmazonContentScript(tabId, windowId).catch((error) => log(`Could not schedule Amazon content script: ${error.message}`, windowId));
    }, delayMs);
  }
}

async function openUrl(url, windowId, options = {}) {
  const preferredTab = await existingTab(options.tabId);
  const activate = options.active === true;
  const focusWindow = options.focus === true;
  let resolvedWindowId = Number(windowId || preferredTab?.windowId || 0) || null;
  const activeQuery = resolvedWindowId ? { active: true, windowId: resolvedWindowId } : { active: true, currentWindow: true };
  const tabs = preferredTab?.id ? [preferredTab] : await chrome.tabs.query(activeQuery);
  let tabId = null;
  if (preferredTab?.id) {
    tabId = preferredTab.id;
    resolvedWindowId = Number(preferredTab.windowId || resolvedWindowId || 0) || null;
    if (preferredTab.url !== url) await chrome.tabs.update(tabId, { url, active: activate });
    else if (activate) await chrome.tabs.update(tabId, { active: true });
  } else if (tabs[0]?.id && isAmazonTrackingExtensionUrl(tabs[0].url || "")) {
    tabId = tabs[0].id;
    resolvedWindowId = Number(tabs[0].windowId || resolvedWindowId || 0) || null;
    if (tabs[0].url !== url) await chrome.tabs.update(tabId, { url, active: activate });
    else if (activate) await chrome.tabs.update(tabId, { active: true });
  } else {
    const candidateQuery = resolvedWindowId ? { windowId: resolvedWindowId } : { currentWindow: true };
    const candidates = await chrome.tabs.query(candidateQuery);
    const reusable = candidates.find((tab) => tab.id && isAmazonTrackingExtensionUrl(tab.url || ""));
    if (reusable?.id) {
      tabId = reusable.id;
      resolvedWindowId = Number(reusable.windowId || resolvedWindowId || 0) || null;
      if (reusable.url !== url) await chrome.tabs.update(tabId, { url, active: activate });
      else if (activate) await chrome.tabs.update(tabId, { active: true });
    } else {
      const tab = await chrome.tabs.create({ url, active: activate, ...(resolvedWindowId ? { windowId: resolvedWindowId } : {}) });
      tabId = tab?.id || null;
      resolvedWindowId = Number(tab?.windowId || resolvedWindowId || 0) || null;
    }
  }
  if (resolvedWindowId && focusWindow) {
    await chrome.windows.update(resolvedWindowId, { focused: true }).catch(() => undefined);
  }
  if (tabId && isAmazonUrl(url)) {
    await waitForTabReadyForInjection(tabId);
    await ensureAmazonContentScript(tabId, resolvedWindowId || windowId);
    scheduleAmazonContentInjection(tabId, resolvedWindowId || windowId);
  }
  return { tabId, windowId: resolvedWindowId };
}

async function openTrackingRunUrl(tracking, windowId, url) {
  const isAutoRun = tracking?.source === "auto";
  const targetWindowId = Number(
    tracking?.trackingWindowId
    || (isAutoRun ? tracking.autoTrackingWindowId : 0)
    || windowId
    || 0,
  ) || windowId || null;
  const targetTabId = Number(
    tracking?.trackingTabId
    || (isAutoRun ? tracking.autoTrackingTabId : 0)
    || 0,
  ) || 0;
  const opened = await openUrl(url, targetWindowId, { ...(targetTabId ? { tabId: targetTabId } : {}), active: false, focus: false });
  if (opened.tabId || opened.windowId) {
    tracking.trackingTabId = opened.tabId || tracking.trackingTabId || 0;
    tracking.trackingWindowId = opened.windowId || targetWindowId || tracking.trackingWindowId || 0;
  }
  if (isAutoRun && (opened.tabId || opened.windowId)) {
    tracking.autoTrackingTabId = opened.tabId || tracking.autoTrackingTabId || tracking.trackingTabId || 0;
    tracking.autoTrackingWindowId = opened.windowId || targetWindowId || tracking.autoTrackingWindowId || tracking.trackingWindowId || 0;
    await rememberAutoTrackingTarget(tracking.autoTrackingWindowId, tracking.autoTrackingTabId);
  }
  if (opened.tabId || opened.windowId) await saveTracking(tracking, tracking.trackingWindowId || targetWindowId || windowId);
  return opened;
}

function orderUrl(order) {
  const orderId = normalizeAmazonOrderId(order?.amazon_order_id || "");
  const rawOrderId = String(order?.amazon_order_id || "").trim();
  const providedUrl = String(order?.amazon_order_url || "").trim();
  if (providedUrl) {
    const urlOrderIds = amazonOrderIdsFromValue(providedUrl);
    if (!orderId || !urlOrderIds.length || urlOrderIds.includes(orderId)) return providedUrl;
  }
  return `https://www.amazon.com/your-orders/order-details?orderID=${encodeURIComponent(orderId || rawOrderId)}`;
}

function physicalTrackingId(value) {
  const text = String(value || "").trim();
  if (/^https?:\/\//i.test(text)) return "";
  const normalized = text.toUpperCase().replace(/[^A-Z0-9-]/g, "");
  if (/^TBA[A-Z0-9]+$/.test(normalized)) return normalized;
  if (/^1Z[A-Z0-9]{12,24}$/.test(normalized)) return normalized;
  if (/^SG\d{10,24}$/.test(normalized)) return normalized;
  if (/^D\d{10,24}$/.test(normalized)) return normalized;
  if (/^\d{12,30}$/.test(normalized)) return normalized;
  return "";
}

function sanitizePackageTrackingIdentity(packageData = {}) {
  const trackingId = physicalTrackingId(
    packageData.tracking_id ||
    packageData.trackingId ||
    packageData.tracking_number ||
    packageData.trackingNumber
  );
  if (trackingId) {
    packageData.tracking_id = trackingId;
    return packageData;
  }
  delete packageData.tracking_id;
  delete packageData.trackingId;
  delete packageData.tracking_number;
  delete packageData.trackingNumber;
  return packageData;
}

function amazonShipmentKey(packageData = {}) {
  const directShipment = String(packageData.shipment_id || packageData.shipmentId || "").trim();
  const directPackage = String(packageData.package_id || packageData.packageId || "").trim();
  const directItem = String(packageData.item_id || packageData.itemId || "").trim();
  if (directShipment) return `shipment:${directShipment}`;
  if (directPackage) return `package:${directPackage}`;
  if (directItem) return `item:${directItem}`;
  const rawUrl = String(packageData.tracking_url || packageData.trackingUrl || "").trim();
  if (!rawUrl) return "";
  try {
    const url = new URL(rawUrl, "https://www.amazon.com");
    const shipmentId = url.searchParams.get("shipmentId") || url.searchParams.get("shipmentID") || "";
    const packageId = url.searchParams.get("packageId") || url.searchParams.get("packageID") || "";
    const itemId = url.searchParams.get("itemId") || url.searchParams.get("itemID") || "";
    return shipmentId ? `shipment:${shipmentId}` : packageId ? `package:${packageId}` : itemId ? `item:${itemId}` : "";
  } catch (_) {
    return "";
  }
}

function splitShipmentMismatch(queuedPackage = {}, pagePackage = {}) {
  const expected = amazonShipmentKey(queuedPackage);
  const actual = amazonShipmentKey(pagePackage);
  return expected && actual && expected !== actual ? { expected, actual } : null;
}

function usefulAmazonProductTitle(value) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (text.length < 6) return false;
  if (/^[\d\W_]+$/.test(text)) return false;
  return !/buy it again|return eligible|order placed|view item|amazon business card/i.test(text);
}

function mergePackageProducts(primary = [], secondary = []) {
  const products = [];
  const byAsin = new Map();
  const add = (item = {}) => {
    const asin = String(item.asin || "").trim().toUpperCase();
    if (!asin) return;
    const existing = byAsin.get(asin) || { asin };
    const title = String(item.title || "").trim();
    const imageUrl = String(item.image_url || item.imageUrl || "").trim();
    const url = String(item.url || "").trim();
    if (url && !existing.url) existing.url = url;
    if (imageUrl && !existing.image_url) existing.image_url = imageUrl;
    if (usefulAmazonProductTitle(title) && !usefulAmazonProductTitle(existing.title)) existing.title = title;
    if (!byAsin.has(asin)) {
      byAsin.set(asin, existing);
      products.push(existing);
    }
  };
  primary.forEach(add);
  secondary.forEach(add);
  return products;
}

function orderHistoryUrl(page = 1) {
  const pageNumber = Math.max(1, Math.round(Number(page || 1)));
  return `${ORDER_HISTORY_URL}#pagination/${pageNumber}/`;
}

function isAmazonOrderHistoryUrl(value = "") {
  try {
    const url = new URL(String(value || ""), ORDER_HISTORY_URL);
    return /(^|\.)amazon\.com$/i.test(url.hostname) && ORDER_HISTORY_PATH_RE.test(url.pathname);
  } catch {
    return false;
  }
}

function normalizeHistoryNextUrl(nextUrl = "", nextPage = 1) {
  const raw = String(nextUrl || "").trim();
  if (!raw) return "";
  if (/^#?pagination\/next\/?$/i.test(raw.replace(/^#/, ""))) {
    return orderHistoryUrl(nextPage);
  }
  try {
    const url = new URL(raw, ORDER_HISTORY_URL);
    if (/(^|\.)amazon\.com$/i.test(url.hostname) && /pagination\/next/i.test(url.hash)) {
      return orderHistoryUrl(nextPage);
    }
    if (isAmazonOrderHistoryUrl(url.href)) return url.href;
    return url.href;
  } catch {
    return raw;
  }
}

async function openCurrentOrder(windowId) {
  const state = await getWindowState(windowId);
  const { tracking } = state;
  if (!tracking.running) return;
  const order = tracking.orders[tracking.index];
  if (!order) {
    const checked = tracking.completedOrderIds?.length || 0;
    const failed = tracking.failedOrderIds?.length || 0;
    const skipped = Number(tracking.skippedRecentCount || 0);
    tracking.running = false;
    tracking.finishedAt = Date.now();
    if (tracking.source === "auto" && state.autoTrackingEnabled === true) {
      tracking.lastMessage = autoWaitMessage("Amazon tracking complete.", state.autoTrackingHours, checked, failed, skipped ? `skipped recent ${skipped}` : "");
    } else {
      const doneReason = tracking.source === "payment_recheck"
        ? "all open payment revision orders were rechecked"
        : tracking.source === "manual"
        ? "all queued Amazon orders finished"
        : "no more eligible open Amazon orders were left";
      tracking.lastMessage = `Stopped because ${doneReason}. All tracking codes scanned. Checked ${checked} order(s), failed ${failed}${skipped ? `, skipped recent ${skipped}` : ""}.`;
    }
    await saveTracking(tracking, windowId);
    await log(`${tracking.lastMessage} No more open Amazon orders.`, windowId);
    await clearWatchdogIfIdle();
    return;
  }
  tracking.packages = [];
  tracking.packageIndex = 0;
  tracking.currentStep = "order";
  const nextOrderUrl = orderUrl(order);
  tracking.currentUrl = nextOrderUrl;
  tracking.currentOrderId = order.amazon_order_id;
  tracking.lastActivityAt = Date.now();
  tracking.lastMessage = `Opening Amazon order ${order.amazon_order_id}.`;
  await saveTracking(tracking, windowId);
  await ensureWatchdog();
  await log(`Opening Amazon order ${order.amazon_order_id}.`, windowId);
  await openTrackingRunUrl(tracking, windowId, nextOrderUrl);
}

async function advanceCurrentOrder(tracking, windowId, status = "checked") {
  const order = tracking.orders?.[tracking.index];
  if (order?.amazon_order_id) {
    await rememberRecentCheck(order.amazon_order_id, status);
    if (status === "failed") {
      tracking.failedOrderIds = [...(tracking.failedOrderIds || []), order.amazon_order_id];
    } else {
      tracking.completedOrderIds = [...(tracking.completedOrderIds || []), order.amazon_order_id];
    }
  }
  tracking.index += 1;
  tracking.packages = [];
  tracking.packageIndex = 0;
  tracking.mismatchRecoveryKey = "";
  tracking.mismatchRecoveryCount = 0;
  tracking.redirectRecoveryKey = "";
  tracking.redirectRecoveryCount = 0;
  tracking.currentStep = "";
  tracking.currentUrl = "";
  tracking.currentOrderId = "";
  tracking.lastActivityAt = Date.now();
  await saveTracking(tracking, windowId);
  await openCurrentOrder(windowId);
}

async function startTracking(windowId, options = {}) {
  const state = await getState();
  const { headlessTrackingMode } = state;
  if (headlessTrackingMode) return startHeadlessTracking();
  const isAutoRun = options.source === "auto";
  const payload = await fetchAllTrackingOrders("active", { includeHistoryRefresh: !isAutoRun });
  const { recentTrackingChecks } = await getState();
  const recent = recentCheckSet(recentTrackingChecks);
  const allOrders = (payload.orders || []).filter((order) => !trackingOrderAlreadyDelivered(order));
  let orders = allOrders.filter((order) => !recent.has(String(order.amazon_order_id || "").trim()));
  let forcedRecentRescan = false;
  if (!orders.length && allOrders.length) {
    orders = allOrders;
    forcedRecentRescan = true;
  }
  const tracking = {
    running: true,
    source: isAutoRun ? "auto" : "visible",
    orders,
    index: 0,
    packages: [],
    packageIndex: 0,
    completedOrderIds: [],
    failedOrderIds: [],
    skippedRecentCount: forcedRecentRescan ? 0 : allOrders.length - orders.length,
    forcedRecentRescan,
    trackingWindowId: Number(windowId || 0) || 0,
    trackingTabId: Number(options.targetTabId || 0) || 0,
    startedAt: Date.now(),
    lastActivityAt: Date.now(),
    lastMessage: forcedRecentRescan ? "Tracking started by rescanning recently checked orders." : "Tracking started.",
  };
  if (tracking.source === "auto") {
    tracking.autoTrackingWindowId = Number(windowId || 0) || 0;
    tracking.autoTrackingTabId = Number(options.targetTabId || 0) || 0;
  }
  if (!orders.length) {
    tracking.running = false;
    tracking.finishedAt = Date.now();
    tracking.lastMessage = tracking.source === "auto" && state.autoTrackingEnabled === true
      ? autoWaitMessage("No eligible Amazon orders need tracking.", state.autoTrackingHours, 0, 0)
      : "No Amazon orders need tracking.";
    await saveTracking(tracking, windowId);
    await clearWatchdogIfIdle();
    await log(tracking.lastMessage, windowId);
    return { ok: tracking.source === "auto", message: tracking.lastMessage, progress: trackingProgress(tracking) };
  }
  await saveTracking(tracking, windowId);
  await log(forcedRecentRescan
    ? `Loaded ${orders.length} Amazon order(s) for tracking by rescanning recently checked orders.`
    : `Loaded ${orders.length} Amazon order(s) for tracking; skipped ${tracking.skippedRecentCount} recently checked order(s).`, windowId);
  await openCurrentOrder(windowId);
  return { ok: true, message: forcedRecentRescan ? `Started tracking ${orders.length} recently checked order(s) again.` : `Started tracking ${orders.length} order(s).`, progress: trackingProgress(tracking) };
}

async function startSingleOrderTracking(windowId, amazonOrderId) {
  const orderId = String(amazonOrderId || "").trim();
  if (!/^\d{3}-\d{7}-\d{7}$/.test(orderId)) {
    return { ok: false, message: "Enter a valid Amazon order number like 113-0000000-0000000." };
  }
  return startManualOrderQueueTracking(windowId, [orderId], "single");
}

function normalizeManualOrderIds(values = []) {
  const ids = [];
  const seen = new Set();
  for (const value of values || []) {
    const matches = String(value || "").match(/\b\d{3}-\d{7}-\d{7}\b/g) || [];
    for (const raw of matches) {
      const orderId = raw.trim();
      if (!orderId || seen.has(orderId)) continue;
      seen.add(orderId);
      ids.push(orderId);
    }
  }
  return ids;
}

async function fetchOpenPaymentFailureOrders() {
  const perPage = 100;
  const orders = [];
  const seen = new Set();
  let total = 0;
  for (let page = 1; page <= 100; page += 1) {
    const params = new URLSearchParams({ status: "open", page: String(page), per_page: String(perPage) });
    const payload = await api(`/api/tracking/payment-failures?${params.toString()}`, { timeoutMs: 30000, retries: 1 });
    total = Number(payload.total || total || 0);
    for (const row of payload.rows || []) {
      const orderId = normalizeAmazonOrderId(row.amazon_order_id || "");
      if (!orderId || seen.has(orderId)) continue;
      seen.add(orderId);
      orders.push({
        amazon_order_id: orderId,
        amazon_order_url: row.amazon_order_url || orderUrl({ amazon_order_id: orderId }),
      });
    }
    if (!payload.rows?.length || page * perPage >= total) break;
  }
  return { orders, total: Math.max(total, orders.length) };
}

async function startPaymentFailureRecheck(windowId) {
  const { orders, total } = await fetchOpenPaymentFailureOrders();
  if (!orders.length) {
    await log("Payment revision recheck found no open payment-failed Amazon orders.", windowId);
    return { ok: true, count: 0, total, message: "No open payment revision orders found in the app." };
  }
  const result = await startManualOrderQueueTracking(windowId, orders.map((order) => order.amazon_order_id), "payment_recheck");
  await log(`Payment revision recheck started for ${orders.length} open Amazon order(s).`, windowId);
  return {
    ...result,
    count: orders.length,
    total,
    message: `Started payment revision recheck for ${orders.length} open Amazon order(s).`,
  };
}

async function startManualOrderQueueTracking(windowId, amazonOrderIds = [], source = "manual") {
  const orderIds = normalizeManualOrderIds(amazonOrderIds);
  if (!orderIds.length) {
    return { ok: false, message: "Paste at least one valid Amazon order number like 113-0000000-0000000." };
  }
  const orders = orderIds.map((orderId) => ({
    amazon_order_id: orderId,
    amazon_order_url: orderUrl({ amazon_order_id: orderId }),
  }));
  const tracking = {
    running: true,
    source: source === "single" && orders.length === 1 ? "single" : source === "payment_recheck" ? "payment_recheck" : "manual",
    singleOrderId: orders.length === 1 ? orderIds[0] : "",
    batchOrderIds: orderIds,
    orders,
    index: 0,
    packages: [],
    packageIndex: 0,
    completedOrderIds: [],
    failedOrderIds: [],
    skippedRecentCount: 0,
    trackingWindowId: Number(windowId || 0) || 0,
    trackingTabId: 0,
    startedAt: Date.now(),
    lastActivityAt: Date.now(),
    lastMessage: source === "payment_recheck"
      ? `Payment revision recheck started for ${orders.length} Amazon orders.`
      : orders.length === 1 ? `Single-order tracking started for ${orderIds[0]}.` : `Queued-order tracking started for ${orders.length} Amazon orders.`,
  };
  await saveTracking(tracking, windowId);
  await ensureWatchdog();
  await log(source === "payment_recheck" ? `Payment revision recheck started for ${orders.length} Amazon orders.` : orders.length === 1 ? `Single-order tracking started for ${orderIds[0]}.` : `Queued-order tracking started for ${orders.length} Amazon orders.`, windowId);
  await openCurrentOrder(windowId);
  return {
    ok: true,
    message: source === "payment_recheck" ? `Started payment revision recheck for ${orders.length} Amazon orders.` : orders.length === 1 ? `Started tracking Amazon order ${orderIds[0]}.` : `Started tracking ${orders.length} queued Amazon orders.`,
    progress: trackingProgress(tracking),
  };
}

async function fetchAllTrackingOrders(status = "active", options = {}) {
  const perPage = 100;
  let page = 1;
  let total = 0;
  const orders = [];
  const seen = new Set();
  const state = await getState();
  const amazonAccountName = String(options.amazonAccountName || state.amazonAccountName || "").trim();
  while (page <= 200) {
    const params = new URLSearchParams({ page: String(page), per_page: String(perPage), status });
    if (amazonAccountName) params.set("amazon_account_name", amazonAccountName);
    if (options.includeHistoryRefresh === false) params.set("include_history_refresh", "0");
    const payload = await api(`/api/tracking/orders?${params.toString()}`, { timeoutMs: 30000 });
    total = Number(payload.total || total || 0);
    for (const order of payload.orders || []) {
      const orderId = String(order.amazon_order_id || "").trim();
      if (!orderId || seen.has(orderId)) continue;
      seen.add(orderId);
      orders.push(order);
    }
    if (!payload.orders?.length || page * perPage >= total) break;
    page += 1;
  }
  return { ok: true, orders, total: Math.max(total, orders.length), page_count: page };
}

function normalizeHistoryProductItem(item = {}) {
  return {
    asin: String(item?.asin || "").trim().toUpperCase(),
    quantity: Math.max(1, Math.round(Number(item?.quantity || 1))),
    quantity_verified: item?.quantity_verified === true,
    title: String(item?.title || "").replace(/\s+/g, " ").trim(),
    image_url: String(item?.image_url || item?.imageUrl || "").trim(),
    url: String(item?.url || "").trim(),
  };
}

function normalizeHistoryOrder(order = {}) {
  const orderId = String(order.amazon_order_id || "").trim();
  if (!/^\d{3}-\d{7}-\d{7}$/.test(orderId)) return null;
  const rawItems = (order.items || order.products || [])
    .map(normalizeHistoryProductItem)
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
    amazon_order_url: order.amazon_order_url || orderUrl({ amazon_order_id: orderId }),
    amazon_account_name: String(order.amazon_account_name || order.amazonAccountName || "").trim(),
    recipient: String(order.recipient || "").replace(/\s+/g, " ").trim(),
    order_date: String(order.order_date || "").replace(/\s+/g, " ").trim(),
    status: String(order.status || "").replace(/\s+/g, " ").trim(),
    asins: Object.keys(asinQuantities),
    items,
    products: items,
    asin_quantities: asinQuantities,
    cancelled: order.cancelled === true,
    payment_revision_needed: order.paymentRevisionNeeded === true || order.payment_revision_needed === true,
    payment_revision_url: String(order.paymentRevisionUrl || order.payment_revision_url || "").trim(),
    page_text: String(order.pageText || order.page_text || "").trim(),
  };
}

async function lookupHistoryOrder(order) {
  const normalized = normalizeHistoryOrder(order);
  if (!normalized) return { ok: false, message: "Invalid Amazon order history record." };
  return api("/api/chrome/order-history/lookup", {
    method: "POST",
    body: JSON.stringify({ orders: [normalized] }),
    timeoutMs: 30000,
    retries: 1,
  });
}

async function lookupHistoryOrders(orders) {
  const normalized = orders.map(normalizeHistoryOrder).filter(Boolean);
  if (!normalized.length) return { ok: true, matches: {}, suggestions: {}, unmatched: [] };
  return api("/api/chrome/order-history/lookup", {
    method: "POST",
    body: JSON.stringify({ orders: normalized }),
    timeoutMs: 60000,
    retries: 1,
  });
}

async function lookupHistoryOrderDirect(order) {
  const normalized = normalizeHistoryOrder(order);
  if (!normalized) return { ok: true, odoo_direct: {} };
  return api("/api/chrome/order-history/odoo-direct", {
    method: "POST",
    body: JSON.stringify({ orders: [normalized] }),
    timeoutMs: 30000,
    retries: 1,
  });
}

async function lookupHistoryOrdersDirect(orders) {
  const normalized = orders.map(normalizeHistoryOrder).filter(Boolean);
  if (!normalized.length) return { ok: true, odoo_direct: {} };
  return api("/api/chrome/order-history/odoo-direct", {
    method: "POST",
    body: JSON.stringify({ orders: normalized }),
    timeoutMs: 120000,
    retries: 1,
  });
}

function rowsForHistoryMatch(orderId, result = {}, directResult = {}) {
  const matchRows = result.matches?.[orderId]?.orders || [];
  const suggestionRows = result.suggestions?.[orderId] || [];
  const direct = directResult.odoo_direct || directResult.odooDirect || {};
  const directRows = Array.isArray(direct) ? direct : (direct?.[orderId] || []);
  return [...matchRows, ...suggestionRows, ...directRows].filter(Boolean);
}

async function manualMatchHistoryOrder(normalized, rows, windowId) {
  const orderNames = [...new Set(rows.map((row) => row.odoo_order_name).filter(Boolean))];
  const lineIds = [...new Set(rows.flatMap((row) => {
    const value = row.line_ids || row.lineIds || row.line_id || row.id || [];
    return Array.isArray(value) ? value : [value];
  }).map(Number).filter(Boolean))];
  const storeIds = [...new Set(rows.map((row) => Number(row.store_id || 0)).filter(Boolean))];
  if (!orderNames.length && !lineIds.length) {
    return { matched: 0, message: `No app match found for ${normalized.amazon_order_id}.` };
  }
  const result = await api("/api/manual-amazon/match", {
    method: "POST",
    body: JSON.stringify({
      amazon_order_id: normalized.amazon_order_id,
      amazon_order_url: normalized.amazon_order_url,
      amazon_account_name: normalized.amazon_account_name || "",
      order_date: normalized.order_date,
      order_names: orderNames,
      line_ids: lineIds,
      asins: normalized.asins || [],
      cancelled: normalized.cancelled === true,
      source_text: normalized.recipient || "",
      store_id: storeIds.length === 1 ? storeIds[0] : null,
      replace_existing: true,
    }),
    timeoutMs: 120000,
    retries: 1,
  });
  await log(result.message || `Matched ${normalized.amazon_order_id}.`, windowId);
  return { matched: Number(result.matched || 0), message: result.message || `Matched ${normalized.amazon_order_id}.` };
}

async function syncHistoryOrderToApp(order, windowId) {
  const normalized = normalizeHistoryOrder(order);
  if (!normalized) return { matched: 0, message: "Invalid order history card." };
  let lookup = null;
  let direct = null;
  try {
    lookup = await lookupHistoryOrder(normalized);
    direct = await lookupHistoryOrderDirect(normalized);
  } catch (error) {
    await log(`Order history lookup failed for ${normalized.amazon_order_id}: ${error.message}`, windowId);
    return { matched: 0, message: error.message };
  }
  const rows = rowsForHistoryMatch(normalized.amazon_order_id, lookup || {}, direct || {});
  return manualMatchHistoryOrder(normalized, rows, windowId);
}

async function syncHistoryOrdersToAppBatch(rawOrders, windowId) {
  const normalizedOrders = rawOrders.map(normalizeHistoryOrder).filter(Boolean);
  if (!normalizedOrders.length) return { matched: 0, prepared: 0 };
  await log(`Preparing ${normalizedOrders.length} Track all order match(es) in batch.`, windowId);
  let lookup = {};
  let direct = {};
  try {
    [lookup, direct] = await Promise.all([
      lookupHistoryOrders(normalizedOrders),
      lookupHistoryOrdersDirect(normalizedOrders),
    ]);
  } catch (error) {
    await log(`Batch order-history lookup failed: ${error.message}. Falling back to queued tracking.`, windowId);
    return { matched: 0, prepared: 0 };
  }
  const latestState = await getWindowState(windowId);
  if (!latestState.tracking?.running || latestState.tracking.source !== "history") {
    await log("Track all preparation stopped before manual matching.", windowId);
    return { matched: 0, prepared: 0, stopped: true };
  }
  let nextIndex = 0;
  let matched = 0;
  let prepared = 0;
  const matchedOrderIds = [];
  const workers = Array.from({ length: Math.min(4, normalizedOrders.length) }, async () => {
    while (true) {
      const currentIndex = nextIndex;
      nextIndex += 1;
      if (currentIndex >= normalizedOrders.length) return;
      const state = await getWindowState(windowId);
      if (!state.tracking?.running || state.tracking.source !== "history") return;
      const normalized = normalizedOrders[currentIndex];
      if (!normalized) return;
      const rows = rowsForHistoryMatch(normalized.amazon_order_id, lookup || {}, direct || {});
      try {
        const result = await manualMatchHistoryOrder(normalized, rows, windowId);
        prepared += 1;
        matched += Number(result.matched || 0);
        if (Number(result.matched || 0) > 0) matchedOrderIds.push(normalized.amazon_order_id);
      } catch (error) {
        prepared += 1;
        await log(`Could not prepare ${normalized.amazon_order_id}: ${error.message}`, windowId);
      }
    }
  });
  await Promise.all(workers);
  const { tracking } = await getWindowState(windowId);
  if (tracking?.running && tracking.source === "history") {
    tracking.matchedOrderIds = [...new Set([...(tracking.matchedOrderIds || []), ...matchedOrderIds])];
    tracking.lastActivityAt = Date.now();
    tracking.lastMessage = `Prepared ${prepared} Track all order(s); matched ${matched} line(s).`;
    await saveTracking(tracking, windowId);
  }
  await log(`Batch preparation complete: ${prepared} order(s), ${matched} matched line(s).`, windowId);
  return { matched, prepared };
}

async function startTrackAll(windowId, startPage = 1, maxPages = 202, startUrl = "") {
  const page = Math.max(1, Math.round(Number(startPage || 1)));
  const pages = Math.max(1, Math.min(999, Math.round(Number(maxPages || 202))));
  const firstUrl = isAmazonOrderHistoryUrl(startUrl) ? String(startUrl || "").trim() : orderHistoryUrl(page);
  const tracking = {
    running: true,
    source: "history",
    orders: [],
    queue: [],
    currentOrder: null,
    currentPage: page,
    startPage: page,
    maxPages: pages,
    pagesScanned: 0,
    seenOrderIds: [],
    completedOrderIds: [],
    failedOrderIds: [],
    matchedOrderIds: [],
    trackingWindowId: Number(windowId || 0) || 0,
    trackingTabId: 0,
    startedAt: Date.now(),
    lastActivityAt: Date.now(),
    lastMessage: `Fresh Track all started from order-history page ${page}.`,
  };
  await saveFreshTrackAllRun(tracking, windowId);
  await ensureWatchdog();
  await log(`Fresh Track all started from Amazon order-history page ${page}; max pages ${pages}.`, windowId);
  await openTrackingRunUrl(tracking, windowId, firstUrl);
  return { ok: true, message: `Fresh Track all started from page ${page}.`, progress: trackingProgress(tracking) };
}

function canResumeHistoryTracking(tracking = {}) {
  if (tracking.source !== "history" || tracking.running) return false;
  const hasCurrent = Boolean(tracking.currentOrder?.amazon_order_id);
  const hasQueue = Array.isArray(tracking.queue) && tracking.queue.length > 0;
  const hasMorePages = Number(tracking.pagesScanned || 0) < Number(tracking.maxPages || 0);
  return hasCurrent || hasQueue || hasMorePages;
}

async function resumeTrackAll(windowId) {
  const { tracking } = await getWindowState(windowId);
  if (tracking.running && tracking.source === "history") {
    return { ok: true, message: "Track all is already running.", progress: trackingProgress(tracking) };
  }
  if (!canResumeHistoryTracking(tracking)) {
    return { ok: false, message: "No stopped Track all run is available to resume." };
  }
  tracking.running = true;
  delete tracking.finishedAt;
  tracking.resumedAt = Date.now();
  tracking.lastActivityAt = Date.now();
  tracking.lastMessage = "Resuming Track all from the saved position.";
  await saveTracking(tracking, windowId);
  await ensureWatchdog();
  await log(`Track all resumed from Amazon order-history page ${tracking.currentPage || tracking.startPage || 1}.`, windowId);
  if (tracking.currentOrder?.amazon_order_id || (tracking.queue || []).length) {
    await openHistoryCurrentOrder(windowId);
  } else {
    await maybeOpenNextHistoryPage(tracking, windowId);
  }
  return { ok: true, message: "Track all resumed from the saved position.", progress: trackingProgress(tracking) };
}

async function stopTracking(windowId) {
  const { headlessTrackingMode } = await getState();
  const { tracking } = await getWindowState(windowId);
  if (headlessTrackingMode && tracking.source !== "history") return stopHeadlessTracking();
  tracking.running = false;
  tracking.lastMessage = "Tracking stopped by user.";
  await saveTracking(tracking, windowId);
  await log("Tracking stopped.", windowId);
  await clearWatchdogIfIdle();
  return { ok: true, message: "Stopped." };
}

async function maybeOpenNextHistoryPage(tracking, windowId) {
  const nextPage = Number(tracking.currentPage || tracking.startPage || 1) + 1;
  const savedNextUrl = normalizeHistoryNextUrl(tracking.nextUrl || "", nextPage);
  if (Number(tracking.pagesScanned || 0) > 0 && !savedNextUrl) {
    tracking.running = false;
    tracking.finishedAt = Date.now();
    tracking.lastMessage = `All tracking codes scanned. Track all reached the last Amazon order-history page; checked ${tracking.completedOrderIds?.length || 0} order(s), failed ${tracking.failedOrderIds?.length || 0}.`;
    await saveTracking(tracking, windowId);
    await log(tracking.lastMessage, windowId);
    await clearWatchdogIfIdle();
    return false;
  }
  if (Number(tracking.pagesScanned || 0) < Number(tracking.maxPages || 1)) {
    tracking.nextUrl = savedNextUrl || orderHistoryUrl(nextPage);
    tracking.currentPage = nextPage;
    tracking.lastActivityAt = Date.now();
    tracking.lastMessage = `Opening Amazon order-history page ${nextPage}.`;
    await saveTracking(tracking, windowId);
    await log(`Opening Amazon order-history page ${nextPage}.`, windowId);
    await openTrackingRunUrl(tracking, windowId, tracking.nextUrl || orderHistoryUrl(nextPage));
    return true;
  }
  tracking.running = false;
  tracking.finishedAt = Date.now();
  tracking.lastMessage = `All tracking codes scanned. Track all checked ${tracking.completedOrderIds?.length || 0} order(s), failed ${tracking.failedOrderIds?.length || 0}.`;
  await saveTracking(tracking, windowId);
  await log(tracking.lastMessage, windowId);
  await clearWatchdogIfIdle();
  return false;
}

async function openHistoryCurrentOrder(windowId) {
  const { tracking } = await getWindowState(windowId);
  if (!tracking.running || tracking.source !== "history") return;
  if (!tracking.currentOrder) {
    tracking.currentOrder = (tracking.queue || []).shift() || null;
  }
  if (!tracking.currentOrder) {
    await maybeOpenNextHistoryPage(tracking, windowId);
    return;
  }
  const order = tracking.currentOrder;
  order.packageIndex = Number(order.packageIndex || 0);
  order.capturedPackages = Array.isArray(order.capturedPackages) ? order.capturedPackages : [];
  tracking.lastActivityAt = Date.now();
  tracking.lastMessage = `Tracking Amazon order ${order.amazon_order_id}.`;
  if (Array.isArray(order.packages) && order.packages.length) {
    const pkg = order.packages[order.packageIndex] || order.packages[0];
    tracking.currentStep = "history_package";
    tracking.currentUrl = pkg.tracking_url || order.amazon_order_url || orderUrl(order);
    await saveTracking(tracking, windowId);
    await log(`Opening tracking page ${order.packageIndex + 1}/${order.packages.length} for ${order.amazon_order_id}.`, windowId);
    await openTrackingRunUrl(tracking, windowId, tracking.currentUrl);
    return;
  }
  tracking.currentStep = "history_order";
  tracking.currentUrl = order.amazon_order_url || orderUrl(order);
  await saveTracking(tracking, windowId);
  await log(`Opening order details for ${order.amazon_order_id} to discover package links.`, windowId);
  await openTrackingRunUrl(tracking, windowId, tracking.currentUrl);
}

async function postHistoryOrderTracking(tracking, windowId, status = "checked") {
  const order = tracking.currentOrder;
  if (!order?.amazon_order_id) return;
  const packages = (order.capturedPackages || order.packages || []).filter(Boolean);
  const statusOnly = packages.length > 0 && packages.every((pkg) => pkg.status_only && !pkg.tracking_id);
  try {
    await postGuardedTrackingUpdate(
      order.amazon_order_id,
      {
        amazon_order_url: order.amazon_order_url || orderUrl(order),
        recipient: order.recipient || "",
        order_status: order.status || "",
        order_date: order.order_date || "",
        otp: packages.find((pkg) => pkg.otp)?.otp || "",
        products: order.products || order.items || [],
        items: order.items || order.products || [],
        packages: packages.length ? packages : [{
          amazon_order_id: order.amazon_order_id,
          status_only: true,
          status: order.status || "Unknown",
          promise: order.status || "",
          expected_delivery_date: order.expected_delivery_date || "",
          expected_delivery_display: order.expected_delivery_display || "",
          tracking_url: order.amazon_order_url || orderUrl(order),
          asins: order.asins || [],
          products: order.products || order.items || [],
        }],
      },
      { timeoutMs: statusOnly ? 8000 : 25000, retries: 0 },
      windowId,
    );
    await rememberRecentCheck(order.amazon_order_id, status);
    return true;
  } catch (error) {
    if (isTrackedOrderNotFoundError(error)) {
      await logUnmatchedTrackingOrder(order.amazon_order_id, windowId, "Track all");
      return false;
    }
    throw error;
  }
}

function activeHistoryRedirectUrl(tracking = {}) {
  const order = tracking.currentOrder || null;
  if (String(tracking.currentUrl || "").trim()) return String(tracking.currentUrl).trim();
  if (!order?.amazon_order_id) return "";
  const packages = Array.isArray(order.packages) ? order.packages : [];
  const packageIndex = Math.max(0, Number(order.packageIndex || 0));
  const packageUrl = packages[packageIndex]?.tracking_url || packages[0]?.tracking_url || "";
  return packageUrl || order.amazon_order_url || orderUrl(order);
}

async function advanceHistoryOrder(tracking, windowId, status = "checked") {
  const order = tracking.currentOrder;
  if (order?.amazon_order_id) {
    if (status === "failed") {
      tracking.failedOrderIds = [...(tracking.failedOrderIds || []), order.amazon_order_id];
    } else {
      tracking.completedOrderIds = [...(tracking.completedOrderIds || []), order.amazon_order_id];
    }
  }
  tracking.currentOrder = null;
  tracking.redirectRecoveryKey = "";
  tracking.redirectRecoveryCount = 0;
  tracking.currentStep = "";
  tracking.currentUrl = "";
  tracking.lastActivityAt = Date.now();
  await saveTracking(tracking, windowId);
  await openHistoryCurrentOrder(windowId);
}

async function forceAdvanceHistoryOrder(message, windowId) {
  const { tracking } = await getWindowState(windowId);
  const messageOrderId = String(message.amazonOrderId || "").trim();
  if (!tracking.running) {
    return { ok: false, message: "Tracking is not running." };
  }
  if (tracking.source !== "history") {
    const order = tracking.orders?.[Number(tracking.index || 0)];
    if (!order?.amazon_order_id) {
      await openCurrentOrder(windowId);
      return { ok: true, message: "Tracking had no active order; opened the next saved item." };
    }
    if (messageOrderId && order.amazon_order_id !== messageOrderId) {
      return { ok: true, ignored: true, message: `Ignored stale force-advance for ${messageOrderId}; active order is ${order.amazon_order_id}.` };
    }
    const status = String(message.status || "failed").trim() || "failed";
    await log(`Force advancing tracking past ${order.amazon_order_id}: ${message.reason || status}.`, windowId);
    await advanceCurrentOrder(tracking, windowId, status);
    return { ok: true, message: `Advanced past ${order.amazon_order_id}.` };
  }
  const order = tracking.currentOrder;
  if (!order?.amazon_order_id) {
    await openHistoryCurrentOrder(windowId);
    return { ok: true, message: "Track all had no active order; opened the next saved item." };
  }
  if (messageOrderId && order.amazon_order_id !== messageOrderId) {
    return { ok: true, ignored: true, message: `Ignored stale force-advance for ${messageOrderId}; active order is ${order.amazon_order_id}.` };
  }
  const status = String(message.status || "failed").trim() || "failed";
  await log(`Force advancing Track all past ${order.amazon_order_id}: ${message.reason || status}.`, windowId);
  await advanceHistoryOrder(tracking, windowId, status);
  return { ok: true, message: `Advanced past ${order.amazon_order_id}.` };
}

async function handleHistoryTrackPage(message, windowId) {
  const { tracking } = await getWindowState(windowId);
  if (!tracking.running || tracking.source !== "history") return { ok: false, message: "Track all is not running." };
  if (message.runId && Number(message.runId) !== Number(tracking.startedAt || 0)) {
    return { ok: false, message: "Ignored stale order-history scan from a previous Track all run." };
  }
  if (!Array.isArray(message.orders) || (!message.orders.length && !message.nextUrl)) {
    return handleHistoryTrackProblem(
      {
        ...message,
        message: message.message || "Amazon order-history page had no orders and no next-page link.",
      },
      windowId,
    );
  }
  const seen = new Set(tracking.seenOrderIds || []);
  const queue = Array.isArray(tracking.queue) ? tracking.queue : [];
  let added = 0;
  let skippedCancelled = 0;
  let skippedPaymentRevision = 0;
  const addedRawOrders = [];
  for (const rawOrder of message.orders || []) {
    const normalized = normalizeHistoryOrder(rawOrder);
    if (!normalized) continue;
    const alreadySeen = seen.has(normalized.amazon_order_id);
    if (alreadySeen && !normalized.cancelled && !normalized.payment_revision_needed) continue;
    seen.add(normalized.amazon_order_id);
    if (normalized.cancelled) {
      skippedCancelled += 1;
      tracking.completedOrderIds = [...(tracking.completedOrderIds || []), normalized.amazon_order_id];
      rememberRecentCheck(normalized.amazon_order_id, "cancelled").catch(() => {});
      postGuardedTrackingUpdate(
        normalized.amazon_order_id,
        {
          amazon_order_url: normalized.amazon_order_url || orderUrl(normalized),
          recipient: normalized.recipient || "",
          order_status: normalized.status || "",
          order_date: normalized.order_date || "",
          products: normalized.products || normalized.items || [],
          items: normalized.items || normalized.products || [],
          packages: [],
          order_cancelled: true,
          cancellation_message: normalized.status || "Cancelled order from Amazon order-history page.",
        },
        { timeoutMs: 8000, retries: 0 },
        windowId,
      ).catch((error) => log(`Could not save cancelled history order ${normalized.amazon_order_id}: ${error.message}; skipped page open.`, windowId));
      continue;
    }
    if (normalized.payment_revision_needed) {
      skippedPaymentRevision += 1;
      tracking.completedOrderIds = [...(tracking.completedOrderIds || []), normalized.amazon_order_id];
      rememberRecentCheck(normalized.amazon_order_id, "payment_revision").catch(() => {});
      postGuardedTrackingUpdate(
        normalized.amazon_order_id,
        {
          amazon_order_url: normalized.amazon_order_url || orderUrl(normalized),
          recipient: normalized.recipient || "",
          order_status: normalized.status || "",
          order_date: normalized.order_date || "",
          products: normalized.products || normalized.items || [],
          items: normalized.items || normalized.products || [],
          packages: Array.isArray(rawOrder.packages) ? rawOrder.packages : [],
          payment_revision_needed: true,
          payment_revision_url: normalized.payment_revision_url || "",
          page_text: normalized.page_text || normalized.status || "Payment revision needed. Please update your payment method.",
        },
        { timeoutMs: 12000, retries: 0 },
        windowId,
      ).catch((error) => log(`Could not save payment revision history order ${normalized.amazon_order_id}: ${error.message}; skipped page open.`, windowId));
      continue;
    }
    addedRawOrders.push(rawOrder);
    queue.push({
      ...normalized,
      products: normalized.products || normalized.items || [],
      packages: Array.isArray(rawOrder.packages) ? rawOrder.packages : [],
      packageIndex: 0,
      capturedPackages: [],
    });
    added += 1;
  }
  tracking.seenOrderIds = [...seen].slice(-5000);
  tracking.queue = queue;
  const nextPage = Number(tracking.currentPage || tracking.startPage || 1) + 1;
  tracking.nextUrl = normalizeHistoryNextUrl(message.nextUrl || "", nextPage);
  tracking.pagesScanned = Number(tracking.pagesScanned || 0) + 1;
  tracking.lastActivityAt = Date.now();
  tracking.lastMessage = `Scanned history page ${tracking.currentPage || ""}: ${added} order(s) queued${skippedCancelled ? `, ${skippedCancelled} cancelled skipped` : ""}${skippedPaymentRevision ? `, ${skippedPaymentRevision} payment revision reported` : ""}; preparing matches in background.`;
  await saveTracking(tracking, windowId);
  await log(`History page scanned: queued ${added} order(s)${skippedCancelled ? `, skipped ${skippedCancelled} cancelled order(s)` : ""}${skippedPaymentRevision ? `, reported ${skippedPaymentRevision} payment revision order(s)` : ""}; matching continues in background.`, windowId);
  if (!tracking.currentOrder) await openHistoryCurrentOrder(windowId);
  if (addedRawOrders.length) {
    syncHistoryOrdersToAppBatch(addedRawOrders, windowId).catch((error) => log(`Batch Track all preparation failed: ${error.message}`, windowId));
  }
  return { ok: true, added, matched: 0, preparing: addedRawOrders.length };
}

async function handleHistoryTrackProblem(message, windowId) {
  const { tracking } = await getWindowState(windowId);
  if (!tracking.running || tracking.source !== "history") return { ok: false, message: "Track all is not running." };
  if (message.runId && Number(message.runId) !== Number(tracking.startedAt || 0)) {
    return { ok: false, message: "Ignored stale order-history problem from a previous Track all run." };
  }
  const page = Number(tracking.currentPage || tracking.startPage || 1);
  const reason = String(message.message || "Amazon could not display this order-history page.").trim();
  tracking.historyProblemCount = Number(tracking.historyProblemCount || 0) + 1;
  tracking.historyProblemUrl = String(message.pageUrl || tracking.currentUrl || "");
  tracking.lastActivityAt = Date.now();
  tracking.lastMessage = `${reason} Track all paused at history page ${page}; restart later from this page.`;
  if (tracking.currentOrder?.amazon_order_id || (tracking.queue || []).length) {
    tracking.maxPages = Number(tracking.pagesScanned || 0);
    await saveTracking(tracking, windowId);
    await log(`${tracking.lastMessage} Finishing already queued order(s) first.`, windowId);
    await openHistoryCurrentOrder(windowId);
    return {
      ok: true,
      paused: true,
      message: `${reason} Finishing already queued order(s); history paging will stop after the queue.`,
    };
  }
  tracking.running = false;
  tracking.finishedAt = Date.now();
  await saveTracking(tracking, windowId);
  await log(tracking.lastMessage, windowId);
  await clearWatchdogIfIdle();
  return {
    ok: true,
    paused: true,
    message: tracking.lastMessage,
  };
}

async function handleHistoryOrderPackages(message, windowId) {
  const { tracking } = await getWindowState(windowId);
  const order = tracking.currentOrder;
  if (!tracking.running || tracking.source !== "history" || !order || order.amazon_order_id !== message.amazonOrderId) return null;
  if (message.orderCancelled || message.paymentRevisionNeeded) {
    try {
      await postGuardedTrackingUpdate(
        order.amazon_order_id,
        {
          amazon_order_url: order.amazon_order_url || orderUrl(order),
          recipient: order.recipient || "",
          order_status: order.status || "",
          order_date: order.order_date || "",
          products: message.products || order.products || order.items || [],
          items: message.products || order.items || order.products || [],
          packages: message.packages || [],
          order_cancelled: Boolean(message.orderCancelled),
          cancellation_message: message.cancellationMessage || "",
          payment_revision_needed: Boolean(message.paymentRevisionNeeded),
          payment_revision_url: message.paymentRevisionUrl || "",
          page_text: message.pageText || "",
        },
        { timeoutMs: 12000, retries: 0 },
        windowId,
      );
    } catch (error) {
      await log(`Could not save ${message.orderCancelled ? "cancelled" : "payment revision"} status for ${order.amazon_order_id}: ${error.message}; continuing Track all.`, windowId);
    }
    await advanceHistoryOrder(tracking, windowId, message.orderCancelled ? "cancelled" : "payment_revision");
    return { ok: true };
  }
  order.packages = Array.isArray(message.packages) ? message.packages : [];
  order.products = Array.isArray(message.products) ? message.products : order.products || [];
  order.items = Array.isArray(message.products) ? message.products : order.items || order.products || [];
  order.status = message.orderStatus || message.promise || order.status || "";
  order.packageIndex = 0;
  order.capturedPackages = [];
  tracking.currentOrder = order;
  tracking.lastActivityAt = Date.now();
  await saveTracking(tracking, windowId);
  if (!order.packages.length) {
    try {
      const posted = await postHistoryOrderTracking(tracking, windowId, "checked");
      await log(posted ? `No tracking links found for ${order.amazon_order_id}; saved order status.` : `No tracking links found for ${order.amazon_order_id}; skipped because it is not matched in the app.`, windowId);
      await advanceHistoryOrder(tracking, windowId, posted ? "checked" : "failed");
    } catch (error) {
      await log(`Could not save order-page status for ${order.amazon_order_id}: ${error.message}; continuing Track all.`, windowId);
      await advanceHistoryOrder(tracking, windowId, "failed");
      return { ok: false, message: `Could not save ${order.amazon_order_id}; skipped and continued. ${error.message}` };
    }
    return { ok: true };
  }
  await openHistoryCurrentOrder(windowId);
  return { ok: true };
}

async function handleHistoryPackageTracking(message, windowId) {
  const { tracking } = await getWindowState(windowId);
  const order = tracking.currentOrder;
  if (!tracking.running || tracking.source !== "history" || !order || order.amazon_order_id !== message.amazonOrderId) return null;
  if (Number(order.packageIndex || 0) >= (order.packages || []).length && (order.capturedPackages || []).length) {
    await log(`Ignored duplicate Track all package retry for ${order.amazon_order_id}; post is already in progress.`, windowId);
    return { ok: true, duplicate: true, message: "Package retry ignored because this order is already posting." };
  }
  const queuedPackage = order.packages?.[Number(order.packageIndex || 0)] || {};
  const pagePackage = message.package || {};
  const shipmentMismatch = splitShipmentMismatch(queuedPackage, pagePackage);
  if (shipmentMismatch) {
    const retryCount = Number(order.shipmentMismatchRetries || 0) + 1;
    order.shipmentMismatchRetries = retryCount;
    tracking.currentOrder = order;
    tracking.lastActivityAt = Date.now();
    tracking.lastMessage = `Blocked split-shipment mismatch for ${order.amazon_order_id}: expected ${shipmentMismatch.expected}, loaded ${shipmentMismatch.actual}.`;
    await saveTracking(tracking, windowId);
    await log(tracking.lastMessage, windowId);
    if (retryCount >= 2) {
      await advanceHistoryOrder(tracking, windowId, "failed");
      return { ok: false, blocked: true, message: `${tracking.lastMessage} Order skipped after two safe retries.` };
    }
    await openHistoryCurrentOrder(windowId);
    return { ok: true, blocked: true, recovering: true, message: `${tracking.lastMessage} Reopening the expected shipment.` };
  }
  order.shipmentMismatchRetries = 0;
  const packageData = { ...queuedPackage, ...pagePackage };
  sanitizePackageTrackingIdentity(packageData);
  if (Array.isArray(queuedPackage.asins) && queuedPackage.asins.length) packageData.asins = queuedPackage.asins;
  packageData.products = mergePackageProducts(
    Array.isArray(queuedPackage.products) ? queuedPackage.products : [],
    Array.isArray(pagePackage.products) ? pagePackage.products : [],
  );
  if (message.otp || pagePackage.otp) {
    packageData.otp = message.otp || pagePackage.otp || "";
    packageData.otp_required = true;
  }
  order.capturedPackages = Array.isArray(order.capturedPackages) ? order.capturedPackages : [];
  order.capturedPackages[Number(order.packageIndex || 0)] = packageData;
  order.packageIndex = Number(order.packageIndex || 0) + 1;
  tracking.currentOrder = order;
  tracking.lastActivityAt = Date.now();
  tracking.lastMessage = `Captured ${packageData.tracking_id || "tracking"} for ${order.amazon_order_id}.`;
  await saveTracking(tracking, windowId);
  await log(`Captured ${packageData.carrier || "carrier"} ${packageData.tracking_id || ""} for ${order.amazon_order_id}.`, windowId);
  if (order.packageIndex < (order.packages || []).length) {
    await openHistoryCurrentOrder(windowId);
    return { ok: true };
  }
  try {
    const posted = await postHistoryOrderTracking(tracking, windowId, "checked");
    const statusOnly = packageData.status_only ? " status-only" : "";
    await log(posted ? `Posted${statusOnly} Track all update for ${order.amazon_order_id}.` : `Skipped unmatched${statusOnly} Track all update for ${order.amazon_order_id}.`, windowId);
    await advanceHistoryOrder(tracking, windowId, posted ? "checked" : "failed");
    return { ok: true };
  } catch (error) {
    await log(`Could not post Track all update for ${order.amazon_order_id}: ${error.message}; continuing.`, windowId);
    await advanceHistoryOrder(tracking, windowId, "failed");
    return { ok: false, message: `Could not post ${order.amazon_order_id}; skipped and continued. ${error.message}` };
  }
}

async function recoverHistoryFromStalePackagePage(message, windowId) {
  const { tracking } = await getWindowState(windowId);
  if (!tracking.running || tracking.source !== "history") return null;
  const pageOrderId = String(message.amazonOrderId || "").trim();
  const activeOrderId = String(tracking.currentOrder?.amazon_order_id || "").trim();
  if (activeOrderId && pageOrderId && activeOrderId === pageOrderId) return null;
  tracking.lastActivityAt = Date.now();
  tracking.lastMessage = activeOrderId
    ? `Ignored stale Amazon page ${pageOrderId || "unknown"}; reopening active Track all order ${activeOrderId}.`
    : `Ignored stale Amazon page ${pageOrderId || "unknown"}; opening the next Track all order.`;
  const redirectUrl = activeHistoryRedirectUrl(tracking);
  await saveTracking(tracking, windowId);
  await log(tracking.lastMessage, windowId);
  await openHistoryCurrentOrder(windowId);
  return {
    ok: true,
    ignored: true,
    recovering: true,
    redirectUrl,
    message: activeOrderId
      ? `Ignored stale Amazon page; reopened active Track all order ${activeOrderId}.`
      : "Ignored stale Amazon page; opened the next Track all order.",
  };
}

async function startHeadlessTracking() {
  const result = await api("/api/tracking/browserless/run", {
    method: "POST",
    body: JSON.stringify({ worker_id: `tracking-extension-${chrome.runtime.id || "local"}` }),
    timeoutMs: 10000,
  });
  await log(result.message || "Headless tracking started.");
  return result;
}

async function stopHeadlessTracking() {
  const result = await api("/api/tracking/browserless/stop", {
    method: "POST",
    body: JSON.stringify({}),
    timeoutMs: 10000,
  });
  await log(result.message || "Headless tracking stop requested.");
  return result;
}

async function headlessTrackingStatus() {
  return api("/api/tracking/browserless/status", { timeoutMs: 10000 });
}

async function headlessTrackingReadiness() {
  return api("/api/tracking/browserless/readiness", { timeoutMs: 30000 });
}

async function openHeadlessSignin() {
  const result = await api("/api/tracking/browserless/open-signin", {
    method: "POST",
    body: JSON.stringify({}),
    timeoutMs: 10000,
  });
  await log(result.message || "Opened headless Chrome sign-in window.");
  return result;
}

function messageFromActiveTrackingTab(tracking = {}, sender = {}) {
  const activeTabId = Number(tracking.trackingTabId || tracking.autoTrackingTabId || 0);
  const senderTabId = Number(sender?.tab?.id || 0);
  return !activeTabId || !senderTabId || activeTabId === senderTabId;
}

async function handleOrderPackages(message, windowId, sender = {}) {
  const { tracking, headlessTrackingMode } = await getWindowState(windowId);
  const standaloneTab = tracking.running && !messageFromActiveTrackingTab(tracking, sender);
  if (standaloneTab) {
    const amazonOrderId = String(message.amazonOrderId || "").trim();
    if (!amazonOrderId) return { ok: false, message: "Could not detect the Amazon order id on this order page." };
    try {
      await postGuardedTrackingUpdate(
        amazonOrderId,
        {
          amazon_order_url: message.amazonOrderUrl || orderUrl({ amazon_order_id: amazonOrderId }),
          recipient: message.recipient || "",
          order_status: message.orderStatus || "",
          products: message.products || [],
          items: message.products || [],
          packages: message.packages || [],
        },
        { timeoutMs: 18000, retries: 0 },
        windowId,
      );
      await log(`Standalone Amazon order page ${amazonOrderId} posted while another tracking tab was running.`, windowId);
      return { ok: true, recovered: true, message: `Posted package/product metadata for ${amazonOrderId}.` };
    } catch (error) {
      if (isTrackedOrderNotFoundError(error)) {
        await logUnmatchedTrackingOrder(amazonOrderId, windowId, "standalone order page");
        return { ok: true, ignored: true, unmatched: true, message: `Skipped ${amazonOrderId}; it is not linked in the app.` };
      }
      await log(`Could not save standalone order page ${amazonOrderId}: ${error.message}.`, windowId);
      return { ok: false, message: error.message };
    }
  }
  const historyResult = await handleHistoryOrderPackages(message, windowId);
  if (historyResult) return historyResult;
  if ((message.paymentRevisionNeeded || message.orderCancelled) && (!tracking.running || tracking.orders?.[tracking.index]?.amazon_order_id !== message.amazonOrderId)) {
    if (headlessTrackingMode && tracking.running) {
      return { ok: true, ignored: true, message: "Headless tracking mode is active; visible Amazon pages are ignored." };
    }
    const amazonOrderId = String(message.amazonOrderId || "").trim();
    if (!amazonOrderId) return { ok: false, message: "Could not detect the Amazon order id on this order page." };
    try {
      await postGuardedTrackingUpdate(
        amazonOrderId,
        {
          amazon_order_url: message.amazonOrderUrl || orderUrl({ amazon_order_id: amazonOrderId }),
          recipient: message.recipient || "",
          order_status: message.orderStatus || "",
          products: message.products || [],
          items: message.products || [],
          packages: message.packages || [],
          order_cancelled: Boolean(message.orderCancelled),
          cancellation_message: message.cancellationMessage || "",
          payment_revision_needed: Boolean(message.paymentRevisionNeeded),
          payment_revision_url: message.paymentRevisionUrl || "",
          page_text: message.pageText || "",
        },
        { timeoutMs: 12000, retries: 0 },
        windowId,
      );
      await log(`${message.orderCancelled ? "Cancelled order" : "Payment revision"} detected on standalone order page ${amazonOrderId}; posted to app.`, windowId);
      return { ok: true, recovered: true, message: `${message.orderCancelled ? "Cancelled order" : "Payment revision"} posted to app for ${amazonOrderId}.` };
    } catch (error) {
      if (isTrackedOrderNotFoundError(error)) {
        await logUnmatchedTrackingOrder(amazonOrderId, windowId, message.orderCancelled ? "standalone cancelled order" : "standalone payment revision");
        return { ok: true, ignored: true, unmatched: true, message: `Skipped ${amazonOrderId}; it is not linked in the app.` };
      }
      await log(`Could not save standalone ${message.orderCancelled ? "cancelled" : "payment revision"} status for ${amazonOrderId}: ${error.message}.`, windowId);
      return { ok: false, message: error.message };
    }
  }
  if (!tracking.running) {
    if (headlessTrackingMode) {
      return { ok: true, ignored: true, message: "Headless tracking mode is active; visible Amazon pages are ignored." };
    }
    const amazonOrderId = String(message.amazonOrderId || "").trim();
    if (!amazonOrderId) return { ok: false, message: "Could not detect the Amazon order id on this order page." };
    try {
      await postGuardedTrackingUpdate(
        amazonOrderId,
        {
          amazon_order_url: message.amazonOrderUrl || orderUrl({ amazon_order_id: amazonOrderId }),
          recipient: message.recipient || "",
          order_status: message.orderStatus || "",
          products: message.products || [],
          items: message.products || [],
          packages: message.packages || [],
        },
        { timeoutMs: 18000, retries: 0 },
        windowId,
      );
      await log(`Standalone Amazon order page ${amazonOrderId} posted package/product metadata to the app.`, windowId);
      return { ok: true, recovered: true, message: `Posted package/product metadata for ${amazonOrderId}.` };
    } catch (error) {
      if (isTrackedOrderNotFoundError(error)) {
        await logUnmatchedTrackingOrder(amazonOrderId, windowId, "standalone order page");
        return { ok: true, ignored: true, unmatched: true, message: `Skipped ${amazonOrderId}; it is not linked in the app.` };
      }
      await log(`Could not save standalone order page ${amazonOrderId}: ${error.message}.`, windowId);
      return { ok: false, message: error.message };
    }
  }
  const order = tracking.orders[tracking.index];
  const activeOrderId = normalizeAmazonOrderId(order?.amazon_order_id || "");
  const pageOrderId = normalizeAmazonOrderId(message.amazonOrderId || "");
  if (!order || activeOrderId !== pageOrderId) {
    await log(`Ignored Amazon order page ${pageOrderId || message.amazonOrderId || "unknown"}; active order is ${activeOrderId || order?.amazon_order_id || "none"}.`, windowId);
    if (headlessTrackingMode) {
      return { ok: true, ignored: true, message: "Headless tracking mode is active; visible Amazon pages are ignored." };
    }
    const recoveryKey = `${activeOrderId || "none"}|${pageOrderId || "unknown"}|${String(message.amazonOrderUrl || "").slice(0, 240)}`;
    tracking.mismatchRecoveryCount = tracking.mismatchRecoveryKey === recoveryKey
      ? Number(tracking.mismatchRecoveryCount || 0) + 1
      : 1;
    tracking.mismatchRecoveryKey = recoveryKey;
    if (order?.amazon_order_id && tracking.mismatchRecoveryCount >= 3) {
      const skippedOrderId = order.amazon_order_id;
      await log(`Skipped Amazon order ${skippedOrderId} after repeated stale page recovery; last page order was ${pageOrderId || "unknown"}.`, windowId);
      await advanceCurrentOrder(tracking, windowId, "failed");
      return {
        ok: true,
        ignored: true,
        recovering: true,
        message: `Skipped ${skippedOrderId} after repeated stale Amazon pages; moving to next order.`,
      };
    }
    await saveTracking(tracking, windowId);
    await openCurrentOrder(windowId);
    return {
      ok: true,
      ignored: true,
      recovering: true,
      message: order?.amazon_order_id
        ? `Ignored stale Amazon order page; reopened active order ${order.amazon_order_id}.`
        : "Ignored stale Amazon order page; opened the next active order.",
    };
  }
  tracking.mismatchRecoveryKey = "";
  tracking.mismatchRecoveryCount = 0;
  if (message.orderCancelled) {
    let cancellationResult = null;
    try {
      cancellationResult = await postGuardedTrackingUpdate(
        order.amazon_order_id,
        {
          amazon_order_url: message.amazonOrderUrl || orderUrl(order),
          recipient: order.recipient || message.recipient || "",
          order_status: message.orderStatus || order.status || "",
          products: message.products || order.products || order.items || [],
          items: message.products || order.items || order.products || [],
          packages: message.packages || [],
          order_cancelled: true,
          cancellation_message: message.cancellationMessage || "This order has been cancelled.",
          page_text: message.pageText || "",
        },
        { timeoutMs: 12000, retries: 0 },
        windowId,
      );
    } catch (error) {
      await log(`Could not save cancelled status for ${order.amazon_order_id}: ${error.message}; continuing.`, windowId);
    }
    if (Number(cancellationResult?.manual_review || 0) > 0) {
      await log(`Amazon order ${order.amazon_order_id} is cancelled; completed downstream fulfilment was kept and flagged for manual review.`, windowId);
    } else if (cancellationResult) {
      await log(`Amazon order ${order.amazon_order_id} is cancelled; reset lines for reorder.`, windowId);
    }
    await advanceCurrentOrder(tracking, windowId, "cancelled");
    return { ok: true, cancellation: cancellationResult };
  }
  if (message.paymentRevisionNeeded) {
    try {
      await postGuardedTrackingUpdate(
        order.amazon_order_id,
        {
          amazon_order_url: message.amazonOrderUrl || orderUrl(order),
          recipient: order.recipient || message.recipient || "",
          order_status: message.orderStatus || order.status || "",
          products: message.products || order.products || order.items || [],
          items: message.products || order.items || order.products || [],
          packages: message.packages || [],
          payment_revision_needed: true,
          payment_revision_url: message.paymentRevisionUrl || "",
          page_text: message.pageText || "",
        },
        { timeoutMs: 12000, retries: 0 },
        windowId,
      );
    } catch (error) {
      await log(`Could not save payment revision status for ${order.amazon_order_id}: ${error.message}; continuing.`, windowId);
    }
    await log(`Payment revision needed for ${order.amazon_order_id}; posted to Payment Failed page.`, windowId);
    await advanceCurrentOrder(tracking, windowId, "payment_revision");
    return { ok: true };
  }
  tracking.packages = message.packages || [];
  order.products = Array.isArray(message.products) ? message.products : order.products || [];
  order.items = Array.isArray(message.products) ? message.products : order.items || order.products || [];
  order.status = message.orderStatus || message.promise || order.status || "";
  tracking.orders[tracking.index] = order;
  tracking.packageIndex = 0;
  tracking.lastActivityAt = Date.now();
  tracking.currentStep = "packages";
  tracking.lastMessage = `Found ${tracking.packages.length} package link(s) for ${order.amazon_order_id}.`;
  await saveTracking(tracking, windowId);
  await log(`Order page parsed for ${order.amazon_order_id}: ${tracking.packages.length} package link(s).`, windowId);
  if (!tracking.packages.length) {
    const products = Array.isArray(message.products) ? message.products : [];
    try {
      await postGuardedTrackingUpdate(
        order.amazon_order_id,
        {
          amazon_order_url: orderUrl(order),
          recipient: order.recipient || "",
          order_status: order.status || "",
          order_date: order.order_date || "",
          products,
          items: products,
          packages: [{
            amazon_order_id: order.amazon_order_id,
            status_only: true,
            status: message.orderStatus || "Unknown",
            promise: message.promise || "",
            expected_delivery_date: message.expected_delivery_date || "",
            expected_delivery_display: message.expected_delivery_display || "",
            tracking_url: orderUrl(order),
            asins: products.map((item) => item.asin).filter(Boolean),
            products,
          }],
        },
        { timeoutMs: 8000, retries: 0 },
        windowId,
      );
      await log(`No tracking buttons found for ${order.amazon_order_id}; saved order-page status.`, windowId);
      await advanceCurrentOrder(tracking, windowId, "checked");
    } catch (error) {
      if (!isTrackedOrderNotFoundError(error)) throw error;
      await logUnmatchedTrackingOrder(order.amazon_order_id, windowId, "active status-only update");
      await advanceCurrentOrder(tracking, windowId, "unmatched");
    }
    return { ok: true };
  }
  await log(`Found ${tracking.packages.length} package link(s) for ${order.amazon_order_id}.`, windowId);
  tracking.currentStep = "package";
  tracking.currentUrl = tracking.packages[0].tracking_url;
  tracking.lastActivityAt = Date.now();
  await saveTracking(tracking, windowId);
  const nextUrl = tracking.packages[0].tracking_url;
  setTimeout(() => {
    openTrackingRunUrl(tracking, windowId, nextUrl).catch((error) => log(`Could not open tracking page for ${order.amazon_order_id}: ${error.message}`, windowId));
  }, 0);
  return { ok: true };
}

async function handlePackageTracking(message, windowId, sender = {}) {
  const { tracking, headlessTrackingMode } = await getWindowState(windowId);
  if (tracking.running && !messageFromActiveTrackingTab(tracking, sender)) {
    return postStandalonePackageTracking(message, windowId);
  }
  const historyResult = await handleHistoryPackageTracking(message, windowId);
  if (historyResult) return historyResult;
  const historyRecovery = await recoverHistoryFromStalePackagePage(message, windowId);
  if (historyRecovery) return historyRecovery;
  if (!tracking.running && headlessTrackingMode) return postStandalonePackageTracking(message, windowId);
  if (!tracking.running) return postStandalonePackageTracking(message, windowId);
  if (tracking.source === "history") {
    const activeOrderId = tracking.currentOrder?.amazon_order_id || "";
    const redirectUrl = activeHistoryRedirectUrl(tracking);
    await log(`Ignored Amazon tracking page ${message.amazonOrderId || "unknown"}; reopening active Track all order ${activeOrderId || "next"}.`, windowId);
    await openHistoryCurrentOrder(windowId);
    return {
      ok: true,
      ignored: true,
      recovering: true,
      redirectUrl,
      message: activeOrderId ? `Ignored stale Amazon page; reopened active Track all order ${activeOrderId}.` : "Ignored stale Amazon page; opened the next Track all order.",
    };
  }
  const order = tracking.orders[tracking.index];
  if (!order || order.amazon_order_id !== message.amazonOrderId) {
    await log(`Ignored Amazon tracking page ${message.amazonOrderId || "unknown"}; active order is ${order?.amazon_order_id || "none"}.`, windowId);
    return { ok: true, ignored: true, message: order?.amazon_order_id ? `Ignored stale Amazon page; active order is ${order.amazon_order_id}.` : "Ignored stale Amazon page while tracking is running." };
  }
  if (Number(tracking.packageIndex || 0) >= (tracking.packages || []).length && (tracking.packages || []).length) {
    await log(`Ignored duplicate package retry for ${order.amazon_order_id}; post is already in progress.`, windowId);
    return { ok: true, duplicate: true, message: "Package retry ignored because this order is already posting." };
  }
  const queuedPackage = tracking.packages[tracking.packageIndex] || {};
  const pagePackage = message.package || {};
  const shipmentMismatch = splitShipmentMismatch(queuedPackage, pagePackage);
  if (shipmentMismatch) {
    const retryCount = Number(tracking.shipmentMismatchRetries || 0) + 1;
    tracking.shipmentMismatchRetries = retryCount;
    tracking.lastActivityAt = Date.now();
    tracking.lastMessage = `Blocked split-shipment mismatch for ${order.amazon_order_id}: expected ${shipmentMismatch.expected}, loaded ${shipmentMismatch.actual}.`;
    await saveTracking(tracking, windowId);
    await log(tracking.lastMessage, windowId);
    if (retryCount >= 2) {
      await advanceCurrentOrder(tracking, windowId, "failed");
      return { ok: false, blocked: true, message: `${tracking.lastMessage} Order skipped after two safe retries.` };
    }
    const expectedUrl = queuedPackage.tracking_url;
    setTimeout(() => {
      openTrackingRunUrl(tracking, windowId, expectedUrl).catch((error) => log(`Could not reopen expected shipment for ${order.amazon_order_id}: ${error.message}`, windowId));
    }, 0);
    return { ok: true, blocked: true, recovering: true, message: `${tracking.lastMessage} Reopening the expected shipment.` };
  }
  tracking.shipmentMismatchRetries = 0;
  const packageData = { ...queuedPackage, ...pagePackage };
  sanitizePackageTrackingIdentity(packageData);
  if (Array.isArray(queuedPackage.asins) && queuedPackage.asins.length) {
    packageData.asins = queuedPackage.asins;
  }
  packageData.products = mergePackageProducts(
    Array.isArray(queuedPackage.products) ? queuedPackage.products : [],
    Array.isArray(pagePackage.products) ? pagePackage.products : [],
  );
  if (message.paymentRevisionNeeded) {
    packageData.payment_revision_needed = true;
    packageData.payment_revision_url = message.paymentRevisionUrl || "";
    packageData.page_text = message.pageText || "";
  }
  if (message.otp || pagePackage.otp) {
    packageData.otp = message.otp || pagePackage.otp || "";
    packageData.otp_required = true;
  }
  tracking.packages[tracking.packageIndex] = packageData;
  tracking.packageIndex += 1;
  tracking.lastActivityAt = Date.now();
  tracking.lastMessage = `Captured ${packageData.carrier || "carrier"} ${packageData.tracking_id || ""} for ${order.amazon_order_id}.`;
  await saveTracking(tracking, windowId);
  await log(`Captured ${packageData.carrier || "carrier"} ${packageData.tracking_id || ""} for ${order.amazon_order_id}.`, windowId);
  if (tracking.packageIndex < tracking.packages.length) {
    tracking.currentStep = "package";
    tracking.currentUrl = tracking.packages[tracking.packageIndex].tracking_url;
    tracking.lastActivityAt = Date.now();
    await saveTracking(tracking, windowId);
    const nextUrl = tracking.packages[tracking.packageIndex].tracking_url;
    setTimeout(() => {
      openTrackingRunUrl(tracking, windowId, nextUrl).catch((error) => log(`Could not open next tracking page for ${order.amazon_order_id}: ${error.message}`, windowId));
    }, 0);
    return { ok: true };
  }
  try {
    await postGuardedTrackingUpdate(
      order.amazon_order_id,
      {
        amazon_order_url: orderUrl(order),
        recipient: order.recipient || "",
        order_status: order.status || "",
        order_date: order.order_date || "",
        otp: tracking.packages.find((pkg) => pkg.otp)?.otp || "",
        products: order.products || order.items || [],
        items: order.items || order.products || [],
        packages: tracking.packages,
        payment_revision_needed: tracking.packages.some((pkg) => pkg.payment_revision_needed),
        payment_revision_url: tracking.packages.find((pkg) => pkg.payment_revision_url)?.payment_revision_url || "",
        page_text: tracking.packages.find((pkg) => pkg.page_text)?.page_text || "",
      },
      { timeoutMs: tracking.packages?.some((pkg) => pkg.status_only && !pkg.tracking_id) ? 8000 : 25000, retries: 0 },
      windowId,
    );
    await log(`Posted tracking update for ${order.amazon_order_id}.`, windowId);
    await advanceCurrentOrder(tracking, windowId, "checked");
  } catch (error) {
    if (!isTrackedOrderNotFoundError(error)) throw error;
    await logUnmatchedTrackingOrder(order.amazon_order_id, windowId, "active tracking update");
    await advanceCurrentOrder(tracking, windowId, "unmatched");
  }
  return { ok: true };
}

async function postStandalonePackageTracking(message, windowId) {
  const amazonOrderId = String(message.amazonOrderId || "").trim();
  const packageData = message.package || {};
  if (!amazonOrderId || !Object.keys(packageData).length) {
    return { ok: false, message: "No active tracking run matched this Amazon package page." };
  }
  const payloadPackage = { ...packageData, tracking_url: packageData.tracking_url || `https://www.amazon.com/your-orders/order-details?orderID=${encodeURIComponent(amazonOrderId)}` };
  sanitizePackageTrackingIdentity(payloadPackage);
  if (message.paymentRevisionNeeded) {
    payloadPackage.payment_revision_needed = true;
    payloadPackage.payment_revision_url = message.paymentRevisionUrl || "";
    payloadPackage.page_text = message.pageText || "";
  }
  if (message.otp || payloadPackage.otp) {
    payloadPackage.otp = message.otp || payloadPackage.otp || "";
    payloadPackage.otp_required = true;
  }
  const statusOnly = Boolean(payloadPackage.status_only && !payloadPackage.tracking_id);
  try {
    await postGuardedTrackingUpdate(
      amazonOrderId,
      {
        amazon_order_url: `https://www.amazon.com/your-orders/order-details?orderID=${encodeURIComponent(amazonOrderId)}`,
        otp: payloadPackage.otp || "",
        packages: [payloadPackage],
        payment_revision_needed: Boolean(payloadPackage.payment_revision_needed),
        payment_revision_url: payloadPackage.payment_revision_url || "",
        page_text: payloadPackage.page_text || "",
      },
      { timeoutMs: statusOnly ? 8000 : 25000, retries: 0 },
      windowId,
    );
  } catch (error) {
    if (!isTrackedOrderNotFoundError(error)) throw error;
    await logUnmatchedTrackingOrder(amazonOrderId, windowId, "standalone package page");
    return { ok: true, ignored: true, unmatched: true, message: `Skipped ${amazonOrderId}; it is not linked in the app.` };
  }
  await log(`Posted standalone tracking update for ${amazonOrderId}; recovered from a stale extension queue.`, windowId);
  return { ok: true, recovered: true, message: `Posted standalone tracking update for ${amazonOrderId}.` };
}

async function recoverStaleTrackingRun(tracking, windowId) {
  if (!tracking?.running) return false;
  const { autoTrackingEnabled } = await getState();
  if (tracking.source === "auto" && autoTrackingEnabled !== true) {
    await saveTracking(stoppedTrackingRun(tracking, "Amazon auto tracking is off; watchdog stopped this saved auto run."), windowId);
    await log("Stopped saved Amazon auto run because auto tracking is off.", windowId);
    await clearWatchdogIfIdle();
    return true;
  }
  const lastActivityAt = Number(tracking.lastActivityAt || tracking.updatedAt || tracking.startedAt || 0);
  if (!lastActivityAt || Date.now() - lastActivityAt < TRACKING_STEP_TIMEOUT_MS) return false;
  if (tracking.source === "history") {
    const order = tracking.currentOrder;
    if (order?.amazon_order_id) {
      await log(`Track all timed out on ${order.amazon_order_id}; skipping it for this session and continuing.`, windowId);
      tracking.failedOrderIds = [...(tracking.failedOrderIds || []), order.amazon_order_id];
      tracking.currentOrder = null;
      tracking.lastActivityAt = Date.now();
      await saveTracking(tracking, windowId);
      await openHistoryCurrentOrder(windowId);
      return true;
    }
    await log("Track all watchdog nudged the saved order-history queue.", windowId);
    tracking.lastActivityAt = Date.now();
    await saveTracking(tracking, windowId);
    await openHistoryCurrentOrder(windowId);
    return true;
  }
  const order = tracking.orders?.[tracking.index];
  if (!order) {
    tracking.running = false;
    tracking.finishedAt = Date.now();
    tracking.lastMessage = "Tracking finished after watchdog found no active order.";
    await saveTracking(tracking, windowId);
    await clearWatchdogIfIdle();
    return true;
  }
  await log(`Amazon tracking timed out on ${order.amazon_order_id}; skipping it for this session and continuing.`, windowId);
  tracking.lastMessage = `Timed out on ${order.amazon_order_id}; moving to next order.`;
  await advanceCurrentOrder(tracking, windowId, "failed");
  return true;
}

async function recoverActiveTrackingPage(message = {}, windowId = null) {
  const { tracking } = await getWindowState(windowId);
  if (!tracking?.running) {
    return { ok: true, ignored: true, message: "No active tracking run to recover." };
  }
  const activeOrderId = tracking.source === "history"
    ? tracking.currentOrder?.amazon_order_id || ""
    : tracking.orders?.[Number(tracking.index || 0)]?.amazon_order_id || "";
  const redirectedToHistory = message.reason === "active order details redirected to order history"
    && ORDER_HISTORY_PATH_RE.test(new URL(String(message.pageUrl || ORDER_HISTORY_URL)).pathname);
  if (redirectedToHistory && activeOrderId) {
    const recoveryKey = `${activeOrderId}|${String(tracking.currentUrl || "")}|${String(message.pageUrl || "")}`;
    tracking.redirectRecoveryCount = tracking.redirectRecoveryKey === recoveryKey
      ? Number(tracking.redirectRecoveryCount || 0) + 1
      : 1;
    tracking.redirectRecoveryKey = recoveryKey;
    if (tracking.redirectRecoveryCount >= 2) {
      await log(`Amazon redirected order ${activeOrderId} away from its detail page twice; skipped it without posting guessed tracking data.`, windowId);
      if (tracking.source === "history") {
        await advanceHistoryOrder(tracking, windowId, "failed");
      } else {
        await advanceCurrentOrder(tracking, windowId, "failed");
      }
      return {
        ok: true,
        skipped: true,
        message: `Amazon would not open order ${activeOrderId}; skipped it safely and continued to the next order.`,
      };
    }
    tracking.lastActivityAt = Date.now();
    tracking.lastMessage = `Amazon redirected order ${activeOrderId}; retrying its detail page once.`;
    await saveTracking(tracking, windowId);
    await log(tracking.lastMessage, windowId);
    if (tracking.source === "history") {
      await openHistoryCurrentOrder(windowId);
    } else {
      await openCurrentOrder(windowId);
    }
    return {
      ok: true,
      recovering: true,
      redirectUrl: String(tracking.currentUrl || "").trim(),
      message: tracking.lastMessage,
    };
  }
  await log(`Recovering active tracking page${activeOrderId ? ` for ${activeOrderId}` : ""}; ignored ${message.amazonOrderId || "unknown"} at ${message.pageUrl || "unknown URL"}.`, windowId);
  tracking.lastActivityAt = Date.now();
  const redirectUrl = tracking.source === "history" ? activeHistoryRedirectUrl(tracking) : String(tracking.currentUrl || "").trim();
  await saveTracking(tracking, windowId);
  if (tracking.source === "history") {
    await openHistoryCurrentOrder(windowId);
  } else {
    await openCurrentOrder(windowId);
  }
  return {
    ok: true,
    recovering: true,
    redirectUrl,
    message: activeOrderId ? `Reopened active order ${activeOrderId}.` : "Reopened active tracking run.",
  };
}

async function runTrackingWatchdog() {
  const state = await getState();
  let recovered = false;
  const windowEntries = Object.entries(state.trackingByWindow || {}).filter(([, tracking]) => tracking?.running);
  if (!windowEntries.length && state.tracking?.running) {
    recovered = await recoverStaleTrackingRun(state.tracking, null) || recovered;
  }
  for (const [key, tracking] of windowEntries) {
    recovered = await recoverStaleTrackingRun(tracking, Number(key) || null) || recovered;
  }
  if (!recovered) await clearWatchdogIfIdle();
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === AUTO_TRACKING_ALARM) {
    startAutoTracking().catch((error) => log(`Scheduled Amazon tracking failed: ${error.message}`));
  }
  if (alarm.name === TRACKING_WATCHDOG_ALARM) {
    runTrackingWatchdog().catch((error) => log(`Amazon tracking watchdog failed: ${error.message}`));
  }
});

chrome.runtime.onInstalled.addListener(() => {
  restoreAutoTrackingAlarm().catch((error) => log(`Could not restore Amazon auto tracking: ${error.message}`));
});

chrome.runtime.onStartup.addListener(() => {
  restoreAutoTrackingAlarm()
    .then(() => startAutoTracking())
    .catch((error) => log(`Could not start Amazon auto tracking after Chrome startup: ${error.message}`));
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  const url = changeInfo.url || tab?.url || "";
  if (!isAmazonTrackingExtensionUrl(url)) return;
  scheduleAmazonContentInjection(tabId, Number(tab?.windowId || 0) || null);
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    const windowId = messageWindowId(message, sender);
    if (message.type === "GET_STATE") {
      return { ...(await getWindowState(windowId)), senderTabId: Number(sender?.tab?.id || 0) || 0 };
    }
    if (message.type === "AMAZON_ACCOUNT_CONTEXT") {
      const amazonAccountName = String(message.amazonAccountName || "").replace(/\s+/g, " ").trim().slice(0, 160);
      if (amazonAccountName) await chrome.storage.local.set({ amazonAccountName });
      return { ok: true, amazonAccountName };
    }
    if (message.type === "CONTENT_LOG") {
      await log(message.message || "Amazon content script reported activity.", windowId);
      return { ok: true };
    }
    if (message.type === "GET_PROGRESS") {
      const { tracking } = await getWindowState(windowId);
      return { ok: true, progress: trackingProgress(tracking) };
    }
    if (message.type === "RECOVER_ACTIVE_TRACKING_PAGE") return recoverActiveTrackingPage(message, windowId);
    if (message.type === "TEST_CONNECTION") return testConnection();
    if (message.type === "SET_API_BASE") {
      const previousState = await getState();
      const wasAutoTrackingEnabled = previousState.autoTrackingEnabled === true;
      const autoTrackingEnabled = message.autoTrackingEnabled === true;
      const autoTrackingHours = clampAutoHours(message.autoTrackingHours);
      await chrome.storage.local.set({
        apiBase: normalizeApiBase(message.apiBase),
        adminToken: message.adminToken || "",
        headlessTrackingMode: message.headlessTrackingMode === true,
        autoTrackingEnabled,
        autoTrackingHours,
        trackAllStartPage: Math.max(1, Math.min(999, Math.round(Number(message.trackAllStartPage || 1)))),
        trackAllMaxPages: Math.max(1, Math.min(999, Math.round(Number(message.trackAllMaxPages || 202)))),
      });
      await scheduleAutoTracking(autoTrackingEnabled, autoTrackingHours);
      if (!autoTrackingEnabled) await stopAutoTrackingRuns();
      if (autoTrackingEnabled && !wasAutoTrackingEnabled) {
        const autoStart = await startAutoTracking(windowId);
        const autoMessage = autoStart?.message || "Auto tracking started.";
        const noOrders = /no amazon orders need tracking/i.test(autoMessage);
        return {
          ok: autoStart?.ok !== false || noOrders,
          message: noOrders ? `Saved. ${autoMessage}` : autoMessage,
          autoStarted: autoStart?.ok !== false,
          progress: autoStart?.progress,
        };
      }
      return { ok: true };
    }
    if (message.type === "START_TRACKING") return startTracking(windowId);
    if (message.type === "START_SINGLE_ORDER_TRACKING") return startSingleOrderTracking(windowId, message.amazonOrderId);
    if (message.type === "START_MANUAL_ORDER_QUEUE_TRACKING") return startManualOrderQueueTracking(windowId, message.amazonOrderIds || [], "manual");
    if (message.type === "START_PAYMENT_FAILURE_RECHECK") return startPaymentFailureRecheck(windowId);
    if (message.type === "START_TRACK_ALL") return startTrackAll(windowId, message.startPage, message.maxPages, message.startUrl);
    if (message.type === "RESUME_TRACK_ALL") return resumeTrackAll(windowId);
    if (message.type === "STOP_TRACKING") return stopTracking(windowId);
    if (message.type === "START_HEADLESS_TRACKING") return startHeadlessTracking();
    if (message.type === "STOP_HEADLESS_TRACKING") return stopHeadlessTracking();
    if (message.type === "GET_HEADLESS_TRACKING_STATUS") return headlessTrackingStatus();
    if (message.type === "CHECK_HEADLESS_TRACKING_READINESS") return headlessTrackingReadiness();
    if (message.type === "OPEN_HEADLESS_SIGNIN") return openHeadlessSignin();
    if (message.type === "ORDER_PACKAGES") return handleOrderPackages(message, windowId, sender);
    if (message.type === "PACKAGE_TRACKING") return handlePackageTracking(message, windowId, sender);
    if (message.type === "ORDER_HISTORY_TRACK_ALL_PAGE") return handleHistoryTrackPage(message, windowId);
    if (message.type === "ORDER_HISTORY_TRACK_ALL_PROBLEM") return handleHistoryTrackProblem(message, windowId);
    if (message.type === "FORCE_ADVANCE_HISTORY_ORDER") return forceAdvanceHistoryOrder(message, windowId);
    return { ok: false, message: "Unknown message." };
  })()
    .then((result) => sendResponse(result))
    .catch((error) => sendResponse({ ok: false, message: error.message }));
  return true;
});
