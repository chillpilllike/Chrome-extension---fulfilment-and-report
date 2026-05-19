const apiBase = document.querySelector("#apiBase");
const adminToken = document.querySelector("#adminToken");
const intervalDays = document.querySelector("#intervalDays");
const statusBox = document.querySelector("#status");
const logsBox = document.querySelector("#logs");

let targetWindowId = null;

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

async function refresh() {
  if (!targetWindowId) await resolveTargetWindowId();
  const state = await send({ type: "GET_STATE" });
  apiBase.value = state.apiBase || "http://127.0.0.1:8000";
  adminToken.value = state.adminToken || "";
  intervalDays.value = state.intervalDays ?? 1;
  const run = state.epostRun || {};
  setStatus(run.running ? `Running: batch ${run.batchIndex + 1 || 1}/${run.batches?.length || 0}` : "Stopped");
  logsBox.innerHTML = "";
  for (const line of state.logs || []) {
    const item = document.createElement("li");
    item.textContent = line;
    logsBox.append(item);
  }
}

document.querySelector("#save").addEventListener("click", async () => {
  const result = await send({ type: "SET_SETTINGS", apiBase: apiBase.value.trim(), adminToken: adminToken.value.trim(), intervalDays: Number(intervalDays.value || 1) });
  setStatus(result.ok ? "Saved." : result.message);
});

document.querySelector("#testConnection").addEventListener("click", async () => {
  await send({ type: "SET_SETTINGS", apiBase: apiBase.value.trim(), adminToken: adminToken.value.trim(), intervalDays: Number(intervalDays.value || 1) });
  const result = await send({ type: "TEST_CONNECTION" });
  setStatus(result.message || (result.ok ? "Connection ok." : "Connection failed."));
});

document.querySelector("#start").addEventListener("click", async () => {
  const result = await send({ type: "START_EPOST" });
  setStatus(result.message || (result.ok ? "Started." : "Could not start."));
});

document.querySelector("#stop").addEventListener("click", async () => {
  const result = await send({ type: "STOP_EPOST" });
  setStatus(result.message || "Stopped.");
});

refresh();
setInterval(refresh, 3000);
