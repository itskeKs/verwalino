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
    migration = ScriptDirectory('migrations').get_revision('0144').module  # accepted source
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


from tests.password_reset_support import MESSAGE, REFUSED, NEW_PASSWORD, reset_token, signup_mail  # noqa: E402,F401
from tests.test_auth_password import password_identity  # noqa: E402,F401


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['selfhost', 'hosted'])
async def test_reset_mail_and_new_password_revoke_all_old_sessions(password_identity, client_unauth, signup_mail, monkeypatch, mode):
    import hashlib
    from datetime import timedelta
    from app.core.config import settings
    from app.core.db import get_session_factory
    old, email = password_identity
    monkeypatch.setattr(settings, 'deployment_mode', mode)
    reply = await client_unauth.post('/api/auth/password-reset', json={'email': email.upper()})
    assert (reply.status_code, reply.json()) == (202, MESSAGE)
    token = reset_token(signup_mail)
    assert len(bytes.fromhex(token)) == 32
    async with get_session_factory()() as session:
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
    from app.core.db import get_session_factory
    _, email = password_identity
    await client_unauth.post('/api/auth/password-reset', json={'email': email})
    token = reset_token(signup_mail)
    if state in {'expired', 'used'}:
        async with get_session_factory()() as session, session.begin():
            await session.execute(text('UPDATE password_reset_request SET ' + ("expires_at=clock_timestamp()-interval '1 second'" if state == 'expired' else 'used_at=clock_timestamp()')))
    else:
        token = 'wrong-token' if state == 'wrong' else ''
    response = await client_unauth.post('/api/auth/password-reset/confirm', json={'token': token, 'password': NEW_PASSWORD})
    assert (response.status_code, response.json()) == (403, REFUSED)
    assert len(signup_mail.messages) == 1


@pytest.mark.asyncio
async def test_confirm_counts_all_ip_requests_and_only_user_failures(password_identity, client_unauth, signup_mail):
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
    async with get_session_factory()() as session:
        assert await session.scalar(text("SELECT count(*) FROM auth_throttle WHERE key_kind='email'")) == 0
    assert (await client_unauth.post('/api/auth/password-reset/confirm', json={'token': token, 'password': NEW_PASSWORD})).status_code == 403
    async with get_session_factory()() as session:
        assert await session.scalar(text("SELECT failures FROM auth_throttle WHERE key_kind='email'")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('password', ['short', 'x' * 129, ''])
async def test_password_policy_refuses_without_consuming_token(password_identity, client_unauth, signup_mail, password):
    from app.core.db import get_session_factory
    _, email = password_identity
    await client_unauth.post('/api/auth/password-reset', json={'email': email})
    token = reset_token(signup_mail)
    response = await client_unauth.post('/api/auth/password-reset/confirm', json={'token': token, 'password': password})
    assert (response.status_code, response.json()) == (403, REFUSED)
    async with get_session_factory()() as session:
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
    from app.core.db import get_session_factory
    old, email = password_identity
    tokens = []
    for _ in range(2):
        await client_unauth.post('/api/auth/password-reset', json={'email': email})
        tokens.append(reset_token(signup_mail))
    replies = await asyncio.gather(*(client_unauth.post('/api/auth/password-reset/confirm', json={'token': tokens[0], 'password': NEW_PASSWORD}) for _ in range(2)))
    assert sorted(r.status_code for r in replies) == [204, 403]
    assert (await client_unauth.post('/api/auth/password-reset/confirm', json={'token': tokens[1], 'password': NEW_PASSWORD})).status_code == 403
    async with get_session_factory()() as session:
        assert await session.scalar(text('SELECT count(*) FROM password_reset_request WHERE used_at IS NULL')) == 0
    assert (await old.get('/api/auth/session')).status_code == 401
    assert len(signup_mail.messages) == 3


@pytest.mark.asyncio
async def test_request_scope_limits_mail_and_does_not_lock_login(password_identity, client_unauth, signup_mail):
    from app.core.deployment_policy import get_deployment_policy
    _, email = password_identity
    limit = get_deployment_policy().rate_limit_rules.ip_failures
    for _ in range(limit + 1):
        reply = await client_unauth.post('/api/auth/password-reset', json={'email': email})
        assert (reply.status_code, reply.json()) == (202, MESSAGE)
    assert len(signup_mail.messages) == limit
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
async def test_invalid_bodies_do_not_echo_secrets_and_count_ip_requests(client_unauth):
    from app.core.db import get_session_factory
    from app.core.rate_limit import key_hash
    for body in [{'token': 'x' * 257, 'password': NEW_PASSWORD}, {'token': 123, 'password': NEW_PASSWORD}, {'token': 'secret-token'}]:
        reply = await client_unauth.post('/api/auth/password-reset/confirm', json=body)
        assert (reply.status_code, reply.json()) == (403, REFUSED)
        assert 'secret-token' not in reply.text and NEW_PASSWORD not in reply.text
    async with get_session_factory()() as session:
        assert await session.scalar(text('SELECT failures FROM auth_throttle WHERE key_hash=:hash'), {'hash': key_hash('password_reset_confirm', 'ip', '127.0.0.1')}) == 3


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
    assert (await client_unauth.post('/api/auth/password-reset', json={'email': email})).json() == MESSAGE
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
