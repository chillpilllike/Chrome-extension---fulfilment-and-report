const DEFAULT_API_BASE = "http://127.0.0.1:8000";
const DEFAULT_REQUEST_TIMEOUT_MS = 45000;
const AUTO_TRACKING_ALARM = "amazonTrackingAuto";
const TRACKING_WATCHDOG_ALARM = "amazonTrackingWatchdog";
const DEFAULT_AUTO_TRACKING_HOURS = 3;
const TRACKING_STEP_TIMEOUT_MS = 45000;
const RECENT_TRACKING_CACHE_TTL_MS = 6 * 60 * 60 * 1000;
const ORDER_HISTORY_URL = "https://www.amazon.com/gp/css/order-history?ref_=abn_yadd_ad_your_orders";

async function getState() {
  return chrome.storage.local.get({
    apiBase: DEFAULT_API_BASE,
    adminToken: "",
    headlessTrackingMode: false,
    autoTrackingEnabled: false,
    autoTrackingHours: DEFAULT_AUTO_TRACKING_HOURS,
    trackAllStartPage: 1,
    trackAllMaxPages: 202,
    tracking: { running: false, orders: [], index: 0, packages: [], packageIndex: 0 },
    recentTrackingChecks: [],
    trackingByWindow: {},
    logs: [],
    logsByWindow: {},
  });
}

function messageWindowId(message = {}, sender = {}) {
  return Number(message.targetWindowId || sender.tab?.windowId || 0) || null;
}

async function getWindowState(windowId) {
  const state = await getState();
  const key = String(windowId || "");
  return {
    ...state,
    targetWindowId: windowId || null,
    tracking: windowId ? state.trackingByWindow?.[key] || { running: false, orders: [], index: 0, packages: [], packageIndex: 0 } : state.tracking,
    logs: windowId ? state.logsByWindow?.[key] || [] : state.logs,
  };
}

async function saveTracking(tracking, windowId) {
  if (!windowId) {
    const current = (await getState()).tracking || {};
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
  const { trackingByWindow } = await getState();
  const key = String(windowId);
  const current = trackingByWindow?.[key] || {};
  const staleRunningSave = current.running === false
    && tracking.running !== false
    && current.source === tracking.source
    && Number(current.startedAt || 0) === Number(tracking.startedAt || 0)
    && Number(current.updatedAt || 0) > Number(tracking.updatedAt || 0);
  if (staleRunningSave) return;
  tracking.updatedAt = Date.now();
  await chrome.storage.local.set({ trackingByWindow: { ...(trackingByWindow || {}), [key]: tracking }, tracking });
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
    message: tracking.running ? "Tracking is running." : "Tracking is stopped.",
  };
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
      .map((item) => String(item.amazon_order_id || "").trim())
      .filter(Boolean),
  );
}

async function clearWatchdogIfIdle() {
  const state = await getState();
  const anyWindowRunning = Object.values(state.trackingByWindow || {}).some((item) => item?.running);
  if (!state.tracking?.running && !anyWindowRunning) {
    await chrome.alarms.clear(TRACKING_WATCHDOG_ALARM);
  }
}

