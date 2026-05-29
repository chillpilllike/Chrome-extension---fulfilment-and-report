const apiBase = document.querySelector("#apiBase");
const adminToken = document.querySelector("#adminToken");
const headlessTrackingMode = document.querySelector("#headlessTrackingMode");
const autoTrackingEnabled = document.querySelector("#autoTrackingEnabled");
const autoTrackingHours = document.querySelector("#autoTrackingHours");
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

const settingsInputs = [apiBase, adminToken, headlessTrackingMode, autoTrackingEnabled, autoTrackingHours];

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
  headlessTrackingMode.checked = state.headlessTrackingMode === true;
  autoTrackingEnabled.checked = state.autoTrackingEnabled === true;
  autoTrackingHours.value = state.autoTrackingHours ?? 3;
  updateModeNotice();
  settingsHydrated = true;
}

async function resolveTargetWindowId() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  targetWindowId = tabs[0]?.windowId || targetWindowId;
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
  const mode = headlessTrackingMode.checked
    ? "Headless mode starts tracking in the local app's background Chrome profile. It reuses the same Amazon parser as normal mode."
    : "Normal mode opens Amazon tabs in this Chrome window and tracks with the extension.";
  const auto = autoTrackingEnabled.checked
    ? ` Auto mode will check every ${Math.max(1, Math.min(168, Number(autoTrackingHours.value || 3)))} hour(s).`
    : " Auto mode is off.";
  modeNotice.textContent = `${mode}${auto}`;
}

function progressText(progress = {}) {
  const processed = Number(progress.processed || 0);
  const total = Number(progress.total || 0);
  const current = progress.current_order ? ` · ${progress.current_order}` : "";
  return `${progress.message || "Headless tracking status loaded."} ${processed}/${total} processed${current}`;
}

function settingsPayload() {
  return {
    type: "SET_API_BASE",
    apiBase: apiBase.value.trim(),
    adminToken: adminToken.value.trim(),
    headlessTrackingMode: headlessTrackingMode.checked,
    autoTrackingEnabled: autoTrackingEnabled.checked,
    autoTrackingHours: Number(autoTrackingHours.value || 3),
  };
}

async function refresh() {
  if (!targetWindowId) await resolveTargetWindowId();
  const state = await send({ type: "GET_STATE" });
  syncSettingsInputs(state);
  const tracking = state.tracking || {};
  if (headlessTrackingMode.checked) {
    try {
      const headless = await send({ type: "GET_HEADLESS_TRACKING_STATUS" });
      const running = headlessRunning(headless.progress || {});
      setRunButtons(running);
      applyStatusFromRefresh(progressText(headless.progress || {}));
    } catch (error) {
      setRunButtons(false);
      applyStatusFromRefresh(error.message || "Could not load headless tracking status.");
    }
  } else {
    setRunButtons(tracking.running);
    applyStatusFromRefresh(tracking.running ? `Running: ${tracking.index + 1 || 1}/${tracking.orders?.length || 0}` : "Stopped");
  }
  logsBox.innerHTML = "";
  for (const line of state.logs || []) {
    const item = document.createElement("li");
    item.textContent = line;
    logsBox.append(item);
  }
}

document.querySelector("#save").addEventListener("click", async () => {
  const result = await send(settingsPayload());
  if (result.ok) settingsDirty = false;
  setResultStatus(result, "Saved.", "Save failed.");
});

document.querySelector("#testConnection").addEventListener("click", async () => {
  try {
    await send(settingsPayload());
    settingsDirty = false;
    const result = await send({ type: "TEST_CONNECTION" });
    setResultStatus(result, "Connection successful.", "Connection failed.");
  } catch (error) {
    setErrorStatus(error);
  }
});

document.querySelector("#openHeadlessSignin").addEventListener("click", async () => {
  await send(settingsPayload());
  settingsDirty = false;
  const result = await send({ type: "OPEN_HEADLESS_SIGNIN" });
  setResultStatus(result, "Opened headless sign-in.", "Could not open headless sign-in.");
});

document.querySelector("#checkHeadlessSignin").addEventListener("click", async () => {
  await send(settingsPayload());
  settingsDirty = false;
  setStatus("Checking headless Amazon session...", "info");
  const result = await send({ type: "CHECK_HEADLESS_TRACKING_READINESS" });
  setResultStatus({ ...result, ok: result.ready !== false && result.ok !== false }, "Headless Amazon session is ready.", "Headless Amazon session is not ready.");
});

document.querySelector("#start").addEventListener("click", async () => {
  await send(settingsPayload());
  settingsDirty = false;
  const result = await send({ type: headlessTrackingMode.checked ? "START_HEADLESS_TRACKING" : "START_TRACKING" });
  if (result.ok !== false) setRunButtons(true);
  setResultStatus(result, "Started.", "Could not start.");
});

document.querySelector("#stop").addEventListener("click", async () => {
  const result = await send({ type: headlessTrackingMode.checked ? "STOP_HEADLESS_TRACKING" : "STOP_TRACKING" });
  if (result.ok !== false) setRunButtons(false);
  setResultStatus(result, "Stopped.", "Could not stop.");
});

updateModeNotice();
setRunButtons(false);
refresh();
setInterval(refresh, 3000);
