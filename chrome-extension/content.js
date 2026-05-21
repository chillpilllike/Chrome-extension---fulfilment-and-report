const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const ACTION_DELAY = 1800;
const PAGE_READY_TIMEOUT = 12000;
const DEFAULT_NEW_DELIVERY_ADDRESS = {
  countryCode: "US",
  phoneNumber: "9176818556",
  addressLine1: "6614 AVENUE U STE 7",
  addressLine2: "",
  city: "BROOKLYN",
  stateOrRegion: "NY",
  postalCode: "11234-6021",
};

let extensionContextAlive = true;
let panelOrderStatusVersion = 0;

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

async function getActiveJob() {
  const data = await send({ type: "GET_ACTIVE_JOB" });
  return data?.activeJob;
}

async function setActiveJob(activeJob) {
  await send({ type: "SET_ACTIVE_JOB", activeJob });
}

async function getExtensionState() {
  const data = await send({ type: "GET_STATE" });
  return data || {};
}

async function isPaused() {
  const activeJob = await getActiveJob();
  return Boolean(activeJob?.paused);
}

async function waitIfPaused() {
  while (await isPaused()) {
    const activeJob = await getActiveJob();
    showPanel(
      "Nutricity fulfilment paused",
      "Fulfilment is paused. Click Resume to retry this step, or continue if you completed it manually.",
      "I did it manually, continue",
      () => continueAfterManualStep(activeJob),
    );
    await sleep(1000);
  }
}

async function waitForPageReady(label = "page") {
  showPanel("Nutricity fulfilment", `Waiting for ${label} controls.`, null, null);
  const started = Date.now();
  while (document.readyState === "loading" && Date.now() - started < PAGE_READY_TIMEOUT) {
    await waitIfPaused();
    await sleep(250);
  }
  await waitForStableDom(350, 2500);
  await sleep(200);
}

async function waitForStableDom(quietMs = 1000, timeoutMs = 8000) {
  let lastMutation = Date.now();
  const observer = new MutationObserver(() => {
    lastMutation = Date.now();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true });
  const started = Date.now();
  try {
    while (Date.now() - started < timeoutMs) {
      await waitIfPaused();
      if (Date.now() - lastMutation >= quietMs) return true;
      await sleep(200);
    }
    return false;
  } finally {
    observer.disconnect();
  }
}

async function waitForElement(selectors, timeoutMs = 18000) {
  const list = Array.isArray(selectors) ? selectors : [selectors];
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    await waitIfPaused();
    for (const selector of list) {
      const element = [...document.querySelectorAll(selector)].find(visible);
      if (element) return element;
    }
    await sleep(300);
  }
  return null;
}

function activeJobOrderLabel(activeJob) {
  const names = Array.isArray(activeJob?.job?.order_names)
    ? activeJob.job.order_names.map((name) => String(name || "").trim()).filter(Boolean)
    : [];
  if (names.length) return names.join(", ");
  const recipient = String(activeJob?.job?.recipient_name || "").replace(/\s+/g, " ").trim();
  if (recipient) return recipient;
  return String(activeJob?.job?.group_key || "").replace(/\s+/g, " ").trim();
}

function activeJobStepLabel(activeJob) {
  const stage = String(activeJob?.stage || "").replace(/_/g, " ").trim();
  if (!stage) return "Preparing";
  return stage.replace(/\b\w/g, (letter) => letter.toUpperCase());
}

async function updatePanelOrderStatus(panel = document.querySelector("#nutricity-panel")) {
  if (!panel) return;
  const version = ++panelOrderStatusVersion;
  const orderNode = panel.querySelector(".nutricity-panel-order");
  const orderText = panel.querySelector(".nutricity-panel-order-text");
  const statusText = panel.querySelector(".nutricity-panel-order-status");
  const stepText = panel.querySelector(".nutricity-panel-step-text");
  if (!orderNode || !orderText || !statusText || !stepText) return;
  const activeJob = await getActiveJob();
  if (version !== panelOrderStatusVersion) return;
  const label = activeJobOrderLabel(activeJob);
  if (!activeJob?.job || !label) {
    orderNode.hidden = true;
    orderText.textContent = "";
    stepText.textContent = "";
    statusText.textContent = "";
    return;
  }
  orderNode.hidden = false;
  orderText.textContent = label;
  stepText.textContent = activeJobStepLabel(activeJob);
  statusText.textContent = activeJob.paused ? "Paused" : "In progress";
}

function showPanel(title, message, actionText, action) {
  let panel = document.querySelector("#nutricity-panel");
  if (!panel) {
    panel = document.createElement("div");
    panel.id = "nutricity-panel";
    panel.innerHTML = `<div class="nutricity-panel-order" hidden><span class="nutricity-panel-order-label">Order being processed</span><span class="nutricity-panel-order-status"></span><div class="nutricity-panel-order-text"></div><div class="nutricity-panel-step">Step: <span class="nutricity-panel-step-text"></span></div></div><div class="nutricity-panel-header"><strong></strong><button class="nutricity-pause-toggle" type="button">Pause</button></div><div class="nutricity-panel-message"></div><ol class="nutricity-panel-activity"></ol>`;
    panel.querySelector(".nutricity-pause-toggle").addEventListener("click", togglePanelPause);
    document.documentElement.append(panel);
  }
  panel.querySelector("strong").textContent = title;
  panel.querySelector(".nutricity-panel-message").textContent = message;
  rememberPanelActivity(panel, title, message);
  panel.querySelector(".nutricity-panel-action")?.remove();
  updatePanelPauseButton(panel);
  updatePanelOrderStatus(panel);
  if (actionText && action) {
    const button = document.createElement("button");
    button.className = "nutricity-panel-action";
    button.textContent = actionText;
    button.addEventListener("click", action);
    panel.append(button);
  }
}

function rememberPanelActivity(panel, title, message) {
  const activity = panel.querySelector(".nutricity-panel-activity");
  if (!activity || !message) return;
  const text = `${title}: ${message}`.replace(/\s+/g, " ").trim();
  if (activity.firstElementChild?.dataset?.text === text) return;
  const item = document.createElement("li");
  item.dataset.text = text;
  item.textContent = text;
  activity.prepend(item);
  while (activity.children.length > 5) {
    activity.lastElementChild.remove();
  }
}

async function togglePanelPause(event) {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const result = await send({ type: "TOGGLE_PAUSE" });
    const paused = Boolean(result?.paused);
    button.textContent = paused ? "Resume" : "Pause";
    button.classList.toggle("is-paused", paused);
    if (paused) {
      const activeJob = await getActiveJob();
      showPanel(
        "Nutricity fulfilment paused",
        "Fulfilment is paused. Click Resume to retry this step, or continue if you completed it manually.",
        "I did it manually, continue",
        () => continueAfterManualStep(activeJob),
      );
    } else {
      showPanel("Nutricity fulfilment", `Resuming ${result?.stage || "last step"}.`, null, null);
      setTimeout(runSafely, 250);
    }
  } finally {
    button.disabled = false;
  }
}

async function updatePanelPauseButton(panel = document.querySelector("#nutricity-panel")) {
  if (!panel) return;
  const button = panel.querySelector(".nutricity-pause-toggle");
  if (!button) return;
  const activeJob = await getActiveJob();
  if (!activeJob?.job) {
    button.hidden = true;
    return;
  }
  button.hidden = false;
  button.textContent = activeJob.paused ? "Resume" : "Pause";
  button.classList.toggle("is-paused", Boolean(activeJob.paused));
}

function visible(element) {
  if (!element) return false;
  const style = getComputedStyle(element);
  const rect = element.getBoundingClientRect();
  return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
}

function clickFirst(selectors) {
  for (const selector of selectors) {
    const element = [...document.querySelectorAll(selector)].find(visible);
    if (element) {
      element.scrollIntoView({ block: "center", behavior: "smooth" });
      element.click();
      return true;
    }
  }
  return false;
}

async function clickElement(element, label = "element") {
  if (!element) return false;
  await waitIfPaused();
  element.scrollIntoView({ block: "center", behavior: "smooth" });
  await sleep(250);
  await waitIfPaused();
  element.click();
  await sleep(ACTION_DELAY);
  return true;
}

function findButtonByText(texts) {
  const wanted = texts.map((text) => text.toLowerCase());
  const candidates = [...document.querySelectorAll("button, input[type='submit'], input[type='button'], a, span.a-button")];
  return candidates.find((element) => {
    const labelledBy = element.getAttribute?.("aria-labelledby");
    const labelText = labelledBy ? document.getElementById(labelledBy)?.textContent : "";
    const text = (element.value || element.title || element.innerText || element.textContent || labelText || "").replace(/\s+/g, " ").trim().toLowerCase();
    return visible(element) && wanted.some((needle) => text.includes(needle));
  });
}

function findVisibleTextTarget(texts, selectors = "a, button, input[type='submit'], input[type='button'], label, span.a-button, span.a-button-text, [role='button'], [role='option']") {
  const wanted = texts.map((text) => text.toLowerCase());
  const candidates = [...document.querySelectorAll(selectors)];
  return candidates.find((element) => {
    const text = (element.value || element.innerText || element.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
    return visible(element) && wanted.some((needle) => text.includes(needle));
  });
}

function parsePriceFrom(element) {
  if (!element) return null;
  const offscreen = element.querySelector(".a-offscreen")?.textContent || "";
  const offscreenPrice = priceFromText(offscreen);
  if (offscreenPrice) return offscreenPrice;
  const whole = element.querySelector(".a-price-whole")?.textContent || "";
  const fraction = element.querySelector(".a-price-fraction")?.textContent || "00";
  const value = Number(`${whole.replace(/[^0-9]/g, "")}.${fraction.replace(/[^0-9]/g, "").slice(0, 2)}`);
  if (Number.isFinite(value) && value > 0) return value;
  return priceFromText(element.textContent || "");
}

function priceCandidatesIn(root, selectors = [".a-price"]) {
  if (!root) return null;
  const prices = selectors
    .flatMap((selector) => [...root.querySelectorAll(selector)])
    .filter((element, index, all) => all.indexOf(element) === index && visible(element) && !element.closest(".aok-hidden"))
    .map(parsePriceFrom)
    .filter((value) => Number(value) > 0);
  return prices;
}

function priceCandidateElementsIn(root, selectors = [".a-price"]) {
  if (!root) return [];
  return selectors
    .flatMap((selector) => [...root.querySelectorAll(selector)])
    .filter((element, index, all) => all.indexOf(element) === index && visible(element) && !element.closest(".aok-hidden"));
}

function firstPriceIn(root) {
  if (!root) return null;
  const prices = priceCandidatesIn(root);
  return prices[0] || priceFromText(root.innerText || root.textContent || "");
}

function lowestPriceIn(root, selectors = [".a-price"]) {
  const prices = priceCandidatesIn(root, selectors);
  return prices.length ? Math.min(...prices) : null;
}

function moneyText(value) {
  const number = Number(value || 0);
  return number ? `$${number.toFixed(2)}` : "unknown price";
}

function isInSubscribeAndSave(element) {
  return Boolean(element?.closest?.(subscribeAndSaveRootSelector()));
}

function subscribeAndSaveRootSelector() {
  return [
    "#snsAccordionRowMiddle",
    "#snsAccordionRow",
    "#snsAccordionRowContent",
    "#reinvent_price_desktop_snsAccordionRowMiddle",
    "#snsQuantity_feature_div",
    "[data-csa-c-slot-id*='sns']",
    "[id*='rcx-subscribe']",
    "[id*='rcxOrdFreqSns']",
    "[id*='snsAccordion']",
    "[id*='snsBuyingOption']",
    "[data-a-accordion-row-name*='sns']",
  ].join(", ");
}

function subscribeAndSavePriceFromText(text) {
  const match = String(text || "")
    .replace(/,/g, "")
    .match(/subscribe\s*&\s*save\s*\$+\s*([0-9]+(?:\.[0-9]{1,2})?)/i);
  if (!match) return null;
  const value = Number(match[1]);
  return Number.isFinite(value) ? value : null;
}

function oneTimePurchaseRootSelector() {
  return [
    "#qualifiedBuybox",
    "#desktop_qualifiedBuyBox",
    "#buybox",
    "#newAccordionRow",
    "#reinvent_price_desktop_newAccordionRow",
    "[data-csa-c-buying-option-type='NEW']",
    "[data-csa-c-buying-option-type='B2B']",
    "[data-a-accordion-row-name*='new']",
  ].join(", ");
}

function oneTimePurchaseRoots() {
  const roots = [...document.querySelectorAll(oneTimePurchaseRootSelector())].filter((root) => visible(root) && !isInSubscribeAndSave(root));
  for (const node of document.querySelectorAll("[id*='new_buyingOption'], [id*='DesktopFfqp_'][id*='new_buyingOption']")) {
    const root = node.closest("[data-csa-c-buying-option-type], [data-a-accordion-row-name], .a-accordion-row, .a-box, #qualifiedBuybox, #desktop_qualifiedBuyBox, #buybox") || node.closest(".a-section") || node;
    if (root && visible(root) && !isInSubscribeAndSave(root) && !roots.includes(root)) roots.push(root);
  }
  return roots;
}

function productPriceSnapshot() {
  const regularPriceSelectors = [
    ".a-price[data-a-color='price'][data-a-size='b']",
    ".a-price[data-a-color='price'].header-price",
    ".a-price.a-color-price[data-a-size='b']",
    "#corePrice_feature_div .a-price[data-a-size='b']",
    "#apex_desktop .a-price[data-a-size='b']",
    "#reinvent_price_desktop_newAccordionRow .a-price[data-a-size='b']",
    "#newAccordionRow .a-price[data-a-size='b']",
    "#buybox .a-price[data-a-size='b']",
    "#tp_price_block_total_price_ww .a-price[data-a-size='b']",
  ];
  const regularRoots = oneTimePurchaseRoots();
  const regularPool = regularRoots.length ? regularRoots : [document];
  const regularPrices = regularPool
    .flatMap((root) => priceCandidateElementsIn(root, regularPriceSelectors))
    .filter((element, index, all) => all.indexOf(element) === index && visible(element) && !isInSubscribeAndSave(element) && !element.closest(".aok-hidden"))
    .map(parsePriceFrom)
    .filter((value) => Number(value) > 0);
  const regular = regularPrices.length ? Math.min(...regularPrices) : null;
  const snsRoots = [...document.querySelectorAll(subscribeAndSaveRootSelector())].filter(visible);
  const snsPriceSelectors = [
    "#sns-tiered-price .a-price[data-a-size='b']",
    "#subscriptionPrice .a-price[data-a-color='secondary'][data-a-size='b']",
    ".a-price[data-a-color='secondary'][data-a-size='b']",
    ".a-price[data-a-color='secondary'].header-price",
    ".a-price.a-color-price[data-a-size='b']",
  ];
  const snsPrices = snsRoots
    .flatMap((root) => priceCandidatesIn(root, snsPriceSelectors))
    .filter((value) => Number(value) > 0);
  const sns = snsPrices.length ? Math.min(...snsPrices) : subscribeAndSavePriceFromText(document.body.innerText);
  const best = sns && regular ? Math.min(sns, regular) : sns || regular || 0;
  return { regular, sns, best };
}

function snsQuantityControlVisible() {
  return Boolean([...document.querySelectorAll(subscribeAndSaveRootSelector())].find((root) => (
    visible(root) && root.querySelector("select[id*='sns'][id*='predefinedQuantitiesDropdown'], input#rcxsubsQuan, input[id*='sns'][id$='freeQuantityTextInput']")
  )));
}

function productTitleText() {
  return (document.querySelector("#productTitle")?.textContent || document.querySelector("#title")?.textContent || "").replace(/\s+/g, " ").trim();
}

function dosageFromProductTitle() {
  const match = productTitleText().match(/\b(\d+(?:\.\d+)?)\s*mg\b/i);
  return match ? `${match[1]}mg` : "";
}

function rememberProductDosage(activeJob, item = null) {
  const dosage = dosageFromProductTitle();
  if (!dosage) return activeJob;
  const dosages = Array.isArray(activeJob.productDosages) ? activeJob.productDosages : [];
  if (!dosages.map((item) => String(item).toLowerCase()).includes(dosage.toLowerCase())) {
    activeJob.productDosages = [...dosages, dosage];
  }
  const dosageByOrder = activeJob.dosageByOrder && typeof activeJob.dosageByOrder === "object" ? activeJob.dosageByOrder : {};
  const orderNames = item?.order_names?.length ? item.order_names : activeJob.job?.order_names || [];
  for (const orderName of orderNames) {
    const key = String(orderName || "").trim();
    if (!key) continue;
    const orderDosages = Array.isArray(dosageByOrder[key]) ? dosageByOrder[key] : [];
    if (!orderDosages.map((value) => String(value).toLowerCase()).includes(dosage.toLowerCase())) {
      dosageByOrder[key] = [...orderDosages, dosage];
    }
  }
  activeJob.dosageByOrder = dosageByOrder;
  return activeJob;
}

function recipientName(activeJob) {
  const orderNames = Array.isArray(activeJob?.job?.order_names) ? activeJob.job.order_names : [];
  const dosageByOrder = activeJob?.dosageByOrder && typeof activeJob.dosageByOrder === "object" ? activeJob.dosageByOrder : {};
  if (orderNames.length) {
    const parts = ["Nutricity"];
    const assigned = new Set();
    for (const orderName of orderNames) {
      const name = String(orderName || "").trim();
      if (!name) continue;
      parts.push(name);
      const dosages = Array.isArray(dosageByOrder[name]) ? dosageByOrder[name] : [];
      for (const dosage of dosages) {
        const normalized = String(dosage || "").replace(/\s+/g, "").trim();
        if (normalized) {
          parts.push(normalized);
          assigned.add(normalized.toLowerCase());
        }
      }
    }
    const globalDosages = (Array.isArray(activeJob?.productDosages) ? activeJob.productDosages : [])
      .map((item) => String(item || "").replace(/\s+/g, "").trim())
      .filter(Boolean);
    for (const dosage of globalDosages) {
      if (!assigned.has(dosage.toLowerCase())) parts.push(dosage);
    }
    return parts.join(" ").replace(/\s+/g, " ").trim();
  }
  const base = String(activeJob?.job?.recipient_name || "").replace(/\s+/g, " ").trim();
  const dosages = (Array.isArray(activeJob?.productDosages) ? activeJob.productDosages : [])
    .map((item) => String(item || "").replace(/\s+/g, "").trim())
    .filter((dosage) => dosage && !base.toLowerCase().includes(dosage.toLowerCase()));
  return [base, ...dosages].join(" ").trim();
}

function priceFromText(text) {
  const match = String(text || "").replace(/,/g, "").match(/\$\s*([0-9]+(?:\.[0-9]{1,2})?)/);
  if (!match) return null;
  const value = Number(match[1]);
  return Number.isFinite(value) ? value : null;
}

function parseCountPack(text) {
  const normalized = String(text || "").replace(/\s+/g, " ").trim();
  if (!normalized) return null;
  const countMatch = normalized.match(/\b(\d+(?:\.\d+)?)\s*(?:mini\s*)?counts?\b/i);
  if (!countMatch) return null;
  const packMatch = normalized.match(/\bpack\s*of\s*(\d+(?:\.\d+)?)\b/i);
  const count = Number(countMatch[1]);
  const pack = packMatch ? Number(packMatch[1]) : 1;
  const units = count * pack;
  if (!Number.isFinite(units) || units <= 0) return null;
  return { count, pack, units, label: normalized };
}

function itemCountPack(item) {
  const names = [
    item.product_name,
    ...(Array.isArray(item.product_names) ? item.product_names : []),
  ].filter(Boolean);
  for (const name of names) {
    const parsed = parseCountPack(name);
    if (parsed) return parsed;
  }
  return null;
}

function selectedVariantFromPage() {
  const text =
    document.querySelector("[id^='inline-twister-expanded-dimension-text']")?.textContent ||
    document.querySelector("[aria-label^='Selected Size is'], [aria-label^='Selected Style is']")?.getAttribute("aria-label") ||
    "";
  return parseCountPack(text);
}

function swatchLabel(swatch) {
  const label =
    swatch.querySelector(".swatch-title-text")?.textContent ||
    swatch.querySelector("img[alt]")?.getAttribute("alt") ||
    swatch.querySelector(".a-button-text")?.textContent ||
    "";
  return label.replace(/\s+/g, " ").trim();
}

function swatchPrice(swatch) {
  return (
    parsePriceFrom(swatch.querySelector(".apex-pricetopay-value")) ||
    parsePriceFrom(swatch.querySelector(".a-price")) ||
    priceFromText(swatch.innerText || swatch.textContent)
  );
}

function variantSwatches() {
  const rows = [
    ...document.querySelectorAll("#twister-plus-inline-twister-card li[data-asin], #twister-plus-inline-twister li[data-asin], li.inline-twister-swatch[data-asin]"),
  ];
  const seen = new Set();
  return rows
    .filter((swatch) => {
      const asin = (swatch.getAttribute("data-asin") || "").toUpperCase();
      if (!asin || seen.has(asin)) return false;
      seen.add(asin);
      return swatch.getAttribute("data-initiallyunavailable") !== "true";
    })
    .map((swatch) => {
      const label = swatchLabel(swatch);
      const parsed = parseCountPack(label);
      const price = swatchPrice(swatch);
      const asin = (swatch.getAttribute("data-asin") || "").toUpperCase();
      const selected =
        swatch.getAttribute("data-initiallyselected") === "true" ||
        Boolean(swatch.querySelector(".a-button-selected, input[aria-checked='true']"));
      const target = swatch.querySelector("input.a-button-input, button, .a-button") || swatch;
      return parsed && price ? { ...parsed, asin, price, selected, target } : null;
    })
    .filter(Boolean);
}

function selectedVariantItem(activeJob, item) {
  const selection = activeJob.variantSelections?.[item.asin];
  if (!selection) return item;
  return {
    ...item,
    asin: selection.asin || item.asin,
    quantity: selection.quantity || item.quantity,
    original_asin: item.asin,
    selected_variant_label: selection.label || "",
    selected_variant_units: selection.units || null,
    requested_total_units: selection.requested_total_units || null,
  };
}

function variantSelectionNote(item, purchaseItem) {
  if (!purchaseItem.selected_variant_label || !purchaseItem.original_asin || purchaseItem.original_asin === purchaseItem.asin) return "";
  const originalLabel = itemCountPack(item)?.label || item.product_name || item.asin;
  const orderedQuantity = Number(item.quantity || 1);
  const purchaseQuantity = Number(purchaseItem.quantity || 1);
  const totalUnits = purchaseItem.requested_total_units ? ` (${purchaseItem.requested_total_units} total count)` : "";
  return `Cheaper Amazon variant found and used: ordered ${orderedQuantity} x ${originalLabel}${totalUnits}; purchased ${purchaseQuantity} x ${purchaseItem.selected_variant_label} (${purchaseItem.asin}) instead of ${item.asin}.`;
}

function chooseBestCountVariant(item, currentUnitPrice) {
  const ordered = itemCountPack(item) || selectedVariantFromPage();
  if (!ordered || !currentUnitPrice) return null;
  const requestedQuantity = Math.max(1, Math.round(Number(item.quantity || 1)));
  const requestedTotalUnits = ordered.units * requestedQuantity;
  const variants = variantSwatches();
  if (!variants.length) return null;

  const currentAsin = currentAsinFromUrl() || item.asin;
  const currentPageVariant = selectedVariantFromPage();
  if (currentPageVariant && currentUnitPrice) {
    variants.push({
      ...currentPageVariant,
      asin: currentAsin,
      price: currentUnitPrice,
      selected: true,
      target: null,
    });
  }

  const candidates = variants
    .map((variant) => {
      const purchaseQuantity = requestedTotalUnits / variant.units;
      if (!Number.isInteger(purchaseQuantity) || purchaseQuantity < 1) return null;
      return {
        ...variant,
        quantity: purchaseQuantity,
        total: purchaseQuantity * variant.price,
        requested_total_units: requestedTotalUnits,
      };
    })
    .filter(Boolean)
    .sort((a, b) => a.total - b.total || b.units - a.units);
  if (!candidates.length) return null;

  const currentTotal = Number(currentUnitPrice || 0) * requestedQuantity;
  const best = candidates[0];
  if (currentTotal && best.total >= currentTotal - 0.01 && best.asin === currentAsin && best.quantity === requestedQuantity) {
    return null;
  }
  if (currentTotal && best.total >= currentTotal - 0.01) return null;
  return best;
}

async function selectCheapestCountVariant(activeJob, item, currentUnitPrice) {
  const best = chooseBestCountVariant(item, currentUnitPrice);
  if (!best) return false;
  activeJob.variantSelections = activeJob.variantSelections || {};
  activeJob.variantSelections[item.asin] = {
    asin: best.asin,
    quantity: best.quantity,
    label: best.label,
    units: best.units,
    price: best.price,
    requested_total_units: best.requested_total_units,
  };
  await setActiveJob(activeJob);
  if (best.asin !== currentAsinFromUrl()) {
    showPanel("Nutricity fulfilment", `Switching to ${best.label} to buy ${best.requested_total_units} total count for less.`, null, null);
    if (best.target && visible(best.target)) {
      best.target.scrollIntoView({ block: "center", behavior: "smooth" });
      await sleep(500);
      best.target.click();
      await sleep(2500);
    }
    if (currentAsinFromUrl() !== best.asin) {
      location.href = `https://www.amazon.com/dp/${best.asin}`;
    }
    return true;
  }
  return false;
}

async function recordAmazonPrice(activeJob, item, unitPrice, source = "product", purchaseItem = item) {
  if (!unitPrice) return activeJob;
  activeJob.pricing = activeJob.pricing || {};
  const quantity = Number(purchaseItem.quantity || item.quantity || 1);
  const storeUnit = Number(item.store_unit_price || 0);
  const storeTotal = Number(item.store_total_price || storeUnit * Number(item.quantity || 1) || 0);
  const amazonTotal = unitPrice * quantity;
  activeJob.pricing[item.asin] = {
    asin: item.asin,
    purchased_asin: purchaseItem.asin || item.asin,
    quantity,
    ordered_quantity: Number(item.quantity || 1),
    selected_variant_label: purchaseItem.selected_variant_label || "",
    requested_total_units: purchaseItem.requested_total_units || null,
    fulfilment_note: variantSelectionNote(item, purchaseItem),
    store_unit_price: storeUnit,
    store_total_price: storeTotal,
    amazon_unit_price: unitPrice,
    amazon_total_price: amazonTotal,
    profit_total: storeTotal - amazonTotal,
    source,
  };
  await setActiveJob(activeJob);
  return activeJob;
}

function currentAsinFromUrl() {
  const match = location.pathname.match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})/i);
  return match ? match[1].toUpperCase() : "";
}

