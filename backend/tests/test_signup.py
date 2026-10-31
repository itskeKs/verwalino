"""Hosted signup, database capability and lifecycle boundaries."""
import asyncio
import hashlib

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.config import settings
from tests.setup_support import fresh_setup  # noqa: F401
from tests.signup_support import BODY, MESSAGE, REFUSED, confirmation_token, signup, signup_mail  # noqa: F401


@pytest.mark.asyncio
async def test_signup_confirm_and_login_use_the_runtime_role(signup):
    client, connection, mail = signup
    response = await client.post('/api/signup', json=BODY)
    assert (response.status_code, response.json()) == (202, MESSAGE)
    token = confirmation_token(mail)
    assert len(bytes.fromhex(token)) == 32
    pending = (await connection.execute(text('SELECT * FROM signup_request'))).mappings().one()
    assert pending['email_normalized'] == 'signup@example.test'
    assert pending['token_hash'] == hashlib.sha256(token.encode()).digest()
    assert pending['expires_at'] - pending['created_at'] == __import__('datetime').timedelta(hours=24)
    assert BODY['password'] not in str(pending) and token not in str(pending)
    assert await connection.scalar(text('SELECT count(*) FROM organization')) == 0
    login = await client.post('/api/auth/login', json={'email': BODY['email'], 'password': BODY['password']})
    assert (login.status_code, login.json()) == (401, {'detail': 'Invalid credentials'})
    response = await client.post('/api/signup/confirm', json={'token': token, 'password': BODY['password']})
    assert response.status_code == 201, response.text
    assert response.json()['role'] == 'OWNER_ADMIN'
    assert 'HttpOnly' in response.headers['set-cookie']
    assert (await client.get('/api/auth/session')).json() == response.json()
    assert await connection.scalar(text('SELECT count(*) FROM signup_request')) == 0
    await client.post('/api/auth/logout')
    assert (await client.post('/api/auth/login', json={'email': BODY['email'], 'password': BODY['password']})).status_code == 200


@pytest.mark.asyncio
async def test_signup_hashing_runs_outside_transactions_for_submit_and_confirmation(signup, monkeypatch):
    from app.api import signup as signup_api
    from app.core.deployment_policy import get_deployment_policy

    client, connection, mail = signup
    factory = signup_api.get_session_factory()
    sessions, hashes = [], []

    def tracked_session():
        session = factory()
        sessions.append(session)
        return session

    def outside_transaction(function):
        def checked(*args):
            assert sessions and all(not session.in_transaction() for session in sessions)
            hashes.append(function.__name__)
            return function(*args)
        return checked

    monkeypatch.setattr(signup_api, 'get_session_factory', lambda: tracked_session)
    monkeypatch.setattr(signup_api, 'hash_password', outside_transaction(signup_api.hash_password))
    monkeypatch.setattr(signup_api, 'verify_password', outside_transaction(signup_api.verify_password))
    for _ in range(2):
        assert (await client.post('/api/signup', json=BODY)).status_code == 202
    token = confirmation_token(mail)
    for _ in range(get_deployment_policy().rate_limit_rules.email_failures + 1):
        assert (await client.post('/api/signup/confirm', json={'token': token, 'password': 'wrong'})).status_code == 403
    assert hashes.count('hash_password') == 2
    assert hashes.count('verify_password') == 2 + get_deployment_policy().rate_limit_rules.email_failures + 1
    await connection.execute(text('DELETE FROM auth_throttle'))
    assert (await client.post('/api/signup/confirm', json={'token': token, 'password': BODY['password']})).status_code == 201
    assert (await client.post('/api/signup', json=BODY)).status_code == 202
    assert (await client.post('/api/signup/confirm', json={'token': 'missing', 'password': BODY['password']})).status_code == 403