async function ensureWatchdog() {
  chrome.alarms.create(TRACKING_WATCHDOG_ALARM, { periodInMinutes: 1 });
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
  let lastError = null;
  for (let attempt = 0; attempt <= Number(retries || 0); attempt += 1) {
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
        if (response.status >= 500 && attempt < Number(retries || 0)) {
          lastError = new Error(responseText || response.statusText || "Server error");
          await new Promise((resolve) => setTimeout(resolve, 1200));
          continue;
        }
        throw new Error(responseText || response.statusText);
      }
      return response.json();
    } catch (error) {
      lastError = error?.name === "AbortError"
        ? new Error(`Local app request timed out after ${Math.round(Number(timeoutMs || DEFAULT_REQUEST_TIMEOUT_MS) / 1000)}s.`)
        : error;
      if (error?.name === "AbortError") break;
      if (!isConnectionError(error) || attempt >= Number(retries || 0)) break;
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

async function startAutoTracking() {
  const state = await getState();
  if (state.autoTrackingEnabled !== true) return { ok: false, message: "Amazon auto tracking is disabled." };
  if (state.tracking?.running) {
    await log("Scheduled Amazon tracking found an existing run; resuming from the saved position.");
    await ensureWatchdog();
    await openCurrentOrder(null);
    return { ok: true, resumed: true, progress: trackingProgress(state.tracking) };
  }
  await log(`Scheduled Amazon tracking started; interval is every ${clampAutoHours(state.autoTrackingHours)} hour(s).`);
  return startTracking(null);
}

async function testConnection() {
  const { apiBase, adminToken } = await getState();
  const base = normalizeApiBase(apiBase);
  try {
    await api("/health", { timeoutMs: 8000 });
    await api("/api/settings/admin-access", { timeoutMs: 8000, headers: adminToken ? { "X-Admin-Token": adminToken } : {} });
  } catch (error) {
    throw new Error(connectionErrorMessage(error, base));
  }
  return { ok: true, message: `Connected to ${base}. Admin token accepted.` };
}

async function openUrl(url, windowId) {
  const query = windowId ? { active: true, windowId } : { active: true, currentWindow: true };
  const tabs = await chrome.tabs.query(query);
  if (tabs[0]?.id) {
    const sameUrl = tabs[0].url === url;
    await chrome.tabs.update(tabs[0].id, { url, active: true });
    if (sameUrl) await chrome.tabs.reload(tabs[0].id);
  } else {
    await chrome.tabs.create({ url, active: true, ...(windowId ? { windowId } : {}) });
  }
}

function orderUrl(order) {
  return order.amazon_order_url || `https://www.amazon.com/your-orders/order-details?orderID=${encodeURIComponent(order.amazon_order_id)}`;
}

function orderHistoryUrl(page = 1) {
  const pageNumber = Math.max(1, Math.round(Number(page || 1)));
  return `${ORDER_HISTORY_URL}#pagination/${pageNumber}/`;
}

function normalizeHistoryNextUrl(nextUrl = "", nextPage = 1) {
  const raw = String(nextUrl || "").trim();
  if (!raw) return "";
  if (/^#?pagination\/next\/?$/i.test(raw.replace(/^#/, ""))) {
    return orderHistoryUrl(nextPage);
  }
  try {
    const url = new URL(raw, ORDER_HISTORY_URL);
    if (/amazon\.com$/i.test(url.hostname) && /pagination\/next/i.test(url.hash)) {
      return orderHistoryUrl(nextPage);
    }
    return url.href;
  } catch {
    return raw;
  }
}

async function openCurrentOrder(windowId) {
  const { tracking } = await getWindowState(windowId);
  if (!tracking.running) return;
  const order = tracking.orders[tracking.index];
  if (!order) {
    tracking.running = false;
    tracking.finishedAt = Date.now();
    tracking.lastMessage = "Tracking complete.";
    await saveTracking(tracking, windowId);
    await log("Tracking complete. No more open Amazon orders.", windowId);
    await clearWatchdogIfIdle();
    return;
  }
  tracking.packages = [];
  tracking.packageIndex = 0;
  tracking.currentStep = "order";
  tracking.currentUrl = orderUrl(order);
  tracking.currentOrderId = order.amazon_order_id;
  tracking.lastActivityAt = Date.now();
  tracking.lastMessage = `Opening Amazon order ${order.amazon_order_id}.`;
  await saveTracking(tracking, windowId);
  await ensureWatchdog();
  await log(`Opening Amazon order ${order.amazon_order_id}.`, windowId);
  await openUrl(orderUrl(order), windowId);
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
  tracking.currentStep = "";
  tracking.currentUrl = "";
  tracking.currentOrderId = "";
  tracking.lastActivityAt = Date.now();
  await saveTracking(tracking, windowId);
  await openCurrentOrder(windowId);
}

async function startTracking(windowId) {
  const { headlessTrackingMode } = await getState();
  if (headlessTrackingMode) return startHeadlessTracking();
  const payload = await api("/api/tracking/orders");
  const { recentTrackingChecks } = await getState();
  const recent = recentCheckSet(recentTrackingChecks);
  const allOrders = (payload.orders || []).filter((order) => String(order.tracking_status || "").toLowerCase() !== "delivered");
  const orders = allOrders.filter((order) => !recent.has(String(order.amazon_order_id || "").trim()));
  const tracking = {
    running: true,
    orders,
    index: 0,
    packages: [],
    packageIndex: 0,
    completedOrderIds: [],
    failedOrderIds: [],
    skippedRecentCount: allOrders.length - orders.length,
    startedAt: Date.now(),
    lastActivityAt: Date.now(),
    lastMessage: "Tracking started.",
  };
  await saveTracking(tracking, windowId);
  await log(`Loaded ${orders.length} Amazon order(s) for tracking; skipped ${tracking.skippedRecentCount} recently checked order(s).`, windowId);
  if (!orders.length) return { ok: false, message: "No Amazon orders need tracking." };
  await openCurrentOrder(windowId);
  return { ok: true, message: `Started tracking ${orders.length} order(s).`, progress: trackingProgress(tracking) };
}

function normalizeHistoryOrder(order = {}) {
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
    amazon_order_url: order.amazon_order_url || orderUrl({ amazon_order_id: orderId }),
    recipient: String(order.recipient || "").replace(/\s+/g, " ").trim(),
    order_date: String(order.order_date || "").replace(/\s+/g, " ").trim(),
    status: String(order.status || "").replace(/\s+/g, " ").trim(),
    asins: Object.keys(asinQuantities),
    items,
    asin_quantities: asinQuantities,
    cancelled: order.cancelled === true,
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
      amazon_account_name: "Amazon Tracking Track All",
      order_date: normalized.order_date,
      order_names: orderNames,
      line_ids: lineIds,
      source_text: normalized.recipient || orderNames.join(" "),
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

async function startTrackAll(windowId, startPage = 1, maxPages = 202) {
  const page = Math.max(1, Math.round(Number(startPage || 1)));
  const pages = Math.max(1, Math.min(999, Math.round(Number(maxPages || 202))));
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
    startedAt: Date.now(),
    lastActivityAt: Date.now(),
    lastMessage: `Track all started from order-history page ${page}.`,
  };
  await saveTracking(tracking, windowId);
  await ensureWatchdog();
  await log(`Track all started from Amazon order-history page ${page}; max pages ${pages}.`, windowId);
  await openUrl(orderHistoryUrl(page), windowId);
  return { ok: true, message: `Track all started from page ${page}.`, progress: trackingProgress(tracking) };
}

function canResumeHistoryTracking(tracking = {}) {
  if (tracking.source !== "history" || tracking.running) return false;
  const hasCurrent = Boolean(tracking.currentOrder?.amazon_order_id);
  const hasQueue = Array.isArray(tracking.queue) && tracking.queue.length > 0;
  const hasMorePages = Boolean(tracking.nextUrl) && Number(tracking.pagesScanned || 0) < Number(tracking.maxPages || 0);
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
  if (tracking.nextUrl && Number(tracking.pagesScanned || 0) < Number(tracking.maxPages || 1)) {
    tracking.nextUrl = normalizeHistoryNextUrl(tracking.nextUrl, nextPage);
    tracking.currentPage = nextPage;
    tracking.lastActivityAt = Date.now();
    tracking.lastMessage = `Opening Amazon order-history page ${nextPage}.`;
    await saveTracking(tracking, windowId);
    await log(`Opening Amazon order-history page ${nextPage}.`, windowId);
    await openUrl(tracking.nextUrl || orderHistoryUrl(nextPage), windowId);
    return true;
  }
  tracking.running = false;
  tracking.finishedAt = Date.now();
  tracking.lastMessage = `Track all complete. Checked ${tracking.completedOrderIds?.length || 0} order(s).`;
  await saveTracking(tracking, windowId);
  await log(`Track all complete. Checked ${tracking.completedOrderIds?.length || 0} order(s), failed ${tracking.failedOrderIds?.length || 0}.`, windowId);
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
    await openUrl(tracking.currentUrl, windowId);
    return;
  }
  tracking.currentStep = "history_order";
  tracking.currentUrl = order.amazon_order_url || orderUrl(order);
  await saveTracking(tracking, windowId);
  await log(`Opening order details for ${order.amazon_order_id} to discover package links.`, windowId);
  await openUrl(tracking.currentUrl, windowId);
}

