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
from tests.bdd.steps.second_factor import enrol_second_factor as _enrol, totp_code
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
        "/api/auth/login/second-factor", json={"pending_token": pending, "code": _next_step_code(secret)}
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


# --- Review round 1 (PR #656, base 0fd7b10d): red first for every finding. ---

from datetime import datetime, timedelta, timezone  # noqa: E402


def _wrong_code(secret: str) -> str:
    current = totp_code(secret)
    return "000000" if current != "000000" else "000001"


def _next_step_code(secret: str) -> str:
    """A code of the following time step (accepted within the +/-1 window)."""
    return totp_code(secret, step_offset=1)


@pytest.mark.anyio
async def test_password_step_does_not_reset_lockout_progress(second_factor_identity, monkeypatch):
    """Knowing the password must not let an attacker dodge the e-mail lock."""
    from app.core import rate_limit

    client, identity = second_factor_identity
    secret, _ = await _enrol(client)
    clock = {"now": datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(rate_limit, "utcnow", lambda: clock["now"])

    # Two cycles: a correct password followed by wrong codes. Without the fix
    # the password step resets the failure row and the lock never fires.
    login = await _login(client, identity["email"])
    assert login.status_code == 200, login.text
    for _ in range(9):
        answer = await client.post(
            "/api/auth/login/second-factor",
            json={"pending_token": login.json()["pending_token"], "code": _wrong_code(secret)},
        )
        assert answer.status_code == 401, answer.text

    login = await _login(client, identity["email"])
    assert login.status_code == 200, login.text
    refused = await client.post(
        "/api/auth/login/second-factor",
        json={"pending_token": login.json()["pending_token"], "code": _wrong_code(secret)},
    )
    assert refused.status_code == 401, refused.text

    # Ten failures reached: the right code is refused while the lock runs.
    locked = await client.post(
        "/api/auth/login/second-factor",
        json={"pending_token": login.json()["pending_token"], "code": _next_step_code(secret)},
    )
    assert locked.status_code == 401, "The right code is refused while the address is locked"

    clock["now"] += timedelta(minutes=16)
    login = await _login(client, identity["email"])
    reopened = await client.post(
        "/api/auth/login/second-factor",
        json={"pending_token": login.json()["pending_token"], "code": _next_step_code(secret)},
    )
    assert reopened.status_code == 200, reopened.text


@pytest.mark.anyio
async def test_session_reports_the_enrolment_requirement(second_factor_identity, monkeypatch):
    """A restricted hosted session must stay restricted across reloads."""
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    client, identity = second_factor_identity

    login = await _login(client, identity["email"])
    assert login.json()["second_factor_enrolment_required"] is True
    session = await client.get("/api/auth/session")
    assert session.status_code == 200, session.text
    assert session.json()["second_factor_enrolment_required"] is True, (
        "GET /api/auth/session must carry the restriction state"
    )

    answer = await client.post("/api/auth/second-factor/enrolment")
    confirm = await client.post(
        "/api/auth/second-factor/enrolment/confirm",
        json={"code": _next_step_code(answer.json()["secret"])},
    )
    assert confirm.status_code == 200, confirm.text
    session = await client.get("/api/auth/session")
    assert session.json()["second_factor_enrolment_required"] is False, session.text


@pytest.mark.anyio
async def test_hosted_gate_holds_for_any_session_cookie(second_factor_identity, monkeypatch):
    """The hosted gate derives from database state, not from the cookie issuer."""
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    client, identity = second_factor_identity

    # A full (unrestricted) session token issued out of band — the way e.g. a
    # sign-up confirmation issues one — must not open the application either.
    foreign_issued = create_session_token(identity["user_id"], identity["organization_id"])
    async with _api_test_client(cookies={settings.session_cookie_name: foreign_issued}) as other:
        blocked = await other.get("/api/auth/preferences")
        assert blocked.status_code == 403, blocked.text
        assert blocked.json()["detail"]["code"] == "second_factor_enrolment_required"
        enrol = await other.post("/api/auth/second-factor/enrolment")
        assert enrol.status_code == 200, enrol.text


@pytest.mark.anyio
async def test_unreadable_totp_secret_is_refused_not_a_server_error(second_factor_identity, monkeypatch):
    """A rotated/restored-away SESSION_SECRET must give the generic refusal."""
    client, identity = second_factor_identity
    await _enrol(client)
    monkeypatch.setattr(settings, "session_secret", "rotated-secret-material")
    login = await _login(client, identity["email"])
    answer = await client.post(
        "/api/auth/login/second-factor",
        json={"pending_token": login.json()["pending_token"], "code": "123456"},
    )
    assert answer.status_code == 401, answer.text
    assert answer.json()["detail"] == "Invalid credentials"


@pytest.mark.anyio
async def test_confirm_code_cannot_be_replayed_at_the_next_login(second_factor_identity):
    """The confirm code is a used step; the next login needs a fresh one."""
    client, identity = second_factor_identity
    answer = await client.post("/api/auth/second-factor/enrolment")
    secret = answer.json()["secret"]
    confirm_code = totp_code(secret)
    confirm = await client.post(
        "/api/auth/second-factor/enrolment/confirm", json={"code": confirm_code}
    )
    assert confirm.status_code == 200, confirm.text

    login = await _login(client, identity["email"])
    replay = await client.post(
        "/api/auth/login/second-factor",
        json={"pending_token": login.json()["pending_token"], "code": confirm_code},
    )
    assert replay.status_code == 401, "The enrolment-confirm code must not sign in again"

    login = await _login(client, identity["email"])
    fresh = await client.post(
        "/api/auth/login/second-factor",
        json={"pending_token": login.json()["pending_token"], "code": _next_step_code(secret)},
    )
    assert fresh.status_code == 200, fresh.text


@pytest.mark.anyio
async def test_recovery_code_input_is_normalized(second_factor_identity):
    """Lower case, missing hyphen or stray spaces must not block a valid code."""
    client, identity = second_factor_identity
    _, codes = await _enrol(client)
    # Each code works once, so every variant style gets its own fresh code.
    variants = [codes[2].lower().replace("-", ""), codes[3].replace("-", " "), codes[4]]
    for variant in variants:
        login = await _login(client, identity["email"])
        answer = await client.post(
            "/api/auth/login/second-factor",
            json={"pending_token": login.json()["pending_token"], "recovery_code": variant},
        )
        assert answer.status_code == 200, f"{variant!r} refused: {answer.text}"


@pytest.mark.anyio
async def test_admin_cannot_read_a_colleagues_factor_row():
    """The admin policy manages (deletes) factors; it must not SELECT them."""
    from tests.password_reset_support import runtime_store
    from sqlalchemy import text as sql_text

    org_id, admin_id, clerk_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    connection = psycopg2.connect(MIGRATOR_SYNC_URL)
    try:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO public.organization (id, name) VALUES (%s, %s)",
                           (org_id, "Factor RLS probe"))
            for user_id, email, role in ((admin_id, f"factor-admin-{uuid.uuid4()}@example.test", "OWNER_ADMIN"),
                                         (clerk_id, f"factor-clerk-{uuid.uuid4()}@example.test", "CLERK")):
                cursor.execute(
                    'INSERT INTO public."user" (id, organization_id, email, password_hash, display_name)'
                    " VALUES (%s, %s, %s, %s, %s)",
                    (user_id, org_id, email, "synthetic-not-a-login", "RLS probe"))
                cursor.execute(
                    "INSERT INTO public.membership (id, organization_id, user_id, role)"
                    " VALUES (%s, %s, %s, %s)",
                    (str(uuid.uuid4()), org_id, user_id, role))
            cursor.execute(
                "INSERT INTO public.user_second_factor (organization_id, user_id, secret_encrypted)"
                " VALUES (%s, %s, %s)",
                (org_id, clerk_id, "gAAAAA-synthetic"))
        connection.commit()
    finally:
        connection.close()

    try:
        async with runtime_store() as session:
            await session.execute(sql_text("SELECT set_config('app.current_org_id', :org, true)"),
                                  {"org": org_id})
            await session.execute(sql_text("SELECT set_config('app.current_user_id', :uid, true)"),
                                  {"uid": admin_id})
            rows = (await session.execute(sql_text(
                "SELECT count(*) FROM public.user_second_factor WHERE user_id = :uid"),
                {"uid": clerk_id})).scalar_one()
            assert rows == 0, "An OWNER_ADMIN must not read a colleague's factor row"
            blocked = await session.execute(sql_text(
                "DELETE FROM public.user_second_factor WHERE organization_id = :org AND user_id = :uid"),
                {"org": org_id, "uid": clerk_id})
            assert blocked.rowcount == 0, (
                "Direct table DELETE is not the admin path; the reset runs through"
                " the reset_second_factor function (covered by the endpoint test)"
            )
    finally:
        connection = psycopg2.connect(MIGRATOR_SYNC_URL)
        try:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM public.user_second_factor WHERE organization_id = %s", (org_id,))
                cursor.execute("DELETE FROM public.membership WHERE organization_id = %s", (org_id,))
                cursor.execute('DELETE FROM public."user" WHERE organization_id = %s', (org_id,))
                cursor.execute("DELETE FROM public.organization WHERE id = %s", (org_id,))
            connection.commit()
        finally:
            connection.close()


