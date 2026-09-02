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

You can also open an individual Amazon tracking page without starting a queue.
Version 0.1.72 fixes these manual updates being blocked with “No active tracking
run to recover.” Active queue order and split-shipment guards still apply.

After updating this folder, reload the extension in Chrome and refresh existing
Amazon tabs. Website deployments do not update already installed extensions.
Refreshing Amazon tracking updates delivery information; it does not retroactively
turn an unsuccessful warehouse scan into a received package. Once a stale delivery
status is corrected, rescan the physical parcel in Package Pickup Check.
