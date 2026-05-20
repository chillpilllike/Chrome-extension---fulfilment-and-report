/* Firefox build: prefer Firefox's Promise-based browser namespace. */
const chrome = globalThis.browser || globalThis.chrome;

const apiBase = document.querySelector("#apiBase");
const adminToken = document.querySelector("#adminToken");
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
  const tracking = state.tracking || {};
  setStatus(tracking.running ? `Running: ${tracking.index + 1 || 1}/${tracking.orders?.length || 0}` : "Stopped");
  logsBox.innerHTML = "";
  for (const line of state.logs || []) {
    const item = document.createElement("li");
    item.textContent = line;
    logsBox.append(item);
  }
}

document.querySelector("#save").addEventListener("click", async () => {
  const result = await send({ type: "SET_API_BASE", apiBase: apiBase.value.trim(), adminToken: adminToken.value.trim() });
  setStatus(result.ok ? "Saved." : result.message);
});

document.querySelector("#testConnection").addEventListener("click", async () => {
  await send({ type: "SET_API_BASE", apiBase: apiBase.value.trim(), adminToken: adminToken.value.trim() });
  const result = await send({ type: "TEST_CONNECTION" });
  setStatus(result.message || (result.ok ? "Connection ok." : "Connection failed."));
});

document.querySelector("#start").addEventListener("click", async () => {
  const result = await send({ type: "START_TRACKING" });
  setStatus(result.message || (result.ok ? "Started." : "Could not start."));
});

document.querySelector("#stop").addEventListener("click", async () => {
  const result = await send({ type: "STOP_TRACKING" });
  setStatus(result.message || "Stopped.");
});

refresh();
setInterval(refresh, 3000);
