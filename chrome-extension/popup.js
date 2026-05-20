const apiBase = document.querySelector("#apiBase");
const adminToken = document.querySelector("#adminToken");
const cardLast4Preference = document.querySelector("#cardLast4Preference");
const editExistingAddress = document.querySelector("#editExistingAddress");
const connectionNotice = document.querySelector("#connectionNotice");
const statusBox = document.querySelector("#status");
const logsBox = document.querySelector("#logs");
const pricingRows = document.querySelector("#pricingRows");
const pricingTotal = document.querySelector("#pricingTotal");
const pauseResume = document.querySelector("#pauseResume");
const queuedJobs = document.querySelector("#queuedJobs");
const queueCount = document.querySelector("#queueCount");

function send(message) {
  return chrome.runtime.sendMessage({ ...message, targetWindowId });
}

let targetWindowId = Number(new URLSearchParams(location.search).get("targetWindowId") || 0) || null;
let controlWindowId = null;
let settingsDirty = false;
let settingsHydrated = false;

function hydrateSettingsValues(settings = {}) {
  apiBase.value = settings.apiBase || "http://127.0.0.1:8000";
  adminToken.value = settings.adminToken || "";
  cardLast4Preference.value = settings.cardLast4Preference || "";
  editExistingAddress.checked = settings.editExistingAddress !== false;
}

function hasSettingsPayload(state) {
  return Boolean(state && typeof state === "object" && (
    Object.hasOwn(state, "apiBase") ||
    Object.hasOwn(state, "adminToken") ||
    Object.hasOwn(state, "cardLast4Preference") ||
    Object.hasOwn(state, "editExistingAddress")
  ));
}

function loadSavedSettings() {
  chrome.storage.local.get({
    apiBase: "http://127.0.0.1:8000",
    adminToken: "",
    cardLast4Preference: "",
    editExistingAddress: true,
  }, (settings) => {
    if (chrome.runtime.lastError || settingsHydrated || settingsDirty || [apiBase, adminToken, cardLast4Preference, editExistingAddress].includes(document.activeElement)) return;
    hydrateSettingsValues(settings);
    settingsHydrated = true;
  });
}

function registerControlWindow() {
  if (!chrome.windows?.getCurrent) return;
  chrome.windows.getCurrent((windowInfo) => {
    if (chrome.runtime.lastError) return;
    controlWindowId = Number(windowInfo?.id || 0) || null;
    if (!controlWindowId) return;
    chrome.runtime.sendMessage({ type: "REGISTER_CONTROL_WINDOW", controlWindowId, targetWindowId });
  });
}

[apiBase, adminToken, cardLast4Preference, editExistingAddress].forEach((input) => {
  input.addEventListener("input", () => {
    settingsDirty = true;
  });
  input.addEventListener("change", () => {
    settingsDirty = true;
  });
});

function syncSettingsInputs(state) {
  if (!hasSettingsPayload(state) || settingsHydrated || settingsDirty || [apiBase, adminToken, cardLast4Preference, editExistingAddress].includes(document.activeElement)) return;
  hydrateSettingsValues(state);
  settingsHydrated = true;
}

function setStatus(text) {
  statusBox.textContent = text;
}

function setConnectionNotice(text, success = true) {
  connectionNotice.textContent = text || "";
  connectionNotice.hidden = !text;
  connectionNotice.classList.toggle("error", !success);
}