@pytest.mark.anyio
async def test_factor_rows_bind_user_and_organization_together():
    """A factor of another organization's user must not be insertable (0145 style)."""
    import psycopg2.errors

    first = {"email": f"fk-first-{uuid.uuid4()}@example.test", "role": "CLERK"}
    second = {"email": f"fk-second-{uuid.uuid4()}@example.test", "role": "CLERK"}
    with temporary_auth_rows([first]):
        with temporary_auth_rows([second]):
            try:
                _db(
                    'INSERT INTO public.user_second_factor (organization_id, user_id, secret_encrypted)'
                    ' VALUES (%s, %s, %s)',
                    (str(first["organization_id"]), str(second["user_id"]), "gAAAAA-synthetic"),
                )
            except psycopg2.errors.ForeignKeyViolation:
                pass
            else:
                raise AssertionError("Composite (organization_id, user_id) foreign key not enforced")
            try:
                _db(
                    'INSERT INTO public.user_recovery_code (organization_id, user_id, position, code_hash)'
                    ' VALUES (%s, %s, 0, %s)',
                    (str(first["organization_id"]), str(second["user_id"]), "ab" * 32),
                )
            except psycopg2.errors.ForeignKeyViolation:
                pass
            else:
                raise AssertionError("Composite (organization_id, user_id) foreign key not enforced")


