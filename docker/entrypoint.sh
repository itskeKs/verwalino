#!/bin/sh
# Container entrypoint: apply DB migrations (as the migrator role, via
# HV_DATABASE_URL_SYNC), then start the API + SPA server (as app_rw, via
# HV_DATABASE_URL). Migrations are idempotent, so running on every start is safe.
set -e

echo "[entrypoint] applying database migrations (alembic upgrade head)..."
alembic upgrade head

echo "[entrypoint] starting uvicorn on :8000 (workers=${WEB_CONCURRENCY:-2})..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers "${WEB_CONCURRENCY:-2}"
