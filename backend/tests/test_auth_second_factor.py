"""Tests for issue #208: TOTP second factor for staff accounts.

Covers the time-step tolerance, replay protection, lockout counting, hashed
recovery codes, the audited admin reset and the hosted-mode enrolment gate.
"""

import uuid

import psycopg2
import pytest
import pytest_asyncio

from app.core.config import settings
from app.core.security import create_session_token
from tests.bdd.steps.second_factor import totp_code
from tests.conftest import MIGRATOR_SYNC_URL, _api_test_client
from tests.test_auth_hardening import temporary_auth_rows

PASSWORD = "testpass1234"


@pytest_asyncio.fixture
async def second_factor_identity():
    rows = [{"email": f"second-factor-{uuid.uuid4()}@example.test", "role": "OWNER_ADMIN"}]
    with temporary_auth_rows(rows):
        identity = rows[0]
        token = create_session_token(identity["user_id"], identity["organization_id"])
        async with _api_test_client(cookies={settings.session_cookie_name: token}) as client:
            yield client, identity


def _db(query, params=()):
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()


async def _enrol(client) -> tuple[str, list[str]]:
    answer = await client.post("/api/auth/second-factor/enrolment")
    assert answer.status_code == 200, answer.text
    secret = answer.json()["secret"]
    confirm = await client.post(
        "/api/auth/second-factor/enrolment/confirm", json={"code": totp_code(secret)}
    )
    assert confirm.status_code == 200, confirm.text
    return secret, confirm.json()["recovery_codes"]


async def _login(client, email):
    return await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})


