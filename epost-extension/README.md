# Nutricity ePost Global Tracking Chrome Extension

## Manual install

1. Start the local app:
   `./.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`
2. Open Chrome and go to `chrome://extensions`.
3. Enable **Developer mode**.
4. Click **Load unpacked**.
5. Select this folder:
   `/Users/amitsoni/Documents/Chrome extension - fulfilment and report/epost-extension`

## Use

1. In the app, open **ePost** and click **Sync from Odoo** to pull EPG tracking codes from fulfilled Odoo pickings.
2. Open the ePost tracking extension popup.
3. Set **Auto check every hours**.
4. Click **Track undelivered codes**.

The extension submits up to 25 undelivered tracking codes at a time to `portal.epgshipping.com`, captures status, last update date/time, destination, AWB, details, and updates the local app. Codes keep being tracked on later runs until the local app marks them `Delivered`.

## Background/headless mode

Enable **Run ePost in background/headless Chrome** before clicking **Track undelivered codes** to run the same extraction pipeline through the local app's Playwright Chrome runner. This mode injects the same `content.js` parser used by normal Chrome mode, so parser fixes and portal workflow improvements are shared between both modes.

The local app must be running and Python Playwright must be installed from `requirements.txt`. The runner uses the shared Chrome background profile at `data/chrome-headless-profile` unless `CHROME_BROWSERLESS_PROFILE_DIR` or `CHROME_HEADLESS_USER_DATA_DIR` is set.
