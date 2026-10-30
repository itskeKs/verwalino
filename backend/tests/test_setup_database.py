"""First-organization bootstrap security metadata and atomicity."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from tests.setup_support import TOKEN, fresh_setup  # noqa: F401


@pytest.mark.asyncio
async def test_runtime_cannot_create_an_organization_or_change_the_token_directly(fresh_setup):
    _, connection = fresh_setup
    for query in ("INSERT INTO organization(name) VALUES ('Bypass')",
                  "UPDATE setup_token SET used_at=NULL", "DELETE FROM setup_token"):
        with pytest.raises(DBAPIError) as error:
            async with connection.begin_nested():
                await connection.execute(text(query))
        assert error.value.orig.sqlstate == "42501"


@pytest.mark.asyncio
async def test_migrator_bootstrap_invalidates_a_pending_token(fresh_setup):
    _, connection = fresh_setup
    await connection.execute(text("RESET ROLE"))
    org = await connection.scalar(text("SELECT bootstrap_organization_admin('CLI organization','cli@example.test','Admin','hash')"))
    assert org is not None
    assert await connection.scalar(text("SELECT used_at IS NOT NULL FROM setup_token"))
    assert await connection.scalar(text("SELECT count(*) FROM membership WHERE role='OWNER_ADMIN'")) == 1


@pytest.mark.asyncio
async def test_downgrade_refuses_to_discard_pending_or_consumed_tokens(fresh_setup):
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    _, connection = fresh_setup
    await connection.execute(text("RESET ROLE"))
    migration = importlib.import_module("migrations.versions.0141_browser_setup")

    def downgrade(sync_connection):
        with Operations.context(MigrationContext.configure(sync_connection)):
            migration.downgrade()

    for used in (False, True):
        if used:
            await connection.execute(text("UPDATE setup_token SET used_at=now()"))
        with pytest.raises(DBAPIError, match="Cannot discard retained setup token state"):
            async with connection.begin_nested():
                await connection.run_sync(downgrade)
        assert await connection.scalar(text("SELECT count(*) FROM setup_token")) == 1


@pytest.mark.asyncio
async def test_setup_function_has_fixed_security_settings_and_narrow_acl(app_rw_session):
    signature = {"signature": "public.setup_first_organization(text,text,text,text,text)"}
    metadata = text("SELECT pg_get_userbyid(proowner) AS owner,prosecdef,proconfig FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)")
    security = (await app_rw_session.execute(metadata, signature)).one()
    assert security.owner == 'app_migrator'
    assert security.prosecdef is True
    assert security.proconfig == ['search_path=pg_catalog, pg_temp', 'row_security=off']
    bootstrap = (await app_rw_session.execute(metadata, {"signature": "public.bootstrap_organization_admin(text,text,text,text)"})).one()
    assert bootstrap.prosecdef is False
    assert bootstrap.proconfig == ['search_path=pg_catalog, pg_temp', 'row_security=off']
    grants = (await app_rw_session.execute(text("""SELECT pg_get_userbyid(a.grantee),a.privilege_type,a.is_grantable
        FROM pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
        WHERE p.oid=CAST(:signature AS regprocedure) AND a.grantee<>p.proowner"""), signature)).all()
    assert grants == [('app_rw', 'EXECUTE', False)]
    assert not await app_rw_session.scalar(text("SELECT has_table_privilege(current_user,'public.organization','INSERT')"))
    assert not await app_rw_session.scalar(text("SELECT has_table_privilege(current_user,'public.setup_token','SELECT')"))
    assert not await app_rw_session.scalar(text("SELECT has_function_privilege(current_user,'public.bootstrap_organization_admin(text,text,text,text)','EXECUTE')"))


@pytest.mark.asyncio
async def test_direct_function_failures_are_generic_and_atomic(fresh_setup):
    _, connection = fresh_setup
    sql = text("SELECT setup_first_organization(:token,:org,:email,:name,:password)")
    args = {"token": TOKEN, "org": "Example", "email": "setup@example.test", "name": "Admin", "password": "hash"}
    for changes in ({"token": "wrong"}, {"name": None}):
        with pytest.raises(DBAPIError) as error:
            async with connection.begin_nested():
                await connection.execute(sql, args | changes)
        assert error.value.orig.sqlstate == "P0001"
        assert "Setup refused" in str(error.value.orig)
        assert await connection.scalar(text("SELECT count(*) FROM organization")) == 0
    org = await connection.scalar(sql, args)
    assert org is not None
    # An exact replay is refused even through SQL, independently of the API guard.
    with pytest.raises(DBAPIError):
        async with connection.begin_nested():
            await connection.execute(sql, args)
