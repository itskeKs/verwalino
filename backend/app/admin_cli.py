"""One-off admin bootstrap CLI: create an organization + an OWNER_ADMIN user.

A fresh production database has no login. Run this once inside the container to
create the first organization and its admin; everything else is then managed
through the app.

    docker compose -f docker-compose.prod.yml exec app \
        python -m app.admin_cli --org "Meine Hausverwaltung" \
        --email admin@example.de --password 'a-strong-password' --name "Admin"

Connects via HV_DATABASE_URL_SYNC (the migrator role). The migrator is the DB
owner/superuser and bypasses RLS, so it may seed the first org/user/membership
rows directly. Idempotent on the user email (aborts if it already exists).
"""

from __future__ import annotations

import argparse
import uuid

import psycopg2

from app.core.config import settings
from app.core.security import hash_password


def main() -> None:
    ap = argparse.ArgumentParser(description="Create an organization + OWNER_ADMIN user.")
    ap.add_argument("--org", required=True, help="Organization name")
    ap.add_argument("--email", required=True, help="Admin login email")
    ap.add_argument("--password", required=True, help="Admin password")
    ap.add_argument("--name", default="Administrator", help="Admin display name")
    args = ap.parse_args()

    conn = psycopg2.connect(settings.database_url_sync)
    conn.autocommit = False
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM organization WHERE name = %s", (args.org,))
        row = cur.fetchone()
        org_id = row[0] if row else str(uuid.uuid4())
        if not row:
            cur.execute(
                "INSERT INTO organization (id, name) VALUES (%s, %s)",
                (org_id, args.org),
            )

        cur.execute('SELECT id FROM "user" WHERE email = %s', (args.email,))
        if cur.fetchone():
            print(f"User '{args.email}' already exists — nothing to do.")
            conn.rollback()
            return

        user_id = str(uuid.uuid4())
        cur.execute(
            'INSERT INTO "user" (id, organization_id, email, password_hash, display_name) '
            "VALUES (%s, %s, %s, %s, %s)",
            (user_id, org_id, args.email, hash_password(args.password), args.name),
        )
        cur.execute(
            "INSERT INTO membership (id, organization_id, user_id, role) VALUES (%s, %s, %s, %s)",
            (str(uuid.uuid4()), org_id, user_id, "OWNER_ADMIN"),
        )
        conn.commit()
        print(f"Created organization '{args.org}' and OWNER_ADMIN '{args.email}'.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
