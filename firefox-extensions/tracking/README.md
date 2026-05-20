# Nutricity Amazon Tracking Firefox Extension

## Manual install

1. Start the local app:
   `uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`
2. Open Firefox and go to `about:debugging#/runtime/this-firefox`.
3. Click **Load Temporary Add-on...**.
5. Select this folder:
   `/Users/amitsoni/Documents/Firefox extension - fulfilment and report/tracking-extension`

## Use

1. Log in to Amazon in Firefox.
2. Log in to the local fulfilment app in the same Firefox profile.
3. Open the tracking extension popup.
4. Confirm the local app URL.
5. Click **Start tracking**.

The extension opens each tracked Amazon order, finds every **Track package** link, visits each package tracking page, captures carrier, tracking ID, current status, latest event date/time/message, and reports the package updates back to the local app.
