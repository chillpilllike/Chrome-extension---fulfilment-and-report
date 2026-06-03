(() => {
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

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

async function sendWithTimeout(message, timeoutMs = 15000) {
  let timer = null;
  try {
    return await Promise.race([
      send(message),
      new Promise((resolve) => {
        timer = setTimeout(() => {
          resolve({
            ok: false,
            timedOut: true,
            message: "The extension is still processing this page in the background. If it does not move shortly, press Stop and Resume Track all.",
          });
        }, timeoutMs);
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function nudgeStuckTrackingPage(data, reason = "tracking page timeout") {
  const amazonOrderId = String(data?.amazonOrderId || "").trim();
  if (!amazonOrderId) return;
  window.setTimeout(async () => {
    try {
      const state = await send({ type: "GET_STATE" });
      const activeOrderId = state?.tracking?.source === "history"
        ? state?.tracking?.currentOrder?.amazon_order_id
        : state?.tracking?.orders?.[Number(state?.tracking?.index || 0)]?.amazon_order_id;
      if (!state?.tracking?.running || String(activeOrderId || "") !== amazonOrderId) return;
      showPanel("Nutricity tracking", `Still waiting on ${amazonOrderId}. Retrying this tracking page.`);
      const retry = await sendWithTimeout({ type: "PACKAGE_TRACKING", ...data, retry: true }, 20000);
      if (retry?.ok || retry?.ignored) return;
      await send({
        type: "FORCE_ADVANCE_HISTORY_ORDER",
        amazonOrderId,
        status: "failed",
        reason,
      });
    } catch (_) {
      // The next watcher tick or background watchdog will continue the saved queue.
    }
  }, 14000);
}

function logContent(message) {
  return send({ type: "CONTENT_LOG", message }).catch(() => null);
}

function clean(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function isoDate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function displayDate(date) {
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function promiseDetails(text) {
  const promise = clean(text);
  const lower = promise.toLowerCase();
  if (!promise) return { promise: "", expected_delivery_date: "", expected_delivery_display: "" };
  const base = new Date();
  let expected = null;
  if (/\btomorrow\b/.test(lower)) {
    expected = new Date(base.getFullYear(), base.getMonth(), base.getDate() + 1);
  } else if (/\btoday\b/.test(lower)) {
    expected = new Date(base.getFullYear(), base.getMonth(), base.getDate());
  } else {
    const monthMatch = promise.match(/\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(\d{1,2})(?:,\s*(\d{4}))?/i);
    if (monthMatch) {
      const monthIndex = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"].indexOf(monthMatch[1].slice(0, 3).toLowerCase());
      const day = Number(monthMatch[2]);
      let year = Number(monthMatch[3] || base.getFullYear());
      expected = new Date(year, monthIndex, day);
      if (!monthMatch[3] && expected < new Date(base.getFullYear(), base.getMonth(), base.getDate() - 1)) {
        year += 1;
        expected = new Date(year, monthIndex, day);
      }
    }
  }
  return {
    promise,
    expected_delivery_date: expected ? isoDate(expected) : "",
    expected_delivery_display: expected ? displayDate(expected) : "",
  };
}

function withPromiseDetails(packageData) {
  const details = promiseDetails(packageData.promise || packageData.status || packageData.order_status || "");
  return { ...packageData, ...details, promise: packageData.promise || details.promise };
}

function showPanel(title, message) {
  let panel = document.querySelector("#nutricity-tracking-panel");
  if (!panel) {
    panel = document.createElement("div");
    panel.id = "nutricity-tracking-panel";
    document.documentElement.append(panel);
  }
  panel.innerHTML = `<strong></strong><div></div>`;
  panel.querySelector("strong").textContent = title;
  panel.querySelector("div").textContent = message;
}

function responseError(response, fallback) {
  if (!response) return "Extension background is not responding. Reload the Nutricity Tracking extension and start tracking again.";
  return response.message || fallback;
}

function processingMessage(response, fallback) {
  if (response?.timedOut) return response.message;
  return responseError(response, fallback);
}

async function waitForPageReady() {
  const started = Date.now();
  while (document.readyState === "loading" && Date.now() - started < 5000) {
    await sleep(250);
  }
  await sleep(500);
}

function isTrackingPage() {
  return /ship-track|progress-tracker\/package/i.test(location.href);
}

async function waitForTrackingPageReady() {
  const started = Date.now();
  while (Date.now() - started < 5000) {
    if (currentOrderId() && (document.querySelector(".pt-status-main-status, .pt-promise-main-slot, .delivery-card, .pt-delivery-card-wrapper, #primaryStatus") || /ordered|shipped|delivered|arriving/i.test(document.body?.innerText || ""))) {
      break;
    }
    await sleep(150);
  }
  await sleep(150);
}

function currentOrderId() {
  const url = new URL(location.href);
  const fromQuery = url.searchParams.get("orderID") || url.searchParams.get("orderId");
  if (fromQuery) return fromQuery;
  const fromHref = location.href.match(/\b\d{3}-\d{7}-\d{7}\b/);
  if (fromHref) return fromHref[0];
  const fromPage = clean(document.querySelector("#orderDetails, .order-date-invoice-item, body")?.textContent || "").match(/\b\d{3}-\d{7}-\d{7}\b/);
  return fromPage ? fromPage[0] : "";
}

function absoluteUrl(href) {
  return new URL(href, location.href).href;
}

function visible(element) {
  if (!element) return false;
  const style = getComputedStyle(element);
  const rect = element.getBoundingClientRect();
  return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
}

function orderIdsFromText(text) {
  return [...new Set((String(text || "").match(/\b\d{3}-\d{7}-\d{7}\b/g) || []).map((value) => value.trim()))];
}

function orderRefsFromText(text) {
  return [...new Set((String(text || "").match(/\b[A-Z]{2,5}\d{2,}\b/gi) || []).map((value) => value.toUpperCase()))];
}

function orderIdFromAmazonHref(href = "") {
  try {
    const url = new URL(href, location.href);
    return url.searchParams.get("orderID") || url.searchParams.get("orderId") || (href.match(/\b\d{3}-\d{7}-\d{7}\b/) || [])[0] || "";
  } catch {
    return (String(href || "").match(/\b\d{3}-\d{7}-\d{7}\b/) || [])[0] || "";
  }
}

function orderDetailsUrl(orderId) {
  return `https://www.amazon.com/your-orders/order-details?orderID=${encodeURIComponent(orderId)}`;
}

function isOrderHistoryPage() {
  const path = location.pathname.replace(/\/+$/, "");
  return /^\/gp\/css\/order-history$/i.test(path)
    || /^\/gp\/your-account\/order-history$/i.test(path)
    || /^\/your-orders(?:\/orders?)?$/i.test(path);
}

function isOrderDetailsLikePage() {
  const path = location.pathname.replace(/\/+$/, "");
  return /^\/your-orders\/order-details$/i.test(path)
    || /order-details|your-orders\/order|gp\/css\/summary|gp\/your-account\/order|gp\/your-account\/ship-track/i.test(location.href);
}

function recipientFromOrderHistoryText(text = "") {
  const value = String(text || "").replace(/\s+/g, " ").trim();
  const nutricityMatch = value.match(/\bNutricity\s+[A-Z]{2,5}\d{2,}(?:\s+[A-Za-z0-9]+){0,4}/i);
  if (nutricityMatch?.[0]) {
    return nutricityMatch[0]
      .replace(/\b(?:Order|Placed|Total|Ship|To|View|Buy|Again|Invoice|Details)\b.*$/i, "")
      .replace(/\b([A-Z]{2,5}\d{2,})\s+(\d{1,4})\b/g, "$1$2")
      .replace(/\s+/g, " ")
      .trim();
  }
  const shipMatch = value.match(/\bShip\s+to\s+(.+?)(?:\s+\b(?:Order placed|Order #|Total|Buy it again|View order details|Invoice|Delivered|Arriving|Cancelled)\b|$)/i);
  return shipMatch?.[1]?.replace(/\s+/g, " ").trim() || "";
}

function orderHistoryCardCandidates() {
  const links = [...document.querySelectorAll("a[href*='orderID='], a[href*='orderId='], a[href*='order-details']")]
    .filter((link) => visible(link) && orderIdFromAmazonHref(link.href || link.getAttribute("href") || ""));
  const cards = [];
  const seenIds = new Set();
  for (const link of links) {
    const orderId = orderIdFromAmazonHref(link.href || link.getAttribute("href") || "");
    if (!orderId || seenIds.has(orderId)) continue;
    let best = link.closest(".a-box-group, .order-card, .js-order-card, [class*='order-card'], [data-order-id], [data-test-id*='order-card']");
    let parent = best || link.parentElement;
    for (let depth = 0; parent && depth < 8; depth += 1, parent = parent.parentElement) {
      const text = clean(parent.innerText || parent.textContent || "");
      const ids = orderIdsFromText(text);
      if (ids.includes(orderId) && ids.length === 1 && text.length < 18000 && /\b(Order placed|Order #|Ship to|Delivered|Arriving|Cancelled|Buy it again)\b/i.test(text)) {
        best = parent;
      }
    }
    if (best && visible(best)) {
      seenIds.add(orderId);
      cards.push(best);
    }
  }
  if (cards.length) return cards;
  return [...document.querySelectorAll(".order-card, .js-order-card, [data-order-id], .a-box-group, .a-box")]
    .filter((card) => visible(card) && orderIdsFromText(clean(card.textContent || "")).length)
    .slice(0, 20);
}

function orderCardOrderId(card) {
  const fromData = card?.getAttribute?.("data-order-id") || "";
  if (fromData) return fromData;
  const link = [...card.querySelectorAll("a[href*='orderID='], a[href*='orderId='], a[href*='order-details']")]
    .find((item) => orderIdFromAmazonHref(item.href || item.getAttribute("href") || ""));
  return orderIdFromAmazonHref(link?.href || link?.getAttribute?.("href") || "") || orderIdsFromText(clean(card.textContent || ""))[0] || "";
}

function orderCardRecipient(card) {
  const orderId = orderCardOrderId(card);
  const selectors = [
    ".shipToTriggerTextTruncate .a-truncate-full",
    ".shipToTriggerTextTruncate .a-truncate-cut",
    ".shipToTriggerTextTruncate",
    "[id^='a-popover-PreloadedContent_'] .a-text-bold",
    "[data-test-id*='recipient']",
    "[data-test-id*='ship']",
  ];
  for (const selector of selectors) {
    const text = [...card.querySelectorAll(selector)]
      .map((node) => recipientFromOrderHistoryText(node.textContent || "") || clean(node.textContent || ""))
      .find(Boolean);
    if (text) return text;
  }
  if (orderId) {
    const preloaded = document.getElementById(`a-popover-PreloadedContent_${orderId}`);
    const popoverText = [...(preloaded?.querySelectorAll?.(".a-text-bold") || [])]
      .map((node) => recipientFromOrderHistoryText(node.textContent || "") || clean(node.textContent || ""))
      .find(Boolean);
    if (popoverText) return popoverText;
  }
  return recipientFromOrderHistoryText(card.innerText || card.textContent || "");
}

function orderCardDate(card) {
  const text = clean(card.querySelector("#orderCardHeader, [id*='orderCardHeader'], [data-test-id*='order-header']")?.textContent || card.textContent || "");
  return (text.match(/Order placed\s+([A-Z][a-z]+ \d{1,2}, \d{4})/) || [])[1] || "";
}

function orderCardStatus(card) {
  const statuses = [...card.querySelectorAll("#orderCardDeliveryBox .a-size-medium .a-text-bold, #orderCardDeliveryBox .a-size-medium, [data-test-id*='status'], [class*='delivery'] .a-size-medium, .a-size-medium .a-text-bold, .a-size-medium")]
    .map((node) => clean(node.textContent))
    .filter(Boolean)
    .filter((text) => !/order placed|order #|total|ship to|buy it again|view order details|invoice/i.test(text));
  return statuses.find((text) => /arriv|deliver|ship|cancel|refund|return|unavailable|payment revision|update your payment/i.test(text)) || statuses[0] || "";
}

function orderCardPaymentRevision(card) {
  const text = clean(card?.innerText || card?.textContent || "");
  const reviseLink = card?.querySelector?.("a[href*='/cpe/revisepayments'], a[href*='revisepayments']");
  const needed = /payment revision needed/i.test(text)
    || /please update your payment method/i.test(text)
    || /revise payment/i.test(clean(reviseLink?.textContent || ""));
  return {
    paymentRevisionNeeded: needed,
    paymentRevisionUrl: reviseLink ? absoluteUrl(reviseLink.getAttribute("href") || "") : "",
    pageText: needed ? text.slice(0, 2000) : "",
  };
}

function orderCardItems(card) {
  const items = [];
  const byAsin = new Map();
  for (const product of productItemsFrom(card)) {
    if (!product.asin) continue;
    const existing = byAsin.get(product.asin) || { ...product, quantity: 0 };
    existing.quantity += 1;
    byAsin.set(product.asin, existing);
  }
  for (const item of byAsin.values()) items.push(item);
  return items;
}

function orderCardTrackingPackages(card, orderId) {
  const links = [...card.querySelectorAll("a[href*='ship-track'], a[href*='/gp/your-account/ship-track']")]
    .filter((link) => {
      const text = clean(link.textContent);
      const href = link.getAttribute("href") || "";
      return /track package|tracking/i.test(text) || /ship-track/i.test(href);
    });
  const packages = [];
  const seen = new Set();
  for (const link of links) {
    const href = absoluteUrl(link.getAttribute("href") || "");
    if (!href || seen.has(href)) continue;
    seen.add(href);
    const box = shipmentRootForTrackingLink(link);
    const products = productItemsFrom(box);
    packages.push(withPromiseDetails({
      tracking_url: href,
      order_status: clean(box.querySelector(".od-status-message, [data-component='shipmentStatus'] h4")?.textContent) || orderCardStatus(card),
      promise: orderCardStatus(card),
      asins: products.map((item) => item.asin).filter(Boolean),
      products,
      amazon_order_id: orderId,
    }));
  }
  return packages;
}

function nextHistoryPageUrl() {
  const next = [...document.querySelectorAll("li.a-last a[href*='pagination/next'], a[href*='#pagination/next']")]
    .find((link) => visible(link));
  if (next) return absoluteUrl(next.getAttribute("href") || next.href || "");
  return "";
}

function extractHistoryTrackOrders() {
  const seen = new Set();
  const orders = [];
  for (const card of orderHistoryCardCandidates()) {
    const orderId = orderCardOrderId(card);
    if (!orderId || seen.has(orderId)) continue;
    seen.add(orderId);
    const items = orderCardItems(card);
    const paymentRevision = orderCardPaymentRevision(card);
    const status = paymentRevision.paymentRevisionNeeded ? "Payment revision needed" : orderCardStatus(card);
    orders.push({
      amazon_order_id: orderId,
      amazon_order_url: orderDetailsUrl(orderId),
      recipient: orderCardRecipient(card),
      order_date: orderCardDate(card),
      status,
      asins: items.map((item) => item.asin).filter(Boolean),
      items,
      products: items,
      packages: orderCardTrackingPackages(card, orderId),
      cancelled: /cancel/i.test(status),
      ...paymentRevision,
    });
  }
  return orders;
}

async function scanOrderHistoryForTrackAll(state = null) {
  if (!isOrderHistoryPage()) return false;
  await waitForPageReady();
  const tracking = state?.tracking || {};
  const orders = extractHistoryTrackOrders();
  const nextUrl = nextHistoryPageUrl();
  const runId = tracking.startedAt || tracking.resumedAt || "";
  const signature = `${runId}|${location.href}|${orders.map((order) => order.amazon_order_id).join(",")}`;
  if (window.__nutricityLastTrackAllHistorySignature === signature) return true;
  window.__nutricityLastTrackAllHistorySignature = signature;
  showPanel("Nutricity tracking", `Track all found ${orders.length} order(s) on this page.`);
  const response = await send({ type: "ORDER_HISTORY_TRACK_ALL_PAGE", orders, nextUrl, runId });
  if (response && response.ok === false) {
    showPanel("Nutricity tracking", responseError(response, "Could not send order-history page to the tracker."));
  } else if (response?.ok) {
    showPanel("Nutricity tracking", `Queued ${response.added || orders.length} order(s). Preparing app matches in background.`);
  }
  return true;
}

function parsePaymentRevision() {
  const alertBlocks = [
    ...document.querySelectorAll("[data-component='alerts'], .a-alert .a-box-inner, .a-alert-heading, .a-alert-content, .od-status-message, .a-color-error.a-text-bold, #deliveryHasAnAlert, #deliveryItemList"),
  ];
  const matchedBlock = alertBlocks.find((element) => {
    const text = clean(element.textContent);
    return /payment revision needed/i.test(text) || /please update your payment method/i.test(text);
  });
  const root = document.querySelector("#orderDetails, [data-component='shipments'], .a-box-group") || document.body;
  const scopedText = clean(
    matchedBlock?.textContent ||
    alertBlocks.map((element) => element.textContent || "").join(" ") ||
    root.textContent ||
    "",
  ).slice(0, 10000);
  const text = clean(matchedBlock?.textContent || scopedText);
  const reviseLink = document.querySelector("a[href*='/cpe/revisepayments'], a[href*='revisepayments']");
  const needed = Boolean(matchedBlock)
    || /payment revision needed/i.test(scopedText)
    || /please update your payment method/i.test(scopedText)
    || /revise payment/i.test(clean(reviseLink?.textContent || ""));
  return {
    paymentRevisionNeeded: needed,
    paymentRevisionUrl: reviseLink ? absoluteUrl(reviseLink.getAttribute("href") || "") : "",
    pageText: needed ? text.slice(0, 2000) : "",
  };
}

function parseOrderCancellation() {
  const alert = [...document.querySelectorAll(".a-alert-heading, .a-alert-content, .a-alert .a-box-inner")]
    .find((element) => /order has been cancell?ed|order was cancell?ed|this order has been cancell?ed|order cancell?ed|cancell?ed order/i.test(clean(element.textContent)));
  const root = document.querySelector("#orderDetails, [data-component='shipments'], .a-box-group") || document.body;
  const scopedText = clean(alert?.textContent || root.textContent || "").slice(0, 10000);
  const cancelled = Boolean(alert) || /order has been cancell?ed|order was cancell?ed|this order has been cancell?ed|order cancell?ed|cancell?ed order/i.test(scopedText);
  return {
    orderCancelled: cancelled,
    cancellationMessage: cancelled ? clean(alert?.textContent || "This order has been cancelled.") : "",
  };
}

function asinsFrom(root) {
  return productItemsFrom(root).map((item) => item.asin);
}

function productItemsFrom(root, limit = 50) {
  const products = [];
  const seen = new Set();
  if (!root) return products;
  const links = [...root.querySelectorAll("a[href*='/dp/'], a[href*='/gp/product/']")].slice(0, Math.max(1, Number(limit || 50)) * 4);
  for (const link of links) {
    const href = link.getAttribute("href") || "";
    const match = href.match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})/i);
    const asin = match ? match[1].toUpperCase() : "";
    if (!asin || seen.has(asin)) continue;
    seen.add(asin);
    const itemBox = link.closest("[data-component='purchasedItems'], .a-fixed-left-grid, .a-row") || link.parentElement || root;
    const image = itemBox.querySelector("img") || link.querySelector("img");
    const title =
      clean(link.textContent) ||
      clean(image?.getAttribute("alt")) ||
      clean(itemBox.querySelector("[data-component='itemTitle'] a")?.textContent);
    products.push({
      asin,
      url: absoluteUrl(href),
      title,
      image_url: image ? absoluteUrl(image.getAttribute("data-a-hires") || image.getAttribute("src") || "") : "",
    });
    if (products.length >= Number(limit || 50)) break;
  }
  return products;
}

function shipmentRootForTrackingLink(link) {
  const rightGrid = link.closest("[data-component='shipmentsRightGrid'], [data-component='shipmentConnections']");
  const shipmentGrid = rightGrid?.closest(".a-fixed-right-grid-inner");
  if (shipmentGrid) return shipmentGrid;
  const shipmentComponent = link.closest("[data-component='shipments']");
  if (shipmentComponent) return shipmentComponent;
  return link.closest(".a-box, [data-component='orderCard']") || document.body;
}

async function parseOrderDetails() {
  const amazonOrderId = currentOrderId();
  const paymentRevision = parsePaymentRevision();
  const cancellation = parseOrderCancellation();
  const links = [...document.querySelectorAll("a[href*='ship-track'], a[href*='/gp/your-account/ship-track']")]
    .filter((link) => {
      const text = clean(link.textContent);
      const href = link.getAttribute("href") || "";
      const url = new URL(href, location.origin);
      return url.searchParams.get("orderID") || url.searchParams.get("orderId") || /track package|tracking/i.test(text);
    });
  const packages = [];
  const seen = new Set();
  for (const link of links) {
    const href = absoluteUrl(link.getAttribute("href") || "");
    if (seen.has(href)) continue;
    seen.add(href);
    const box = shipmentRootForTrackingLink(link);
    const status = clean(box.querySelector(".od-status-message, [data-component='shipmentStatus'] h4")?.textContent);
    const products = productItemsFrom(box, 20);
    const asins = products.map((item) => item.asin);
    packages.push(withPromiseDetails({
      tracking_url: href,
      order_status: status,
      promise: status,
      asins,
      products,
    }));
  }
  const productsByAsin = new Map();
  for (const pkg of packages) {
    for (const product of pkg.products || []) {
      if (product.asin && !productsByAsin.has(product.asin)) productsByAsin.set(product.asin, product);
    }
  }
  const orderRoot = document.querySelector("#orderDetails, [data-component='shipments'], .a-box-group") || document.body;
  const products = productsByAsin.size ? [...productsByAsin.values()] : productItemsFrom(orderRoot, 20);
  const orderStatus = clean(document.querySelector(".od-status-message, [data-component='shipmentStatus'] h4")?.textContent);
  return { amazonOrderId, amazonOrderUrl: location.href, packages, products, orderStatus, ...promiseDetails(orderStatus), ...paymentRevision, ...cancellation };
}

function parseCarrierAndTrackingId() {
  const deliveryCard = document.querySelector(".delivery-card, .pt-delivery-card-wrapper") || document.body;
  const carrier =
    clean(deliveryCard.querySelector("h3")?.textContent) ||
    clean(document.querySelector(".tracking-event-carrier-header h2")?.textContent);
  const trackingText =
    clean(document.querySelector(".pt-delivery-card-trackingId")?.textContent) ||
    clean(document.querySelector(".tracking-event-trackingId-text h4")?.textContent);
  const trackingMatch = trackingText.match(/Tracking ID:\s*(.+)$/i);
  return { carrier, tracking_id: trackingMatch ? trackingMatch[1].trim() : trackingText.replace(/^Tracking ID:\s*/i, "") };
}

function parseStatus() {
  return (
    clean(document.querySelector(".pt-status-main-status")?.textContent) ||
    clean(document.querySelector(".pt-promise-main-slot")?.textContent) ||
    clean(document.querySelector(".od-status-message")?.textContent) ||
    "Unknown"
  );
}

function parsePromise() {
  return clean(document.querySelector(".pt-promise-main-slot")?.textContent);
}

function parseEvents() {
  const container = document.querySelector("#tracking-events-container");
  if (!container) return [];
  const events = [];
  let currentDate = "";
  for (const node of [...container.querySelectorAll(".tracking-event-date, .tracking-event-message")]) {
    if (node.classList.contains("tracking-event-date")) {
      currentDate = clean(node.textContent);
      continue;
    }
    const row = node.closest(".a-row.a-spacing-large, .a-row");
    events.push({
      date: currentDate,
      time: clean(row?.querySelector(".tracking-event-time")?.textContent),
      message: clean(node.textContent),
      location: clean(row?.querySelector(".tracking-event-location")?.textContent),
    });
  }
  return events.filter((event) => event.message);
}

async function openTrackingEvents() {
  const trigger = document.querySelector(".tracking-events-modal-trigger, a[data-displayablelabel='See all updates']");
  if (trigger) {
    trigger.scrollIntoView({ block: "center" });
    await sleep(100);
    trigger.click();
    for (let i = 0; i < 6; i += 1) {
      await sleep(250);
      if (document.querySelector("#tracking-events-container .tracking-event-message")) break;
    }
  }
}

function shouldOpenTrackingEvents(status, carrierInfo = {}) {
  if (clean(carrierInfo.tracking_id) || clean(carrierInfo.carrier)) return true;
  return /shipped|transit|out for delivery|delivered|delayed|attempted|carrier|tracking/i.test(status || "");
}

async function parseTrackingPage() {
  const initialStatus = parseStatus();
  const initialCarrierInfo = parseCarrierAndTrackingId();
  if (shouldOpenTrackingEvents(initialStatus, initialCarrierInfo)) {
    await openTrackingEvents();
  }
  const amazonOrderId = currentOrderId();
  const paymentRevision = parsePaymentRevision();
  const events = parseEvents();
  const carrierInfo = parseCarrierAndTrackingId();
  const status = parseStatus();
  const deliveryCard = document.querySelector(".delivery-card, .pt-delivery-card-wrapper, #primaryStatus, #tracking-events-container") || document.body;
  const products = productItemsFrom(deliveryCard);
  const hasTrackingIdentity = Boolean(clean(carrierInfo.carrier) || clean(carrierInfo.tracking_id));
  const statusOnly = !hasTrackingIdentity && !events.length;
  const productAsins = products.map((item) => item.asin).filter(Boolean);
  const pkg = {
    ...carrierInfo,
    status,
    promise: parsePromise() || status,
    latest_event: events[0] || null,
    events: events.slice(0, 20),
    tracking_url: location.href,
    asins: statusOnly ? [] : productAsins.length ? productAsins : asinsFrom(deliveryCard),
    products: statusOnly ? [] : products,
    status_only: statusOnly,
  };
  return { amazonOrderId, package: withPromiseDetails(pkg), ...paymentRevision };
}

async function run() {
  if (!extensionContextAlive) return;
  if (!/amazon\.com$/i.test(location.hostname)) return;
  const runSignature = location.href;
  const now = Date.now();
  if (
    window.__nutricityLastRunSignature === runSignature
    && now - Number(window.__nutricityLastRunAt || 0) < 15000
  ) {
    return;
  }
  window.__nutricityLastRunSignature = runSignature;
  window.__nutricityLastRunAt = now;
  showPanel("Nutricity tracking", `Scanning Amazon page: ${location.pathname}`);
  if (isTrackingPage()) {
    await waitForTrackingPageReady();
    const data = await parseTrackingPage();
    if (!data.amazonOrderId) return;
    showPanel("Nutricity tracking", `Capturing tracking for ${data.amazonOrderId}: ${data.package.status}.`);
    const response = await sendWithTimeout({ type: "PACKAGE_TRACKING", ...data }, 12000);
    if (response?.ignored) {
      showPanel("Nutricity tracking", response.message || "Headless tracking mode is active; visible Amazon pages are ignored.");
    } else if (response?.ok) {
      const recovered = response.recovered ? " Recovered from a stale queue and posted directly." : "";
      showPanel("Nutricity tracking", `Synced tracking for ${data.amazonOrderId}: ${data.package.status}.${recovered}`);
    } else {
      showPanel("Nutricity tracking", processingMessage(response, "Captured the page, but could not post tracking to the app."));
      if (response?.timedOut) {
        await nudgeStuckTrackingPage(data, "Tracking page stayed busy after retry");
      }
    }
    return;
  }
  await waitForPageReady();
  if (isOrderHistoryPage()) {
    const state = await send({ type: "GET_STATE" });
    if (state?.tracking?.running && state.tracking.source === "history") {
      await scanOrderHistoryForTrackAll(state);
    }
    return;
  }
  if (isOrderDetailsLikePage()) {
    const data = await parseOrderDetails();
    void logContent(`Order details parsed for ${data.amazonOrderId || "unknown"}: ${data.packages?.length || 0} package link(s), cancelled=${Boolean(data.orderCancelled)}, paymentRevision=${Boolean(data.paymentRevisionNeeded)}.`);
    if (!data.amazonOrderId) {
      const state = await send({ type: "GET_STATE" });
      const activeOrder = state?.tracking?.currentOrder || state?.tracking?.orders?.[Number(state?.tracking?.index || 0)] || null;
      data.amazonOrderId = activeOrder?.amazon_order_id || "";
    }
    if (!data.amazonOrderId) return;
    showPanel(
      "Nutricity tracking",
      data.orderCancelled
        ? `Cancelled order detected for ${data.amazonOrderId}. Moving to next order.`
        : data.paymentRevisionNeeded
          ? `Payment revision needed for ${data.amazonOrderId}. Reporting to the app.`
        : `Found ${data.packages.length} package link(s) for ${data.amazonOrderId}.`,
    );
    const response = await sendWithTimeout({ type: "ORDER_PACKAGES", ...data }, 15000);
    if (data.orderCancelled) {
      window.setTimeout(() => {
        void send({
          type: "FORCE_ADVANCE_HISTORY_ORDER",
          amazonOrderId: data.amazonOrderId,
          status: "cancelled",
          reason: data.cancellationMessage || "Cancelled order page",
        });
      }, 5000);
    }
    if (response?.ignored) {
      showPanel("Nutricity tracking", response.message || "Headless tracking mode is active; visible Amazon pages are ignored.");
    } else if (response && response.ok === false) {
      showPanel("Nutricity tracking", responseError(response, "Could not send package links to the app."));
    }
    return;
  }
}

window.__nutricityTrackingRunning = true;
run().catch((error) => {
  showPanel("Nutricity tracking", `Content script error: ${error.message}`);
  void logContent(`Content script error on ${location.href}: ${error.message}`);
}).finally(() => {
  window.__nutricityTrackingRunning = false;
});

if (!window.__nutricityRunContentListener) {
  window.__nutricityRunContentListener = true;
  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "NUTRICITY_RUN_CONTENT") return false;
    extensionContextAlive = true;
    window.__nutricityTrackingRunning = true;
    run().catch((error) => {
      showPanel("Nutricity tracking", `Content script error: ${error.message}`);
      void logContent(`Content script error on ${location.href}: ${error.message}`);
    }).finally(() => {
      window.__nutricityTrackingRunning = false;
    });
    sendResponse({ ok: true });
    return false;
  });
}

if (!window.__nutricityTrackAllWatcher) {
  window.__nutricityTrackAllWatcher = setInterval(async () => {
    if (!extensionContextAlive) return;
    try {
      const state = await send({ type: "GET_STATE" });
      if (!(state?.tracking?.running && state.tracking.source === "history")) return;
      if (isOrderHistoryPage()) {
        await scanOrderHistoryForTrackAll(state);
        return;
      }
      if (isOrderDetailsLikePage()) {
        const data = await parseOrderDetails();
        if (data.amazonOrderId && data.orderCancelled) {
          const signature = `${state.tracking.startedAt || ""}|cancelled|${data.amazonOrderId}|${location.href}`;
          if (window.__nutricityLastCancelledOrderSignature === signature) return;
          window.__nutricityLastCancelledOrderSignature = signature;
          showPanel("Nutricity tracking", `Cancelled order detected for ${data.amazonOrderId}. Moving to next order.`);
          const response = await sendWithTimeout({ type: "ORDER_PACKAGES", ...data }, 15000);
          if (!response?.ok) {
            await send({
              type: "FORCE_ADVANCE_HISTORY_ORDER",
              amazonOrderId: data.amazonOrderId,
              status: "cancelled",
              reason: data.cancellationMessage || "Cancelled order page",
            });
          }
        }
        return;
      }
      const cancellation = parseOrderCancellation();
      const activeOrderId = state.tracking.currentOrder?.amazon_order_id || currentOrderId();
      if (activeOrderId && cancellation.orderCancelled) {
        const signature = `${state.tracking.startedAt || ""}|generic-cancelled|${activeOrderId}|${location.href}`;
        if (window.__nutricityLastCancelledOrderSignature === signature) return;
        window.__nutricityLastCancelledOrderSignature = signature;
        showPanel("Nutricity tracking", `Cancelled order detected for ${activeOrderId}. Moving to next order.`);
        await send({
          type: "FORCE_ADVANCE_HISTORY_ORDER",
          amazonOrderId: activeOrderId,
          status: "cancelled",
          reason: cancellation.cancellationMessage || "Cancelled order page",
        });
      }
      if (isTrackingPage()) {
        const data = await parseTrackingPage();
        const activeOrderId = state.tracking.currentOrder?.amazon_order_id || state.tracking.orders?.[Number(state.tracking.index || 0)]?.amazon_order_id || "";
        if (!data.amazonOrderId || String(data.amazonOrderId) !== String(activeOrderId || "")) return;
        const signature = `${state.tracking.startedAt || ""}|tracking|${data.amazonOrderId}|${location.href}|${state.tracking.lastActivityAt || ""}`;
        if (window.__nutricityLastTrackingRetrySignature === signature) return;
        const lastActivityAt = Number(state.tracking.lastActivityAt || state.tracking.updatedAt || 0);
        if (lastActivityAt && Date.now() - lastActivityAt > 20000) {
          window.__nutricityLastTrackingRetrySignature = signature;
          showPanel("Nutricity tracking", `Retrying stalled tracking page for ${data.amazonOrderId}.`);
          const response = await sendWithTimeout({ type: "PACKAGE_TRACKING", ...data, retry: true }, 20000);
          if (!response?.ok && response?.timedOut) {
            await send({
              type: "FORCE_ADVANCE_HISTORY_ORDER",
              amazonOrderId: data.amazonOrderId,
              status: "failed",
              reason: "Tracking page watcher retry timed out",
            });
          }
        }
      }
    } catch (_) {
      // The background service worker can sleep between scans; the next tick will retry.
    }
  }, 2500);
}
})();
