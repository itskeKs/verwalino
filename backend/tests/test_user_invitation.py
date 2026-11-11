"""User invitations: single-use issuance, acceptance, refusal and revocation."""
import re
from email import policy
from email.parser import BytesParser
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

INVITED_PASSWORD = 'InviteePassword789!'
REFUSED = {'detail': 'Diese Einladung ist nicht mehr gültig. Bitte wenden Sie sich an Ihre Verwaltung.'}
SUBJECT = 'Verwalino: Einladung von Verwaltung Alpha'


def invitation_token(mail):
    message = BytesParser(policy=policy.default).parsebytes(mail.messages[-1][0])
    assert message['Subject'] == SUBJECT
    body = message.get_content()
    assert '72 Stunden' in body
    link = re.search(r'https://signup.example.test/einladung\?token=\S+', body)[0]
    return parse_qs(urlsplit(link).query)['token'][0]


async def invite(client, email, role='CLERK'):
    answer = await client.post('/api/users/invitations', json={'email': email, 'role': role})
    assert answer.status_code == 201, answer.text
    return answer.json()


@pytest.mark.asyncio
async def test_definer_has_fixed_metadata_and_only_runtime_execute(app_rw_session):
    row = (await app_rw_session.execute(text(
        "SELECT pg_get_userbyid(proowner),prosecdef,proconfig FROM pg_proc "
        "WHERE oid='public.accept_user_invitation(text,text)'::regprocedure"))).one()
    assert tuple(row) == ('app_migrator', True, ['search_path=pg_catalog, pg_temp', 'row_security=off'])
    grants = (await app_rw_session.execute(text("""SELECT pg_get_userbyid(a.grantee),a.privilege_type,a.is_grantable
        FROM pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
        WHERE p.oid='public.accept_user_invitation(text,text)'::regprocedure AND a.grantee<>p.proowner"""))).all()
    assert grants == [('app_rw', 'EXECUTE', False)]


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['selfhost', 'hosted'])
async def test_invited_user_sets_password_and_signs_in_with_invited_role(
        client_a_admin, client_unauth, signup_mail, monkeypatch, mode):
    from app.core.config import settings
    monkeypatch.setattr(settings, 'deployment_mode', mode)
    await invite(client_a_admin, 'kollegin@example.test', 'CLERK')
    token = invitation_token(signup_mail)
    answer = await client_unauth.post('/api/auth/invitation/accept',
                                      json={'token': token, 'password': INVITED_PASSWORD})
    assert answer.status_code == 204, answer.text
    assert 'set-cookie' not in answer.headers
    login = await client_unauth.post('/api/auth/login',
                                     json={'email': 'kollegin@example.test', 'password': INVITED_PASSWORD})
    assert login.status_code == 200, login.text
    assert login.json()['role'] == 'CLERK'
    again = await client_unauth.post('/api/auth/invitation/accept',
                                     json={'token': token, 'password': INVITED_PASSWORD})
    assert again.status_code == 403
    assert again.json() == REFUSED


@pytest.mark.asyncio
async def test_expired_invitation_is_refused_in_german(client_a_admin, client_unauth, signup_mail,
                                                       migrator_session):
    invitation = await invite(client_a_admin, 'alt@example.test')
    await migrator_session.execute(text(
        'UPDATE user_invitation SET expires_at=now()-interval \'1 minute\' WHERE id=:id'),
        {'id': invitation['id']})
    answer = await client_unauth.post('/api/auth/invitation/accept',
                                      json={'token': invitation_token(signup_mail),
                                            'password': INVITED_PASSWORD})
    assert answer.status_code == 403
    assert answer.json() == REFUSED


