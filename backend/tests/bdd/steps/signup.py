"""Hosted signup through HTTP and real SMTP, read back from Mailpit."""
import re
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from pytest_bdd import given, then, when
from app.core.config import settings
from tests.signup_support import BODY, MESSAGE


@given('a hosted signup form with a test mailbox')
def hosted_signup(world, fresh_setup, mailpit, monkeypatch):
    world.anonymous = fresh_setup[0]
    world.data['mailpit'] = mailpit
    monkeypatch.setattr(settings, 'public_host', 'signup.example.test')


@when('the administrator registers a new organization')
def register(world):
    answer = world.send(world.anonymous, 'POST', '/api/signup', json=BODY)
    assert (answer.status_code, answer.json()) == (202, MESSAGE)


def emailed_token(world, recipient):
    message = world.data['mailpit'].wait_for_message(recipient)
    assert [address['Address'] for address in message['To']] == [recipient]
    assert message['From']['Address'] == settings.smtp_from
    assert message['Subject'] == 'Verwalino: E-Mail-Adresse bestätigen'
    assert '24 Stunden' in message['Text']
    match = re.search(r'https://\S+', message['Text'])
    assert match, 'The confirmation email must contain an HTTPS link'
    link = urlsplit(match[0])
    assert (link.scheme, link.netloc, link.path) == ('https', 'signup.example.test', '/signup/confirm')
    query = parse_qs(link.query)
    assert list(query) == ['token']
    assert len(query['token']) == 1
    token = query['token'][0]
    assert re.fullmatch(r'[0-9a-f]{64}', token), 'The emailed link must contain the confirmation token'
    return token


@then('the test mailbox receives a confirmation email with the correct recipient, sender, subject and token link')
def email_sent(world):
    world.data['signup_token'] = emailed_token(world, BODY['email'].strip().lower())


@then('the unconfirmed administrator cannot sign in')
def cannot_sign_in(world):
    answer = world.send(world.anonymous, 'POST', '/api/auth/login', json={'email': BODY['email'], 'password': BODY['password']})
    assert (answer.status_code, answer.json()) == (401, {'detail': 'Invalid credentials'})


@when('the administrator confirms the emailed link with the chosen password')
def confirm(world):
    world.send(world.anonymous, 'POST', '/api/signup/confirm', json={'token': world.data['signup_token'], 'password': BODY['password']})


@then('the new organization administrator is signed in')
def signed_in(world):
    assert world.response.status_code == 201, world.describe_response()
    assert world.look(world.anonymous, '/api/auth/session').json()['role'] == 'OWNER_ADMIN'


@then('the new administrator can sign in again')
def sign_in_again(world):
    world.send(world.anonymous, 'POST', '/api/auth/logout')
    assert world.send(world.anonymous, 'POST', '/api/auth/login', json={'email': BODY['email'], 'password': BODY['password']}).status_code == 200


@pytest.fixture
def signup_isolation(world, mailpit, monkeypatch):
    body = BODY | {'email': f'isolation-{uuid.uuid4().hex}@example.test', 'organization_name': 'Verwaltung Beta'}
    world.data['signup_isolation_body'] = body
    world.data['mailpit'] = mailpit
    monkeypatch.setattr(settings, 'public_host', 'signup.example.test')
    yield
    import psycopg2
    from tests.conftest import MIGRATOR_SYNC_URL
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute('DELETE FROM signup_request WHERE email_normalized=%s', (body['email'],))
    if created := world.data.get('signup_created_organization'):
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
            cursor.execute('DELETE FROM legal_document_assent WHERE organization_id=%s', (created,))
            cursor.execute('DELETE FROM membership WHERE organization_id=%s', (created,))
            cursor.execute('DELETE FROM "user" WHERE organization_id=%s', (created,))
            cursor.execute('DELETE FROM organization WHERE id=%s', (created,))


@when('a visitor registers using the name of another organization')
def register_existing_name(world, signup_isolation):
    world.data['original_signup_session'] = world.look(world.actor, '/api/auth/session').json()
    assert world.send(world.anonymous, 'POST', '/api/signup', json=world.data['signup_isolation_body']).status_code == 202


@when('the visitor follows the emailed confirmation link')
def confirm_existing_name(world):
    token = emailed_token(world, world.data['signup_isolation_body']['email'])
    answer = world.send(world.anonymous, 'POST', '/api/signup/confirm', json={'token': token, 'password': BODY['password']})
    if answer.status_code == 201:
        world.data['signup_created_organization'] = answer.json()['organization_id']


@then('the registration creates a separate organization with the same name')
def creates_separate_organization(world):
    assert world.response.status_code == 201, world.describe_response()
    session = world.look(world.anonymous, '/api/auth/session').json()
    existing = world.look(world.administrator_of('Verwaltung Beta'), '/api/auth/session').json()
    assert session['organization_id'] != existing['organization_id']
    assert world.look(world.anonymous, '/api/organizations/me').json()['name'] == 'Verwaltung Beta'


@then('the existing administrator stays in the original organization')
def original_session_preserved(world):
    assert world.look(world.actor, '/api/auth/session').json() == world.data['original_signup_session']
    assert world.look(world.actor, '/api/organizations/me').json()['name'] == 'Verwaltung Alpha'


ASSENT_DOCUMENTS = ("terms_version", "av_version")


@when('the visitor submits the sign-up without assent to both documents')
def register_without_assent(world):
    body = {key: value for key, value in BODY.items() if key not in ASSENT_DOCUMENTS}
    body['email'] = f'no-assent-{uuid.uuid4().hex}@example.test'
    world.data['no_assent_email'] = body['email'].strip().lower()
    world.data['no_assent_response'] = world.send(world.anonymous, 'POST', '/api/signup', json=body)


@then('the sign-up is refused with a structured error and no mail is sent')
def refused_without_assent(world):
    answer = world.data['no_assent_response']
    assert answer.status_code == 422, world.describe_response()
    assert answer.json()['detail']['code'] == 'legal_assent_refused'
    messages = world.data['mailpit'].request('GET', '/api/v1/messages').json()['messages']
    recipients = [address['Address'] for message in messages for address in message['To']]
    assert world.data['no_assent_email'] not in recipients


@when('the administrator registers a new organization with assent to both documents')
def register_with_assent(world):
    answer = world.send(world.anonymous, 'POST', '/api/signup', json=BODY)
    assert (answer.status_code, answer.json()) == (202, MESSAGE)


@then('both assent versions are recorded for the organization and the administrator')
def assents_recorded(world):
    # The scenario transaction holds every tenant table for the app; records
    # are verified through the API here and against the database in
    # tests/test_signup.py.
    meta = world.look(world.anonymous, '/api/meta').json()
    expected = {document: meta['legal_documents'][document]['version'] for document in ('terms', 'av_vertrag')}
    answer = world.look(world.anonymous, '/api/organizations/legal-assents')
    assert answer.status_code == 200, world.describe_response()
    recorded = {entry['document']: entry['version'] for entry in answer.json()}
    assert recorded == {'TERMS': expected['terms'], 'AV': expected['av_vertrag']}, answer.json()


@then('the settings show both assents with version, time and administrator')
def settings_show_assents(world):
    answer = world.look(world.anonymous, '/api/organizations/legal-assents')
    assert answer.status_code == 200, world.describe_response()
    entries = {entry['document']: entry for entry in answer.json()}
    assert set(entries) == {'TERMS', 'AV'}
    for entry in entries.values():
        assert entry['version']
        assert entry['assented_at']
        assert entry['admin_name'] == BODY['admin_name']
