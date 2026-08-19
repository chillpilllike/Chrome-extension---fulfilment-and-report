(() => {
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const TRACKING_WATCHER_INTERVAL_MS = 15000;
const TRACKING_RETRY_AFTER_MS = 120000;
const ORDER_PAGE_RECHECK_AFTER_MS = 90000;

let extensionContextAlive = true;

function backgroundTimeoutMessage(message = {}) {
  const type = String(message.type || "message");
  return `Nutricity Tracking background did not respond to ${type}. Reload the extension or stop/start tracking if this keeps repeating.`;
}

async function send(message, timeoutMs = 12000) {
  if (!extensionContextAlive) return null;
  return new Promise((resolve, reject) => {
    let finished = false;
    const finish = (callback) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      callback();
    };
    const timer = setTimeout(() => {
      finish(() => resolve({
        ok: false,
        timedOut: true,
        backgroundTimeout: true,
        message: backgroundTimeoutMessage(message),
      }));
    }, Math.max(3000, Number(timeoutMs || 12000)));
    try {
      chrome.runtime.sendMessage(message, (response) => {
        const lastError = chrome.runtime.lastError;
        finish(() => {
          if (lastError) {
            if (/Extension context invalidated/i.test(String(lastError.message || lastError))) {
              extensionContextAlive = false;
              resolve(null);
              return;
            }
            resolve({ ok: false, message: lastError.message || String(lastError) });
            return;
          }
          resolve(response);
        });
      });
    } catch (error) {
      finish(() => {
        if (/Extension context invalidated/i.test(String(error?.message || error))) {
          extensionContextAlive = false;
          resolve(null);
          return;
        }
        reject(error);
      });
    }
  });
}

async function sendWithTimeout(message, timeoutMs = 15000) {
  const response = await send(message, timeoutMs);
  if (response?.timedOut) {
    return {
      ...response,
      message: "Syncing this Amazon tracking page with the app. This can take a moment when package matches are being repaired.",
    };
  }
  return response;
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
  }, 45000);
}

function logContent(message) {
  return send({ type: "CONTENT_LOG", message }).catch(() => null);
}