function itemPrimaryLineId(item) {
  return item.line_id || item.line_ids?.[0] || null;
}

function comparableText(value) {
  return String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").replace(/\s+/g, " ").trim();
}

function matchCheckoutIssueItem(activeJob, title) {
  const items = activeJob.job?.items || [];
  if (!items.length) return null;
  if (items.length === 1) return items[0];
  const wanted = comparableText(title);
  if (!wanted) return null;
  return items.find((item) => {
    const names = [item.product_name, ...(item.product_names || [])].map(comparableText).filter(Boolean);
    return names.some((name) => name.includes(wanted) || wanted.includes(name));
  }) || null;
}

function checkoutLimitPurchaseIssue(activeJob) {
  const panel = document.querySelector("#checkout-javaItemSelectPanel");
  if (!panel || !visible(panel)) return null;
  const panelText = normalizedText(panel.innerText || panel.textContent);
  if (
    !panelText.includes("limited purchase quantity") ||
    !panelText.includes("business has reached it")
  ) {
    return null;
  }

  const messageNode = [...panel.querySelectorAll("[data-messageid], .line-item-destination-message-groups, .a-alert-content, span")]
    .find((element) => normalizedText(element.innerText || element.textContent).includes("limited purchase quantity"));
  const parentView = messageNode?.closest?.("[id^='line-item-parent-view-']");
  const parentId = parentView?.id || "";
  const suffix = parentId.startsWith("line-item-parent-view-") ? parentId.slice("line-item-parent-view-".length) : "";
  const groupView = suffix ? document.getElementById(`line-item-group-display-${suffix}`) : messageNode?.closest?.("[id^='line-item-group-display-']");
  const titleNode = suffix ? document.getElementById(`item-block-product-title-${suffix}`) : groupView?.querySelector?.(".lineitem-title-text, [id^='item-block-product-title-']");
  const title = String(titleNode?.innerText || titleNode?.textContent || "").replace(/\s+/g, " ").trim();
  const item = matchCheckoutIssueItem(activeJob, title);
  const removeButton = [...(groupView || parentView || panel).querySelectorAll?.("a.js-delete-button, a[aria-label='Remove item'], a, button") || []]
    .find((element) => visible(element) && normalizedText(element.getAttribute?.("aria-label") || element.innerText || element.textContent).includes("remove item"));
  return {
    item,
    title,
    removeButton,
    message: "Limit purchase: Amazon says this item has limited purchase quantity and the business has already reached it.",
  };
}

function cartDeleteButtons() {
  const activeCart = document.querySelector("#sc-active-cart");
  if (!activeCart || !visible(activeCart)) return [];
  const activeItems = [...activeCart.querySelectorAll("[data-itemtype='active'], .sc-list-item[data-asin]")].filter(visible);
  const buttons = activeItems
    .map((item) =>
      item.querySelector(
        "input[data-action='delete-active'][data-feature-id='item-delete-button'], input[name^='submit.delete-active.'][value='Delete']",
      ),
    )
    .filter((button) => button && visible(button));
  if (buttons.length) return [...new Set(buttons)];
  return [
    ...activeCart.querySelectorAll(
      "input[data-action='delete-active'][data-feature-id='item-delete-button'], input[name^='submit.delete-active.'][value='Delete']",
    ),
  ].filter(visible);
}

function cartLooksEmpty() {
  const text = (document.body.innerText || "").replace(/\s+/g, " ").toLowerCase();
  return (
    text.includes("your amazon cart is empty") ||
    text.includes("your shopping cart is empty") ||
    text.includes("cart is empty")
  );
}

function cartItemQuantity(item) {
  const select = item.querySelector("select[name='quantity'], select[id*='quantity']");
  if (select?.value) {
    const value = Number(select.value);
    if (Number.isFinite(value) && value > 0) return value;
  }
  const input = item.querySelector("input[name='quantity'], input[id*='quantity']");
  if (input?.value) {
    const value = Number(input.value);
    if (Number.isFinite(value) && value > 0) return value;
  }
  const text = (item.innerText || item.textContent || "").replace(/\s+/g, " ");
  const match = text.match(/\bQty(?:uantity)?\s*:?\s*(\d+)\b/i);
  return match ? Number(match[1]) : 1;
}

function cartActiveItems() {
  const activeCart = document.querySelector("#sc-active-cart");
  if (!activeCart || !visible(activeCart)) return [];
  return [...activeCart.querySelectorAll("[data-itemtype='active'], .sc-list-item, [data-asin], [data-name='Active Items'] [role='listitem']")]
    .filter((item) => visible(item) && !/saved for later|sponsored/i.test(item.innerText || item.textContent || ""));
}

function addedItemMarkerKey(activeJob) {
  return `nutricity-added-${activeJob?.job?.group_key || "active"}`;
}

function checkoutMarkerKey(activeJob) {
  return `nutricity-checkout-${activeJob?.job?.group_key || "active"}`;
}

function markItemAdded(activeJob) {
  try {
    sessionStorage.setItem(addedItemMarkerKey(activeJob), String(Date.now()));
  } catch {
    // Session storage can be blocked on unusual browser profiles; state storage still carries the marker.
  }
}

function markCheckoutStarted(activeJob) {
  activeJob.checkoutStartedAt = Date.now();
  try {
    sessionStorage.setItem(checkoutMarkerKey(activeJob), String(activeJob.checkoutStartedAt));
  } catch {
    // State storage still carries the marker when session storage is unavailable.
  }
}

function itemWasAdded(activeJob) {
  if (activeJob?.addClickedAt || Object.keys(activeJob?.pricing || {}).length) return true;
  try {
    return Boolean(sessionStorage.getItem(addedItemMarkerKey(activeJob)));
  } catch {
    return false;
  }
}

function checkoutWasStarted(activeJob) {
  if (activeJob?.checkoutStartedAt || ["subscribe_checkout", "checkout", "editing_address", "complete_pending", "find_order_id"].includes(String(activeJob?.stage || ""))) return true;
  try {
    return Boolean(sessionStorage.getItem(checkoutMarkerKey(activeJob)));
  } catch {
    return false;
  }
}

function canClearCart(activeJob) {
  return (
    activeJob?.stage === "clear_cart" &&
    !activeJob.cartCleared &&
    !itemWasAdded(activeJob) &&
    !checkoutWasStarted(activeJob) &&
    Number(activeJob.itemIndex || 0) === 0
  );
}

function cartItemAsin(item) {
  const direct = String(item.getAttribute("data-asin") || "").toUpperCase();
  if (/^[A-Z0-9]{10}$/.test(direct)) return direct;
  const link = [...item.querySelectorAll("a[href]")].find((anchor) => /\/(?:dp|gp\/product)\/[A-Z0-9]{10}/i.test(anchor.href || anchor.getAttribute("href") || ""));
  const href = link?.href || link?.getAttribute("href") || "";
  const match = href.match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})/i);
  if (match) return match[1].toUpperCase();
  const textMatch = (item.innerText || item.textContent || "").match(/\b([A-Z0-9]{10})\b/);
  return textMatch ? textMatch[1].toUpperCase() : "";
}

function expectedCartQuantities(activeJob) {
  const expected = {};
  const pricing = Object.values(activeJob.pricing || {});
  if (pricing.length) {
    for (const item of pricing) {
      const asin = String(item.purchased_asin || item.asin || "").toUpperCase();
      if (!asin) continue;
      expected[asin] = (expected[asin] || 0) + Number(item.quantity || 1);
    }
    return expected;
  }
  for (const item of activeJob.job?.items || []) {
    const asin = String(item.asin || "").toUpperCase();
    if (!asin) continue;
    expected[asin] = (expected[asin] || 0) + Number(item.quantity || 1);
  }
  return expected;
}

function verifyCartQuantities(activeJob) {
  const activeCart = document.querySelector("#sc-active-cart");
  if (!activeCart || !visible(activeCart)) return { ok: true };
  const expected = expectedCartQuantities(activeJob);
  if (!Object.keys(expected).length) return { ok: true };
  const actual = {};
  const unknownItems = [];
  const items = cartActiveItems();
  for (const item of items) {
    const asin = cartItemAsin(item);
    if (!asin) {
      unknownItems.push((item.innerText || item.textContent || "").replace(/\s+/g, " ").trim().slice(0, 120));
      continue;
    }
    if (!expected[asin]) continue;
    actual[asin] = (actual[asin] || 0) + cartItemQuantity(item);
  }
  if (!Object.keys(actual).length && items.length && unknownItems.length) {
    return { ok: true, warning: `Could not read ASIN from Amazon cart markup. Found ${items.length} active cart item(s), so checkout was not blocked.` };
  }
  const mismatches = Object.entries(expected).filter(([asin, quantity]) => Number(actual[asin] || 0) !== Number(quantity));
  if (!mismatches.length) return { ok: true };
  const details = mismatches.map(([asin, quantity]) => ({ asin, expected: Number(quantity), actual: Number(actual[asin] || 0) }));
  const message = mismatches
    .map(([asin, quantity]) => `${asin} expected ${quantity}, cart has ${actual[asin] || 0}`)
    .join("; ");
  return { ok: false, message, mismatches: details };
}

function lineIdForAsin(activeJob, asin) {
  const wanted = String(asin || "").toUpperCase();
  const item = (activeJob.job?.items || []).find((entry) => String(entry.asin || "").toUpperCase() === wanted);
  if (item) return itemPrimaryLineId(item);
  const pricingItem = Object.values(activeJob.pricing || {}).find((entry) => {
    const purchasedAsin = String(entry.purchased_asin || "").toUpperCase();
    const originalAsin = String(entry.asin || "").toUpperCase();
    return purchasedAsin === wanted || originalAsin === wanted;
  });
  if (!pricingItem?.asin) return null;
  const originalItem = (activeJob.job?.items || []).find((entry) => String(entry.asin || "").toUpperCase() === String(pricingItem.asin || "").toUpperCase());
  return originalItem ? itemPrimaryLineId(originalItem) : null;
}

