const DEFAULT_API_BASE = "http://127.0.0.1:8000";
const DEFAULT_REQUEST_TIMEOUT_MS = 45000;
const TRACKER_URL = "https://portal.epgshipping.com/ParcelTracker/HomePageTracker";
const EPOST_TRACKING_ALARM = "epostTracking";
const DEFAULT_EPOST_AUTO_HOURS = 24;

async function getState() {
  return chrome.storage.local.get({
    apiBase: DEFAULT_API_BASE,
    adminToken: "",
    intervalDays: 1,
    intervalHours: DEFAULT_EPOST_AUTO_HOURS,
    autoEpostEnabled: false,
    headlessEpostMode: false,
    epostRun: { running: false, batches: [], batchIndex: 0 },
    epostRunByWindow: {},
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
    epostRun: windowId ? state.epostRunByWindow?.[key] || { running: false, batches: [], batchIndex: 0 } : state.epostRun,
    logs: windowId ? state.logsByWindow?.[key] || [] : state.logs,
  };
}

async function saveEpostRun(epostRun, windowId) {
  if (!windowId) {
    await chrome.storage.local.set({ epostRun });
    return;
  }
  const { epostRunByWindow } = await getState();
  await chrome.storage.local.set({ epostRunByWindow: { ...(epostRunByWindow || {}), [String(windowId)]: epostRun }, epostRun });
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

function normalizeApiBase(apiBase) {
  return String(apiBase || DEFAULT_API_BASE).trim().replace(/\/+$/, "") || DEFAULT_API_BASE;
}

function connectionErrorMessage(error, base = "") {
  const raw = String(error?.message || error || "Failed to fetch");
  if (/failed to fetch|networkerror|load failed|abort/i.test(raw)) {
    return `Could not reach the local app at ${base || DEFAULT_API_BASE}. Make sure it is running on port 8000, then save and check the connection again.`;
  }
  return raw;
}

async function api(path, options = {}) {
  const { apiBase, adminToken } = await getState();
  const base = normalizeApiBase(apiBase);
  const requestPath = String(path || "").startsWith("/") ? path : `/${path}`;
  const { timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS, headers = {}, ...fetchOptions } = options;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), Number(timeoutMs || DEFAULT_REQUEST_TIMEOUT_MS));
  const response = await fetch(`${base}${requestPath}`, {
    ...fetchOptions,
    headers: { "Content-Type": "application/json", ...(adminToken ? { "X-Admin-Token": adminToken } : {}), ...headers },
    signal: controller.signal,
  }).finally(() => clearTimeout(timeout));
  if (!response.ok) {
    throw new Error((await response.text()) || response.statusText);
  }
  return response.json();
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

function chunk(items, size) {
  const chunks = [];
  for (let i = 0; i < items.length; i += size) chunks.push(items.slice(i, i + size));
  return chunks;
}

async function openTracker(windowId) {
  const url = `${TRACKER_URL}?nutricityBatch=${Date.now()}`;
  try {
    const query = windowId ? { active: true, windowId } : { active: true, currentWindow: true };
    const tabs = await chrome.tabs.query(query);
    if (tabs[0]?.id) {
      await chrome.tabs.update(tabs[0].id, { url, active: true });
      return;
    }
  } catch (error) {
    await log(`Could not reuse active tab: ${error.message}`, windowId);
  }
  await chrome.tabs.create({ url, active: true, ...(windowId ? { windowId } : {}) });
}

function clampIntervalHours(value, fallback = DEFAULT_EPOST_AUTO_HOURS) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(1, Math.min(720, Math.round(parsed)));
}

function hoursToDueDays(hours) {
  return Math.max(1, Math.min(30, Math.ceil(clampIntervalHours(hours) / 24)));
}

async function scheduleAlarm(enabled, hours) {
  await chrome.alarms.clear(EPOST_TRACKING_ALARM);
  if (!enabled) return;
  const intervalHours = clampIntervalHours(hours);
  chrome.alarms.create(EPOST_TRACKING_ALARM, {
    delayInMinutes: Math.max(1, intervalHours * 60),
    periodInMinutes: Math.max(1, intervalHours * 60),
  });
}

async function restoreAlarm() {
  const state = await getState();
  const migratedHours = state.intervalHours || Math.max(1, Number(state.intervalDays || 1)) * 24;
  await scheduleAlarm(state.autoEpostEnabled === true, migratedHours);
}

