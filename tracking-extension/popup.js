const apiBase = document.querySelector("#apiBase");
const adminToken = document.querySelector("#adminToken");
const headlessTrackingMode = document.querySelector("#headlessTrackingMode");
const modeNotice = document.querySelector("#modeNotice");
const statusBox = document.querySelector("#status");
const logsBox = document.querySelector("#logs");

let targetWindowId = null;
let settingsDirty = false;
let settingsHydrated = false;

[apiBase, adminToken, headlessTrackingMode].forEach((input) => {
  input.addEventListener("input", () => {
    settingsDirty = true;
  });
  input.addEventListener("change", () => {
    settingsDirty = true;
    updateModeNotice();
  });
});

function syncSettingsInputs(state) {
  if (settingsHydrated || settingsDirty || [apiBase, adminToken, headlessTrackingMode].includes(document.activeElement)) return;
  apiBase.value = state.apiBase || "http://127.0.0.1:8000";
  adminToken.value = state.adminToken || "";
  headlessTrackingMode.checked = state.headlessTrackingMode === true;
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

function setStatus(text) {
  statusBox.textContent = text;
}

function updateModeNotice() {
  modeNotice.textContent = headlessTrackingMode.checked
    ? "Headless mode starts tracking in the local app's background Chrome profile. It reuses the same Amazon parser as normal mode."
    : "Normal mode opens Amazon tabs in this Chrome window and tracks with the extension.";
}

function progressText(progress = {}) {
  const processed = Number(progress.processed || 0);
  const total = Number(progress.total || 0);
  const current = progress.current_order ? ` · ${progress.current_order}` : "";
  return `${progress.message || "Headless tracking status loaded."} ${processed}/${total} processed${current}`;
}

async function refresh() {
  if (!targetWindowId) await resolveTargetWindowId();
  const state = await send({ type: "GET_STATE" });
  syncSettingsInputs(state);
  const tracking = state.tracking || {};
  if (headlessTrackingMode.checked) {
    try {
      const headless = await send({ type: "GET_HEADLESS_TRACKING_STATUS" });
      setStatus(progressText(headless.progress || {}));
    } catch (error) {
      setStatus(error.message || "Could not load headless tracking status.");
    }
  } else {
    setStatus(tracking.running ? `Running: ${tracking.index + 1 || 1}/${tracking.orders?.length || 0}` : "Stopped");
  }
  logsBox.innerHTML = "";
  for (const line of state.logs || []) {
    const item = document.createElement("li");
    item.textContent = line;
    logsBox.append(item);
  }
}

document.querySelector("#save").addEventListener("click", async () => {
  const result = await send({ type: "SET_API_BASE", apiBase: apiBase.value.trim(), adminToken: adminToken.value.trim(), headlessTrackingMode: headlessTrackingMode.checked });
  if (result.ok) settingsDirty = false;
  setStatus(result.ok ? "Saved." : result.message);
});

document.querySelector("#testConnection").addEventListener("click", async () => {
  await send({ type: "SET_API_BASE", apiBase: apiBase.value.trim(), adminToken: adminToken.value.trim(), headlessTrackingMode: headlessTrackingMode.checked });
  settingsDirty = false;
  const result = await send({ type: "TEST_CONNECTION" });
  setStatus(result.message || (result.ok ? "Connection ok." : "Connection failed."));
});

document.querySelector("#openHeadlessSignin").addEventListener("click", async () => {
  await send({ type: "SET_API_BASE", apiBase: apiBase.value.trim(), adminToken: adminToken.value.trim(), headlessTrackingMode: headlessTrackingMode.checked });
  settingsDirty = false;
  const result = await send({ type: "OPEN_HEADLESS_SIGNIN" });
  setStatus(result.message || (result.ok ? "Opened headless sign-in." : "Could not open headless sign-in."));
});

document.querySelector("#checkHeadlessSignin").addEventListener("click", async () => {
  await send({ type: "SET_API_BASE", apiBase: apiBase.value.trim(), adminToken: adminToken.value.trim(), headlessTrackingMode: headlessTrackingMode.checked });
  settingsDirty = false;
  setStatus("Checking headless Amazon session...");
  const result = await send({ type: "CHECK_HEADLESS_TRACKING_READINESS" });
  setStatus(result.message || (result.ready ? "Headless Amazon session is ready." : "Headless Amazon session is not ready."));
});

document.querySelector("#start").addEventListener("click", async () => {
  await send({ type: "SET_API_BASE", apiBase: apiBase.value.trim(), adminToken: adminToken.value.trim(), headlessTrackingMode: headlessTrackingMode.checked });
  settingsDirty = false;
  const result = await send({ type: headlessTrackingMode.checked ? "START_HEADLESS_TRACKING" : "START_TRACKING" });
  setStatus(result.message || (result.ok ? "Started." : "Could not start."));
});

document.querySelector("#stop").addEventListener("click", async () => {
  const result = await send({ type: headlessTrackingMode.checked ? "STOP_HEADLESS_TRACKING" : "STOP_TRACKING" });
  setStatus(result.message || "Stopped.");
});

updateModeNotice();
refresh();
setInterval(refresh, 3000);