# --- Review round 3 (PR #656, base 87fa2d42): red first for the reset findings. ---


def _add_colleague(identity, *, active: bool) -> str:
    """A second account of the admin's organization with a confirmed factor and one recovery code."""
    colleague, organization = str(uuid.uuid4()), str(identity["organization_id"])
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute(
            'INSERT INTO public."user" (id, organization_id, email, password_hash, display_name, is_active)'
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (colleague, organization, f"colleague-{colleague}@example.test", "synthetic-not-a-login", "Colleague", active),
        )
        cursor.execute(
            "INSERT INTO public.membership (id, organization_id, user_id, role) VALUES (%s, %s, %s, 'CLERK')",
            (str(uuid.uuid4()), organization, colleague),
        )
        cursor.execute(
            "INSERT INTO public.user_second_factor (organization_id, user_id, secret_encrypted, confirmed, confirmed_at)"
            " VALUES (%s, %s, 'gAAAAA-synthetic', true, now())",
            (organization, colleague),
        )
        cursor.execute(
            "INSERT INTO public.user_recovery_code (organization_id, user_id, position, code_hash)"
            " VALUES (%s, %s, 0, %s)",
            (organization, colleague, "ab" * 32),
        )
    return colleague


def _remove_colleague(colleague: str) -> None:
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        for statement in (
            "DELETE FROM public.user_recovery_code WHERE user_id = %s",
            "DELETE FROM public.user_second_factor WHERE user_id = %s",
            "DELETE FROM public.membership WHERE user_id = %s",
            'DELETE FROM public."user" WHERE id = %s',
        ):
            cursor.execute(statement, (colleague,))


