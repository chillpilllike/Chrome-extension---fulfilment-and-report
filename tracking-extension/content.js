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

function clean(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
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

async function waitForPageReady() {
  const started = Date.now();
  while (document.readyState !== "complete" && Date.now() - started < 25000) {
    await sleep(250);
  }
  await sleep(1200);
}

function currentOrderId() {
  const url = new URL(location.href);
  return url.searchParams.get("orderID") || url.searchParams.get("orderId") || "";
}

function absoluteUrl(href) {
  return new URL(href, location.origin).href;
}

function parsePaymentRevision() {
  const text = clean(document.querySelector("[data-component='alerts']")?.textContent || document.body.textContent || "");
  const needed = /payment revision needed/i.test(text) || /please update your payment method/i.test(text);
  const reviseLink = document.querySelector("a[href*='/cpe/revisepayments'], a[href*='revisepayments']");
  return {
    paymentRevisionNeeded: needed,
    paymentRevisionUrl: reviseLink ? absoluteUrl(reviseLink.getAttribute("href") || "") : "",
    pageText: needed ? text.slice(0, 2000) : "",
  };
}

function parseOrderCancellation() {
  const alert = [...document.querySelectorAll(".a-alert-heading, .a-alert-content, .a-box-inner")]
    .find((element) => /order has been cancelled|order was cancelled|this order has been canceled|this order has been cancelled/i.test(clean(element.textContent)));
  const pageText = clean(document.body.textContent || "");
  const cancelled = Boolean(alert) || /this order has been cancelled|this order has been canceled/i.test(pageText);
  return {
    orderCancelled: cancelled,
    cancellationMessage: cancelled ? clean(alert?.textContent || "This order has been cancelled.") : "",
  };
}

function asinsFrom(root) {
  return [...root.querySelectorAll("a[href*='/dp/'], a[href*='/gp/product/']")]
    .map((link) => {
      const href = link.getAttribute("href") || "";
      const match = href.match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})/i);
      return match ? match[1].toUpperCase() : "";
    })
    .filter(Boolean);
}

function parseOrderDetails() {
  const amazonOrderId = currentOrderId();
  const paymentRevision = parsePaymentRevision();
  const cancellation = parseOrderCancellation();
  const links = [...document.querySelectorAll("a[href*='ship-track'][href*='orderId=']")];
  const packages = [];
  const seen = new Set();
  for (const link of links) {
    const href = absoluteUrl(link.getAttribute("href") || "");
    if (seen.has(href)) continue;
    seen.add(href);
    const box = link.closest(".a-box, [data-component='shipments'], [data-component='orderCard']") || document.body;
    const status = clean(box.querySelector(".od-status-message, [data-component='shipmentStatus'] h4")?.textContent);
    const asins = [...new Set(asinsFrom(box))];
    packages.push({
      tracking_url: href,
      order_status: status,
      promise: status,
      asins,
    });
  }
  const orderStatus = clean(document.querySelector(".od-status-message, [data-component='shipmentStatus'] h4")?.textContent);
  return { amazonOrderId, packages, orderStatus, promise: orderStatus, ...paymentRevision, ...cancellation };
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
    await sleep(300);
    trigger.click();
    for (let i = 0; i < 20; i += 1) {
      await sleep(300);
      if (document.querySelector("#tracking-events-container .tracking-event-message")) break;
    }
  }
}

async function parseTrackingPage() {
  await openTrackingEvents();
  const amazonOrderId = currentOrderId();
  const paymentRevision = parsePaymentRevision();
  const events = parseEvents();
  const carrierInfo = parseCarrierAndTrackingId();
  const status = parseStatus();
  const pkg = {
    ...carrierInfo,
    status,
    promise: parsePromise(),
    latest_event: events[0] || null,
    events: events.slice(0, 20),
    tracking_url: location.href,
    asins: asinsFrom(document.body),
  };
  return { amazonOrderId, package: pkg, ...paymentRevision };
}

async function run() {
  if (!extensionContextAlive) return;
  await waitForPageReady();
  if (!/amazon\.com$/i.test(location.hostname)) return;
  if (/order-details/i.test(location.href)) {
    const data = parseOrderDetails();
    if (!data.amazonOrderId) return;
    showPanel("Nutricity tracking", `Found ${data.packages.length} package link(s) for ${data.amazonOrderId}.`);
    await send({ type: "ORDER_PACKAGES", ...data });
    return;
  }
  if (/ship-track/i.test(location.href)) {
    const data = await parseTrackingPage();
    if (!data.amazonOrderId) return;
    showPanel("Nutricity tracking", `Captured tracking for ${data.amazonOrderId}: ${data.package.status}.`);
    await send({ type: "PACKAGE_TRACKING", ...data });
  }
}

if (!window.__nutricityTrackingRunning) {
  window.__nutricityTrackingRunning = true;
  run().finally(() => {
    window.__nutricityTrackingRunning = false;
  });
}
