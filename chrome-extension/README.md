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
5. Open the extension popup, tick **I confirm this Amazon account is ready for auto ordering**, then click **Start Auto Ordering**.

Auto ordering never starts merely because Chrome opened or the extension reloaded. The confirmation is required for each browser session. While it is running, **Start Auto Ordering** becomes **Stop Auto Ordering**. Stopping clears the session permission, safely releases an active pre-submit job, and prevents any new claim until the confirmation is ticked and Start is clicked again.

Missing ASIN lines are checked at most once every 48 hours. When every unresolved missing line in an Odoo order is available (including an assigned replacement ASIN), the remaining whole order is automatically returned to the Chrome queue. Third-party fulfilled, cancelled, refunded, already ordered, and safety-blocked lines are not requeued.

The extension opens each Amazon product page, pauses when coupon or promotion text is found, adds items to cart, proceeds through checkout, edits only the full name to `Nutricity <OdooOrderNumber>`, clicks **Place your order**, and reports the Amazon order ID back to the local app when it can detect it.

## Amazon account types (do not combine these flows)

- **Consumer account (any Chrome profile):** if Subscribe & Save is cheaper, the extension selects it. For a multi-item order it must wait for and click **Add subscription to cart**. If that consumer-only control does not render, the order pauses; it must not silently use One-time purchase.
- **Amazon Business account (any Chrome profile):** Amazon does not provide **Add subscription to cart** and uses its separate normal-cart path.

Each extension positively detects its current account type before claiming. When app routing is enabled, the first idle compatible consumer extension may claim a whole multi-ASIN order, while the first idle compatible Business extension may claim a whole single-ASIN order. Multiple Odoo lines consolidated to the same purchasable ASIN still count as one ASIN. Profile names are never used, and one Odoo order is never split among workers.

The mandatory implementation rules for future changes are in `AGENTS.md` in this directory.
