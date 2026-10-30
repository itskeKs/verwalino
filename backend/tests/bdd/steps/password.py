"""Steps for the password-change feature.

The signed-in user is a throw-away identity (fixture ``password_identity``,
shared with tests/test_auth_password.py); the shared seed users are never
changed because a password change revokes sessions durably.
"""

from __future__ import annotations

from pytest_bdd import given, parsers, then, when

# The password the shared fixtures give every test user (tests/conftest.py,
# tests/test_auth_hardening.py::temporary_auth_rows).
CURRENT_PASSWORD = "testpass1234"
NEW_PASSWORD = "NewPassword456!"
WRONG_PASSWORD = "WrongPassword123!"


def _sign_in(world, password):
    world.anonymous.cookies.clear()
    return world.send(
        world.anonymous, "POST", "/api/auth/login", json={"email": world.data["email"], "password": password}
    )


def _change_password(world, current):
    return world.act("POST", "/api/auth/password", json={"old_password": current, "new_password": NEW_PASSWORD})


@given("a user who is signed in")
def signed_in_user(world, password_identity):
    client, email = password_identity
    world.actor = client
    world.data["email"] = email


@when("the user changes the password, entering the current password correctly")
def change_with_current_password(world):
    _change_password(world, CURRENT_PASSWORD)


@when("the user changes the password, entering a wrong current password")
def change_with_wrong_password(world):
    _change_password(world, WRONG_PASSWORD)


@then("the password change is accepted")
def password_change_accepted(world):
    assert world.response is not None and world.response.status_code == 204, world.describe_response()


@then("the password change is refused")
def password_change_refused(world):
    assert world.response is not None and world.response.status_code == 401, (
        "Expected the password change to be refused with 401; " + world.describe_response()
    )


@then(parsers.parse("the user can sign in with the {which} password"))
def can_sign_in(world, which):
    answer = _sign_in(world, _password_named(which))
    assert answer.status_code == 200, f"Expected sign-in with the {which} password to work; " + world.describe_response()


@then(parsers.parse("the user can still sign in with the {which} password"))
def can_still_sign_in(world, which):
    can_sign_in(world, which)


@then(parsers.parse("the user can no longer sign in with the {which} password"))
def can_no_longer_sign_in(world, which):
    answer = _sign_in(world, _password_named(which))
    assert answer.status_code == 401, (
        f"Expected sign-in with the {which} password to be refused; " + world.describe_response()
    )


@then(parsers.parse("the user cannot sign in with the {which} password"))
def cannot_sign_in(world, which):
    can_no_longer_sign_in(world, which)


def _password_named(which):
    passwords = {"old": CURRENT_PASSWORD, "current": CURRENT_PASSWORD, "new": NEW_PASSWORD}
    assert which in passwords, f'Unknown password "{which}"; use one of: {", ".join(passwords)}'
    return passwords[which]