async function startScheduledEpost() {
  const state = await getState();
  if (state.autoEpostEnabled !== true) return { ok: false, message: "ePost auto tracking is disabled." };
  if (state.epostRun?.running) {
    await log("Skipped scheduled ePost tracking because a visible ePost run is already active.");
    return { ok: true, skipped: true };
  }
  await log(`Scheduled ePost tracking started; interval is every ${clampIntervalHours(state.intervalHours)} hour(s).`);
  return startEpost(null);
}

async function startEpost(windowId = null) {
  const state = await getState();
  if (state.headlessEpostMode) return startHeadlessEpost();
  await log("Starting visible ePost tracking.", windowId);
  try {
    const intervalHours = clampIntervalHours(state.intervalHours || (Number(state.intervalDays || 1) * 24));
    const dueDays = hoursToDueDays(intervalHours);
    const payload = await api(`/api/epost/due?days=${encodeURIComponent(dueDays)}&hours=${encodeURIComponent(intervalHours)}`);
    const rows = payload.rows || [];
    const batches = chunk(rows.map((row) => row.tracking_code).filter(Boolean), 25);
    const epostRun = { running: true, batches, batchIndex: 0, submittedBatchIndex: null };
    if (!batches.length) {
      epostRun.running = false;
      await saveEpostRun(epostRun, windowId);
      await log("No ePost tracking codes are due.", windowId);
      return { ok: false, message: "No ePost tracking codes are due." };
    }
    await saveEpostRun(epostRun, windowId);
    await log(`Loaded ${rows.length} due ePost code(s) in ${batches.length} batch(es).`, windowId);
    await openTracker(windowId);
    await log("Opened ePost portal for visible tracking.", windowId);
    return { ok: true, message: `Tracking ${rows.length} ePost code(s).` };
  } catch (error) {
    await log(`Visible ePost tracking failed to start: ${error.message}`, windowId);
    throw error;
  }
}

async function stopEpost(windowId) {
  const { headlessEpostMode } = await getState();
  if (headlessEpostMode) return stopHeadlessEpost();
  const { epostRun } = await getWindowState(windowId);
  epostRun.running = false;
  await saveEpostRun(epostRun, windowId);
  await log("ePost tracking stopped.", windowId);
  return { ok: true, message: "Stopped." };
}

async function startHeadlessEpost() {
  const { intervalDays, intervalHours } = await getState();
  const hours = clampIntervalHours(intervalHours || (Number(intervalDays || 1) * 24));
  const dueDays = hoursToDueDays(hours);
  const result = await api("/api/epost/browserless/run", {
    method: "POST",
    body: JSON.stringify({ worker_id: `epost-extension-${chrome.runtime.id || "local"}`, interval_days: dueDays, interval_hours: hours }),
    timeoutMs: 10000,
  });
  await log(result.message || "Headless ePost started.");
  return result;
}

async function stopHeadlessEpost() {
  const result = await api("/api/epost/browserless/stop", {
    method: "POST",
    body: JSON.stringify({}),
    timeoutMs: 10000,
  });
  await log(result.message || "Headless ePost stop requested.");
  return result;
}

async function headlessEpostStatus() {
  return api("/api/epost/browserless/status", { timeoutMs: 10000 });
}

async function openHeadlessSession() {
  const result = await api("/api/chrome/browserless/open-session", {
    method: "POST",
    body: JSON.stringify({}),
    timeoutMs: 10000,
  });
  await log(result.message || "Opened shared headless Chrome session.");
  return result;
}

async function handlePortalReady(windowId) {
  const { epostRun } = await getWindowState(windowId);
  if (!epostRun.running) return { ok: false };
  const submittedBatchIndex = Number.isInteger(epostRun.submittedBatchIndex) ? epostRun.submittedBatchIndex : null;
  const batchIndex = submittedBatchIndex ?? epostRun.batchIndex;
  const codes = epostRun.batches[batchIndex] || [];
  return { ok: true, codes, batchIndex, submitted: submittedBatchIndex !== null };
}

async function handleBatchSubmitted(message, windowId) {
  const { epostRun } = await getWindowState(windowId);
  if (!epostRun.running) return { ok: false };
  if (message.batchIndex !== epostRun.batchIndex) {
    await log(`Ignored stale ePost submit batch ${Number(message.batchIndex) + 1}.`, windowId);
    return { ok: false, stale: true };
  }
  epostRun.submittedBatchIndex = message.batchIndex;
  await saveEpostRun(epostRun, windowId);
  await log(`Submitted ePost batch ${message.batchIndex + 1}/${epostRun.batches.length}.`, windowId);
  return { ok: true };
}