async function postHistoryOrderTracking(tracking, windowId, status = "checked") {
  const order = tracking.currentOrder;
  if (!order?.amazon_order_id) return;
  const packages = (order.capturedPackages || order.packages || []).filter(Boolean);
  const statusOnly = packages.length > 0 && packages.every((pkg) => pkg.status_only && !pkg.tracking_id);
  try {
    await api("/api/tracking/update", {
      method: "POST",
      body: JSON.stringify({
        amazon_order_id: order.amazon_order_id,
        amazon_order_url: order.amazon_order_url || orderUrl(order),
        packages: packages.length ? packages : [{
          status: order.status || "Unknown",
          promise: order.status || "",
          expected_delivery_date: order.expected_delivery_date || "",
          expected_delivery_display: order.expected_delivery_display || "",
          tracking_url: order.amazon_order_url || orderUrl(order),
          asins: order.asins || [],
          products: order.products || order.items || [],
        }],
      }),
      timeoutMs: statusOnly ? 8000 : 25000,
      retries: 0,
    });
    await rememberRecentCheck(order.amazon_order_id, status);
    return true;
  } catch (error) {
    if (/Tracked Amazon order not found/i.test(String(error?.message || error))) {
      await rememberRecentCheck(order.amazon_order_id, "unmatched");
      await log(`Skipped unmatched Amazon order ${order.amazon_order_id}; tracking page was captured but no app order matched.`, windowId);
      return false;
    }
    throw error;
  }
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
  tracking.currentStep = "";
  tracking.currentUrl = "";
  tracking.lastActivityAt = Date.now();
  await saveTracking(tracking, windowId);
  await openHistoryCurrentOrder(windowId);
}

