const DEFAULT_API_BASE = "http://127.0.0.1:8000";

async function getSettings() {
  const data = await chrome.storage.local.get({
    apiBase: DEFAULT_API_BASE,
    adminToken: "",
    workerId: "",
    activeJob: null,
    activeJobsByWindow: {},
    logs: [],
    logsByWindow: {},
  });
  return data;
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

async function getWindowState(windowId) {
  const state = await getSettings();
  const key = String(windowId || "");
  return {
    ...state,
    targetWindowId: windowId || null,
    activeJob: windowId ? state.activeJobsByWindow?.[key] || null : state.activeJob,
    logs: windowId ? state.logsByWindow?.[key] || [] : state.logs,
  };
}

async function setWindowJob(windowId, activeJob) {
  const { activeJobsByWindow } = await getSettings();
  const next = { ...(activeJobsByWindow || {}) };
  const key = String(windowId || "");
  if (windowId && activeJob) next[key] = activeJob;
  if (windowId && !activeJob) delete next[key];
  await chrome.storage.local.set({ activeJobsByWindow: next, activeJob: activeJob || null });
}

async function log(message, windowId = null) {
  const { logs, logsByWindow } = await getSettings();
  const entry = `${new Date().toLocaleTimeString()} ${message}`;
  if (!windowId) {
    await chrome.storage.local.set({ logs: [entry, ...logs].slice(0, 40) });
    return;
  }
  const key = String(windowId);
  const next = { ...(logsByWindow || {}) };
  next[key] = [entry, ...(next[key] || [])].slice(0, 40);
  await chrome.storage.local.set({ logsByWindow: next, logs: next[key] });
}

async function api(path, options = {}) {
  const { apiBase, adminToken } = await getSettings();
  const base = normalizeApiBase(apiBase);
  const requestPath = String(path || "").startsWith("/") ? path : `/${path}`;
  const response = await fetch(`${base}${requestPath}`, {
    headers: { "Content-Type": "application/json", ...(adminToken ? { "X-Admin-Token": adminToken } : {}), ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    throw new Error((await response.text()) || response.statusText);
  }
  return response.json();
}

async function testConnection() {
  const { apiBase, adminToken } = await getSettings();
  const base = normalizeApiBase(apiBase);
  const health = await fetch(`${base}/health`);
  if (!health.ok) {
    throw new Error(`Server health check failed: HTTP ${health.status}`);
  }
  const auth = await fetch(`${base}/api/settings/admin-access`, {
    headers: adminToken ? { "X-Admin-Token": adminToken } : {},
  });
  if (!auth.ok) {
    throw new Error((await auth.text()) || `Admin token check failed: HTTP ${auth.status}`);
  }
  return { ok: true, message: `Connected to ${base}. Admin token accepted.` };
}

async function getQueueStatus() {
  const workerId = await getWorkerId();
  const payload = await api("/api/chrome/jobs?claim=false");
  return { ok: true, jobs: payload.jobs || [], workerId };
}

async function refreshActiveJobFromQueue(windowId, force = false) {
  const { activeJob } = await getWindowState(windowId);
  if (!activeJob?.job?.group_key) return activeJob;
  if (!force && Date.now() - Number(activeJob.jobRefreshedAt || 0) < 30000) return activeJob;
  try {
    const queue = await getQueueStatus();
    const freshJob = (queue.jobs || []).find((job) => job.group_key === activeJob.job.group_key);
    if (!freshJob) return activeJob;
    const { activeJob: currentJob } = await getWindowState(windowId);
    const next = { ...(currentJob || activeJob), job: freshJob, jobRefreshedAt: Date.now() };
    await setWindowJob(windowId, next);
    return next;
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
    await chrome.windows.update(existingTab.windowId, { focused: true });
    await chrome.tabs.update(existingTab.id, { active: true });
    return;
  }
  await chrome.windows.create({
    url: popupUrl,
    type: "popup",
    width: 430,
    height: 620,
    focused: true,
  });
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

async function navigateWindowToCart(windowId) {
  if (!windowId) return;
  const tabs = await chrome.tabs.query({ windowId });
  const tab = tabs.find((item) => item.active) || tabs[0];
  if (tab?.id) {
    await chrome.tabs.update(tab.id, { url: "https://www.amazon.com/cart?ref_=sw_gtc", active: true });
  }
  await chrome.windows.update(windowId, { focused: true });
}

async function startNextJob() {
  const workerId = await getWorkerId();
  const payload = await api(`/api/chrome/jobs?worker_id=${encodeURIComponent(workerId)}&claim=true`);
  const job = payload.jobs?.[0];
  if (!job) {
    await log("No queued Chrome jobs found.");
    return { ok: false, message: "No queued Chrome jobs found." };
  }
  const createdWindow = await chrome.windows.create({ url: "https://www.amazon.com/cart?ref_=sw_gtc", type: "normal", focused: true });
  const targetWindowId = createdWindow.id;
  const activeJob = activeJobFor(job, workerId, targetWindowId);
  await setWindowJob(targetWindowId, activeJob);
  await log(`Started ${job.group_key} with ${job.items.length} item(s).`, targetWindowId);
  return { ok: true, message: `Started ${job.group_key}.`, targetWindowId };
}

async function claimNextJobInWindow(windowId) {
  const workerId = await getWorkerId();
  const payload = await api(`/api/chrome/jobs?worker_id=${encodeURIComponent(workerId)}&claim=true`);
  const job = payload.jobs?.[0];
  if (!job) {
    await setWindowJob(windowId, null);
    await log("No more queued Chrome jobs found.", windowId);
    return null;
  }
  const activeJob = activeJobFor(job, workerId, windowId);
  await setWindowJob(windowId, activeJob);
  await log(`Started next ${job.group_key} with ${job.items.length} item(s).`, windowId);
  await navigateWindowToCart(windowId);
  return activeJob;
}

async function stopJob(windowId) {
  const { activeJob } = await getWindowState(windowId);
  if (activeJob?.job?.group_key && activeJob?.workerId) {
    try {
      await api(`/api/chrome/jobs/${encodeURIComponent(activeJob.job.group_key)}/release`, {
        method: "POST",
        body: JSON.stringify({ worker_id: activeJob.workerId }),
      });
    } catch (error) {
      await log(`Could not release ${activeJob.job.group_key}: ${error.message}`, windowId);
    }
  }
  await setWindowJob(windowId, null);
  await log("Stopped active job.", windowId);
  return { ok: true, message: "Stopped active job." };
}

async function heartbeatJob(activeJob, windowId) {
  if (!activeJob?.job?.group_key || !activeJob?.workerId) return;
  await api(`/api/chrome/jobs/${encodeURIComponent(activeJob.job.group_key)}/heartbeat`, {
    method: "POST",
    body: JSON.stringify({ worker_id: activeJob.workerId }),
  });
  activeJob.lastHeartbeatAt = Date.now();
  await setWindowJob(windowId, activeJob);
}

async function togglePause(windowId) {
  const { activeJob } = await getWindowState(windowId);
  if (!activeJob?.job) return { ok: false, message: "No active job." };
  activeJob.paused = !activeJob.paused;
  await setWindowJob(windowId, activeJob);
  await log(`${activeJob.paused ? "Paused" : "Resumed"} ${activeJob.job.group_key}.`, windowId);
  return { ok: true, paused: activeJob.paused, message: activeJob.paused ? "Paused fulfilment." : "Resumed fulfilment." };
}

async function completeJob(orderId, orderUrl, amazonAccountName, windowId) {
  const { activeJob } = await getWindowState(windowId);
  if (!activeJob?.job) return { ok: false, message: "No active job." };
  await heartbeatJob(activeJob, windowId);
  const body = {
    amazon_order_id: orderId || "",
    amazon_order_url: orderUrl || "",
    amazon_account_name: amazonAccountName || "",
    line_ids: activeJob.job.line_ids || [],
    pricing_summary: Object.values(activeJob.pricing || {}),
    worker_id: activeJob.workerId || "",
  };
  const result = await api(`/api/chrome/jobs/${encodeURIComponent(activeJob.job.group_key)}/complete`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  await log(`Completed ${activeJob.job.group_key} as ${result.amazon_order_id}.`, windowId);
  const nextJob = await claimNextJobInWindow(windowId);
  return { ...result, next_job_started: Boolean(nextJob), next_group_key: nextJob?.job?.group_key || "" };
}

async function failJob(message, details = {}, windowId) {
  const { activeJob } = await getWindowState(windowId);
  if (!activeJob?.job) return { ok: false, message: "No active job." };
  await heartbeatJob(activeJob, windowId);
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
  const nextJob = await claimNextJobInWindow(windowId);
  return { ...result, next_job_started: Boolean(nextJob), next_group_key: nextJob?.job?.group_key || "" };
}

async function costlyJob(message, details = {}, windowId) {
  const { activeJob } = await getWindowState(windowId);
  if (!activeJob?.job) return { ok: false, message: "No active job." };
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
  return result;
}

async function clearFailedJobs() {
  const result = await api("/api/chrome/failed-jobs/clear", { method: "POST", body: JSON.stringify({}) });
  await chrome.storage.local.set({ activeJob: null });
  await log(result.message || `Cleared ${result.cleared || 0} failed Chrome job line(s).`);
  return result;
}

chrome.action.onClicked.addListener((tab) => {
  openControlWindow(tab);
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    const windowId = messageWindowId(message, sender);
    if (message.type === "START_NEXT") return startNextJob();
    if (message.type === "STOP_JOB") return stopJob(windowId);
    if (message.type === "TOGGLE_PAUSE") return togglePause(windowId);
    if (message.type === "GET_STATE") {
      await refreshActiveJobFromQueue(windowId, true);
      return getWindowState(windowId);
    }
    if (message.type === "GET_QUEUE_STATUS") return getQueueStatus();
    if (message.type === "TEST_CONNECTION") return testConnection();
    if (message.type === "GET_ACTIVE_JOB") {
      const { activeJob } = await getWindowState(windowId);
      if (activeJob?.job && activeJob?.workerId && Date.now() - Number(activeJob.lastHeartbeatAt || 0) > 5 * 60 * 1000) {
        try {
          await heartbeatJob(activeJob, windowId);
        } catch (error) {
          await log(`Chrome job lock heartbeat failed: ${error.message}`, windowId);
        }
      }
      return { ok: true, activeJob: (await getWindowState(windowId)).activeJob };
    }
    if (message.type === "HEARTBEAT_JOB") {
      const { activeJob } = await getWindowState(windowId);
      if (!activeJob?.job) return { ok: false, message: "No active job." };
      await heartbeatJob(activeJob, windowId);
      return { ok: true };
    }
    if (message.type === "SET_ACTIVE_JOB") {
      await setWindowJob(windowId, message.activeJob || null);
      return { ok: true };
    }
    if (message.type === "SET_API_BASE") {
      await chrome.storage.local.set({ apiBase: normalizeApiBase(message.apiBase), adminToken: message.adminToken || "" });
      return { ok: true };
    }
    if (message.type === "COMPLETE_JOB") return completeJob(message.orderId, message.orderUrl, message.amazonAccountName || "", windowId);
    if (message.type === "FAIL_JOB") return failJob(message.message || "Chrome extension job failed.", message, windowId);
    if (message.type === "COSTLY_JOB") return costlyJob(message.message || "Chrome extension job needs costly approval.", message, windowId);
    if (message.type === "CLEAR_FAILED_JOBS") return clearFailedJobs();
    return { ok: false, message: "Unknown message." };
  })()
    .then((result) => sendResponse(result))
    .catch((error) => sendResponse({ ok: false, message: error.message }));
  return true;
});
