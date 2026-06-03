#!/bin/bash
# Production DB bootstrap: create the application role app_rw (NO BYPASSRLS) with
# a password taken from the APP_RW_PASSWORD env var. Run once by the postgres
# image entrypoint as POSTGRES_USER (= app_migrator, the table owner). This is the
# env-driven counterpart of infra/db/init.sql (which hard-codes a dev password).
#
# The application connects as app_rw; the migrator (app_migrator) owns the tables.
# RLS is FORCE'd, so even the owner obeys policies; app_rw additionally can never
# bypass RLS. Table-level GRANTs to app_rw are issued by the Alembic migrations.
set -e

: "${APP_RW_PASSWORD:?APP_RW_PASSWORD must be set for the db service}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  DO \$\$
  BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_rw') THEN
      CREATE ROLE app_rw LOGIN PASSWORD '${APP_RW_PASSWORD}' NOBYPASSRLS NOCREATEDB NOCREATEROLE;
    END IF;
  END
  \$\$;
  GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO app_rw;
EOSQL

echo "[db-init] app_rw role ensured."
