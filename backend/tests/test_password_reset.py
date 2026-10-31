"""Password reset capability, atomic consumption and session revocation."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


@pytest.mark.asyncio
async def test_reset_definer_has_fixed_metadata_and_only_runtime_execute(app_rw_session):
    args = {'signature': 'public.reset_password_with_token(text,text)'}
    row = (await app_rw_session.execute(text('SELECT pg_get_userbyid(proowner),prosecdef,proconfig FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)'), args)).one()
    assert tuple(row) == ('app_migrator', True, ['search_path=pg_catalog, pg_temp', 'row_security=off'])
    grants = (await app_rw_session.execute(text("""SELECT pg_get_userbyid(a.grantee),a.privilege_type,a.is_grantable
        FROM pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
        WHERE p.oid=CAST(:signature AS regprocedure) AND a.grantee<>p.proowner"""), args)).all()
    assert grants == [('app_rw', 'EXECUTE', False)]
    grants = (await app_rw_session.execute(text("""SELECT grantee,privilege_type FROM information_schema.table_privileges
        WHERE table_schema='public' AND table_name='password_reset_request' AND grantee<>'app_migrator'
        ORDER BY grantee,privilege_type"""))).all()
    assert grants == [('app_rw', command) for command in ('DELETE', 'INSERT', 'SELECT', 'UPDATE')]


def test_reset_migration_roundtrip_is_exact_and_refuses_retained_state(run_migrations):
    from alembic.operations import Operations
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_session_review_access import _chain_snapshot
    migration = ScriptDirectory('migrations').get_revision('0144').module
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            before = _chain_snapshot(connection)
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                assert connection.scalar(text("SELECT to_regclass('public.password_reset_request')")) is None
                migration.upgrade()
                assert _chain_snapshot(connection) == before
                connection.execute(text("INSERT INTO organization(name) VALUES ('Reset roundtrip') RETURNING id"))
                actor = connection.execute(text('''INSERT INTO "user"(organization_id,email,display_name,password_hash)
                    SELECT id,'reset-roundtrip@example.test','Reset','hash' FROM organization WHERE name='Reset roundtrip'
                    RETURNING id,organization_id''')).one()
                connection.execute(text('''INSERT INTO password_reset_request(user_id,organization_id,token_hash)
                    VALUES (:user,:org,sha256('roundtrip'::bytea))'''), {'user': actor.id, 'org': actor.organization_id})
                with pytest.raises(DBAPIError, match='Cannot discard retained password reset state'):
                    with connection.begin_nested():
                        migration.downgrade()
            transaction.rollback()
    finally:
        engine.dispose()