@pytest.mark.asyncio
async def test_confirmation_refuses_a_password_changed_between_check_and_record(signup, monkeypatch):
    from contextlib import asynccontextmanager
    from app.api import signup as signup_api
    from app.core import rate_limit
    from sqlalchemy.ext.asyncio import AsyncSession

    client, connection, mail = signup
    await client.post('/api/signup', json=BODY)
    token = confirmation_token(mail)
    replacement_hash = signup_api.hash_password('Replacement-password-123')
    original = AsyncSession.begin
    began = 0

    @asynccontextmanager
    async def change_before_record(session):
        nonlocal began
        began += 1
        if began == 2:
            await connection.execute(text('UPDATE signup_request SET password_hash=:hash'), {'hash': replacement_hash})
        async with original(session) as transaction:
            yield transaction

    monkeypatch.setattr(AsyncSession, 'begin', change_before_record)
    monkeypatch.setattr(rate_limit, 'cleanup_due', lambda: False)
    response = await client.post('/api/signup/confirm', json={'token': token, 'password': BODY['password']})
    assert (response.status_code, response.json()) == (403, REFUSED)
    assert began == 2
    assert await connection.scalar(text('SELECT count(*) FROM organization')) == 0
    assert await connection.scalar(text("SELECT failures FROM auth_throttle WHERE key_kind='email'")) == 1


@pytest.mark.asyncio
async def test_double_submit_and_resend_share_a_persistent_ten_minute_cooldown(signup):
    client, connection, mail = signup
    first = await client.post('/api/signup', json=BODY)
    token = confirmation_token(mail)
    second = await client.post('/api/signup', json=BODY | {'email': 'signup@example.test'})
    assert (second.status_code, second.content, second.headers.raw) == (first.status_code, first.content, first.headers.raw)
    assert (await client.post('/api/signup/resend', json={'email': BODY['email']})).json() == MESSAGE
    assert len(mail.messages) == 1
    assert await connection.scalar(text('SELECT count(*) FROM signup_request')) == 1
    await connection.execute(text("UPDATE signup_request SET created_at=created_at-interval '11 minutes'"))
    assert (await client.post('/api/signup/resend', json={'email': BODY['email']})).status_code == 202
    replacement = confirmation_token(mail)
    assert replacement != token and len(mail.messages) == 2
    assert (await client.post('/api/signup/confirm', json={'token': token, 'password': BODY['password']})).json() == REFUSED


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['expired', 'used', 'wrong', 'missing'])
async def test_wrong_expired_and_used_tokens_have_one_generic_refusal(signup, state):
    client, connection, mail = signup
    await client.post('/api/signup', json=BODY)
    token = confirmation_token(mail)
    if state == 'expired':
        await connection.execute(text("UPDATE signup_request SET expires_at=clock_timestamp()-interval '1 second'"))
    elif state == 'used':
        assert (await client.post('/api/signup/confirm', json={'token': token, 'password': BODY['password']})).status_code == 201
        await client.post('/api/auth/logout')
    else:
        token = '00' * 32 if state == 'wrong' else ''
    response = await client.post('/api/signup/confirm', json={'token': token, 'password': BODY['password']})
    assert (response.status_code, response.json()) == (403, REFUSED)
    assert 'set-cookie' not in response.headers


@pytest.mark.asyncio
async def test_honeypot_and_unknown_resend_have_no_signup_or_mail_side_effect(signup):
    client, connection, mail = signup
    for path, body in [('/api/signup', BODY | {'website': 'spam'}), ('/api/signup/resend', {'email': BODY['email']})]:
        response = await client.post(path, json=body)
        assert (response.status_code, response.json()) == (202, MESSAGE)
    assert await connection.scalar(text('SELECT count(*) FROM signup_request')) == 0
    assert not mail.messages


@pytest.mark.asyncio
async def test_resend_honeypot_preserves_the_delivered_link_and_cooldown(signup):
    client, connection, mail = signup
    await client.post('/api/signup', json=BODY)
    token = confirmation_token(mail)
    await connection.execute(text("UPDATE signup_request SET created_at=created_at-interval '11 minutes'"))
    response = await client.post('/api/signup/resend', json={'email': BODY['email'], 'website': 'spam'})
    assert (response.status_code, response.json()) == (202, MESSAGE)
    assert len(mail.messages) == 1
    assert await connection.scalar(text('SELECT resent_at IS NULL FROM signup_request'))
    assert (await client.post('/api/signup/confirm', json={'token': token, 'password': BODY['password']})).status_code == 201


