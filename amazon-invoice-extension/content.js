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

function textOf(element) {
  return (element?.textContent || "").replace(/\s+/g, " ").trim();
}

function absoluteAmazonUrl(path) {
  return new URL(path, "https://www.amazon.com").href;
}

function directDocumentUrl(url) {
  const parsed = new URL(url, location.href);
  parsed.search = "";
  return parsed.href;
}

async function findPrintableSummaryLink() {
  const existing = [...document.querySelectorAll("a")].find((link) => /summary\/print\.html/i.test(link.href || link.getAttribute("href") || ""));
  if (existing) return existing.href || absoluteAmazonUrl(existing.getAttribute("href"));
  const invoiceTrigger = [...document.querySelectorAll("span, a, button")].find((node) => textOf(node).toLowerCase() === "invoice");
  invoiceTrigger?.click();
  await sleep(1200);
  const link = [...document.querySelectorAll("a")].find((item) => /Printable Order Summary/i.test(textOf(item)) || /summary\/print\.html/i.test(item.href || item.getAttribute("href") || ""));
  return link ? link.href || absoluteAmazonUrl(link.getAttribute("href")) : "";
}

async function run() {
  if (!/amazon\.com$/i.test(location.hostname)) return;
  if (/summary\/print\.html/i.test(location.href)) {
    const download = [...document.querySelectorAll("a")].find((link) => /documents\/download\/.*order-document\.pdf/i.test(link.href || link.getAttribute("href") || ""));
    if (!download) return;
    const response = await fetch(directDocumentUrl(download.href || download.getAttribute("href")), { credentials: "include" });
    const bytes = Array.from(new Uint8Array(await response.arrayBuffer()));
    await send({ type: "AMAZON_INVOICE_FOUND", bytes });
    return;
  }
  if (!/order-details|your-orders/i.test(location.href)) return;
  const printable = await findPrintableSummaryLink();
  if (printable) location.href = printable;
}

run();
