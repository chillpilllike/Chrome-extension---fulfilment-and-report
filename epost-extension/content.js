const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const TRACKER_URL = "https://portal.epgshipping.com/ParcelTracker/HomePageTracker";

let extensionContextAlive = true;
const MAX_PAGE_RETRIES = 6;

async function send(message) {
  if (!extensionContextAlive) return null;
  try {
    return await chrome.runtime.sendMessage(message);
  } catch (error) {
    if (/Extension context invalidated/i.test(String(error?.message || error))) {
      extensionContextAlive = false;
      return null;
    }
    throw error;
  }
}

function clean(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function showPanel(title, message) {
  let panel = document.querySelector("#nutricity-epost-panel");
  if (!panel) {
    panel = document.createElement("div");
    panel.id = "nutricity-epost-panel";
    document.documentElement.append(panel);
  }
  panel.innerHTML = `<strong></strong><div></div>`;
  panel.querySelector("strong").textContent = title;
  panel.querySelector("div").textContent = message;
}

function retryPageRun(message, delayMs = 5000) {
  window.__nutricityEpostRetries = Number(window.__nutricityEpostRetries || 0) + 1;
  if (window.__nutricityEpostRetries > MAX_PAGE_RETRIES) {
    showPanel("Nutricity ePost", `${message} Retried ${MAX_PAGE_RETRIES} time(s); watchdog will move this batch if it remains stuck.`);
    return;
  }
  showPanel("Nutricity ePost", `${message} Retrying ${window.__nutricityEpostRetries}/${MAX_PAGE_RETRIES}.`);
  setTimeout(() => {
    if (window.__nutricityEpostRunning) return;
    window.__nutricityEpostRunning = true;
    run().finally(() => {
      window.__nutricityEpostRunning = false;
    });
  }, delayMs);
}

function setFieldValue(field, value) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
  if (setter) setter.call(field, value);
  else field.value = value;
  field.setAttribute("value", value);
  field.dispatchEvent(new Event("input", { bubbles: true }));
  field.dispatchEvent(new Event("change", { bubbles: true }));
  field.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true }));
}

function fieldCodes(field) {
  return String(field.value || "")
    .split(/\s+/)
    .map((code) => code.trim().toUpperCase())
    .filter(Boolean);
}

async function hardReplaceTrackingCodes(field, codes) {
  const value = codes.map((code) => String(code || "").trim().toUpperCase()).filter(Boolean).join("\n");
  field.autocomplete = "off";
  field.focus();
  for (let attempt = 0; attempt < 6; attempt += 1) {
    field.select();
    setFieldValue(field, "");
    await sleep(250);
    setFieldValue(field, value);
    await sleep(500);
    if (sameCodeSet(fieldCodes(field), codes)) return true;
  }
  try {
    field.focus();
    field.select();
    document.execCommand("delete");
    document.execCommand("insertText", false, value);
  } catch {
    setFieldValue(field, value);
  }
  await sleep(500);
  return sameCodeSet(fieldCodes(field), codes);
}

async function waitForReady() {
  const started = Date.now();
  while (document.readyState !== "complete" && Date.now() - started < 25000) await sleep(250);
  await sleep(800);
}

async function waitForResults(timeoutMs = 30000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (document.querySelector("#TableParcelTracker tbody tr")) return true;
    await sleep(500);
  }
  return false;
}

async function waitForMatchingResults(codes, timeoutMs = 30000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (document.querySelector("#TableParcelTracker tbody tr")) {
      await selectAllRows();
      const coverage = resultCoverage(codes);
      if (coverage.exact) return coverage;
      if (coverage.hasRequestedCode && coverage.hasLookupError) return coverage;
    }
    await sleep(500);
  }
  return null;
}

async function selectAllRows() {
  const started = Date.now();
  while (Date.now() - started < 10000) {
    const selects = [...document.querySelectorAll("#TableParcelTracker_wrapper select")];
    const allSelect = selects.find((select) => [...select.options].some((option) => option.value === "-1" || clean(option.textContent).toLowerCase() === "all"));
    if (allSelect) {
      allSelect.value = "-1";
      allSelect.dispatchEvent(new Event("change", { bubbles: true }));
      await sleep(2000);
      return true;
    }
    await sleep(300);
  }
  return false;
}

function parseDetailRows(childRow) {
  const events = [];
  const rows = [...(childRow?.querySelectorAll(".expandable-details-Container .row.row-light") || [])];
  for (const row of rows) {
    const cells = [...row.querySelectorAll(".col-sm-4")].map((cell) => clean(cell.textContent));
    if (cells.length >= 3) {
      events.push({ date: cells[0], status: cells[1], location: cells[2] });
    }
  }
  return events;
}

function parseResults() {
  const results = [];
  const rows = [...document.querySelectorAll("#TableParcelTracker tbody tr")];
  for (let i = 0; i < rows.length; i += 1) {
    const row = rows[i];
    if (row.classList.contains("child")) continue;
    const cells = [...row.querySelectorAll("td")].map((cell) => clean(cell.textContent));
    if (cells.length < 2) continue;
    const tracking = cells[0];
    if (!/^EPG/i.test(tracking)) continue;
    const child = rows[i + 1]?.classList.contains("child") ? rows[i + 1] : null;
    const events = parseDetailRows(child);
    results.push({
      tracking_code: tracking.toUpperCase(),
      status: cells[1] || "",
      date: cells[2] || "",
      location: cells[3] || "",
      destination: cells[4] || "",
      awb: cells[5] || "",
      tracking_url: `https://epgtrack.com/${tracking.toUpperCase()}`,
      events,
    });
  }
  return results;
}

