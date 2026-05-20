/* Firefox build: prefer Firefox's Promise-based browser namespace. */
const chrome = globalThis.browser || globalThis.chrome;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const ACTION_DELAY = 1800;
const PAGE_READY_TIMEOUT = 12000;

let extensionContextAlive = true;

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

async function isPaused() {
  const activeJob = await getActiveJob();
  return Boolean(activeJob?.paused);
}

async function waitIfPaused() {
  while (await isPaused()) {
    showPanel("Nutricity fulfilment paused", "Fulfilment is paused. Click Resume to continue.", null, null);
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

function showPanel(title, message, actionText, action) {
  let panel = document.querySelector("#nutricity-panel");
  if (!panel) {
    panel = document.createElement("div");
    panel.id = "nutricity-panel";
    document.documentElement.append(panel);
  }
  panel.innerHTML = `<div class="nutricity-panel-header"><strong></strong><button class="nutricity-pause-toggle" type="button">Pause</button></div><div class="nutricity-panel-message"></div>`;
  panel.querySelector("strong").textContent = title;
  panel.querySelector(".nutricity-panel-message").textContent = message;
  updatePanelPauseButton(panel);
  if (actionText && action) {
    const button = document.createElement("button");
    button.className = "nutricity-panel-action";
    button.textContent = actionText;
    button.addEventListener("click", action);
    panel.append(button);
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
  button.onclick = async () => {
    button.disabled = true;
    try {
      const result = await send({ type: "TOGGLE_PAUSE" });
      const paused = Boolean(result?.paused);
      button.textContent = paused ? "Resume" : "Pause";
      button.classList.toggle("is-paused", paused);
      const latest = await getActiveJob();
      if (latest?.paused) {
        showPanel("Nutricity fulfilment paused", "Fulfilment is paused. Click Resume to continue.", null, null);
      }
    } finally {
      button.disabled = false;
    }
  };
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
    const text = (element.value || element.innerText || element.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
    return visible(element) && wanted.some((needle) => text.includes(needle));
  });
}

function parsePriceFrom(element) {
  if (!element) return null;
  const whole = element.querySelector(".a-price-whole")?.textContent || "";
  const fraction = element.querySelector(".a-price-fraction")?.textContent || "00";
  const value = Number(`${whole.replace(/[^0-9]/g, "")}.${fraction.replace(/[^0-9]/g, "").slice(0, 2)}`);
  return Number.isFinite(value) ? value : null;
}

function firstPriceIn(root) {
  if (!root) return null;
  const price = [...root.querySelectorAll(".a-price")].filter(visible).map(parsePriceFrom).find((value) => value);
  return price || priceFromText(root.innerText || root.textContent || "");
}

function isInSubscribeAndSave(element) {
  return Boolean(element?.closest?.("#snsAccordionRowMiddle, #snsAccordionRow, #snsAccordionRowContent, #reinvent_price_desktop_snsAccordionRowMiddle"));
}

function productPriceSnapshot() {
  const regularSelectors = [
    "#corePrice_feature_div .a-price",
    "#apex_desktop .a-price",
    "#reinvent_price_desktop_newAccordionRow .a-price",
    "#newAccordionRow .a-price",
    "#buybox .a-price",
    "#tp_price_block_total_price_ww .a-price",
    ".a-price",
  ];
  const regular = regularSelectors
    .flatMap((selector) => [...document.querySelectorAll(selector)])
    .filter((element) => visible(element) && !isInSubscribeAndSave(element))
    .map(parsePriceFrom)
    .find((value) => value) || null;
  const snsRoot = document.querySelector("#snsAccordionRowMiddle, #snsAccordionRow, #snsAccordionRowContent, #reinvent_price_desktop_snsAccordionRowMiddle");
  const sns = firstPriceIn(snsRoot);
  const best = sns && regular ? Math.min(sns, regular) : sns || regular || 0;
  return { regular, sns, best };
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
  return item ? itemPrimaryLineId(item) : null;
}

function cartAlreadyHasExpectedItems(activeJob) {
  const expected = expectedCartQuantities(activeJob);
  if (!Object.keys(expected).length) return false;
  const actual = {};
  for (const item of cartActiveItems()) {
    const asin = cartItemAsin(item);
    if (asin && expected[asin]) {
      actual[asin] = (actual[asin] || 0) + cartItemQuantity(item);
    }
  }
  return Object.entries(expected).some(([asin, quantity]) => Number(actual[asin] || 0) >= Number(quantity));
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
      ...root.querySelectorAll(".a-checkbox:not(.aok-hidden) label, .a-icon-checkbox"),
    ].filter((element) => visible(element) || visible(element.closest?.("label, .a-checkbox, span, div")));
    for (const candidate of candidates) {
      const target = candidate.closest?.("label") || candidate;
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
  await waitForElement(["#corePrice_feature_div .a-price", "#apex_desktop .a-price", "#snsAccordionRowMiddle, #snsAccordionRow, #snsAccordionRowContent"], 12000);
  const { regular: pagePrice, sns: snsPrice } = productPriceSnapshot();
  const snsRoot = document.querySelector("#snsAccordionRowMiddle, #snsAccordionRow, #snsAccordionRowContent, #reinvent_price_desktop_snsAccordionRowMiddle");
  if (!pagePrice || !snsPrice || snsPrice >= pagePrice) return false;

  const radio = document.querySelector(".a-accordion-radio.a-icon-radio-inactive") || snsRoot?.querySelector("[role='radio'], input[type='radio']");
  if (radio) {
    radio.scrollIntoView({ block: "center", behavior: "smooth" });
    await sleep(500);
    radio.click();
    await sleep(1500);
  }
  await applyAdditionalSavings("sns");
  const quantitySet = await setQuantity(quantity, "sns");
  if (!quantitySet) {
    throw new Error(`Could not set Subscribe & Save quantity ${quantity || 1}.`);
  }
  const subscribeButton = document.querySelector("#rcx-subscribe-submit-button button, #rcx-subscribe-submit-button input") || findButtonByText(["subscribe"]);
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

function quantitySelects(context = "regular") {
  const selects = [...document.querySelectorAll("select")].filter((select) => {
    const id = select.id || "";
    if (context === "sns") {
      return (
        /^sns/i.test(id) && id.includes("predefinedQuantitiesDropdown") ||
        Boolean(select.closest("#snsAccordionRowMiddle, #snsAccordionRow, #snsAccordionRowContent"))
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

function quantityFreeFormInput(select, context = "regular") {
  const id = select?.id || "";
  const candidates = [];
  if (id.includes("predefinedQuantitiesDropdown")) {
    candidates.push(`#${CSS.escape(id.replace("predefinedQuantitiesDropdown", "freeQuantityTextInput"))}`);
  }
  if (context === "sns") {
    candidates.push("input[id^='sns'][id$='freeQuantityTextInput']", "#snsAccordionRowMiddle input.freeQuantityTextInput", "#snsAccordionRow input.freeQuantityTextInput");
  } else {
    candidates.push("input[id*='new_buyingOption'][id$='freeQuantityTextInput']", "input.freeQuantityTextInput:not([id^='sns'])", "input#quantity");
  }
  for (const selector of candidates) {
    const input = document.querySelector(selector);
    if (input) return input;
  }
  return null;
}

async function chooseAmazonDropdownOption(select, qty) {
  const container = select.closest(".a-dropdown-container") || select.parentElement;
  const dropdownButton = container?.querySelector(".a-button-dropdown, [data-action='a-dropdown-button']");
  if (!dropdownButton || !visible(dropdownButton)) return false;
  dropdownButton.scrollIntoView({ block: "center", behavior: "smooth" });
  await sleep(350);
  dropdownButton.click();
  await sleep(700);
  const optionText = qty > 9 ? "10+" : String(qty);
  const links = [...document.querySelectorAll(".a-popover-wrapper a.a-dropdown-link, .a-popover-inner a.a-dropdown-link, a[role='option']")].filter(visible);
  const option = links.find((link) => (link.textContent || "").replace(/\s+/g, " ").trim() === optionText);
  if (!option) return false;
  option.scrollIntoView({ block: "nearest" });
  await sleep(250);
  option.click();
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
  if (qty <= 9) return true;
  const input = quantityFreeFormInput(select, context);
  if (!input) return false;
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
  return true;
}

async function setQuantity(quantity, context = "regular") {
  await waitForElement(["select#quantity", "select[name='quantity']", "select[id*='predefinedQuantitiesDropdown']", "#add-to-cart-button", "#rcx-subscribe-submit-button"], 12000);
  const qty = Math.max(1, Math.round(Number(quantity) || 1));
  if (qty === 1) return true;

  const selects = quantitySelects(context);
  for (const select of selects) {
    showPanel("Nutricity fulfilment", `Setting ${context === "sns" ? "Subscribe & Save " : ""}quantity to ${qty}.`, null, null);
    const clicked = await chooseAmazonDropdownOption(select, qty);
    const selected = clicked || await setNativeSelectQuantity(select, qty);
    if (!selected) continue;
    const freeFormSet = await setFreeFormQuantity(select, qty, context);
    if (qty <= 9 || freeFormSet) return true;
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
  if (!canClearCart(activeJob) || cartAlreadyHasExpectedItems(activeJob)) {
    activeJob.stage = "cart";
    activeJob.cartCleared = true;
    await setActiveJob(activeJob);
    showPanel("Nutricity fulfilment", "Cart already prepared for this order. Skipping cart clear.", null, null);
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
  await waitForElement([
    "#productTitle",
    "#add-to-cart-button",
    "input[name='submit.add-to-cart']",
    "#rcx-subscribe-submit-button",
    "#corePrice_feature_div .a-price",
    "#apex_desktop .a-price",
  ], 18000);
  const item = activeJob.job.items[activeJob.itemIndex];
  if (!item) return;
  const asin = currentAsinFromUrl();
  const expectedItem = selectedVariantItem(activeJob, item);
  showPanel("Nutricity fulfilment", `Adding ${expectedItem.asin} for ${recipientName(activeJob)}.`, null, null);
  if (asin && asin !== expectedItem.asin) {
    location.href = `https://www.amazon.com/dp/${expectedItem.asin}`;
    return;
  }
  rememberProductDosage(activeJob, item);
  await setActiveJob(activeJob);

  await applyAdditionalSavings("regular");
  const promoCount = markPromotions();
  if (promoCount && !activeJob.promoAcknowledged[item.asin]) {
    showPanel(
      "Coupon or promotion found",
      "Tick any Amazon coupon/promotion you want to use, then continue.",
      "I applied it, continue",
      async () => {
        const latest = await getActiveJob();
        latest.promoAcknowledged[item.asin] = true;
        await setActiveJob(latest);
        handleProduct(latest);
      },
    );
    return;
  }

  const priceSnapshot = productPriceSnapshot();
  const switchedVariant = await selectCheapestCountVariant(activeJob, item, priceSnapshot.best);
  if (switchedVariant) return;
  const purchaseItem = selectedVariantItem(activeJob, item);
  const selectionNote = variantSelectionNote(item, purchaseItem);
  if (selectionNote) {
    showPanel("Cheaper variant found", `${selectionNote} Proceeding with this option.`, null, null);
    await sleep(1800);
  }
  await recordAmazonPrice(activeJob, item, priceSnapshot.best, priceSnapshot.sns && priceSnapshot.sns === priceSnapshot.best ? "subscribe-save" : "product", purchaseItem);
  const quantity = Number(purchaseItem.quantity || 1);
  const storeTotal = Number(item.store_total_price || Number(item.store_unit_price || 0) * Number(item.quantity || 1) || 0);
  const amazonTotal = Number(priceSnapshot.best || 0) * quantity;
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

  const subscribed = await applySubscribeAndSaveIfCheaper(purchaseItem.quantity, activeJob);
  if (!subscribed) {
    const quantitySet = await setQuantity(purchaseItem.quantity, "regular");
    if (!quantitySet) {
      const requestedQuantity = Math.max(1, Math.round(Number(purchaseItem.quantity || 1)));
      const availableQuantity = maxSelectableQuantity("regular");
      const lessQuantity = availableQuantity > 0 && availableQuantity < requestedQuantity;
      await send({
        type: "FAIL_JOB",
        message: lessQuantity
          ? `Less quantity available for ASIN ${purchaseItem.asin}. Customer ordered ${requestedQuantity}, Amazon only allows ${availableQuantity}.`
          : `ASIN ${purchaseItem.asin} is missing or unavailable. Could not set quantity ${purchaseItem.quantity || 1}.`,
        missingAsin: item.asin,
        missingLineId: itemPrimaryLineId(item),
        failureCode: lessQuantity ? "partial_quantity" : "",
        requestedQuantity,
        fulfilledQuantity: lessQuantity ? availableQuantity : null,
        availableQuantity: lessQuantity ? availableQuantity : null,
      });
      showPanel("Nutricity fulfilment", lessQuantity ? "Less quantity available. Order moved to Missing ASINs." : "Could not set item quantity. Job marked as error.", null, null);
      return;
    }
    const addButton = await waitForElement(["#add-to-cart-button", "input[name='submit.add-to-cart']", "#buybox-add-to-cart-button input"], 18000);
    activeJob.stage = "add_clicked";
    activeJob.addClickedAt = Date.now();
    markItemAdded(activeJob);
    await setActiveJob(activeJob);
    const added = await clickElement(addButton, "Add to cart button");
    if (!added) {
      await send({ type: "FAIL_JOB", message: `ASIN ${purchaseItem.asin} is missing or unavailable. Could not find Add to cart button.`, missingAsin: item.asin, missingLineId: itemPrimaryLineId(item) });
      showPanel("Nutricity fulfilment", "Could not find Add to cart. Job marked as error.", null, null);
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
    const shortQuantity = (cartCheck.mismatches || []).find((item) => item.actual > 0 && item.actual < item.expected);
    if (shortQuantity) {
      await send({
        type: "FAIL_JOB",
        message: `Less quantity available for ASIN ${shortQuantity.asin}. Customer ordered ${shortQuantity.expected}, Amazon cart has ${shortQuantity.actual}.`,
        missingAsin: shortQuantity.asin,
        missingLineId: lineIdForAsin(activeJob, shortQuantity.asin),
        failureCode: "partial_quantity",
        requestedQuantity: shortQuantity.expected,
        fulfilledQuantity: shortQuantity.actual,
        availableQuantity: shortQuantity.actual,
      });
      showPanel("Cart quantity needs review", "Less quantity available. Order moved to Missing ASINs.", null, null);
      return;
    }
    activeJob.paused = true;
    activeJob.stage = "cart";
    await setActiveJob(activeJob);
    showPanel("Cart quantity needs review", `Paused before checkout. ${cartCheck.message}`, null, null);
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
  const selectors = [
    "#address-ui-widgets-enterAddressFullName",
    "input[name='address-ui-widgets-enterAddressFullName']",
    "input[aria-label='Full name']",
    "input[aria-label='Full Name']",
    "input[placeholder='Full name']",
    "input[placeholder='Full Name']",
    "input[autocomplete='name']",
    "input[name*='FullName']",
    "input[id*='FullName']",
  ];
  const input = await waitForElement(selectors, 20000);
  if (!input) return false;
  input.scrollIntoView({ block: "center", behavior: "smooth" });
  await sleep(500);
  input.focus();
  input.select?.();
  input.value = name;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
  input.blur();
  await sleep(1000);
  return true;
}

function findEditAddressTrigger() {
  const triggers = [...document.querySelectorAll("[id^='declarativeAction-'][data-action='checkout-view-modal']")].filter((element) => {
    const modalData = element.getAttribute("data-checkout-view-modal") || "";
    const text = (element.innerText || element.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
    return visible(element) && (modalData.includes("editAddressModal") || text.includes("edit address"));
  });
  const preferred = triggers.find((element, index) => {
    if (index === 0) return false;
    const row = element.closest("[data-testid^='address-row-'], .address-row, [data-action='select_address_in_list']");
    return /nutricity/i.test(row?.innerText || "");
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
    if (!root || root.closest?.("#nutricity-panel")) continue;
    const text = normalizedText(root.innerText || root.textContent);
    if (text.includes(wanted)) return true;
  }
  return false;
}

function findAddressNameInput() {
  return [...document.querySelectorAll(
    "#address-ui-widgets-enterAddressFullName, input[name='address-ui-widgets-enterAddressFullName'], input[aria-label='Full name'], input[name*='FullName'], input[id*='FullName']",
  )].find((element) => visible(element) && !element.disabled);
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
    "input[data-csa-c-slot-id*='continue-edit-address']",
    "button[data-csa-c-slot-id*='continue-edit-address']",
  ];
  if (addressEditorOpen) {
    selectors.push(
      "input[data-testid='bottom-continue-button'][aria-labelledby='checkout-primary-continue-button-id-announce']",
      "#checkout-primary-continue-button-id input",
    );
  }
  const direct = [...document.querySelectorAll(selectors.join(", "))].find((element) => visible(element) && !element.disabled);
  if (direct) return direct;

  return [...document.querySelectorAll("input[type='submit'], button, span.a-button")].find((element) => {
    const text = normalizedText(element.value || element.innerText || element.textContent);
    return visible(element) && [
      "use this address",
      "save address",
      "deliver to this address",
      "continue to checkout",
      "continue",
    ].some((needle) => text.includes(needle));
  });
}

function findPaymentRadio() {
  return [...document.querySelectorAll("input[type='radio'][name='ppw-instrumentRowSelection']")]
    .find((element) => visible(element) && !element.disabled);
}

function findPaymentSelection() {
  const radio = findPaymentRadio();
  if (!radio) return null;

  const exactContinue = [...document.querySelectorAll("input[data-csa-c-slot-id*='continue-payselect'], button[data-csa-c-slot-id*='continue-payselect']")]
    .find((element) => visible(element) && !element.disabled);
  if (exactContinue) return { radio, continueButton: exactContinue };

  const continueButton = [...document.querySelectorAll("input[type='submit'], button, span.a-button")].find((element) => {
    const text = normalizedText(element.value || element.innerText || element.textContent);
    return visible(element) && !element.disabled && (
      text.includes("use this payment method") ||
      text === "continue" ||
      text.includes("continue")
    );
  });
  return radio && continueButton ? { radio, continueButton } : null;
}

function findPlaceOrderButton() {
  return [...document.querySelectorAll("#placeOrder, input.place-your-order-button, input[type='submit'], button, span.a-button")].find((element) => {
    const text = normalizedText(element.value || element.innerText || element.textContent);
    return visible(element) && !element.disabled && (
      element.matches?.("#placeOrder, input.place-your-order-button") ||
      text.includes("place your order")
    );
  });
}

async function handlePaymentSelection(activeJob) {
  let payment = findPaymentSelection();
  if (!payment) {
    if (!findPaymentRadio()) return false;
    payment = await waitUntil(findPaymentSelection, 5000);
    if (!payment) {
      await pauseForManualCheckout(activeJob, "Could not find the Use this payment method button.");
      return true;
    }
  }
  showPanel("Nutricity checkout", "Selecting Amazon payment method.", null, null);
  if (!payment.radio.checked) {
    await clickElement(payment.radio, "Payment method radio");
  }
  await clickElement(payment.continueButton, "Use this payment method button");
  showPanel("Nutricity checkout", "Payment method selected. Waiting for checkout.", null, null);
  await sleep(2000);
  await waitUntil(() => findPlaceOrderButton(), 10000);
  activeJob.stage = "checkout";
  await setActiveJob(activeJob);
  return true;
}

async function pauseForManualCheckout(activeJob, message) {
  activeJob.stage = "checkout";
  activeJob.paused = true;
  await setActiveJob(activeJob);
  showPanel("Nutricity checkout needs attention", `${message} Make the needed Amazon checkout change, then click Resume.`, null, null);
}

async function saveEditedAddress(activeJob, checkoutRecipient) {
  showPanel("Nutricity checkout", "Editing Amazon address name.", null, null);
  const filled = await fillFullName(checkoutRecipient);
  if (!filled) return false;

  showPanel("Nutricity checkout", "Saving Amazon address.", null, null);
  const useAddress = await waitUntil(findUseAddressButton, 8000) || findButtonByText(["use this address", "save address", "deliver to this address", "continue"]);
  if (!useAddress) {
    await pauseForManualCheckout(activeJob, "Could not find the Use this address button.");
    return false;
  }
  await clickElement(useAddress, "Use this address button");
  await sleep(2000);

  showPanel("Nutricity checkout", "Waiting for checkout to update.", null, null);
  await waitUntil(
    () => checkoutShowsRecipient(checkoutRecipient) || findPaymentSelection() || findPlaceOrderButton() || !findAddressNameInput(),
    12000,
  );
  activeJob.stage = "checkout";
  activeJob.editAddressClickedAt = null;
  await setActiveJob(activeJob);
  return true;
}

async function handleCheckout(activeJob) {
  await waitForElement([
    "#placeOrder",
    "input.place-your-order-button",
    "[data-checkout-view-modal]",
    "#checkout-primary-continue-button-id",
    "input[aria-label='Full name']",
    "input[type='radio'][name='ppw-instrumentRowSelection']",
    "input[data-csa-c-slot-id*='continue-payselect']",
  ], 18000);
  const checkoutRecipient = recipientName(activeJob);
  showPanel("Nutricity checkout", `Using recipient name: ${checkoutRecipient}`, null, null);
  if (activeJob.stage === "editing_address") {
    if (checkoutShowsRecipient(checkoutRecipient) || findPaymentSelection() || findPlaceOrderButton()) {
      activeJob.stage = "checkout";
      activeJob.editAddressClickedAt = null;
      await setActiveJob(activeJob);
    } else if (await saveEditedAddress(activeJob, checkoutRecipient)) {
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
  const continueCheckout = findButtonByText(["continue to checkout"]);
  if (continueCheckout) {
    await clickElement(continueCheckout, "Continue to checkout button");
    return;
  }

  const handledPayment = await handlePaymentSelection(activeJob);
  if (handledPayment) {
    // Amazon sometimes asks for payment confirmation before the final place-order step.
  }
  if (activeJob.paused) return;

  if (handledPayment) {
    showPanel("Nutricity checkout", "Payment confirmed. Continuing checkout.", null, null);
  } else if (checkoutShowsRecipient(checkoutRecipient)) {
    showPanel("Nutricity checkout", "Recipient already set. Continuing checkout.", null, null);
  } else {
    const editAddress = findEditAddressTrigger();
    if (editAddress) {
      showPanel("Nutricity checkout", "Opening Amazon edit address modal.", null, null);
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
      if (await saveEditedAddress(activeJob, checkoutRecipient)) {
        // Continue below to place the order immediately when Amazon has returned to checkout.
      } else {
        return;
      }
    }
  }

  if (!handledPayment && !checkoutShowsRecipient(checkoutRecipient) && !findPlaceOrderButton() && await fillFullName(checkoutRecipient)) {
    await sleep(1200);
    const useAddress = await waitUntil(findUseAddressButton, 8000) || findButtonByText(["use this address", "save address", "deliver to this address", "continue"]);
    if (useAddress) {
      await clickElement(useAddress, "Use this address button");
      await sleep(2000);
      await waitUntil(() => checkoutShowsRecipient(checkoutRecipient) || findPaymentSelection() || findPlaceOrderButton(), 10000);
    } else {
      await pauseForManualCheckout(activeJob, "Could not find the Use this address button.");
      return;
    }
  }

  if (!handledPayment && await handlePaymentSelection(activeJob)) {
    // Continue below to place the order if Amazon exposed the final button.
  }
  if (activeJob.paused) return;

  const placeOrder = await waitUntil(findPlaceOrderButton, 10000) || await waitForElement(["#placeOrder:not([disabled])", "input.place-your-order-button:not([disabled])"], 3000) || findButtonByText(["place your order"]);
  if (placeOrder && !placeOrder.disabled) {
    showPanel("Final step", "Clicking Place your order now.", null, null);
    activeJob.stage = "complete_pending";
    await setActiveJob(activeJob);
    await clickElement(placeOrder, "Place your order button");
  } else {
    await pauseForManualCheckout(activeJob, "Could not find the payment or Place your order control.");
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
    showPanel("Nutricity fulfilment", "Amazon order placed. Reporting back to the app.", null, null);
    await send({ type: "COMPLETE_JOB", orderId, orderUrl: orderDetailsUrl(orderId), amazonAccountName: amazonSignedInAccountName() });
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
  showPanel("Nutricity fulfilment", `Found Amazon order ${orderId}. Reporting back to the app.`, null, null);
  await send({ type: "COMPLETE_JOB", orderId, orderUrl: orderDetailsUrl(orderId), amazonAccountName: amazonSignedInAccountName() });
}

async function run() {
  if (!extensionContextAlive) return;
  const activeJob = await getActiveJob();
  if (!activeJob?.job || !/amazon\.com$/i.test(location.hostname)) return;
  if (activeJob.paused) {
    showPanel("Nutricity fulfilment paused", "Fulfilment is paused. Click Resume to continue.", null, null);
    return;
  }
  try {
    if (activeJob.stage === "complete_pending") {
      await handleCompletion(activeJob);
    } else if (activeJob.stage === "subscribe_checkout") {
      await handleSubscribeCheckout(activeJob);
    } else if (activeJob.stage === "add_clicked") {
      await handleAddClicked(activeJob);
    } else if (activeJob.stage === "find_order_id" || /order-history|your-account\/order-history|your-orders/i.test(location.href)) {
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
    await send({ type: "FAIL_JOB", message: error.message });
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