def _factor_state(colleague: str) -> tuple[int, int]:
    return (
        _db("SELECT count(*) FROM public.user_second_factor WHERE user_id = %s", (colleague,))[0][0],
        _db("SELECT count(*) FROM public.user_recovery_code WHERE user_id = %s", (colleague,))[0][0],
    )


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["selfhost", "hosted"])
async def test_admin_resets_the_factor_of_a_deactivated_user(second_factor_identity, monkeypatch, mode):
    """A lost device of a deactivated account may be cleaned up; it must not answer 500."""
    monkeypatch.setattr(settings, "deployment_mode", mode)
    client, identity = second_factor_identity
    if mode == "hosted":
        await _enrol(client)
    colleague = _add_colleague(identity, active=False)
    try:
        reset = await client.post(f"/api/users/{colleague}/reset-second-factor")
        assert reset.status_code == 204, reset.text
        assert _factor_state(colleague) == (0, 0)
        audited = _db(
            "SELECT count(*) FROM public.audit_event WHERE entity_id = %s AND action = 'RESET_SECOND_FACTOR'",
            (colleague,),
        )
        assert audited[0][0] == 1
    finally:
        _remove_colleague(colleague)


@pytest.mark.anyio
async def test_reset_of_a_user_outside_the_organization_stays_refused(second_factor_identity):
    client, identity = second_factor_identity
    other = {"email": f"foreign-{uuid.uuid4()}@example.test", "role": "CLERK"}
    with temporary_auth_rows([other]):
        reset = await client.post(f"/api/users/{other['user_id']}/reset-second-factor")
        assert reset.status_code == 404, reset.text


@pytest.mark.anyio
@pytest.mark.parametrize("runtime", ["worker", "review"])
async def test_reset_second_factor_function_refuses_other_runtime_roles(second_factor_identity, runtime):
    """The definer function is for the interactive runtime only (the 0147 session_user guard)."""
    from sqlalchemy import text as sql_text
    from sqlalchemy.exc import DBAPIError

    from app.core.db import get_review_session_factory, get_worker_session_factory, set_tenant_context

    _, identity = second_factor_identity
    colleague = _add_colleague(identity, active=True)
    factory = get_worker_session_factory() if runtime == "worker" else get_review_session_factory()
    try:
        with pytest.raises(DBAPIError, match="permission denied|Interactive runtime required"):
            async with factory() as session, session.begin():
                await set_tenant_context(session, identity["organization_id"])
                await session.execute(
                    sql_text("SELECT set_config('app.current_user_id', :actor, true)"),
                    {"actor": str(identity["user_id"])},
                )
                await session.execute(
                    sql_text("SELECT public.reset_second_factor(:org, :target)"),
                    {"org": str(identity["organization_id"]), "target": colleague},
                )
        assert _factor_state(colleague) == (1, 1)
    finally:
        _remove_colleague(colleague)


# --- CI round 4 (PR #656, base c36180fb): the hosted BDD run found this. ---


@pytest.mark.anyio
async def test_import_admission_ignores_second_factor_rows():
    """Every hosted staff account has a factor, so factor rows must not make
    an organization non-empty for archive import (like invitations and assents)."""
    import json

    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tests.conftest import APP_RW_URL

    admin = {"email": f"admission-{uuid.uuid4()}@example.test", "role": "OWNER_ADMIN"}
    with temporary_auth_rows([admin]):
        organization, user = str(admin["organization_id"]), str(admin["user_id"])
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.user_second_factor (organization_id, user_id, secret_encrypted, confirmed, confirmed_at)"
                " VALUES (%s, %s, 'gAAAAA-synthetic', true, now())", (organization, user))
            cursor.execute(
                "INSERT INTO public.user_recovery_code (organization_id, user_id, position, code_hash)"
                " VALUES (%s, %s, 0, %s)", (organization, user, "ab" * 32))
        engine = create_async_engine(APP_RW_URL)
        try:
            async with async_sessionmaker(engine)() as session, session.begin():
                await session.execute(sql_text("SELECT set_config('app.current_org_id', :org, false)"), {"org": organization})
                await session.execute(sql_text("SELECT set_config('app.current_user_id', :actor, false)"), {"actor": user})
                result = await session.scalar(
                    sql_text("SELECT public.organization_import_admission(:org, '{}'::jsonb)::text"), {"org": organization})
            assert json.loads(result)["empty"] is True
        finally:
            await engine.dispose()