function visibleResultCodes() {
  return parseResults().map((result) => result.tracking_code);
}

function resultCoverage(codes) {
  const requested = codes.map((code) => String(code || "").trim().toUpperCase()).filter(Boolean);
  const requestedSet = new Set(requested);
  const results = parseResults();
  const visible = results.map((result) => result.tracking_code).filter(Boolean);
  const visibleSet = new Set(visible);
  const matched = requested.filter((code) => visibleSet.has(code));
  const unexpected = visible.filter((code) => !requestedSet.has(code));
  const hasLookupError = results.some((result) => /error locating tracking number|tracking number not found|unable to locate/i.test(result.status || ""));
  return {
    exact: requested.length === visible.length && requested.every((code) => visibleSet.has(code)),
    hasRequestedCode: matched.length > 0,
    matched,
    missing: requested.filter((code) => !visibleSet.has(code)),
    unexpected,
    hasLookupError,
  };
}

function sameCodeSet(left, right) {
  const a = left.map((code) => String(code || "").trim().toUpperCase()).filter(Boolean).sort();
  const b = right.map((code) => String(code || "").trim().toUpperCase()).filter(Boolean).sort();
  return a.length === b.length && a.every((code, index) => code === b[index]);
}

async function captureResults(batchIndex) {
  showPanel("Nutricity ePost", "Selecting All rows before reading results.");
  await selectAllRows();
  await sleep(800);
  const results = parseResults();
  showPanel("Nutricity ePost", `Captured ${results.length} tracking result(s).`);
  const response = await send({ type: "EPOST_RESULTS", results, batchIndex });
  if (!response?.ok) {
    showPanel("Nutricity ePost", response?.message || "Could not advance to the next batch.");
  }
  return results.length;
}

async function run() {
  await waitForReady();
  if (!/portal\.epgshipping\.com$/i.test(location.hostname)) return;
  const response = await send({ type: "PORTAL_READY" });
  const codes = response?.codes || [];
  const batchIndex = response?.batchIndex;
  const submitted = Boolean(response?.submitted);
  if (!codes.length) return;
  if (submitted) {
    showPanel("Nutricity ePost", `Waiting for submitted batch ${Number(batchIndex) + 1} results.`);
    const coverage = await waitForMatchingResults(codes, 30000);
    if (coverage) {
      window.__nutricityEpostRetries = 0;
      if (!coverage.exact) {
        showPanel("Nutricity ePost", `ePost returned lookup error result(s); capturing ${coverage.matched.length} matched code(s) and moving on.`);
        await sleep(600);
      }
      await captureResults(batchIndex);
    } else {
      const currentCoverage = resultCoverage(codes);
      const detail = currentCoverage.unexpected.length
        ? ` Visible codes belong to another batch: ${currentCoverage.unexpected.slice(0, 3).join(", ")}.`
        : "";
      retryPageRun(`Submitted batch ${Number(batchIndex) + 1} results are not visible yet.${detail}`);
    }
    return;
  }
  if (document.querySelector("#TableParcelTracker tbody tr")) {
    const resultCodes = visibleResultCodes();
    if (sameCodeSet(resultCodes, codes)) {
      await captureResults(batchIndex);
      return;
    }
    showPanel("Nutricity ePost", "Previous batch is still visible. Replacing with the next batch.");
  }
  showPanel("Nutricity ePost", `Submitting ${codes.length} tracking code(s).`);
  const textarea = document.querySelector("#txtTrackingNumber, textarea[name='trackingNumbers']");
  if (!textarea) {
    showPanel("Nutricity ePost", "Could not find tracking textarea.");
    return;
  }
  const replaced = await hardReplaceTrackingCodes(textarea, codes);
  if (!replaced) {
    showPanel("Nutricity ePost", "Could not replace the old saved tracking codes.");
    return;
  }
  showPanel("Nutricity ePost", `Inserted batch ${Number(batchIndex) + 1}: ${codes.length} tracking code(s).`);
  const button = [...document.querySelectorAll("button, input[type='submit']")].find((item) => /track parcels/i.test(item.textContent || item.value || ""));
  if (!button) {
    showPanel("Nutricity ePost", "Could not find Track Parcels button.");
    return;
  }
  const submittedResponse = await send({ type: "BATCH_SUBMITTED", batchIndex });
  if (!submittedResponse?.ok) {
    showPanel("Nutricity ePost", submittedResponse?.message || "Could not mark ePost batch as submitted.");
    return;
  }
  button.click();
  if (await waitForResults()) {
    window.__nutricityEpostRetries = 0;
    await captureResults(batchIndex);
  } else {
    retryPageRun(`No ePost result table appeared after submitting batch ${Number(batchIndex) + 1}.`);
  }
}

if (!window.__nutricityEpostRunning) {
  window.__nutricityEpostRunning = true;
  run().finally(() => {
    window.__nutricityEpostRunning = false;
  });
}
