# Nutricity Firefox Extensions

These folders are generated from the Chrome extension sources by:

```sh
python3 scripts/build_firefox_extensions.py
```

Load a Firefox extension manually:

1. Open Firefox.
2. Go to `about:debugging#/runtime/this-firefox`.
3. Click **Load Temporary Add-on...**.
4. Select the `manifest.json` inside one of these folders:
   - `firefox-extensions/fulfilment`
   - `firefox-extensions/tracking`
   - `firefox-extensions/manual-order-match`
   - `firefox-extensions/amazon-invoice`
   - `firefox-extensions/epost`

Packaged ZIP files are in `firefox-extensions/dist` for easier sharing or signing.
