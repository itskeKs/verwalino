-- init.sql: Bootstrap DB roles for RLS enforcement.
-- Executed once by docker-entrypoint as the POSTGRES_USER (app_migrator / superuser stand-in).
--
-- Runtime roles (architecture.md section 2.3 / 7.2):
--   app_migrator  - owns tables, runs DDL/migrations, CAN bypass RLS (table owner).
--   app_rw        - the role the application connects as; NO BYPASSRLS, NO table ownership.
--                   RLS policies are FORCE'd, so even the table owner obeys them,
--                   but app_rw has an extra guarantee: it can never bypass.

-- app_worker inherits app_rw data privileges and alone receives worker commands.
-- Create the application role (if not exists)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_rw') THEN
    CREATE ROLE app_rw LOGIN PASSWORD 'app_secret' NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
END
$$;

-- Grant connect
GRANT CONNECT ON DATABASE hausverwaltung TO app_rw;

-- The migrator will GRANT table-level privileges after creating tables (in Alembic migrations).
-- app_rw gets SELECT, INSERT, UPDATE, DELETE on tenant tables (except audit_event: no UPDATE/DELETE).

-- Separate worker login; never grant this role to the web principal.
DO $$ BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='app_worker') THEN
    CREATE ROLE app_worker LOGIN PASSWORD 'worker_secret' INHERIT NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
END $$;
GRANT app_rw TO app_worker;
GRANT CONNECT ON DATABASE hausverwaltung TO app_worker;
