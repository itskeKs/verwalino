"""Inviting colleagues by e-mail and accepting through the single-use link."""
import re
from urllib.parse import parse_qs, urlsplit

from pytest_bdd import given, parsers, then, when

from app.core.config import settings
from tests.bdd.steps.password_reset import mailbox_message

INVITEE_PASSWORD = 'InviteePassword789!'


@given('a test mailbox for invitations')
def invitation_mailbox(mailpit, monkeypatch):
    monkeypatch.setattr(settings, 'public_host', 'signup.example.test')


@when(parsers.parse('the administrator invites "{email}" as "{role}"'))
def invite_user(world, email, role):
    world.data['email'] = email
    world.data['invitee_role'] = role
    answer = world.send(world.actor, 'POST', '/api/users/invitations', json={'email': email, 'role': role})
    assert answer.status_code == 201, world.describe_response()


@then('the test mailbox receives an invitation with a single-use link valid for 72 hours')
def invitation_mail_sent(world, mailpit):
    message = mailbox_message(world, mailpit, 'Verwalino: Einladung von Verwaltung Alpha')
    assert '72 Stunden' in message['Text']
    match = re.search(r'https://\S+', message['Text'])
    assert match, 'The invitation email must contain an HTTPS link'
    link = urlsplit(match[0])
    assert (link.scheme, link.netloc, link.path) == ('https', 'signup.example.test', '/einladung')
    query = parse_qs(link.query)
    assert list(query) == ['token']
    token = query['token'][0]
    assert re.fullmatch(r'[0-9a-f]{64}', token), 'The emailed link must contain the invitation token'
    world.data['invitation_token'] = token
    mailpit.clear()


@when('the administrator revokes the invitation for "{email}"')
def revoke_invitation(world, email):
    answer = world.send(world.actor, 'GET', '/api/users/invitations')
    assert answer.status_code == 200, world.describe_response()
    invitation = next(item for item in answer.json() if item['email'] == email)
    answer = world.send(world.actor, 'DELETE', f"/api/users/invitations/{invitation['id']}")
    assert answer.status_code == 204, world.describe_response()


@when('the invitee sets a password through the emailed link')
def accept_invitation(world):
    world.send(world.anonymous, 'POST', '/api/auth/invitation/accept',
               json={'token': world.data['invitation_token'], 'password': INVITEE_PASSWORD})


@then('the invitation is accepted without signing in')
def accepted_invitation(world):
    assert world.response.status_code == 204, world.describe_response()
    assert 'set-cookie' not in world.response.headers
    assert world.look(world.anonymous, '/api/auth/session').status_code == 401


@then('the invitation is refused in German')
def refused_invitation(world):
    assert world.response.status_code == 403, world.describe_response()
    assert 'Einladung' in world.response.json()['detail']


@then('the invitee can sign in with the invited role')
def invitee_signs_in(world):
    answer = world.send(world.anonymous, 'POST', '/api/auth/login',
                        json={'email': world.data['email'], 'password': INVITEE_PASSWORD})
    assert answer.status_code == 200, world.describe_response()
    assert answer.json()['role'] == world.data['invitee_role']
