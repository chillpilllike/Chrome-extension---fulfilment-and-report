# Nutricity Amazon Fulfilment Firefox Extension

## Manual install

1. Start the local app:
   `uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`
2. Open Firefox and go to `about:debugging#/runtime/this-firefox`.
3. Click **Load Temporary Add-on...**.
5. Select this folder:
   `/Users/amitsoni/Documents/Firefox extension - fulfilment and report/chrome-extension`

## Use

1. Log in to Amazon in Firefox.
2. Log in to the local fulfilment app in the same Firefox profile.
3. In the app, set **Ordering engine** to **Firefox Extension**.
4. Select the order lines and click **Place Selected** or **Club Place**. This queues Firefox jobs.
5. Open the extension popup and click **Start next queued order**.

The extension opens each Amazon product page, pauses when coupon or promotion text is found, adds items to cart, proceeds through checkout, edits only the full name to `Nutricity <OdooOrderNumber>`, clicks **Place your order**, and reports the Amazon order ID back to the local app when it can detect it.
