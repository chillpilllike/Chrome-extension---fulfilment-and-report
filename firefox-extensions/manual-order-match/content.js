/* Firefox build: prefer Firefox's Promise-based browser namespace. */
const chrome = globalThis.browser || globalThis.chrome;

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

function visible(element) {
  if (!element) return false;
  const style = getComputedStyle(element);
  const rect = element.getBoundingClientRect();
  return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
}

function textOf(element) {
  return (element?.innerText || element?.textContent || "").replace(/\s+/g, " ").trim();
}

function absoluteUrl(value) {
  try {
    return new URL(value, location.href).href;
  } catch {
    return "";
  }
}

function showPanel(title, message) {
  let panel = document.getElementById("nutricity-manual-match-panel");
  if (!panel) {
    panel = document.createElement("div");
    panel.id = "nutricity-manual-match-panel";
    document.documentElement.appendChild(panel);
  }
  panel.innerHTML = `<strong>${title}</strong><span>${message}</span>`;
}

function amazonSignedInAccountName() {
  const candidates = ["#nav-link-accountList-nav-line-1", "#nav-link-accountList .nav-line-1", "span.nav-line-1"];
  for (const selector of candidates) {
    const element = [...document.querySelectorAll(selector)].find((node) => visible(node) && /hello,/i.test(node.textContent || ""));
    const text = textOf(element);
    const match = text.match(/^hello,\s*(.+)$/i);
    if (match?.[1]) return match[1].trim();
  }
  return "";
}

function orderIdsFromText(text) {
  return [...new Set((text.match(/\b\d{3}-\d{7}-\d{7}\b/g) || []).map((value) => value.trim()))];
}

function orderRefsFromText(text) {
  return [...new Set((text.match(/\b[A-Z]{2,5}\d{2,}\b/gi) || []).map((value) => value.toUpperCase()))];
}

function orderUrlFor(orderId, root) {
  const link = [...(root || document).querySelectorAll?.("a") || []].find((item) => {
    const href = item.href || item.getAttribute("href") || "";
    return href.includes(orderId) || /order-details/i.test(href);
  });
  return link ? absoluteUrl(link.href || link.getAttribute("href")) : `https://www.amazon.com/your-orders/order-details?orderID=${encodeURIComponent(orderId)}`;
}

function orderCardCandidates() {
  const selectors = [
    ".order-card",
    ".js-order-card",
    "[id*='orderCard']",
    "[data-order-id]",
    ".a-box-group",
    ".a-box",
  ];
  const cards = [...document.querySelectorAll(selectors.join(", "))]
    .filter((element) => visible(element) && orderIdsFromText(textOf(element)).length && orderRefsFromText(textOf(element)).length);
  if (cards.length) return cards;
  return /order-details/i.test(location.href) ? [document.body] : [];
}

function extractManualOrders() {
  const accountName = amazonSignedInAccountName();
  const byOrderId = new Map();
  for (const card of orderCardCandidates()) {
    const text = textOf(card);
    const refs = orderRefsFromText(text);
    if (!refs.length) continue;
    for (const orderId of orderIdsFromText(text)) {
      const existing = byOrderId.get(orderId);
      const orderNames = existing ? [...new Set([...existing.order_names, ...refs])] : refs;
      byOrderId.set(orderId, {
        amazon_order_id: orderId,
        amazon_order_url: orderUrlFor(orderId, card),
        amazon_account_name: accountName,
        order_names: orderNames,
        source_text: text.slice(0, 4000),
      });
    }
  }
  return [...byOrderId.values()];
}

function nextPageUrl() {
  const links = [...document.querySelectorAll("a")].filter(visible);
  const next = links.find((link) => /next/i.test(textOf(link)) && !/disabled/i.test(link.className || ""));
  return next ? absoluteUrl(next.href || next.getAttribute("href")) : "";
}

async function scan(oneShot = false) {
  if (!/amazon\.com$/i.test(location.hostname)) return;
  await sleep(1200);
  const orders = extractManualOrders();
  showPanel("Nutricity manual matcher", `Found ${orders.length} Amazon order candidate(s).`);
  await send({ type: "MANUAL_ORDER_PAGE", orders, nextUrl: nextPageUrl(), oneShot });
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    if (message.type === "SCAN_MANUAL_PAGE") {
      await scan(Boolean(message.oneShot));
      return { ok: true };
    }
    return { ok: false };
  })().then(sendResponse).catch((error) => sendResponse({ ok: false, message: error.message }));
  return true;
});

(async () => {
  const state = await send({ type: "GET_STATE" });
  if (state?.run?.running && /order-history|your-orders|order-details/i.test(location.href)) {
    await scan(false);
  }
})();
