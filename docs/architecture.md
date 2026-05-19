# Architecture Direction

This app is being refactored toward the structure used by the FastAPI full-stack template, without doing a risky one-shot rewrite.

## Current Shape

- `app/main.py` still owns the FastAPI app, routes, startup, legacy Jinja pages, and most business workflows.
- `frontend/` owns the Vite React control panel.
- `chrome-extension/` owns the Chrome-based Amazon fulfilment worker.

## Target Backend Shape

- `app/core/`
  - Runtime configuration, environment loading, paths, and app-wide helpers.
- `app/db/`
  - Database connection/session ownership and, later, migrations/repositories.
- `app/services/`
  - Domain logic for Amazon, Odoo, cXML, ASIN parsing, search, backups, and reports.
- `app/api/routes/`
  - FastAPI routers grouped by domain, mounted from `app/main.py`.
- `app/schemas/`
  - Pydantic request/response models currently living in `app/main.py`.

## Refactor Sequence

1. Extract stable infrastructure from `app/main.py`.
2. Move Pydantic payload models into `app/schemas/`.
3. Split settings, stores, addresses, Amazon accounts, orders, Chrome jobs, punchout, and reports into `app/api/routes/`.
4. Move business workflows behind service modules.
5. Add focused tests around duplicate-order prevention, order placement, Chrome job completion, and delivery checks.
6. Replace inline SQLite schema updates with migrations before any PostgreSQL migration.

## Guardrails

- Keep existing endpoints stable for the React frontend and Chrome extension.
- Prefer small behavior-preserving moves over broad rewrites.
- Do not introduce template features such as JWT auth, SQLModel, or PostgreSQL until the domain code is separated enough to make those changes boring.