# --- Issue #659: the enrolment stays reachable while the organization is in its
# cooling-off period, so an account without a factor can still rescue it. ---

from contextlib import contextmanager  # noqa: E402


@contextmanager
def _organization_deletion(organization_id, status):
    """Hold the organization in a deletion state for the length of the block."""
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO public.organization_deletion (organization_id, status, confirmed_at, execute_after)"
            " VALUES (%s, %s, now(), now() + interval '7 days')",
            (str(organization_id), status),
        )
    try:
        yield
    finally:
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM public.organization_deletion WHERE organization_id = %s",
                (str(organization_id),),
            )


READ_ONLY_DURING_COOLING_OFF = "Widerrufsfrist schreibgeschützt"


@pytest.mark.anyio
async def test_hosted_cooling_off_leaves_an_account_without_a_factor_only_its_enrolment(
    second_factor_identity, monkeypatch
):
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    client, identity = second_factor_identity
    with _organization_deletion(identity["organization_id"], "cooling_off"):
        # Signing in works and leads to the enrolment, as outside cooling-off.
        login = await _login(client, identity["email"])
        assert login.status_code == 200, login.text
        assert login.json()["second_factor_enrolment_required"] is True

        # Nothing but the enrolment is writable: the records, the preferences
        # and another account's factor stay refused by the cooling-off gate.
        refused_writes = [
            ("POST", "/api/persons", {"json": {"last_name": "Blocked"}}),
            ("PATCH", "/api/auth/preferences", {"json": {"design_theme": "warm"}}),
            ("POST", f"/api/users/{identity['user_id']}/reset-second-factor", {}),
        ]
        for method, path, options in refused_writes:
            refused = await client.request(method, path, **options)
            assert refused.status_code == 409, (method, path, refused.text)
            assert READ_ONLY_DURING_COOLING_OFF in refused.text, (method, path, refused.text)

        # Every other request still needs the factor, as outside cooling-off:
        # the export and the cancellation are not reachable without one.
        for method, path in (
            ("GET", "/api/auth/preferences"),
            ("POST", "/api/organizations/export"),
            ("POST", "/api/organizations/deletion/cancel"),
        ):
            gated = await client.request(method, path)
            assert gated.status_code == 403, (method, path, gated.text)
            assert gated.json()["detail"]["code"] == "second_factor_enrolment_required"

        started = await client.post("/api/auth/second-factor/enrolment")
        assert started.status_code == 200, started.text
        secret = started.json()["secret"]
        wrong = await client.post(
            "/api/auth/second-factor/enrolment/confirm", json={"code": _wrong_code(secret)}
        )
        assert wrong.status_code == 401, wrong.text
        confirmed = await client.post(
            "/api/auth/second-factor/enrolment/confirm", json={"code": totp_code(secret)}
        )
        assert confirmed.status_code == 200, confirmed.text
        assert len(confirmed.json()["recovery_codes"]) == 10

        # The factor is enrolled, the records stay read-only until the deletion
        # is cancelled, and the cancellation now works.
        status = await client.get("/api/auth/second-factor")
        assert status.json() == {"enrolled": True}
        assert (await client.post("/api/persons", json={"last_name": "Blocked"})).status_code == 409
        assert (await client.post("/api/organizations/deletion/cancel")).status_code == 200
        allowed = await client.patch("/api/auth/preferences", json={"design_theme": "warm"})
        assert allowed.status_code == 200, allowed.text


