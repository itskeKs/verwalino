"""Password recovery through HTTP and real SMTP, read back from Mailpit."""
import re
from urllib.parse import parse_qs, urlsplit

from pytest_bdd import given, then, when

from app.core.config import settings
from tests.password_reset_support import MESSAGE, NEW_PASSWORD


@given('a test mailbox for password recovery')
def reset_mailbox(mailpit, monkeypatch):
    monkeypatch.setattr(settings, 'public_host', 'signup.example.test')


@when('the user requests a password reset link')
def request_reset(world):
    answer = world.send(world.anonymous, 'POST', '/api/auth/password-reset', json={'email': world.data['email']})
    assert (answer.status_code, answer.json()) == (202, MESSAGE)


def mailbox_message(world, mailpit, subject):
    recipient = world.data['email']
    message = mailpit.wait_for_message(recipient)
    assert [address['Address'] for address in message['To']] == [recipient]
    assert message['From']['Address'] == settings.smtp_from
    assert message['Subject'] == subject
    return message


@then('the test mailbox receives a password reset email with the correct recipient, sender, subject and token link')
def reset_mail_sent(world, mailpit):
    message = mailbox_message(world, mailpit, 'Verwalino: Passwort zurücksetzen')
    assert '1 Stunde' in message['Text']
    match = re.search(r'https://\S+', message['Text'])
    assert match, 'The password reset email must contain an HTTPS link'
    link = urlsplit(match[0])
    assert (link.scheme, link.netloc, link.path) == ('https', 'signup.example.test', '/passwort-zuruecksetzen')
    query = parse_qs(link.query)
    assert list(query) == ['token']
    assert len(query['token']) == 1
    token = query['token'][0]
    assert re.fullmatch(r'[0-9a-f]{64}', token), 'The emailed link must contain the password reset token'
    world.data['reset_token'] = token
    # The next delivery must be the confirmation, not the already-read reset link.
    mailpit.clear()


@then('the test mailbox receives a password reset confirmation email')
def reset_confirmation_sent(world, mailpit):
    message = mailbox_message(world, mailpit, 'Verwalino: Passwort geändert')
    assert 'Ihr Passwort wurde geändert' in message['Text']
    assert 'Alle bisherigen Sitzungen wurden beendet' in message['Text']


@when('the user submits a new password through the emailed link')
def submit_reset(world):
    world.send(world.anonymous, 'POST', '/api/auth/password-reset/confirm', json={'token': world.data['reset_token'], 'password': NEW_PASSWORD})


@then('the password reset is accepted without signing in')
def accepted_reset(world):
    assert world.response.status_code == 204, world.describe_response()
    assert 'set-cookie' not in world.response.headers
    assert world.look(world.anonymous, '/api/auth/session').status_code == 401


@then('the old session is no longer valid')
def revoked_reset_session(world):
    assert world.look(world.actor, '/api/auth/session').status_code == 401


@when('the user submits the captured recovery link')
def submit_captured_reset(world, mailpit):
    reset_mail_sent(world, mailpit)
    submit_reset(world)


@then("the other organization's administrator remains signed in")
def other_reset_session_preserved(world):
    other = world.administrator_of('Verwaltung Beta')
    assert world.look(other, '/api/auth/session').status_code == 200
    assert world.look(other, '/api/organizations/me').json()['name'] == 'Verwaltung Beta'
