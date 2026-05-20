const apiBase = document.querySelector("#apiBase");
const adminToken = document.querySelector("#adminToken");
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
let settingsDirty = false;
let settingsHydrated = false;

[apiBase, adminToken].forEach((input) => {
  input.addEventListener("input", () => {
    settingsDirty = true;
  });
});

function syncSettingsInputs(state) {
  if (settingsHydrated || settingsDirty || [apiBase, adminToken].includes(document.activeElement)) return;
  apiBase.value = state.apiBase || "http://127.0.0.1:8000";
  adminToken.value = state.adminToken || "";
  settingsHydrated = true;
}

function setStatus(text) {
  statusBox.textContent = text;
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

async function refresh() {
  const state = await send({ type: "GET_STATE" });
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
  } catch (error) {
    queueCount.textContent = "Not loaded";
    queuedJobs.innerHTML = `<div class="empty-queue">${error.message || "Could not load queue."}</div>`;
  }
}

document.querySelector("#save").addEventListener("click", async () => {
  const result = await send({ type: "SET_API_BASE", apiBase: apiBase.value.trim(), adminToken: adminToken.value.trim() });
  if (result.ok) settingsDirty = false;
  setStatus(result.ok ? "Saved." : result.message);
});

document.querySelector("#testConnection").addEventListener("click", async () => {
  await send({ type: "SET_API_BASE", apiBase: apiBase.value.trim(), adminToken: adminToken.value.trim() });
  settingsDirty = false;
  const result = await send({ type: "TEST_CONNECTION" });
  setStatus(result.message || (result.ok ? "Connection ok." : "Connection failed."));
});

document.querySelector("#start").addEventListener("click", async () => {
  const result = await send({ type: "START_NEXT" });
  if (result.targetWindowId) targetWindowId = result.targetWindowId;
  setStatus(result.message || (result.ok ? "Started." : "Could not start."));
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

refresh();
setInterval(refresh, 3000);