@pytest.mark.anyio
async def test_enrolment_confirms_with_current_code(second_factor_identity):
    client, identity = second_factor_identity
    answer = await client.post("/api/auth/second-factor/enrolment")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    secret = body["secret"]
    assert body["otpauth_uri"].startswith("otpauth://totp/")
    assert secret in body["otpauth_uri"]

    wrong = "000000" if totp_code(secret) != "000000" else "000001"
    refused = await client.post("/api/auth/second-factor/enrolment/confirm", json={"code": wrong})
    assert refused.status_code == 401, refused.text

    confirm = await client.post(
        "/api/auth/second-factor/enrolment/confirm", json={"code": totp_code(secret)}
    )
    assert confirm.status_code == 200, confirm.text
    codes = confirm.json()["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10

    status = await client.get("/api/auth/second-factor")
    assert status.status_code == 200 and status.json() == {"enrolled": True}

    # An enrolled factor is not silently replaced: the admin reset is the path back.
    again = await client.post("/api/auth/second-factor/enrolment")
    assert again.status_code == 409, again.text
    rows = _db(
        'SELECT secret_encrypted, confirmed FROM public.user_second_factor WHERE user_id = %s',
        (str(identity["user_id"]),),
    )
    assert len(rows) == 1
    assert rows[0][1] is True


@pytest.mark.anyio
async def test_secret_encrypted_and_recovery_codes_hashed(second_factor_identity):
    client, identity = second_factor_identity
    secret, codes = await _enrol(client)

    rows = _db(
        'SELECT secret_encrypted FROM public.user_second_factor WHERE user_id = %s',
        (str(identity["user_id"]),),
    )
    assert len(rows) == 1
    stored = rows[0][0]
    assert stored != secret, "The TOTP secret must not be stored in plaintext"
    assert stored.startswith("gAAAA"), "The TOTP secret must be stored with the Fernet secret handling"

    hashes = _db(
        'SELECT code_hash, used_at FROM public.user_recovery_code WHERE user_id = %s ORDER BY position',
        (str(identity["user_id"]),),
    )
    assert len(hashes) == 10
    assert all(used_at is None for _, used_at in hashes)
    for code, (stored_hash, _) in zip(codes, hashes):
        assert stored_hash != code, "Recovery codes must not be stored in plaintext"
    assert len({stored_hash for stored_hash, _ in hashes}) == 10


@pytest.mark.anyio
async def test_totp_tolerance_one_step_each_way_and_replay():
    from app.services.second_factor_service import totp_at, verify_totp

    secret = "JBSWY3DPEHPK3PXP"
    now = 1_700_000_000
    previous = totp_at(secret, now - 30)
    current = totp_at(secret, now)
    following = totp_at(secret, now + 30)
    beyond = totp_at(secret, now + 60)

    ok, step = verify_totp(secret, previous, now, last_step=None)
    assert ok and step == (now - 30) // 30
    ok, step = verify_totp(secret, following, now, last_step=None)
    assert ok and step == (now + 30) // 30
    ok, _ = verify_totp(secret, beyond, now, last_step=None)
    assert not ok

    ok, step = verify_totp(secret, current, now, last_step=None)
    assert ok
    replayed, _ = verify_totp(secret, current, now + 5, last_step=step)
    assert not replayed, "A code of an already used time step must be refused"


@pytest.mark.anyio
async def test_wrong_code_counts_toward_the_password_lockout(second_factor_identity):
    client, identity = second_factor_identity
    secret, _ = await _enrol(client)
    login = await _login(client, identity["email"])
    assert login.status_code == 200 and login.json()["second_factor_required"] is True
    pending = login.json()["pending_token"]

    wrong = "000000" if totp_code(secret) != "000000" else "000001"
    for _ in range(10):
        answer = await client.post(
            "/api/auth/login/second-factor", json={"pending_token": pending, "code": wrong}
        )
        assert answer.status_code == 401, answer.text
        assert answer.json()["detail"] == "Invalid credentials"

    locked = await client.post(
        "/api/auth/login/second-factor", json={"pending_token": pending, "code": totp_code(secret)}
    )
    assert locked.status_code == 401, "Ten wrong codes lock the address as ten wrong passwords do"

    locked_login = await _login(client, identity["email"])
    assert locked_login.status_code == 401


@pytest.mark.anyio
async def test_recovery_code_works_once_and_reports_remaining(second_factor_identity):
    client, identity = second_factor_identity
    _, codes = await _enrol(client)

    login = await _login(client, identity["email"])
    answer = await client.post(
        "/api/auth/login/second-factor",
        json={"pending_token": login.json()["pending_token"], "recovery_code": codes[0]},
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["recovery_codes_remaining"] == 9

    login = await _login(client, identity["email"])
    replay = await client.post(
        "/api/auth/login/second-factor",
        json={"pending_token": login.json()["pending_token"], "recovery_code": codes[0]},
    )
    assert replay.status_code == 401, replay.text

    login = await _login(client, identity["email"])
    second = await client.post(
        "/api/auth/login/second-factor",
        json={"pending_token": login.json()["pending_token"], "recovery_code": codes[1]},
    )
    assert second.status_code == 200, second.text
    assert second.json()["recovery_codes_remaining"] == 8


@pytest.mark.anyio
async def test_second_factor_step_refuses_a_tampered_pending_token(second_factor_identity):
    client, identity = second_factor_identity
    await _enrol(client)
    login = await _login(client, identity["email"])
    assert login.json()["pending_token"]
    answer = await client.post(
        "/api/auth/login/second-factor",
        json={"pending_token": login.json()["pending_token"] + "tampered", "code": "123456"},
    )
    assert answer.status_code == 401, answer.text


@pytest.mark.anyio
async def test_unenrolled_selfhost_login_stays_unrestricted(second_factor_identity):
    client, identity = second_factor_identity
    login = await _login(client, identity["email"])
    assert login.status_code == 200, login.text
    assert login.json()["second_factor_required"] is False
    assert login.json()["second_factor_enrolment_required"] is False
    preferences = await client.get("/api/auth/preferences")
    assert preferences.status_code == 200, preferences.text


@pytest.mark.anyio
async def test_admin_reset_is_audited_and_clears_the_factor(second_factor_identity):
    client, identity = second_factor_identity
    await _enrol(client)

    reset = await client.post(f"/api/users/{identity['user_id']}/reset-second-factor")
    assert reset.status_code == 204, reset.text

    rows = _db(
        'SELECT action FROM public.audit_event WHERE entity_id = %s AND action = %s',
        (str(identity["user_id"]), "RESET_SECOND_FACTOR"),
    )
    assert len(rows) == 1

    status = await client.get("/api/auth/second-factor")
    assert status.json() == {"enrolled": False}
    remaining = _db(
        'SELECT count(*) FROM public.user_recovery_code WHERE user_id = %s',
        (str(identity["user_id"]),),
    )
    assert remaining[0][0] == 0

    login = await _login(client, identity["email"])
    assert login.status_code == 200 and login.json()["second_factor_required"] is False


@pytest.mark.anyio
async def test_selfhost_admin_reset_keeps_sessions_valid(second_factor_identity):
    client, identity = second_factor_identity
    await _enrol(client)
    reset = await client.post(f"/api/users/{identity['user_id']}/reset-second-factor")
    assert reset.status_code == 204, reset.text
    preferences = await client.get("/api/auth/preferences")
    assert preferences.status_code == 200, preferences.text


@pytest.mark.anyio
async def test_hosted_login_without_factor_is_restricted_until_enrolment(
    second_factor_identity, monkeypatch
):
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    client, identity = second_factor_identity

    login = await _login(client, identity["email"])
    assert login.status_code == 200, login.text
    assert login.json()["second_factor_enrolment_required"] is True

    blocked = await client.get("/api/auth/preferences")
    assert blocked.status_code == 403, blocked.text
    assert blocked.json()["detail"]["code"] == "second_factor_enrolment_required"

    answer = await client.post("/api/auth/second-factor/enrolment")
    assert answer.status_code == 200, answer.text
    confirm = await client.post(
        "/api/auth/second-factor/enrolment/confirm",
        json={"code": totp_code(answer.json()["secret"])},
    )
    assert confirm.status_code == 200, confirm.text

    allowed = await client.get("/api/auth/preferences")
    assert allowed.status_code == 200, allowed.text


@pytest.mark.anyio
async def test_hosted_admin_reset_revokes_the_target_sessions(
    second_factor_identity, monkeypatch
):
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    client, identity = second_factor_identity
    await _enrol(client)

    reset = await client.post(f"/api/users/{identity['user_id']}/reset-second-factor")
    assert reset.status_code == 204, reset.text

    revoked = await client.get("/api/auth/session")
    assert revoked.status_code == 401, revoked.text

    login = await _login(client, identity["email"])
    assert login.status_code == 200, login.text
    assert login.json()["second_factor_enrolment_required"] is True