@pytest.mark.asyncio
async def test_existing_email_gets_identical_acceptance_without_mail_or_request(client_unauth, seed_data, signup_mail, monkeypatch):
    monkeypatch.setattr(settings, 'deployment_mode', 'hosted')
    fresh = await client_unauth.post('/api/signup', json=BODY)
    try:
        existing = await client_unauth.post('/api/signup', json=BODY | {'email': ' ADMIN@ALPHA.DE '})
        assert (existing.status_code, existing.content, existing.headers.raw) == (fresh.status_code, fresh.content, fresh.headers.raw)
        assert len(signup_mail.messages) == 1
        from tests.conftest import MIGRATOR_SYNC_URL
        import psycopg2
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM signup_request WHERE email_normalized='admin@alpha.de'")
            assert cursor.fetchone() == (0,)
    finally:
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM signup_request WHERE email_normalized='signup@example.test'")


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['/api/signup', '/api/signup/confirm', '/api/signup/resend'])
async def test_selfhost_signup_matches_unknown_routes_before_parsing_or_database_access(client_unauth, monkeypatch, path):
    from app.core import db
    monkeypatch.setattr(settings, 'deployment_mode', 'selfhost')
    monkeypatch.setattr(db, '_session_factory', lambda: pytest.fail('Selfhost signup accessed the database'))
    unknown = await client_unauth.get('/api/not-a-route')
    for method in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD', 'TRACE', 'CONNECT'):
        response = await client_unauth.request(method, path, content='{')
        assert response.status_code == 404, (method, response.text)
        if method != 'HEAD':
            assert response.content == unknown.content


@pytest.mark.asyncio
async def test_signup_function_has_fixed_security_metadata_and_narrow_grants(app_rw_session):
    for name in ('provision_organization(text)', 'signup_email_registered(text)'):
        args = {'signature': 'public.' + name}
        row = (await app_rw_session.execute(text('SELECT pg_get_userbyid(proowner),prosecdef,proconfig FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)'), args)).one()
        assert tuple(row) == ('app_migrator', True, ['search_path=pg_catalog, pg_temp', 'row_security=off'])
        grants = (await app_rw_session.execute(text("""SELECT pg_get_userbyid(a.grantee),a.privilege_type,a.is_grantable
            FROM pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
            WHERE p.oid=CAST(:signature AS regprocedure) AND a.grantee<>p.proowner"""), args)).all()
        assert grants == [('app_rw', 'EXECUTE', False)]
    assert not await app_rw_session.scalar(text("SELECT has_table_privilege(current_user,'organization','INSERT')"))
    for name in ('bootstrap_organization_admin(text,text,text,text)',
                 'bootstrap_organization_admin(text,text,text,text,boolean)'):
        args = {'signature': 'public.' + name}
        row = (await app_rw_session.execute(text('SELECT pg_get_userbyid(proowner),prosecdef,proconfig FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)'), args)).one()
        assert tuple(row) == ('app_migrator', False, ['search_path=pg_catalog, pg_temp', 'row_security=off'])
        grants = (await app_rw_session.execute(text("""SELECT a.grantee FROM pg_proc p
            CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
            WHERE p.oid=CAST(:signature AS regprocedure) AND a.grantee<>p.proowner"""), args)).all()
        assert grants == []


