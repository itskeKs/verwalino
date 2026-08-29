"""Collection-level safety checks for destructive database fixtures."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parent.parent
EXPECTED_USERS = {
    "HV_DATABASE_URL": "app_rw",
    "HV_DATABASE_URL_MIGRATOR": "app_migrator",
    "HV_DATABASE_URL_SYNC": "app_migrator",
}
EXPECTED_PASSWORDS = {
    "HV_DATABASE_URL": "app_secret",
    "HV_DATABASE_URL_MIGRATOR": "migrator_secret",
    "HV_DATABASE_URL_SYNC": "migrator_secret",
}


def _url(
    env_name: str,
    *,
    host: str = "localhost",
    port: int = 55432,
    database: str = "hausverwaltung",
    username: str | None = None,
    password: str | None = None,
    query: str = "",
    fragment: str = "",
) -> str:
    scheme = (
        "postgresql" if env_name == "HV_DATABASE_URL_SYNC" else "postgresql+asyncpg"
    )
    value = (
        f"{scheme}://{username or EXPECTED_USERS[env_name]}:"
        f"{password or EXPECTED_PASSWORDS[env_name]}"
        f"@{host}:{port}/{database}"
    )
    if query:
        value = f"{value}?{query}"
    if fragment:
        value = f"{value}#{fragment}"
    return value


DISPOSABLE_ENV = {env_name: _url(env_name) for env_name in EXPECTED_USERS}


@pytest.fixture(scope="session", autouse=True)
def run_migrations() -> None:
    """Keep this collection-only guard test independent of a running database."""


def _collect_with(overrides: dict[str, str | None]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(DISPOSABLE_ENV)
    for env_name, value in overrides.items():
        if value is None:
            env.pop(env_name, None)
        else:
            env[env_name] = value
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/test_tenant_isolation.py"],
        cwd=BACKEND_DIR,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _alembic_current_with(
    overrides: dict[str, str | None],
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(DISPOSABLE_ENV)
    for env_name, value in overrides.items():
        if value is None:
            env.pop(env_name, None)
        else:
            env[env_name] = value
    return subprocess.run(
        [sys.executable, "-m", "alembic", "current"],
        cwd=BACKEND_DIR,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("env_name", EXPECTED_USERS)
def test_collection_refuses_a_missing_database_url(env_name: str) -> None:
    result = _collect_with({env_name: None})

    assert result.returncode != 0
    assert "disposable declaration test database" in result.stderr
    assert "app_secret" not in result.stderr
    assert "migrator_secret" not in result.stderr


@pytest.mark.parametrize("env_name", EXPECTED_USERS)
@pytest.mark.parametrize(
    "invalid_url",
    [
        lambda env_name: _url(env_name, port=5433),
        lambda env_name: _url(env_name, host="database.example.test"),
        lambda env_name: _url(env_name, username="unexpected_role"),
    ],
)
def test_collection_refuses_non_disposable_database_urls(
    env_name: str,
    invalid_url,
) -> None:
    result = _collect_with({env_name: invalid_url(env_name)})

    assert result.returncode != 0
    assert "Refusing destructive tests" in result.stderr
    assert "app_secret" not in result.stderr
    assert "migrator_secret" not in result.stderr


@pytest.mark.parametrize("env_name", EXPECTED_USERS)
@pytest.mark.parametrize(
    "query",
    ["host=database.example.test", "port=55431", "dbname=unexpected_database"],
)
def test_collection_refuses_database_url_query_overrides(
    env_name: str,
    query: str,
) -> None:
    result = _collect_with({env_name: _url(env_name, query=query)})

    assert result.returncode != 0
    assert "Refusing destructive tests" in result.stderr
    assert "app_secret" not in result.stderr
    assert "migrator_secret" not in result.stderr


@pytest.mark.parametrize("env_name", EXPECTED_USERS)
def test_collection_refuses_database_url_fragments(env_name: str) -> None:
    result = _collect_with({env_name: _url(env_name, fragment="override")})

    assert result.returncode != 0
    assert "Refusing destructive tests" in result.stderr
    assert "app_secret" not in result.stderr
    assert "migrator_secret" not in result.stderr


@pytest.mark.parametrize("env_name", EXPECTED_USERS)
def test_collection_refuses_lowercase_database_url_overrides(env_name: str) -> None:
    result = _collect_with({env_name.lower(): _url(env_name, port=55431)})

    assert result.returncode != 0
    assert "effective database configuration" in result.stderr
    assert "app_secret" not in result.stderr
    assert "migrator_secret" not in result.stderr


def test_direct_alembic_refuses_lowercase_sync_url_override() -> None:
    result = _alembic_current_with(
        {"hv_database_url_sync": _url("HV_DATABASE_URL_SYNC", port=55431)}
    )

    assert result.returncode != 0
    assert "effective database configuration" in result.stderr
    assert "app_secret" not in result.stderr
    assert "migrator_secret" not in result.stderr


def test_direct_alembic_refuses_lowercase_disposable_sync_url_with_query_override() -> None:
    lowercase_sync_url = _url(
        "HV_DATABASE_URL_SYNC", query="host=database.example.test"
    )
    result = _alembic_current_with(
        {
            "HV_DATABASE_URL_SYNC": None,
            "hv_database_url_sync": lowercase_sync_url,
        }
    )

    assert result.returncode != 0
    assert "HV_DATABASE_URL_SYNC must target the disposable declaration test database" in result.stderr
    assert lowercase_sync_url not in result.stderr
    assert lowercase_sync_url not in result.stdout
    assert "app_secret" not in result.stderr
    assert "migrator_secret" not in result.stderr


def test_collection_accepts_all_disposable_database_urls() -> None:
    result = _collect_with({})

    assert result.returncode == 0, result.stderr