function promotionNodes() {
  const roots = [
    ...document.querySelectorAll(
      ".promoPriceBlockMessage, [data-csa-c-owner='PromotionsDiscovery'], #promoPriceBlockMessage_feature_div, #couponFeature, [id*='coupon'][id*='Feature']",
    ),
  ].filter(visible);
  const nodes = roots.filter((root) => {
    const text = (root.innerText || root.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
    if (!text || text.length > 600) return false;
    if (!/\b(coupon|promotion|promo|save)\b/i.test(text)) return false;
    const redeemed = [...root.querySelectorAll("[id^='doneButton'], .a-color-success")].some((node) => {
      const nodeText = (node.innerText || node.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
      return visible(node) && nodeText.includes("redeemed");
    });
    if (redeemed) return false;
    const hasRedeem = [...root.querySelectorAll("span.a-button:not(.a-button-disabled), input.a-button-input[type='submit'], button")].some((candidate) => promoRedeemTarget(candidate));
    const hasCouponCheckbox = [...root.querySelectorAll("input[type='checkbox']:not(:checked), .a-checkbox:not(.aok-hidden) label, .a-icon-checkbox")]
      .some((candidate) => visible(candidate) || visible(candidate.closest?.("label, .a-checkbox, span, div")));
    return hasRedeem || hasCouponCheckbox;
  });
  return nodes.filter(visible).slice(0, 12);
}

function savingsRoots(context = "regular") {
  return context === "sns"
    ? [
        document.querySelector("#snsAccordionRowMiddle"),
        document.querySelector("#snsAccordionRow"),
        document.querySelector("#reinvent_price_desktop_snsAccordionRowMiddle"),
        document.querySelector("#promoPriceBlockMessage_feature_div"),
        document.body,
      ].filter(Boolean)
    : [
        document.querySelector("#reinvent_price_desktop_newAccordionRow"),
        document.querySelector("#promoPriceBlockMessage_feature_div"),
        document.body,
      ].filter(Boolean);
}

function promoRedeemTarget(candidate) {
  const button = candidate.closest?.("span.a-button") || candidate;
  if (button.classList?.contains("a-button-disabled") || button.closest?.(".aok-hidden")) return null;
  const text = (button.innerText || button.textContent || candidate.value || "").replace(/\s+/g, " ").trim().toLowerCase();
  if (text !== "redeem") return null;
  const promoBlock = button.closest?.(".promoPriceBlockMessage, [data-csa-c-owner='PromotionsDiscovery'], #promoPriceBlockMessage_feature_div");
  if (!promoBlock) return null;
  const clickTarget = button.closest(".a-declarative") || candidate;
  return visible(clickTarget) || visible(button) ? clickTarget : null;
}

async function clickVisibleRedeemPromotions(context = "regular") {
  const clicked = new Set();
  let count = 0;
  for (const root of savingsRoots(context)) {
    const candidates = [
      ...root.querySelectorAll("span.a-button:not(.a-button-disabled), input.a-button-input[type='submit']"),
    ];
    for (const candidate of candidates) {
      const target = promoRedeemTarget(candidate);
      if (!target || clicked.has(target)) continue;
      target.scrollIntoView({ block: "center", behavior: "smooth" });
      await sleep(350);
      target.click();
      clicked.add(target);
      count += 1;
      await waitForStableDom(700, 5000);
      await sleep(1300);
    }
  }
  if (count) {
    showPanel("Nutricity fulfilment", `Redeemed ${count} Amazon promotion${count === 1 ? "" : "s"}.`, null, null);
  }
  return count;
}

async function clipVisibleCoupons(context = "regular") {
  const roots = savingsRoots(context);
  const clicked = new Set();
  let count = 0;
  for (const root of roots) {
    const candidates = [
      ...root.querySelectorAll("input[type='checkbox']:not(:checked)"),
      ...root.querySelectorAll("[data-csa-c-action='clipPromotion'] input[type='checkbox']:not(:checked), [data-csa-c-owner='PromotionsDiscovery'] input[type='checkbox']:not(:checked)"),
      ...root.querySelectorAll(".a-checkbox:not(.aok-hidden) label, .a-icon-checkbox"),
    ].filter((element) => visible(element) || visible(element.closest?.("label, .a-checkbox, span, div")));
    for (const candidate of candidates) {
      const target = candidate.matches?.("input[type='checkbox']") ? candidate : candidate.closest?.("label") || candidate;
      if (clicked.has(target)) continue;
      const nearbyText = (target.closest?.(".promoPriceBlockMessage, [data-csa-c-owner='PromotionsDiscovery'], #promoPriceBlockMessage_feature_div")?.innerText || target.parentElement?.innerText || "").toLowerCase();
      if (!nearbyText.includes("coupon") && !nearbyText.includes("save")) continue;
      target.scrollIntoView({ block: "center", behavior: "smooth" });
      await sleep(350);
      target.click();
      clicked.add(target);
      count += 1;
      await sleep(1200);
    }
  }
  if (count) {
    showPanel("Nutricity fulfilment", `Applied ${count} Amazon coupon${count === 1 ? "" : "s"}.`, null, null);
  }
  return count;
}

function unavailableMessage() {
  const allBuyingOptions = [...document.querySelectorAll(
    "a[href*='/gp/offer-listing/'], a[href*='/offer-listing/'], a[title='See All Buying Options'], a.a-button-text",
  )].find((element) => {
    const text = (element.innerText || element.textContent || element.getAttribute("title") || "").replace(/\s+/g, " ").trim().toLowerCase();
    return visible(element) && text.includes("see all buying options");
  });
  if (allBuyingOptions) {
    return "Amazon only shows See All Buying Options, so this ASIN is not directly available to add to cart.";
  }
  const roots = [
    ...document.querySelectorAll("#availability, #outOfStock, #availabilityInsideBuyBox_feature_div, #desktop_qualifiedBuyBox, #buybox, #centerCol"),
  ].filter(Boolean);
  for (const root of roots) {
    if (!visible(root) && root.id !== "centerCol") continue;
    const text = (root.innerText || root.textContent || "").replace(/\s+/g, " ").trim();
    const lowered = text.toLowerCase();
    if (
      lowered.includes("currently unavailable") ||
      lowered.includes("we don't know when or if this item will be back in stock") ||
      lowered.includes("we do not know when or if this item will be back in stock")
    ) {
      return text.match(/currently unavailable[^.]*\./i)?.[0] || "Currently unavailable. We do not know when or if this item will be back in stock.";
    }
  }
  return "";
}

function amazonProductPageErrorMessage() {
  const title = String(document.title || "").replace(/\s+/g, " ").trim();
  const bodyText = String(document.body?.innerText || document.body?.textContent || "").replace(/\s+/g, " ").trim();
  const loweredTitle = title.toLowerCase();
  const loweredBody = bodyText.toLowerCase();
  if (
    loweredTitle.includes("page not found") ||
    loweredTitle.includes("404") ||
    loweredBody.includes("sorry! we couldn't find that page") ||
    loweredBody.includes("sorry we couldn't find that page") ||
    loweredBody.includes("looking for something?") ||
    loweredBody.includes("the web address you entered is not a functioning page")
  ) {
    return "Amazon opened a 404 / product page not found page.";
  }
  if (
    loweredBody.includes("no results for") ||
    loweredBody.includes("did not match any products") ||
    loweredBody.includes("couldn't find a match")
  ) {
    return "Amazon could not find a product page for this ASIN.";
  }
  return "";
}

function removeMissingItemFromActiveJob(activeJob, item, purchaseItem = item) {
  if (!item) return activeJob.job?.items || [];
  const missingLineId = itemPrimaryLineId(item);
  const missingAsins = new Set([item?.asin, purchaseItem?.asin].filter(Boolean).map((asin) => String(asin).toUpperCase()));
  const currentIndex = Number(activeJob.itemIndex || 0);
  const remainingItems = (activeJob.job?.items || []).filter((entry) => {
    const lineId = itemPrimaryLineId(entry);
    if (missingLineId && lineId === missingLineId) return false;
    if (missingLineId) return true;
    return !missingAsins.has(String(entry.asin || "").toUpperCase());
  });
  activeJob.job.items = remainingItems;
  activeJob.job.line_ids = (activeJob.job.line_ids || []).filter((lineId) => !missingLineId || lineId !== missingLineId);
  if (activeJob.pricing) {
    for (const asin of missingAsins) delete activeJob.pricing[asin];
  }
  activeJob.itemIndex = currentIndex;
  return remainingItems;
}

async function continueAfterPartialMissing(activeJob, item, purchaseItem, message, failureCode = "unavailable", details = {}) {
  if (!item) return false;
  const lineId = itemPrimaryLineId(item);
  if (!lineId || (activeJob.job?.items || []).length <= 1) return false;
  showPanel("Sending line to Missing ASINs", message, null, null);
  const result = await send({
    type: "MARK_LINE_MISSING",
    message,
    missingAsin: item?.asin || purchaseItem?.asin || currentAsinFromUrl(),
    missingLineId: lineId,
    failureCode,
    requestedQuantity: details.requestedQuantity ?? null,
    fulfilledQuantity: details.fulfilledQuantity ?? null,
    availableQuantity: details.availableQuantity ?? null,
  });
  if (!result?.ok) return false;
  const remainingItems = removeMissingItemFromActiveJob(activeJob, item, purchaseItem);
  if (!remainingItems.length) return false;
  if (activeJob.itemIndex < remainingItems.length) {
    activeJob.stage = "product";
    await setActiveJob(activeJob);
    showPanel("Split fulfilment", `${message} ${result.message || ""} Continuing with remaining Amazon item(s).`, null, null);
    location.href = `https://www.amazon.com/dp/${remainingItems[activeJob.itemIndex].asin}`;
  } else {
    activeJob.stage = "cart";
    await setActiveJob(activeJob);
    showPanel("Split fulfilment", `${message} ${result.message || ""} Proceeding to checkout for remaining Amazon item(s).`, null, null);
    location.href = "https://www.amazon.com/cart?ref_=sw_gtc";
  }
  return true;
}

async function failCurrentItemAsMissing(activeJob, item, purchaseItem, message, failureCode = "unavailable", details = {}) {
  if (await continueAfterPartialMissing(activeJob, item, purchaseItem, message, failureCode, details)) return true;
  showPanel("Sending to Missing ASINs", message, null, null);
  const result = await send({
    type: "FAIL_JOB",
    message,
    missingAsin: item?.asin || purchaseItem?.asin || currentAsinFromUrl(),
    missingLineId: item ? itemPrimaryLineId(item) : null,
    failureCode,
    requestedQuantity: details.requestedQuantity ?? null,
    fulfilledQuantity: details.fulfilledQuantity ?? null,
    availableQuantity: details.availableQuantity ?? null,
  });
  if (result?.ok) {
    showPanel("Missing ASINs", `${message} ${result.message || "Order moved to Missing ASINs."}`, null, null);
    return true;
  }
  throw new Error(result?.message || "The app did not confirm the Missing ASIN report.");
}

async function applyAdditionalSavings(context = "regular") {
  const redeemed = await clickVisibleRedeemPromotions(context);
  const clipped = await clipVisibleCoupons(context);
  return redeemed + clipped;
}

function markPromotions() {
  const nodes = promotionNodes();
  nodes.forEach((node) => node.classList.add("nutricity-highlight"));
  return nodes.length;
}

async function applySubscribeAndSaveIfCheaper(quantity, activeJob = null) {
  await waitForElement(["#corePrice_feature_div .a-price", "#apex_desktop .a-price", "#snsAccordionRowMiddle, #snsAccordionRow, #snsAccordionRowContent, [data-csa-c-slot-id*='sns']"], 12000);
  const { regular: pagePrice, sns: snsPrice } = productPriceSnapshot();
  if (!pagePrice || !snsPrice || snsPrice >= pagePrice) {
    showPanel(
      "Subscribe & Save",
      `Using one-time purchase because Subscribe & Save was not cheaper or could not be read (${moneyText(snsPrice)} vs ${moneyText(pagePrice)}).`,
      null,
      null,
    );
    return false;
  }
  showPanel("Subscribe & Save", `Subscribe & Save is cheaper (${moneyText(snsPrice)} vs ${moneyText(pagePrice)}). Switching to subscription checkout.`, null, null);

  const activated = await activateSubscribeAndSaveOption();
  if (!activated) {
    const error = new Error("Subscribe & Save is cheaper, but Amazon did not activate the Subscribe & Save radio button. Fulfilment paused before touching regular Add to cart quantity.");
    error.failureCode = "";
    throw error;
  }
  const controlsReady = await waitUntil(snsQuantityControlVisible, 8000, 300);
  if (!controlsReady) {
    const error = new Error("Subscribe & Save is cheaper and selected, but Amazon did not show the Subscribe & Save quantity controls. Fulfilment paused before touching regular Add to cart quantity.");
    error.failureCode = "";
    throw error;
  }
  await applyAdditionalSavings("sns");
  const quantitySet = await setQuantity(quantity, "sns");
  if (!quantitySet) {
    const requestedQuantity = Math.max(1, Math.round(Number(quantity || 1)));
    const quantityIssue = window.__nutricityLastQuantityIssue || {};
    const availableQuantity = quantityIssue.availableQuantity || maxPredefinedQuantity("sns") || maxSelectableQuantity("sns");
    const error = new Error(
      availableQuantity > 0 && availableQuantity < requestedQuantity
        ? `Less Subscribe & Save quantity available. Customer ordered ${requestedQuantity}, Amazon only allows ${availableQuantity}. ${quantityIssue.message || ""}`.trim()
        : `Could not set Subscribe & Save quantity ${requestedQuantity}. ${quantityIssue.message || ""}`.trim(),
    );
    error.failureCode = "partial_quantity";
    error.requestedQuantity = requestedQuantity;
    error.fulfilledQuantity = availableQuantity > 0 && availableQuantity < requestedQuantity ? availableQuantity : null;
    error.availableQuantity = availableQuantity > 0 && availableQuantity < requestedQuantity ? availableQuantity : null;
    throw error;
  }
  await configureSubscribeAndSaveDelivery();
  const subscribeButton = document.querySelector("#rcx-subscribe-submit-button-announce, #rcx-subscribe-submit-button button, #rcx-subscribe-submit-button input, button[value='snsText']") || findButtonByText(["subscribe"]);
  if (subscribeButton) {
    if (activeJob) {
      activeJob.stage = "subscribe_checkout";
      activeJob.addClickedAt = Date.now();
      activeJob.subscribeAndSave = true;
      markCheckoutStarted(activeJob);
      markItemAdded(activeJob);
      await setActiveJob(activeJob);
    }
    await clickElement(subscribeButton, "Subscribe button");
    return true;
  }
  return false;
}

async function activateSubscribeAndSaveOption() {
  if (subscribeAndSaveIsActive()) return true;
  const target = findSubscribeAndSaveAccordionClickTarget();
  if (!target) return false;
  showPanel("Subscribe & Save", "Clicking the Subscribe & Save accordion row.", null, null);
  const clickTargets = [
    target,
    target.querySelector?.("[data-action='a-accordion'][role='button']"),
    target.querySelector?.(".a-accordion-row[role='button']"),
    target.querySelector?.(".accordion-header[role='button']"),
    target.querySelector?.("h5"),
  ].filter(Boolean);
  for (const clickTarget of [...new Set(clickTargets)]) {
    clickTarget.scrollIntoView({ block: "center", behavior: "smooth" });
    await sleep(250);
    clickTarget.focus?.();
    clickTarget.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, cancelable: true, pointerType: "mouse" }));
    clickTarget.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window }));
    clickTarget.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: window }));
    clickTarget.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, cancelable: true, pointerType: "mouse" }));
    clickTarget.click();
    clickTarget.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true, cancelable: true }));
    clickTarget.dispatchEvent(new KeyboardEvent("keyup", { key: "Enter", code: "Enter", bubbles: true, cancelable: true }));
    clickTarget.dispatchEvent(new KeyboardEvent("keydown", { key: " ", code: "Space", bubbles: true, cancelable: true }));
    clickTarget.dispatchEvent(new KeyboardEvent("keyup", { key: " ", code: "Space", bubbles: true, cancelable: true }));
    await waitForStableDom(700, 5000);
    if (subscribeAndSaveIsActive()) return true;
  }
  return await waitUntil(subscribeAndSaveIsActive, 3000, 250);
}

function subscribeAndSaveIsActive() {
  return Boolean(
    document.querySelector("#snsAccordionRowMiddle .a-accordion-radio-active, #snsAccordionRow .a-accordion-radio-active, #snsAccordionRowContent .a-accordion-radio-active, [data-csa-c-slot-id='snsAccordionRowMiddle'] .a-accordion-radio-active") ||
    document.querySelector("#snsAccordionRowMiddle [data-action='a-accordion'][aria-expanded='true'], #snsAccordionRow [data-action='a-accordion'][aria-expanded='true'], #snsAccordionRowContent [data-action='a-accordion'][aria-expanded='true']") ||
    snsQuantityControlVisible() ||
    [...document.querySelectorAll(subscribeAndSaveRootSelector())].some((root) => root.querySelector?.("[role='radio'][aria-checked='true']")),
  );
}

function findSubscribeAndSaveAccordionClickTarget() {
  return document.querySelector("#snsAccordionRowMiddle")
    || document.querySelector("#snsAccordionRow")
    || document.querySelector("#snsAccordionRowContent")
    || document.querySelector("[data-a-accordion-row-name='snsAccordionRowMiddle']")
    || document.querySelector("[data-csa-c-slot-id='snsAccordionRowMiddle']")?.closest(".a-box, [data-a-accordion-row-name]")
    || findSubscribeAndSaveRadio();
}

function findSubscribeAndSaveRadio(root = null) {
  const direct = [
    "#snsAccordionRowMiddle .a-accordion-row",
    "#snsAccordionRow .a-accordion-row",
    "#snsAccordionRowContent .a-accordion-row",
    "[data-csa-c-slot-id='snsAccordionRowMiddle'][role='button']",
    "[data-a-accordion-row-name='snsAccordionRowMiddle'] .a-accordion-row",
  ]
    .flatMap((selector) => [...document.querySelectorAll(selector)])
    .find((item) => item.querySelector(".a-accordion-radio.a-icon-radio-inactive") || /subscribe\s*&\s*save/i.test(item.innerText || item.textContent || ""));
  if (direct) return direct;
  const roots = root ? [root] : [...document.querySelectorAll(subscribeAndSaveRootSelector())];
  for (const item of roots) {
    if (!item) continue;
    const text = (item.innerText || item.textContent || "").replace(/\s+/g, " ").toLowerCase();
    if (!text.includes("subscribe") && !/sns/i.test(item.id || "") && !/sns/i.test(item.getAttribute("data-csa-c-slot-id") || "")) continue;
    const inactive = item.querySelector(".a-accordion-radio.a-icon-radio-inactive, [role='radio'][aria-checked='false'], input[type='radio']:not(:checked)");
    if (inactive) return inactive.closest("[role='button'], .a-accordion-row, label") || inactive;
    const header = item.matches("[role='button'], .a-accordion-row") ? item : item.closest?.("[role='button'], .a-accordion-row");
    if (header && !header.querySelector(".a-accordion-radio-active")) return header;
  }
  return document.querySelector(".a-accordion-row[data-csa-c-slot-id*='sns'] .a-accordion-radio.a-icon-radio-inactive")?.closest("[role='button'], .a-accordion-row") || null;
}

