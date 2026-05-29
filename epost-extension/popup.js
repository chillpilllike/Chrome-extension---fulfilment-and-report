const apiBase = document.querySelector("#apiBase");
const adminToken = document.querySelector("#adminToken");
const intervalHours = document.querySelector("#intervalHours");
const autoEpostEnabled = document.querySelector("#autoEpostEnabled");
const headlessEpostMode = document.querySelector("#headlessEpostMode");
const modeNotice = document.querySelector("#modeNotice");
const statusBox = document.querySelector("#status");
const logsBox = document.querySelector("#logs");
const startButton = document.querySelector("#start");
const stopButton = document.querySelector("#stop");

let targetWindowId = null;
let settingsDirty = false;
let settingsHydrated = false;
let statusFadeTimer = null;
let statusHoldUntil = 0;

const settingsInputs = [apiBase, adminToken, intervalHours, autoEpostEnabled, headlessEpostMode];

settingsInputs.forEach((input) => {
  input.addEventListener("input", () => {
    settingsDirty = true;
  });
  input.addEventListener("change", () => {
    settingsDirty = true;
    updateModeNotice();
  });
});

function syncSettingsInputs(state) {
  if (settingsHydrated || settingsDirty || settingsInputs.includes(document.activeElement)) return;
  apiBase.value = state.apiBase || "http://127.0.0.1:8000";
  adminToken.value = state.adminToken || "";
  intervalHours.value = state.intervalHours ?? Math.max(1, Number(state.intervalDays || 1)) * 24;
  autoEpostEnabled.checked = state.autoEpostEnabled === true;
  headlessEpostMode.checked = state.headlessEpostMode === true;
  updateModeNotice();
  settingsHydrated = true;
}

async function resolveTargetWindowId() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  targetWindowId = tabs[0]?.windowId || targetWindowId;
}

async function ensureTargetWindowId() {
  if (!targetWindowId) await resolveTargetWindowId();
  return targetWindowId;
}

function send(message) {
  return chrome.runtime.sendMessage({ ...message, targetWindowId });
}

function setStatus(text, kind = "info", options = {}) {
  if (statusFadeTimer) clearTimeout(statusFadeTimer);
  statusBox.className = "";
  statusBox.classList.add(`status-${kind}`);
  statusBox.textContent = text;
  statusHoldUntil = options.hold === "forever" ? Number.POSITIVE_INFINITY : Date.now() + Number(options.holdMs || 0);
  if (options.fadeAfterMs) {
    statusHoldUntil = Date.now() + Number(options.fadeAfterMs) + 400;
    statusFadeTimer = setTimeout(() => {
      statusBox.classList.add("status-fading");
    }, Number(options.fadeAfterMs));
  }
}

function setRunButtons(running) {
  startButton.hidden = Boolean(running);
  stopButton.hidden = !running;
}

function applyStatusFromRefresh(text) {
  if (Date.now() < statusHoldUntil) return;
  setStatus(text, "info");
}

function headlessRunning(progress = {}) {
  return progress.running === true || progress.status === "running" || progress.message?.toLowerCase?.().includes("running");
}

function resultKind(result) {
  return result?.ok === false ? "error" : "success";
}

function resultHoldOptions(result) {
  return result?.ok === false ? { hold: "forever" } : { fadeAfterMs: 2000 };
}

function setResultStatus(result, successText, failureText) {
  setStatus(result?.message || (result?.ok === false ? failureText : successText), resultKind(result), resultHoldOptions(result));
}

function setErrorStatus(error) {
  setStatus(error.message || String(error) || "Action failed.", "error", { hold: "forever" });
}

function updateModeNotice() {
  const mode = headlessEpostMode.checked
    ? "Headless mode starts ePost extraction in the local app's background Chrome profile and reuses the same portal parser as normal mode."
    : "Normal mode opens the ePost portal in this Chrome window and extracts with the extension.";
  const auto = autoEpostEnabled.checked
    ? ` Auto mode will check every ${Math.max(1, Math.min(720, Number(intervalHours.value || 24)))} hour(s).`
    : " Auto mode is off.";
  modeNotice.textContent = `${mode}${auto}`;
}

function progressText(progress = {}) {
  const processed = Number(progress.processed || 0);
  const total = Number(progress.total || 0);
  const current = progress.current_batch ? ` · batch ${progress.current_batch}` : "";
  return `${progress.message || "Headless ePost status loaded."} ${processed}/${total} batch(es) processed${current}`;
}