function clean(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function cleanProductTitle(text) {
  let value = String(text || "");
  const altMatch = value.match(/\balt=(["'])(.*?)\1/i);
  if (altMatch?.[2]) value = altMatch[2];
  value = value
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">");
  return clean(value);
}

function blockedProductCandidate(element) {
  if (!element) return true;
  if (element.closest?.("[aria-label*='Sponsored'], [data-cel-widget*='sponsored'], [data-csa-c-slot-id*='sponsored'], [class*='sponsored'], [id*='sponsored'], [id*='rhf'], [id*='ape_'], [id*='sp_']")) {
    return true;
  }
  const text = clean(element.textContent || "").slice(0, 1500);
  return /sponsored|advertisement|business credit account|amazon business card|customers also bought|related to this item|recommended for you|inspired by your|shop more deals|featured offer/i.test(text);
}

function physicalTrackingId(text) {
  if (/^https?:\/\//i.test(clean(text))) return "";
  const value = clean(text).toUpperCase().replace(/[^A-Z0-9-]/g, "");
  if (/^TBA[A-Z0-9]+$/.test(value)) return value;
  if (/^1Z[A-Z0-9]{12,24}$/.test(value)) return value;
  if (/^SG\d{10,24}$/.test(value)) return value;
  if (/^D\d{10,24}$/.test(value)) return value;
  if (/^\d{12,30}$/.test(value)) return value;
  return "";
}

function trackingIdFromText(text) {
  const raw = clean(text);
  const patterns = [
    /\bTracking\s+(?:ID|number|#)\s*:?\s*([A-Z0-9-]{10,40})\b/i,
    /\bCarrier\s+tracking\s+(?:ID|number|#)\s*:?\s*([A-Z0-9-]{10,40})\b/i,
    /\b(?:USPS|UPS|FedEx|Amazon)\s+(?:tracking\s+)?(?:ID|number|#)\s*:?\s*([A-Z0-9-]{10,40})\b/i,
    /\b(TBA[A-Z0-9]{8,30})\b/i,
    /\b(1Z[A-Z0-9]{12,24})\b/i,
    /\b(SG\d{10,24})\b/i,
    /\b(D\d{10,24})\b/i,
    /\b(\d{12,30})\b/,
  ];
  for (const pattern of patterns) {
    const match = raw.match(pattern);
    const id = physicalTrackingId(match?.[1] || "");
    if (id) return id;
  }
  return physicalTrackingId(raw);
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
      if (!/\bdelivered\b/i.test(promise) && !monthMatch[3] && expected < new Date(base.getFullYear(), base.getMonth(), base.getDate() - 1)) {
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

async function waitForOrderHistoryReady() {
  await waitForPageReady();
  const started = Date.now();
  while (Date.now() - started < 12000) {
    const text = document.body?.innerText || "";
    const orderIds = text.match(/\b\d{3}-\d{7}-\d{7}\b/g) || [];
    const detailLinks = document.querySelectorAll("a[href*='orderID='], a[href*='orderId='], a[href*='order-details']").length;
    if (amazonHistoryUnavailableMessage()) break;
    if (detailLinks > 0 && orderIds.length > 0 && /Order placed|Buy it again|View order details|Track package/i.test(text)) break;
    await sleep(300);
  }
  await sleep(300);
}

function isTrackingPage() {
  return /ship-track|progress-tracker\/package/i.test(location.href);
}

async function waitForTrackingPageReady() {
  const started = Date.now();
  while (Date.now() - started < 8000) {
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

function isRelevantTrackingPage() {
  return isTrackingPage() || isOrderHistoryPage() || isOrderDetailsLikePage();
}

function activeTrackingOrderId(state = {}) {
  const tracking = state.tracking || {};
  return tracking.currentOrder?.amazon_order_id || tracking.orders?.[Number(tracking.index || 0)]?.amazon_order_id || "";
}

function activeTrackingUrl(state = {}) {
  return String(state.tracking?.currentUrl || "");
}

function orderDetailsLikeUrl(value = "") {
  try {
    const url = new URL(value, location.href);
    return /\/(your-orders\/order-details|your-orders\/order|gp\/your-account\/order|gp\/your-account\/order-details|gp\/css\/summary)/i.test(url.pathname);
  } catch {
    return /order-details|your-orders\/order|gp\/your-account\/order|gp\/css\/summary/i.test(String(value || ""));
  }
}

function trackingLikeUrl(value = "") {
  try {
    const url = new URL(value, location.href);
    return /\/(gp\/your-account\/ship-track|progress-tracker\/package)/i.test(url.pathname);
  } catch {
    return /ship-track|progress-tracker\/package/i.test(String(value || ""));
  }
}

function currentPageMatchesActiveTracking(state = {}) {
  if (!state?.tracking?.running) return false;
  const activeTabId = Number(state.tracking.trackingTabId || state.tracking.autoTrackingTabId || 0);
  const senderTabId = Number(state.senderTabId || 0);
  if (activeTabId && senderTabId && activeTabId !== senderTabId) return true;
  const activeUrl = activeTrackingUrl(state);
  if (!activeUrl) return true;
  try {
    const active = new URL(activeUrl);
    const here = new URL(location.href);
    if (active.origin !== here.origin) return false;
    const activeOrder = active.searchParams.get("orderID") || active.searchParams.get("orderId") || "";
    const hereOrder = here.searchParams.get("orderID") || here.searchParams.get("orderId") || "";
    if (activeOrder && hereOrder && activeOrder === hereOrder && orderDetailsLikeUrl(active.href) && orderDetailsLikeUrl(here.href)) return true;
    if (activeOrder && hereOrder && activeOrder === hereOrder && trackingLikeUrl(active.href) && trackingLikeUrl(here.href)) return true;
    if (active.pathname.replace(/\/+$/, "") !== here.pathname.replace(/\/+$/, "")) return false;
    if (activeOrder && hereOrder && activeOrder !== hereOrder) return false;
    return true;
  } catch {
    return true;
  }
}

async function recoverActiveTrackingPage(state = {}, reason = "stale Amazon page") {
  const activeOrderId = activeTrackingOrderId(state);
  showPanel("Nutricity tracking", activeOrderId ? `Recovering active order ${activeOrderId}.` : "Recovering active tracking run.");
  const response = await send({
    type: "RECOVER_ACTIVE_TRACKING_PAGE",
    amazonOrderId: currentOrderId(),
    pageUrl: location.href,
    reason,
  });
  if (response?.redirectUrl && response.redirectUrl !== location.href) {
    window.location.assign(response.redirectUrl);
  }
}

function followRecoveryRedirect(response = {}) {
  const redirectUrl = String(response?.redirectUrl || "").trim();
  if (!redirectUrl || redirectUrl === location.href) return false;
  showPanel("Nutricity tracking", response.message || "Recovering active tracking page.");
  window.location.assign(redirectUrl);
  return true;
}

function trackAllProgressMessage(tracking = {}) {
  if (!(tracking?.running && tracking.source === "history")) return "";
  const completed = Number(tracking.completedOrderIds?.length || 0);
  const failed = Number(tracking.failedOrderIds?.length || 0);
  const queue = Number(tracking.queue?.length || 0);
  const page = tracking.currentPage ? `page ${tracking.currentPage}` : "order history";
  const currentOrder = tracking.currentOrder?.amazon_order_id || tracking.currentOrderId || "";
  const lastMessage = clean(tracking.lastMessage || "");
  const current = currentOrder ? ` Current ${currentOrder}.` : "";
  return `${lastMessage || `Track all is running on ${page}.`}${current} Queue ${queue}; checked ${completed}; failed ${failed}.`;
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
    const existing = byAsin.get(product.asin) || { ...product, quantity: 0, quantity_verified: false };
    if (!existing.quantity_verified || product.quantity_verified) {
      existing.quantity = Math.max(Number(existing.quantity || 0), Number(product.quantity || 1));
    }
    existing.quantity_verified = existing.quantity_verified || product.quantity_verified === true;
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
  const next = [...document.querySelectorAll("li.a-last a, .a-pagination .a-last a")]
    .find((link) => {
      if (!visible(link)) return false;
      const parentText = clean(link.closest("li")?.textContent || link.textContent || "");
      const href = link.getAttribute("href") || link.href || "";
      return /\bnext\b|→/.test(parentText.toLowerCase()) || /pagination\/next|startIndex=/i.test(href);
    });
  if (next) return absoluteUrl(next.getAttribute("href") || next.href || "");
  return "";
}

function amazonHistoryUnavailableMessage() {
  const text = clean(document.body?.innerText || "");
  const unavailablePatterns = [
    /there'?s a problem displaying some of your orders right now/i,
    /problem displaying some of your orders/i,
    /try refreshing this page/i,
    /sorry[, ]+we are unable to display your orders/i,
    /we're sorry[, ]+something went wrong/i,
  ];
  const matched = unavailablePatterns.find((pattern) => pattern.test(text));
  if (!matched) return "";
  const heading = clean(document.querySelector("h1, h2, h3, h4, .a-alert-heading, .a-alert-content")?.textContent || "");
  return heading || "Amazon could not display this order-history page.";
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
  await waitForOrderHistoryReady();
  const tracking = state?.tracking || {};
  const unavailableMessage = amazonHistoryUnavailableMessage();
  if (unavailableMessage) {
    const runId = tracking.startedAt || tracking.resumedAt || "";
    const signature = `${runId}|history-unavailable|${location.href}|${unavailableMessage}`;
    if (window.__nutricityLastTrackAllHistorySignature === signature) return true;
    window.__nutricityLastTrackAllHistorySignature = signature;
    showPanel("Nutricity tracking", unavailableMessage);
    const response = await send({
      type: "ORDER_HISTORY_TRACK_ALL_PROBLEM",
      pageUrl: location.href,
      message: unavailableMessage,
      runId,
    });
    if (response?.message) showPanel("Nutricity tracking", response.message);
    return true;
  }
  let orders = extractHistoryTrackOrders();
  if (!orders.length) {
    showPanel("Nutricity tracking", "Amazon orders are still loading. Checking this history page again.");
    await sleep(1800);
    await waitForOrderHistoryReady();
    orders = extractHistoryTrackOrders();
  }
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
    const added = Number(response.added ?? orders.length);
    if (added > 0) {
      showPanel("Nutricity tracking", `Queued ${added} order(s). Preparing app matches in background.`);
    } else if (orders.length > 0) {
      showPanel("Nutricity tracking", `Found ${orders.length} order(s), but no new orders needed queueing on this page. Moving to the next step.`);
    } else {
      showPanel("Nutricity tracking", "No Amazon orders were ready on this page after retry. Moving to the next step.");
    }
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

function orderDetailsStatusFallback(root = document.body) {
  const shipmentText = clean([...document.querySelectorAll("[data-component='shipments']")]
    .map((node) => node.innerText || node.textContent || "")
    .join(" "));
  if (/cancel items/i.test(shipmentText) && /buy it again|view your subscribe|print packing slip|sold by/i.test(shipmentText)) return "Order received";
  const text = clean(root?.innerText || root?.textContent || document.body?.innerText || "");
  if (/cancel items/i.test(text) && /buy it again|view your subscribe|print packing slip|sold by/i.test(text)) return "Order received";
  if (/order placed/i.test(text) && /ship to/i.test(text) && !/\b(track package|ship-track|out for delivery|delivered)\b/i.test(shipmentText)) return "Order received";
  const statusMatch = text.match(/\b(Arriving\s+(?:today|tomorrow|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)|Out for delivery|Delivered(?:\s+\w+\s+\d{1,2})?|Shipped|Preparing for shipment|Not yet shipped|Order received|Running late|Delayed)\b/i);
  if (statusMatch) return clean(statusMatch[1]);
  return "";
}

function productItemsFrom(root, limit = 50) {
  const products = [];
  const seen = new Set();
  if (!root) return products;

  function productTitleCandidate(value) {
    const text = cleanProductTitle(value);
    if (!text) return "";
    if (text.length < 4) return "";
    if (/^\d+$/.test(text)) return "";
    if (/^\$|^-\d+%|list price|out of 5 stars|buy it again|view your item|amazon business card|business credit account|sponsored|advertisement/i.test(text)) return "";
    return text;
  }

  function titleForProductBox(box, link, asin, image) {
    const sameAsinLinks = [
      ...box.querySelectorAll("a[href*='/dp/'], a[href*='/gp/product/']"),
    ]
      .filter((candidate) => {
        const href = candidate.getAttribute("href") || "";
        return new RegExp(`/(?:dp|gp/product)/${asin}`, "i").test(href);
      });
    const titleLink = sameAsinLinks.find((candidate) => productTitleCandidate(candidate.textContent));
    const imageTitle = image ? (
      productTitleCandidate(image.getAttribute("alt")) ||
      productTitleCandidate(image.getAttribute("aria-label")) ||
      productTitleCandidate(image.getAttribute("title"))
    ) : "";
    return (
      productTitleCandidate(box.querySelector("[data-component='itemTitle'] a")?.textContent) ||
      productTitleCandidate(titleLink?.textContent) ||
      imageTitle ||
      productTitleCandidate(link.getAttribute("aria-label")) ||
      productTitleCandidate(link.getAttribute("title")) ||
      productTitleCandidate(link.textContent)
    );
  }

  function addFromBox(box) {
    if (!box || blockedProductCandidate(box)) return;
    const link =
      box.querySelector("[data-component='itemTitle'] a[href*='/dp/'], [data-component='itemTitle'] a[href*='/gp/product/']") ||
      box.querySelector("a[href*='/dp/'], a[href*='/gp/product/']");
    if (!link || blockedProductCandidate(link)) return;
    const href = link.getAttribute("href") || "";
    const match = href.match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})/i);
    const asin = match ? match[1].toUpperCase() : "";
    if (!asin || seen.has(asin)) return;
    const image = box.querySelector("img") || link.querySelector("img");
    const title = titleForProductBox(box, link, asin, image);
    const imageUrl = image ? absoluteUrl(
      image.getAttribute("data-a-hires") ||
      image.getAttribute("data-src") ||
      image.getAttribute("src") ||
      "",
    ) : "";
    if (!title || !imageUrl) return;
    const quantityNode = box.querySelector(".od-item-view-qty, .itemQuantity, [data-quantity]");
    const quantityValue = quantityNode?.getAttribute?.("data-quantity") || quantityNode?.textContent || box.getAttribute?.("data-quantity") || "";
    const explicitQuantity = String(quantityValue).match(/\d+/)?.[0];
    seen.add(asin);
    products.push({
      asin,
      url: absoluteUrl(href),
      title,
      image_url: imageUrl,
      quantity: Math.max(1, Math.round(Number(explicitQuantity || 1))),
      quantity_verified: Boolean(explicitQuantity),
    });
  }

  const itemBoxes = [...root.querySelectorAll("[data-component='purchasedItems']")]
    .filter((box) => !blockedProductCandidate(box));
  for (const box of itemBoxes) {
    addFromBox(box);
    if (products.length >= Number(limit || 50)) break;
  }
  if (products.length < Number(limit || 50)) {
    const links = [...root.querySelectorAll("a[href*='/dp/'], a[href*='/gp/product/']")].slice(0, Math.max(1, Number(limit || 50)) * 4);
    for (const link of links) {
      const box = link.closest("[data-component='purchasedItems'], [data-component='item'], .od-item, .a-fixed-left-grid");
      if (!box || !root.contains(box) || blockedProductCandidate(box)) continue;
      addFromBox(box);
      if (products.length >= Number(limit || 50)) break;
    }
  }
  return products;
}

function shipmentRootForTrackingLink(link) {
  const shipmentComponent = link.closest("[data-component='shipments']");
  if (shipmentComponent) return shipmentComponent;
  const rightGrid = link.closest("[data-component='shipmentsRightGrid'], [data-component='shipmentConnections']");
  const shipmentGrid = rightGrid?.closest(".a-fixed-right-grid-inner");
  if (shipmentGrid) return shipmentGrid;
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
      amazon_order_id: amazonOrderId,
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
  if (packages.length === 1 && products.length && !(packages[0].products || []).length) {
    packages[0].products = products;
    packages[0].asins = products.map((item) => item.asin).filter(Boolean);
  }
  const orderStatus =
    clean(document.querySelector(".od-status-message, [data-component='shipmentStatus'] h4")?.textContent) ||
    orderDetailsStatusFallback(orderRoot);
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
  const trackingId = trackingIdFromText(trackingMatch ? trackingMatch[1] : trackingText)
    || trackingIdFromText(document.body?.innerText || "");
  return { carrier, tracking_id: trackingId };
}

function parseStatus() {
  const pageText = clean(document.body?.innerText || "");
  const headlineMatch = pageText.match(/\b(Now expected by\s+\w+\s+\d{1,2}|Was expected\s+\w+\s+\d{1,2}|Arriving\s+(?:today|tomorrow|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)|Out for delivery|Delivery attempted(?:\s+\w+\s+\d{1,2})?|Delivered(?:\s+\w+\s+\d{1,2})?|Shipped|Delayed|Running late)\b/i);
  const headline = headlineMatch ? clean(headlineMatch[1]) : "";
  const selectedStatus = (
    clean(document.querySelector(".pt-status-main-status")?.textContent) ||
    clean(document.querySelector(".pt-promise-main-slot")?.textContent) ||
    headline ||
    clean(document.querySelector(".od-status-message")?.textContent) ||
    "Unknown"
  );
  if (/delivered/i.test(selectedStatus) && /\b(arriving|out for delivery|delivery attempted|delayed|running late)\b/i.test(headline)) {
    return headline;
  }
  return selectedStatus;
}

function parsePromise() {
  const pageText = clean(document.body?.innerText || "");
  const headlineMatch = pageText.match(/\b(Now expected by\s+\w+\s+\d{1,2}|Was expected\s+\w+\s+\d{1,2}|Arriving\s+(?:today|tomorrow|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday))\b/i);
  return clean(document.querySelector(".pt-promise-main-slot")?.textContent) || (headlineMatch ? clean(headlineMatch[1]) : "");
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

function relatedOrderIdsFromPage() {
  const ids = new Set();
  const text = clean(document.body?.innerText || "");
  for (const match of text.matchAll(/\bOrder\s+ID\s+(\d{3}-\d{7}-\d{7})\b/gi)) {
    ids.add(match[1]);
  }
  for (const match of location.href.matchAll(/\b\d{3}-\d{7}-\d{7}\b/g)) {
    ids.add(match[0]);
  }
  return [...ids];
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

function parseOtpFromTrackingPage() {
  const selectors = [
    ".pt-notice-MAPS .a-alert-content",
    ".map-banner-card .a-alert-content",
    ".a-alert-content",
  ];
  for (const selector of selectors) {
    for (const node of document.querySelectorAll(selector)) {
      const text = clean(node.textContent || "");
      const match = text.match(/\b(?:one[-\s]?time\s+password|otp)\s+(?:is|:)?\s*(\d{4,8})\b/i)
        || text.match(/\bpassword\s+(?:is|:)\s*(\d{4,8})\b/i);
      if (match?.[1]) return match[1];
    }
  }
  const bodyText = clean(document.body?.innerText || "");
  const match = bodyText.match(/\b(?:one[-\s]?time\s+password|otp)\s+(?:is|:)?\s*(\d{4,8})\b/i)
    || bodyText.match(/\bpassword\s+(?:is|:)\s*(\d{4,8})\b/i);
  return match?.[1] || "";
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
  const otp = parseOtpFromTrackingPage();
  const deliveryCard = document.querySelector(".delivery-card, .pt-delivery-card-wrapper, #primaryStatus, #tracking-events-container") || document.body;
  const trackingProducts = productItemsFrom(deliveryCard);
  const products = trackingProducts;
  const hasTrackingIdentity = Boolean(clean(carrierInfo.carrier) || clean(carrierInfo.tracking_id));
  const statusOnly = !hasTrackingIdentity && !events.length && !products.length;
  const productAsins = products.map((item) => item.asin).filter(Boolean);
  const pkg = {
    amazon_order_id: amazonOrderId,
    ...carrierInfo,
    status,
    promise: parsePromise() || status,
    latest_event: events[0] || null,
    events: events.slice(0, 20),
    tracking_url: location.href,
    asins: statusOnly ? [] : productAsins,
    products: statusOnly ? [] : products,
    status_only: statusOnly,
    otp,
    otp_required: Boolean(otp),
    related_amazon_order_ids: relatedOrderIdsFromPage(),
  };
  return { amazonOrderId, package: withPromiseDetails(pkg), otp, ...paymentRevision };
}

async function run() {
  if (!extensionContextAlive) return;
  if (!/amazon\.com$/i.test(location.hostname)) return;
  if (!isRelevantTrackingPage()) return;
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
  const initialState = await send({ type: "GET_STATE" });
  if (!extensionContextAlive || initialState == null) {
    showPanel("Nutricity tracking", "Extension was reloaded while this Amazon page was open. Refresh this Amazon tab, then start or resume tracking.");
    return;
  }
  if (initialState?.timedOut) {
    showPanel("Nutricity tracking", initialState.message || "Nutricity Tracking background did not respond. Reload the extension and start tracking again.");
    return;
  }
  if (initialState?.tracking?.running && initialState.tracking.source === "history") {
    const progressMessage = trackAllProgressMessage(initialState.tracking);
    if (progressMessage) showPanel("Nutricity tracking", progressMessage);
  } else if (isOrderHistoryPage()) {
    showPanel("Nutricity tracking", "No active Track all run. Open Nutricity Tracking and start Track all from this Amazon order-history page.");
  }
  if (isTrackingPage()) {
    const state = initialState;
    if (state?.timedOut) {
      showPanel("Nutricity tracking", state.message || "Nutricity Tracking background did not respond. Reload the extension and start tracking again.");
      return;
    }
    if (!currentPageMatchesActiveTracking(state)) {
      await recoverActiveTrackingPage(state, "tracking page did not match the active run");
      return;
    }
    await waitForTrackingPageReady();
    const data = await parseTrackingPage();
    if (!data.amazonOrderId) return;
    showPanel("Nutricity tracking", `Capturing tracking for ${data.amazonOrderId}: ${data.package.status}.`);
    const response = await sendWithTimeout({ type: "PACKAGE_TRACKING", ...data }, 45000);
    if (followRecoveryRedirect(response)) return;
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
    const state = initialState;
    if (state?.timedOut) {
      showPanel("Nutricity tracking", state.message || "Nutricity Tracking background did not respond. Reload the extension and start Track all again.");
      return;
    }
    if (state?.tracking?.running && state.tracking.source === "history") {
      await scanOrderHistoryForTrackAll(state);
    }
    return;
  }
  if (isOrderDetailsLikePage()) {
    const state = initialState;
    if (state?.timedOut) {
      showPanel("Nutricity tracking", state.message || "Nutricity Tracking background did not respond. Reload the extension and start tracking again.");
      return;
    }
    if (state?.tracking?.running && !currentPageMatchesActiveTracking(state)) {
      await recoverActiveTrackingPage(state, "order details page did not match the active run");
      return;
    }
    const data = await parseOrderDetails();
    void logContent(`Order details parsed for ${data.amazonOrderId || "unknown"}: ${data.packages?.length || 0} package link(s), cancelled=${Boolean(data.orderCancelled)}, paymentRevision=${Boolean(data.paymentRevisionNeeded)}.`);
    if (!data.amazonOrderId) {
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
        : data.packages.length
          ? `Found ${data.packages.length} package link(s) for ${data.amazonOrderId}.`
          : `Captured order details for ${data.amazonOrderId}; no package tracking link yet.`,
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
      if (followRecoveryRedirect(response)) return;
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
    if (!isRelevantTrackingPage()) {
      clearInterval(window.__nutricityTrackAllWatcher);
      window.__nutricityTrackAllWatcher = null;
      return;
    }
    try {
      const state = await send({ type: "GET_STATE" });
      if (state?.timedOut) {
        showPanel("Nutricity tracking", state.message || "Nutricity Tracking background did not respond. Reload the extension and start Track all again.");
        return;
      }
      if (!(state?.tracking?.running && state.tracking.source === "history")) return;
      const progressMessage = trackAllProgressMessage(state.tracking);
      if (progressMessage) showPanel("Nutricity tracking", progressMessage);
      if (!currentPageMatchesActiveTracking(state)) {
        await recoverActiveTrackingPage(state, "watcher page did not match the active Track all run");
        return;
      }
      if (isOrderHistoryPage()) {
        await scanOrderHistoryForTrackAll(state);
        return;
      }
      if (isOrderDetailsLikePage()) {
        const lastOrderPageCheckAt = Number(window.__nutricityLastOrderPageWatcherAt || 0);
        if (Date.now() - lastOrderPageCheckAt < ORDER_PAGE_RECHECK_AFTER_MS) return;
        window.__nutricityLastOrderPageWatcherAt = Date.now();
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
      const activeOrderId = activeTrackingOrderId(state) || currentOrderId();
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
        const lastActivityAt = Number(state.tracking.lastActivityAt || state.tracking.updatedAt || 0);
        if (lastActivityAt && Date.now() - lastActivityAt > TRACKING_RETRY_AFTER_MS) {
          const data = await parseTrackingPage();
          const activeOrderId = activeTrackingOrderId(state);
          if (!data.amazonOrderId || String(data.amazonOrderId) !== String(activeOrderId || "")) return;
          const signature = `${state.tracking.startedAt || ""}|tracking|${data.amazonOrderId}|${location.href}|${state.tracking.lastActivityAt || ""}`;
          if (window.__nutricityLastTrackingRetrySignature === signature) return;
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
  }, TRACKING_WATCHER_INTERVAL_MS);
}
})();
