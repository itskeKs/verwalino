"""Regression tests for runtime authentication hardening."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from io import StringIO

import pytest
import psycopg2
import httpx
from itsdangerous import URLSafeTimedSerializer

from app.core.config import settings
from app.core import security
from app.core.security import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    create_session_token,
    decode_session_token,
    hash_password,
    validate_new_password,
)


INVALID_CREDENTIALS = (401, {"detail": "Invalid credentials"})


def test_temporary_auth_rows_rolls_back_and_closes_after_partial_setup_error(monkeypatch):
    class Cursor:
        def __init__(self):
            self.calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("partial setup failure")

    class Connection:
        def __init__(self):
            self.cursor_instance = Cursor()
            self.rollback_calls = 0
            self.closed = False

        def cursor(self):
            return self.cursor_instance

        def commit(self):
            pass

        def rollback(self):
            self.rollback_calls += 1

        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(psycopg2, "connect", lambda *_: connection)
    with pytest.raises(RuntimeError, match="partial setup failure"):
        with temporary_auth_rows([{"email": "partial@example.test"}]):
            pass
    assert connection.rollback_calls >= 1
    assert connection.closed


def test_temporary_auth_rows_rolls_back_and_closes_after_cleanup_error(monkeypatch):
    class Cursor:
        def __init__(self):
            self.delete_phase = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, *_args):
            if query.startswith("DELETE"):
                raise RuntimeError("cleanup failure")

    class Connection:
        def __init__(self):
            self.cursor_instance = Cursor()
            self.rollback_calls = 0
            self.closed = False

        def cursor(self):
            return self.cursor_instance

        def commit(self):
            pass

        def rollback(self):
            self.rollback_calls += 1

        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(psycopg2, "connect", lambda *_: connection)
    with pytest.raises(RuntimeError, match="cleanup failure"):
        with temporary_auth_rows([{"email": "cleanup@example.test"}]):
            pass
    assert connection.rollback_calls >= 1
    assert connection.closed


@contextmanager
def temporary_auth_rows(rows: list[dict]):
    """Create committed rows visible to the app, and remove them in FK order."""
    from tests.conftest import MIGRATOR_SYNC_URL

    connection = psycopg2.connect(MIGRATOR_SYNC_URL)
    created_organization_ids: list[uuid.UUID] = []
    created_user_ids: list[uuid.UUID] = []
    created_membership_user_ids: list[uuid.UUID] = []
    try:
        with connection.cursor() as cursor:
            for row in rows:
                row["organization_id"] = uuid.uuid4()
                row["user_id"] = uuid.uuid4()
                password_hash = row.get("password_hash")
                if password_hash is None:
                    password_hash = hash_password("testpass1234")
                cursor.execute(
                    "INSERT INTO public.organization (id, name) VALUES (%s, %s)",
                    (str(row["organization_id"]), f"Auth test {row['organization_id']}"),
                )
                created_organization_ids.append(row["organization_id"])
                cursor.execute(
                    'INSERT INTO public."user" (id, organization_id, email, password_hash, display_name, is_active) '
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        str(row["user_id"]),
                        str(row["organization_id"]),
                        row["email"],
                        password_hash,
                        "Auth test",
                        row.get("is_active", True),
                    ),
                )
                created_user_ids.append(row["user_id"])
                if row.get("membership", True):
                    cursor.execute(
                        "INSERT INTO public.membership (id, organization_id, user_id, role) "
                        "VALUES (%s, %s, %s, %s)",
                        (
                            str(uuid.uuid4()),
                            str(row["organization_id"]),
                            str(row["user_id"]),
                            row.get("role", "CLERK"),
                        ),
                    )
                    created_membership_user_ids.append(row["user_id"])
        connection.commit()
        yield rows
    except BaseException:
        connection.rollback()
        raise
    finally:
        try:
            with connection.cursor() as cursor:
                for user_id in reversed(created_membership_user_ids):
                    cursor.execute("DELETE FROM public.membership WHERE user_id = %s", (str(user_id),))
                for user_id in reversed(created_user_ids):
                    cursor.execute('DELETE FROM public."user" WHERE id = %s', (str(user_id),))
                for organization_id in reversed(created_organization_ids):
                    cursor.execute(
                        "DELETE FROM public.organization WHERE id = %s", (str(organization_id),)
                    )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


def test_session_token_contains_only_uuid_identities():
    token = create_session_token(uuid.uuid4(), uuid.uuid4())
    payload = decode_session_token(token)
    assert payload is not None
    assert set(payload) == {"uid", "oid"}
    assert decode_session_token("not-a-valid-token") is None


def test_decode_session_token_rejects_signed_payload_with_non_string_identities():
    signed_payload = URLSafeTimedSerializer(settings.session_secret).dumps(
        {"uid": 1, "oid": str(uuid.uuid4())}, salt="hv-session"
    )
    assert decode_session_token(signed_payload) is None


def test_new_password_policy_has_explicit_bounds():
    assert MIN_PASSWORD_LENGTH == 12
    assert MAX_PASSWORD_LENGTH == 128
    validate_new_password("a" * MIN_PASSWORD_LENGTH)
    validate_new_password("a" * MAX_PASSWORD_LENGTH)
    with pytest.raises(ValueError):
        validate_new_password("short")
    with pytest.raises(ValueError):
        validate_new_password("a" * (MAX_PASSWORD_LENGTH + 1))


def test_normalize_email_has_single_canonical_form_and_bounds():
    assert security.MAX_EMAIL_LENGTH == 320
    assert security.normalize_email("  Admin@Example.Test ") == "admin@example.test"
    for invalid in (None, 1, "   ", "a" * (security.MAX_EMAIL_LENGTH + 1)):
        with pytest.raises(ValueError):
            security.normalize_email(invalid)


def test_admin_cli_rejects_invalid_password_before_connecting(monkeypatch):
    from app import admin_cli

    monkeypatch.setattr(
        "sys.argv", ["admin_cli", "--org", "Example", "--email", "admin@example.test", "--password", "short"]
    )
    monkeypatch.setattr(admin_cli.psycopg2, "connect", lambda *_: pytest.fail("must not connect"))
    with pytest.raises(SystemExit):
        admin_cli.main()


def test_admin_cli_rejects_invalid_email_before_connecting(monkeypatch):
    from app import admin_cli

    monkeypatch.setattr(
        "sys.argv", ["admin_cli", "--org", "Example", "--email", "   ", "--password", "this-password-is-valid"]
    )
    monkeypatch.setattr(admin_cli.psycopg2, "connect", lambda *_: pytest.fail("must not connect"))
    with pytest.raises(SystemExit):
        admin_cli.main()


def test_admin_cli_normalizes_email_before_query_and_insert(monkeypatch):
    from app import admin_cli

    class Cursor:
        def __init__(self):
            self.calls: list[tuple[str, tuple]] = []
            self.lookup_count = 0

        def execute(self, query, params):
            self.calls.append((query, params))

        def fetchone(self):
            self.lookup_count += 1
            return None

    class Connection:
        def __init__(self):
            self.cursor_instance = Cursor()

        def cursor(self):
            return self.cursor_instance

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    connection = Connection()
    monkeypatch.setattr(
        "sys.argv",
        [
            "admin_cli",
            "--org",
            "Example",
            "--email",
            "  ADMIN@Example.Test ",
            "--password",
            "this-password-is-valid",
        ],
    )
    monkeypatch.setattr(admin_cli.psycopg2, "connect", lambda *_: connection)
    admin_cli.main()
    email_params = [
        params
        for query, params in connection.cursor_instance.calls
        if 'public."user"' in query or 'bootstrap_organization_admin' in query
    ]
    assert len(email_params) == 2
    assert all("admin@example.test" in params for params in email_params)


def test_admin_cli_detects_legacy_email_with_global_canonical_lookup(monkeypatch):
    from app import admin_cli

    class Cursor:
        def __init__(self):
            self.calls: list[tuple[str, tuple]] = []

        def execute(self, query, params):
            self.calls.append((query, params))

        def fetchone(self):
            return ("existing-id",)

    class Connection:
        def __init__(self):
            self.cursor_instance = Cursor()
            self.rollback_calls = 0

        def cursor(self):
            return self.cursor_instance

        def rollback(self):
            self.rollback_calls += 1

        def close(self):
            pass

    connection = Connection()
    monkeypatch.setattr(
        "sys.argv",
        [
            "admin_cli",
            "--org",
            "Existing organization",
            "--email",
            "legacy@example.test",
            "--password",
            "this-password-is-valid",
        ],
    )
    monkeypatch.setattr(admin_cli.psycopg2, "connect", lambda *_: connection)
    admin_cli.main()

    lookup_query, lookup_params = connection.cursor_instance.calls[1]
    assert "pg_catalog.lower(pg_catalog.btrim(email))" in lookup_query
    assert "organization_id" not in lookup_query
    assert lookup_params == ("legacy@example.test",)
    assert connection.rollback_calls == 1


def test_admin_cli_reads_newline_terminated_password_from_stdin(monkeypatch):
    from app import admin_cli

    class Cursor:
        def execute(self, *_args):
            pass

        def fetchone(self):
            return ("existing",)

    class Connection:
        def __init__(self):
            self.cursor_instance = Cursor()

        def cursor(self):
            return self.cursor_instance

        def rollback(self):
            pass

        def close(self):
            pass

    connection = Connection()
    monkeypatch.setattr(
        "sys.argv", ["admin_cli", "--org", "Example", "--email", "admin@example.test", "--password-stdin"]
    )
    assert all("this-password-is-valid" not in argument for argument in __import__("sys").argv)
    monkeypatch.setattr("sys.stdin", StringIO("this-password-is-valid\n"))
    monkeypatch.setattr(admin_cli.psycopg2, "connect", lambda *_: connection)
    admin_cli.main()


def test_admin_cli_rejects_stdin_without_newline_before_connecting(monkeypatch):
    from app import admin_cli

    monkeypatch.setattr(
        "sys.argv", ["admin_cli", "--org", "Example", "--email", "admin@example.test", "--password-stdin"]
    )
    monkeypatch.setattr("sys.stdin", StringIO("this-password-is-valid"))
    monkeypatch.setattr(admin_cli.psycopg2, "connect", lambda *_: pytest.fail("must not connect"))
    with pytest.raises(SystemExit):
        admin_cli.main()


def test_login_request_strips_and_bounds_email():
    from app.api.auth import LoginRequest

    assert LoginRequest(email="  ADMIN@example.test ", password="short").email == "admin@example.test"
    with pytest.raises(ValueError):
        LoginRequest(email="a" * 321, password="short")
    with pytest.raises(ValueError):
        LoginRequest(email="   ", password="short")


@pytest.mark.asyncio
async def test_login_normalizes_email_and_returns_current_session(client_unauth, seed_data):
    response = await client_unauth.post(
        "/api/auth/login",
        json={"email": "  ADMIN@ALPHA.DE ", "password": "testpass1234"},
    )
    assert response.status_code == 200
    assert response.json()["role"] == "OWNER_ADMIN"
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]

    session = await client_unauth.get("/api/auth/session")
    assert session.status_code == 200
    assert session.json()["role"] == "OWNER_ADMIN"


@pytest.mark.asyncio
async def test_session_rejects_signed_payload_with_non_string_identity(client_a_admin, seed_data):
    malformed = URLSafeTimedSerializer(settings.session_secret).dumps(
        {"uid": 1, "oid": str(seed_data["org_a_id"])}, salt="hv-session"
    )
    client_a_admin.cookies.set(settings.session_cookie_name, malformed)
    response = await client_a_admin.get("/api/auth/session")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_rejects_empty_and_oversized_email_before_lookup(client_unauth):
    for email in ("   ", "a" * 321):
        response = await client_unauth.post(
            "/api/auth/login", json={"email": email, "password": "testpass1234"}
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_login_failures_have_identical_public_response(client_unauth, monkeypatch, seed_data):
    from app.api import auth as auth_api

    original_verify = auth_api.verify_password
    verified_hashes: list[str] = []

    def spy_verify(password: str, password_hash: str) -> bool:
        verified_hashes.append(password_hash)
        return original_verify(password, password_hash)

    monkeypatch.setattr(auth_api, "verify_password", spy_verify)
    responses = []
    for email, password in (("missing@example.test", "wrongpassword"), ("admin@alpha.de", "wrongpassword")):
        response = await client_unauth.post("/api/auth/login", json={"email": email, "password": password})
        responses.append((response.status_code, response.json()))
    assert responses == [INVALID_CREDENTIALS] * 2
    assert verified_hashes.count(auth_api._DUMMY_PASSWORD_HASH) == 1


@pytest.mark.asyncio
async def test_login_sensitive_failures_have_one_public_response(client_unauth):
    shared_email = f"auth-{uuid.uuid4().hex}@example.test"
    rows = [
        {"email": f"inactive-{uuid.uuid4().hex}@example.test", "is_active": False},
        {"email": f"memberless-{uuid.uuid4().hex}@example.test", "membership": False},
        {"email": shared_email},
        {"email": f" {shared_email.upper()} "},
        {"email": f"invalid-hash-{uuid.uuid4().hex}@example.test", "password_hash": "not-an-argon2-hash"},
    ]
    with temporary_auth_rows(rows):
        responses = []
        for row in rows:
            response = await client_unauth.post(
                "/api/auth/login",
                json={"email": row["email"], "password": "testpass1234"},
            )
            responses.append((response.status_code, response.json()))
        assert responses == [INVALID_CREDENTIALS] * len(rows)


@pytest.mark.asyncio
async def test_role_in_cookie_is_not_trusted(client_a_admin):
    token = create_session_token(uuid.uuid4(), uuid.uuid4())
    client_a_admin.cookies.set("hv_session", token)
    response = await client_a_admin.get("/api/auth/session")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_session_rejects_known_user_with_wrong_organization(client_a_admin, seed_data):
    token = create_session_token(seed_data["user_a_admin_id"], seed_data["org_b_id"])
    client_a_admin.cookies.set(settings.session_cookie_name, token)
    response = await client_a_admin.get("/api/auth/session")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_legacy_role_cookie_is_rejected(client_a_admin, seed_data):
    legacy_token = URLSafeTimedSerializer(settings.session_secret).dumps(
        {
            "uid": str(seed_data["user_a_admin_id"]),
            "oid": str(seed_data["org_a_id"]),
            "role": "OWNER_ADMIN",
        },
        salt="hv-session",
    )
    client_a_admin.cookies.set(settings.session_cookie_name, legacy_token)
    response = await client_a_admin.get("/api/users")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_membership_role_change_is_visible_on_next_request(
    client_a_admin, seed_data
):
    before = await client_a_admin.get("/api/users")
    assert before.status_code == 200
    from tests.conftest import MIGRATOR_SYNC_URL

    connection = psycopg2.connect(MIGRATOR_SYNC_URL)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE public.membership SET role = 'READ_ONLY' "
                "WHERE user_id = %s AND organization_id = %s",
                (str(seed_data["user_a_admin_id"]), str(seed_data["org_a_id"])),
            )
        connection.commit()
        session_response = await client_a_admin.get("/api/auth/session")
        assert session_response.status_code == 200
        assert session_response.json()["role"] == "READ_ONLY"
        response = await client_a_admin.post(
            "/api/users",
            json={"email": "blocked@example.test", "password": "this-password-is-valid", "role": "CLERK"},
        )
        assert response.status_code == 403
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE public.membership SET role = 'OWNER_ADMIN' "
                "WHERE user_id = %s AND organization_id = %s",
                (str(seed_data["user_a_admin_id"]), str(seed_data["org_a_id"])),
            )
        connection.commit()
        connection.close()


@pytest.mark.asyncio
async def test_session_is_invalid_immediately_after_user_deactivation(seed_data):
    from tests.conftest import MIGRATOR_SYNC_URL, _api_test_client

    # A durable invalidation must not revoke the shared seed's later test cookies.
    rows = [{"email": f"deactivation-{uuid.uuid4()}@example.test", "role": "OWNER_ADMIN"}]
    with temporary_auth_rows(rows):
        user = rows[0]
        token = create_session_token(user["user_id"], user["organization_id"])
        async with _api_test_client(cookies={settings.session_cookie_name: token}) as client:
            with psycopg2.connect(MIGRATOR_SYNC_URL) as connection:
                with connection.cursor() as cursor:
                    cursor.execute('UPDATE public."user" SET is_active = false WHERE id = %s', (str(user["user_id"]),))
            response = await client.get("/api/auth/session")
            assert response.status_code == 401
            with psycopg2.connect(MIGRATOR_SYNC_URL) as connection:
                with connection.cursor() as cursor:
                    cursor.execute('UPDATE public."user" SET is_active = true WHERE id = %s', (str(user["user_id"]),))
            # Reactivation must not resurrect the old cookie either.
            assert (await client.get("/api/auth/session")).status_code == 401


@pytest.mark.asyncio
async def test_user_create_enforces_new_password_bounds(client_a_admin):
    created_user_ids: list[str] = []
    try:
        for length, expected_status in ((11, 422), (12, 201), (128, 201), (129, 422)):
            response = await client_a_admin.post(
                "/api/users",
                json={
                    "email": f"password-{length}-{uuid.uuid4().hex}@example.test",
                    "password": "a" * length,
                    "role": "CLERK",
                },
            )
            assert response.status_code == expected_status
            if expected_status == 201:
                created_user_ids.append(response.json()["id"])
    finally:
        if created_user_ids:
            from tests.conftest import MIGRATOR_SYNC_URL

            connection = psycopg2.connect(MIGRATOR_SYNC_URL)
            try:
                with connection.cursor() as cursor:
                    for user_id in created_user_ids:
                        cursor.execute("DELETE FROM public.membership WHERE user_id = %s", (user_id,))
                        cursor.execute('DELETE FROM public."user" WHERE id = %s', (user_id,))
                connection.commit()
            finally:
                connection.close()


@pytest.mark.asyncio
async def test_user_create_stores_canonical_email_and_rejects_same_org_variant(client_a_admin):
    created_user_ids: list[str] = []
    raw_email = f"  Admin-{uuid.uuid4().hex}@Example.Test "
    canonical_email = raw_email.strip().lower()
    try:
        created = await client_a_admin.post(
            "/api/users",
            json={"email": raw_email, "password": "this-password-is-valid", "role": "CLERK"},
        )
        assert created.status_code == 201
        assert created.json()["email"] == canonical_email
        created_user_ids.append(created.json()["id"])

        duplicate = await client_a_admin.post(
            "/api/users",
            json={"email": canonical_email.upper(), "password": "this-password-is-valid", "role": "CLERK"},
        )
        assert duplicate.status_code == 409
    finally:
        if created_user_ids:
            from tests.conftest import MIGRATOR_SYNC_URL

            connection = psycopg2.connect(MIGRATOR_SYNC_URL)
            try:
                with connection.cursor() as cursor:
                    for user_id in created_user_ids:
                        cursor.execute("DELETE FROM public.membership WHERE user_id = %s", (user_id,))
                        cursor.execute('DELETE FROM public."user" WHERE id = %s', (user_id,))
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()


@pytest.mark.asyncio
async def test_user_create_rejects_normalized_legacy_email_without_a_server_error(seed_data):
    from app.main import app
    from tests.conftest import MIGRATOR_SYNC_URL

    legacy_user_id = uuid.uuid4()
    canonical_email = f"legacy-{uuid.uuid4().hex}@example.test"
    legacy_email = f"  {canonical_email.upper()} "
    connection = psycopg2.connect(MIGRATOR_SYNC_URL)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                'INSERT INTO public."user" (id, organization_id, email, password_hash, display_name) '
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    str(legacy_user_id),
                    str(seed_data["org_a_id"]),
                    legacy_email,
                    hash_password("testpass1234"),
                    "Legacy user",
                ),
            )
        connection.commit()

        token = create_session_token(seed_data["user_a_admin_id"], seed_data["org_a_id"])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
            cookies={settings.session_cookie_name: token},
        ) as client:
            response = await client.post(
                "/api/users",
                json={
                    "email": canonical_email,
                    "password": "this-password-is-valid",
                    "role": "CLERK",
                },
            )
        assert response.status_code == 409
        assert response.json() == {"detail": "Email already in use"}
    finally:
        try:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM public.membership WHERE user_id = %s", (str(legacy_user_id),))
                cursor.execute('DELETE FROM public."user" WHERE id = %s', (str(legacy_user_id),))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


@pytest.mark.asyncio
async def test_production_cookie_attributes_are_set_and_cleared(client_unauth, monkeypatch, seed_data):
    monkeypatch.setattr(settings, "session_cookie_name", "__Host-hv_session")
    monkeypatch.setattr(settings, "session_cookie_secure", True)
    monkeypatch.setattr(settings, "session_max_age_seconds", 7200)

    login = await client_unauth.post(
        "/api/auth/login", json={"email": "admin@alpha.de", "password": "testpass1234"}
    )
    assert login.status_code == 200
    set_cookie = login.headers["set-cookie"].lower()
    for expected in ("__host-hv_session=", "httponly", "secure", "samesite=lax", "path=/", "max-age=7200"):
        assert expected in set_cookie
    assert "domain=" not in set_cookie

    logout = await client_unauth.post("/api/auth/logout")
    assert logout.status_code == 204
    clear_cookie = logout.headers["set-cookie"].lower()
    for expected in ("__host-hv_session=", "httponly", "secure", "samesite=lax", "path=/", "max-age=0", "expires="):
        assert expected in clear_cookie


@pytest.fixture
def throttle_clock(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.core import rate_limit

    class Clock:
        now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)

        def advance(self, minutes):
            self.now += timedelta(minutes=minutes)

    clock = Clock()
    monkeypatch.setattr(rate_limit, "utcnow", lambda: clock.now)
    return clock


async def _login(client, email="admin@alpha.de", password="wrong-password", **kwargs):
    return await client.post("/api/auth/login", json={"email": email, "password": password}, **kwargs)


def _public_response(response):
    return response.status_code, response.content, response.headers.raw


@pytest.mark.asyncio
async def test_repeated_wrong_passwords_lock_out_even_a_correct_password(client_unauth, seed_data):
    # No service imports: this must fail against an unthrottled login.
    from app.core.deployment_policy import get_deployment_policy

    count = 5 if get_deployment_policy().rate_limit_profile == "strict" else 10
    for _ in range(count):
        assert (await _login(client_unauth)).status_code == 401
    assert (await _login(client_unauth, password="testpass1234")).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,ip_limit,email_limit,lock_minutes", [("selfhost", 20, 10, 15), ("hosted", 10, 5, 30)])
async def test_profile_limits_email_and_ip_independently(client_unauth, seed_data, monkeypatch, throttle_clock, mode, ip_limit, email_limit, lock_minutes):
    from app.core.config import Settings
    from app.core.rate_limit import key_hash
    from tests.conftest import MIGRATOR_SYNC_URL

    monkeypatch.setenv("HV_DEPLOYMENT_MODE", mode)
    monkeypatch.setattr(settings, "deployment_mode", Settings(_env_file=None).deployment_mode)
    for index in range(email_limit):
        # Distinct direct peers prevent the IP threshold from masking email limits.
        from app.main import app
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(f"192.0.2.{index + 1}", 123)), base_url="http://test") as client:
            assert (await _login(client, email=" ADMIN@ALPHA.DE ")).status_code == 401
    assert (await _login(client_unauth, password="testpass1234")).status_code == 401
    throttle_clock.advance(lock_minutes)
    assert (await _login(client_unauth, password="testpass1234")).status_code == 200

    for index in range(ip_limit):
        assert (await _login(client_unauth, email=f"unknown-{index}@example.test")).status_code == 401
    assert (await _login(client_unauth, email="other@example.test")).status_code == 401
    assert (await _login(client_unauth, password="testpass1234")).status_code == 401
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT failures,locked_until FROM auth_throttle WHERE key_kind='ip' AND key_hash=%s", (key_hash("login", "ip", "127.0.0.1"),))
        failures, until = cursor.fetchone()
        assert failures == ip_limit + 2
        assert until == throttle_clock.now + __import__("datetime").timedelta(minutes=lock_minutes)
    throttle_clock.advance(lock_minutes)
    assert (await _login(client_unauth, password="testpass1234")).status_code == 200


@pytest.mark.asyncio
async def test_success_without_failures_does_not_start_the_ip_failure_window(client_unauth, seed_data, monkeypatch, throttle_clock):
    from app.api import auth as auth_api
    from app.core.deployment_policy import get_deployment_policy

    monkeypatch.setattr(auth_api, "verify_password", lambda password, _: password == "testpass1234")
    rules = get_deployment_policy().rate_limit_rules
    assert (await _login(client_unauth, password="testpass1234")).status_code == 200
    throttle_clock.advance(rules.window_minutes - 1)
    assert (await _login(client_unauth, email="first-failure@example.test")).status_code == 401
    throttle_clock.advance(1)
    for index in range(rules.ip_failures - 1):
        assert (await _login(client_unauth, email=f"missing-{index}@example.test")).status_code == 401
    assert (await _login(client_unauth, password="testpass1234")).status_code == 401


@pytest.mark.asyncio
async def test_success_resets_only_email_and_failure_window_expires(client_unauth, seed_data, throttle_clock):
    from app.core.deployment_policy import get_deployment_policy
    from tests.conftest import MIGRATOR_SYNC_URL

    rules = get_deployment_policy().rate_limit_rules
    for _ in range(rules.email_failures - 1):
        await _login(client_unauth)
    throttle_clock.advance(rules.window_minutes)
    await _login(client_unauth)
    assert (await _login(client_unauth, password="testpass1234")).status_code == 200
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT key_kind,failures FROM auth_throttle")
        assert cursor.fetchall() == [("ip", 1)]
    for _ in range(rules.email_failures - 1):
        await _login(client_unauth)
    assert (await _login(client_unauth, password="testpass1234")).status_code == 200


@pytest.mark.asyncio
async def test_lockout_survives_new_app_and_session_and_escalates_to_cap(client_unauth, seed_data, monkeypatch, throttle_clock):
    from datetime import timedelta
    from fastapi import FastAPI
    from app.api.auth import router
    from app.core.rate_limit import key_hash
    from tests.conftest import MIGRATOR_SYNC_URL

    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    for minutes in (30, 60, 120, 240, 480, 960, 1440, 1440):
        for _ in range(5):
            await _login(client_unauth)
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT locked_until FROM auth_throttle WHERE key_kind='email' AND key_hash=%s", (key_hash("login", "email", "admin@alpha.de"),))
            assert cursor.fetchone() == (throttle_clock.now + timedelta(minutes=minutes),)
        fresh_app = FastAPI()
        fresh_app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fresh_app), base_url="http://test") as restarted:
            assert (await _login(restarted, password="testpass1234")).status_code == 401
        throttle_clock.advance(minutes)
    assert (await _login(client_unauth, password="testpass1234")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("trusted", [False, True])
async def test_forwarded_ip_is_used_only_by_trusted_proxy_middleware(seed_data, trusted):
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    from app.main import app
    from app.core.rate_limit import key_hash
    from tests.conftest import MIGRATOR_SYNC_URL

    wrapped = ProxyHeadersMiddleware(app, trusted_hosts=["192.0.2.1"] if trusted else [])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=wrapped, client=("192.0.2.1", 123)), base_url="http://test") as client:
        await _login(client, headers={"X-Forwarded-For": "203.0.113.5, 192.0.2.1"})
    expected_ip = "203.0.113.5" if trusted else "192.0.2.1"
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT key_hash FROM auth_throttle WHERE key_kind='ip'")
        assert bytes(cursor.fetchone()[0]) == key_hash("login", "ip", expected_ip)


@pytest.mark.asyncio
async def test_locked_unknown_and_wrong_password_responses_are_byte_identical(client_unauth, seed_data, monkeypatch, throttle_clock, caplog):
    from app.api import auth as auth_api
    from app.core.deployment_policy import get_deployment_policy
    from app.core.rate_limit import key_hash, logger

    # Alembic fileConfig disables loggers imported before migration rehearsals.
    monkeypatch.setattr(logger, "disabled", False)
    # Hold correlation metadata fixed while comparing credential outcomes.
    headers = {"X-Request-ID": "credential-failure-equivalence"}
    hashes = []
    original = auth_api.verify_password

    def spy(password, hashed):
        hashes.append(hashed)
        return original(password, hashed)

    monkeypatch.setattr(auth_api, "verify_password", spy)
    unknown = await _login(client_unauth, email="absent@example.test", headers=headers)
    wrong = await _login(client_unauth, headers=headers)
    assert _public_response(unknown) == _public_response(wrong)
    assert len(hashes) == 2
    assert hashes[0] == auth_api._DUMMY_PASSWORD_HASH
    assert hashes[1] != auth_api._DUMMY_PASSWORD_HASH
    for _ in range(get_deployment_policy().rate_limit_rules.email_failures - 1):
        await _login(client_unauth)
    locked = await _login(client_unauth, password="testpass1234", headers=headers)
    assert hashes[-1] == auth_api._DUMMY_PASSWORD_HASH
    assert len(hashes) == get_deployment_policy().rate_limit_rules.email_failures + 2
    assert _public_response(locked) == _public_response(wrong)
    assert "retry-after" not in locked.headers and "set-cookie" not in locked.headers
    message = next(record.getMessage() for record in caplog.records if record.name == "app.core.rate_limit")
    assert key_hash("login", "email", "admin@alpha.de").hex() in message
    assert "request_id=" in message and "admin@alpha.de" not in message and "127.0.0.1" not in message


@pytest.mark.asyncio
async def test_cleanup_removes_only_stale_rows_and_storage_contains_only_hashes(client_unauth, monkeypatch, throttle_clock):
    from app.core import rate_limit
    from tests.conftest import MIGRATOR_SYNC_URL

    await _login(client_unauth, email="stale@example.test")
    throttle_clock.advance(24 * 60 + 1)
    monkeypatch.setattr(rate_limit, "cleanup_due", lambda: True)
    await _login(client_unauth, email="  NEW@Example.Test ")
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT key_kind,key_hash,failures FROM auth_throttle ORDER BY key_kind")
        rows = cursor.fetchall()
        import hmac
        pepper = hmac.digest(settings.session_secret.encode(), b"verwalino-auth-throttle", "sha256")
        assert [(kind, bytes(digest), failures) for kind, digest, failures in rows] == [
            ("email", hmac.digest(pepper, b"login:new@example.test", "sha256"), 1),
            ("ip", hmac.digest(pepper, b"login:127.0.0.1", "sha256"), 1),
        ]
        cursor.execute("SELECT row_to_json(t)::text FROM auth_throttle t")
        stored = str(cursor.fetchall())
        assert "new@example.test" not in stored and "127.0.0.1" not in stored


@pytest.mark.asyncio
async def test_login_throttle_locks_and_key_forms_are_canonical(app_rw_session):
    from app.core.rate_limit import check_and_record, key_hash
    from app.core.deployment_policy import get_deployment_policy

    rules = get_deployment_policy().rate_limit_rules
    keys = {"email": " Scope@Example.Test ", "ip": "2001:0db8:0:0:0:0:0:1"}
    for _ in range(rules.email_failures):
        attempt = await check_and_record(app_rw_session, "login", keys, rules)
        await attempt.record_failure()
    locked = await check_and_record(app_rw_session, "login", keys, rules)
    assert locked.allowed is False
    assert key_hash("login", "ip", keys["ip"]) == key_hash("login", "ip", "2001:db8::1")
    assert key_hash("login", "email", keys["email"]) == key_hash("login", "email", "scope@example.test")


@pytest.mark.asyncio
async def test_throttle_table_has_only_narrow_runtime_grants(migrator_session):
    from sqlalchemy import text

    grants = (await migrator_session.execute(text("""SELECT grantee,privilege_type,is_grantable
        FROM information_schema.table_privileges WHERE table_schema='public'
        AND table_name='auth_throttle' AND grantee<>'app_migrator' ORDER BY grantee,privilege_type"""))).all()
    assert grants == [("app_rw", command, "NO") for command in ("DELETE", "INSERT", "SELECT", "UPDATE")]
    assert not await migrator_session.scalar(text("SELECT has_table_privilege('app_review','auth_throttle','SELECT')"))


def test_throttle_migration_roundtrip_preserves_schema_and_refuses_retained_state(run_migrations):
    from alembic.operations import Operations
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import MIGRATOR_SYNC_URL

    migration = ScriptDirectory("migrations").get_revision("0142").module  # accepted source under test
    signup_migration = ScriptDirectory("migrations").get_revision("0143").module
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            connection.execute(text("DELETE FROM auth_throttle"))
            connection.execute(text("DELETE FROM signup_request"))
            def snapshot():
                return connection.execute(text("""SELECT column_name,data_type,is_nullable,column_default
                    FROM information_schema.columns WHERE table_name='auth_throttle' ORDER BY ordinal_position""")).all()
            with Operations.context(MigrationContext.configure(connection)):
                signup_migration.downgrade()
                before = snapshot()
                migration.downgrade()
                assert connection.scalar(text("SELECT to_regclass('public.auth_throttle')")) is None
                migration.upgrade()
                assert snapshot() == before
                connection.execute(text("""INSERT INTO auth_throttle(key_kind,key_hash,window_start,failures,updated_at)
                    VALUES ('email',sha256('synthetic'::bytea),now(),1,now())"""))
                with pytest.raises(DBAPIError, match="Cannot discard retained auth throttle state"):
                    with connection.begin_nested():
                        migration.downgrade()
                assert connection.scalar(text("SELECT count(*) FROM auth_throttle")) == 1
            transaction.rollback()
    finally:
        engine.dispose()


@pytest.mark.parametrize("mode", ["selfhost", "hosted"])
@pytest.mark.parametrize("still_at_limit", [False, True])
def test_success_refund_retracts_only_a_provisional_ip_lock(mode, still_at_limit):
    from datetime import datetime, timedelta, timezone
    from app.core.config import DeploymentMode
    from app.core.deployment_policy import DeploymentPolicy
    from app.core.rate_limit import _refund_ip_reservation

    rules = DeploymentPolicy.for_mode(DeploymentMode(mode)).rate_limit_rules
    now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    reserved = {"key_kind": "ip", "failures": 1, "window_start": now,
                "locked_until": None, "lockouts": 1,
                "last_lockout_at": now - timedelta(minutes=rules.lockout_minutes)}
    current = {**reserved, "failures": rules.ip_failures + int(still_at_limit),
               "locked_until": now + timedelta(minutes=rules.lockout_minutes),
               "lockouts": 2 if mode == "hosted" else 1, "last_lockout_at": now}
    before = dict(current)
    _refund_ip_reservation(current, reserved, rules)
    assert current["failures"] == before["failures"] - 1
    if still_at_limit:
        assert current["locked_until"] == before["locked_until"]
        assert current["lockouts"] == before["lockouts"]
        assert current["last_lockout_at"] == before["last_lockout_at"]
    else:
        assert current["locked_until"] is None
        assert current["lockouts"] == reserved["lockouts"]
        assert current["last_lockout_at"] == reserved["last_lockout_at"]


@pytest.mark.parametrize("mode", ["selfhost", "hosted"])
@pytest.mark.parametrize("kind", ["email", "ip"])
@pytest.mark.parametrize("expired_lock", [False, True])
def test_failure_finalization_starts_a_fresh_window_after_expiry(mode, kind, expired_lock):
    from datetime import datetime, timedelta, timezone
    from app.core.config import DeploymentMode
    from app.core.deployment_policy import DeploymentPolicy
    from app.core.rate_limit import _finalize_failure

    rules = DeploymentPolicy.for_mode(DeploymentMode(mode)).rate_limit_rules
    now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    limit = rules.email_failures if kind == "email" else rules.ip_failures
    row = {"key_kind": kind, "failures": limit + 1,
           "window_start": now - timedelta(minutes=rules.window_minutes),
           "locked_until": now - timedelta(milliseconds=1) if expired_lock else None,
           "lockouts": 1 if expired_lock else 0,
           "last_lockout_at": now - timedelta(minutes=rules.lockout_minutes, milliseconds=1) if expired_lock else None}
    assert _finalize_failure(row, rules, now) is False
    assert row["failures"] == 1
    assert row["window_start"] == now
    assert row["locked_until"] is None


@pytest.mark.parametrize("mode", ["selfhost", "hosted"])
@pytest.mark.parametrize("kind", ["email", "ip"])
@pytest.mark.parametrize("over_limit", [False, True])
def test_admission_refuses_reservations_above_each_key_limit(mode, kind, over_limit):
    from datetime import datetime, timezone
    from app.core.config import DeploymentMode
    from app.core.deployment_policy import DeploymentPolicy
    from app.core.rate_limit import ThrottleAttempt

    rules = DeploymentPolicy.for_mode(DeploymentMode(mode)).rate_limit_rules
    limit = rules.email_failures if kind == "email" else rules.ip_failures
    attempt = ThrottleAttempt(None, "login", [
        {"key_kind": kind, "failures": limit + int(over_limit), "locked_until": None},
    ], rules, datetime.now(timezone.utc), "boundary-test")
    assert attempt.allowed is not over_limit


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["selfhost", "hosted"])
async def test_concurrent_burst_never_checks_more_real_hashes_than_the_email_limit(seed_data, monkeypatch, throttle_clock, mode):
    import asyncio
    from app.api import auth as auth_api
    from app.main import app
    from app.core.deployment_policy import get_deployment_policy

    monkeypatch.setattr(settings, "deployment_mode", mode)
    original = auth_api.verify_password
    real_hash_checks = []

    def verify(password, password_hash):
        if password_hash != auth_api._DUMMY_PASSWORD_HASH:
            real_hash_checks.append(password)
        return original(password, password_hash)

    monkeypatch.setattr(auth_api, "verify_password", verify)
    limit = get_deployment_policy().rate_limit_rules.email_failures
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        responses = await asyncio.gather(*(
            _login(client, password=f"guess-{index}-xxxxxxxx") for index in range(4 * limit)
        ))
    assert all((response.status_code, response.json()) == INVALID_CREDENTIALS for response in responses)
    assert all("retry-after" not in response.headers and "set-cookie" not in response.headers for response in responses)
    assert 0 < len(real_hash_checks) <= limit


@pytest.mark.asyncio
async def test_concurrent_failures_do_not_lose_counts_or_bypass_lockout(seed_data, monkeypatch):
    import asyncio
    from app.api import auth as auth_api
    from app.main import app
    from app.core.deployment_policy import get_deployment_policy
    from tests.conftest import MIGRATOR_SYNC_URL

    # Keep concurrency about the real database serialization, not Argon2 cost.
    monkeypatch.setattr(auth_api, "verify_password", lambda *_: False)
    count = get_deployment_policy().rate_limit_rules.email_failures + 2
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        responses = await asyncio.gather(*(_login(client) for _ in range(count)))
    assert all(response.status_code == 401 for response in responses)
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT failures,locked_until IS NOT NULL FROM auth_throttle WHERE key_kind='email'")
        assert cursor.fetchone() == (count, True)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["selfhost", "hosted"])
async def test_interleaved_successes_cannot_bypass_ip_limit(client_unauth, seed_data, monkeypatch, throttle_clock, mode):
    from app.api import auth as auth_api
    from app.core.deployment_policy import get_deployment_policy
    from tests.conftest import MIGRATOR_SYNC_URL

    monkeypatch.setattr(settings, "deployment_mode", mode)
    monkeypatch.setattr(auth_api, "verify_password", lambda password, _: password == "testpass1234")
    limit = get_deployment_policy().rate_limit_rules.ip_failures
    for index in range(45):
        assert (await _login(client_unauth, email=f"missing-{index}@example.test")).status_code == 401
        success = await _login(client_unauth, password="testpass1234")
        assert success.status_code == (200 if index + 1 < limit else 401)
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT failures,locked_until FROM auth_throttle WHERE key_kind='ip'")
        failures, locked_until = cursor.fetchone()
        assert failures == 45 + (46 - limit)
        assert locked_until > throttle_clock.now


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,decay", [("selfhost", 15), ("hosted", 1440)])
@pytest.mark.parametrize("past_boundary", [False, True])
async def test_escalation_decays_by_last_lockout_even_with_recent_traffic(client_unauth, monkeypatch, throttle_clock, mode, decay, past_boundary):
    from app.api import auth as auth_api
    from app.core.deployment_policy import get_deployment_policy
    from tests.conftest import MIGRATOR_SYNC_URL

    monkeypatch.setattr(settings, "deployment_mode", mode)
    monkeypatch.setattr(auth_api, "verify_password", lambda *_: False)
    rules = get_deployment_policy().rate_limit_rules
    for _ in range(rules.email_failures):
        await _login(client_unauth, email="decay@example.test")
    throttle_clock.advance(decay + (1 / 60 if past_boundary else 0))
    # Traffic keeps updated_at fresh; cleanup must not determine escalation.
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE auth_throttle SET updated_at=%s", (throttle_clock.now,))
    await _login(client_unauth, email="decay@example.test")
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT lockouts FROM auth_throttle WHERE key_kind='email'")
        assert cursor.fetchone() == (0 if past_boundary else 1,)
    for _ in range(rules.email_failures - 1):
        await _login(client_unauth, email="decay@example.test")
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT lockouts FROM auth_throttle WHERE key_kind='email'")
        expected = 1 if past_boundary or mode == "selfhost" else 2
        assert cursor.fetchone() == (expected,)
        cursor.execute("SELECT last_lockout_at FROM auth_throttle WHERE key_kind='email'")
        assert cursor.fetchone() == (throttle_clock.now,)


@pytest.mark.asyncio
@pytest.mark.parametrize("peer,forwarded", [("testclient", None), ("192.0.2.1", "unknown")])
async def test_invalid_client_address_returns_normal_failure(seed_data, peer, forwarded):
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    from app.main import app
    from app.core.rate_limit import key_hash
    from tests.conftest import MIGRATOR_SYNC_URL

    wrapped = ProxyHeadersMiddleware(app, trusted_hosts=["192.0.2.1"])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=wrapped, client=(peer, 123), raise_app_exceptions=False), base_url="http://test") as client:
        response = await _login(client, headers={"X-Forwarded-For": forwarded} if forwarded else {})
    assert response.status_code == 401
    assert (response.status_code, response.json()) == INVALID_CREDENTIALS
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT key_hash FROM auth_throttle WHERE key_kind='ip'")
        assert bytes(cursor.fetchone()[0]) == key_hash("login", "ip", "invalid")


def test_ipv6_keys_share_a_64_bit_network_and_mapped_ipv4_uses_ipv4():
    from app.core.rate_limit import key_hash

    assert key_hash("login", "ip", "2001:db8:1234:5678::1") == key_hash("login", "ip", "2001:db8:1234:5678:ffff::2")
    assert key_hash("login", "ip", "2001:db8:1234:5678::1") != key_hash("login", "ip", "2001:db8:1234:5679::1")
    assert key_hash("login", "ip", "::ffff:192.0.2.1") == key_hash("login", "ip", "192.0.2.1")


def test_rotating_session_secret_changes_throttle_keys(monkeypatch):
    from app.core.rate_limit import key_hash

    before = key_hash("login", "email", "user@example.test")
    monkeypatch.setattr(settings, "session_secret", "synthetic-rotated-secret")
    assert key_hash("login", "email", "user@example.test") != before


@pytest.mark.asyncio
async def test_hasher_runs_outside_transactions_for_all_failure_paths(client_unauth, seed_data, monkeypatch):
    from app.api import auth as auth_api
    from app.core.deployment_policy import get_deployment_policy

    factory = auth_api.get_session_factory()
    sessions = []
    hashes = []

    def tracked_session():
        session = factory()
        sessions.append(session)
        return session

    def verify(_, hashed):
        assert sessions and all(not session.in_transaction() for session in sessions)
        hashes.append(hashed)
        return False

    monkeypatch.setattr(auth_api, "get_session_factory", lambda: tracked_session)
    monkeypatch.setattr(auth_api, "verify_password", verify)
    await _login(client_unauth, email="absent@example.test")
    limit = get_deployment_policy().rate_limit_rules.email_failures
    for _ in range(limit + 1):
        await _login(client_unauth)
    assert len(hashes) == limit + 2
    assert hashes[0] == hashes[-1] == auth_api._DUMMY_PASSWORD_HASH
    assert hashes[1] != auth_api._DUMMY_PASSWORD_HASH


@pytest.mark.asyncio
async def test_both_attempt_reservations_are_committed_before_hashing(client_unauth, seed_data, monkeypatch):
    from app.api import auth as auth_api
    from tests.conftest import MIGRATOR_SYNC_URL

    def verify(*_):
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT key_kind,failures FROM auth_throttle ORDER BY key_kind")
            assert cursor.fetchall() == [("email", 1), ("ip", 1)]
        return False

    monkeypatch.setattr(auth_api, "verify_password", verify)
    assert (await _login(client_unauth)).status_code == 401


@pytest.mark.asyncio
async def test_cleanup_is_bounded_separate_and_best_effort(client_unauth, monkeypatch, throttle_clock):
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.api import auth as auth_api
    from app.core import rate_limit
    from app.core.deployment_policy import get_deployment_policy
    from tests.conftest import MIGRATOR_SYNC_URL

    rules = get_deployment_policy().rate_limit_rules
    monkeypatch.setattr(rate_limit, "cleanup_due", lambda: True)
    monkeypatch.setattr(auth_api, "verify_password", lambda *_: False)
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("""INSERT INTO auth_throttle(key_kind,key_hash,window_start,updated_at)
            SELECT 'email',sha256(i::text::bytea),%s,%s FROM generate_series(1,%s) i""",
            (throttle_clock.now, throttle_clock.now - __import__("datetime").timedelta(days=2), rules.cleanup_batch_size + 7))
    original = AsyncSession.execute
    cleanup_sessions = []

    async def spy(session, statement, params=None, **kwargs):
        if "DELETE FROM public.auth_throttle" in str(statement) and "cutoff" in str(statement):
            cleanup_sessions.append(session)
            # A different connection sees the committed login failure already.
            with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
                cursor.execute("SELECT failures FROM auth_throttle WHERE key_kind='ip'")
                assert cursor.fetchone() == (1,)
        return await original(session, statement, params, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", spy)
    assert (await _login(client_unauth)).status_code == 401
    assert len(cleanup_sessions) == 1
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM auth_throttle WHERE updated_at < %s", (throttle_clock.now,))
        assert cursor.fetchone() == (7,)

    async def fail_cleanup(session, statement, params=None, **kwargs):
        if "DELETE FROM public.auth_throttle" in str(statement) and "cutoff" in str(statement):
            raise OperationalError("synthetic cleanup failure", {}, None)
        return await original(session, statement, params, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", fail_cleanup)
    assert (await _login(client_unauth)).status_code == 401
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT failures FROM auth_throttle WHERE key_kind='ip'")
        assert cursor.fetchone() == (2,)


def test_cleanup_cadence_comes_from_rules(monkeypatch):
    import itertools
    from app.core import rate_limit
    from app.core.deployment_policy import get_deployment_policy

    rules = get_deployment_policy().rate_limit_rules
    monkeypatch.setattr(rate_limit, "_attempts", itertools.count(1))
    assert [rate_limit.cleanup_due() for _ in range(rules.cleanup_every)] == [False] * (rules.cleanup_every - 1) + [True]
