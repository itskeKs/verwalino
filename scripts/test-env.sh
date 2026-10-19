#!/bin/sh
# Export environment variables for the disposable declaration test database.
# Source this file before running backend tests that need a live database.
#
#     source scripts/test-env.sh
#     uv run pytest tests/test_declaration_runtime_config.py

set -eu

export HV_DATABASE_URL="postgresql+asyncpg://app_rw:app_secret@127.0.0.1:55432/hausverwaltung"
export HV_DATABASE_URL_MIGRATOR="postgresql+asyncpg://app_migrator:migrator_secret@127.0.0.1:55432/hausverwaltung"
export HV_DATABASE_URL_SYNC="postgresql://app_migrator:migrator_secret@127.0.0.1:55432/hausverwaltung"
export HV_DATABASE_URL_REVIEW="postgresql+asyncpg://app_review:review_secret@127.0.0.1:55432/hausverwaltung"
