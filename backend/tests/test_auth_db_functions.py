"""Database authentication functions use the app runtime role safely."""

from __future__ import annotations

from pathlib import Path
import uuid

import pytest
import psycopg2
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


def test_0027_accepts_a_single_legacy_email_created_under_0022():
    """A 0022-valid legacy address stays usable after the full 0027 upgrade."""
    from tests.conftest import MIGRATOR_SYNC_URL

    backend_dir = Path(__file__).resolve().parents[1]
    alembic_cfg = Config(str(backend_dir / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", MIGRATOR_SYNC_URL)
    alembic_cfg.set_main_option("script_location", str(backend_dir / "migrations"))
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
    await migrator_session.execute(
        text('UPDATE public."user" SET is_active = false WHERE id = :user_id'),
        {"user_id": seed_data["user_a_admin_id"]},
    )
    try:
        result = await migrator_session.execute(
            text("SELECT * FROM public.app_validate_session(:user_id, :organization_id)"),
            {"user_id": seed_data["user_a_admin_id"], "organization_id": seed_data["org_a_id"]},
        )
        assert result.all() == []
    finally:
        await migrator_session.execute(
            text('UPDATE public."user" SET is_active = true WHERE id = :user_id'),
            {"user_id": seed_data["user_a_admin_id"]},
        )
