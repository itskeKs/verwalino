"""Password reset capability, atomic consumption and session revocation."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


@pytest.mark.asyncio
@pytest.mark.parametrize('signature', ['public.reset_password_with_token(text,text)', 'public.issue_password_reset(text)', 'public.cleanup_password_reset_requests()'])
async def test_reset_definer_has_fixed_metadata_and_only_runtime_execute(app_rw_session, signature):
    args = {'signature': signature}
    row = (await app_rw_session.execute(text('SELECT pg_get_userbyid(proowner),prosecdef,proconfig FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)'), args)).one()
    assert tuple(row) == ('app_migrator', True, ['search_path=pg_catalog, pg_temp', 'row_security=off'])
    grants = (await app_rw_session.execute(text("""SELECT pg_get_userbyid(a.grantee),a.privilege_type,a.is_grantable
        FROM pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
        WHERE p.oid=CAST(:signature AS regprocedure) AND a.grantee<>p.proowner"""), args)).all()
    assert grants == [('app_rw', 'EXECUTE', False)]
    grants = (await app_rw_session.execute(text("""SELECT grantee,privilege_type FROM information_schema.table_privileges
        WHERE table_schema='public' AND table_name='password_reset_request' AND grantee<>'app_migrator'
        ORDER BY grantee,privilege_type"""))).all()
    assert grants == []


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


from tests.password_reset_support import MESSAGE, REFUSED, NEW_PASSWORD, reset_token, reset_store, signup_mail  # noqa: E402,F401
from tests.test_auth_password import password_identity  # noqa: E402,F401


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['selfhost', 'hosted'])
async def test_reset_mail_and_new_password_revoke_all_old_sessions(password_identity, client_unauth, signup_mail, monkeypatch, mode):
    import hashlib
    from datetime import timedelta
    from app.core.config import settings
    old, email = password_identity
    monkeypatch.setattr(settings, 'deployment_mode', mode)
    reply = await client_unauth.post('/api/auth/password-reset', json={'email': email.upper()})
    assert (reply.status_code, reply.json()) == (202, MESSAGE)
    token = reset_token(signup_mail)
    assert len(bytes.fromhex(token)) == 32
    async with reset_store() as session:
        pending = (await session.execute(text('SELECT * FROM password_reset_request'))).mappings().one()
        assert pending['token_hash'] == hashlib.sha256(token.encode()).digest()
        assert pending['expires_at'] - pending['created_at'] == timedelta(hours=1)
        assert token not in str(pending)
    assert (await old.get('/api/auth/session')).status_code == 200
    response = await client_unauth.post('/api/auth/password-reset/confirm', json={'token': token, 'password': NEW_PASSWORD})
    assert response.status_code == 204, response.text
    assert 'set-cookie' not in response.headers
    assert (await old.get('/api/auth/session')).status_code == 401
    assert len(signup_mail.messages) == 2
    from email import policy
    from email.parser import BytesParser
    assert 'Ihr Passwort wurde geändert' in BytesParser(policy=policy.default).parsebytes(signup_mail.messages[-1][0]).get_content()
    assert (await client_unauth.post('/api/auth/login', json={'email': email, 'password': 'testpass1234'})).status_code == 401
    assert (await client_unauth.post('/api/auth/login', json={'email': email, 'password': NEW_PASSWORD})).status_code == 200
    assert (await client_unauth.post('/api/auth/password-reset/confirm', json={'token': token, 'password': NEW_PASSWORD})).json() == REFUSED


@pytest.mark.asyncio
async def test_unknown_address_is_identical_and_does_the_same_hash_and_mail_construction(password_identity, client_unauth, signup_mail, monkeypatch):
    from app.api import password_reset
    old, email = password_identity
    hashes, messages = [], []
    original_hash, original_message = password_reset.hash_password, password_reset.mail_service.build_password_reset
    def hashed(value):
        hashes.append(True)
        return original_hash(value)
    def built(*args, **kwargs):
        messages.append(True)
        return original_message(*args, **kwargs)
    monkeypatch.setattr(password_reset, 'hash_password', hashed)
    monkeypatch.setattr(password_reset.mail_service, 'build_password_reset', built)
    known = await client_unauth.post('/api/auth/password-reset', json={'email': email})
    unknown = await client_unauth.post('/api/auth/password-reset', json={'email': 'unknown@example.test'})
    assert (known.status_code, known.content, known.headers.raw) == (unknown.status_code, unknown.content, unknown.headers.raw)
    assert len(signup_mail.messages) == 1
    assert len(hashes) == len(messages) == 2
    assert 'cache-control' in known.headers and known.headers['cache-control'] == 'no-store'


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['expired', 'used', 'wrong', 'missing'])
async def test_expired_used_wrong_and_missing_tokens_share_a_refusal(password_identity, client_unauth, signup_mail, state):
    _, email = password_identity
    await client_unauth.post('/api/auth/password-reset', json={'email': email})
    token = reset_token(signup_mail)
    if state in {'expired', 'used'}:
        async with reset_store() as session:
            await session.execute(text('UPDATE password_reset_request SET ' + ("expires_at=clock_timestamp()-interval '1 second'" if state == 'expired' else 'used_at=clock_timestamp()')))
    else:
        token = 'wrong-token' if state == 'wrong' else ''
    response = await client_unauth.post('/api/auth/password-reset/confirm', json={'token': token, 'password': NEW_PASSWORD})
    assert (response.status_code, response.json()) == (403, REFUSED)
    assert len(signup_mail.messages) == 1


@pytest.mark.asyncio
async def test_confirm_counts_all_ip_requests_and_only_token_failures(password_identity, client_unauth, signup_mail):
    from app.core.db import get_session_factory
    from app.core.rate_limit import key_hash
    from app.core.deployment_policy import get_deployment_policy
    _, email = password_identity
    for _ in range(get_deployment_policy().rate_limit_rules.ip_failures + 1):
        response = await client_unauth.post('/api/auth/password-reset/confirm', json={'token': 'wrong-token', 'password': NEW_PASSWORD})
        assert (response.status_code, response.json()) == (403, REFUSED)
    async with get_session_factory()() as session, session.begin():
        assert await session.scalar(text('SELECT failures FROM auth_throttle WHERE key_hash=:hash'), {'hash': key_hash('password_reset_confirm', 'ip', '127.0.0.1')}) == get_deployment_policy().rate_limit_rules.ip_failures + 1
        await session.execute(text('DELETE FROM auth_throttle'))
    await client_unauth.post('/api/auth/password-reset', json={'email': email})
    token = reset_token(signup_mail)
    response = await client_unauth.post('/api/auth/password-reset/confirm', json={'token': token, 'password': NEW_PASSWORD})
    assert response.status_code == 204
    import hashlib
    token_key = key_hash('password_reset_confirm', 'email', hashlib.sha256(token.encode()).hexdigest() + '@password-reset.invalid')
    async with get_session_factory()() as session:
        assert await session.scalar(text('SELECT count(*) FROM auth_throttle WHERE key_hash=:key'), {'key': token_key}) == 0
    assert (await client_unauth.post('/api/auth/password-reset/confirm', json={'token': token, 'password': NEW_PASSWORD})).status_code == 403
    async with get_session_factory()() as session:
        assert await session.scalar(text('SELECT failures FROM auth_throttle WHERE key_hash=:key'), {'key': token_key}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('password', ['short', 'x' * 129, '', '😀' * 6, '😀' * 129])
async def test_password_policy_refuses_without_consuming_token(password_identity, client_unauth, signup_mail, password):
    from app.core.db import get_session_factory
    _, email = password_identity
    await client_unauth.post('/api/auth/password-reset', json={'email': email})
    token = reset_token(signup_mail)
    response = await client_unauth.post('/api/auth/password-reset/confirm', json={'token': token, 'password': password})
    assert (response.status_code, response.json()) == (422, {'detail': 'Das Passwort muss zwischen 12 und 128 Zeichen lang sein.'})
    async with get_session_factory()() as session:
        assert await session.scalar(text('SELECT count(*) FROM auth_throttle')) == 2  # Request IP and recipient only.
    async with reset_store() as session:
        assert await session.scalar(text('SELECT used_at IS NULL FROM password_reset_request'))


@pytest.mark.asyncio
async def test_honeypot_and_missing_mail_configuration_return_generic_acceptance(password_identity, client_unauth, signup_mail, monkeypatch, caplog):
    from app.core.config import settings
    from app.api import password_reset
    _, email = password_identity
    assert (await client_unauth.post('/api/auth/password-reset', json={'email': email, 'website': 'spam'})).json() == MESSAGE
    assert not signup_mail.messages
    monkeypatch.setattr(settings, 'smtp_host', None)
    monkeypatch.setattr(password_reset, '_warned_unavailable', False)
    monkeypatch.setattr(password_reset.logger, 'disabled', False)
    for _ in range(2):
        reply = await client_unauth.post('/api/auth/password-reset', json={'email': email})
        assert (reply.status_code, reply.json()) == (202, MESSAGE)
    warnings = [r for r in caplog.records if r.name == password_reset.__name__ and r.levelname == 'WARNING']
    assert len(warnings) == 1 and email not in warnings[0].getMessage()
    assert not signup_mail.messages
    assert (await client_unauth.get('/api/meta')).json()['mail_available'] is False


@pytest.mark.asyncio
async def test_confirmation_runs_same_hash_work_for_absent_expired_and_valid_tokens(password_identity, client_unauth, signup_mail, monkeypatch):
    from app.api import password_reset
    _, email = password_identity
    await client_unauth.post('/api/auth/password-reset', json={'email': email})
    token = reset_token(signup_mail)
    hashes = []
    original = password_reset.hash_password
    def hashed(value):
        hashes.append(True)
        return original(value)
    monkeypatch.setattr(password_reset, 'hash_password', hashed)
    for candidate in ['absent', token, token]:
        await client_unauth.post('/api/auth/password-reset/confirm', json={'token': candidate, 'password': NEW_PASSWORD})
    assert len(hashes) == 3


@pytest.mark.asyncio
async def test_concurrent_confirmations_consume_once_and_invalidate_other_links(password_identity, client_unauth, signup_mail):
    import asyncio
    old, email = password_identity
    tokens = []
    for _ in range(2):
        await client_unauth.post('/api/auth/password-reset', json={'email': email})
        tokens.append(reset_token(signup_mail))
    replies = await asyncio.gather(*(client_unauth.post('/api/auth/password-reset/confirm', json={'token': tokens[1], 'password': NEW_PASSWORD}) for _ in range(2)))
    assert sorted(r.status_code for r in replies) == [204, 403]
    assert (await client_unauth.post('/api/auth/password-reset/confirm', json={'token': tokens[0], 'password': NEW_PASSWORD})).status_code == 403
    async with reset_store() as session:
        assert await session.scalar(text('SELECT count(*) FROM password_reset_request WHERE used_at IS NULL')) == 0
    assert (await old.get('/api/auth/session')).status_code == 401
    assert len(signup_mail.messages) == 3


@pytest.mark.asyncio
async def test_newly_issued_link_consumes_the_previous_pending_link(password_identity, client_unauth, signup_mail):
    _, email = password_identity
    await client_unauth.post('/api/auth/password-reset', json={'email': email})
    first = reset_token(signup_mail)
    await client_unauth.post('/api/auth/password-reset', json={'email': email})
    second = reset_token(signup_mail)
    assert (await client_unauth.post('/api/auth/password-reset/confirm', json={'token': first, 'password': NEW_PASSWORD})).status_code == 403
    assert (await client_unauth.post('/api/auth/password-reset/confirm', json={'token': second, 'password': NEW_PASSWORD})).status_code == 204


@pytest.mark.asyncio
async def test_request_scope_limits_mail_and_does_not_lock_login(password_identity, client_unauth, signup_mail):
    from app.core.deployment_policy import get_deployment_policy
    _, email = password_identity
    limit = get_deployment_policy().rate_limit_rules.ip_failures
    for _ in range(limit + 1):
        reply = await client_unauth.post('/api/auth/password-reset', json={'email': email})
        assert (reply.status_code, reply.json()) == (202, MESSAGE)
    assert len(signup_mail.messages) == 3
    assert (await client_unauth.post('/api/auth/login', json={'email': email, 'password': 'testpass1234'})).status_code == 200


@pytest.mark.asyncio
async def test_admin_reset_still_uses_existing_authorization_and_audit(client_a_admin, seed_data):
    from tests.test_auth_hardening import temporary_auth_rows
    from tests.conftest import MIGRATOR_SYNC_URL
    import psycopg2
    # Create a target in the existing administrator's tenant without mutating seeds.
    with temporary_auth_rows([{'email': 'admin-reset-target@example.test'}]) as rows:
        row = rows[0]
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
            cursor.execute('UPDATE membership SET organization_id=%s WHERE user_id=%s', (str(seed_data['org_a_id']), str(row['user_id'])))
            cursor.execute('UPDATE "user" SET organization_id=%s WHERE id=%s', (str(seed_data['org_a_id']), str(row['user_id'])))
        response = await client_a_admin.post(f"/api/users/{row['user_id']}/reset-password", json={'password': NEW_PASSWORD})
        assert response.status_code == 204, response.text
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
            cursor.execute('SELECT session_version FROM "user" WHERE id=%s', (str(row['user_id']),))
            assert cursor.fetchone() == (1,)
            cursor.execute("SELECT count(*) FROM audit_event WHERE entity_id=%s AND action='RESET_PASSWORD'", (str(row['user_id']),))
            assert cursor.fetchone() == (1,)
            cursor.execute('DELETE FROM audit_event WHERE entity_id=%s', (str(row['user_id']),))


@pytest.mark.asyncio
async def test_reset_queries_are_removed_before_access_logging_and_get_has_no_effect(client_unauth, password_identity, signup_mail):
    from app.api.signup import ConfirmationLinkPrivacyMiddleware
    _, email = password_identity
    await client_unauth.post('/api/auth/password-reset', json={'email': email})
    token = reset_token(signup_mail)
    seen = []
    async def downstream(scope, receive, send):
        seen.append(scope['query_string'])
    scope = {'type': 'http', 'path': '/passwort-zuruecksetzen', 'query_string': ('token=' + token).encode()}
    await ConfirmationLinkPrivacyMiddleware(downstream)(scope, None, None)
    assert seen == [b''] and scope['query_string'] == b''
    await client_unauth.get('/passwort-zuruecksetzen', params={'token': token})
    assert (await client_unauth.post('/api/auth/password-reset/confirm', json={'token': token, 'password': NEW_PASSWORD})).status_code == 204


@pytest.mark.asyncio
async def test_invalid_bodies_do_not_echo_secrets_or_count_confirm_failures(client_unauth):
    from app.core.db import get_session_factory
    from app.core.rate_limit import key_hash
    for body in [{'token': 'x' * 257, 'password': NEW_PASSWORD}, {'token': 123, 'password': NEW_PASSWORD}, {'token': 'secret-token'}]:
        reply = await client_unauth.post('/api/auth/password-reset/confirm', json=body)
        assert reply.status_code == 422
        assert 'secret-token' not in reply.text and NEW_PASSWORD not in reply.text
    async with get_session_factory()() as session:
        assert await session.scalar(text('SELECT count(*) FROM auth_throttle')) == 0


@pytest.mark.asyncio
async def test_smtp_failure_keeps_generic_receipt_and_cannot_undo_success(password_identity, client_unauth, signup_mail, monkeypatch):
    import smtplib
    from app.services import mail_service
    _, email = password_identity
    await client_unauth.post('/api/auth/password-reset', json={'email': email})
    token = reset_token(signup_mail)
    def unavailable(*args):
        raise smtplib.SMTPException('private recipient diagnostics')
    monkeypatch.setattr(mail_service, 'submit_password_mail', unavailable)
    assert (await client_unauth.post('/api/auth/password-reset', json={'email': 'unknown@example.test'})).json() == MESSAGE
    assert (await client_unauth.post('/api/auth/password-reset/confirm', json={'token': token, 'password': NEW_PASSWORD})).status_code == 204
    assert (await client_unauth.post('/api/auth/login', json={'email': email, 'password': NEW_PASSWORD})).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('smtp_from', 'invalid\r\nInjected: value'), ('smtp_security', 'invalid'), ('public_url', None)])
async def test_unusable_mail_configuration_is_always_generic(password_identity, client_unauth, signup_mail, monkeypatch, field, value):
    from app.core.config import settings
    _, email = password_identity
    monkeypatch.setattr(settings, field, value)
    response = await client_unauth.post('/api/auth/password-reset', json={'email': email})
    assert (response.status_code, response.json()) == (202, MESSAGE)
    assert not signup_mail.messages
    assert (await client_unauth.get('/api/meta')).json()['mail_available'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['app_rw', 'app_worker'])
@pytest.mark.parametrize('command', ['SELECT * FROM password_reset_request',
    "INSERT INTO password_reset_request(user_id,organization_id,token_hash) VALUES (gen_random_uuid(),gen_random_uuid(),sha256('forged'::bytea))",
    'UPDATE password_reset_request SET used_at=clock_timestamp()', 'DELETE FROM password_reset_request'])
async def test_runtime_cannot_read_or_forge_reset_rows(app_rw_session, role, command):
    # The worker inherits app_rw; has_table_privilege resolves inherited ACLs too.
    assert not await app_rw_session.scalar(text("SELECT has_table_privilege(:role,'password_reset_request',:privilege)"),
        {'role': role, 'privilege': command.split()[0]})
    from app.core.db import get_worker_session_factory
    if role == 'app_rw':
        with pytest.raises(DBAPIError) as refused:
            async with app_rw_session.begin_nested():
                await app_rw_session.execute(text(command))
    else:
        with pytest.raises(DBAPIError) as refused:
            async with get_worker_session_factory()() as session, session.begin():
                await session.execute(text(command))
    assert refused.value.orig.sqlstate == '42501'


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['issue', 'confirm'])
async def test_tenant_context_refuses_both_definers_without_changing_victim(password_identity, client_unauth, signup_mail, app_rw_session, seed_data, operation):
    from app.core.security import hash_password
    _, email = password_identity
    await client_unauth.post('/api/auth/password-reset', json={'email': email})
    token = reset_token(signup_mail)
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id',:org,true)"), {'org': str(seed_data['org_a_id'])})
    if operation == 'issue':
        assert await app_rw_session.scalar(text('SELECT public.issue_password_reset(:email)'), {'email': email}) is None
    else:
        with pytest.raises(DBAPIError) as refused:
            async with app_rw_session.begin_nested():
                await app_rw_session.execute(text('SELECT public.reset_password_with_token(:token,:hash)'),
                    {'token': token, 'hash': hash_password(NEW_PASSWORD)})
        assert refused.value.orig.sqlstate == 'P0001'
        assert 'Password reset refused' in str(refused.value.orig)
    assert (await client_unauth.post('/api/auth/login', json={'email': email, 'password': 'testpass1234'})).status_code == 200
    assert (await client_unauth.post('/api/auth/password-reset/confirm', json={'token': token, 'password': NEW_PASSWORD})).status_code == 204


@pytest.mark.asyncio
@pytest.mark.parametrize('identities', [[], [False], [True, True], [True, False]])
async def test_issuer_requires_exactly_one_active_normalized_identity(app_rw_session, identities):
    from tests.test_auth_hardening import temporary_auth_rows
    email = 'issuer-uniqueness@example.test'
    with temporary_auth_rows([{'email': email, 'is_active': active} for active in identities]):
        issued = await app_rw_session.scalar(text('SELECT public.issue_password_reset(:email)'), {'email': '  ' + email.upper() + '  '})
        assert (issued is not None) == (identities.count(True) == 1)
        # Release user/ACL locks before the fixture deletes its committed users.
        await app_rw_session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['self', 'admin', 'deactivate'])
async def test_account_changes_invalidate_outstanding_links(password_identity, client_unauth, signup_mail, change):
    actor, email = password_identity
    await client_unauth.post('/api/auth/password-reset', json={'email': email})
    token = reset_token(signup_mail)
    identity = (await actor.get('/api/auth/session')).json()['user_id']
    created_target = False
    try:
        if change == 'self':
            result = await actor.post('/api/auth/password', json={'old_password': 'testpass1234', 'new_password': NEW_PASSWORD})
        elif change == 'admin':
            result = await actor.post(f'/api/users/{identity}/reset-password', json={'password': NEW_PASSWORD})
        else:
            # Keep an administrator active while deactivating the recovery target.
            target = await actor.post('/api/users', json={'email': 'deactivate-reset@example.test', 'password': NEW_PASSWORD, 'role': 'CLERK'})
            assert target.status_code == 201, target.text
            identity = target.json()['id']
            created_target = True
            await client_unauth.post('/api/auth/password-reset', json={'email': 'deactivate-reset@example.test'})
            token = reset_token(signup_mail)
            result = await actor.patch(f'/api/users/{identity}', json={'is_active': False})
        assert result.status_code in (200, 204), result.text
        assert (await client_unauth.post('/api/auth/password-reset/confirm', json={'token': token, 'password': 'AnotherPassword789!'})).status_code == 403
        async with reset_store() as session:
            assert await session.scalar(text('SELECT count(*) FROM password_reset_request WHERE user_id=CAST(:user AS uuid) AND used_at IS NULL'), {'user': identity}) == 0
    finally:
        async with reset_store() as session:
            await session.execute(text('DELETE FROM audit_event WHERE entity_id=CAST(:user AS uuid)'), {'user': identity})
            if created_target:
                await session.execute(text('DELETE FROM membership WHERE user_id=CAST(:user AS uuid)'), {'user': identity})
                await session.execute(text('DELETE FROM "user" WHERE id=CAST(:user AS uuid)'), {'user': identity})


@pytest.mark.asyncio
@pytest.mark.parametrize('email', ['invalid', 'name@@example.test', 'name\r\n@example.test'])
async def test_malformed_request_address_has_a_german_format_error(client_unauth, email):
    reply = await client_unauth.post('/api/auth/password-reset', json={'email': email})
    assert (reply.status_code, reply.json()) == (422, {'detail': 'Bitte geben Sie eine gültige E-Mail-Adresse ein.'})


@pytest.mark.asyncio
async def test_policy_errors_do_not_look_up_tokens_and_unicode_bounds_use_code_points(client_unauth, password_identity, signup_mail, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    _, email = password_identity
    await client_unauth.post('/api/auth/password-reset', json={'email': email})
    token = reset_token(signup_mail)
    original = AsyncSession.execute
    async def no_lookup(*args, **kwargs):
        pytest.fail('Policy errors must precede database work')
    with monkeypatch.context() as scoped:
        scoped.setattr(AsyncSession, 'execute', no_lookup)
        for candidate in [token, 'absent']:
            reply = await client_unauth.post('/api/auth/password-reset/confirm', json={'token': candidate, 'password': '😀' * 6})
            assert reply.status_code == 422
    assert AsyncSession.execute is original
    assert (await client_unauth.post('/api/auth/password-reset/confirm', json={'token': token, 'password': '😀' * 128})).status_code == 204


@pytest.mark.asyncio
async def test_recipient_cap_counts_only_issued_mail_across_ips_and_expires_after_an_hour(password_identity, client_unauth, signup_mail, monkeypatch):
    from datetime import timedelta
    from app.core import rate_limit
    from app.core.db import get_session_factory
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    _, email = password_identity
    now = rate_limit.utcnow()
    monkeypatch.setattr(rate_limit, 'utcnow', lambda: now)
    for index in range(5):
        async with AsyncClient(transport=ASGITransport(app=app, client=(f'192.0.2.{index + 1}', 123)), base_url='http://test') as client:
            reply = await client.post('/api/auth/password-reset', json={'email': '  ' + email.upper() + '  '})
            assert (reply.status_code, reply.json()) == (202, MESSAGE)
    assert len(signup_mail.messages) == 3
    async with get_session_factory()() as session:
        assert await session.scalar(text("SELECT failures FROM auth_throttle WHERE key_hash=:key"),
            {'key': rate_limit.key_hash('password_reset_recipient', 'email', email)}) == 3
    for _ in range(4):
        await client_unauth.post('/api/auth/password-reset', json={'email': 'absent@example.test'})
    async with get_session_factory()() as session:
        assert not await session.scalar(text("SELECT count(*) FROM auth_throttle WHERE key_hash=:key"),
            {'key': rate_limit.key_hash('password_reset_recipient', 'email', 'absent@example.test')})
    now += timedelta(hours=1)
    await client_unauth.post('/api/auth/password-reset', json={'email': email})
    assert len(signup_mail.messages) == 4


@pytest.mark.asyncio
async def test_concurrent_requests_cannot_exceed_recipient_mail_cap(password_identity, client_unauth, signup_mail):
    import asyncio
    _, email = password_identity
    replies = await asyncio.gather(*(client_unauth.post('/api/auth/password-reset', json={'email': email}) for _ in range(6)))
    assert all((reply.status_code, reply.json()) == (202, MESSAGE) for reply in replies)
    assert len(signup_mail.messages) == 3


@pytest.mark.asyncio
async def test_recipient_cap_cannot_burst_across_a_partial_hour_window(password_identity, client_unauth, signup_mail, monkeypatch):
    from datetime import timedelta
    from app.core import rate_limit
    _, email = password_identity
    start = rate_limit.utcnow()
    for minutes in [0, 59, 61, 62]:
        monkeypatch.setattr(rate_limit, 'utcnow', lambda: start + timedelta(minutes=minutes))
        reply = await client_unauth.post('/api/auth/password-reset', json={'email': email})
        assert (reply.status_code, reply.json()) == (202, MESSAGE)
    assert len(signup_mail.messages) == 3


@pytest.mark.asyncio
async def test_reset_cleanup_is_bounded_and_runs_after_the_request_transaction(password_identity, client_unauth, signup_mail, monkeypatch):
    import itertools
    from app.api import password_reset
    from app.core import rate_limit
    from sqlalchemy.ext.asyncio import AsyncSession
    _, email = password_identity
    async with reset_store() as session:
        await session.execute(text('''INSERT INTO password_reset_request(user_id,organization_id,token_hash,expires_at,used_at)
            SELECT u.id,u.organization_id,sha256(n::text::bytea),
              CASE WHEN n<=105 THEN clock_timestamp()-interval '8 days' ELSE clock_timestamp()+interval '1 hour' END,
              CASE WHEN n>105 THEN clock_timestamp()-interval '8 days' END
            FROM "user" u CROSS JOIN generate_series(1,110) n WHERE u.email=:email'''), {'email': email})
        await session.execute(text('''INSERT INTO password_reset_request(user_id,organization_id,token_hash,expires_at)
            SELECT id,organization_id,sha256('recent'::bytea),clock_timestamp()-interval '6 days' FROM "user" WHERE email=:email'''), {'email': email})
    transactions = []
    original = AsyncSession.begin
    def track(session):
        assert all(not previous.in_transaction() for previous in transactions)
        transactions.append(session)
        return original(session)
    monkeypatch.setattr(rate_limit, 'cleanup_due', lambda: False)
    monkeypatch.setattr(password_reset, '_reset_attempts', itertools.count(99), raising=False)
    with monkeypatch.context() as scoped:
        scoped.setattr(AsyncSession, 'begin', track)
        for _ in range(2):
            assert (await client_unauth.post('/api/auth/password-reset', json={'email': email})).status_code == 202
    async with reset_store() as session:
        assert await session.scalar(text('SELECT count(*) FROM password_reset_request')) == 13
        assert await session.scalar(text("SELECT count(*) FROM password_reset_request WHERE token_hash=sha256('recent'::bytea)")) == 1
    assert transactions[-1] is not transactions[-2]
