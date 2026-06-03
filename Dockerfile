FROM node:22-bookworm-slim AS source

ARG REPO_URL=https://github.com/chillpilllike/Chrome-extension---fulfilment-and-report.git
ARG GIT_BRANCH=main

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# Bust Docker cache whenever the public GitHub main branch changes.
ADD https://api.github.com/repos/chillpilllike/Chrome-extension---fulfilment-and-report/commits/main /tmp/github-version.json

WORKDIR /src
RUN git clone --depth 1 --branch "$GIT_BRANCH" "$REPO_URL" .


FROM source AS frontend-build

WORKDIR /src/frontend
RUN npm ci
RUN npm run build


FROM python:3.12-slim AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    WEB_CONCURRENCY=1 \
    APP_SECRET_KEY=change-this-in-coolify \
    ADMIN_ACCESS_TOKEN=change-this-in-coolify \
    MASTER_ADMIN_ACCESS_TOKEN=change-this-in-coolify \
    POSTGRES_URL= \
    DATABASE_URL= \
    POSTGRES_POOL_MAX=10 \
    REDIS_URL=redis://default:aPw7vPnqfobUrhuTdR7dUNVNHnAMn9REdXYkWrw64HYBojJkAkRDLAEb0uQZmwBE@185.194.236.161:5465/0 \
    DRAMATIQ_REDIS_URL=redis://default:aPw7vPnqfobUrhuTdR7dUNVNHnAMn9REdXYkWrw64HYBojJkAkRDLAEb0uQZmwBE@185.194.236.161:5465/0 \
    REDIS_ENABLED=true \
    DRAMATIQ_ENABLED=true \
    REDIS_CONNECT_TIMEOUT=0.08 \
    REDIS_SOCKET_TIMEOUT=0.12 \
    DRAMATIQ_PROCESSES=1 \
    DRAMATIQ_THREADS=8 \
    FAST_PAGE_CACHE_MAX_ENTRIES=3000 \
    TYPESENSE_URL= \
    TYPESENSE_API_KEY= \
    TYPESENSE_ENABLED=false \
    OPENEXCHANGE_API_KEY= \
    OPENEXCHANGE_SYNC_INTERVAL_MINUTES=2880 \
    STORAGE_S3_ENDPOINT= \
    STORAGE_S3_BUCKET= \
    STORAGE_S3_REGION=auto \
    STORAGE_S3_ACCESS_KEY_ID= \
    STORAGE_S3_SECRET_ACCESS_KEY= \
    BACKUP_S3_ENDPOINT= \
    BACKUP_S3_BUCKET= \
    AMAZON_API_BASE_URL=https://na.business-api.amazon.com \
    AMAZON_TRACKING_API_BASE_URL=https://na.business-api.amazon.com \
    AMAZON_LWA_TOKEN_URL=https://api.amazon.com/auth/o2/token \
    AMAZON_LWA_CLIENT_ID= \
    AMAZON_LWA_CLIENT_SECRET= \
    AMAZON_LWA_REFRESH_TOKEN= \
    AMAZON_API_ACCESS_TOKEN= \
    AMAZON_PRODUCT_SEARCH_PATH=/products/2020-08-26/products \
    AMAZON_ORDER_CREATE_PATH=/ordering/2022-10-30/orders \
    AMAZON_ORDER_DETAILS_PATH=/ordering/2022-10-30/orders/{amazon_order_id} \
    AMAZON_PACKAGE_TRACKING_PATH=/ab-tracking/2025-07-02/orders/{orderId}/shipments/{shipmentId}/packages/{packageId} \
    AMAZON_ORDER_URL_TEMPLATE=https://www.amazon.com/gp/css/order-details?orderID={amazon_order_id} \
    ODOO_VALIDATE_PICKINGS=true

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=source /src/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=source /src/app ./app
COPY --from=frontend-build /src/frontend/dist ./frontend/dist

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\", \"8000\")}/health', timeout=5).read()"

ENTRYPOINT ["/usr/bin/tini", "--"]

CMD ["bash", "-lc", "set -euo pipefail; pids=(); shutdown() { for pid in \"${pids[@]:-}\"; do kill -TERM \"$pid\" 2>/dev/null || true; done; wait || true; }; trap shutdown TERM INT; if [ \"${DRAMATIQ_ENABLED:-true}\" != \"false\" ] && [ \"${DRAMATIQ_ENABLED:-true}\" != \"0\" ]; then dramatiq app.tasks --processes \"${DRAMATIQ_PROCESSES:-1}\" --threads \"${DRAMATIQ_THREADS:-8}\" & pids+=(\"$!\"); fi; uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\" --workers \"${WEB_CONCURRENCY:-1}\" & pids+=(\"$!\"); set +e; wait -n \"${pids[@]}\"; status=$?; set -e; shutdown; exit \"$status\""]