async function handleHistoryTrackPage(message, windowId) {
  const { tracking } = await getWindowState(windowId);
  if (!tracking.running || tracking.source !== "history") return { ok: false, message: "Track all is not running." };
  if (message.runId && Number(message.runId) !== Number(tracking.startedAt || 0)) {
    return { ok: false, message: "Ignored stale order-history scan from a previous Track all run." };
  }
  const seen = new Set(tracking.seenOrderIds || []);
  const queue = Array.isArray(tracking.queue) ? tracking.queue : [];
  let added = 0;
  const addedRawOrders = [];
  for (const rawOrder of message.orders || []) {
    const normalized = normalizeHistoryOrder(rawOrder);
    if (!normalized || seen.has(normalized.amazon_order_id)) continue;
    seen.add(normalized.amazon_order_id);
    addedRawOrders.push(rawOrder);
    queue.push({
      ...normalized,
      products: rawOrder.products || rawOrder.items || [],
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
  tracking.lastMessage = `Scanned history page ${tracking.currentPage || ""}: ${added} order(s) queued; preparing matches in background.`;
  await saveTracking(tracking, windowId);
  await log(`History page scanned: queued ${added} order(s); matching continues in background.`, windowId);
  if (!tracking.currentOrder) await openHistoryCurrentOrder(windowId);
  if (addedRawOrders.length) {
    syncHistoryOrdersToAppBatch(addedRawOrders, windowId).catch((error) => log(`Batch Track all preparation failed: ${error.message}`, windowId));
  }
  return { ok: true, added, matched: 0, preparing: addedRawOrders.length };
}

async function handleHistoryOrderPackages(message, windowId) {
  const { tracking } = await getWindowState(windowId);
  const order = tracking.currentOrder;
  if (!tracking.running || tracking.source !== "history" || !order || order.amazon_order_id !== message.amazonOrderId) return null;
  if (message.orderCancelled || message.paymentRevisionNeeded) {
    try {
      await api("/api/tracking/update", {
        method: "POST",
        body: JSON.stringify({
          amazon_order_id: order.amazon_order_id,
          amazon_order_url: order.amazon_order_url || orderUrl(order),
          packages: message.packages || [],
          order_cancelled: Boolean(message.orderCancelled),
          cancellation_message: message.cancellationMessage || "",
          payment_revision_needed: Boolean(message.paymentRevisionNeeded),
          payment_revision_url: message.paymentRevisionUrl || "",
          page_text: message.pageText || "",
        }),
        timeoutMs: 12000,
        retries: 0,
      });
    } catch (error) {
      await log(`Could not save ${message.orderCancelled ? "cancelled" : "payment revision"} status for ${order.amazon_order_id}: ${error.message}; continuing Track all.`, windowId);
    }
    await advanceHistoryOrder(tracking, windowId, message.orderCancelled ? "cancelled" : "payment_revision");
    return { ok: true };
  }
  order.packages = Array.isArray(message.packages) ? message.packages : [];
  order.products = Array.isArray(message.products) ? message.products : order.products || [];
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
  const queuedPackage = order.packages?.[Number(order.packageIndex || 0)] || {};
  const pagePackage = message.package || {};
  const packageData = { ...queuedPackage, ...pagePackage };
  if (Array.isArray(queuedPackage.asins) && queuedPackage.asins.length) packageData.asins = queuedPackage.asins;
  if (Array.isArray(queuedPackage.products) && queuedPackage.products.length) packageData.products = queuedPackage.products;
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

async function handleOrderPackages(message, windowId) {
  const historyResult = await handleHistoryOrderPackages(message, windowId);
  if (historyResult) return historyResult;
  const { tracking, headlessTrackingMode } = await getWindowState(windowId);
  if (!tracking.running) {
    if (headlessTrackingMode) {
      return { ok: true, ignored: true, message: "Headless tracking mode is active; visible Amazon pages are ignored." };
    }
    return { ok: false, message: "Normal tracking is not running. Press Start Tracking from the extension popup." };
  }
  const order = tracking.orders[tracking.index];
  if (!order || order.amazon_order_id !== message.amazonOrderId) {
    if (headlessTrackingMode) {
      return { ok: true, ignored: true, message: "Headless tracking mode is active; visible Amazon pages are ignored." };
    }
    return { ok: false, message: "This Amazon page does not match the active tracking order." };
  }
  if (message.orderCancelled) {
    try {
      await api("/api/tracking/update", {
        method: "POST",
        body: JSON.stringify({
          amazon_order_id: order.amazon_order_id,
          amazon_order_url: orderUrl(order),
          packages: message.packages || [],
          order_cancelled: true,
          cancellation_message: message.cancellationMessage || "This order has been cancelled.",
          page_text: message.pageText || "",
        }),
        timeoutMs: 12000,
        retries: 0,
      });
    } catch (error) {
      await log(`Could not save cancelled status for ${order.amazon_order_id}: ${error.message}; continuing.`, windowId);
    }
    await log(`Amazon order ${order.amazon_order_id} is cancelled; reset lines for reorder.`, windowId);
    await advanceCurrentOrder(tracking, windowId, "cancelled");
    return { ok: true };
  }
  if (message.paymentRevisionNeeded) {
    await api("/api/tracking/update", {
      method: "POST",
      body: JSON.stringify({
        amazon_order_id: order.amazon_order_id,
        amazon_order_url: orderUrl(order),
        packages: message.packages || [],
        payment_revision_needed: true,
        payment_revision_url: message.paymentRevisionUrl || "",
        page_text: message.pageText || "",
      }),
    });
    await log(`Payment revision needed for ${order.amazon_order_id}; posted to Payment Failed page.`, windowId);
    await advanceCurrentOrder(tracking, windowId, "payment_revision");
    return { ok: true };
  }
  tracking.packages = message.packages || [];
  tracking.packageIndex = 0;
  tracking.lastActivityAt = Date.now();
  tracking.currentStep = "packages";
  tracking.lastMessage = `Found ${tracking.packages.length} package link(s) for ${order.amazon_order_id}.`;
  await saveTracking(tracking, windowId);
  if (!tracking.packages.length) {
    const products = Array.isArray(message.products) ? message.products : [];
    await api("/api/tracking/update", {
      method: "POST",
      body: JSON.stringify({
        amazon_order_id: order.amazon_order_id,
        amazon_order_url: orderUrl(order),
        packages: [{
          status: message.orderStatus || "Unknown",
          promise: message.promise || "",
          expected_delivery_date: message.expected_delivery_date || "",
          expected_delivery_display: message.expected_delivery_display || "",
          tracking_url: orderUrl(order),
          asins: products.map((item) => item.asin).filter(Boolean),
          products,
        }],
      }),
    });
    await log(`No tracking buttons found for ${order.amazon_order_id}; saved order-page status.`, windowId);
    await advanceCurrentOrder(tracking, windowId, "checked");
    return { ok: true };
  }
  await log(`Found ${tracking.packages.length} package link(s) for ${order.amazon_order_id}.`, windowId);
  tracking.currentStep = "package";
  tracking.currentUrl = tracking.packages[0].tracking_url;
  tracking.lastActivityAt = Date.now();
  await saveTracking(tracking, windowId);
  await openUrl(tracking.packages[0].tracking_url, windowId);
  return { ok: true };
}

async function handlePackageTracking(message, windowId) {
  const historyResult = await handleHistoryPackageTracking(message, windowId);
  if (historyResult) return historyResult;
  const { tracking, headlessTrackingMode } = await getWindowState(windowId);
  if (!tracking.running && headlessTrackingMode) {
    return { ok: true, ignored: true, message: "Headless tracking mode is active; visible Amazon pages are ignored." };
  }
  if (!tracking.running) return postStandalonePackageTracking(message, windowId);
  const order = tracking.orders[tracking.index];
  if (!order || order.amazon_order_id !== message.amazonOrderId) {
    if (headlessTrackingMode) {
      return { ok: true, ignored: true, message: "Headless tracking mode is active; visible Amazon pages are ignored." };
    }
    return postStandalonePackageTracking(message, windowId);
  }
  const queuedPackage = tracking.packages[tracking.packageIndex] || {};
  const pagePackage = message.package || {};
  const packageData = { ...queuedPackage, ...pagePackage };
  if (Array.isArray(queuedPackage.asins) && queuedPackage.asins.length) {
    packageData.asins = queuedPackage.asins;
  }
  if (Array.isArray(queuedPackage.products) && queuedPackage.products.length) {
    packageData.products = queuedPackage.products;
  }
  if (message.paymentRevisionNeeded) {
    packageData.payment_revision_needed = true;
    packageData.payment_revision_url = message.paymentRevisionUrl || "";
    packageData.page_text = message.pageText || "";
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
    await openUrl(tracking.packages[tracking.packageIndex].tracking_url, windowId);
    return { ok: true };
  }
  await api("/api/tracking/update", {
    method: "POST",
    body: JSON.stringify({
      amazon_order_id: order.amazon_order_id,
      amazon_order_url: orderUrl(order),
      packages: tracking.packages,
      payment_revision_needed: tracking.packages.some((pkg) => pkg.payment_revision_needed),
      payment_revision_url: tracking.packages.find((pkg) => pkg.payment_revision_url)?.payment_revision_url || "",
      page_text: tracking.packages.find((pkg) => pkg.page_text)?.page_text || "",
    }),
    timeoutMs: tracking.packages?.some((pkg) => pkg.status_only && !pkg.tracking_id) ? 8000 : 25000,
    retries: 0,
  });
  await log(`Posted tracking update for ${order.amazon_order_id}.`, windowId);
  await advanceCurrentOrder(tracking, windowId, "checked");
  return { ok: true };
}

async function postStandalonePackageTracking(message, windowId) {
  const amazonOrderId = String(message.amazonOrderId || "").trim();
  const packageData = message.package || {};
  if (!amazonOrderId || !Object.keys(packageData).length) {
    return { ok: false, message: "No active tracking run matched this Amazon package page." };
  }
  const payloadPackage = { ...packageData, tracking_url: packageData.tracking_url || `https://www.amazon.com/your-orders/order-details?orderID=${encodeURIComponent(amazonOrderId)}` };
  if (message.paymentRevisionNeeded) {
    payloadPackage.payment_revision_needed = true;
    payloadPackage.payment_revision_url = message.paymentRevisionUrl || "";
    payloadPackage.page_text = message.pageText || "";
  }
  const statusOnly = Boolean(payloadPackage.status_only && !payloadPackage.tracking_id);
  await api("/api/tracking/update", {
    method: "POST",
    body: JSON.stringify({
      amazon_order_id: amazonOrderId,
      amazon_order_url: `https://www.amazon.com/your-orders/order-details?orderID=${encodeURIComponent(amazonOrderId)}`,
      packages: [payloadPackage],
      payment_revision_needed: Boolean(payloadPackage.payment_revision_needed),
      payment_revision_url: payloadPackage.payment_revision_url || "",
      page_text: payloadPackage.page_text || "",
    }),
    timeoutMs: statusOnly ? 8000 : 25000,
    retries: 0,
  });
  await log(`Posted standalone tracking update for ${amazonOrderId}; recovered from a stale extension queue.`, windowId);
  return { ok: true, recovered: true, message: `Posted standalone tracking update for ${amazonOrderId}.` };
}

async function recoverStaleTrackingRun(tracking, windowId) {
  if (!tracking?.running) return false;
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

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    const windowId = messageWindowId(message, sender);
    if (message.type === "GET_STATE") return getWindowState(windowId);
    if (message.type === "GET_PROGRESS") {
      const { tracking } = await getWindowState(windowId);
      return { ok: true, progress: trackingProgress(tracking) };
    }
    if (message.type === "TEST_CONNECTION") return testConnection();
    if (message.type === "SET_API_BASE") {
      await chrome.storage.local.set({
        apiBase: normalizeApiBase(message.apiBase),
        adminToken: message.adminToken || "",
        headlessTrackingMode: message.headlessTrackingMode === true,
        autoTrackingEnabled: message.autoTrackingEnabled === true,
        autoTrackingHours: clampAutoHours(message.autoTrackingHours),
        trackAllStartPage: Math.max(1, Math.min(999, Math.round(Number(message.trackAllStartPage || 1)))),
        trackAllMaxPages: Math.max(1, Math.min(999, Math.round(Number(message.trackAllMaxPages || 202)))),
      });
      await scheduleAutoTracking(message.autoTrackingEnabled === true, message.autoTrackingHours);
      return { ok: true };
    }
    if (message.type === "START_TRACKING") return startTracking(windowId);
    if (message.type === "START_TRACK_ALL") return startTrackAll(windowId, message.startPage, message.maxPages);
    if (message.type === "RESUME_TRACK_ALL") return resumeTrackAll(windowId);
    if (message.type === "STOP_TRACKING") return stopTracking(windowId);
    if (message.type === "START_HEADLESS_TRACKING") return startHeadlessTracking();
    if (message.type === "STOP_HEADLESS_TRACKING") return stopHeadlessTracking();
    if (message.type === "GET_HEADLESS_TRACKING_STATUS") return headlessTrackingStatus();
    if (message.type === "CHECK_HEADLESS_TRACKING_READINESS") return headlessTrackingReadiness();
    if (message.type === "OPEN_HEADLESS_SIGNIN") return openHeadlessSignin();
    if (message.type === "ORDER_PACKAGES") return handleOrderPackages(message, windowId);
    if (message.type === "PACKAGE_TRACKING") return handlePackageTracking(message, windowId);
    if (message.type === "ORDER_HISTORY_TRACK_ALL_PAGE") return handleHistoryTrackPage(message, windowId);
    return { ok: false, message: "Unknown message." };
  })()
    .then((result) => sendResponse(result))
    .catch((error) => sendResponse({ ok: false, message: error.message }));
  return true;
});
