"""Hosted signup through HTTP with the existing captured SMTP transport."""
import uuid
import pytest
from pytest_bdd import given, then, when
from tests.signup_support import BODY, MESSAGE, confirmation_token


@given('a hosted signup form with captured confirmation mail')
def hosted_signup(world, fresh_setup, signup_mail):
    world.anonymous = fresh_setup[0]
    world.data['signup_mail'] = signup_mail


@when('the administrator registers a new organization')
def register(world):
    answer = world.send(world.anonymous, 'POST', '/api/signup', json=BODY)
    assert (answer.status_code, answer.json()) == (202, MESSAGE)


@then('a confirmation email is sent')
def email_sent(world):
    mail = world.data['signup_mail']
    assert len(mail.messages) == 1
    world.data['signup_token'] = confirmation_token(mail)


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
def signup_isolation(world, signup_mail):
    body = BODY | {'email': f'isolation-{uuid.uuid4().hex}@example.test', 'organization_name': 'Verwaltung Beta'}
    world.data['signup_isolation_body'] = body
    world.data['signup_mail'] = signup_mail
    yield
    import psycopg2
    from tests.conftest import MIGRATOR_SYNC_URL
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute('DELETE FROM signup_request WHERE email_normalized=%s', (body['email'],))
    if created := world.data.get('signup_created_organization'):
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
            cursor.execute('DELETE FROM membership WHERE organization_id=%s', (created,))
            cursor.execute('DELETE FROM "user" WHERE organization_id=%s', (created,))
            cursor.execute('DELETE FROM organization WHERE id=%s', (created,))


@when('a visitor registers using the name of another organization')
def register_existing_name(world, signup_isolation):
    world.data['original_signup_session'] = world.look(world.actor, '/api/auth/session').json()
    assert world.send(world.anonymous, 'POST', '/api/signup', json=world.data['signup_isolation_body']).status_code == 202


@when('the visitor follows the emailed confirmation link')
def confirm_existing_name(world):
    answer = world.send(world.anonymous, 'POST', '/api/signup/confirm', json={'token': confirmation_token(world.data['signup_mail']), 'password': BODY['password']})
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