async function configureSubscribeAndSaveDelivery() {
  showPanel("Nutricity fulfilment", "Checking Subscribe & Save delivery preferences.", null, null);
  await chooseSubscribeSoonerDelivery();
  await chooseSubscribeFrequencySixMonths();
}

async function chooseSubscribeSoonerDelivery() {
  const changeDateLink = findVisibleTextTarget(["change first delivery date"]);
  if (!changeDateLink) return false;
  await clickElement(changeDateLink, "Change first delivery date");
  const soonerOption = await waitUntil(() => (
    document.querySelector("#onmlDeliveryOpt") ||
    findVisibleTextTarget(["get it sooner"], "#snsDeliveryDateBottomSheet label, .a-popover-inner label, .a-popover-inner span, .a-popover-inner div")
  ), 7000);
  if (!soonerOption) return false;
  const soonerTarget = soonerOption.closest?.("label") || soonerOption;
  await clickElement(soonerTarget, "Get it sooner delivery option");
  const selectDateButton = await waitUntil(() => (
    findVisibleTextTarget(["select date"], "#snsDeliveryDateBottomSheet input, #snsDeliveryDateBottomSheet button, #snsDeliveryDateBottomSheet span.a-button, #snsDeliveryDateBottomSheet span.a-button-text, .a-popover-inner input, .a-popover-inner button, .a-popover-inner span.a-button, .a-popover-inner span.a-button-text")
  ), 5000);
  if (selectDateButton) {
    const buttonTarget = selectDateButton.closest?.("span.a-button, button") || selectDateButton;
    await clickElement(buttonTarget, "Select Subscribe & Save delivery date");
  }
  await waitForStableDom(700, 5000);
  return true;
}

async function chooseSubscribeFrequencySixMonths() {
  showPanel("Subscribe & Save", "Opening delivery every dropdown.", null, null);
  const nativeFrequency = [...document.querySelectorAll("#snsAccordionRowMiddle select, #snsAccordionRow select, #snsAccordionRowContent select, #reinvent_price_desktop_snsAccordionRowMiddle select")]
    .find((select) => [...select.options || []].some((option) => /\b(weeks?|months?)\b/i.test(option.textContent || "") || /\d+[WM]\|sns/i.test(String(option.value || ""))));
  const nativeContainerButton = nativeFrequency?.closest?.(".a-dropdown-container")?.querySelector?.(".a-button-dropdown, [data-action='a-dropdown-button']");
  const explicitButton = document.querySelector("#rcxOrdFreqSns, #rcxOrdFreqSns-announce")?.closest?.("[data-action='a-dropdown-button'], .a-button-dropdown, span.a-button");
  const frequencyButton = findSubscribeFrequencyDropdownButton();
  const dropdownButton = explicitButton || nativeContainerButton || frequencyButton;
  if (!dropdownButton || !visible(dropdownButton)) return selectNativeSubscribeFrequency(nativeFrequency);
  await clickElement(dropdownButton, "Subscribe & Save delivery schedule dropdown");
  const frequencyOption = await waitUntil(findLastSubscribeFrequencyOption, 5000);
  if (!frequencyOption) return selectNativeSubscribeFrequency(nativeFrequency);
  const selectedText = (frequencyOption.textContent || "").replace(/\s+/g, " ").trim();
  showPanel("Subscribe & Save", `Selecting delivery every ${selectedText}.`, null, null);
  await clickElement(frequencyOption, `Subscribe & Save ${selectedText} schedule`);
  await waitForStableDom(700, 5000);
  return true;
}

async function selectNativeSubscribeFrequency(select) {
  if (!select?.options?.length) return false;
  const options = [...select.options].filter((option) => /sns/i.test(String(option.value || "")) || /\b(weeks?|months?)\b/i.test(option.textContent || ""));
  const target = options.at(-1);
  if (!target) return false;
  const selectedText = (target.textContent || "").replace(/\s+/g, " ").trim();
  showPanel("Subscribe & Save", `Selecting delivery every ${selectedText}.`, null, null);
  select.value = target.value;
  select.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
  select.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  await waitForStableDom(700, 5000);
  return true;
}

function findSubscribeFrequencyDropdownButton() {
  const roots = [
    document.querySelector("#snsAccordionRowMiddle"),
    document.querySelector("#snsAccordionRow"),
    document.querySelector("#snsAccordionRowContent"),
    document.querySelector("#reinvent_price_desktop_snsAccordionRowMiddle"),
  ].filter(Boolean);
  const candidates = [
    ...roots.flatMap((root) => [
      ...root.querySelectorAll("[data-action='a-dropdown-button'], .a-button-dropdown, span.a-button"),
    ]),
    ...document.querySelectorAll("[id$='-announce'], span.a-dropdown-prompt"),
  ];
  return candidates.find((element) => {
    const text = (element.innerText || element.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
    const inSns = roots.some((root) => root.contains(element));
    const looksLikeFrequency = /deliver(?:y)?\s*every/.test(text) || /\b\d+\s*(weeks?|months?)\b/.test(text);
    return visible(element) && (inSns || /rcxordfreqsns/i.test(element.id || "")) && looksLikeFrequency;
  })?.closest?.("[data-action='a-dropdown-button'], .a-button-dropdown, span.a-button") || null;
}

function findLastSubscribeFrequencyOption() {
  const popovers = [...document.querySelectorAll(".a-popover-wrapper, .a-popover-inner")]
    .filter((popover) => visible(popover) && /\b(weeks?|months?)\b/i.test(popover.innerText || popover.textContent || ""));
  for (const popover of popovers) {
    const scroller = popover.querySelector(".a-popover-inner") || popover;
    scroller.scrollTop = scroller.scrollHeight;
  }
  const options = popovers
    .flatMap((popover) => [...popover.querySelectorAll("a.a-dropdown-link, a[role='option']")])
    .filter((link, index, all) => {
      const text = (link.textContent || "").replace(/\s+/g, " ").trim();
      const value = link.getAttribute("data-value") || "";
      return all.indexOf(link) === index && visible(link) && (/\b\d+\s*(weeks?|months?)\b/i.test(text) || /\d+[WM]\|sns/i.test(value));
    });
  return options.at(-1) || null;
}

function quantitySelects(context = "regular") {
  const selects = [...document.querySelectorAll("select")].filter((select) => {
    const id = select.id || "";
    if (context === "sns") {
      return (
        /sns/i.test(id) && id.includes("predefinedQuantitiesDropdown") ||
        Boolean(select.closest(subscribeAndSaveRootSelector()))
      );
    }
    return (
      id === "quantity" ||
      id.includes("predefinedQuantitiesDropdown") && !/^sns/i.test(id) ||
      select.name === "quantity"
    );
  });
  return selects;
}

function maxSelectableQuantity(context = "regular") {
  const quantities = [];
  for (const select of quantitySelects(context)) {
    for (const option of [...select.options || []]) {
      const text = clean(option.textContent || option.value);
      if (/10\+/.test(text)) {
        quantities.push(10);
        continue;
      }
      const numeric = Number((option.value || text).match(/\d+/)?.[0] || 0);
      if (numeric > 0) quantities.push(numeric);
    }
  }
  return quantities.length ? Math.max(...quantities) : 0;
}

function maxPredefinedQuantity(context = "regular") {
  const quantities = [];
  const selectors = context === "sns"
    ? ["select[id^='sns'][id*='predefinedQuantitiesDropdown']"]
    : ["select[id*='new_buyingOption'][id*='predefinedQuantitiesDropdown']", "select[id*='DesktopFfqp'][id*='predefinedQuantitiesDropdown']:not([id^='sns'])"];
  for (const selector of selectors) {
    for (const select of document.querySelectorAll(selector)) {
      for (const option of [...select.options || []]) {
        const numeric = Number((option.value || option.textContent || "").match(/\d+/)?.[0] || 0);
        if (numeric > 0) quantities.push(numeric);
      }
    }
  }
  return quantities.length ? Math.max(...quantities) : 0;
}

function quantityFreeFormInput(select, context = "regular") {
  const id = select?.id || "";
  const candidates = [];
  if (id.includes("predefinedQuantitiesDropdown")) {
    candidates.push(`#${CSS.escape(id.replace("predefinedQuantitiesDropdown", "freeQuantityTextInput"))}`);
    candidates.push(`#${CSS.escape(id.replace("predefinedQuantitiesDropdown", "quantityTextInput"))}`);
  }
  if (context === "sns") {
    candidates.push(
      "input[id*='sns'][id$='freeQuantityTextInput']",
      "input[id*='sns'][id$='quantityTextInput']",
      "input[id^='sns'][id$='freeQuantityTextInput']",
      "input[id^='sns'][id$='quantityTextInput']",
      "#snsAccordionRowMiddle input.freeQuantityTextInput",
      "#snsAccordionRowMiddle input.quantity-text-input-with-label",
      "#snsAccordionRow input.freeQuantityTextInput",
      "#snsAccordionRow input.quantity-text-input-with-label",
    );
  } else {
    candidates.push(
      "input[id*='new_buyingOption'][id$='freeQuantityTextInput']",
      "input[id*='new_buyingOption'][id$='quantityTextInput']",
      "input.freeQuantityTextInput:not([id^='sns'])",
      "input.quantity-text-input-with-label:not([id^='sns'])",
      "input#quantity",
    );
  }
  for (const selector of candidates) {
    const input = document.querySelector(selector);
    if (input) return input;
  }
  if (context === "sns") {
    for (const root of document.querySelectorAll(subscribeAndSaveRootSelector())) {
      const input = root.querySelector("input.freeQuantityTextInput, input.quantity-text-input-with-label, input[id$='quantityTextInput'], input[id$='freeQuantityTextInput']");
      if (input) return input;
    }
  }
  return null;
}

function quantityUpdateButton(select, context = "regular") {
  const id = select?.id || "";
  const candidates = [];
  if (id.includes("predefinedQuantitiesDropdown")) {
    candidates.push(`#${CSS.escape(id.replace("predefinedQuantitiesDropdown", "updateButton"))}`);
  }
  if (id.includes("quantityTextInput")) {
    candidates.push(`#${CSS.escape(id.replace("quantityTextInput", "updateButton"))}`);
  }
  if (id.includes("freeQuantityTextInput")) {
    candidates.push(`#${CSS.escape(id.replace("freeQuantityTextInput", "updateButton"))}`);
  }
  if (context === "sns") {
    candidates.push("#snsQuantity_feature_div [id$='-updateButton']", "#snsAccordionRowMiddle [id$='-updateButton']", "#snsAccordionRow [id$='-updateButton']");
  } else {
    candidates.push("[id*='new_buyingOption'][id$='-updateButton']", "#qualifiedBuybox [id$='-updateButton']", "#desktop_qualifiedBuyBox [id$='-updateButton']");
  }
  for (const selector of candidates) {
    const button = document.querySelector(`${selector} button, ${selector} input, ${selector}`);
    if (button && visible(button)) return button;
  }
  return null;
}

function quantityTextInputs(context = "regular") {
  const selectors = context === "sns"
    ? [
        "input[id*='sns'][id$='quantityTextInput']",
        "input[id*='sns'][id$='freeQuantityTextInput']",
        "input[id^='sns'][id$='quantityTextInput']",
        "input[id^='sns'][id$='freeQuantityTextInput']",
        "#snsAccordionRowMiddle input.quantity-text-input-with-label",
        "#snsAccordionRowMiddle input.freeQuantityTextInput",
      ]
    : [
        "input[id*='new_buyingOption'][id$='quantityTextInput']",
        "input[id*='new_buyingOption'][id$='freeQuantityTextInput']",
        "input.quantity-text-input-with-label:not([id^='sns'])",
        "input.freeQuantityTextInput:not([id^='sns'])",
      ];
  const inputs = selectors.flatMap((selector) => [...document.querySelectorAll(selector)]);
  if (context === "sns") {
    for (const root of document.querySelectorAll(subscribeAndSaveRootSelector())) {
      inputs.push(...root.querySelectorAll("input.quantity-text-input-with-label, input.freeQuantityTextInput, input[id$='quantityTextInput'], input[id$='freeQuantityTextInput']"));
    }
  }
  return inputs.filter((input, index, all) => (
    all.indexOf(input) === index && visible(input)
  ));
}

async function clickQuantityUpdateButton(select, context = "regular") {
  const button = await waitUntil(() => quantityUpdateButton(select, context), 2500, 250);
  if (!button) return false;
  await clickElement(button, `${context === "sns" ? "Subscribe & Save " : ""}quantity update`);
  await waitForStableDom(500, 4000);
  return true;
}

function quantityAvailabilityIssue(context = "regular", requestedQuantity = 1) {
  const roots = context === "sns"
    ? [
        ...document.querySelectorAll("#snsQuantity_feature_div, #snsAccordionRowMiddle, #snsAccordionRow, #snsAccordionRowContent, [id^='snsABDesktopFfqp_'][id$='-limitedAvailabilityMessage']"),
      ]
    : [
        ...document.querySelectorAll("#qualifiedBuybox, #desktop_qualifiedBuyBox, #buybox, #centerCol, #splitoffer_detailpage_buybox_section, [id*='DesktopFfqp_'][id$='-limitedAvailabilityMessage']:not([id^='sns'])"),
      ];
  const text = roots
    .map((root) => (root.innerText || root.textContent || "").replace(/\s+/g, " ").trim())
    .filter(Boolean)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
  const lowered = text.toLowerCase();
  const hasSingleSellerSplitOffer = lowered.includes("quantity unavailable from a single seller") || lowered.includes("buy from multiple sellers");
  const hasSpecificInventoryLimit = (
    lowered.includes("limited availability at this quantity") ||
    lowered.includes("limited availability") ||
    lowered.includes("not enough inventory") ||
    lowered.includes("not enough available") ||
    lowered.includes("maximum quantity") ||
    lowered.includes("seller does not have") ||
    lowered.includes("seller doesn't have") ||
    lowered.includes("only") && lowered.includes("available")
  );
  if (context === "sns" && hasSingleSellerSplitOffer && !hasSpecificInventoryLimit) {
    return null;
  }
  const hasIssue = (
    hasSpecificInventoryLimit ||
    hasSingleSellerSplitOffer
  );
  const splitOffer = context !== "sns" && document.querySelector("#splitoffer_detailpage_buybox_link[href*='quantity=']");
  if (!hasIssue && !splitOffer) return null;

  const requested = Math.max(1, Math.round(Number(requestedQuantity || 1)));
  const textQuantity =
    Number(lowered.match(/only\s+(\d+)\s+(?:left|available|in stock)/)?.[1] || 0) ||
    Number(lowered.match(/maximum(?: order)? quantity(?: is)?\s+(\d+)/)?.[1] || 0);
  const predefinedQuantity = maxPredefinedQuantity(context);
  const availableQuantity = textQuantity || (predefinedQuantity > 0 && predefinedQuantity < requested ? predefinedQuantity : 0);
  return {
    message: text || (context === "sns" ? "Limited availability at this Subscribe & Save quantity." : "Quantity unavailable from a single seller."),
    requestedQuantity: requested,
    availableQuantity: availableQuantity || null,
    fulfilledQuantity: availableQuantity || null,
  };
}

function partialQuantityMessage(asin, requestedQuantity, availableQuantity, issueMessage = "", context = "regular") {
  const prefix = context === "sns" ? "Less Subscribe & Save quantity available" : "Less quantity available";
  const availableText = availableQuantity
    ? `Amazon only allows ${availableQuantity}`
    : "Amazon showed limited availability";
  return `${prefix} for ASIN ${asin}. Customer ordered ${requestedQuantity}, ${availableText}.${issueMessage ? ` ${issueMessage}` : ""}`.trim();
}

async function pauseAfterMissingAsinReportFailure(activeJob, message) {
  activeJob.pausedStage = activeJob.stage || "product";
  activeJob.paused = true;
  await setActiveJob(activeJob);
  showPanel(
    "Missing ASIN report failed",
    `${message} The order is paused so it does not stay locked silently.`,
    "I did it manually, continue",
    () => continueAfterManualStep(activeJob),
  );
}

async function reportMissingQuantityIssue(activeJob, item, purchaseItem, quantityIssue = {}) {
  const requestedQuantity = Math.max(1, Math.round(Number(purchaseItem?.quantity || item?.quantity || quantityIssue.requestedQuantity || 1)));
  const availableQuantity = quantityIssue.availableQuantity || quantityIssue.fulfilledQuantity || maxPredefinedQuantity(quantityIssue.context || "regular") || maxSelectableQuantity(quantityIssue.context || "regular") || null;
  const message = partialQuantityMessage(purchaseItem?.asin || item?.asin || "", requestedQuantity, availableQuantity, quantityIssue.message || "", quantityIssue.context || "regular");
  showPanel("Sending to Missing ASINs", message, null, null);
  const result = await send({
    type: "FAIL_JOB",
    message,
    missingAsin: item?.asin || purchaseItem?.asin || "",
    missingLineId: item ? itemPrimaryLineId(item) : null,
    failureCode: "partial_quantity",
    requestedQuantity,
    fulfilledQuantity: availableQuantity,
    availableQuantity,
  });
  if (result?.ok) {
    showPanel("Missing ASINs", `${message} ${result.message || "Quantity issue moved to Missing ASINs."}`, null, null);
    return true;
  }
  throw new Error(result?.message || "Could not move quantity issue to Missing ASINs.");
}

async function chooseAmazonDropdownOption(select, qty) {
  return chooseAmazonDropdownText(select, qty > 9 ? "10+" : String(qty));
}

async function chooseAmazonDropdownText(select, optionText) {
  const container = select.closest(".a-dropdown-container") || select.parentElement;
  const dropdownButton = container?.querySelector(".a-button-dropdown, [data-action='a-dropdown-button']");
  if (!dropdownButton || !visible(dropdownButton)) return false;
  dropdownButton.scrollIntoView({ block: "center", behavior: "smooth" });
  await sleep(350);
  dropdownButton.click();
  await sleep(700);
  const wanted = String(optionText || "").replace(/\s+/g, " ").trim();
  const links = [...document.querySelectorAll(".a-popover-wrapper a.a-dropdown-link, .a-popover-inner a.a-dropdown-link, a[role='option']")].filter(visible);
  const option = links.find((link) => (link.textContent || "").replace(/\s+/g, " ").trim() === wanted);
  if (!option) return false;
  option.scrollIntoView({ block: "nearest" });
  await sleep(250);
  option.click();
  await sleep(900);
  return true;
}

async function selectFreeFormQuantityOption(select, qty) {
  const thresholds = qty > 9 ? ["10+", "4+"] : ["4+", "10+"];
  const option = [...select.options || []].find((item) => {
    const text = (item.textContent || item.value || "").replace(/\s+/g, " ").trim();
    return thresholds.includes(text) || item.value === "";
  });
  if (!option) return false;
  const text = (option.textContent || option.value || "").replace(/\s+/g, " ").trim();
  const clicked = await chooseAmazonDropdownText(select, text || thresholds[0]);
  if (clicked) return true;
  select.value = option.value;
  select.dispatchEvent(new Event("input", { bubbles: true }));
  select.dispatchEvent(new Event("change", { bubbles: true }));
  await sleep(900);
  return true;
}

async function setNativeSelectQuantity(select, qty) {
  if (!select) return false;
  const wantedValue = qty > 9 ? "" : String(qty);
  const option = [...select.options].find((item) => String(item.value) === wantedValue || Number(item.value) === qty);
  if (!option) return false;
  select.value = option.value;
  select.dispatchEvent(new Event("input", { bubbles: true }));
  select.dispatchEvent(new Event("change", { bubbles: true }));
  await sleep(1000);
  return true;
}

async function setFreeFormQuantity(select, qty, context = "regular") {
  const input = quantityFreeFormInput(select, context);
  if (!input) return false;
  await selectFreeFormQuantityOption(select, qty);
  await sleep(500);
  input.classList.remove("aok-hidden");
  input.removeAttribute("aria-hidden");
  input.scrollIntoView({ block: "center", behavior: "smooth" });
  await sleep(350);
  input.focus();
  input.select?.();
  input.value = String(qty);
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
  input.blur();
  await sleep(1000);
  const updated = await clickQuantityUpdateButton(select, context);
  await sleep(updated ? 1300 : 700);
  const value = Number(String(input.value || "").replace(/[^\d.]/g, ""));
  return Number.isFinite(value) && value === qty;
}

async function setDirectQuantityInput(input, qty, context = "regular") {
  if (!input) return false;
  showPanel("Nutricity fulfilment", `Typing ${context === "sns" ? "Subscribe & Save " : ""}quantity ${qty}.`, null, null);
  input.classList.remove("aok-hidden");
  input.removeAttribute("aria-hidden");
  input.scrollIntoView({ block: "center", behavior: "smooth" });
  await sleep(350);
  await setInputValue(input, String(qty));
  input.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
  input.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  input.blur();
  await sleep(800);
  const updated = await clickQuantityUpdateButton(input, context);
  await sleep(updated ? 1300 : 700);
  const value = Number(String(input.value || "").replace(/[^\d.]/g, ""));
  return Number.isFinite(value) && value === qty;
}

async function setQuantity(quantity, context = "regular") {
  await waitForElement(["select#quantity", "select[name='quantity']", "select[id*='predefinedQuantitiesDropdown']", "input[id$='quantityTextInput']", ".quantity-text-input-with-label", "#add-to-cart-button", "#rcx-subscribe-submit-button"], 12000);
  const qty = Math.max(1, Math.round(Number(quantity) || 1));
  window.__nutricityLastQuantityIssue = null;
  if (qty === 1) return true;

  const selects = quantitySelects(context);
  for (const select of selects) {
    showPanel("Nutricity fulfilment", `Setting ${context === "sns" ? "Subscribe & Save " : ""}quantity to ${qty}.`, null, null);
    const freeFormInput = quantityFreeFormInput(select, context);
    const freeFormSet = freeFormInput ? await setFreeFormQuantity(select, qty, context) : false;
    const selected = freeFormSet || await chooseAmazonDropdownOption(select, qty) || await setNativeSelectQuantity(select, qty);
    if (!selected) continue;
    await sleep(900);
    const issue = quantityAvailabilityIssue(context, qty);
    if (issue) {
      issue.context = context;
      window.__nutricityLastQuantityIssue = issue;
      return false;
    }
    return true;
  }
  for (const input of quantityTextInputs(context)) {
    const typed = await setDirectQuantityInput(input, qty, context);
    if (!typed) continue;
    const issue = quantityAvailabilityIssue(context, qty);
    if (issue) {
      issue.context = context;
      window.__nutricityLastQuantityIssue = issue;
      return false;
    }
    return true;
  }
  const issue = quantityAvailabilityIssue(context, qty);
  if (issue) {
    issue.context = context;
    window.__nutricityLastQuantityIssue = issue;
  }
  return false;
}

async function navigateToNext(activeJob) {
  const nextIndex = activeJob.itemIndex + 1;
  if (nextIndex < activeJob.job.items.length) {
    activeJob.itemIndex = nextIndex;
    activeJob.stage = "product";
    await setActiveJob(activeJob);
    location.href = `https://www.amazon.com/dp/${activeJob.job.items[nextIndex].asin}`;
    return;
  }
  activeJob.stage = "cart";
  await setActiveJob(activeJob);
  location.href = "https://www.amazon.com/cart?ref_=sw_gtc";
}

async function handleClearCart(activeJob) {
  if (!canClearCart(activeJob)) {
    activeJob.stage = "cart";
    activeJob.cartCleared = true;
    await setActiveJob(activeJob);
    showPanel("Nutricity fulfilment", "Cart clear is no longer safe at this stage. Continuing with cart check.", null, null);
    return;
  }
  await waitForElement(["#sc-active-cart", "input[name='proceedToRetailCheckout']", "#sc-buy-box-ptc-button input"], 15000);
  showPanel("Nutricity fulfilment", "Clearing the existing Amazon cart before starting this order.", null, null);
  await waitForElement("#sc-active-cart", 15000);
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const buttons = cartDeleteButtons();
    if (!buttons.length) break;
    showPanel("Nutricity fulfilment", `Clearing Amazon cart item ${attempt + 1}. ${buttons.length} active delete link(s) found.`, null, null);
    await clickElement(buttons[0], "cart delete button");
    await waitForStableDom(800, 6000);
  }

  activeJob.stage = "product";
  activeJob.itemIndex = 0;
  activeJob.cartCleared = true;
  await setActiveJob(activeJob);
  const first = activeJob.job.items[0];
  location.href = `https://www.amazon.com/dp/${first.asin}`;
}

