/* Firefox build: prefer Firefox's Promise-based browser namespace. */
const chrome = globalThis.browser || globalThis.chrome;

const DEFAULT_API_BASE = "http://127.0.0.1:8000";

async function getState() {
  return chrome.storage.local.get({
    apiBase: DEFAULT_API_BASE,
    adminToken: "",
    invoiceRun: { running: false, orders: [], index: 0 },
    logs: [],
  });
}

async function log(message) {
  const { logs } = await getState();
  const entry = `${new Date().toLocaleTimeString()} ${message}`;
  await chrome.storage.local.set({ logs: [entry, ...logs].slice(0, 80) });
}

async function api(path, options = {}) {
  const { apiBase, adminToken } = await getState();
  const response = await fetch(`${apiBase}${path}`, {
    ...options,
    headers: { ...(adminToken ? { "X-Admin-Token": adminToken } : {}), ...(options.headers || {}) },
  });
  if (!response.ok) throw new Error((await response.text()) || response.statusText);
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

function orderUrl(order) {
  return order.amazon_order_url || `https://www.amazon.com/your-orders/order-details?orderID=${encodeURIComponent(order.amazon_order_id)}`;
}

async function openCurrentOrder() {
  const { invoiceRun } = await getState();
  if (!invoiceRun.running) return;
  const order = invoiceRun.orders[invoiceRun.index];
  if (!order) {
    invoiceRun.running = false;
    await chrome.storage.local.set({ invoiceRun });
    await log("Amazon invoice run complete.");
    return;
  }
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const url = orderUrl(order);
  if (tabs[0]?.id) await chrome.tabs.update(tabs[0].id, { url, active: true });
  else await chrome.tabs.create({ url, active: true });
  await log(`Opening ${order.amazon_order_id} for ${order.odoo_order_name}.`);
}

async function startInvoices() {
  const payload = await api("/api/accounting/amazon-invoice-orders?limit=200");
  const orders = payload.orders || [];
  const invoiceRun = { running: true, orders, index: 0 };
  await chrome.storage.local.set({ invoiceRun });
  if (!orders.length) return { ok: false, message: "No Amazon invoices are missing." };
  await openCurrentOrder();
  return { ok: true, message: `Started ${orders.length} Amazon invoice(s).` };
}

async function stopInvoices() {
  const { invoiceRun } = await getState();
  invoiceRun.running = false;
  await chrome.storage.local.set({ invoiceRun });
  await log("Amazon invoice run stopped.");
  return { ok: true, message: "Stopped." };
}

async function handleInvoiceFound(message) {
  const { invoiceRun } = await getState();
  if (!invoiceRun.running) return { ok: false };
  const order = invoiceRun.orders[invoiceRun.index];
  if (!order) return { ok: false };
  const form = new FormData();
  const filename = `${order.odoo_order_name || order.amazon_order_id}_amazon_invoice.pdf`;
  form.append("file", new File([new Uint8Array(message.bytes || [])], filename, { type: "application/pdf" }));
  form.append("document_type", "amazon");
  form.append("odoo_order_name", order.odoo_order_name || "");
  form.append("amazon_order_id", order.amazon_order_id || "");
  await api("/api/accounting/amazon-document", { method: "POST", body: form });
  await log(`Uploaded Amazon invoice for ${order.odoo_order_name || order.amazon_order_id}.`);
  invoiceRun.index += 1;
  await chrome.storage.local.set({ invoiceRun });
  await openCurrentOrder();
  return { ok: true };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    if (message.type === "GET_STATE") return getState();
    if (message.type === "TEST_CONNECTION") return testConnection();
    if (message.type === "SET_API_BASE") {
      await chrome.storage.local.set({ apiBase: message.apiBase || DEFAULT_API_BASE, adminToken: message.adminToken || "" });
      return { ok: true };
    }
    if (message.type === "START_INVOICES") return startInvoices();
    if (message.type === "STOP_INVOICES") return stopInvoices();
    if (message.type === "AMAZON_INVOICE_FOUND") return handleInvoiceFound(message);
    return { ok: false, message: "Unknown message." };
  })().then(sendResponse).catch((error) => sendResponse({ ok: false, message: error.message }));
  return true;
});
