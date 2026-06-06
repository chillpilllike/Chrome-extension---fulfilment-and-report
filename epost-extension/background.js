const DEFAULT_API_BASE = "http://127.0.0.1:8000";
const DEFAULT_REQUEST_TIMEOUT_MS = 45000;
const TRACKER_URL = "https://portal.epgshipping.com/ParcelTracker/HomePageTracker";
const EPOST_TRACKING_ALARM = "epostTracking";
const EPOST_WATCHDOG_ALARM = "epostTrackingWatchdog";
const DEFAULT_EPOST_AUTO_HOURS = 24;
const EPOST_STEP_TIMEOUT_MS = 120000;
const RECENT_EPOST_CACHE_TTL_MS = 36 * 60 * 60 * 1000;

async function getState() {
  return chrome.storage.local.get({
    apiBase: DEFAULT_API_BASE,
    adminToken: "",
    intervalDays: 1,
    intervalHours: DEFAULT_EPOST_AUTO_HOURS,
    autoEpostEnabled: false,
    headlessEpostMode: false,
    epostRun: { running: false, batches: [], batchIndex: 0 },
    recentEpostChecks: [],
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
  epostRun.updatedAt = Date.now();
  if (!windowId) {
    await chrome.storage.local.set({ epostRun });
    return;
  }
  const { epostRunByWindow } = await getState();
  await chrome.storage.local.set({ epostRunByWindow: { ...(epostRunByWindow || {}), [String(windowId)]: epostRun }, epostRun });
}

function stoppedEpostRun(run = {}, message = "ePost tracking stopped.") {
  return {
    ...(run || {}),
    running: false,
    submittedBatchIndex: null,
    lastMessage: message,
    stoppedAt: Date.now(),
    updatedAt: Date.now(),
  };
}

async function stopAutoEpostRuns(message = "ePost auto tracking is off; saved auto run stopped.") {
  const state = await getState();
  const updates = {};
  const rootRun = state.epostRun || {};
  if (rootRun.running && rootRun.source === "auto") {
    updates.epostRun = stoppedEpostRun(rootRun, message);
  }
  const nextByWindow = { ...(state.epostRunByWindow || {}) };
  let changedByWindow = false;
  for (const [key, run] of Object.entries(nextByWindow)) {
    if (run?.running && run.source === "auto") {
      nextByWindow[key] = stoppedEpostRun(run, message);
      changedByWindow = true;
    }
  }
  if (changedByWindow) updates.epostRunByWindow = nextByWindow;
  if (Object.keys(updates).length) await chrome.storage.local.set(updates);
  await clearEpostWatchdogIfIdle();
}

function flatCodes(batches = []) {
  return (batches || []).flat().map((code) => String(code || "").trim().toUpperCase()).filter(Boolean);
}

function epostProgress(epostRun = {}) {
  const batches = epostRun.batches || [];
  const totalCodes = flatCodes(batches).length;
  const processedCodes = flatCodes(batches.slice(0, Math.max(0, Number(epostRun.batchIndex || 0)))).length;
  const currentBatch = batches[epostRun.batchIndex] || [];
  return {
    total_batches: batches.length,
    processed_batches: Math.min(Number(epostRun.batchIndex || 0), batches.length),
    total_codes: totalCodes,
    processed_codes: Math.min(processedCodes, totalCodes),
    current_batch_size: currentBatch.length,
    completed_codes: epostRun.completedCodes?.length || 0,
    failed_codes: epostRun.failedCodes?.length || 0,
    skipped_recent: epostRun.skippedRecentCount || 0,
    started_at: epostRun.startedAt || null,
    last_activity_at: epostRun.lastActivityAt || epostRun.updatedAt || null,
    message: epostRun.running ? "ePost tracking is running." : "ePost tracking is stopped.",
  };
}

async function rememberRecentEpostCodes(codes, status = "checked") {
  const cleanCodes = (codes || []).map((code) => String(code || "").trim().toUpperCase()).filter(Boolean);
  if (!cleanCodes.length) return;
  const { recentEpostChecks } = await getState();
  const cutoff = Date.now() - RECENT_EPOST_CACHE_TTL_MS;
  const seen = new Set(cleanCodes);
  const next = cleanCodes.map((tracking_code) => ({ tracking_code, checkedAt: Date.now(), status }))
    .concat((recentEpostChecks || []).filter((item) => !seen.has(String(item.tracking_code || "").trim().toUpperCase()) && Number(item.checkedAt || 0) >= cutoff))
    .slice(0, 5000);
  await chrome.storage.local.set({ recentEpostChecks: next });
}

function recentEpostSet(recentEpostChecks = []) {
  const cutoff = Date.now() - RECENT_EPOST_CACHE_TTL_MS;
  return new Set(
    (recentEpostChecks || [])
      .filter((item) => Number(item.checkedAt || 0) >= cutoff)
      .map((item) => String(item.tracking_code || "").trim().toUpperCase())
      .filter(Boolean),
  );
}

async function clearEpostWatchdogIfIdle() {
  const state = await getState();
  const anyWindowRunning = Object.values(state.epostRunByWindow || {}).some((item) => item?.running);
  if (!state.epostRun?.running && !anyWindowRunning) {
    await chrome.alarms.clear(EPOST_WATCHDOG_ALARM);
  }
}

async function ensureEpostWatchdog() {
  chrome.alarms.create(EPOST_WATCHDOG_ALARM, { periodInMinutes: 1 });
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

function isEpostTrackerUrl(url = "") {
  try {
    const parsed = new URL(url);
    return /(^|\.)epgshipping\.com$/i.test(parsed.hostname) && /\/ParcelTracker\/HomePageTracker/i.test(parsed.pathname);
  } catch {
    return false;
  }
}

async function openTracker(windowId) {
  const url = `${TRACKER_URL}?nutricityBatch=${Date.now()}`;
  try {
    const activeQuery = windowId ? { active: true, windowId } : { active: true, currentWindow: true };
    const tabs = await chrome.tabs.query(activeQuery);
    if (tabs[0]?.id && isEpostTrackerUrl(tabs[0].url || "")) {
      await chrome.tabs.update(tabs[0].id, { url, active: true });
      return;
    }
    const candidateQuery = windowId ? { windowId } : { currentWindow: true };
    const candidates = await chrome.tabs.query(candidateQuery);
    const reusable = candidates.find((tab) => tab.id && isEpostTrackerUrl(tab.url || ""));
    if (reusable?.id) {
      await chrome.tabs.update(reusable.id, { url, active: true });
      return;
    }
  } catch (error) {
    await log(`Could not reuse ePost tab: ${error.message}`, windowId);
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
  if (state.autoEpostEnabled !== true) {
    await stopAutoEpostRuns();
    return { ok: false, message: "ePost auto tracking is disabled." };
  }
  if (state.epostRun?.running) {
    await log("Scheduled ePost tracking found an existing run; resuming from the saved batch.");
    await ensureEpostWatchdog();
    await openTracker(null);
    return { ok: true, resumed: true, progress: epostProgress(state.epostRun) };
  }
  await log(`Scheduled ePost tracking started; interval is every ${clampIntervalHours(state.intervalHours)} hour(s).`);
  return startEpost(null, { includeRecent: true, source: "auto" });
}

async function startEpost(windowId = null, options = {}) {
  const state = await getState();
  const includeRecent = options.includeRecent !== false;
  const source = options.source || "manual";
  if (state.headlessEpostMode) return startHeadlessEpost({ includeRecent, source });
  await log("Starting visible ePost tracking.", windowId);
  try {
    const intervalHours = clampIntervalHours(state.intervalHours || (Number(state.intervalDays || 1) * 24));
    const dueDays = hoursToDueDays(intervalHours);
    const payload = await api(`/api/epost/due?days=${encodeURIComponent(dueDays)}&hours=${encodeURIComponent(intervalHours)}${includeRecent ? "&include_recent=true" : ""}`);
    const recent = recentEpostSet(state.recentEpostChecks);
    const allRows = payload.rows || [];
    const rows = includeRecent ? allRows : allRows.filter((row) => !recent.has(String(row.tracking_code || "").trim().toUpperCase()));
    const batches = chunk(rows.map((row) => row.tracking_code).filter(Boolean), 25);
    const epostRun = {
      running: true,
      batches,
      batchIndex: 0,
      submittedBatchIndex: null,
      completedCodes: [],
      failedCodes: [],
      skippedRecentCount: allRows.length - rows.length,
      startedAt: Date.now(),
      lastActivityAt: Date.now(),
      lastMessage: includeRecent ? "ePost tracking started for all undelivered codes." : "ePost tracking started.",
      includeRecent,
      source,
    };
    if (!batches.length) {
      epostRun.running = false;
      await saveEpostRun(epostRun, windowId);
      const message = includeRecent ? "No undelivered ePost tracking codes found." : "No ePost tracking codes are due.";
      await log(message, windowId);
      return { ok: false, message };
    }
    await saveEpostRun(epostRun, windowId);
    await ensureEpostWatchdog();
    await log(`Loaded ${rows.length} ${includeRecent ? "undelivered" : "due"} ePost code(s) in ${batches.length} batch(es); skipped ${epostRun.skippedRecentCount} recently checked code(s).`, windowId);
    await openTracker(windowId);
    await log("Opened ePost portal for visible tracking.", windowId);
    return { ok: true, message: `Tracking ${rows.length} ePost code(s).`, progress: epostProgress(epostRun) };
  } catch (error) {
    await log(`Visible ePost tracking failed to start: ${error.message}`, windowId);
    throw error;
  }
}

async function stopEpost(windowId) {
  const { headlessEpostMode } = await getState();
  if (headlessEpostMode) return stopHeadlessEpost();
  const state = await getState();
  const stopped = stoppedEpostRun(state.epostRun || {}, "ePost tracking stopped by user.");
  const nextByWindow = {};
  for (const [key, run] of Object.entries(state.epostRunByWindow || {})) {
    nextByWindow[key] = stoppedEpostRun(run, "ePost tracking stopped by user.");
  }
  await chrome.storage.local.set({ epostRun: stopped, epostRunByWindow: nextByWindow });
  await log("ePost tracking stopped.", windowId);
  await clearEpostWatchdogIfIdle();
  return { ok: true, message: "Stopped." };
}

async function startHeadlessEpost(options = {}) {
  const { intervalDays, intervalHours } = await getState();
  const hours = clampIntervalHours(intervalHours || (Number(intervalDays || 1) * 24));
  const dueDays = hoursToDueDays(hours);
  const result = await api("/api/epost/browserless/run", {
    method: "POST",
    body: JSON.stringify({
      worker_id: `epost-extension-${chrome.runtime.id || "local"}`,
      interval_days: dueDays,
      interval_hours: hours,
      include_recent: options.includeRecent !== false,
    }),
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
  epostRun.lastActivityAt = Date.now();
  epostRun.lastMessage = `Portal ready for batch ${batchIndex + 1}/${epostRun.batches.length}.`;
  await saveEpostRun(epostRun, windowId);
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
  epostRun.lastActivityAt = Date.now();
  epostRun.lastMessage = `Submitted ePost batch ${message.batchIndex + 1}/${epostRun.batches.length}.`;
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
  const submittedCodes = epostRun.batches?.[expectedBatchIndex] || [];
  const completedCodes = (message.results || []).map((item) => item.tracking_code).filter(Boolean);
  const checkedCodes = completedCodes.length ? completedCodes : submittedCodes;
  await rememberRecentEpostCodes(checkedCodes, "checked");
  epostRun.completedCodes = [...(epostRun.completedCodes || []), ...checkedCodes];
  epostRun.submittedBatchIndex = null;
  epostRun.batchIndex = expectedBatchIndex + 1;
  epostRun.lastActivityAt = Date.now();
  epostRun.lastMessage = `Captured ePost batch ${expectedBatchIndex + 1}/${epostRun.batches.length}.`;
  if (epostRun.batchIndex >= epostRun.batches.length) {
    epostRun.running = false;
    epostRun.finishedAt = Date.now();
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
    await clearEpostWatchdogIfIdle();
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

async function skipStaleEpostBatch(epostRun, windowId) {
  if (!epostRun?.running) return false;
  const { autoEpostEnabled } = await getState();
  if (epostRun.source === "auto" && autoEpostEnabled !== true) {
    const stopped = stoppedEpostRun(epostRun, "ePost auto tracking is off; watchdog stopped this saved auto run.");
    await saveEpostRun(stopped, windowId);
    await log("Stopped saved ePost auto run because auto tracking is off.", windowId);
    await clearEpostWatchdogIfIdle();
    return true;
  }
  const lastActivityAt = Number(epostRun.lastActivityAt || epostRun.updatedAt || epostRun.startedAt || 0);
  if (!lastActivityAt || Date.now() - lastActivityAt < EPOST_STEP_TIMEOUT_MS) return false;
  const batchIndex = Number.isInteger(epostRun.submittedBatchIndex) ? epostRun.submittedBatchIndex : Number(epostRun.batchIndex || 0);
  const codes = epostRun.batches?.[batchIndex] || [];
  await rememberRecentEpostCodes(codes, "failed");
  epostRun.failedCodes = [...(epostRun.failedCodes || []), ...codes];
  epostRun.submittedBatchIndex = null;
  epostRun.batchIndex = batchIndex + 1;
  epostRun.lastActivityAt = Date.now();
  epostRun.lastMessage = `Timed out on ePost batch ${batchIndex + 1}; moving to next batch.`;
  await log(`ePost batch ${batchIndex + 1} timed out; skipped ${codes.length} code(s) for this session and continuing.`, windowId);
  if (epostRun.batchIndex >= (epostRun.batches?.length || 0)) {
    epostRun.running = false;
    epostRun.finishedAt = Date.now();
    await saveEpostRun(epostRun, windowId);
    await log("ePost tracking run complete after timeout recovery.", windowId);
    await clearEpostWatchdogIfIdle();
    return true;
  }
  await saveEpostRun(epostRun, windowId);
  await openTracker(windowId);
  return true;
}

async function runEpostWatchdog() {
  const state = await getState();
  let recovered = false;
  const windowEntries = Object.entries(state.epostRunByWindow || {}).filter(([, run]) => run?.running);
  if (!windowEntries.length && state.epostRun?.running) {
    recovered = await skipStaleEpostBatch(state.epostRun, null) || recovered;
  }
  for (const [key, run] of windowEntries) {
    recovered = await skipStaleEpostBatch(run, Number(key) || null) || recovered;
  }
  if (!recovered) await clearEpostWatchdogIfIdle();
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === EPOST_TRACKING_ALARM) {
    startScheduledEpost().catch((error) => log(`Scheduled ePost tracking failed: ${error.message}`));
  }
  if (alarm.name === EPOST_WATCHDOG_ALARM) {
    runEpostWatchdog().catch((error) => log(`ePost tracking watchdog failed: ${error.message}`));
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
    if (message.type === "GET_PROGRESS") {
      const { epostRun } = await getWindowState(windowId);
      return { ok: true, progress: epostProgress(epostRun) };
    }
    if (message.type === "TEST_CONNECTION") return testConnection();
    if (message.type === "SET_SETTINGS") {
      const intervalHours = clampIntervalHours(message.intervalHours ?? (Number(message.intervalDays || 1) * 24));
      const autoEpostEnabled = message.autoEpostEnabled === true;
      await chrome.storage.local.set({
        apiBase: normalizeApiBase(message.apiBase),
        adminToken: message.adminToken || "",
        intervalDays: hoursToDueDays(intervalHours),
        intervalHours,
        autoEpostEnabled,
        headlessEpostMode: message.headlessEpostMode === true,
      });
      await scheduleAlarm(autoEpostEnabled, intervalHours);
      if (!autoEpostEnabled) await stopAutoEpostRuns();
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