function visibleProgressText(run = {}) {
  const batches = run.batches || [];
  const processedBatches = Math.min(Number(run.batchIndex || 0), batches.length);
  const totalCodes = batches.flat().filter(Boolean).length;
  const processedCodes = batches.slice(0, processedBatches).flat().filter(Boolean).length;
  const currentCodes = batches[run.batchIndex]?.length || 0;
  const completed = run.completedCodes?.length || 0;
  const failed = run.failedCodes?.length || 0;
  const skipped = Number(run.skippedRecentCount || 0);
  return `Running: batch ${processedBatches + 1}/${batches.length} · codes ${processedCodes}/${totalCodes} · current ${currentCodes} · checked ${completed} · failed ${failed}${skipped ? ` · skipped recent ${skipped}` : ""}`;
}

function errorMessage(error) {
  return String(error?.message || error || "Unexpected extension error.");
}

function settingsPayload() {
  return {
    type: "SET_SETTINGS",
    apiBase: apiBase.value.trim(),
    adminToken: adminToken.value.trim(),
    intervalHours: Number(intervalHours.value || 24),
    autoEpostEnabled: autoEpostEnabled.checked,
    headlessEpostMode: headlessEpostMode.checked,
  };
}

async function runAction(label, action) {
  setStatus(label);
  try {
    await ensureTargetWindowId();
    await action();
  } catch (error) {
    setStatus(errorMessage(error));
  }
}

async function refresh() {
  if (!targetWindowId) await resolveTargetWindowId();
  const state = await send({ type: "GET_STATE" });
  syncSettingsInputs(state);
  const run = state.epostRun || {};
  if (headlessEpostMode.checked) {
    try {
      const headless = await send({ type: "GET_HEADLESS_EPOST_STATUS" });
      const running = headlessRunning(headless.progress || {});
      setRunButtons(running);
      applyStatusFromRefresh(progressText(headless.progress || {}));
    } catch (error) {
      setRunButtons(false);
      applyStatusFromRefresh(error.message || "Could not load headless ePost status.");
    }
  } else {
    setRunButtons(run.running);
    applyStatusFromRefresh(run.running ? visibleProgressText(run) : "Stopped");
  }
  logsBox.innerHTML = "";
  for (const line of state.logs || []) {
    const item = document.createElement("li");
    item.textContent = line;
    logsBox.append(item);
  }
}

document.querySelector("#save").addEventListener("click", () => {
  runAction("Saving settings...", async () => {
    const result = await send(settingsPayload());
    if (result.ok) settingsDirty = false;
    setResultStatus(result, "Saved.", "Save failed.");
  });
});

document.querySelector("#testConnection").addEventListener("click", () => {
  runAction("Checking connection...", async () => {
    try {
      await send(settingsPayload());
      settingsDirty = false;
      const result = await send({ type: "TEST_CONNECTION" });
      setResultStatus(result, "Connection successful.", "Connection failed.");
    } catch (error) {
      setErrorStatus(error);
    }
  });
});

document.querySelector("#openHeadlessSession")?.addEventListener("click", () => {
  runAction("Opening headless session...", async () => {
    await send(settingsPayload());
    settingsDirty = false;
    const result = await send({ type: "OPEN_HEADLESS_SESSION" });
    setResultStatus(result, "Opened headless session.", "Could not open headless session.");
  });
});

document.querySelector("#start").addEventListener("click", () => {
  runAction(headlessEpostMode.checked ? "Starting headless ePost..." : "Starting visible ePost...", async () => {
    await send(settingsPayload());
    settingsDirty = false;
    const result = await send({ type: headlessEpostMode.checked ? "START_HEADLESS_EPOST" : "START_EPOST" });
    if (result.ok !== false) setRunButtons(true);
    setResultStatus(result, "Started.", "Could not start.");
  });
});

document.querySelector("#stop").addEventListener("click", () => {
  runAction("Stopping ePost...", async () => {
    const result = await send({ type: headlessEpostMode.checked ? "STOP_HEADLESS_EPOST" : "STOP_EPOST" });
    if (result.ok !== false) setRunButtons(false);
    setResultStatus(result, "Stopped.", "Could not stop.");
  });
});

updateModeNotice();
setRunButtons(false);
refresh();
setInterval(refresh, 3000);
