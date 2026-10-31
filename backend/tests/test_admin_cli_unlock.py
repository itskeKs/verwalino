"""Operator unlock uses the migrator and the same normalized, peppered keys."""

import psycopg2
import pytest

from app import admin_cli
from app.core.config import settings
from app.core.rate_limit import key_hash
from tests.conftest import MIGRATOR_SYNC_URL


@pytest.mark.parametrize("kind,value,equivalent", [
    ("email", "  USER@Example.Test ", "user@example.test"),
    ("ip", "2001:db8::1234", "2001:db8::abcd"),
])
def test_unlock_removes_only_the_selected_login_key(monkeypatch, capsys, kind, value, equivalent):
    digest = key_hash("login", kind, equivalent)
    other = key_hash("login", kind, "other@example.test" if kind == "email" else "192.0.2.1")
    signup = key_hash("signup", kind, equivalent)
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        for key in (digest, other, signup):
            cursor.execute("INSERT INTO auth_throttle(key_kind,key_hash,window_start,updated_at) VALUES (%s,%s,now(),now())", (kind, key))
    monkeypatch.setattr("sys.argv", ["admin_cli", "unlock", f"--{kind}", value])
    original = admin_cli.psycopg2.connect
    connected = []

    def connect(url):
        assert url == settings.database_url_sync
        connection = original(url)
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user")
            assert cursor.fetchone() == ("app_migrator",)
        connected.append(connection)
        return connection

    monkeypatch.setattr(admin_cli.psycopg2, "connect", connect)
    admin_cli.main()
    assert connected and "1" in capsys.readouterr().out
    with original(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT key_hash FROM auth_throttle")
        assert {bytes(row[0]) for row in cursor.fetchall()} == {other, signup}
    admin_cli.main()
    assert "0" in capsys.readouterr().out


@pytest.mark.parametrize("arguments", [[], ["--email", "a@example.test", "--ip", "192.0.2.1"], ["--email", "   "]])
def test_unlock_rejects_missing_ambiguous_or_invalid_email_before_connecting(monkeypatch, arguments):
    monkeypatch.setattr("sys.argv", ["admin_cli", "unlock", *arguments])
    monkeypatch.setattr(admin_cli.psycopg2, "connect", lambda *_: pytest.fail("must not connect"))
    with pytest.raises(SystemExit):
        admin_cli.main()
