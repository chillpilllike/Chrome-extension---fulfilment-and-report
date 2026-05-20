/* Firefox build: prefer Firefox's Promise-based browser namespace. */
const chrome = globalThis.browser || globalThis.chrome;

const DEFAULT_API_BASE = "http://127.0.0.1:8000";
const HISTORY_URL = "https://www.amazon.com/gp/your-account/order-history?ref_=ya_d_c_yo";

async function getState() {
  return chrome.storage.local.get({
    apiBase: DEFAULT_API_BASE,
    adminToken: "",
    maxPages: 10,
    run: { running: false, pagesScanned: 0, maxPages: 10, seenOrderIds: [] },
    logs: [],
  });
}

async function log(message) {
  const { logs } = await getState();
  const entry = `${new Date().toLocaleTimeString()} ${message}`;
  await chrome.storage.local.set({ logs: [entry, ...logs].slice(0, 120) });
}

async function api(path, options = {}) {
  const { apiBase, adminToken } = await getState();
  const response = await fetch(`${String(apiBase || DEFAULT_API_BASE).replace(/\/+$/, "")}${path}`, {
    headers: { "Content-Type": "application/json", ...(adminToken ? { "X-Admin-Token": adminToken } : {}), ...(options.headers || {}) },
    ...options,
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

async function activeTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0] || null;
}

async function openUrl(url) {
  const tab = await activeTab();
  if (tab?.id) return chrome.tabs.update(tab.id, { url, active: true });
  return chrome.tabs.create({ url, active: true });
}

async function startScan(maxPages) {
  const run = { running: true, pagesScanned: 0, maxPages: Math.max(1, Number(maxPages || 10)), seenOrderIds: [] };
  await chrome.storage.local.set({ run, maxPages: run.maxPages });
  await log(`Starting Amazon order history scan. Max pages: ${run.maxPages}.`);
  await openUrl(HISTORY_URL);
  return { ok: true, message: "History scan started." };
}

async function stopScan() {
  const { run } = await getState();
  run.running = false;
  await chrome.storage.local.set({ run });
  await log("Manual matcher stopped.");
  return { ok: true, message: "Stopped." };
}

async function scanCurrentPage() {
  const tab = await activeTab();
  if (!tab?.id) return { ok: false, message: "No active tab." };
  await chrome.tabs.sendMessage(tab.id, { type: "SCAN_MANUAL_PAGE", oneShot: true });
  return { ok: true, message: "Scanning current Amazon page." };
}

async function handlePageScan(message, sender) {
  const { run } = await getState();
  const oneShot = Boolean(message.oneShot);
  if (!oneShot && !run.running) return { ok: false };
  const seen = new Set(run.seenOrderIds || []);
  let matched = 0;
  let reported = 0;
  for (const candidate of message.orders || []) {
    if (!candidate.amazon_order_id || seen.has(candidate.amazon_order_id)) continue;
    seen.add(candidate.amazon_order_id);
    reported += 1;
    try {
      const result = await api("/api/manual-amazon/match", {
        method: "POST",
        body: JSON.stringify(candidate),
      });
      matched += Number(result.matched || 0);
      await log(result.message || `Checked ${candidate.amazon_order_id}.`);
    } catch (error) {
      await log(`Failed ${candidate.amazon_order_id}: ${error.message}`);
    }
  }
  run.seenOrderIds = [...seen].slice(-1000);
  run.pagesScanned = Number(run.pagesScanned || 0) + 1;
  if (!oneShot && run.running && message.nextUrl && run.pagesScanned < Number(run.maxPages || 10)) {
    await chrome.storage.local.set({ run });
    await log(`Page ${run.pagesScanned} scanned. Moving to next Amazon page.`);
    if (sender.tab?.id) {
      await chrome.tabs.update(sender.tab.id, { url: message.nextUrl, active: true });
    } else {
      await openUrl(message.nextUrl);
    }
  } else {
    if (!oneShot) {
      run.running = false;
      await log(`History scan complete. Pages: ${run.pagesScanned}, orders checked: ${seen.size}.`);
    }
    await chrome.storage.local.set({ run });
  }
  return { ok: true, reported, matched };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    if (message.type === "GET_STATE") return getState();
    if (message.type === "TEST_CONNECTION") return testConnection();
    if (message.type === "SET_SETTINGS") {
      await chrome.storage.local.set({
        apiBase: message.apiBase || DEFAULT_API_BASE,
        adminToken: message.adminToken || "",
        maxPages: Math.max(1, Number(message.maxPages || 10)),
      });
      return { ok: true };
    }
    if (message.type === "START_SCAN") return startScan(message.maxPages);
    if (message.type === "STOP_SCAN") return stopScan();
    if (message.type === "SCAN_CURRENT_PAGE") return scanCurrentPage();
    if (message.type === "MANUAL_ORDER_PAGE") return handlePageScan(message, sender);
    return { ok: false, message: "Unknown message." };
  })().then(sendResponse).catch((error) => sendResponse({ ok: false, message: error.message }));
  return true;
});
