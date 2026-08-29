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
        if 'FROM "user"' in query or 'INSERT INTO "user"' in query
    ]
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
async def test_session_is_invalid_immediately_after_user_deactivation(client_a_admin, seed_data):
    from tests.conftest import MIGRATOR_SYNC_URL

    connection = psycopg2.connect(MIGRATOR_SYNC_URL)
    try:
        with connection.cursor() as cursor:
            cursor.execute('UPDATE public."user" SET is_active = false WHERE id = %s', (str(seed_data["user_a_admin_id"]),))
        connection.commit()
        response = await client_a_admin.get("/api/auth/session")
        assert response.status_code == 401
    finally:
        with connection.cursor() as cursor:
            cursor.execute('UPDATE public."user" SET is_active = true WHERE id = %s', (str(seed_data["user_a_admin_id"]),))
        connection.commit()
        connection.close()


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