async function handleProduct(activeJob) {
  const item = activeJob.job.items[activeJob.itemIndex];
  if (!item) return;
  const expectedItem = selectedVariantItem(activeJob, item);
  const pageError = amazonProductPageErrorMessage();
  if (pageError) {
    await failCurrentItemAsMissing(activeJob, item, expectedItem, `ASIN ${expectedItem.asin} is missing or unavailable on Amazon. ${pageError}`);
    return;
  }
  const productReady = await waitForElement([
    "#productTitle",
    "#add-to-cart-button",
    "input[name='submit.add-to-cart']",
    "#rcx-subscribe-submit-button",
    "#corePrice_feature_div .a-price",
    "#apex_desktop .a-price",
  ], 18000);
  const delayedPageError = amazonProductPageErrorMessage();
  if (!productReady || delayedPageError) {
    await failCurrentItemAsMissing(
      activeJob,
      item,
      expectedItem,
      `ASIN ${expectedItem.asin} is missing or unavailable on Amazon. ${delayedPageError || "Amazon did not load product controls for this ASIN."}`,
    );
    return;
  }
  const asin = currentAsinFromUrl();
  showPanel("Nutricity fulfilment", `Adding ${expectedItem.asin} for ${recipientName(activeJob)}.`, null, null);
  if (asin && asin !== expectedItem.asin) {
    location.href = `https://www.amazon.com/dp/${expectedItem.asin}`;
    return;
  }
  rememberProductDosage(activeJob, item);
  await setActiveJob(activeJob);

  const unavailable = unavailableMessage();
  if (unavailable) {
    await failCurrentItemAsMissing(activeJob, item, expectedItem, `ASIN ${expectedItem.asin} is unavailable on Amazon. ${unavailable}`);
    return;
  }

  const savingsApplied = await applyAdditionalSavings("regular");
  if (savingsApplied) {
    await waitForStableDom(900, 6000);
  }
  const promoCount = markPromotions();
  if (promoCount && !activeJob.promoAcknowledged[item.asin]) {
    activeJob.promoAcknowledged[item.asin] = true;
    await setActiveJob(activeJob);
    showPanel("Nutricity fulfilment", "Coupon or promotion detected. Continuing price comparison.", null, null);
    await sleep(900);
  }

  const priceSnapshot = productPriceSnapshot();
  const multiItemJob = (activeJob.job?.items || []).length > 1;
  const priceForDecision = multiItemJob ? (priceSnapshot.regular || priceSnapshot.best) : priceSnapshot.best;
  const switchedVariant = await selectCheapestCountVariant(activeJob, item, priceForDecision);
  if (switchedVariant) return;
  const purchaseItem = selectedVariantItem(activeJob, item);
  const selectionNote = variantSelectionNote(item, purchaseItem);
  if (selectionNote) {
    showPanel("Cheaper variant found", `${selectionNote} Proceeding with this option.`, null, null);
    await sleep(1800);
  }
  await recordAmazonPrice(activeJob, item, priceForDecision, !multiItemJob && priceSnapshot.sns && priceSnapshot.sns === priceForDecision ? "subscribe-save" : "product", purchaseItem);
  const quantity = Number(purchaseItem.quantity || 1);
  const storeTotal = Number(item.store_total_price || Number(item.store_unit_price || 0) * Number(item.quantity || 1) || 0);
  const amazonTotal = Number(priceForDecision || 0) * quantity;
  if (!item.cost_approved && storeTotal > 0 && amazonTotal > storeTotal) {
    await send({
      type: "COSTLY_JOB",
      message: `ASIN ${purchaseItem.asin} costs $${amazonTotal.toFixed(2)} on Amazon but store sale value is $${storeTotal.toFixed(2)}. Approval required before fulfilment.`,
      costlyAsin: item.asin,
      costlyLineId: itemPrimaryLineId(item),
      storeTotalPrice: storeTotal,
      amazonTotalPrice: amazonTotal,
    });
    showPanel("Costly fulfilment review", "Amazon cost is higher than store sale value. Order moved to Costly page.", null, null);
    return;
  }

  const subscribed = !multiItemJob && await applySubscribeAndSaveIfCheaper(purchaseItem.quantity, activeJob);
  if (!subscribed) {
    if (!multiItemJob && priceSnapshot.sns && priceSnapshot.regular && priceSnapshot.sns < priceSnapshot.regular) {
      throw new Error(`Subscribe & Save is cheaper (${moneyText(priceSnapshot.sns)} vs ${moneyText(priceSnapshot.regular)}), but the extension did not complete the Subscribe & Save selection. Fulfilment paused before changing regular quantity.`);
    }
    const quantitySet = await setQuantity(purchaseItem.quantity, "regular");
    if (!quantitySet) {
      const requestedQuantity = Math.max(1, Math.round(Number(purchaseItem.quantity || 1)));
      const quantityIssue = window.__nutricityLastQuantityIssue || {};
      const availableQuantity = quantityIssue.availableQuantity || maxPredefinedQuantity("regular") || maxSelectableQuantity("regular");
      const lessQuantity = availableQuantity > 0 && availableQuantity < requestedQuantity;
      const issueMessage = quantityIssue.message ? ` ${quantityIssue.message}` : "";
      const message = lessQuantity
        ? `Less quantity available for ASIN ${purchaseItem.asin}. Customer ordered ${requestedQuantity}, Amazon only allows ${availableQuantity}.${issueMessage}`
        : `ASIN ${purchaseItem.asin} is missing or unavailable. Could not set quantity ${purchaseItem.quantity || 1}.${issueMessage}`;
      if (await continueAfterPartialMissing(activeJob, item, purchaseItem, message, "partial_quantity", {
        requestedQuantity,
        fulfilledQuantity: availableQuantity || null,
        availableQuantity: availableQuantity || null,
      })) {
        return;
      }
      showPanel("Sending to Missing ASINs", message, null, null);
      await send({
        type: "FAIL_JOB",
        message,
        missingAsin: item.asin,
        missingLineId: itemPrimaryLineId(item),
        failureCode: "partial_quantity",
        requestedQuantity,
        fulfilledQuantity: availableQuantity || null,
        availableQuantity: availableQuantity || null,
      });
      showPanel("Missing ASINs", `${message} Order moved to Missing ASINs.`, null, null);
      return;
    }
    const addButton = await waitForElement(["#add-to-cart-button", "input[name='submit.add-to-cart']", "#buybox-add-to-cart-button input"], 18000);
    activeJob.stage = "add_clicked";
    activeJob.addClickedAt = Date.now();
    markItemAdded(activeJob);
    await setActiveJob(activeJob);
    const added = await clickElement(addButton, "Add to cart button");
    if (!added) {
      const message = `ASIN ${purchaseItem.asin} is missing or unavailable. Could not find Add to cart button.`;
      showPanel("Sending to Missing ASINs", message, null, null);
      await send({ type: "FAIL_JOB", message, missingAsin: item.asin, missingLineId: itemPrimaryLineId(item) });
      showPanel("Missing ASINs", `${message} Job marked as error.`, null, null);
      return;
    }
  }

  showPanel("Nutricity fulfilment", `Add clicked for ${purchaseItem.asin}. Waiting for Amazon before moving on.`, null, null);
}

async function handleAddClicked(activeJob) {
  const clickedAt = Number(activeJob.addClickedAt || 0);
  const waitMs = Math.max(0, 4500 - (Date.now() - clickedAt));
  if (waitMs) {
    showPanel("Nutricity fulfilment", "Amazon add was clicked. Waiting before moving to the next step.", null, null);
    await sleep(waitMs);
  }
  await navigateToNext(activeJob);
}

async function handleSubscribeCheckout(activeJob) {
  markCheckoutStarted(activeJob);
  activeJob.subscribeAndSave = true;
  if (/\/checkout/i.test(location.pathname) || document.querySelector("#placeOrder, input.place-your-order-button, [data-checkout-view-modal], #checkout-primary-continue-button-id, input[aria-label='Full name']")) {
    activeJob.stage = "checkout";
    await setActiveJob(activeJob);
    await handleCheckout(activeJob);
    return;
  }
  showPanel("Nutricity fulfilment", "Subscribe & Save clicked. Waiting for checkout.", null, null);
  await sleep(2500);
  const latest = await getActiveJob();
  if (latest?.stage === "subscribe_checkout" && !/\/checkout/i.test(location.pathname)) {
    latest.stage = "checkout";
    await setActiveJob(latest);
  }
}

async function handleCart(activeJob) {
  await waitForElement(["#sc-active-cart", "input[name='proceedToRetailCheckout']", "#sc-buy-box-ptc-button input"], 15000);
  if (activeJob.subscribeAndSave || activeJob.stage === "subscribe_checkout") {
    activeJob.stage = "checkout";
    await setActiveJob(activeJob);
    showPanel("Nutricity fulfilment", "Subscribe & Save is already in checkout flow. Not clearing cart.", null, null);
    return;
  }
  if (activeJob.stage === "clear_cart") {
    if (!canClearCart(activeJob)) {
      activeJob.stage = "cart";
      activeJob.cartCleared = true;
      await setActiveJob(activeJob);
    } else {
      await handleClearCart(activeJob);
      return;
    }
  }
  const cartCheck = verifyCartQuantities(activeJob);
  if (!cartCheck.ok) {
    const mismatch = (cartCheck.mismatches || [])[0];
    const missingAsin = mismatch?.asin || "";
    const message = mismatch
      ? `Could not add the desired quantity for ASIN ${missingAsin}. Customer ordered ${mismatch.expected}, Amazon cart has ${mismatch.actual}.`
      : `Could not verify the desired Amazon cart quantities. ${cartCheck.message}`;
    const missingItem = (activeJob.job?.items || []).find((entry) => String(entry.asin || "").toUpperCase() === String(missingAsin || "").toUpperCase());
    if (missingItem && (activeJob.job?.items || []).length > 1) {
      showPanel("Sending line to Missing ASINs", message, null, null);
      const partialResult = await send({
        type: "MARK_LINE_MISSING",
        message,
        missingAsin,
        missingLineId: itemPrimaryLineId(missingItem),
        failureCode: "partial_quantity",
        requestedQuantity: mismatch?.expected ?? null,
        fulfilledQuantity: mismatch?.actual ?? 0,
        availableQuantity: mismatch?.actual ?? 0,
      });
      if (partialResult?.ok) {
        removeMissingItemFromActiveJob(activeJob, missingItem, selectedVariantItem(activeJob, missingItem));
        activeJob.stage = "cart";
        await setActiveJob(activeJob);
        showPanel("Split fulfilment", `${message} ${partialResult.message || ""} Proceeding to checkout for remaining Amazon item(s).`, null, null);
        return;
      }
    }
    showPanel("Sending to Missing ASINs", message, null, null);
    await send({
      type: "FAIL_JOB",
      message,
      missingAsin,
      missingLineId: lineIdForAsin(activeJob, missingAsin),
      failureCode: "partial_quantity",
      requestedQuantity: mismatch?.expected ?? null,
      fulfilledQuantity: mismatch?.actual ?? 0,
      availableQuantity: mismatch?.actual ?? 0,
    });
    showPanel("Missing ASINs", `${message} Order moved to Missing ASINs.`, null, null);
    return;
  }
  if (cartCheck.warning) {
    showPanel("Nutricity fulfilment", cartCheck.warning, null, null);
    await sleep(1800);
  }
  showPanel("Nutricity fulfilment", "Cart ready. Proceeding to checkout.", null, null);
  let clicked = false;
  const checkoutInput = await waitForElement([
    "input[name='proceedToRetailCheckout']",
    "#sc-buy-box-ptc-button input",
    "input[data-feature-id='proceed-to-checkout-action']",
  ], 18000);
  if (checkoutInput) {
    markCheckoutStarted(activeJob);
    activeJob.stage = "checkout";
    await setActiveJob(activeJob);
    clicked = await clickElement(checkoutInput, "Proceed to checkout button");
  }
  if (!clicked) {
    const button = findButtonByText(["proceed to checkout", "check out amazon cart"]);
    if (button) {
      markCheckoutStarted(activeJob);
      activeJob.stage = "checkout";
      await setActiveJob(activeJob);
      clicked = await clickElement(button, "Proceed to checkout button");
    }
  } else {
    markCheckoutStarted(activeJob);
    activeJob.stage = "checkout";
    await setActiveJob(activeJob);
  }
  if (!clicked) return;
}

