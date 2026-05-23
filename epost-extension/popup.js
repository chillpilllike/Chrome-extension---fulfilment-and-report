const apiBase = document.querySelector("#apiBase");
const adminToken = document.querySelector("#adminToken");
const intervalDays = document.querySelector("#intervalDays");
const headlessEpostMode = document.querySelector("#headlessEpostMode");
const modeNotice = document.querySelector("#modeNotice");
const statusBox = document.querySelector("#status");
const logsBox = document.querySelector("#logs");

let targetWindowId = null;
let settingsDirty = false;
let settingsHydrated = false;

[apiBase, adminToken, intervalDays, headlessEpostMode].forEach((input) => {
  input.addEventListener("input", () => {
    settingsDirty = true;
  });
  input.addEventListener("change", () => {
    settingsDirty = true;
    updateModeNotice();
  });
});

function syncSettingsInputs(state) {
  if (settingsHydrated || settingsDirty || [apiBase, adminToken, intervalDays, headlessEpostMode].includes(document.activeElement)) return;
  apiBase.value = state.apiBase || "http://127.0.0.1:8000";
  adminToken.value = state.adminToken || "";
  intervalDays.value = state.intervalDays ?? 1;
  headlessEpostMode.checked = state.headlessEpostMode === true;
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
  modeNotice.textContent = headlessEpostMode.checked
    ? "Headless mode starts ePost extraction in the local app's background Chrome profile and reuses the same portal parser as normal mode."
    : "Normal mode opens the ePost portal in this Chrome window and extracts with the extension.";
}

function progressText(progress = {}) {
  const processed = Number(progress.processed || 0);
  const total = Number(progress.total || 0);
  const current = progress.current_batch ? ` · batch ${progress.current_batch}` : "";
  return `${progress.message || "Headless ePost status loaded."} ${processed}/${total} batch(es) processed${current}`;
}

async function refresh() {
  if (!targetWindowId) await resolveTargetWindowId();
  const state = await send({ type: "GET_STATE" });
  syncSettingsInputs(state);
  const run = state.epostRun || {};
  if (headlessEpostMode.checked) {
    try {
      const headless = await send({ type: "GET_HEADLESS_EPOST_STATUS" });
      setStatus(progressText(headless.progress || {}));
    } catch (error) {
      setStatus(error.message || "Could not load headless ePost status.");
    }
  } else {
    setStatus(run.running ? `Running: batch ${run.batchIndex + 1 || 1}/${run.batches?.length || 0}` : "Stopped");
  }
  logsBox.innerHTML = "";
  for (const line of state.logs || []) {
    const item = document.createElement("li");
    item.textContent = line;
    logsBox.append(item);
  }
}

document.querySelector("#save").addEventListener("click", async () => {
  const result = await send({ type: "SET_SETTINGS", apiBase: apiBase.value.trim(), adminToken: adminToken.value.trim(), intervalDays: Number(intervalDays.value || 1), headlessEpostMode: headlessEpostMode.checked });
  if (result.ok) settingsDirty = false;
  setStatus(result.ok ? "Saved." : result.message);
});

document.querySelector("#testConnection").addEventListener("click", async () => {
  await send({ type: "SET_SETTINGS", apiBase: apiBase.value.trim(), adminToken: adminToken.value.trim(), intervalDays: Number(intervalDays.value || 1), headlessEpostMode: headlessEpostMode.checked });
  settingsDirty = false;
  const result = await send({ type: "TEST_CONNECTION" });
  setStatus(result.message || (result.ok ? "Connection ok." : "Connection failed."));
});

document.querySelector("#start").addEventListener("click", async () => {
  await send({ type: "SET_SETTINGS", apiBase: apiBase.value.trim(), adminToken: adminToken.value.trim(), intervalDays: Number(intervalDays.value || 1), headlessEpostMode: headlessEpostMode.checked });
  settingsDirty = false;
  const result = await send({ type: headlessEpostMode.checked ? "START_HEADLESS_EPOST" : "START_EPOST" });
  setStatus(result.message || (result.ok ? "Started." : "Could not start."));
});

document.querySelector("#stop").addEventListener("click", async () => {
  const result = await send({ type: headlessEpostMode.checked ? "STOP_HEADLESS_EPOST" : "STOP_EPOST" });
  setStatus(result.message || "Stopped.");
});

updateModeNotice();
refresh();
setInterval(refresh, 3000);