function money(value) {
  const number = Number(value || 0);
  return number.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

function renderPricing(activeJob) {
  pricingRows.innerHTML = "";
  const items = activeJob?.job?.items || [];
  const pricing = activeJob?.pricing || {};
  let total = 0;
  for (const item of items) {
    const rowPricing = pricing[item.asin] || {};
    const qty = Number(item.quantity || 1);
    const storeTotal = Number(item.store_total_price ?? rowPricing.store_total_price ?? 0);
    const amazonTotal = Number(rowPricing.amazon_total_price || 0);
    const profit = amazonTotal ? storeTotal - amazonTotal : 0;
    total += profit;
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${item.asin}</td>
      <td>${qty}</td>
      <td>${money(storeTotal)}</td>
      <td>${amazonTotal ? money(amazonTotal) : "Pending"}</td>
      <td class="${profit >= 0 ? "profit" : "loss"}">${amazonTotal ? money(profit) : "Pending"}</td>
    `;
    pricingRows.append(row);
  }
  pricingTotal.textContent = money(total);
  pricingTotal.className = total >= 0 ? "profit" : "loss";
}

function shortWorkerId(value) {
  const text = String(value || "");
  return text.length > 18 ? `${text.slice(0, 10)}...${text.slice(-5)}` : text;
}

function renderQueuedJobs(jobs, workerId = "") {
  const queue = Array.isArray(jobs) ? jobs : [];
  const locked = queue.filter((job) => job.claimed_by).length;
  queueCount.textContent = locked ? `${queue.length} queued · ${locked} locked` : `${queue.length} waiting`;
  queuedJobs.innerHTML = "";
  if (!queue.length) {
    const empty = document.createElement("div");
    empty.className = "empty-queue";
    empty.textContent = "No queued Chrome orders.";
    queuedJobs.append(empty);
    return;
  }
  for (const job of queue) {
    const itemCount = (job.items || []).reduce((total, item) => total + Number(item.quantity || 0), 0);
    const asins = (job.items || []).map((item) => item.asin).filter(Boolean).join(", ");
    const claimedBy = String(job.claimed_by || "");
    const isLocked = Boolean(claimedBy);
    const isMine = isLocked && claimedBy === workerId;
    const lockText = isLocked
      ? `${isMine ? "Locked by this Chrome" : "Locked by another Chrome"}${job.claim_expires_at ? ` until ${new Date(job.claim_expires_at).toLocaleTimeString()}` : ""}`
      : "Ready";
    const row = document.createElement("div");
    row.className = `queued-job${isLocked ? " locked" : ""}`;
    row.innerHTML = `
      <strong>${isLocked ? `<span class="lock-icon" title="${lockText}" aria-label="${lockText}"></span>` : ""}${(job.order_names || []).join(", ") || job.group_key}</strong>
      <span>${job.recipient_name || "No recipient"} · ${itemCount || 0} item(s)</span>
      <span>${asins || "No ASINs"}</span>
      <span class="lock-status">${lockText}${isLocked && !isMine ? ` · ${shortWorkerId(claimedBy)}` : ""}</span>
    `;
    queuedJobs.append(row);
  }
}

function queueCountDetails(counts = []) {
  const extras = (Array.isArray(counts) ? counts : [])
    .filter((item) => item.state !== "submitted" && Number(item.count || 0) > 0)
    .map((item) => `${item.count} ${item.state}`)
  return extras.length ? ` · ${extras.join(" · ")}` : "";
}

async function refresh() {
  const state = await send({ type: "GET_STATE" });
  if (state?.ok === false) {
    setStatus(state.message || "Could not load extension state.");
    return;
  }
  syncSettingsInputs(state);
  const job = state.activeJob?.job;
  pauseResume.textContent = state.activeJob?.paused ? "Resume" : "Pause";
  pauseResume.disabled = !job;
  setStatus(job ? `${state.activeJob.paused ? "Paused" : "Active"}: ${job.group_key} (${state.activeJob.stage})` : "No active job.");
  renderPricing(state.activeJob);
  logsBox.innerHTML = "";
  for (const line of state.logs || []) {
    const item = document.createElement("li");
    item.textContent = line;
    logsBox.append(item);
  }
  try {
    const queue = await send({ type: "GET_QUEUE_STATUS" });
    if (!queue.ok) throw new Error(queue.message || "Could not load queue.");
    renderQueuedJobs(queue.jobs || [], queue.workerId || state.workerId || "");
    queueCount.textContent += queueCountDetails(queue.counts || []);
  } catch (error) {
    queueCount.textContent = "Not loaded";
    queuedJobs.innerHTML = `<div class="empty-queue">${error.message || "Could not load queue. Check App URL, Admin token, then click Save."}</div>`;
  }
}

document.querySelector("#save").addEventListener("click", async () => {
  const result = await send({ type: "SET_API_BASE", apiBase: apiBase.value.trim(), adminToken: adminToken.value.trim(), cardLast4Preference: cardLast4Preference.value.trim(), editExistingAddress: editExistingAddress.checked });
  if (result.ok) settingsDirty = false;
  setStatus(result.ok ? "Saved." : result.message);
});

document.querySelector("#testConnection").addEventListener("click", async () => {
  await send({ type: "SET_API_BASE", apiBase: apiBase.value.trim(), adminToken: adminToken.value.trim(), cardLast4Preference: cardLast4Preference.value.trim(), editExistingAddress: editExistingAddress.checked });
  settingsDirty = false;
  const result = await send({ type: "TEST_CONNECTION" });
  if (result.ok) {
    setConnectionNotice("Connection successful.", true);
  } else {
    setConnectionNotice("", false);
  }
  setStatus(result.message || (result.ok ? "Connection ok." : "Connection failed."));
});

document.querySelector("#start").addEventListener("click", async () => {
  setStatus("Starting next queued order...");
  try {
    const result = await send({ type: "START_NEXT" });
    if (result.targetWindowId) {
      targetWindowId = result.targetWindowId;
      registerControlWindow();
    }
    setStatus(result.message || (result.ok ? "Started." : "Could not start."));
  } catch (error) {
    setStatus(error.message || "Could not start queued order.");
  }
});

document.querySelector("#stop").addEventListener("click", async () => {
  const result = await send({ type: "STOP_JOB" });
  setStatus(result.message);
});

pauseResume.addEventListener("click", async () => {
  const result = await send({ type: "TOGGLE_PAUSE" });
  setStatus(result.message || (result.ok ? "Updated." : "Could not pause/resume."));
});

document.querySelector("#clearFailed").addEventListener("click", async () => {
  const confirmed = confirm("Clear failed or stale queued Chrome jobs in the app so they can be queued again?");
  if (!confirmed) return;
  const result = await send({ type: "CLEAR_FAILED_JOBS" });
  setStatus(result.message || (result.ok ? "Stale queue cleared." : "Could not clear stale queue."));
});

loadSavedSettings();
registerControlWindow();
refresh();
setInterval(refresh, 3000);
