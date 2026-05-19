# Nutricity Amazon Tracking Chrome Extension

## Manual install

1. Start the local app:
   `uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`
2. Open Chrome and go to `chrome://extensions`.
3. Enable **Developer mode**.
4. Click **Load unpacked**.
5. Select this folder:
   `/Users/amitsoni/Documents/Chrome extension - fulfilment and report/tracking-extension`

## Use

1. Log in to Amazon in Chrome.
2. Log in to the local fulfilment app in the same Chrome profile.
3. Open the tracking extension popup.
4. Confirm the local app URL.
5. Click **Start tracking**.

The extension opens each tracked Amazon order, finds every **Track package** link, visits each package tracking page, captures carrier, tracking ID, current status, latest event date/time/message, and reports the package updates back to the local app.