@pytest.mark.asyncio
async def test_revoked_invitation_is_refused_and_resend_rotates_the_token(
        client_a_admin, client_unauth, signup_mail):
    invitation = await invite(client_a_admin, 'wechsel@example.test')
    first_token = invitation_token(signup_mail)
    answer = await client_a_admin.post(f"/api/users/invitations/{invitation['id']}/resend")
    assert answer.status_code == 200, answer.text
    second_token = invitation_token(signup_mail)
    assert second_token != first_token
    old = await client_unauth.post('/api/auth/invitation/accept',
                                   json={'token': first_token, 'password': INVITED_PASSWORD})
    assert (old.status_code, old.json()) == (403, REFUSED)
    fresh = await client_unauth.post('/api/auth/invitation/accept',
                                     json={'token': second_token, 'password': INVITED_PASSWORD})
    assert fresh.status_code == 204, fresh.text
    revoked = await client_a_admin.delete(f"/api/users/invitations/{invitation['id']}")
    assert revoked.status_code == 204, revoked.text
    answer = await client_unauth.post('/api/auth/invitation/accept',
                                      json={'token': second_token, 'password': INVITED_PASSWORD})
    assert (answer.status_code, answer.json()) == (403, REFUSED)
    missing = await client_a_admin.delete(f"/api/users/invitations/{invitation['id']}")
    assert missing.status_code == 404
    unknown = await client_a_admin.post(f"/api/users/invitations/{invitation['id']}/resend")
    assert unknown.status_code == 404


@pytest.mark.asyncio
async def test_create_refuses_existing_addresses_and_pending_duplicates(client_a_admin, signup_mail):
    taken = await client_a_admin.post('/api/users/invitations',
                                      json={'email': 'ADMIN@alpha.de', 'role': 'CLERK'})
    assert taken.status_code == 409
    assert taken.json() == {'detail': 'Email already in use'}
    await invite(client_a_admin, 'doppelt@example.test')
    duplicate = await client_a_admin.post('/api/users/invitations',
                                          json={'email': 'doppelt@example.test', 'role': 'CLERK'})
    assert duplicate.status_code == 409
    assert duplicate.json() == {'detail': 'Invitation already pending'}


@pytest.mark.asyncio
async def test_accept_refuses_when_the_address_was_created_as_a_user(
        client_a_admin, client_unauth, signup_mail):
    await invite(client_a_admin, 'vergeben@example.test')
    token = invitation_token(signup_mail)
    created = await client_a_admin.post('/api/users', json={
        'email': 'vergeben@example.test', 'password': 'StartPasswort123!', 'role': 'CLERK'})
    assert created.status_code == 201, created.text
    answer = await client_unauth.post('/api/auth/invitation/accept',
                                      json={'token': token, 'password': INVITED_PASSWORD})
    assert (answer.status_code, answer.json()) == (403, REFUSED)


@pytest.mark.asyncio
async def test_clerk_cannot_invite_and_sees_no_pending_invitations(client_a_clerk):
    answer = await client_a_clerk.post('/api/users/invitations',
                                       json={'email': 'heheim@example.test', 'role': 'CLERK'})
    assert answer.status_code == 403
    listing = await client_a_clerk.get('/api/users/invitations')
    assert listing.status_code == 200
    assert listing.json() == []


@pytest.mark.asyncio
async def test_pending_list_is_scoped_to_the_organization(client_a_admin, client_b_admin, signup_mail):
    await invite(client_a_admin, 'nur-alpha@example.test')
    ours = await client_a_admin.get('/api/users/invitations')
    assert ours.status_code == 200
    assert [item['email'] for item in ours.json()] == ['nur-alpha@example.test']
    assert ours.json()[0]['role'] == 'CLERK'
    foreign = await client_b_admin.get('/api/users/invitations')
    assert foreign.status_code == 200
    assert foreign.json() == []


@pytest.mark.asyncio
async def test_invitation_requires_mail_configuration(client_a_admin, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, 'smtp_host', None)
    answer = await client_a_admin.post('/api/users/invitations',
                                       json={'email': 'ohne-mail@example.test', 'role': 'CLERK'})
    assert answer.status_code == 409


@pytest.mark.asyncio
async def test_recipient_window_refuses_the_fourth_send(client_a_admin, signup_mail):
    invitation = await invite(client_a_admin, 'häufig@example.test')
    for _ in range(2):
        resent = await client_a_admin.post(f"/api/users/invitations/{invitation['id']}/resend")
        assert resent.status_code == 200, resent.text
    refused = await client_a_admin.post(f"/api/users/invitations/{invitation['id']}/resend")
    assert refused.status_code == 429


