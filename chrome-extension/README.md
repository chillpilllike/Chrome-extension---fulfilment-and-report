# Nutricity Amazon Fulfilment Chrome Extension

## Manual install

1. Start the local app:
   `uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`
2. Open Chrome and go to `chrome://extensions`.
3. Enable **Developer mode**.
4. Click **Load unpacked**.
5. Select this folder:
   `/Users/amitsoni/Documents/Chrome extension - fulfilment and report/chrome-extension`

## Use

1. Log in to Amazon in Chrome.
2. Log in to the local fulfilment app in the same Chrome profile.
3. In the app, set **Ordering engine** to **Chrome Extension**.
4. Select the order lines and click **Place Selected** or **Club Place**. This queues Chrome jobs.
5. Open the extension popup and click **Start next queued order**, or enable **Continuously process compatible queued orders**.

The extension opens each Amazon product page, pauses when coupon or promotion text is found, adds items to cart, proceeds through checkout, edits only the full name to `Nutricity <OdooOrderNumber>`, clicks **Place your order**, and reports the Amazon order ID back to the local app when it can detect it.

## Amazon account types (do not combine these flows)

- **Consumer account (any Chrome profile):** if Subscribe & Save is cheaper, the extension selects it. For a multi-item order it must wait for and click **Add subscription to cart**. If that consumer-only control does not render, the order pauses; it must not silently use One-time purchase.
- **Amazon Business account (any Chrome profile):** Amazon does not provide **Add subscription to cart** and uses its separate normal-cart path.

Each extension positively detects its current account type before claiming. When app routing is enabled, the first idle compatible consumer extension may claim a whole multi-line order, while the first idle compatible Business extension may claim a whole single-line order. Profile names are never used, and one Odoo order is never split among workers.

The mandatory implementation rules for future changes are in `AGENTS.md` in this directory.