async function fillFullName(name) {
  const input = await waitUntil(() => (
    document.querySelector("input#address-ui-widgets-enterAddressFullName[name='address-ui-widgets-enterAddressFullName']")
    || findAddressNameInput()
  ), 20000);
  if (!input) return false;
  const desired = String(name || "").replace(/\s+/g, " ").trim();
  if (!desired) return false;
  input.scrollIntoView({ block: "center", behavior: "smooth" });
  await sleep(500);
  await setInputValue(input, desired);
  await sleep(800);
  const saved = input.value.replace(/\s+/g, " ").trim() === desired;
  input.blur();
  return saved;
}

async function setInputValue(input, value) {
  const desired = String(value ?? "");
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  input.focus();
  input.click?.();
  await sleep(100);
  input.select?.();
  input.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "a", code: "KeyA", ctrlKey: true, metaKey: true }));
  input.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true, key: "a", code: "KeyA", ctrlKey: true, metaKey: true }));
  await sleep(50);
  try {
    document.execCommand("insertText", false, desired);
    await sleep(150);
    if (input.value === desired) {
      input.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
      input.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, composed: true, key: "Tab" }));
      input.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true, composed: true, key: "Tab" }));
      return;
    }
  } catch {
    // Fall back to manual value/input events below.
  }
  input.select?.();
  setter ? setter.call(input, "") : (input.value = "");
  input.dispatchEvent(new InputEvent("beforeinput", { bubbles: true, composed: true, inputType: "deleteContentBackward", data: null }));
  input.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, inputType: "deleteContentBackward", data: null }));
  await sleep(50);
  for (const char of desired) {
    const key = char === " " ? " " : char;
    input.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, composed: true, key }));
    input.dispatchEvent(new KeyboardEvent("keypress", { bubbles: true, composed: true, key }));
    const nextValue = `${input.value || ""}${char}`;
    setter ? setter.call(input, nextValue) : (input.value = nextValue);
    input.dispatchEvent(new InputEvent("beforeinput", { bubbles: true, composed: true, inputType: "insertText", data: char }));
    input.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, inputType: "insertText", data: char }));
    input.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true, composed: true, key }));
    await sleep(18);
  }
  input.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  input.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, composed: true, key: "Tab" }));
  input.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true, composed: true, key: "Tab" }));
}

function addressDataForRow(row) {
  const carrier = row?.closest?.("[data-action='select_address_in_list'][data-select_address_in_list]") || row?.querySelector?.("[data-action='select_address_in_list'][data-select_address_in_list]");
  if (!carrier) return {};
  try {
    return JSON.parse(carrier.getAttribute("data-select_address_in_list") || "{}");
  } catch {
    return {};
  }
}

function rowIsNutricityAddress(row) {
  if (!row) return false;
  const data = addressDataForRow(row);
  const dataName = normalizedText(data.fullName);
  const visibleName = normalizedText(row.querySelector?.("[data-testid='address-row-radio-name']")?.textContent || "");
  const rowText = normalizedText(row.innerText || row.textContent || "");
  return dataName.includes("nutricity") || visibleName.includes("nutricity") || rowText.includes("nutricity");
}

function nutricityAddressRow() {
  const rows = [...document.querySelectorAll(
    [
      "[data-testid='address-row-radio']",
      "[data-testid='address-row-section']",
      "[data-testid^='address-row-'][id^='address-row-']",
      ".address-row-section",
      ".address-row",
    ].join(", "),
  )].filter(visible);
  return rows.find(rowIsNutricityAddress) || null;
}

function addressRowForRecipient(name) {
  const wanted = normalizedText(name);
  if (!wanted) return null;
  const rows = [...document.querySelectorAll(
    [
      "[data-testid='address-row-radio']",
      "[data-testid='address-row-section']",
      "[data-testid^='address-row-'][id^='address-row-']",
      ".address-row-section",
      ".address-row",
      "[data-action='select_address_in_list']",
    ].join(", "),
  )].filter(visible);
  return rows.find((row) => normalizedText(row.innerText || row.textContent).includes(wanted)) || null;
}

function addressSelectionControl(row) {
  if (!row) return null;
  const label = row.closest?.("label");
  const radio = row.querySelector("input[type='radio'][name='addressID']")
    || label?.querySelector?.("input[type='radio'][name='addressID']")
    || row.closest?.("[data-testid='address-row-radio']")?.querySelector?.("input[type='radio'][name='addressID']");
  return radio?.closest?.("label") || radio || row;
}

function nutricityAddressSelectionControl() {
  return addressSelectionControl(nutricityAddressRow());
}

function recipientAddressSelectionControl(name) {
  return addressSelectionControl(addressRowForRecipient(name));
}

function addressSelectionControlForElement(element) {
  const row = element?.closest?.(
    "[data-testid='address-row-radio'], [data-testid='address-row-section'], [data-testid^='address-row-'], .address-row-section, .address-row, [data-action='select_address_in_list']",
  );
  if (!row) return null;
  const label = row.closest?.("label");
  const radio = row.querySelector("input[type='radio'][name='addressID']")
    || label?.querySelector?.("input[type='radio'][name='addressID']")
    || row.closest?.("[data-testid='address-row-radio']")?.querySelector?.("input[type='radio'][name='addressID']");
  return radio?.checked ? null : radio?.closest?.("label") || radio || null;
}

async function selectAddressRadioForElement(element) {
  const control = addressSelectionControlForElement(element);
  if (!control) return false;
  showPanel("Nutricity checkout", "Selecting delivery address row.", null, null);
  await clickElement(control, "Address radio button");
  await sleep(700);
  return true;
}

function findEditAddressTrigger() {
  const triggers = [...document.querySelectorAll("[id^='declarativeAction-'][data-action='checkout-view-modal']")].filter((element) => {
    const modalData = element.getAttribute("data-checkout-view-modal") || "";
    const text = (element.innerText || element.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
    return visible(element) && (modalData.includes("editAddressModal") || text.includes("edit address"));
  });
  const nutricityRowTrigger = triggers.find((element) => {
    const row = element.closest("[data-testid='address-row-radio'], [data-testid='address-row-section'], [data-testid^='address-row-'], .address-row-section, .address-row, [data-action='select_address_in_list']");
    return rowIsNutricityAddress(row);
  });
  if (nutricityRowTrigger) return nutricityRowTrigger.querySelector("a, button, span") || nutricityRowTrigger;

  const preferred = triggers.find((element) => {
    const row = element.closest("[data-testid='address-row-radio'], [data-testid='address-row-section'], [data-testid^='address-row-'], .address-row-section, .address-row, [data-action='select_address_in_list']");
    return rowIsNutricityAddress(row);
  });
  const selected = preferred || triggers[1] || triggers[0];
  if (selected) return selected.querySelector("a, button, span") || selected;

  const modalTriggers = [...document.querySelectorAll("[data-checkout-view-modal]")].filter((element) => {
    const modalData = element.getAttribute("data-checkout-view-modal") || "";
    return visible(element) && modalData.includes("editAddressModal");
  });
  const modalTrigger = modalTriggers[1] || modalTriggers[0];
  if (modalTrigger) return modalTrigger.querySelector("a, button, span") || modalTrigger;

  const xpath = "//*[starts-with(@id,'declarativeAction-') and .//*[normalize-space()='Edit address']]";
  const xpathNode = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
  if (xpathNode && visible(xpathNode)) return xpathNode.querySelector?.("a, button, span") || xpathNode;

  return [...document.querySelectorAll("a, button, span")].find((element) => {
    const text = (element.innerText || element.textContent || "").trim().toLowerCase();
    return visible(element) && text === "edit address";
  });
}

function normalizedText(value) {
  return String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
}

function checkoutShowsRecipient(name) {
  const wanted = normalizedText(name);
  if (!wanted) return false;
  const roots = [
    ...document.querySelectorAll(
      "[data-testid^='address-row-'], .address-row, [data-action='select_address_in_list'], [data-checkout-view-modal], #checkoutDisplayPage, #spc-orders, body",
    ),
  ];
  for (const root of roots) {
    if (!root || root.closest?.("#nutricity-panel") || root.closest?.(".a-popover, .a-popover-preload")) continue;
    const text = normalizedText(root.innerText || root.textContent);
    if (text.includes(wanted)) return true;
  }
  return false;
}

function checkoutDeliveryRecipientText() {
  const direct = document.querySelector("#deliver-to-customer-text");
  const candidates = [
    direct,
    ...document.querySelectorAll("h2, [id*='deliver-to'][id*='customer'], a[aria-label='Change delivery address']"),
  ].filter(Boolean);
  for (const element of candidates) {
    if (!visible(element)) continue;
    const text = (element.innerText || element.textContent || "").replace(/\s+/g, " ").trim();
    if (/^delivering\s+to\s+/i.test(text)) return text.replace(/^delivering\s+to\s+/i, "").trim();
  }
  return "";
}

function checkoutDeliveryRecipientMatches(name) {
  const deliveredTo = normalizedText(checkoutDeliveryRecipientText());
  const wanted = normalizedText(name);
  return Boolean(deliveredTo && wanted && deliveredTo === wanted);
}

function findChangeDeliveryAddressButton() {
  return [...document.querySelectorAll(
    [
      "a[data-csa-c-slot-id='checkout-change-shipaddressselect']",
      "a[data-topage='shipaddressselect']",
      "a[aria-label='Change delivery address']",
      "a#change-delivery-link",
      "a.expand-panel-button",
      "a",
      "button",
    ].join(", "),
  )].find((element) => {
    const href = String(element.getAttribute?.("href") || element.href || "").toLowerCase();
    const text = normalizedText(element.getAttribute?.("aria-label") || element.innerText || element.textContent);
    return visible(element) && !element.disabled && (
      element.getAttribute?.("data-csa-c-slot-id") === "checkout-change-shipaddressselect" ||
      element.getAttribute?.("data-topage") === "shipaddressselect" ||
      text === "change delivery address" ||
      text === "change" && href.includes("/checkout/") && href.includes("/address")
    );
  });
}

function findAddressNameInput() {
  return [...document.querySelectorAll(
    [
      "#address-ui-widgets-enterAddressFullName",
      "input[name='address-ui-widgets-enterAddressFullName']",
      "input[aria-label='Full name']",
      "input[aria-label='Full Name']",
      "input[placeholder='Full name']",
      "input[placeholder='Full Name']",
      "input[autocomplete='name']",
      "input[name*='FullName']",
      "input[id*='FullName']",
      "input[name*='fullName']",
      "input[id*='fullName']",
      "input[name*='Name']",
      "input[id*='Name']",
    ].join(", "),
  )].find((element) => visible(element) && !element.disabled);
}

function findAddressField(selectors) {
  return [...document.querySelectorAll(selectors.join(", "))].find((element) => visible(element) && !element.disabled);
}

async function fillAddressInput(selectors, value, required = true) {
  const input = await waitUntil(() => findAddressField(selectors), 8000, 300);
  if (!input) return !required;
  input.scrollIntoView({ block: "center", behavior: "smooth" });
  await sleep(200);
  await setInputValue(input, value);
  await sleep(150);
  input.blur();
  return String(input.value || "").trim() === String(value || "").trim();
}

async function setAddressSelect(selectors, value, required = true) {
  const select = await waitUntil(() => findAddressField(selectors), 8000, 300);
  if (!select) return !required;
  select.scrollIntoView({ block: "center", behavior: "smooth" });
  await sleep(200);
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set;
  setter ? setter.call(select, value) : (select.value = value);
  select.dispatchEvent(new Event("input", { bubbles: true }));
  select.dispatchEvent(new Event("change", { bubbles: true }));
  await sleep(250);
  return select.value === value;
}

async function fillNewDeliveryAddress(checkoutRecipient) {
  const address = DEFAULT_NEW_DELIVERY_ADDRESS;
  const steps = [
    () => setAddressSelect([
      "#address-ui-widgets-countryCode-dropdown-nativeId",
      "select[name='address-ui-widgets-countryCode']",
    ], address.countryCode),
    () => fillAddressInput([
      "#address-ui-widgets-enterAddressFullName",
      "input[name='address-ui-widgets-enterAddressFullName']",
      "input[autocomplete='name']",
    ], checkoutRecipient),
    () => fillAddressInput([
      "#address-ui-widgets-enterAddressPhoneNumber",
      "input[name='address-ui-widgets-enterAddressPhoneNumber']",
      "input[autocomplete='tel']",
    ], address.phoneNumber),
    () => fillAddressInput([
      "#address-ui-widgets-enterAddressLine1",
      "input[name='address-ui-widgets-enterAddressLine1']",
    ], address.addressLine1),
    () => fillAddressInput([
      "#address-ui-widgets-enterAddressLine2",
      "input[name='address-ui-widgets-enterAddressLine2']",
    ], address.addressLine2, false),
    () => fillAddressInput([
      "#address-ui-widgets-enterAddressCity",
      "input[name='address-ui-widgets-enterAddressCity']",
    ], address.city),
    () => setAddressSelect([
      "#address-ui-widgets-enterAddressStateOrRegion-dropdown-nativeId",
      "select[name='address-ui-widgets-enterAddressStateOrRegion']",
    ], address.stateOrRegion),
    () => fillAddressInput([
      "#address-ui-widgets-enterAddressPostalCode",
      "input[name='address-ui-widgets-enterAddressPostalCode']",
    ], address.postalCode),
  ];
  for (const step of steps) {
    if (!await step()) return false;
  }
  return true;
}

async function waitUntil(check, timeoutMs = 12000, intervalMs = 300) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    await waitIfPaused();
    const result = check();
    if (result) return result;
    await sleep(intervalMs);
  }
  return null;
}

function findUseAddressButton() {
  const addressEditorOpen = Boolean(findAddressNameInput());
  const selectors = [
    "input[data-csa-c-slot-id='address-ui-widgets-continue-edit-address-btn-bottom'][data-testid='bottom-continue-button']",
    "input[data-csa-c-slot-id*='continue-edit-address']",
    "button[data-csa-c-slot-id*='continue-edit-address']",
    "input[data-testid='bottom-continue-button']",
    "button[data-testid='bottom-continue-button']",
    "input[data-testid*='continue-button']",
    "button[data-testid*='continue-button']",
    "input[aria-labelledby='checkout-primary-continue-button-id-announce']",
    "#checkout-primary-continue-button-id input",
  ];
  if (addressEditorOpen) {
    selectors.push(
      ".a-popover input[type='submit']",
      ".a-popover button[type='submit']",
      ".a-modal-scroller input[type='submit']",
      ".a-modal-scroller button[type='submit']",
    );
  }
  const direct = [...document.querySelectorAll(selectors.join(", "))].find((element) => visible(element) && !element.disabled);
  if (direct) return direct;

  return [...document.querySelectorAll("input[type='submit'], button, span.a-button")].find((element) => {
    const text = normalizedText(element.value || element.innerText || element.textContent);
    return visible(element) && [
      "use this address",
      "save address",
      "continue to checkout",
      "continue",
    ].some((needle) => text.includes(needle));
  });
}

async function clickUseAddressButton(useAddress) {
  const target = document.querySelector("#checkout-primary-continue-button-id-announce")
    || useAddress?.closest?.("[data-action='checkout-continue-button-click-action']")?.querySelector?.("#checkout-primary-continue-button-id-announce, .a-button-text")
    || useAddress?.closest?.("#checkout-primary-continue-button-id")?.querySelector?.("#checkout-primary-continue-button-id-announce, .a-button-text")
    || useAddress?.closest?.("span.a-button")?.querySelector?.(".a-button-text")
    || useAddress;
  return clickElement(target, "Use this address button");
}

function findDeliverToThisAddressButton() {
  if (findAddressNameInput()) return null;
  const direct = [...document.querySelectorAll(
    "input[data-testid='ab-select-address-continue-button-bottom'], #ab-select-address-continue-button-bottom input, input[aria-labelledby='ab-select-address-continue-button-bottom-announce']",
  )].find((element) => visible(element) && !element.disabled);
  return direct || findButtonByText(["deliver to this address", "use this address"]);
}

function findPaymentRadio() {
  return [...document.querySelectorAll("input[type='radio'][name='ppw-instrumentRowSelection']")]
    .find((element) => visible(element) && !element.disabled);
}

function cardPreferenceList(value) {
  return String(value || "")
    .split(/[\s,;|]+/)
    .map((item) => item.replace(/\D/g, "").slice(-4))
    .filter((item) => item.length === 4);
}

function paymentRowForRadio(radio) {
  return radio?.closest?.(".a-box-inner, .a-fixed-left-grid, [data-pmts-component-id], .pmts-instrument-selector") || radio?.closest?.("label") || radio?.parentElement || radio;
}

function paymentRowText(radio) {
  const label = radio?.closest?.("label");
  const labelledText = label?.innerText || label?.textContent || "";
  const root = paymentRowForRadio(radio);
  return [labelledText, root?.innerText || root?.textContent || "", radio?.value || ""].join(" ");
}

function cardDigitsForPaymentRadio(radio) {
  const root = paymentRowForRadio(radio);
  const dataNumber = root?.querySelector?.("[data-number]")?.getAttribute("data-number");
  const text = paymentRowText(radio);
  const endingMatch = text.match(/ending\s+in\s+(\d{4})/i);
  return (dataNumber || endingMatch?.[1] || "").replace(/\D/g, "").slice(-4);
}

function findCheckoutPaymentPanel() {
  return [...document.querySelectorAll(
    "#checkout-paymentOptionPanel, #selected-payment-methods-list-container",
  )].find((element) => visible(element));
}

function checkoutSelectedPaymentText() {
  const panel = findCheckoutPaymentPanel();
  if (!panel) return "";
  const root = panel.closest?.("#checkout-paymentOptionPanel") || panel;
  const heading = root.querySelector?.(
    [
      "#selected-payment-methods-list-container h2",
      "#payment-option-text-default",
      "[id^='payment-option-text'][data-testid]",
      ".selected-payment-method-no-art-description-heading",
    ].join(", "),
  );
  return String(heading?.innerText || heading?.textContent || root.innerText || root.textContent || "").replace(/\s+/g, " ").trim();
}

function checkoutSelectedCardDigits() {
  const text = checkoutSelectedPaymentText();
  const preferredPattern = text.match(/(?:ending\s+in|visa|mastercard|american\s+express|amex|discover|card|paying\s+with)[^\d]{0,80}(\d{4})\b/i);
  if (preferredPattern) return preferredPattern[1];
  const fallback = text.match(/\b(\d{4})\b/);
  return fallback?.[1] || "";
}