def test_signup_migration_roundtrip_is_exact_and_refuses_retained_state(run_migrations):
    from alembic.operations import Operations
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_session_review_access import _chain_snapshot
    migration = ScriptDirectory('migrations').get_revision('0143').module
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            # Reuse the whole-schema/data/ACL snapshot used by release-chain
            # rehearsals: exact means more than reproducing the column names.
            connection.execute(text('DELETE FROM signup_request'))
            before = _chain_snapshot(connection)
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                assert connection.scalar(text("SELECT to_regclass('public.signup_request')")) is None
                assert not connection.scalar(text("SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='auth_throttle' AND column_name='last_lockout_at')"))
                connection.execute(text("""INSERT INTO auth_throttle(key_kind,key_hash,window_start,lockouts,updated_at)
                    VALUES ('email',sha256('legacy-lockout'::bytea),now(),2,now())"""))
                migration.upgrade()
                assert connection.scalar(text('SELECT last_lockout_at=updated_at FROM auth_throttle'))
                with pytest.raises(DBAPIError, match='Cannot discard retained auth throttle state'):
                    with connection.begin_nested():
                        migration.downgrade()
                connection.execute(text('DELETE FROM auth_throttle'))
                assert _chain_snapshot(connection) == before
                connection.execute(text("""INSERT INTO signup_request(email_normalized,organization_name,admin_name,password_hash,token_hash)
                    VALUES ('roundtrip@example.test','Synthetic','Admin','hash',sha256('token'::bytea))"""))
                with pytest.raises(DBAPIError, match='Cannot discard retained signup state'):
                    with connection.begin_nested():
                        migration.downgrade()
            transaction.rollback()
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_same_email_serializes_signup_and_confirmation(client_unauth, seed_data, signup_mail, monkeypatch):
    import uuid
    import psycopg2
    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.conftest import MIGRATOR_SYNC_URL

    monkeypatch.setattr(settings, 'deployment_mode', 'hosted')
    body = BODY | {'email': f'race-{uuid.uuid4().hex}@example.test', 'organization_name': f'Signup race {uuid.uuid4()}'}
    admitted, resume, contender = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = AsyncSession.execute

    async def pause_first_insert(session, statement, params=None, **kwargs):
        name = asyncio.current_task().get_name()
        if name == 'signup-two' and 'INSERT INTO public.auth_throttle' in str(statement):
            contender.set()
        result = await original(session, statement, params, **kwargs)
        if name == 'signup-one' and 'INSERT INTO public.auth_throttle' in str(statement) and params['key_kind'] == 'ip':
            admitted.set()
            await resume.wait()
        return result

    monkeypatch.setattr(AsyncSession, 'execute', pause_first_insert)
    first = asyncio.create_task(client_unauth.post('/api/signup', json=body), name='signup-one')
    second = None
    try:
        await asyncio.wait_for(admitted.wait(), 5)
        second = asyncio.create_task(client_unauth.post('/api/signup', json=body | {'email': body['email'].upper()}), name='signup-two')
        await asyncio.wait_for(contender.wait(), 5)
        # The second real session must wait behind the uncommitted first attempt.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(second), 0.1)
        resume.set()
        responses = await asyncio.wait_for(asyncio.gather(first, second), 10)
        assert [(r.status_code, r.json()) for r in responses] == [(202, MESSAGE)] * 2
        assert len(signup_mail.messages) == 1
        token = confirmation_token(signup_mail)
        replies = await asyncio.gather(*(client_unauth.post('/api/signup/confirm', json={'token': token, 'password': BODY['password']}) for _ in range(2)))
        assert sorted(r.status_code for r in replies) == [201, 403]
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
            cursor.execute('SELECT count(*) FROM signup_request WHERE email_normalized=%s', (body['email'],))
            assert cursor.fetchone() == (0,)
            cursor.execute('SELECT count(*) FROM "user" WHERE email=%s', (body['email'],))
            assert cursor.fetchone() == (1,)
            cursor.execute('SELECT count(*) FROM membership m JOIN "user" u ON u.id=m.user_id WHERE u.email=%s AND m.role=\'OWNER_ADMIN\'', (body['email'],))
            assert cursor.fetchone() == (1,)
    finally:
        resume.set()
        await asyncio.gather(*(t for t in (first, second) if t), return_exceptions=True)
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
            cursor.execute('DELETE FROM signup_request WHERE email_normalized=%s', (body['email'],))
            cursor.execute('DELETE FROM membership WHERE user_id IN (SELECT id FROM "user" WHERE email=%s)', (body['email'],))
            cursor.execute('DELETE FROM "user" WHERE email=%s', (body['email'],))
            cursor.execute('DELETE FROM organization WHERE name=%s', (body['organization_name'],))


