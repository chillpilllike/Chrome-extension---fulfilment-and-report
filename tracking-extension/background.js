const DEFAULT_API_BASE = "http://127.0.0.1:8000";
const DEFAULT_REQUEST_TIMEOUT_MS = 45000;
const AUTO_TRACKING_ALARM = "amazonTrackingAuto";
const TRACKING_WATCHDOG_ALARM = "amazonTrackingWatchdog";
const DEFAULT_AUTO_TRACKING_HOURS = 3;
const TRACKING_STEP_TIMEOUT_MS = 90000;
const RECENT_TRACKING_CACHE_TTL_MS = 6 * 60 * 60 * 1000;

async function getState() {
  return chrome.storage.local.get({
    apiBase: DEFAULT_API_BASE,
    adminToken: "",
    headlessTrackingMode: false,
    autoTrackingEnabled: false,
    autoTrackingHours: DEFAULT_AUTO_TRACKING_HOURS,
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
  tracking.updatedAt = Date.now();
  if (!windowId) {
    await chrome.storage.local.set({ tracking });
    return;
  }
  const { trackingByWindow } = await getState();
  await chrome.storage.local.set({ trackingByWindow: { ...(trackingByWindow || {}), [String(windowId)]: tracking }, tracking });
}

function trackingProgress(tracking = {}) {
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
        throw new Error((await response.text()) || response.statusText);
      }
      return response.json();
    } catch (error) {
      lastError = error;
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
  return /failed to fetch|networkerror|load failed|abort|could not reach/i.test(String(error?.message || error || ""));
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
    await chrome.tabs.update(tabs[0].id, { url, active: true });
  } else {
    await chrome.tabs.create({ url, active: true, ...(windowId ? { windowId } : {}) });
  }
}

function orderUrl(order) {
  return order.amazon_order_url || `https://www.amazon.com/your-orders/order-details?orderID=${encodeURIComponent(order.amazon_order_id)}`;
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

async function stopTracking(windowId) {
  const { headlessTrackingMode } = await getState();
  if (headlessTrackingMode) return stopHeadlessTracking();
  const { tracking } = await getWindowState(windowId);
  tracking.running = false;
  tracking.lastMessage = "Tracking stopped by user.";
  await saveTracking(tracking, windowId);
  await log("Tracking stopped.", windowId);
  await clearWatchdogIfIdle();
  return { ok: true, message: "Stopped." };
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
    });
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
  });
  await log(`Posted standalone tracking update for ${amazonOrderId}; recovered from a stale extension queue.`, windowId);
  return { ok: true, recovered: true, message: `Posted standalone tracking update for ${amazonOrderId}.` };
}

async function recoverStaleTrackingRun(tracking, windowId) {
  if (!tracking?.running) return false;
  const lastActivityAt = Number(tracking.lastActivityAt || tracking.updatedAt || tracking.startedAt || 0);
  if (!lastActivityAt || Date.now() - lastActivityAt < TRACKING_STEP_TIMEOUT_MS) return false;
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
      });
      await scheduleAutoTracking(message.autoTrackingEnabled === true, message.autoTrackingHours);
      return { ok: true };
    }
    if (message.type === "START_TRACKING") return startTracking(windowId);
    if (message.type === "STOP_TRACKING") return stopTracking(windowId);
    if (message.type === "START_HEADLESS_TRACKING") return startHeadlessTracking();
    if (message.type === "STOP_HEADLESS_TRACKING") return stopHeadlessTracking();
    if (message.type === "GET_HEADLESS_TRACKING_STATUS") return headlessTrackingStatus();
    if (message.type === "CHECK_HEADLESS_TRACKING_READINESS") return headlessTrackingReadiness();
    if (message.type === "OPEN_HEADLESS_SIGNIN") return openHeadlessSignin();
    if (message.type === "ORDER_PACKAGES") return handleOrderPackages(message, windowId);
    if (message.type === "PACKAGE_TRACKING") return handlePackageTracking(message, windowId);
    return { ok: false, message: "Unknown message." };
  })()
    .then((result) => sendResponse(result))
    .catch((error) => sendResponse({ ok: false, message: error.message }));
  return true;
});
