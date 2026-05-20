const DEFAULT_API_BASE = "http://127.0.0.1:8000";

async function getState() {
  return chrome.storage.local.get({
    apiBase: DEFAULT_API_BASE,
    adminToken: "",
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
  const response = await fetch(`${apiBase}${path}`, {
    headers: { "Content-Type": "application/json", ...(adminToken ? { "X-Admin-Token": adminToken } : {}), ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    throw new Error((await response.text()) || response.statusText);
  }
  return response.json();
}

async function testConnection() {
  const { apiBase, adminToken } = await getState();
  const base = String(apiBase || DEFAULT_API_BASE).replace(/\/+$/, "");
  const health = await fetch(`${base}/health`);
  if (!health.ok) throw new Error(`Server health check failed: HTTP ${health.status}`);
  const auth = await fetch(`${base}/api/settings/admin-access`, {
    headers: adminToken ? { "X-Admin-Token": adminToken } : {},
  });
  if (!auth.ok) throw new Error((await auth.text()) || `Admin token check failed: HTTP ${auth.status}`);
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
  const { tracking } = await getWindowState(windowId);
  tracking.running = false;
  await saveTracking(tracking, windowId);
  await log("Tracking stopped.", windowId);
  return { ok: true, message: "Stopped." };
}

async function handleOrderPackages(message, windowId) {
  const { tracking } = await getWindowState(windowId);
  if (!tracking.running) return { ok: false };
  const order = tracking.orders[tracking.index];
  if (!order || order.amazon_order_id !== message.amazonOrderId) return { ok: false };
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
  const { tracking } = await getWindowState(windowId);
  if (!tracking.running) return { ok: false };
  const order = tracking.orders[tracking.index];
  if (!order || order.amazon_order_id !== message.amazonOrderId) return { ok: false };
  const packageData = { ...(tracking.packages[tracking.packageIndex] || {}), ...(message.package || {}) };
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

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    const windowId = messageWindowId(message, sender);
    if (message.type === "GET_STATE") return getWindowState(windowId);
    if (message.type === "TEST_CONNECTION") return testConnection();
    if (message.type === "SET_API_BASE") {
      await chrome.storage.local.set({ apiBase: message.apiBase || DEFAULT_API_BASE, adminToken: message.adminToken || "" });
      return { ok: true };
    }
    if (message.type === "START_TRACKING") return startTracking(windowId);
    if (message.type === "STOP_TRACKING") return stopTracking(windowId);
    if (message.type === "ORDER_PACKAGES") return handleOrderPackages(message, windowId);
    if (message.type === "PACKAGE_TRACKING") return handlePackageTracking(message, windowId);
    return { ok: false, message: "Unknown message." };
  })()
    .then((result) => sendResponse(result))
    .catch((error) => sendResponse({ ok: false, message: error.message }));
  return true;
});