@pytest.mark.asyncio
async def test_concurrent_form_correction_cannot_change_credentials_after_password_proof(client_unauth, seed_data, signup_mail, monkeypatch):
    import uuid
    import psycopg2
    from sqlalchemy.ext.asyncio import AsyncSession
    from tests.conftest import MIGRATOR_SYNC_URL

    monkeypatch.setattr(settings, 'deployment_mode', 'hosted')
    body = BODY | {'email': f'proof-{uuid.uuid4().hex}@example.test', 'organization_name': f'Password proof {uuid.uuid4()}'}
    assert (await client_unauth.post('/api/signup', json=body)).status_code == 202
    token = confirmation_token(signup_mail)
    verified, resume, contender = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = AsyncSession.scalar

    async def pause_after_password_proof(session, statement, params=None, **kwargs):
        if asyncio.current_task().get_name() == 'confirm-owner' and 'SELECT public.provision_organization' in str(statement):
            verified.set()
            await resume.wait()
        if asyncio.current_task().get_name() == 'correct-form' and 'INSERT INTO public.signup_request' in str(statement):
            contender.set()
        return await original(session, statement, params, **kwargs)

    monkeypatch.setattr(AsyncSession, 'scalar', pause_after_password_proof)
    confirmation = asyncio.create_task(client_unauth.post('/api/signup/confirm', json={'token': token, 'password': body['password']}), name='confirm-owner')
    correction = None
    try:
        await asyncio.wait_for(verified.wait(), 5)
        correction = asyncio.create_task(client_unauth.post('/api/signup', json=body | {'password': 'Substituted-password-123'}), name='correct-form')
        await asyncio.wait_for(contender.wait(), 5)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(correction), 0.1)
        resume.set()
        confirmed, corrected = await asyncio.wait_for(asyncio.gather(confirmation, correction), 10)
        assert confirmed.status_code == 201 and corrected.status_code == 202
        assert (await client_unauth.post('/api/auth/login', json={'email': body['email'], 'password': body['password']})).status_code == 200
        assert (await client_unauth.post('/api/auth/login', json={'email': body['email'], 'password': 'Substituted-password-123'})).status_code == 401
    finally:
        resume.set()
        await asyncio.gather(*(task for task in (confirmation, correction) if task), return_exceptions=True)
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
            cursor.execute('DELETE FROM signup_request WHERE email_normalized=%s', (body['email'],))
            cursor.execute('DELETE FROM membership WHERE user_id IN (SELECT id FROM "user" WHERE email=%s)', (body['email'],))
            cursor.execute('DELETE FROM "user" WHERE email=%s', (body['email'],))
            cursor.execute('DELETE FROM organization WHERE name=%s', (body['organization_name'],))


@pytest.mark.asyncio
async def test_signup_cannot_join_an_existing_organization_by_name(signup):
    client, connection, mail = signup
    await connection.execute(text('RESET ROLE'))
    await connection.execute(text("INSERT INTO organization(name) VALUES (:name)"), {'name': BODY['organization_name']})
    await connection.execute(text('SET LOCAL ROLE app_rw'))
    await client.post('/api/signup', json=BODY)
    response = await client.post('/api/signup/confirm', json={'token': confirmation_token(mail), 'password': BODY['password']})
    assert response.status_code == 201
    assert await connection.scalar(text('SELECT count(*) FROM organization')) == 2
    await connection.execute(text('RESET ROLE'))
    assert await connection.scalar(text('SELECT count(*) FROM "user"')) == 1


@pytest.mark.asyncio
async def test_two_signup_owners_can_choose_the_same_organization_display_name(signup):
    client, connection, mail = signup
    organizations = []
    for email in ('first@example.test', 'second@example.test'):
        assert (await client.post('/api/signup', json=BODY | {'email': email})).status_code == 202
        response = await client.post('/api/signup/confirm', json={'token': confirmation_token(mail), 'password': BODY['password']})
        assert response.status_code == 201, response.text
        organizations.append(response.json()['organization_id'])
        await client.post('/api/auth/logout')
    assert len(set(organizations)) == 2
    assert await connection.scalar(text('SELECT count(*) FROM organization WHERE name=:name'), {'name': BODY['organization_name']}) == 2


@pytest.mark.asyncio
async def test_five_attacker_resends_do_not_block_the_owners_confirmation(signup):
    client, connection, mail = signup
    await client.post('/api/signup', json=BODY)
    token = confirmation_token(mail)
    for _ in range(5):
        assert (await client.post('/api/signup/resend', json={'email': BODY['email']})).json() == MESSAGE
    assert len(mail.messages) == 1
    response = await client.post('/api/signup/confirm', json={'token': token, 'password': BODY['password']})
    assert response.status_code == 201, response.text
    assert await connection.scalar(text("SELECT count(*) FROM auth_throttle WHERE key_kind='email' AND failures>0")) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['/api/signup', '/api/signup/resend'])
