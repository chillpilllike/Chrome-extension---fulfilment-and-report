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
5. Open the extension popup and click **Start next queued order**.

The extension opens each Amazon product page, pauses when coupon or promotion text is found, adds items to cart, proceeds through checkout, edits only the full name to `Nutricity <OdooOrderNumber>`, clicks **Place your order**, and reports the Amazon order ID back to the local app when it can detect it.
