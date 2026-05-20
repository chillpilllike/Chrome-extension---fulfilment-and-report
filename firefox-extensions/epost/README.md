# Nutricity ePost Global Tracking Firefox Extension

## Manual install

1. Start the local app:
   `./.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`
2. Open Firefox and go to `about:debugging#/runtime/this-firefox`.
3. Click **Load Temporary Add-on...**.
5. Select this folder:
   `/Users/amitsoni/Documents/Firefox extension - fulfilment and report/epost-extension`

## Use

1. In the app, open **ePost** and click **Sync from Odoo** to pull EPG tracking codes from fulfilled Odoo pickings.
2. Open the ePost tracking extension popup.
3. Set **Track again after days**.
4. Click **Track due codes**.

The extension submits up to 25 tracking codes at a time to `portal.epgshipping.com`, captures status, last update date/time, destination, AWB, details, and updates the local app. Codes stop being tracked once status contains `Delivered`.
