# Amazon Business Fulfilment Control Panel

Local FastAPI app with a React/shadcn UI for pulling Odoo sale orders, extracting Amazon ASINs from the product reference/internal note convention used by your importer, placing Amazon Business orders, writing Amazon order links back to Odoo, and exporting CSV reports.

## Install

```bash
cd "/Users/amitsoni/Documents/Chrome extension - fulfilment and report"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cd frontend
npm install
```

Edit `.env` and add your Amazon Business API credentials.

## Run

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
cd "/Users/amitsoni/Documents/Chrome extension - fulfilment and report/frontend"
npm run dev -- --host 127.0.0.1 --port 5173
```

For production-like dispatch performance, run Redis and the Dramatiq worker in another terminal:

```bash
brew services start redis
export REDIS_URL=redis://127.0.0.1:6379/0
./scripts/run-dramatiq-worker.sh
```

Use a shared Redis URL in `.env` for team/production use so every app process and worker sees the same queue and hot cache. The worker handles dispatch scan-index rebuilds, per-order dispatch package syncing, stale payment-failure cleanup, and hot page warmups. Redis is also used for shared page cache and rebuild progress so refreshes do not wait on heavy SQL work.

Open the shadcn UI at [http://127.0.0.1:5173](http://127.0.0.1:5173). The backend API runs at [http://127.0.0.1:8000](http://127.0.0.1:8000).

## First Use

1. Open **Stores** and add one or more Odoo websites/stores.
2. Open **Amazon Accounts** and add one or more Amazon Business API credential profiles.
3. Open **Addresses** and add your warehouse/fulfilment ship-to address. Set one address as default.
4. Use **Pull Orders** for the selected store.
5. Review extracted ASINs. The app only keeps lines with a valid ASIN and checks:
   - decoded `product.template.default_code`
   - raw ASIN in `default_code`
   - `Amazon ASIN: <ASIN>` in product internal notes/description
6. Use **Place Amazon Orders**. Select the Amazon account and fulfilment address to use. Amazon receives the saved address, with recipient name set to `Nutricity <OdooOrderNumber>`.
7. Use **Reports** to download success/failure CSVs.
8. Use **Delivery Check** to refresh Amazon order status and validate Odoo pickings when delivered.

## Amazon API Credentials

Do not enter your Amazon Business username/password in this app. Amazon Business APIs use OAuth with Login With Amazon (LWA).

You need:

- Amazon Business developer/Solution Provider Portal access.
- An app/client with the required roles, especially `AmazonBusinessOrderPlacement` for Ordering API and Package Tracking access for tracking.
- LWA client ID.
- LWA client secret.
- LWA refresh token generated through the Amazon Business authorization workflow.
- Correct region endpoint in `AMAZON_API_BASE_URL`.

Put those values in `.env`:

```bash
AMAZON_API_BASE_URL=https://na.business-api.amazon.com
AMAZON_TRACKING_API_BASE_URL=https://na.business-api.amazon.com
AMAZON_LWA_CLIENT_ID=...
AMAZON_LWA_CLIENT_SECRET=...
AMAZON_LWA_REFRESH_TOKEN=...
```

Only use credentials that are approved for the Amazon Business APIs you plan to call.

## Important

The local SQLite DB is the duplicate-ordering source of truth. If the DB is missing, the app still pulls Odoo order notes and checks for existing Amazon order links before ordering again.
