"""Password recovery through HTTP and captured SMTP."""
from pytest_bdd import given, then, when
from tests.password_reset_support import MESSAGE, NEW_PASSWORD, reset_token


@given('password reset mail is captured')
def captured_reset_mail(world, signup_mail):
    world.data['reset_mail'] = signup_mail


@when('the user requests a password reset link')
def request_reset(world):
    answer = world.send(world.anonymous, 'POST', '/api/auth/password-reset', json={'email': world.data['email']})
    assert (answer.status_code, answer.json()) == (202, MESSAGE)


@then('a password reset email is sent')
def reset_mail_sent(world):
    world.data['reset_token'] = reset_token(world.data['reset_mail'])


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
def submit_captured_reset(world):
    world.data['reset_token'] = reset_token(world.data['reset_mail'])
    submit_reset(world)


@then("the other organization's administrator remains signed in")
def other_reset_session_preserved(world):
    other = world.administrator_of('Verwaltung Beta')
    assert world.look(other, '/api/auth/session').status_code == 200
    assert world.look(other, '/api/organizations/me').json()['name'] == 'Verwaltung Beta'
