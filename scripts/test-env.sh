#!/bin/sh
# Export environment variables for the disposable test database and Mailpit.
# Source this file before running backend tests that need a live database.
#
#     source scripts/test-env.sh
#     uv run pytest tests/test_declaration_runtime_config.py

set -eu

export VERWALINO_TEST_DB=1
export HV_DATABASE_URL="postgresql+asyncpg://app_rw:app_secret@127.0.0.1:55432/hausverwaltung"
export HV_DATABASE_URL_MIGRATOR="postgresql+asyncpg://app_migrator:migrator_secret@127.0.0.1:55432/hausverwaltung"
export HV_DATABASE_URL_SYNC="postgresql://app_migrator:migrator_secret@127.0.0.1:55432/hausverwaltung"
export HV_DATABASE_URL_REVIEW="postgresql+asyncpg://app_review:review_secret@127.0.0.1:55432/hausverwaltung"
export HV_SMTP_HOST="127.0.0.1"
export HV_SMTP_PORT="1025"
export HV_SMTP_SECURITY="plain"
export HV_SMTP_FROM="verwaltung@example.invalid"
export HV_SMTP_USERNAME=""
export HV_SMTP_PASSWORD=""
export VERWALINO_MAILPIT_API="http://127.0.0.1:8025"
