"""Database authentication functions use the app runtime role safely."""

from __future__ import annotations

from pathlib import Path
import uuid

import pytest

from tests.legacy_migration_database import legacy_auth_database
import psycopg2
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, InternalError


def test_0027_accepts_a_single_legacy_email_created_under_0022(legacy_auth_database):
    """A 0022-valid legacy address stays usable after the full 0027 upgrade."""
    from tests.conftest import MIGRATOR_SYNC_URL

    backend_dir = Path(__file__).resolve().parents[1]
    alembic_cfg = Config(str(backend_dir / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", MIGRATOR_SYNC_URL)
    alembic_cfg.set_main_option("script_location", str(legacy_auth_database))
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    canonical_email = f"legacy-upgrade-{uuid.uuid4().hex}@example.test"
    legacy_email = f" {canonical_email.upper()} "
    connection = None
    downgraded = False
    try:
        command.downgrade(alembic_cfg, "0022")
        downgraded = True
        connection = psycopg2.connect(MIGRATOR_SYNC_URL)
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.organization (id, name) VALUES (%s, %s)",
                (str(organization_id), f"Legacy migration {organization_id}"),
            )
            cursor.execute(
                'INSERT INTO public."user" (id, organization_id, email, password_hash, display_name) '
                "VALUES (%s, %s, %s, %s, %s)",
                (str(user_id), str(organization_id), legacy_email, "hash", "Legacy migration"),
            )
            cursor.execute(
                "INSERT INTO public.membership (id, organization_id, user_id, role) "
                "VALUES (%s, %s, %s, 'CLERK')",
                (str(uuid.uuid4()), str(organization_id), str(user_id)),
            )
        connection.commit()

        command.upgrade(alembic_cfg, "0027")
        with connection.cursor() as cursor:
            cursor.execute("SELECT user_id FROM public.app_auth_lookup(%s)", (canonical_email,))
            row = cursor.fetchone()
        assert row is not None
        assert str(row[0]) == str(user_id)
    finally:
        if connection is not None:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM public.membership WHERE user_id = %s", (str(user_id),))
                    cursor.execute('DELETE FROM public."user" WHERE id = %s', (str(user_id),))
                    cursor.execute("DELETE FROM public.organization WHERE id = %s", (str(organization_id),))
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()
        if downgraded:
            command.upgrade(alembic_cfg, "0027")


def test_0023_upgrade_reconciles_old_property_schema_and_preexisting_0027_auth_objects(legacy_auth_database) -> None:
    """A DB stamped at 0023 can carry the old property schema and leaked auth objects.

    Production fully applied a separate auth migration that reused revision
    0023, so this repository's Property/MEA migration never ran; the auth
    functions and index were already present. The next upgrade must repair the
    complete known Property/MEA 0023 contract without losing legacy unit data,
    reconcile the auth state, and finish at 0028.
    """
    from tests.conftest import MIGRATOR_SYNC_URL

    backend_dir = Path(__file__).resolve().parents[1]
    alembic_cfg = Config(str(backend_dir / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", MIGRATOR_SYNC_URL)
    alembic_cfg.set_main_option("script_location", str(legacy_auth_database))
    migrated_to_head = False
    organization_id = uuid.uuid4()
    property_id = uuid.uuid4()
    unit_id = uuid.uuid4()
    legacy_seeded = False
    try:
        command.downgrade(alembic_cfg, "0022")
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO public.organization (id, name) VALUES (%s, %s)",
                    (str(organization_id), f"Legacy 0023 repair {organization_id}"),
                )
                cursor.execute(
                    "INSERT INTO public.property "
                    "(id, organization_id, name, management_type) VALUES (%s, %s, %s, 'WEG')",
                    (str(property_id), str(organization_id), "Legacy Objekt"),
                )
                cursor.execute(
                    "INSERT INTO public.unit "
                    "(id, organization_id, property_id, number, usage_type, mea) "
                    "VALUES (%s, %s, %s, 'Alt-1', 'WOHNUNG', -1.000000)",
                    (str(unit_id), str(organization_id), str(property_id)),
                )
        legacy_seeded = True
        command.stamp(alembic_cfg, "0023")
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE FUNCTION public.app_auth_lookup(login_email text)
                    RETURNS TABLE (
                        user_id uuid,
                        organization_id uuid,
                        password_hash text,
                        is_active boolean,
                        role text
                    )
                    LANGUAGE sql STABLE SECURITY DEFINER
                    SET search_path = pg_catalog, pg_temp
                    AS $$
                        WITH candidates AS (
                            SELECT u.id AS user_id,
                                   u.organization_id,
                                   u.password_hash,
                                   u.is_active,
                                   m.role
                            FROM public."user" AS u
                            JOIN public.membership AS m
                              ON m.user_id = u.id
                             AND m.organization_id = u.organization_id
                            WHERE pg_catalog.lower(pg_catalog.btrim(u.email)) =
                                  pg_catalog.lower(pg_catalog.btrim(login_email))
                        )
                        SELECT user_id, organization_id, password_hash, is_active, role
                        FROM candidates
                        WHERE (SELECT pg_catalog.count(*) FROM candidates) = 1
                    $$;
                    """
                )
                cursor.execute(
                    """
                    CREATE FUNCTION public.app_validate_session(
                        session_user_id uuid, session_organization_id uuid
                    )
                    RETURNS TABLE (role text)
                    LANGUAGE sql STABLE SECURITY DEFINER
                    SET search_path = pg_catalog, pg_temp
                    AS $$
                        SELECT m.role
                        FROM public."user" AS u
                        JOIN public.membership AS m
                          ON m.user_id = u.id
                         AND m.organization_id = u.organization_id
                        WHERE u.id = session_user_id
                          AND u.organization_id = session_organization_id
                          AND u.is_active
                    $$;
                    """
                )
                cursor.execute(
                    """
                    CREATE UNIQUE INDEX uq_user_org_canonical_email
                    ON public."user"
                    (organization_id, pg_catalog.lower(pg_catalog.btrim(email)))
                    """
                )

        command.upgrade(alembic_cfg, "head")
        migrated_to_head = True

        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT version_num FROM alembic_version")
                assert cursor.fetchone() == ("0028",)
                cursor.execute(
                    "SELECT mea_denominator, mea_scale_status FROM public.property WHERE id = %s",
                    (str(property_id),),
                )
                assert cursor.fetchone() == (None, "LEGACY_UNCONFIRMED")
                cursor.execute(
                    "SELECT mea::text, building_part FROM public.unit WHERE id = %s",
                    (str(unit_id),),
                )
                assert cursor.fetchone() == ("-1.000000", "")
                cursor.execute(
                    "SELECT column_name, data_type, numeric_precision, numeric_scale, "
                    "character_maximum_length, is_nullable "
                    "FROM information_schema.columns WHERE table_schema = 'public' "
                    "AND table_name = 'unit' AND column_name IN ('mea', 'building_part') "
                    "ORDER BY column_name"
                )
                assert cursor.fetchall() == [
                    ("building_part", "character varying", None, None, 255, "NO"),
                    ("mea", "numeric", 19, 6, None, "YES"),
                ]
                cursor.execute(
                    "SELECT conname, convalidated FROM pg_constraint "
                    "WHERE conrelid IN ('public.property'::regclass, 'public.unit'::regclass) "
                    "AND conname IN ('ck_property_mea_scale_status', "
                    "'ck_property_mea_scale_consistency', 'ck_unit_mea_nonnegative') "
                    "ORDER BY conname"
                )
                assert cursor.fetchall() == [
                    ("ck_property_mea_scale_consistency", True),
                    ("ck_property_mea_scale_status", True),
                    ("ck_unit_mea_nonnegative", False),
                ]
                for signature in (
                    "public.app_auth_lookup(text)",
                    "public.app_validate_session(uuid,uuid)",
                ):
                    cursor.execute(
                        """
                        SELECT pg_get_functiondef(CAST(%s AS regprocedure)),
                               has_function_privilege('app_rw', CAST(%s AS regprocedure), 'EXECUTE'),
                               has_function_privilege('public', CAST(%s AS regprocedure), 'EXECUTE')
                        """,
                        (signature, signature, signature),
                    )
                    definition, app_rw_execute, public_execute = cursor.fetchone()
                    assert "SECURITY DEFINER" in definition
                    assert "SET search_path TO 'pg_catalog', 'pg_temp'" in definition
                    assert app_rw_execute is True
                    assert public_execute is False
                cursor.execute(
                    """
                    SELECT i.indisunique,
                           i.indpred IS NULL,
                           i.indnkeyatts,
                           i.indnatts,
                           pg_get_indexdef(i.indexrelid, 1, true),
                           pg_get_indexdef(i.indexrelid, 2, true)
                    FROM pg_index AS i
                    WHERE i.indexrelid = 'public.uq_user_org_canonical_email'::regclass
                    """
                )
                assert cursor.fetchone() == (
                    True,
                    True,
                    2,
                    2,
                    "organization_id",
                    "lower(btrim(email::text))",
                )
    finally:
        if legacy_seeded:
            with psycopg2.connect(MIGRATOR_SYNC_URL) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM public.unit WHERE id = %s", (str(unit_id),))
                    cursor.execute("DELETE FROM public.property WHERE id = %s", (str(property_id),))
                    cursor.execute("DELETE FROM public.organization WHERE id = %s", (str(organization_id),))
        if not migrated_to_head:
            with psycopg2.connect(MIGRATOR_SYNC_URL) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("DROP INDEX IF EXISTS public.uq_user_org_canonical_email")
                    cursor.execute("DROP FUNCTION IF EXISTS public.app_validate_session(uuid, uuid)")
                    cursor.execute("DROP FUNCTION IF EXISTS public.app_auth_lookup(text)")
            command.upgrade(alembic_cfg, "head")


def test_0026_refuses_an_incompatible_preexisting_building_part_column(legacy_auth_database) -> None:
    """Only the complete known 0022-as-0023 state is repaired automatically."""
    from tests.conftest import MIGRATOR_SYNC_URL

    backend_dir = Path(__file__).resolve().parents[1]
    alembic_cfg = Config(str(backend_dir / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", MIGRATOR_SYNC_URL)
    alembic_cfg.set_main_option("script_location", str(legacy_auth_database))
    try:
        command.downgrade(alembic_cfg, "0022")
        command.stamp(alembic_cfg, "0023")
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute("ALTER TABLE public.unit ADD COLUMN building_part text")

        with pytest.raises(InternalError, match="incompatible property/MEA schema"):
            command.upgrade(alembic_cfg, "0026")
    finally:
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute("ALTER TABLE public.unit DROP COLUMN IF EXISTS building_part")
        command.upgrade(alembic_cfg, "head")


def test_0027_refuses_a_conflicting_canonical_email_index(legacy_auth_database) -> None:
    """A same-named index with different key semantics must stop the upgrade."""
    from tests.conftest import MIGRATOR_SYNC_URL

    backend_dir = Path(__file__).resolve().parents[1]
    alembic_cfg = Config(str(backend_dir / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", MIGRATOR_SYNC_URL)
    alembic_cfg.set_main_option("script_location", str(legacy_auth_database))
    try:
        command.downgrade(alembic_cfg, "0026")
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE UNIQUE INDEX uq_user_org_canonical_email
                    ON public."user" (organization_id, email)
                    """
                )

        with pytest.raises(InternalError, match="conflicting canonical-email index"):
            command.upgrade(alembic_cfg, "0027")
    finally:
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute("DROP INDEX IF EXISTS public.uq_user_org_canonical_email")
                cursor.execute("DROP FUNCTION IF EXISTS public.app_validate_session(uuid, uuid)")
                cursor.execute("DROP FUNCTION IF EXISTS public.app_auth_lookup(text)")
        command.upgrade(alembic_cfg, "head")


def test_0027_refuses_a_canonical_email_index_on_another_table(legacy_auth_database) -> None:
    """A matching index name and expression are insufficient on a decoy table."""
    from tests.conftest import MIGRATOR_SYNC_URL

    backend_dir = Path(__file__).resolve().parents[1]
    alembic_cfg = Config(str(backend_dir / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", MIGRATOR_SYNC_URL)
    alembic_cfg.set_main_option("script_location", str(legacy_auth_database))
    try:
        command.downgrade(alembic_cfg, "0026")
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE public.canonical_email_index_decoy (
                        organization_id uuid NOT NULL,
                        email varchar(320) NOT NULL
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE UNIQUE INDEX uq_user_org_canonical_email
                    ON public.canonical_email_index_decoy
                    (organization_id, pg_catalog.lower(pg_catalog.btrim(email)))
                    """
                )

        with pytest.raises(InternalError, match="conflicting canonical-email index"):
            command.upgrade(alembic_cfg, "0027")
    finally:
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute("DROP INDEX IF EXISTS public.uq_user_org_canonical_email")
                cursor.execute("DROP TABLE IF EXISTS public.canonical_email_index_decoy")
                cursor.execute("DROP FUNCTION IF EXISTS public.app_validate_session(uuid, uuid)")
                cursor.execute("DROP FUNCTION IF EXISTS public.app_auth_lookup(text)")
                cursor.execute("UPDATE alembic_version SET version_num = '0026'")
        command.upgrade(alembic_cfg, "head")


@pytest.mark.asyncio
async def test_app_rw_can_execute_login_lookup(app_rw_session, seed_data):
    result = await app_rw_session.execute(
        text("SELECT * FROM public.app_auth_lookup(:email)"),
        {"email": "  ADMIN@ALPHA.DE  "},
    )
    row = result.one()
    assert row.user_id == seed_data["user_a_admin_id"]
    assert row.organization_id == seed_data["org_a_id"]
    assert row.role == "OWNER_ADMIN"


@pytest.mark.asyncio
async def test_auth_functions_have_fixed_search_path_and_no_public_execute(app_rw_session):
    for signature in (
        "public.app_auth_lookup(text)",
        "public.app_validate_session(uuid,uuid)",
    ):
        search_path = await app_rw_session.scalar(
            text("SELECT proconfig FROM pg_proc WHERE oid = CAST(:signature AS regprocedure)"),
            {"signature": signature},
        )
        assert search_path == ["search_path=pg_catalog, pg_temp"]
        public_execute = await app_rw_session.scalar(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_proc p "
                "CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) acl "
                "WHERE p.oid = CAST(:signature AS regprocedure) "
                "AND acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'"
                ")"
            ),
            {"signature": signature},
        )
        assert public_execute is False


@pytest.mark.asyncio
async def test_unscoped_app_rw_select_remains_empty(app_rw_session):
    result = await app_rw_session.execute(text('SELECT id FROM public."user"'))
    assert result.all() == []


@pytest.mark.asyncio
async def test_normalized_email_uniqueness_is_per_org_and_login_fails_closed_across_orgs(
    migrator_session,
):
    email = f"duplicate-{uuid.uuid4().hex}@example.test"
    first_org_id = uuid.uuid4()
    second_org_id = uuid.uuid4()
    first_user = uuid.uuid4()
    second_user = uuid.uuid4()
    # Keep every setup write in a savepoint: partial setup or assertion failures
    # roll back cleanly, while the success path explicitly deletes in FK order.
    async with migrator_session.begin_nested():
        for org_id in (first_org_id, second_org_id):
            await migrator_session.execute(
                text("INSERT INTO public.organization (id, name) VALUES (:id, :name)"),
                {"id": org_id, "name": f"Duplicate {org_id}"},
            )
        await migrator_session.execute(
            text(
                'INSERT INTO public."user" (id, organization_id, email, password_hash, display_name) '
                "VALUES (:id, :org_id, :email, :password_hash, :display_name)"
            ),
            {
                "id": first_user,
                "org_id": first_org_id,
                "email": email,
                "password_hash": "hash",
                "display_name": "Duplicate",
            },
        )
        await migrator_session.execute(
            text(
                "INSERT INTO public.membership (id, organization_id, user_id, role) "
                "VALUES (:id, :org_id, :user_id, 'CLERK')"
            ),
            {"id": uuid.uuid4(), "org_id": first_org_id, "user_id": first_user},
        )

        with pytest.raises(IntegrityError):
            async with migrator_session.begin_nested():
                await migrator_session.execute(
                    text(
                        'INSERT INTO public."user" (id, organization_id, email, password_hash, display_name) '
                        "VALUES (:id, :org_id, :email, :password_hash, :display_name)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "org_id": first_org_id,
                        "email": f" {email.upper()} ",
                        "password_hash": "hash",
                        "display_name": "Duplicate",
                    },
                )

        await migrator_session.execute(
            text(
                'INSERT INTO public."user" (id, organization_id, email, password_hash, display_name) '
                "VALUES (:id, :org_id, :email, :password_hash, :display_name)"
            ),
            {
                "id": second_user,
                "org_id": second_org_id,
                "email": f" {email.upper()} ",
                "password_hash": "hash",
                "display_name": "Duplicate",
            },
        )
        await migrator_session.execute(
            text(
                "INSERT INTO public.membership (id, organization_id, user_id, role) "
                "VALUES (:id, :org_id, :user_id, 'CLERK')"
            ),
            {"id": uuid.uuid4(), "org_id": second_org_id, "user_id": second_user},
        )
        result = await migrator_session.execute(
            text("SELECT * FROM public.app_auth_lookup(:email)"), {"email": email}
        )
        assert result.all() == []
        for user_id in (first_user, second_user):
            await migrator_session.execute(
                text("DELETE FROM public.membership WHERE user_id = :user_id"), {"user_id": user_id}
            )
            await migrator_session.execute(
                text('DELETE FROM public."user" WHERE id = :user_id'), {"user_id": user_id}
            )
        for org_id in (first_org_id, second_org_id):
            await migrator_session.execute(
                text("DELETE FROM public.organization WHERE id = :organization_id"),
                {"organization_id": org_id},
            )


@pytest.mark.asyncio
async def test_session_validation_requires_active_matching_membership(
    app_rw_session, seed_data
):
    valid = await app_rw_session.execute(
        text("SELECT * FROM public.app_validate_session(:user_id, :organization_id)"),
        {"user_id": seed_data["user_a_admin_id"], "organization_id": seed_data["org_a_id"]},
    )
    assert valid.scalar_one() == "OWNER_ADMIN"

    wrong_org = await app_rw_session.execute(
        text("SELECT * FROM public.app_validate_session(:user_id, :organization_id)"),
        {"user_id": seed_data["user_a_admin_id"], "organization_id": seed_data["org_b_id"]},
    )
    assert wrong_org.all() == []


@pytest.mark.asyncio
async def test_session_validation_rejects_inactive_user(migrator_session, seed_data):
    # Roll back the whole security mutation, not only is_active: reactivation
    # deliberately does not undo durable session invalidation in production.
    savepoint = await migrator_session.begin_nested()
    try:
        await migrator_session.execute(
            text('UPDATE public."user" SET is_active = false WHERE id = :user_id'),
            {"user_id": seed_data["user_a_admin_id"]},
        )
        result = await migrator_session.execute(
            text("SELECT * FROM public.app_validate_session(:user_id, :organization_id)"),
            {"user_id": seed_data["user_a_admin_id"], "organization_id": seed_data["org_a_id"]},
        )
        assert result.all() == []
    finally:
        await savepoint.rollback()
