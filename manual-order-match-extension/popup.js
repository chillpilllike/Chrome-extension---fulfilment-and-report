function qs(id) {
  return document.getElementById(id);
}

const settingsInputs = ["apiBase", "adminToken", "maxPages"].map(qs);
let settingsDirty = false;
let settingsHydrated = false;

settingsInputs.forEach((input) => {
  input.addEventListener("input", () => {
    settingsDirty = true;
  });
});

async function send(message) {
  return chrome.runtime.sendMessage(message);
}

function syncSettingsInputs(state) {
  if (settingsHydrated || settingsDirty || settingsInputs.includes(document.activeElement)) return;
  qs("apiBase").value = state.apiBase || "http://127.0.0.1:8000";
  qs("adminToken").value = state.adminToken || "";
  qs("maxPages").value = state.maxPages || state.run?.maxPages || 10;
  settingsHydrated = true;
}

function render(state) {
  syncSettingsInputs(state);
  const run = state.run || {};
  qs("status").textContent = run.running
    ? `Running. Pages scanned: ${run.pagesScanned || 0}/${run.maxPages || 10}. Orders seen: ${(run.seenOrderIds || []).length}.`
    : `Stopped. Last pages scanned: ${run.pagesScanned || 0}. Orders seen: ${(run.seenOrderIds || []).length}.`;
  qs("logs").innerHTML = (state.logs || []).map((line) => `<li>${line}</li>`).join("");
}

async function refresh() {
  render(await send({ type: "GET_STATE" }));
}

async function saveSettings() {
  const response = await send({
    type: "SET_SETTINGS",
    apiBase: qs("apiBase").value.trim(),
    adminToken: qs("adminToken").value.trim(),
    maxPages: Number(qs("maxPages").value || 10),
  });
  if (response?.ok) settingsDirty = false;
  qs("status").textContent = response?.ok ? "Saved." : response?.message || "Save failed.";
  await refresh();
}

async function action(message) {
  const response = await send(message);
  qs("status").textContent = response?.message || (response?.ok ? "Done." : "Failed.");
  await refresh();
}

qs("save").onclick = saveSettings;
qs("testConnection").onclick = () => action({ type: "TEST_CONNECTION" });
qs("scanCurrent").onclick = async () => {
  await saveSettings();
  await action({ type: "SCAN_CURRENT_PAGE" });
};
qs("start").onclick = async () => {
  await saveSettings();
  await action({ type: "START_SCAN", maxPages: Number(qs("maxPages").value || 10) });
};
qs("stop").onclick = () => action({ type: "STOP_SCAN" });

refresh();
setInterval(refresh, 1500);
