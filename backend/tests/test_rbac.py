"""RBAC tests -- architecture.md section 7.4, assertion 6.

Tests that roles are enforced:
  - READ_ONLY can read, but cannot create/modify.
  - CLERK can operate (create data), but cannot manage users.
  - OWNER_ADMIN can do everything within their org.

Combined with isolation: all within the same org (org_a).
"""

import pytest
import uuid


class TestReadOnlyRole:
    """READ_ONLY can read but not write."""

    @pytest.mark.asyncio
    async def test_readonly_can_list_mandates(self, client_a_readonly):
        resp = await client_a_readonly.get("/api/mandates")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_readonly_can_list_users(self, client_a_readonly):
        resp = await client_a_readonly.get("/api/users")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_readonly_can_view_org(self, client_a_readonly):
        resp = await client_a_readonly.get("/api/organizations/me")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_readonly_cannot_create_mandate(self, client_a_readonly):
        resp = await client_a_readonly.post(
            "/api/mandates",
            json={
                "mandate_type": "MIET",
                "label": "Should Fail",
                "start_date": "2025-01-01",
            },
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_readonly_cannot_create_user(self, client_a_readonly):
        resp = await client_a_readonly.post(
            "/api/users",
            json={
                "email": "readonly-attempt@alpha.de",
                "password": "testpassword12",
                "display_name": "Should Fail",
            },
        )
        assert resp.status_code == 403


class TestClerkRole:
    """CLERK can operate but not manage users/roles."""

    @pytest.mark.asyncio
    async def test_clerk_can_list_mandates(self, client_a_clerk):
        resp = await client_a_clerk.get("/api/mandates")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_clerk_cannot_create_mandate(self, client_a_clerk):
        """Mandates are OWNER_ADMIN only (per SDD section 4 role matrix)."""
        resp = await client_a_clerk.post(
            "/api/mandates",
            json={
                "mandate_type": "MIET",
                "label": "Clerk Attempt",
                "start_date": "2025-01-01",
            },
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_clerk_cannot_create_user(self, client_a_clerk):
        resp = await client_a_clerk.post(
            "/api/users",
            json={
                "email": "clerk-attempt@alpha.de",
                "password": "testpassword12",
            },
        )
        assert resp.status_code == 403


class TestOwnerAdminRole:
    """OWNER_ADMIN can do everything within their org."""

    @pytest.mark.asyncio
    async def test_admin_can_list_mandates(self, client_a_admin):
        resp = await client_a_admin.get("/api/mandates")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_admin_can_create_mandate(self, client_a_admin):
        resp = await client_a_admin.post(
            "/api/mandates",
            json={
                "mandate_type": "SEV",
                "label": "Admin Created SEV",
                "start_date": "2025-03-01",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["mandate_type"] == "SEV"
        assert data["label"] == "Admin Created SEV"

    @pytest.mark.asyncio
    async def test_admin_can_create_user(self, client_a_admin):
        resp = await client_a_admin.post(
            "/api/users",
            json={
                "email": f"newuser-{__import__('uuid').uuid4().hex[:8]}@alpha.de",
                "password": "securepass123",
                "display_name": "New User",
                "role": "CLERK",
            },
        )
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_admin_can_view_org(self, client_a_admin):
        resp = await client_a_admin.get("/api/organizations/me")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Verwaltung Alpha"


class TestCrossRoleIsolation:
    """OWNER_ADMIN of org_b cannot perform admin actions on org_a resources."""

    @pytest.mark.asyncio
    async def test_org_b_admin_cannot_see_org_a_users(self, client_b_admin, seed_data):
        resp = await client_b_admin.get(f"/api/users/{seed_data['user_a_admin_id']}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_org_b_admin_sees_own_org(self, client_b_admin):
        resp = await client_b_admin.get("/api/organizations/me")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Verwaltung Beta"


@pytest.mark.asyncio
async def test_created_clerk_can_login(client_a_admin, client_unauth):
    email = f"clerk-{uuid.uuid4()}@example.test"
    password = "T06-Strong-Test-Password"
    resp = await client_a_admin.post("/api/users", json={
        "email": email, "password": password, "display_name": "T06 Sachbearbeiter", "role": "CLERK",
    })
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["id"]
    resp = await client_unauth.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "CLERK"
    assert resp.json()["user_id"] == user_id
    resp = await client_unauth.get("/api/auth/session")
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "CLERK"


# T57 uses isolated organizations and real login cookies, not mocked principals.
from tests.test_rent_changes import rent_db, rent_world, api_client  # noqa: E402,F401


async def t57_user(api, role='CLERK'):
    value = {'email': f't57-{uuid.uuid4()}@example.invalid',
             'password': 'T57-Original-Password-123', 'display_name': 'Synthetic T57', 'role': role}
    response = await api.post('/api/users', json=value)
    assert response.status_code == 201, response.text
    return value, response.json()


def t57_guest():
    import httpx
    from app.main import app
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')


@pytest.mark.asyncio
async def test_t57_admin_role_change_persists_and_updates_existing_session(rent_world):
    async with api_client(rent_world) as admin, t57_guest() as user:
        credentials, row = await t57_user(admin)
        login = await user.post('/api/auth/login', json={key: credentials[key] for key in ('email', 'password')})
        assert login.status_code == 200, login.text
        changed = await admin.patch(f"/api/users/{row['id']}", json={'role': 'READ_ONLY'})
        assert changed.status_code == 200, changed.text
        assert changed.json()['role'] == 'READ_ONLY'
        assert (await admin.get(f"/api/users/{row['id']}")).json()['role'] == 'READ_ONLY'
        session = await user.get('/api/auth/session')
        assert session.status_code == 200 and session.json()['role'] == 'READ_ONLY'
        assert (await user.post('/api/properties', json={'name': 'Forbidden', 'management_type': 'MIET'})).status_code == 403


@pytest.mark.asyncio
async def test_t57_deactivation_blocks_login_and_old_cookie_never_revives(rent_world):
    async with api_client(rent_world) as admin, t57_guest() as user:
        credentials, row = await t57_user(admin)
        login_body = {key: credentials[key] for key in ('email', 'password')}
        assert (await user.post('/api/auth/login', json=login_body)).status_code == 200
        old_cookie = dict(user.cookies)
        changed = await admin.patch(f"/api/users/{row['id']}", json={'is_active': False})
        assert changed.status_code == 200, changed.text
        assert changed.json()['is_active'] is False
        assert (await user.get('/api/auth/session')).status_code == 401
        assert (await user.post('/api/auth/login', json=login_body)).status_code == 401
        assert (await admin.patch(f"/api/users/{row['id']}", json={'is_active': True})).status_code == 200
        user.cookies.clear()
        user.cookies.update(old_cookie)
        assert (await user.get('/api/auth/session')).status_code == 401
        assert (await user.post('/api/auth/login', json=login_body)).status_code == 200


@pytest.mark.asyncio
async def test_t57_admin_password_reset_revokes_sessions_without_returning_secret(rent_world):
    async with api_client(rent_world) as admin, t57_guest() as user:
        credentials, row = await t57_user(admin)
        login_body = {key: credentials[key] for key in ('email', 'password')}
        assert (await user.post('/api/auth/login', json=login_body)).status_code == 200
        response = await admin.post(f"/api/users/{row['id']}/reset-password", json={'password': 'T57-Replaced-Password-456'})
        assert response.status_code == 204, response.text
        assert response.content == b''
        assert (await user.get('/api/auth/session')).status_code == 401
        assert (await user.post('/api/auth/login', json=login_body)).status_code == 401
        assert (await user.post('/api/auth/login', json={**login_body, 'password': 'T57-Replaced-Password-456'})).status_code == 200
        result = (await admin.get(f"/api/users/{row['id']}")).json()
        assert not any(key in result for key in ('password', 'password_hash', 'session_version'))


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [{'is_active': False}, {'role': 'CLERK'}, {'role': 'READ_ONLY'}])
async def test_t57_admin_cannot_disable_or_demote_self(changes, rent_world):
    async with api_client(rent_world) as admin:
        response = await admin.patch(f"/api/users/{rent_world['admin']}", json=changes)
        assert response.status_code == 409, response.text
        session = await admin.get('/api/auth/session')
        assert session.status_code == 200 and session.json()['role'] == 'OWNER_ADMIN'


@pytest.mark.asyncio
@pytest.mark.parametrize('actor', ['clerk', 'reader'])
async def test_t57_nonadmin_cannot_manage_users(actor, rent_world):
    async with api_client(rent_world, actor=actor) as api:
        assert (await api.patch(f"/api/users/{rent_world['admin']}", json={'role': 'CLERK'})).status_code == 403
        assert (await api.post(f"/api/users/{rent_world['admin']}/reset-password", json={'password': 'T57-Replaced-Password-456'})).status_code == 403


@pytest.mark.asyncio
async def test_t57_foreign_user_is_not_revealed(rent_world):
    async with api_client(rent_world) as admin, api_client(rent_world, foreign=True) as foreign:
        _, row = await t57_user(admin)
        assert (await foreign.patch(f"/api/users/{row['id']}", json={'is_active': False})).status_code == 404
        assert (await foreign.post(f"/api/users/{row['id']}/reset-password", json={'password': 'T57-Replaced-Password-456'})).status_code == 404
        assert (await admin.get(f"/api/users/{row['id']}")).json()['is_active'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [{'role': 'ROOT'}, {'is_active': 1}, {}, {'email': 'invented@example.invalid'}])
async def test_t57_invalid_admin_patch_does_not_mutate(changes, rent_world):
    async with api_client(rent_world) as admin:
        _, row = await t57_user(admin)
        before = (await admin.get(f"/api/users/{row['id']}")).json()
        response = await admin.patch(f"/api/users/{row['id']}", json=changes)
        assert response.status_code == 422, response.text
        assert (await admin.get(f"/api/users/{row['id']}")).json() == before


@pytest.mark.asyncio
async def test_t57_password_policy_is_not_bypassed(rent_world):
    async with api_client(rent_world) as admin:
        _, row = await t57_user(admin)
        response = await admin.post(f"/api/users/{row['id']}/reset-password", json={'password': 'short'})
        assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_t57_self_service_password_change_also_invalidates_old_session(rent_world):
    async with api_client(rent_world) as admin, t57_guest() as user:
        credentials, row = await t57_user(admin)
        login_body = {key: credentials[key] for key in ('email', 'password')}
        assert (await user.post('/api/auth/login', json=login_body)).status_code == 200
        changed = await user.post('/api/auth/password', json={'old_password': credentials['password'],
            'new_password': 'T57-Self-Service-Replaced-789'})
        assert changed.status_code == 204, changed.text
        assert (await user.get('/api/auth/session')).status_code == 401
        assert (await user.post('/api/auth/login', json=login_body)).status_code == 401
        assert (await user.post('/api/auth/login', json={**login_body, 'password': 'T57-Self-Service-Replaced-789'})).status_code == 200


@pytest.mark.asyncio
async def test_t57_concurrent_admin_demotions_leave_one_live_administrator(rent_world):
    import asyncio

    async with api_client(rent_world) as first, t57_guest() as second:
        credentials, row = await t57_user(first, role='OWNER_ADMIN')
        login = await second.post('/api/auth/login', json={key: credentials[key] for key in ('email', 'password')})
        assert login.status_code == 200, login.text
        responses = await asyncio.gather(
            first.patch(f"/api/users/{row['id']}", json={'role': 'READ_ONLY'}),
            second.patch(f"/api/users/{rent_world['admin']}", json={'role': 'READ_ONLY'}),
        )
        assert sorted(response.status_code for response in responses) == [200, 403], [response.text for response in responses]
        current = [(await actor.get('/api/auth/session')).json()['role'] for actor in (first, second)]
        assert sorted(current) == ['OWNER_ADMIN', 'READ_ONLY']


@pytest.mark.asyncio
async def test_t57_admin_mutation_waits_for_existing_shared_acl_effect(rent_world, rent_db):
    import asyncio
    from sqlalchemy import text

    w = rent_world
    async with api_client(w) as admin:
        _, row = await t57_user(admin)
        await rent_db.execute(text('SELECT pg_advisory_xact_lock_shared(hashtextextended(:key,0))'),
                              {'key': f"scope-acl:{w['org']}"})
        blocker = await rent_db.scalar(text('SELECT pg_backend_pid()'))
        request = asyncio.create_task(admin.patch(f"/api/users/{row['id']}", json={'is_active': False}))
        waiting = False
        try:
            for _ in range(200):
                waiting = await rent_db.scalar(text("""SELECT EXISTS(SELECT 1 FROM pg_locks l
                    WHERE l.locktype='advisory' AND NOT l.granted
                    AND :blocker=ANY(pg_blocking_pids(l.pid)))"""), {'blocker': blocker})
                if waiting or request.done():
                    break
                await asyncio.sleep(0.01)
        finally:
            await rent_db.rollback()
        response = await asyncio.wait_for(request, timeout=15)
        assert waiting, 'Admin mutation must wait for already authorized shared ACL effects'
        assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_t57_late_audit_failure_rolls_back_password_and_revocation(rent_world, monkeypatch):
    from fastapi import HTTPException
    from app.repositories.audit_repo import AuditRepository

    async with api_client(rent_world) as admin, t57_guest() as user:
        credentials, row = await t57_user(admin)
        login_body = {key: credentials[key] for key in ('email', 'password')}
        assert (await user.post('/api/auth/login', json=login_body)).status_code == 200


        before = (await admin.get(f"/api/users/{row['id']}")).json()

        async def fail_audit(self, event):
            raise HTTPException(422, 'Injected late audit failure')

        with monkeypatch.context() as patch:
            patch.setattr(AuditRepository, 'create', fail_audit)
            response = await admin.post(f"/api/users/{row['id']}/reset-password",
                                        json={'password': 'T57-Rolled-Back-Password-012'})
        assert response.status_code == 422, response.text
        assert (await admin.get(f"/api/users/{row['id']}")).json() == before
        assert (await user.get('/api/auth/session')).status_code == 200
        assert (await user.post('/api/auth/login', json=login_body)).status_code == 200


@pytest.mark.asyncio
async def test_t57_stale_admin_snapshot_cannot_authorize_after_reset(rent_world):
    from app.main import app
    from app.core.tenant import AuthContext, get_current_user

    w = rent_world
    async with api_client(w) as admin:
        _, target = await t57_user(admin)
        snapshot = AuthContext(user_id=w['admin'], organization_id=w['org'], role='OWNER_ADMIN')
        reset = await admin.post(f"/api/users/{w['admin']}/reset-password", json={'password': 'T57-Admin-New-Password-678'})
        assert reset.status_code == 204, reset.text

        async def old_authenticated_snapshot():
            return snapshot

        app.dependency_overrides[get_current_user] = old_authenticated_snapshot
        try:
            response = await admin.patch(f"/api/users/{target['id']}", json={'is_active': False})
            assert response.status_code == 401, response.text
        finally:
            app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.parametrize('version', [True, -1, '1', 1.5, None, {}])
def test_t57_signed_cookie_still_rejects_invalid_generation_types(version):
    from app.core.security import _serializer, decode_session_token
    token = _serializer.dumps({'uid': str(uuid.uuid4()), 'oid': str(uuid.uuid4()), 'sv': version}, salt='hv-session')
    assert decode_session_token(token) is None


@pytest.mark.asyncio
async def test_t57_generation_migration_preserves_old_rows_security_and_original_pdf(rent_world):
    import importlib.util
    import re
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_letters import create_template, issue_body

    w = rent_world
    async with api_client(w) as admin:
        _, target = await t57_user(admin)
        reset = await admin.post(f"/api/users/{target['id']}/reset-password", json={'password': 'T57-Roundtrip-Password-345'})
        assert reset.status_code == 204, reset.text
        template = await create_template(admin)
        issued = await admin.post('/api/letters/issue', json=issue_body(w, template))
        assert issued.status_code == 201, issued.text
        pdf_url = issued.json()[0]['pdf_url']
        original_pdf = (await admin.get(pdf_url)).content
        assert original_pdf.startswith(b'%PDF')
    paths = list((Path(__file__).parents[1] / 'migrations/versions').glob('0138_*.py'))
    assert len(paths) == 1
    source = paths[0].read_text()
    functions = sorted(set(re.findall(r'CREATE(?:\s+OR\s+REPLACE)?\s+FUNCTION\s+(?:public\.)?([a-z_0-9]+)\s*\(', source, re.I)))
    triggers = sorted(set(re.findall(r'CREATE\s+TRIGGER\s+([a-z_0-9]+)', source, re.I)))
    assert functions and triggers
    assert not set(functions) & {'app_auth_lookup', 'app_validate_session', 'scope_actor_role', 'lock_scope_principal_change'}
    spec = importlib.util.spec_from_file_location('t57_migration', paths[0])
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ('0138', '0137')
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()

            def snapshot():
                names = connection.execute(text("""SELECT c.relname FROM pg_class c
                    JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public' AND c.relkind IN ('r','p')
                    AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass
                        AND d.objid=c.oid AND d.deptype='e') ORDER BY c.relname""")).scalars().all()
                rows = {}
                for name in names:
                    expression = "to_jsonb(t) - 'session_version'" if name == 'user' else 'to_jsonb(t)'
                    rows[name] = connection.execute(text(f'SELECT ({expression})::text FROM "{name}" t ORDER BY ({expression})::text')).scalars().all()
                queries = (
                    "SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,c.relacl::text,pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p') ORDER BY c.relname",
                    "SELECT tablename,policyname,permissive,roles::text,cmd,qual,with_check FROM pg_policies WHERE schemaname='public' ORDER BY tablename,policyname",
                    "SELECT pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal AND NOT (t.tgname=ANY(CAST(:triggers AS text[]))) ORDER BY pg_get_triggerdef(t.oid)",
                    "SELECT pg_get_functiondef(p.oid),p.proacl::text,pg_get_userbyid(p.proowner) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind='f' AND NOT (p.proname=ANY(CAST(:functions AS text[]))) ORDER BY p.proname,p.oid",
                )
                return rows, [connection.execute(text(query), {'functions': functions, 'triggers': triggers}).all() for query in queries]

            try:
                before = snapshot()
                generation = connection.scalar(text('SELECT session_version FROM "user" WHERE id=:id'), {'id': target['id']})
                assert generation > 0
                assert before[0]['lease_agreement'] and before[0]['letter_document']
                with pytest.raises(DBAPIError):
                    with connection.begin_nested():
                        connection.execute(text('SET LOCAL ROLE app_rw'))
                        connection.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"), {'org': str(w['org']), 'actor': str(w['admin'])})
                        connection.execute(text('UPDATE "user" SET session_version=0 WHERE id=:id'), {'id': target['id']})
                with Operations.context(MigrationContext.configure(connection)):
                    with pytest.raises(DBAPIError) as refusal:
                        with connection.begin_nested():
                            migration.downgrade()
                    assert 'session' in str(refusal.value).lower()
                    assert snapshot() == before
                    # Privileged migrator, rollback-only; no old column is changed.
                    connection.execute(text('UPDATE "user" SET session_version=0 WHERE session_version<>0'))
                    connection.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
                    migration.downgrade()
                    assert snapshot() == before
                    migration.upgrade()
                    assert snapshot() == before
                    assert connection.scalar(text('SELECT count(*) FROM "user" WHERE session_version<>0')) == 0
            finally:
                transaction.rollback()
            assert connection.scalar(text('SELECT session_version FROM "user" WHERE id=:id'), {'id': target['id']}) == generation
    finally:
        engine.dispose()
    async with api_client(w) as admin:
        assert (await admin.get(pdf_url)).content == original_pdf