function findPaymentRadioForPreferences(preferences = []) {
  const radios = [...document.querySelectorAll("input[type='radio'][name='ppw-instrumentRowSelection']")]
    .filter((element) => visible(element) && !element.disabled);
  if (!radios.length) return null;
  for (const preferred of preferences) {
    const radio = radios.find((candidate) => cardDigitsForPaymentRadio(candidate) === preferred);
    if (radio) return radio;
  }
  return radios.find((radio) => radio.checked) || radios[0];
}

function findPaymentSelection(preferences = []) {
  const radio = findPaymentRadioForPreferences(preferences);
  if (!radio) return null;

  const paymentRoot = paymentRowForRadio(radio)?.closest?.(
    "form, [data-testid*='pay'], [data-csa-c-slot-id*='payselect'], [data-pmts-component-id], .pmts-portal-component, body",
  ) || document;
  const exactContinue = [...document.querySelectorAll(
    [
      "input[data-csa-c-slot-id*='continue-payselect']",
      "button[data-csa-c-slot-id*='continue-payselect']",
      "input[data-testid='bottom-continue-button'][data-csa-c-slot-id*='payselect']",
      "input[data-testid='secondary-continue-button'][data-csa-c-slot-id*='payselect']",
      "button[data-testid='bottom-continue-button'][data-csa-c-slot-id*='payselect']",
      "button[data-testid='secondary-continue-button'][data-csa-c-slot-id*='payselect']",
      "input[name='ppw-widgetEvent:SetPaymentPlanSelectContinueEvent']",
      "input[name='ppw-widgetEvent:SetPaymentPlanContinueEvent']",
      "input[aria-labelledby*='continue']",
    ].join(", "),
  )]
    .find((element) => visible(element) && !element.disabled);
  if (exactContinue) return { radio, continueButton: exactContinue };

  const continueButton = [...paymentRoot.querySelectorAll("input[type='submit'], input[type='button'], button, span.a-button")].find((element) => {
    const text = normalizedText(element.value || element.innerText || element.textContent);
    return visible(element) && !element.disabled && (
      text.includes("use this payment method") ||
      text.includes("use this card") ||
      text === "continue" ||
      text.includes("continue")
    );
  });
  return radio && continueButton ? { radio, continueButton } : null;
}

function findChangePaymentButton() {
  return [...document.querySelectorAll(
    [
      "a[data-csa-c-slot-id='checkout-change-payselect']",
      "a[data-topage='payselect']",
      "a[href*='/checkout/'][href*='/pay']",
      "a[href*='ref_=chk_spc_chg_payselect']",
      "button",
      "a",
    ].join(", "),
  )].find((element) => {
    const href = String(element.getAttribute?.("href") || element.href || "").toLowerCase();
    const text = normalizedText(element.getAttribute?.("aria-label") || element.innerText || element.textContent);
    return visible(element) && !element.disabled && (
      element.getAttribute?.("data-csa-c-slot-id") === "checkout-change-payselect" ||
      element.getAttribute?.("data-topage") === "payselect" ||
      href.includes("/pay") && href.includes("checkout") ||
      text.includes("change payment method")
    );
  });
}

function findAddNewDeliveryAddressLink() {
  return [...document.querySelectorAll(
    [
      "#add-new-address-popover-link",
      "a[data-csa-c-slot-id='add-new-address-non-mobile-tango-sasp']",
      "a",
      "button",
      "span.a-button",
    ].join(", "),
  )].find((element) => {
    const text = normalizedText(element.getAttribute?.("aria-label") || element.innerText || element.textContent);
    return visible(element) && !element.disabled && text.includes("add a new delivery address");
  });
}

function findPlaceOrderButton() {
  const selectors = [
    "input#placeOrder",
    "input[name='placeYourOrder1']",
    "input[data-testid='SPC_selectPlaceOrder']",
    "input[data-csa-c-slot-id='checkout-place-your-order-button']",
    "input.place-your-order-button",
    "input[title='Place your order']",
    "input[value='Place your order']",
    "button",
    "span.a-button",
  ].join(", ");
  return [...document.querySelectorAll(selectors)].find((element) => {
    const labelledBy = element.getAttribute?.("aria-labelledby");
    const labelText = labelledBy ? document.getElementById(labelledBy)?.textContent : "";
    const text = normalizedText(element.value || element.title || element.innerText || element.textContent || labelText);
    return visible(element) && !element.disabled && (
      element.matches?.("input#placeOrder, input[name='placeYourOrder1'], input[data-testid='SPC_selectPlaceOrder'], input[data-csa-c-slot-id='checkout-place-your-order-button'], input.place-your-order-button") ||
      text.includes("place your order")
    );
  });
}

function findBusinessContinueCheckoutButton() {
  return [...document.querySelectorAll(
    "a[name='checkout-byg-ptc-button'], a[href*='/checkout/entry/cart/amazon_business'], a[href*='proceedToCheckout=1'], button, input[type='submit'], input[type='button'], span.a-button",
  )].find((element) => {
    const href = String(element.getAttribute?.("href") || element.href || "").toLowerCase();
    const text = normalizedText(element.value || element.innerText || element.textContent);
    return visible(element) && !element.disabled && (
      element.getAttribute?.("name") === "checkout-byg-ptc-button" ||
      href.includes("/checkout/entry/cart/amazon_business") ||
      (href.includes("proceedtocheckout=1") && text.includes("continue to checkout"))
    );
  });
}

async function handleBusinessCheckoutInterstitial() {
  const continueCheckout = findBusinessContinueCheckoutButton();
  if (!continueCheckout) return false;
  showPanel("Nutricity checkout", "Continuing through Amazon Business checkout.", null, null);
  await clickElement(continueCheckout, "Amazon Business continue to checkout button");
  await sleep(1500);
  return true;
}

async function handlePaymentSelection(activeJob) {
  try {
    const state = await getExtensionState();
    const cardPreferences = cardPreferenceList(state.cardLast4Preference);
    let payment = findPaymentSelection(cardPreferences);
    if (!payment) {
      if (!findPaymentRadio()) return false;
      payment = await waitUntil(() => findPaymentSelection(cardPreferences), 5000);
      if (!payment) {
        await pauseForManualCheckout(activeJob, "Amazon is asking for a payment method, but I could not find the payment Continue button.");
        return true;
      }
    }
    const selectedDigits = cardDigitsForPaymentRadio(payment.radio);
    showPanel("Nutricity checkout", selectedDigits ? `Selecting card ending in ${selectedDigits}.` : "Selecting Amazon payment method.", null, null);
    if (!payment.radio.checked) {
      const target = payment.radio.closest?.("label") || payment.radio;
      await clickElement(target, "Payment method radio");
      if (!payment.radio.checked) {
        payment.radio.checked = true;
        payment.radio.dispatchEvent(new Event("input", { bubbles: true }));
        payment.radio.dispatchEvent(new Event("change", { bubbles: true }));
        await sleep(800);
      }
    }
    if (cardPreferences.length && selectedDigits && !cardPreferences.includes(selectedDigits)) {
      await pauseForManualCheckout(activeJob, `Could not find preferred card ending in ${cardPreferences.join(" or ")}.`);
      return true;
    }
    await clickElement(payment.continueButton, "Use this payment method button");
    showPanel("Nutricity checkout", "Payment method selected. Waiting for checkout.", null, null);
    await sleep(2500);
    activeJob.stage = "checkout";
    await setActiveJob(activeJob);
    return true;
  } catch (error) {
    await pauseForManualCheckout(activeJob, `Payment selection got stuck: ${error.message || error}`);
    return true;
  }
}

async function openPaymentSelectionIfAvailable() {
  const changePayment = await waitUntil(findChangePaymentButton, 6000, 400);
  if (!changePayment) return false;
  showPanel("Nutricity checkout", "Opening payment method selection.", null, null);
  await clickElement(changePayment, "Change payment method button");
  await sleep(2000);
  return true;
}

async function ensurePreferredCheckoutPayment(activeJob) {
  const state = await getExtensionState();
  const cardPreferences = cardPreferenceList(state.cardLast4Preference);
  if (!cardPreferences.length) return true;
  const paymentPanel = findCheckoutPaymentPanel();
  if (!paymentPanel) return true;

  const selectedDigits = checkoutSelectedCardDigits();
  if (selectedDigits && cardPreferences.includes(selectedDigits)) {
    showPanel("Nutricity checkout", `Verified checkout card ending in ${selectedDigits}.`, null, null);
    return true;
  }

  const expected = cardPreferences.join(" or ");
  if (selectedDigits) {
    showPanel("Nutricity checkout", `Checkout shows card ending in ${selectedDigits}; switching to ${expected}.`, null, null);
  } else {
    showPanel("Nutricity checkout", `Could not verify checkout card; opening payment selection for ${expected}.`, null, null);
  }

  if (!await openPaymentSelectionIfAvailable()) {
    await pauseForManualCheckout(activeJob, selectedDigits
      ? `Checkout shows card ending in ${selectedDigits}, but I could not find the Change payment method link to switch to ${expected}.`
      : `Could not verify the checkout card, and I could not find the Change payment method link to select ${expected}.`);
    return false;
  }
  if (await waitUntil(findPaymentRadio, 10000, 400)) {
    await handlePaymentSelection(activeJob);
    return !activeJob.paused;
  }
  await pauseForManualCheckout(activeJob, `Opened payment selection, but Amazon did not show card choices for ${expected}.`);
  return false;
}

async function openAddressEditorIfAvailable(activeJob) {
  const directEditor = findAddressNameInput();
  if (directEditor) return directEditor;

  let editAddress = await waitUntil(findEditAddressTrigger, 6000, 400);
  if (!editAddress) {
    const changeAddress = findChangeDeliveryAddressButton();
    if (changeAddress) {
      showPanel("Nutricity checkout", "Opening delivery address selection.", null, null);
      activeJob.stage = "editing_address";
      activeJob.editAddressClickedAt = Date.now();
      await setActiveJob(activeJob);
      await clickElement(changeAddress, "Change delivery address link");
      await sleep(2000);
      editAddress = await waitUntil(findEditAddressTrigger, 10000, 400);
      if (!editAddress && findAddressNameInput()) return findAddressNameInput();
    }
  }

  if (!editAddress) return null;
  await selectAddressRadioForElement(editAddress);
  showPanel("Nutricity checkout", "Opening Amazon address editor.", null, null);
  activeJob.stage = "editing_address";
  activeJob.editAddressClickedAt = Date.now();
  await setActiveJob(activeJob);
  await clickElement(editAddress, "Edit address link");
  await waitForElement([
    "#address-ui-widgets-enterAddressFullName",
    "input[name='address-ui-widgets-enterAddressFullName']",
    "input[aria-label='Full name']",
    "input[name*='FullName']",
    "input[id*='FullName']",
  ], 8000);
  return findAddressNameInput();
}

async function openNewDeliveryAddressFormIfAvailable(activeJob) {
  const directEditor = findAddressNameInput();
  if (directEditor) return directEditor;

  let addNewAddress = findAddNewDeliveryAddressLink();
  if (!addNewAddress) {
    const changeAddress = findChangeDeliveryAddressButton();
    if (changeAddress) {
      showPanel("Nutricity checkout", "Opening delivery address selection.", null, null);
      activeJob.stage = "editing_address";
      activeJob.editAddressClickedAt = Date.now();
      await setActiveJob(activeJob);
      await clickElement(changeAddress, "Change delivery address link");
      await sleep(2000);
      addNewAddress = await waitUntil(findAddNewDeliveryAddressLink, 10000, 400);
    }
  }
  if (!addNewAddress) return null;

  showPanel("Nutricity checkout", "Opening new delivery address form.", null, null);
  activeJob.stage = "editing_address";
  activeJob.editAddressClickedAt = Date.now();
  await setActiveJob(activeJob);
  await clickElement(addNewAddress, "Add a new delivery address link");
  await waitForElement([
    "#address-ui-widgets-enterAddressFullName",
    "input[name='address-ui-widgets-enterAddressFullName']",
    "#address-ui-widgets-enterAddressPhoneNumber",
    "#address-ui-widgets-enterAddressLine1",
  ], 10000);
  return findAddressNameInput();
}

function manualNextStage(activeJob, requestedStage = "") {
  if (requestedStage) return requestedStage;
  const stage = String(activeJob?.stage || "");
  if (stage === "clear_cart") return "product";
  if (stage === "product") return "add_clicked";
  if (stage === "add_clicked") return "navigate_next";
  if (stage === "subscribe_checkout") return "checkout";
  if (stage === "cart") return "checkout";
  if (stage === "editing_address") return "checkout";
  if (stage === "complete_pending") return "find_order_id";
  return stage || "product";
}

async function continueAfterManualStep(activeJob, nextStage = "") {
  const latest = await getActiveJob();
  const next = { ...(latest || activeJob), paused: false, pausedStage: null };
  const targetStage = manualNextStage(next, nextStage);
  if (targetStage === "navigate_next") {
    showPanel("Nutricity fulfilment", "Manual step done. Moving to the next item.", null, null);
    await navigateToNext(next);
    setTimeout(runSafely, 250);
    return;
  }
  if (targetStage === "product") {
    next.itemIndex = Number(next.itemIndex || 0);
    next.cartCleared = true;
  }
  if (targetStage === "add_clicked") {
    next.addClickedAt = Date.now();
    markItemAdded(next);
  }
  next.stage = targetStage;
  await setActiveJob(next);
  showPanel("Nutricity fulfilment", `Manual step done. Continuing ${targetStage}.`, null, null);
  setTimeout(runSafely, 250);
}

async function pauseForManualCheckout(activeJob, message, nextStage = "checkout") {
  activeJob.pausedStage = activeJob.stage || "checkout";
  activeJob.stage = activeJob.pausedStage;
  activeJob.paused = true;
  await setActiveJob(activeJob);
  showPanel(
    "Nutricity checkout needs attention",
    `${message} Make the needed Amazon checkout change, then click Resume to retry this step.`,
    "I did it manually, continue",
    () => continueAfterManualStep(activeJob, nextStage),
  );
}

async function saveEditedAddress(activeJob, checkoutRecipient) {
  showPanel("Nutricity checkout", "Editing Amazon address name.", null, null);
  const filled = await fillFullName(checkoutRecipient);
  if (!filled) return false;

  showPanel("Nutricity checkout", "Saving Amazon address.", null, null);
  await sleep(1000);
  const useAddress = await waitUntil(findUseAddressButton, 8000) || findButtonByText(["use this address", "save address", "continue"]);
  if (!useAddress) {
    await pauseForManualCheckout(activeJob, "Could not find the Use this address button.");
    return false;
  }
  await clickUseAddressButton(useAddress);
  await sleep(3000);

  const recipientAddressControl = await waitUntil(() => !findAddressNameInput() && recipientAddressSelectionControl(checkoutRecipient), 10000, 300);
  if (recipientAddressControl) {
    showPanel("Nutricity checkout", "Selecting the recipient delivery address.", null, null);
    await clickElement(recipientAddressControl, "Recipient address row");
  }

  const deliverToThisAddress = await waitUntil(findDeliverToThisAddressButton, 5000, 300);
  if (deliverToThisAddress) {
    if (!recipientAddressControl && !checkoutShowsRecipient(checkoutRecipient)) {
      await pauseForManualCheckout(activeJob, `Could not find the edited address row for "${checkoutRecipient}" after saving.`);
      return false;
    }
    showPanel("Nutricity checkout", "Selecting edited delivery address.", null, null);
    await clickElement(deliverToThisAddress, "Deliver to this address button");
    await sleep(2000);
  }

  showPanel("Nutricity checkout", "Waiting for checkout to update.", null, null);
  await waitUntil(
    () => checkoutShowsRecipient(checkoutRecipient) || findPaymentSelection() || findPlaceOrderButton() || !findAddressNameInput(),
    12000,
  );
  activeJob.stage = "checkout";
  activeJob.editAddressClickedAt = null;
  activeJob.addressEditedRecipient = checkoutRecipient;
  activeJob.addressEditedAt = Date.now();
  await setActiveJob(activeJob);
  return true;
}

async function saveNewDeliveryAddress(activeJob, checkoutRecipient) {
  if (!findAddressNameInput() && !await openNewDeliveryAddressFormIfAvailable(activeJob)) {
    await pauseForManualCheckout(activeJob, "Could not find the Add a new delivery address link.");
    return false;
  }

  showPanel("Nutricity checkout", "Adding Amazon delivery address.", null, null);
  const filled = await fillNewDeliveryAddress(checkoutRecipient);
  if (!filled) {
    await pauseForManualCheckout(activeJob, "Could not fill the new delivery address form.");
    return false;
  }

  showPanel("Nutricity checkout", "Saving new Amazon address.", null, null);
  await sleep(1000);
  const useAddress = await waitUntil(findUseAddressButton, 8000) || findButtonByText(["use this address", "save address", "continue"]);
  if (!useAddress) {
    await pauseForManualCheckout(activeJob, "Could not find the Use this address button.");
    return false;
  }
  await clickUseAddressButton(useAddress);
  await sleep(3000);

  const deliverToThisAddress = await waitUntil(findDeliverToThisAddressButton, 5000, 300);
  if (deliverToThisAddress) {
    showPanel("Nutricity checkout", "Selecting new delivery address.", null, null);
    await clickElement(deliverToThisAddress, "Deliver to this address button");
    await sleep(2000);
  }

  await waitUntil(
    () => checkoutShowsRecipient(checkoutRecipient) || findPaymentSelection() || findPlaceOrderButton() || !findAddressNameInput(),
    12000,
  );
  activeJob.stage = "checkout";
  activeJob.editAddressClickedAt = null;
  activeJob.addressEditedRecipient = checkoutRecipient;
  activeJob.addressEditedAt = Date.now();
  activeJob.addressMode = "new";
  await setActiveJob(activeJob);
  return true;
}

async function verifyCheckoutDeliveryRecipient(activeJob, checkoutRecipient) {
  const deliveredTo = checkoutDeliveryRecipientText();
  if (!deliveredTo) return true;
  if (checkoutDeliveryRecipientMatches(checkoutRecipient)) {
    activeJob.addressVerifiedRecipient = checkoutRecipient;
    activeJob.addressVerifiedAt = Date.now();
    activeJob.addressVerifyAttempts = 0;
    await setActiveJob(activeJob);
    return true;
  }

  const attempts = Number(activeJob.addressVerifyAttempts || 0) + 1;
  activeJob.addressVerifyAttempts = attempts;
  activeJob.addressVerifiedRecipient = "";
  await setActiveJob(activeJob);
  if (attempts > 2) {
    await pauseForManualCheckout(
      activeJob,
      `Delivery name still shows "${deliveredTo}" instead of "${checkoutRecipient}".`,
    );
    return false;
  }

  const changeAddress = findChangeDeliveryAddressButton();
  if (!changeAddress) {
    await pauseForManualCheckout(
      activeJob,
      `Delivery name shows "${deliveredTo}" instead of "${checkoutRecipient}", and I could not find the Change delivery address link.`,
    );
    return false;
  }
  showPanel("Nutricity checkout", `Delivery name shows ${deliveredTo}. Reopening address edit.`, null, null);
  activeJob.stage = "editing_address";
  activeJob.editAddressClickedAt = Date.now();
  await setActiveJob(activeJob);
  await clickElement(changeAddress, "Change delivery address link");
  return false;
}

