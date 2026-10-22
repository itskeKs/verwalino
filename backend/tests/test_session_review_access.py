"""Final T57 regressions: isolated review runtime and unknown legacy roles."""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import MIGRATOR_URL
from tests.test_rent_changes import rent_db, rent_world, api_client  # noqa: F401
from tests.test_rbac import t57_user, t57_guest

pytestmark = pytest.mark.asyncio


async def test_review_runtime_validates_generation_without_password_lookup(rent_world):
    w = rent_world
    engine = create_async_engine(MIGRATOR_URL)
    try:
        async with engine.begin() as connection:
            await connection.execute(text('SET LOCAL ROLE app_review'))
            assert await connection.scalar(text("SELECT has_function_privilege(current_user,'public.app_auth_lookup_versioned(text)','EXECUTE')")) is False
            result = await connection.execute(text('SELECT role FROM public.app_validate_session_versioned(:actor,:org,0)'), {'actor': w['admin'], 'org': w['org']})
            assert result.scalars().all() == ['OWNER_ADMIN']
        async with api_client(w) as admin:
            response = await admin.post(f"/api/users/{w['admin']}/reset-password", json={'password': 'Final-Review-Reset-Password-123'})
            assert response.status_code == 204, response.text
        async with engine.begin() as connection:
            await connection.execute(text('SET LOCAL ROLE app_review'))
            for generation, expected in [(0, []), (1, ['OWNER_ADMIN'])]:
                result = await connection.execute(text('SELECT role FROM public.app_validate_session_versioned(:actor,:org,:generation)'), {'actor': w['admin'], 'org': w['org'], 'generation': generation})
                assert result.scalars().all() == expected
    finally:
        await engine.dispose()


async def test_unknown_legacy_role_is_visible_as_unknown_but_cannot_login(rent_world):
    w = rent_world
    async with api_client(w) as admin:
        credentials, target = await t57_user(admin)
        engine = create_async_engine(MIGRATOR_URL)
        try:
            async with engine.begin() as connection:
                await connection.execute(text('UPDATE membership SET role=\'UNKNOWN\' WHERE organization_id=:org AND user_id=:id'), {'org': w['org'], 'id': uuid.UUID(target['id'])})
        finally:
            await engine.dispose()
        response = await admin.get('/api/users')
        assert response.status_code == 200, response.text
        row = next(item for item in response.json() if item['id'] == target['id'])
        assert row['role'] is None
        assert (await admin.get(f"/api/users/{target['id']}")).json()['role'] is None
        async with t57_guest() as user:
            assert (await user.post('/api/auth/login', json={key: credentials[key] for key in ('email', 'password')})).status_code == 401


async def test_review_validation_grant_roundtrip_preserves_data_and_security(rent_world):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_letters import create_template, issue_body

    w = rent_world
    async with api_client(w) as admin:
        template = await create_template(admin)
        issued = await admin.post('/api/letters/issue', json=issue_body(w, template))
        assert issued.status_code == 201, issued.text
        url = issued.json()[0]['pdf_url']
        original = (await admin.get(url)).content
        assert original.startswith(b'%PDF')
    path = Path(__file__).parents[1] / 'migrations/versions/0139_review_session_validation.py'
    spec = importlib.util.spec_from_file_location('review_session_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ('0139', '0138')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()

            def snapshot():
                tables = connection.execute(text("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p') AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass AND d.objid=c.oid AND d.deptype='e') ORDER BY c.relname")).scalars().all()
                rows = {name: connection.execute(text(f'SELECT to_jsonb(t)::text FROM "{name}" t ORDER BY to_jsonb(t)::text')).scalars().all() for name in tables}
                queries = (
                    "SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,c.relacl::text,pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p') ORDER BY c.relname",
                    "SELECT tablename,policyname,permissive,roles::text,cmd,qual,with_check FROM pg_policies WHERE schemaname='public' ORDER BY tablename,policyname",
                    "SELECT pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY pg_get_triggerdef(t.oid)",
                    "SELECT pg_get_functiondef(p.oid),CASE WHEN p.proname='app_validate_session_versioned' THEN NULL ELSE p.proacl::text END,pg_get_userbyid(p.proowner) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind='f' ORDER BY p.proname,p.oid",
                )
                return rows, [connection.execute(text(query)).all() for query in queries]

            def allowed(function):
                return connection.scalar(text("SELECT has_function_privilege('app_review',:function,'EXECUTE')"), {'function': function})

            try:
                before = snapshot()
                assert before[0]['lease_agreement'] and before[0]['letter_document']
                assert allowed('public.app_validate_session_versioned(uuid,uuid,integer)')
                assert not allowed('public.app_auth_lookup_versioned(text)')
                acl = connection.scalar(text("SELECT proacl::text FROM pg_proc WHERE oid='public.app_validate_session_versioned(uuid,uuid,integer)'::regprocedure"))
                with Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()
                    assert not allowed('public.app_validate_session_versioned(uuid,uuid,integer)')
                    assert snapshot() == before
                    migration.upgrade()
                    assert allowed('public.app_validate_session_versioned(uuid,uuid,integer)')
                    assert not allowed('public.app_auth_lookup_versioned(text)')
                    assert snapshot() == before
                    assert connection.scalar(text("SELECT proacl::text FROM pg_proc WHERE oid='public.app_validate_session_versioned(uuid,uuid,integer)'::regprocedure")) == acl
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
    async with api_client(w) as admin:
        assert (await admin.get(url)).content == original