async function handleResults(message, windowId) {
  const { epostRun } = await getWindowState(windowId);
  if (!epostRun.running) return { ok: false };
  const expectedBatchIndex = Number.isInteger(epostRun.submittedBatchIndex) ? epostRun.submittedBatchIndex : epostRun.batchIndex;
  if (Number.isInteger(message.batchIndex) && message.batchIndex !== expectedBatchIndex) {
    await log(`Ignored stale ePost result batch ${message.batchIndex + 1}.`, windowId);
    return { ok: false, stale: true };
  }
  const resultCount = message.results?.length || 0;
  epostRun.submittedBatchIndex = null;
  epostRun.batchIndex = expectedBatchIndex + 1;
  if (epostRun.batchIndex >= epostRun.batches.length) {
    epostRun.running = false;
    await saveEpostRun(epostRun, windowId);
    try {
      await api("/api/epost/update", {
        method: "POST",
        body: JSON.stringify({ results: message.results || [] }),
      });
      await log(`Posted ${resultCount} ePost result(s).`, windowId);
    } catch (error) {
      await log(`ePost result upload failed after final batch: ${error.message}`, windowId);
    }
    await log("ePost tracking run complete.", windowId);
    return { ok: true, done: true };
  }
  await saveEpostRun(epostRun, windowId);
  try {
    await api("/api/epost/update", {
      method: "POST",
      body: JSON.stringify({ results: message.results || [] }),
    });
    await log(`Posted ${resultCount} ePost result(s).`, windowId);
  } catch (error) {
      await log(`ePost result upload failed after batch ${expectedBatchIndex + 1}: ${error.message}`, windowId);
  }
  await log(`Moving to ePost batch ${epostRun.batchIndex + 1}/${epostRun.batches.length}.`, windowId);
  await openTracker(windowId);
  return { ok: true };
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === EPOST_TRACKING_ALARM) {
    startScheduledEpost().catch((error) => log(`Scheduled ePost tracking failed: ${error.message}`));
  }
});

chrome.runtime.onInstalled.addListener(() => {
  restoreAlarm().catch((error) => log(`Could not restore ePost auto tracking: ${error.message}`));
});

chrome.runtime.onStartup.addListener(() => {
  restoreAlarm()
    .then(() => startScheduledEpost())
    .catch((error) => log(`Could not start ePost auto tracking after Chrome startup: ${error.message}`));
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    const windowId = messageWindowId(message, sender);
    if (message.type === "GET_STATE") return getWindowState(windowId);
    if (message.type === "TEST_CONNECTION") return testConnection();
    if (message.type === "SET_SETTINGS") {
      const intervalHours = clampIntervalHours(message.intervalHours ?? (Number(message.intervalDays || 1) * 24));
      await chrome.storage.local.set({
        apiBase: normalizeApiBase(message.apiBase),
        adminToken: message.adminToken || "",
        intervalDays: hoursToDueDays(intervalHours),
        intervalHours,
        autoEpostEnabled: message.autoEpostEnabled === true,
        headlessEpostMode: message.headlessEpostMode === true,
      });
      await scheduleAlarm(message.autoEpostEnabled === true, intervalHours);
      return { ok: true };
    }
    if (message.type === "START_EPOST") return startEpost(windowId);
    if (message.type === "STOP_EPOST") return stopEpost(windowId);
    if (message.type === "START_HEADLESS_EPOST") return startHeadlessEpost();
    if (message.type === "STOP_HEADLESS_EPOST") return stopHeadlessEpost();
    if (message.type === "GET_HEADLESS_EPOST_STATUS") return headlessEpostStatus();
    if (message.type === "OPEN_HEADLESS_SESSION") return openHeadlessSession();
    if (message.type === "PORTAL_READY") return handlePortalReady(windowId);
    if (message.type === "BATCH_SUBMITTED") return handleBatchSubmitted(message, windowId);
    if (message.type === "EPOST_RESULTS") return handleResults(message, windowId);
    return { ok: false, message: "Unknown message." };
  })()
    .then((result) => sendResponse(result))
    .catch((error) => sendResponse({ ok: false, message: error.message }));
  return true;
});
