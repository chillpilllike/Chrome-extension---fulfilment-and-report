const DEFAULT_API_BASE = "http://127.0.0.1:8000";
const DEFAULT_REQUEST_TIMEOUT_MS = 45000;

async function getState() {
  return chrome.storage.local.get({
    apiBase: DEFAULT_API_BASE,
    adminToken: "",
    headlessTrackingMode: false,
    tracking: { running: false, orders: [], index: 0, packages: [], packageIndex: 0 },
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
    await chrome.storage.local.set({ tracking });
    return;
  }
  const { trackingByWindow } = await getState();
  await chrome.storage.local.set({ trackingByWindow: { ...(trackingByWindow || {}), [String(windowId)]: tracking }, tracking });
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
    await saveTracking(tracking, windowId);
    await log("Tracking complete. No more open Amazon orders.", windowId);
    return;
  }
  tracking.packages = [];
  tracking.packageIndex = 0;
  await saveTracking(tracking, windowId);
  await log(`Opening Amazon order ${order.amazon_order_id}.`, windowId);
  await openUrl(orderUrl(order), windowId);
}

async function startTracking(windowId) {
  const { headlessTrackingMode } = await getState();
  if (headlessTrackingMode) return startHeadlessTracking();
  const payload = await api("/api/tracking/orders");
  const orders = (payload.orders || []).filter((order) => String(order.tracking_status || "").toLowerCase() !== "delivered");
  const tracking = { running: true, orders, index: 0, packages: [], packageIndex: 0 };
  await saveTracking(tracking, windowId);
  await log(`Loaded ${orders.length} Amazon order(s) for tracking.`, windowId);
  if (!orders.length) return { ok: false, message: "No Amazon orders need tracking." };
  await openCurrentOrder(windowId);
  return { ok: true, message: `Started tracking ${orders.length} order(s).` };
}

async function stopTracking(windowId) {
  const { headlessTrackingMode } = await getState();
  if (headlessTrackingMode) return stopHeadlessTracking();
  const { tracking } = await getWindowState(windowId);
  tracking.running = false;
  await saveTracking(tracking, windowId);
  await log("Tracking stopped.", windowId);
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
    tracking.index += 1;
    tracking.packages = [];
    tracking.packageIndex = 0;
    await saveTracking(tracking, windowId);
    await openCurrentOrder(windowId);
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
    tracking.index += 1;
    tracking.packages = [];
    tracking.packageIndex = 0;
    await saveTracking(tracking, windowId);
    await openCurrentOrder(windowId);
    return { ok: true };
  }
  tracking.packages = message.packages || [];
  tracking.packageIndex = 0;
  await saveTracking(tracking, windowId);
  if (!tracking.packages.length) {
    await api("/api/tracking/update", {
      method: "POST",
      body: JSON.stringify({
        amazon_order_id: order.amazon_order_id,
        amazon_order_url: orderUrl(order),
        packages: [{
          status: message.orderStatus || "Unknown",
          promise: message.promise || "",
          tracking_url: orderUrl(order),
          asins: [],
        }],
      }),
    });
    await log(`No tracking buttons found for ${order.amazon_order_id}; saved order-page status.`, windowId);
    tracking.index += 1;
    await saveTracking(tracking, windowId);
    await openCurrentOrder(windowId);
    return { ok: true };
  }
  await log(`Found ${tracking.packages.length} package link(s) for ${order.amazon_order_id}.`, windowId);
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
  if (message.paymentRevisionNeeded) {
    packageData.payment_revision_needed = true;
    packageData.payment_revision_url = message.paymentRevisionUrl || "";
    packageData.page_text = message.pageText || "";
  }
  tracking.packages[tracking.packageIndex] = packageData;
  tracking.packageIndex += 1;
  await saveTracking(tracking, windowId);
  await log(`Captured ${packageData.carrier || "carrier"} ${packageData.tracking_id || ""} for ${order.amazon_order_id}.`, windowId);
  if (tracking.packageIndex < tracking.packages.length) {
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
  tracking.index += 1;
  tracking.packages = [];
  tracking.packageIndex = 0;
  await saveTracking(tracking, windowId);
  await openCurrentOrder(windowId);
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

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    const windowId = messageWindowId(message, sender);
    if (message.type === "GET_STATE") return getWindowState(windowId);
    if (message.type === "TEST_CONNECTION") return testConnection();
    if (message.type === "SET_API_BASE") {
      await chrome.storage.local.set({
        apiBase: normalizeApiBase(message.apiBase),
        adminToken: message.adminToken || "",
        headlessTrackingMode: message.headlessTrackingMode === true,
      });
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