async function handleCheckoutLimitPurchase(activeJob) {
  const issue = checkoutLimitPurchaseIssue(activeJob);
  if (!issue) return false;
  const item = issue.item;
  const asin = item?.asin || "";
  const message = asin
    ? `${issue.message} ASIN ${asin}${issue.title ? ` (${issue.title})` : ""}.`
    : `${issue.message}${issue.title ? ` ${issue.title}.` : ""}`;

  if (item && (activeJob.job?.items || []).length > 1) {
    showPanel("Limit purchase", message, null, null);
    const result = await send({
      type: "MARK_LINE_MISSING",
      message,
      missingAsin: asin,
      missingLineId: itemPrimaryLineId(item),
      failureCode: "limit_purchase",
      requestedQuantity: item.quantity ?? null,
      fulfilledQuantity: 0,
      availableQuantity: 0,
    });
    if (!result?.ok) {
      await pauseForManualCheckout(activeJob, `${message} I could not report this line to the app.`);
      return true;
    }
    removeMissingItemFromActiveJob(activeJob, item, item);
    activeJob.stage = "checkout";
    await setActiveJob(activeJob);
    if (issue.removeButton) {
      await clickElement(issue.removeButton, "Remove limited purchase item");
      await sleep(1800);
    }
    const continueButton = await waitUntil(findUseAddressButton, 3000, 300)
      || findButtonByText(["continue"]);
    if (continueButton) {
      await clickElement(continueButton, "Continue after removing limited purchase item");
      await sleep(2500);
    }
    showPanel("Split fulfilment", `${message} Remaining item(s) will continue.`, null, null);
    return true;
  }

  showPanel("Limit purchase", message, null, null);
  await send({
    type: "FAIL_JOB",
    message,
    missingAsin: asin,
    missingLineId: item ? itemPrimaryLineId(item) : null,
    failureCode: "limit_purchase",
    requestedQuantity: item?.quantity ?? null,
    fulfilledQuantity: 0,
    availableQuantity: 0,
  });
  showPanel("Missing ASINs", `${message} Order moved to Missing ASINs.`, null, null);
  return true;
}

async function handleCheckout(activeJob) {
  await waitForElement([
    "#placeOrder",
    "input.place-your-order-button",
    "#checkout-javaItemSelectPanel",
    "[data-checkout-view-modal]",
    "#checkout-primary-continue-button-id",
    "input[aria-label='Full name']",
    "input[type='radio'][name='ppw-instrumentRowSelection']",
    "input[data-csa-c-slot-id*='continue-payselect']",
    "a[name='checkout-byg-ptc-button']",
    "a[href*='/checkout/entry/cart/amazon_business']",
    "a[href*='proceedToCheckout=1']",
  ], 18000);
  if (await handleBusinessCheckoutInterstitial()) return;
  if (await handleCheckoutLimitPurchase(activeJob)) return;
  const checkoutRecipient = recipientName(activeJob);
  const extensionState = await getExtensionState();
  const shouldEditExistingAddress = extensionState.editExistingAddress !== false;
  showPanel("Nutricity checkout", `Using recipient name: ${checkoutRecipient}`, null, null);
  if (activeJob.stage === "editing_address") {
    if (shouldEditExistingAddress && !findAddressNameInput()) {
      await openAddressEditorIfAvailable(activeJob);
    } else if (!shouldEditExistingAddress && !findAddressNameInput()) {
      await openNewDeliveryAddressFormIfAvailable(activeJob);
    }
    if (shouldEditExistingAddress ? await saveEditedAddress(activeJob, checkoutRecipient) : await saveNewDeliveryAddress(activeJob, checkoutRecipient)) {
      // Continue in this same pass so checkout can place the order without waiting for the next interval.
    } else if (Date.now() - Number(activeJob.editAddressClickedAt || 0) > 45000) {
      activeJob.stage = "checkout";
      activeJob.editAddressClickedAt = null;
      await setActiveJob(activeJob);
    } else {
      showPanel("Nutricity checkout", "Waiting for Amazon address editor.", null, null);
      return;
    }
  }
  const continueCheckout = findBusinessContinueCheckoutButton() || findButtonByText(["continue to checkout"]);
  if (continueCheckout) {
    await clickElement(continueCheckout, "Continue to checkout button");
    return;
  }

  let handledPayment = false;
  if (findPaymentRadio()) {
    handledPayment = await handlePaymentSelection(activeJob);
    if (handledPayment) {
      showPanel("Nutricity checkout", "Payment confirmed. Changing address after payment.", null, null);
    }
    if (activeJob.paused) return;
  }

  const addressEditIsFresh = activeJob.addressEditedRecipient === checkoutRecipient && Date.now() - Number(activeJob.addressEditedAt || 0) < 30000;
  if (!addressEditIsFresh) {
    if (shouldEditExistingAddress) {
      const addressRow = nutricityAddressRow();
      if (addressRow) {
        addressRow.scrollIntoView({ block: "center", behavior: "smooth" });
        await sleep(250);
      }
      const addressEditor = await openAddressEditorIfAvailable(activeJob);
      if (addressEditor) {
        if (await saveEditedAddress(activeJob, checkoutRecipient)) {
          // Continue below to place the order immediately when Amazon has returned to checkout.
        } else {
          return;
        }
      } else {
        await pauseForManualCheckout(activeJob, "Could not find the Change delivery address or Edit address link for the Nutricity address.");
        return;
      }
    } else {
      const addressEditor = await openNewDeliveryAddressFormIfAvailable(activeJob);
      if (!addressEditor) {
        await pauseForManualCheckout(activeJob, "Could not find the Add a new delivery address link.");
        return;
      }
      if (!await saveNewDeliveryAddress(activeJob, checkoutRecipient)) return;
    }
  } else {
    showPanel("Nutricity checkout", "Recipient address edit saved. Continuing checkout.", null, null);
  }

  if (!await verifyCheckoutDeliveryRecipient(activeJob, checkoutRecipient)) return;

  if (!handledPayment && !checkoutShowsRecipient(checkoutRecipient) && !findPlaceOrderButton() && await fillFullName(checkoutRecipient)) {
    await sleep(1200);
    const useAddress = await waitUntil(findUseAddressButton, 8000) || findButtonByText(["use this address", "save address", "deliver to this address", "continue"]);
    if (useAddress) {
      await sleep(1000);
      await clickUseAddressButton(useAddress);
      await sleep(3000);
      await waitUntil(() => checkoutShowsRecipient(checkoutRecipient) || findPaymentSelection() || findPlaceOrderButton(), 10000);
    } else {
      await pauseForManualCheckout(activeJob, "Could not find the Use this address button.");
      return;
    }
  }

  if (!handledPayment && findPaymentRadio() && await handlePaymentSelection(activeJob)) {
    handledPayment = true;
    // Continue below to place the order if Amazon exposed the final button.
  }
  if (activeJob.paused) return;

  if (!await verifyCheckoutDeliveryRecipient(activeJob, checkoutRecipient)) return;
  if (!await ensurePreferredCheckoutPayment(activeJob)) return;

  const placeOrder = await waitUntil(findPlaceOrderButton, 20000, 500)
    || await waitForElement([
      "input#placeOrder:not([disabled])",
      "input[name='placeYourOrder1']:not([disabled])",
      "input[data-testid='SPC_selectPlaceOrder']:not([disabled])",
      "input[data-csa-c-slot-id='checkout-place-your-order-button']:not([disabled])",
      "input.place-your-order-button:not([disabled])",
    ], 5000)
    || findButtonByText(["place your order"]);
  if (placeOrder && !placeOrder.disabled) {
    const duplicateCheck = await send({ type: "CHECK_EXISTING_AMAZON_ORDER" });
    if (!duplicateCheck?.ok) {
      activeJob.paused = true;
      activeJob.pausedStage = "checkout";
      await setActiveJob(activeJob);
      showPanel(
        "Duplicate check failed",
        duplicateCheck?.message || "Could not confirm whether an Amazon order already exists in the app. Fulfilment is paused before placing the order.",
        "Retry",
        () => continueAfterManualStep(activeJob, "checkout"),
      );
      return;
    }
    if (duplicateCheck?.duplicate) {
      const orders = (duplicateCheck.orders || []).map((item) => item.amazon_order_id).filter(Boolean).join(", ");
      showPanel(
        "Amazon order already exists",
        `${orders || "An Amazon order"} is already saved in the app. Open the extension popup to review it, copy the number, or clear it after confirming the Amazon order was cancelled.`,
        null,
        null,
      );
      return;
    }
    showPanel("Final step", "Clicking Place your order now.", null, null);
    activeJob.stage = "complete_pending";
    await setActiveJob(activeJob);
    await clickElement(placeOrder, "Place your order button");
  } else {
    await pauseForManualCheckout(activeJob, "Could not find the payment or Place your order control.", "complete_pending");
  }
}

function extractOrderId() {
  const text = document.body.innerText || "";
  const patterns = [
    /order(?:\s*#|\s*number|\s*id)?\s*[:#]?\s*([0-9]{3}-[0-9]{7}-[0-9]{7})/i,
    /\b([0-9]{3}-[0-9]{7}-[0-9]{7})\b/,
  ];
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match) return match[1];
  }
  return "";
}

function confirmationSaysPlaced() {
  const text = (document.body.innerText || "").replace(/\s+/g, " ").toLowerCase();
  return text.includes("has been placed") || text.includes("your order") && text.includes("has been placed");
}

function recentOrdersLink() {
  return [...document.querySelectorAll("a")].find((link) => {
    const text = (link.innerText || link.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
    const href = link.getAttribute("href") || "";
    return visible(link) && (text.includes("review or edit your recent orders") || href.includes("order-history"));
  });
}

function orderDetailsUrl(orderId) {
  return `https://www.amazon.com/your-orders/order-details?orderID=${encodeURIComponent(orderId)}`;
}

function amazonSignedInAccountName() {
  const candidates = [
    "#nav-link-accountList-nav-line-1",
    "#nav-link-accountList .nav-line-1",
    "span.nav-line-1",
  ];
  for (const selector of candidates) {
    const element = [...document.querySelectorAll(selector)].find((node) => visible(node) && /hello,/i.test(node.textContent || ""));
    const text = (element?.textContent || "").replace(/\s+/g, " ").trim();
    const match = text.match(/^hello,\s*(.+)$/i);
    if (match?.[1]) return match[1].trim();
  }
  return "";
}

function extractRecentOrderId(activeJob) {
  const recipient = String(activeJob.job.recipient_name || "").toLowerCase();
  const names = (activeJob.job.order_names || []).map((name) => String(name).toLowerCase());
  const cards = [...document.querySelectorAll("#orderCardHeader, .order-card, [id*='orderCard']")].filter(visible);
  const candidates = cards.length ? cards : [...document.querySelectorAll("body")];
  for (const card of candidates) {
    const text = (card.innerText || card.textContent || "").replace(/\s+/g, " ");
    const lower = text.toLowerCase();
    const matchesRecipient = recipient && lower.includes(recipient);
    const matchesOrderName = names.some((name) => name && lower.includes(name));
    const orderMatch = text.match(/\b\d{3}-\d{7}-\d{7}\b/);
    if (orderMatch && (matchesRecipient || matchesOrderName || candidates.length === 1)) {
      return orderMatch[0];
    }
  }
  return "";
}

async function handleCompletion(activeJob) {
  await waitForElement(["body"], 8000);
  const orderId = extractOrderId();
  if (orderId) {
    await reportAmazonOrder(activeJob, orderId);
    return;
  }
  if (!confirmationSaysPlaced()) return;
  const link = recentOrdersLink();
  if (link) {
    showPanel("Nutricity fulfilment", "Order placed. Opening recent orders to capture the Amazon order number.", null, null);
    activeJob.stage = "find_order_id";
    await setActiveJob(activeJob);
    await clickElement(link, "Review recent orders link");
  }
}

async function handleOrderHistory(activeJob) {
  await waitForElement(["#orderCardHeader", ".order-card", "[id*='orderCard']", "body"], 12000);
  const orderId = extractRecentOrderId(activeJob);
  if (!orderId) {
    showPanel("Nutricity fulfilment", `Looking for recent Amazon order for ${activeJob.job.recipient_name}.`, null, null);
    return;
  }
  await reportAmazonOrder(activeJob, orderId);
}

async function reportAmazonOrder(activeJob, orderId) {
  showPanel("Nutricity fulfilment", `Found Amazon order ${orderId}. Reporting back to the app.`, null, null);
  activeJob.stage = "reporting_complete";
  activeJob.reportedOrderId = orderId;
  activeJob.reportAttemptedAt = Date.now();
  await setActiveJob(activeJob);
  const result = await send({ type: "COMPLETE_JOB", orderId, orderUrl: orderDetailsUrl(orderId), amazonAccountName: amazonSignedInAccountName() });
  if (!result?.ok) {
    const message = result?.message || "Could not report Amazon order back to the app.";
    activeJob.paused = true;
    activeJob.pausedStage = "reporting_complete";
    activeJob.reportError = message;
    await setActiveJob(activeJob);
    showPanel(
      "Nutricity reporting needs attention",
      `${message} Amazon order ${orderId} was found, but the app did not confirm completion.`,
      "Retry reporting",
      () => continueAfterManualStep(activeJob, "reporting_complete"),
    );
    return false;
  }
  showPanel("Nutricity fulfilment", result.message || `Reported Amazon order ${orderId}.`, null, null);
  return true;
}

async function run() {
  if (!extensionContextAlive) return;
  const activeJob = await getActiveJob();
  if (!activeJob?.job || !/amazon\.com$/i.test(location.hostname)) return;
  if (activeJob.paused) {
    showPanel(
      "Nutricity fulfilment paused",
      "Fulfilment is paused. Click Resume to retry this step, or continue if you completed it manually.",
      "I did it manually, continue",
      () => continueAfterManualStep(activeJob),
    );
    return;
  }
  try {
    if (activeJob.stage === "reporting_complete") {
      if (activeJob.reportedOrderId) {
        await reportAmazonOrder(activeJob, activeJob.reportedOrderId);
      } else {
        showPanel("Nutricity fulfilment", "Reporting Amazon order back to the app.", null, null);
      }
    } else if (activeJob.stage === "duplicate_order") {
      const orders = (activeJob.duplicateOrder?.orders || []).map((item) => item.amazon_order_id).filter(Boolean).join(", ");
      showPanel(
        "Amazon order already exists",
        `${orders || "An Amazon order"} is already saved in the app. Open the extension popup to review or clear it before continuing.`,
        null,
        null,
      );
    } else if (activeJob.stage === "complete_pending") {
      await handleCompletion(activeJob);
    } else if (activeJob.stage === "subscribe_checkout") {
      await handleSubscribeCheckout(activeJob);
    } else if (activeJob.stage === "add_clicked") {
      await handleAddClicked(activeJob);
    } else if (activeJob.stage === "find_order_id") {
      activeJob.stage = "find_order_id";
      await setActiveJob(activeJob);
      await handleOrderHistory(activeJob);
    } else if (/\/cart/i.test(location.pathname)) {
      if (activeJob.stage !== "clear_cart") {
        activeJob.stage = "cart";
      }
      await setActiveJob(activeJob);
      await handleCart(activeJob);
    } else if (/\/checkout/i.test(location.pathname)) {
      if (activeJob.stage !== "editing_address") {
        activeJob.stage = "checkout";
      }
      await setActiveJob(activeJob);
      await handleCheckout(activeJob);
    } else {
      activeJob.stage = "product";
      await setActiveJob(activeJob);
      await handleProduct(activeJob);
    }
  } catch (error) {
    showPanel("Nutricity fulfilment error", error.message, null, null);
    const item = activeJob.job.items?.[activeJob.itemIndex];
    const purchaseItem = item ? selectedVariantItem(activeJob, item) : item;
    const shouldMarkItemMissing = Boolean(error.failureCode || error.missingAsin);
    if (!shouldMarkItemMissing) {
      activeJob.pausedStage = activeJob.stage || "product";
      activeJob.paused = true;
      await setActiveJob(activeJob);
      showPanel(
        "Nutricity fulfilment needs attention",
        `${error.message} Complete the stuck Amazon step manually, then continue.`,
        "I did it manually, continue",
        () => continueAfterManualStep(activeJob),
      );
      return;
    }
    if (await continueAfterPartialMissing(activeJob, item, purchaseItem, error.message, error.failureCode || "unavailable", {
      requestedQuantity: error.requestedQuantity ?? null,
      fulfilledQuantity: error.fulfilledQuantity ?? null,
      availableQuantity: error.availableQuantity ?? null,
    })) {
      return;
    }
    let failResult = null;
    try {
      failResult = await send({
        type: "FAIL_JOB",
        message: error.message,
        missingAsin: error.missingAsin || item?.asin || purchaseItem?.asin || "",
        missingLineId: item ? itemPrimaryLineId(item) : null,
        failureCode: error.failureCode || "",
        requestedQuantity: error.requestedQuantity ?? null,
        fulfilledQuantity: error.fulfilledQuantity ?? null,
        availableQuantity: error.availableQuantity ?? null,
      });
    } catch (reportError) {
      await pauseAfterMissingAsinReportFailure(activeJob, reportError.message || "Could not send the Missing ASIN report to the app.");
      return;
    }
    if (!failResult?.ok) {
      await pauseAfterMissingAsinReportFailure(activeJob, failResult?.message || "The app did not confirm the Missing ASIN report.");
      return;
    }
    const reason = error.message || "No reason supplied.";
    const nextMessage = failResult.next_job_started && failResult.next_group_key
      ? `Reason: ${reason} Order moved to Missing ASINs. Starting next order ${failResult.next_group_key}.`
      : `Reason: ${reason} Order moved to Missing ASINs.`;
    showPanel("Missing ASINs", nextMessage, null, null);
  }
}

async function runSafely() {
  if (window.__nutricityRunning) return;
  window.__nutricityRunning = true;
  try {
    await run();
  } finally {
    window.__nutricityRunning = false;
  }
}

runSafely();
setInterval(runSafely, 5000);
