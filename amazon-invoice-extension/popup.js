const apiBase = document.querySelector("#apiBase");
const adminToken = document.querySelector("#adminToken");
const statusBox = document.querySelector("#status");
const logsBox = document.querySelector("#logs");
let settingsDirty = false;

[apiBase, adminToken].forEach((input) => {
  input.addEventListener("input", () => {
    settingsDirty = true;
  });
});

function syncSettingsInputs(state) {
  if (settingsDirty || [apiBase, adminToken].includes(document.activeElement)) return;
  apiBase.value = state.apiBase || "http://127.0.0.1:8000";
  adminToken.value = state.adminToken || "";
}

function send(message) {
  return chrome.runtime.sendMessage(message);
}

async function refresh() {
  const state = await send({ type: "GET_STATE" });
  syncSettingsInputs(state);
  const run = state.invoiceRun || {};
  statusBox.textContent = run.running ? `Running: ${run.index + 1 || 1}/${run.orders?.length || 0}` : "Stopped";
  logsBox.innerHTML = "";
  for (const line of state.logs || []) {
    const item = document.createElement("li");
    item.textContent = line;
    logsBox.append(item);
  }
}

document.querySelector("#save").addEventListener("click", async () => {
  await send({ type: "SET_API_BASE", apiBase: apiBase.value.trim(), adminToken: adminToken.value.trim() });
  settingsDirty = false;
});

document.querySelector("#testConnection").addEventListener("click", async () => {
  await send({ type: "SET_API_BASE", apiBase: apiBase.value.trim(), adminToken: adminToken.value.trim() });
  settingsDirty = false;
  const result = await send({ type: "TEST_CONNECTION" });
  statusBox.textContent = result.message || (result.ok ? "Connection ok." : "Connection failed.");
});

document.querySelector("#start").addEventListener("click", async () => {
  const result = await send({ type: "START_INVOICES" });
  statusBox.textContent = result.message || (result.ok ? "Started." : "Could not start.");
});

document.querySelector("#stop").addEventListener("click", async () => {
  const result = await send({ type: "STOP_INVOICES" });
  statusBox.textContent = result.message || "Stopped.";
});

refresh();
setInterval(refresh, 3000);