async def test_signup_and_resend_flood_limits_use_only_their_own_ip_scope(signup, path):
    from app.core.deployment_policy import get_deployment_policy
    from app.core.rate_limit import key_hash
    client, connection, mail = signup
    for index in range(get_deployment_policy().rate_limit_rules.ip_failures + 1):
        body = BODY | {'email': f'flood-{index}@example.test'} if path == '/api/signup' else {'email': BODY['email']}
        assert (await client.post(path, json=body)).json() == MESSAGE
    scope = 'signup' if path == '/api/signup' else 'signup_resend'
    rows = (await connection.execute(text('SELECT key_kind,key_hash,locked_until FROM auth_throttle'))).all()
    assert len(rows) == 1
    assert rows[0].key_kind == 'ip' and rows[0].key_hash == key_hash(scope, 'ip', '127.0.0.1')
    assert rows[0].locked_until is not None
    if path == '/api/signup':
        assert len(mail.messages) == get_deployment_policy().rate_limit_rules.ip_failures


@pytest.mark.asyncio
async def test_only_failed_confirmations_count_toward_the_confirm_scope(signup):
    from app.core.deployment_policy import get_deployment_policy
    from app.core.rate_limit import key_hash
    client, connection, mail = signup
    await client.post('/api/signup', json=BODY)
    token = confirmation_token(mail)
    for _ in range(get_deployment_policy().rate_limit_rules.email_failures):
        response = await client.post('/api/signup/confirm', json={'token': token, 'password': 'wrong'})
        assert (response.status_code, response.json()) == (403, REFUSED)
    response = await client.post('/api/signup/confirm', json={'token': token, 'password': BODY['password']})
    assert (response.status_code, response.json()) == (403, REFUSED)
    assert await connection.scalar(text("SELECT locked_until IS NOT NULL FROM auth_throttle WHERE key_kind='email' AND key_hash=:key"),
                                   {'key': key_hash('signup_confirm', 'email', BODY['email'])})


@pytest.mark.asyncio
async def test_confirmed_address_can_signup_again_after_its_organization_is_removed(signup):
    client, connection, mail = signup
    await client.post('/api/signup', json=BODY)
    response = await client.post('/api/signup/confirm', json={'token': confirmation_token(mail), 'password': BODY['password']})
    assert response.status_code == 201
    await client.post('/api/auth/logout')
    await connection.execute(text('RESET ROLE'))
    await connection.execute(text('DELETE FROM membership'))
    await connection.execute(text('DELETE FROM "user"'))
    await connection.execute(text('DELETE FROM organization'))
    await connection.execute(text('SET LOCAL ROLE app_rw'))
    await client.post('/api/signup', json=BODY)
    assert len(mail.messages) == 2
    assert (await client.post('/api/signup/confirm', json={'token': confirmation_token(mail), 'password': BODY['password']})).status_code == 201


@pytest.mark.asyncio
async def test_expired_pending_cleanup_is_batched_and_runs_after_the_request_transaction(signup, monkeypatch):
    import itertools
    from app.api import signup as signup_api
    from app.core import rate_limit
    client, connection, mail = signup
    await connection.execute(text("""INSERT INTO signup_request
        (email_normalized,organization_name,admin_name,password_hash,token_hash,expires_at)
        SELECT 'stale-'||n||'@example.test','Private organization','Private name','private hash',
        sha256(n::text::bytea),clock_timestamp()-interval '8 days' FROM generate_series(1,105) n"""))
    await connection.execute(text("""INSERT INTO signup_request
        (email_normalized,organization_name,admin_name,password_hash,token_hash,expires_at)
        VALUES ('recent@example.test','Recent','Recent','hash',sha256('recent'::bytea),clock_timestamp()-interval '6 days')"""))
    transactions = []
    from sqlalchemy.ext.asyncio import AsyncSession
    original_begin = AsyncSession.begin
    def track_begin(session):
        transactions.append(session)
        assert all(not previous.in_transaction() for previous in transactions[:-1])
        return original_begin(session)
    monkeypatch.setattr(AsyncSession, 'begin', track_begin)
    monkeypatch.setattr(rate_limit, 'cleanup_due', lambda: False)
    monkeypatch.setattr(signup_api, '_signup_attempts', itertools.count(99), raising=False)
    response = await client.post('/api/signup', json=BODY)
    assert response.status_code == 202, response.text
    assert await connection.scalar(text("SELECT count(*) FROM signup_request WHERE email_normalized LIKE 'stale-%'")) == 105
    response = await client.post('/api/signup', json=BODY)
    assert response.status_code == 202, response.text
    assert await connection.scalar(text("SELECT count(*) FROM signup_request WHERE email_normalized LIKE 'stale-%'")) == 5
    assert await connection.scalar(text("SELECT count(*) FROM signup_request WHERE email_normalized='recent@example.test'")) == 1
    assert len(transactions) == 5
    assert transactions[0] is transactions[1] and transactions[2] is transactions[3]
    assert transactions[3] is not transactions[4]


