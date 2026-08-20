# Coolify Deployment

This app is deployed as one Docker container:

- Vite/React is built during the Docker build.
- FastAPI serves the built frontend and the API.
- PostgreSQL must be provided through Coolify or an external managed database.

## 1. Create the Database

In Coolify, add a PostgreSQL resource first. Copy its internal connection string.

Use that value as both:

```text
POSTGRES_URL=postgres://...
DATABASE_URL=postgres://...
```

## 2. Create the Application

1. Add a new application in Coolify.
2. Select the GitHub repository.
3. Build pack: `Dockerfile`.
4. Dockerfile path: `Dockerfile`.
5. Port: `8000`.
6. Health check path: `/health`.

## 3. Environment Variables

Set at least these variables in Coolify:

```text
POSTGRES_URL=postgres://user:password@host:5432/database
DATABASE_URL=postgres://user:password@host:5432/database
POSTGRES_POOL_MAX=10

ADMIN_ACCESS_TOKEN=change-this-admin-code
MASTER_ADMIN_ACCESS_TOKEN=change-this-master-code

OPENEXCHANGE_API_KEY=
OPENEXCHANGE_SYNC_INTERVAL_MINUTES=2880

TYPESENSE_URL=
TYPESENSE_API_KEY=
TYPESENSE_ENABLED=false

STORAGE_S3_ENDPOINT=
STORAGE_S3_BUCKET=
STORAGE_S3_REGION=auto
STORAGE_S3_ACCESS_KEY_ID=
STORAGE_S3_SECRET_ACCESS_KEY=

BACKUP_S3_ENDPOINT=
BACKUP_S3_BUCKET=

AMAZON_API_BASE_URL=https://na.business-api.amazon.com
AMAZON_TRACKING_API_BASE_URL=https://na.business-api.amazon.com
AMAZON_LWA_TOKEN_URL=https://api.amazon.com/auth/o2/token
AMAZON_LWA_CLIENT_ID=
AMAZON_LWA_CLIENT_SECRET=
AMAZON_LWA_REFRESH_TOKEN=
AMAZON_API_ACCESS_TOKEN=

ODOO_VALIDATE_PICKINGS=true
```

Do not put real keys in GitHub. Add them only in Coolify environment variables.

## 4. Deploy

Click **Deploy** in Coolify. On first boot the app creates or updates its PostgreSQL tables.

For this project on the configured Mac, use the saved Coolify API credentials
instead of opening the dashboard:

```bash
./scripts/deploy-coolify.sh
```

The command reads the API token, host, and application UUID from macOS
Keychain service `codex.coolify.185.194.236.161`; no token is stored in Git.

After deploy, open:

```text
https://your-domain/health
```

Expected response:

```json
{"ok":true,"db":"postgres","storage":"cloudflare-r2"}
```

## Notes

- The Chrome extensions are not part of the Docker image. They should still point to the deployed app URL.
- If Coolify gives the app a `PORT` variable, the Dockerfile uses it automatically.
- The app requires PostgreSQL. SQLite is intentionally disabled.
