"""One-off admin bootstrap CLI: create an organization + an OWNER_ADMIN user.

A fresh production database has no login. Run this once inside the container to
create the first organization and its admin; everything else is then managed
through the app.

    printf '%s\\n' 'use-a-strong-password' | \
        docker compose --profile ops run --rm -T bootstrap \
        --org "Meine Hausverwaltung" --email admin@example.de \
        --name "Admin" --password-stdin

Connects via HV_DATABASE_URL_SYNC (the migrator role). The migrator is the DB
owner/superuser and bypasses RLS, so it may seed the first org/user/membership
rows directly. Idempotent on the user email (aborts if it already exists).
"""

from __future__ import annotations

import argparse
import sys

import psycopg2

from app.core.config import settings
from app.core.security import hash_password, normalize_email, validate_new_password


def main() -> None:
    ap = argparse.ArgumentParser(description="Create an organization + OWNER_ADMIN user.")
    ap.add_argument("--org", required=True, help="Organization name")
    ap.add_argument("--email", required=True, help="Admin login email")
    password_options = ap.add_mutually_exclusive_group(required=True)
    password_options.add_argument("--password", help="Admin password")
    password_options.add_argument(
        "--password-stdin", action="store_true", help="Read the admin password from stdin"
    )
    ap.add_argument("--name", default="Administrator", help="Admin display name")
    args = ap.parse_args()
    password = args.password
    if args.password_stdin:
        password = sys.stdin.readline()
        if not password.endswith("\n"):
            ap.error("--password-stdin requires one newline-terminated line")
        password = password[:-1]
    try:
        password = validate_new_password(password)
        email = normalize_email(args.email)
    except ValueError as error:
        ap.error(str(error))

    conn = psycopg2.connect(settings.database_url_sync)
    conn.autocommit = False
    try:
        cur = conn.cursor()
        cur.execute("LOCK TABLE public.organization IN SHARE ROW EXCLUSIVE MODE", ())
        cur.execute(
            'SELECT id FROM public."user" '
            "WHERE pg_catalog.lower(pg_catalog.btrim(email)) = %s",
            (email,),
        )
        if cur.fetchone():
            print(f"User '{email}' already exists — nothing to do.")
            conn.rollback()
            return

        cur.execute(
            "SELECT public.bootstrap_organization_admin(%s,%s,%s,%s)",
            (args.org, email, args.name, hash_password(password)),
        )
        conn.commit()
        print(f"Created organization '{args.org}' and OWNER_ADMIN '{email}'.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