@pytest.mark.asyncio
async def test_mail_submission_failure_preserves_pending_state_and_generic_response(signup):
    client, connection, mail = signup
    mail.error = TimeoutError('private SMTP diagnostics')
    response = await client.post('/api/signup', json=BODY)
    assert (response.status_code, response.json()) == (202, MESSAGE)
    assert await connection.scalar(text('SELECT count(*) FROM signup_request')) == 1
    assert not mail.messages
    mail.error = None
    await connection.execute(text("UPDATE signup_request SET created_at=created_at-interval '11 minutes'"))
    await client.post('/api/signup/resend', json={'email': BODY['email']})
    assert len(mail.messages) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [
    {'organization_name': ' '}, {'admin_name': 'a' * 256}, {'admin_name': 'before\x00after'},
    {'email': 'bad-address'}, {'email': 'x\r\nBcc:victim@example.test'},
    {'password': 'short'}, {'password': 'x' * 129},
])
async def test_invalid_signup_input_never_echoes_the_password_or_creates_state(signup, changes):
    client, connection, mail = signup
    response = await client.post('/api/signup', json=BODY | changes)
    assert response.status_code == 422
    assert BODY['password'] not in response.text
    assert await connection.scalar(text('SELECT count(*) FROM signup_request')) == 0
    assert not mail.messages


@pytest.mark.asyncio
async def test_hosted_startup_requires_a_public_origin_and_smtp(monkeypatch):
    from app.main import app
    monkeypatch.setattr(settings, 'deployment_mode', 'hosted')
    monkeypatch.setattr(settings, 'public_url', None)
    async def start():
        async with app.router.lifespan_context(app):
            pass
    with pytest.raises(ValueError, match='HV_PUBLIC_URL'):
        await start()
    monkeypatch.setattr(settings, 'public_url', 'https://signup.example.test')
    monkeypatch.setattr(settings, 'smtp_host', None)
    with pytest.raises(ValueError, match='SMTP'):
        await start()
    monkeypatch.setattr(settings, 'deployment_mode', 'selfhost')
    await start()


@pytest.mark.asyncio
async def test_corrected_form_keeps_the_delivered_link_during_cooldown_and_requires_the_new_password(signup):
    client, connection, mail = signup
    attacker_password = 'Attacker-knows-this-password-123'
    await client.post('/api/signup', json=BODY | {'password': attacker_password, 'admin_name': 'Attacker'})
    attacker_token = confirmation_token(mail)
    assert (await client.post('/api/signup', json=BODY)).json() == MESSAGE
    assert len(mail.messages) == 1  # Changed payload must still respect the cooldown.
    assert await connection.scalar(text('SELECT admin_name FROM signup_request')) == BODY['admin_name']
    assert (await client.post('/api/signup/confirm', json={'token': attacker_token, 'password': attacker_password})).json() == REFUSED
    assert (await client.post('/api/signup/confirm', json={'token': attacker_token, 'password': BODY['password']})).status_code == 201
    await client.post('/api/auth/logout')
    assert (await client.post('/api/auth/login', json={'email': BODY['email'], 'password': attacker_password})).status_code == 401
    assert (await client.post('/api/auth/login', json={'email': BODY['email'], 'password': BODY['password']})).status_code == 200