@pytest.mark.asyncio
async def test_invite_revoke_and_resend_are_audit_logged(client_a_admin, signup_mail,
                                                         migrator_session, seed_data):
    invitation = await invite(client_a_admin, 'audit@example.test')
    await client_a_admin.post(f"/api/users/invitations/{invitation['id']}/resend")
    await client_a_admin.delete(f"/api/users/invitations/{invitation['id']}")
    rows = (await migrator_session.execute(text(
        "SELECT action FROM audit_event WHERE entity_type='user_invitation' "
        "AND organization_id=:org ORDER BY created_at"), {'org': seed_data['org_a_id']})).all()
    assert [row.action for row in rows] == ['INVITE', 'RESEND', 'REVOKE']
    foreign = (await migrator_session.execute(text(
        "SELECT count(*) FROM audit_event WHERE entity_type='user_invitation' "
        "AND organization_id=:org"), {'org': seed_data['org_b_id']})).scalar()
    assert foreign == 0


@pytest.mark.asyncio
async def test_tenant_context_refuses_the_accept_definer_without_changing_the_invitation(
        client_a_admin, client_unauth, signup_mail, app_rw_session, seed_data):
    invitation = await invite(client_a_admin, 'kontext@example.test')
    token = invitation_token(signup_mail)
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id', :org, false)"),
                                 {'org': str(seed_data['org_a_id'])})
    with pytest.raises(DBAPIError, match='Invitation acceptance refused'):
        await app_rw_session.execute(text(
            "SELECT * FROM public.accept_user_invitation(:token, :hash)"),
            {'token': token, 'hash': '$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$hash'})
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id', '', false)"))
    answer = await client_unauth.post('/api/auth/invitation/accept',
                                      json={'token': token, 'password': INVITED_PASSWORD})
    assert answer.status_code == 204, answer.text


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [
    {'token': '0' * 64, 'password': INVITED_PASSWORD},
    {'token': '0' * 63, 'password': INVITED_PASSWORD},
    {'token': '0' * 64, 'password': 'short'},
    {'email': 'a@b.de', 'role': 'CLERK', 'website': ''},
])
@pytest.mark.parametrize('path', ['/api/auth/invitation/accept', '/api/users/invitations'])
async def test_malformed_bodies_are_refused_without_echoing_secrets(client_unauth, body, path):
    answer = await client_unauth.post(path, json=body)
    assert answer.status_code in (403, 404, 409, 422)
    assert INVITED_PASSWORD not in answer.text


def test_invitation_migration_roundtrip_is_exact_and_refuses_retained_state(run_migrations):
    from alembic.operations import Operations
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_session_review_access import _chain_snapshot
    scripts = ScriptDirectory('migrations')
    migration = scripts.get_revision('0182').module
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            before = _chain_snapshot(connection)
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                assert connection.scalar(text("SELECT to_regclass('public.user_invitation')")) is None
                migration.upgrade()
                assert _chain_snapshot(connection) == before
                connection.execute(text("INSERT INTO organization(name) VALUES ('Invitation roundtrip') RETURNING id"))
                actor = connection.execute(text('''INSERT INTO "user"(organization_id,email,display_name,password_hash)
                    SELECT id,'invitation-roundtrip@example.test','Invitation','hash' FROM organization WHERE name='Invitation roundtrip'
                    RETURNING id,organization_id''')).one()
                connection.execute(text('''INSERT INTO user_invitation(organization_id,email,role,invited_by,token_hash)
                    VALUES (:org,'invitation-roundtrip@example.test','CLERK',:user,sha256('roundtrip'::bytea))'''),
                    {'user': actor.id, 'org': actor.organization_id})
                with pytest.raises(DBAPIError, match='Cannot discard retained invitation state'):
                    with connection.begin_nested():
                        migration.downgrade()
            transaction.rollback()
    finally:
        engine.dispose()


from tests.password_reset_support import signup_mail as signup_mail  # noqa: E402,F401