@pytest.mark.anyio
async def test_selfhost_cooling_off_cancels_without_a_factor_and_keeps_records_read_only(
    second_factor_identity, monkeypatch
):
    monkeypatch.setattr(settings, "deployment_mode", "selfhost")
    client, identity = second_factor_identity
    with _organization_deletion(identity["organization_id"], "cooling_off"):
        refused = await client.post("/api/persons", json={"last_name": "Blocked"})
        assert refused.status_code == 409, refused.text
        assert READ_ONLY_DURING_COOLING_OFF in refused.text
        assert (await client.post("/api/organizations/deletion/cancel")).status_code == 200
        allowed = await client.patch("/api/auth/preferences", json={"design_theme": "warm"})
        assert allowed.status_code == 200, allowed.text


@pytest.mark.anyio
async def test_erased_organization_refuses_the_enrolment_at_the_gate(second_factor_identity, monkeypatch):
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    client, identity = second_factor_identity
    with _organization_deletion(identity["organization_id"], "deleted_pending_retention"):
        for method, path in (
            ("POST", "/api/auth/second-factor/enrolment"),
            ("POST", "/api/auth/second-factor/enrolment/confirm"),
            ("GET", "/api/auth/second-factor"),
        ):
            refused = await client.request(method, path)
            assert refused.status_code == 401, (method, path, refused.text)


@pytest.mark.anyio
async def test_lifecycle_guard_exempts_only_the_factor_tables_and_only_during_cooling_off(
    second_factor_identity,
):
    """The route gate is not the only barrier: the database refuses an
    authentication that completed just before the state changed."""
    from sqlalchemy import text as sql_text
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tests.conftest import APP_RW_URL

    _, identity = second_factor_identity
    organization, user = str(identity["organization_id"]), str(identity["user_id"])
    factor = (
        "INSERT INTO public.user_second_factor (organization_id, user_id, secret_encrypted)"
        " VALUES (:org, :user, 'gAAAAA-synthetic')"
    )
    recovery = (
        "INSERT INTO public.user_recovery_code (organization_id, user_id, position, code_hash)"
        " VALUES (:org, :user, 0, :hash)"
    )
    record = "INSERT INTO public.person (organization_id, last_name) VALUES (:org, 'Blocked')"
    params = {"org": organization, "user": user, "hash": "ab" * 32}

    async def run_as_runtime(statement):
        engine = create_async_engine(APP_RW_URL)
        try:
            async with async_sessionmaker(engine)() as session, session.begin():
                await session.execute(sql_text("SELECT set_config('app.current_org_id', :org, true)"), params)
                await session.execute(sql_text("SELECT set_config('app.current_user_id', :user, true)"), params)
                await session.execute(sql_text(statement), params)
        finally:
            await engine.dispose()

    async def refused(statement):
        with pytest.raises(DBAPIError) as raised:
            await run_as_runtime(statement)
        return getattr(raised.value.orig, "sqlstate", None)

    with _organization_deletion(identity["organization_id"], "cooling_off"):
        await run_as_runtime(factor)
        await run_as_runtime(recovery)
        assert await refused(record) == "P0023", "every other table stays read-only in cooling-off"
    with _organization_deletion(identity["organization_id"], "deleted_pending_retention"):
        assert await refused(factor) == "P0023"
        assert await refused(recovery) == "P0023"


def test_cooling_off_guard_exemption_migration_roundtrip(run_migrations):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, text as sql_text

    guard = "SELECT prosrc FROM pg_proc WHERE oid='public.organization_lifecycle_guard()'::regprocedure"
    exemption = "TG_TABLE_NAME IN ('user_second_factor','user_recovery_code')"
    migration = ScriptDirectory("migrations").get_revision("0189").module
    assert migration.down_revision == "0188"
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            upgraded = connection.scalar(sql_text(guard))
            assert exemption in upgraded
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                downgraded = connection.scalar(sql_text(guard))
                assert exemption not in downgraded
                assert "AND d.status IN ('cooling_off','deleted_pending_retention')) THEN" in downgraded
                # The 0184 invitation exemption is not this revision's to touch.
                assert "TG_TABLE_NAME='user_invitation' AND TG_OP='DELETE'" in downgraded
                migration.upgrade()
                assert connection.scalar(sql_text(guard)) == upgraded
            transaction.rollback()
    finally:
        engine.dispose()
