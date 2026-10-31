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
from contextlib import ExitStack
import fcntl
import os
import sys
import uuid

import psycopg2
from psycopg2.extras import Json, execute_values

from app.core.config import settings
from app.core.rate_limit import key_hash
from app.core.security import hash_password, normalize_email, validate_new_password


def main() -> None:
    if sys.argv[1:2] == ["unlock"]:
        unlock(sys.argv[2:])
        return
    if sys.argv[1:2] == ["quota-recount"]:
        quota_recount(sys.argv[2:])
        return
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


def unlock(arguments: list[str]) -> None:
    """Delete exactly one login key using the configured migrator connection."""
    ap = argparse.ArgumentParser(prog="admin_cli unlock", description="Clear a login throttle key as the migrator.")
    keys = ap.add_mutually_exclusive_group(required=True)
    keys.add_argument("--email", help="Login email (normalized like the app)")
    keys.add_argument("--ip", help="Client IP (native IPv6 shares a /64; unknown addresses use 'invalid')")
    args = ap.parse_args(arguments)
    kind, value = ("email", args.email) if args.email is not None else ("ip", args.ip)
    try:
        digest = key_hash("login", kind, value)
    except ValueError as error:
        ap.error(str(error))
    conn = psycopg2.connect(settings.database_url_sync)
    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM public.auth_throttle WHERE key_kind=%s AND key_hash=%s", (kind, digest))
            removed = cursor.rowcount
        conn.commit()
        print(f"Removed {removed} login throttle row(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def quota_recount(arguments: list[str]) -> None:
    """Record legacy physical sizes without changing retained document metadata."""
    from app.core.quotas import get_quota_plan
    from app.core.storage import LocalFsDocumentStore, open_storage_lock

    ap = argparse.ArgumentParser(prog="admin_cli quota-recount", description="Reconcile retained local document sizes as the migrator.")
    ap.add_argument("--org", required=True, type=uuid.UUID, help="Organization UUID")
    args = ap.parse_args(arguments)
    org = str(args.org)
    store = LocalFsDocumentStore(settings.document_storage_root)
    conn = psycopg2.connect(settings.database_url_sync)
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM public.organization WHERE id=%s", (org,))
            if cursor.fetchone() is None:
                ap.error("Organization not found")
            hosted = get_quota_plan().name != "unlimited"
            if hosted:
                cursor.execute("SELECT pg_advisory_xact_lock_shared(hashtextextended(%s,0))", (f"scope-acl:{org}",))
                cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"organization-quota:{org}",))
            with ExitStack() as cleanup:
                if hosted:
                    handle = open_storage_lock(store, org)
                    cleanup.callback(os.close, handle)
                    fcntl.flock(handle, fcntl.LOCK_EX)
                # The migrator can see source-restricted references. Recount
                # must not freeze sized live originals as permanent audit charges.
                cursor.execute("""SELECT storage_uri FROM (
                    SELECT storage_uri FROM public.attachment WHERE organization_id=%(org)s
                    UNION ALL SELECT storage_uri FROM public.invoice_document WHERE organization_id=%(org)s
                    UNION ALL SELECT storage_uri FROM public.mandate_document WHERE organization_id=%(org)s
                    UNION ALL SELECT storage_uri FROM public.property_document WHERE organization_id=%(org)s
                    UNION ALL SELECT storage_uri FROM public.intake_original WHERE organization_id=%(org)s
                    UNION ALL SELECT storage_uri FROM public.letter_document WHERE organization_id=%(org)s AND size_bytes IS NOT NULL
                    UNION ALL SELECT payload_diff->>'storage_uri' FROM public.audit_event
                        WHERE organization_id=%(org)s AND action='mail_prepared' AND payload_diff->>'bytes' IS NOT NULL
                ) sized""", {"org": org})
                sized = {row[0] for row in cursor.fetchall()}
                originals = []
                total = 0
                scanned = 0
                for path in store._resolve_key(org).rglob("*.bin"):
                    key = path.relative_to(store._root).as_posix()
                    size = store._resolve_key(key).stat().st_size
                    if key not in sized:
                        originals.append((org, org, Json({"storage_uri": key, "bytes": size})))
                    total += size
                    scanned += 1
                if originals:
                    execute_values(cursor, """INSERT INTO public.audit_event(organization_id,entity_type,entity_id,action,payload_diff)
                        SELECT v.org::uuid,'organization',v.entity::uuid,'quota_recount',v.payload::jsonb
                        FROM (VALUES %s) v(org,entity,payload)
                        WHERE NOT EXISTS(SELECT 1 FROM public.audit_event a
                          WHERE a.organization_id=v.org::uuid AND a.action='quota_recount'
                            AND a.payload_diff->>'storage_uri'=v.payload::jsonb->>'storage_uri'
                            AND a.payload_diff->>'bytes'=v.payload::jsonb->>'bytes')""", originals)
                conn.commit()
                print(f"Reconciled {scanned} originals, {total} bytes for {org}.")
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
