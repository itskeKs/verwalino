"""Fail-closed validation for the disposable declaration test database."""

from __future__ import annotations

import os
from urllib.parse import urlparse


_EXPECTED_SCHEMES = {
    "HV_DATABASE_URL": "postgresql+asyncpg",
    "HV_DATABASE_URL_MIGRATOR": "postgresql+asyncpg",
    "HV_DATABASE_URL_SYNC": "postgresql",
}


def _refusal_message(env_name: str) -> str:
    return (
        f"Refusing destructive tests: {env_name} is not the expected "
        "localhost:55432 declaration database"
    )


def require_disposable_database_url(
    env_name: str,
    expected_username: str,
) -> str:
    """Return a raw URL only when it names the disposable test database."""
    value = os.environ.get(env_name)
    if value is None:
        raise RuntimeError(
            f"{env_name} must target the disposable declaration test database"
        )

    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise RuntimeError(_refusal_message(env_name)) from error

    if (
        parsed.scheme != _EXPECTED_SCHEMES[env_name]
        or parsed.hostname not in {"localhost", "127.0.0.1"}
        or port != 55432
        or parsed.path != "/hausverwaltung"
        or parsed.username != expected_username
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(_refusal_message(env_name))
    return value


def require_disposable_database_settings(settings: object) -> tuple[str, str, str]:
    """Validate the raw variables and the Settings values used by connections."""
    migrator_url = require_disposable_database_url(
        "HV_DATABASE_URL_MIGRATOR", "app_migrator"
    )
    app_rw_url = require_disposable_database_url("HV_DATABASE_URL", "app_rw")
    migrator_sync_url = require_disposable_database_url(
        "HV_DATABASE_URL_SYNC", "app_migrator"
    )
    expected_values = {
        "HV_DATABASE_URL": (app_rw_url, "database_url"),
        "HV_DATABASE_URL_MIGRATOR": (migrator_url, "database_url_migrator"),
        "HV_DATABASE_URL_SYNC": (migrator_sync_url, "database_url_sync"),
    }
    for env_name, (raw_value, settings_attribute) in expected_values.items():
        if getattr(settings, settings_attribute) != raw_value:
            raise RuntimeError(
                "Refusing destructive tests: effective database configuration "
                f"for {env_name} does not match its required disposable URL"
            )
    return migrator_url, app_rw_url, migrator_sync_url


def is_disposable_database_candidate(value: str | None) -> bool:
    """Identify the direct Alembic path that must use test-only validation."""
    if value is None:
        return False
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError:
        return False
    return parsed.hostname in {"localhost", "127.0.0.1"} and port == 55432
