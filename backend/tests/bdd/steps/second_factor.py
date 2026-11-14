"""Steps for the second-factor feature (issue #208).

The throw-away identity of ``tests/test_auth_password.py`` enrols, signs in
with a TOTP code and uses recovery codes; the audit-log assertion reads the
database because the app exposes no audit endpoint.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time

from pytest_bdd import given, then, when
from sqlalchemy import select

from app.models.audit_event import AuditEvent
from tests.bdd.steps.password import CURRENT_PASSWORD, _sign_in

GENERIC_INVALID_MESSAGE = "Invalid credentials"


def totp_code(secret_base32: str, step_offset: int = 0, timestamp: float | None = None) -> str:
    """The RFC 6238 code of the secret at the current (or given) time."""
    counter = int((time.time() if timestamp is None else timestamp) // 30) + step_offset
    digest = hmac.new(base64.b32decode(secret_base32), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{code % 1_000_000:06d}"


async def enrol_second_factor(client) -> tuple[str, list[str]]:
    """Enrol through the API with the current code; returns the secret and the recovery codes.

    A restricted hosted session may reach exactly these two routes, so tests
    that sign up or sign in as a new hosted user use it to leave the gate.
    """
    started = await client.post("/api/auth/second-factor/enrolment")
    assert started.status_code == 200, started.text
    secret = started.json()["secret"]
    confirmed = await client.post(
        "/api/auth/second-factor/enrolment/confirm", json={"code": totp_code(secret)}
    )
    assert confirmed.status_code == 200, confirmed.text
    return secret, confirmed.json()["recovery_codes"]


def finish_sign_in(world, client, answer, secret=None):
    """Finish a hosted sign-in or sign-up the way a real user does (issue #208).

    An account without a confirmed factor is restricted to the enrolment routes,
    so the scenario's user enrols at once; an account with one answers the code
    step with its authenticator. Any other answer is returned unchanged, so the
    steps stay the same in the self-hosted run.
    """
    if answer.status_code not in (200, 201):
        return answer
    body = answer.json()
    if body.get("second_factor_enrolment_required"):
        secret, _ = world.run(enrol_second_factor(client))
        world.data["second_factor_secret"] = secret
        return answer
    if body.get("second_factor_required"):
        from tests.conftest import CONFIRMED_FACTOR_SECRET

        # One step ahead: a code of the step that enrolled the factor is a replay.
        code = totp_code(secret or world.data.get("second_factor_secret", CONFIRMED_FACTOR_SECRET), step_offset=1)
        return world.send(client, "POST", "/api/auth/login/second-factor",
                          json={"pending_token": body["pending_token"], "code": code})
    return answer


def _start_enrolment(world):
    answer = world.act("POST", "/api/auth/second-factor/enrolment")
    assert answer.status_code == 200, world.describe_response()
    world.data["totp_secret"] = answer.json()["secret"]
    return answer


def _confirm_enrolment(world, code):
    return world.act("POST", "/api/auth/second-factor/enrolment/confirm", json={"code": code})


def _enrol(world):
    _start_enrolment(world)
    answer = _confirm_enrolment(world, totp_code(world.data["totp_secret"]))
    assert answer.status_code == 200, world.describe_response()
    world.data["recovery_codes"] = answer.json()["recovery_codes"]


@given("the user has an enrolled second factor")
def enrolled_second_factor(world):
    _enrol(world)


@when("the user starts enrolling a second factor")
def start_enrolment(world):
    _start_enrolment(world)


@when("the user confirms the enrolment with the current code")
def confirm_enrolment(world):
    world.response = _confirm_enrolment(world, totp_code(world.data["totp_secret"]))


@when("the user signs in with the password")
def sign_in_with_password(world):
    world.data["login_response"] = _sign_in(world, CURRENT_PASSWORD)


@when("the user answers the second-factor step with the current code")
def answer_with_current_code(world):
    # The enrolment-confirm code consumed its time step; the authenticator's
    # next code is within the tolerated +/-1 window.
    world.response = world.send(world.anonymous, "POST", "/api/auth/login/second-factor", json={
        "pending_token": world.data["login_response"].json()["pending_token"],
        "code": totp_code(world.data["totp_secret"], step_offset=1),
    })


@when("the user answers the second-factor step with a wrong code")
def answer_with_wrong_code(world):
    # Deterministically wrong: not the current code, whatever the clock says.
    current = totp_code(world.data["totp_secret"])
    wrong = "000000" if current != "000000" else "000001"
    world.response = world.send(world.anonymous, "POST", "/api/auth/login/second-factor", json={
        "pending_token": world.data["login_response"].json()["pending_token"],
        "code": wrong,
    })


@when("the user answers the second-factor step with the first recovery code")
def answer_with_recovery_code(world):
    world.response = world.send(world.anonymous, "POST", "/api/auth/login/second-factor", json={
        "pending_token": world.data["login_response"].json()["pending_token"],
        "recovery_code": world.data["recovery_codes"][0],
    })


@when("an administrator resets the user's second factor")
def admin_resets_second_factor(world):
    session = world.look(world.actor, "/api/auth/session")
    assert session.status_code == 200, world.describe_response()
    world.data["second_factor_user_id"] = session.json()["user_id"]
    world.response = world.act("POST", f"/api/users/{world.data['second_factor_user_id']}/reset-second-factor")


@then("the enrolment answers with ten recovery codes")
def enrolment_answered(world):
    answer = world.response
    assert answer is not None and answer.status_code == 200, world.describe_response()
    codes = answer.json()["recovery_codes"]
    assert len(codes) == 10, f"Expected ten recovery codes, got {len(codes)}"
    assert len(set(codes)) == 10, "Recovery codes must be distinct"


@then("the second factor is enrolled")
def factor_is_enrolled(world):
    answer = world.look(world.actor, "/api/auth/second-factor")
    assert answer.status_code == 200 and answer.json()["enrolled"] is True, world.describe_response()


@then("the login asks for the second-factor code")
def login_asks_for_code(world):
    answer = world.data["login_response"]
    assert answer.status_code == 200, world.describe_response()
    body = answer.json()
    assert body.get("second_factor_required") is True, f"Expected the login to ask for the code; got {body}"
    assert body.get("pending_token"), f"Expected a pending token; got {body}"


@then("the login does not ask for a second-factor code")
def login_asks_for_no_code(world):
    answer = world.data["login_response"]
    assert answer.status_code == 200, world.describe_response()
    body = answer.json()
    assert not body.get("second_factor_required"), f"Expected no second-factor step; got {body}"


@then("the user is signed in")
def user_is_signed_in(world):
    answer = world.response
    assert answer is not None and answer.status_code == 200, world.describe_response()
    assert answer.json().get("user_id"), f"Expected a signed-in answer; got {answer.json()}"
    session = world.look(world.anonymous, "/api/auth/session")
    assert session.status_code == 200, world.describe_response()


@then("the second-factor step is refused with the generic message")
def second_factor_refused(world):
    answer = world.response
    assert answer is not None and answer.status_code == 401, world.describe_response()
    assert answer.json()["detail"] == GENERIC_INVALID_MESSAGE, world.describe_response()


@then("the answer says that 9 recovery codes remain")
def nine_codes_remain(world):
    answer = world.response
    assert answer is not None and answer.json().get("recovery_codes_remaining") == 9, world.describe_response()


@then("the reset is recorded in the audit log")
def reset_is_audited(world, migrator_session):
    result = world.run(migrator_session.execute(select(AuditEvent).where(
        AuditEvent.entity_id == world.data["second_factor_user_id"],
        AuditEvent.action == "RESET_SECOND_FACTOR",
    )))
    rows = result.scalars().all()
    assert len(rows) == 1, f"Expected exactly one RESET_SECOND_FACTOR audit event, got {len(rows)}"
    assert rows[0].actor_user_id is not None, "The audit event must name who reset the factor"