@pytest.mark.asyncio
async def test_signup_sends_the_http_response_before_smtp_and_hashes_existing_emails(signup, monkeypatch):
    import httpx
    from app.api import signup as signup_api
    from app.main import app
    from app.core.security import hash_password
    client, connection, mail = signup
    response_sent = asyncio.Event()
    submitted = mail.send_message
    hashes = []
    verifications = []
    original_verify = signup_api.verify_password

    def verify_spy(password, hashed):
        verifications.append(hashed)
        return original_verify(password, hashed)

    def hash_spy(password):
        hashes.append(password)
        return hash_password(password)

    def send_after_response(self, *args, **kwargs):
        assert response_sent.is_set(), 'SMTP must not delay the public response'
        return submitted(self, *args, **kwargs)

    async def observed_app(scope, receive, send):
        async def observed_send(message):
            await send(message)
            if message['type'] == 'http.response.body' and not message.get('more_body'):
                response_sent.set()
        await app(scope, receive, observed_send)

    monkeypatch.setattr(signup_api, 'hash_password', hash_spy)
    monkeypatch.setattr(signup_api, 'verify_password', verify_spy)
    monkeypatch.setattr(mail, 'send_message', send_after_response)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=observed_app), base_url='http://test') as observed:
        fresh = await observed.post('/api/signup', json=BODY)
        assert fresh.status_code == 202 and len(mail.messages) == 1
        await observed.post('/api/signup/confirm', json={'token': confirmation_token(mail), 'password': BODY['password']})
        response_sent.clear()
        registered = await observed.post('/api/signup', json=BODY)
    assert registered.status_code == 202 and len(mail.messages) == 1
    assert hashes == [BODY['password'], BODY['password']]
    assert len(verifications) == 3  # Signup, password proof, registered signup.
    assert verifications[0] == verifications[2]


@pytest.mark.asyncio
async def test_confirmation_query_is_redacted_from_default_uvicorn_access_logging():
    from app.main import app
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    from uvicorn.protocols.utils import get_path_with_query_string
    scope = {'type': 'http', 'http_version': '1.1', 'method': 'GET', 'scheme': 'http',
             'path': '/signup/confirm', 'raw_path': b'/signup/confirm',
             'root_path': '', 'query_string': b'token=private-live-token',
             'headers': [(b'host', b'test')], 'client': ('127.0.0.1', 123), 'server': ('test', 80)}
    recorded = []
    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}
    async def send(message):
        if message['type'] == 'http.response.start':
            recorded.append(get_path_with_query_string(scope))
    await ProxyHeadersMiddleware(app, trusted_hosts=['127.0.0.1'])(scope, receive, send)
    assert recorded == ['/signup/confirm']
    assert scope['query_string'] == b''


@pytest.mark.asyncio
async def test_confirmation_requires_the_password_even_with_a_valid_mailbox_token(signup):
    client, connection, mail = signup
    await client.post('/api/signup', json=BODY)
    token = confirmation_token(mail)
    for changes in ({}, {'password': 'Wrong-password-123'}):
        response = await client.post('/api/signup/confirm', json={'token': token} | changes)
        assert (response.status_code, response.json()) == (403, REFUSED)
    assert await connection.scalar(text('SELECT confirmed_at IS NULL FROM signup_request'))
    assert (await client.post('/api/signup/confirm', json={'token': token, 'password': BODY['password']})).status_code == 201


@pytest.mark.asyncio
async def test_overwrite_after_owner_signup_cannot_activate_an_attacker_password_via_resend(signup):
    client, connection, mail = signup
    await client.post('/api/signup', json=BODY)
    owner_link = confirmation_token(mail)
    attacker_password = 'Attacker-overwrites-the-password-123'
    await client.post('/api/signup', json=BODY | {'password': attacker_password})
    await connection.execute(text("UPDATE signup_request SET created_at=created_at-interval '11 minutes'"))
    await client.post('/api/signup/resend', json={'email': BODY['email']})
    resent_link = confirmation_token(mail)
    assert owner_link != resent_link
    response = await client.post('/api/signup/confirm', json={'token': resent_link, 'password': BODY['password']})
    assert (response.status_code, response.json()) == (403, REFUSED)
    assert await connection.scalar(text('SELECT confirmed_at IS NULL FROM signup_request'))
    assert (await client.post('/api/auth/login', json={'email': BODY['email'], 'password': attacker_password})).status_code == 401
