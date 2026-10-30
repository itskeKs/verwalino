"""Safety guards and recovery for the single shared disposable test database."""

from contextlib import closing
import os
from unittest.mock import MagicMock

import psycopg2
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core import declaration_test_database as testdb
from app.core.config import settings
from tests.conftest import MIGRATOR_SYNC_URL, migrate_test_database
from tests import conftest as harness


@pytest.fixture(scope="session")
def run_migrations():
    """Unit tests must not invoke the suite's database setup."""


@pytest.fixture
def inject_test_engine():
    """Only the integration tests below explicitly open database connections."""


@pytest.fixture
def guarded_environment(monkeypatch):
    monkeypatch.setenv("HV_DECLARATION_TEST_DATABASE_PROFILE", "local")
    monkeypatch.setenv("VERWALINO_TEST_DB", "1")
    for name, attribute, role in (
        ("HV_DATABASE_URL", "database_url", "app_rw:app_secret"),
        ("HV_DATABASE_URL_MIGRATOR", "database_url_migrator", "app_migrator:migrator_secret"),
        ("HV_DATABASE_URL_SYNC", "database_url_sync", "app_migrator:migrator_secret"),
    ):
        scheme = "postgresql" if name.endswith("_SYNC") else "postgresql+asyncpg"
        url = f"{scheme}://{role}@127.0.0.1:55432/hausverwaltung"
        monkeypatch.setenv(name, url)
        monkeypatch.setattr(settings, attribute, url)

    def unexpected_connection(*args, **kwargs):
        pytest.fail("database connection attempted before reset authorization")

    monkeypatch.setattr(psycopg2, "connect", unexpected_connection)
    return settings.database_url_sync


@pytest.mark.parametrize("replacement", [
    ("55432", "5432"),
    ("127.0.0.1", "production.example"),
    ("/hausverwaltung", "/production"),
    ("app_migrator:", "app_rw:"),
    ("migrator_secret", "different_password"),
    ("55432", "invalid"),
    ("/hausverwaltung", "/hausverwaltung?host=production.example"),
])
def test_reset_refuses_other_database_urls_without_connecting(guarded_environment, replacement):
    url = guarded_environment.replace(*replacement)
    with pytest.raises(RuntimeError, match="Refusing"):
        testdb.reset_foreign_test_database(url)


@pytest.mark.parametrize("flag", [None, "0", "true"])
def test_reset_requires_explicit_environment_opt_in(guarded_environment, monkeypatch, flag):
    if flag is None:
        monkeypatch.delenv("VERWALINO_TEST_DB")
    else:
        monkeypatch.setenv("VERWALINO_TEST_DB", flag)
    with pytest.raises(RuntimeError, match="VERWALINO_TEST_DB=1"):
        testdb.reset_foreign_test_database(guarded_environment)


@pytest.mark.parametrize("attribute", ["database_url", "database_url_migrator", "database_url_sync"])
def test_reset_revalidates_effective_settings_without_connecting(guarded_environment, monkeypatch, attribute):
    monkeypatch.setattr(settings, attribute, getattr(settings, attribute).replace("55432", "5432"))
    with pytest.raises(RuntimeError, match="effective database configuration"):
        testdb.reset_foreign_test_database(guarded_environment)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_reset_guard_accepts_only_the_validated_test_url_without_connecting(guarded_environment, monkeypatch, host):
    for name, attribute in (
        ("HV_DATABASE_URL", "database_url"),
        ("HV_DATABASE_URL_MIGRATOR", "database_url_migrator"),
        ("HV_DATABASE_URL_SYNC", "database_url_sync"),
    ):
        url = getattr(settings, attribute).replace("127.0.0.1", host)
        monkeypatch.setenv(name, url)
        monkeypatch.setattr(settings, attribute, url)
    assert testdb.require_disposable_database_reset_url(settings.database_url_sync) == settings.database_url_sync


def test_reset_refuses_a_validated_ci_database_without_connecting(guarded_environment, monkeypatch):
    monkeypatch.setenv("HV_DECLARATION_TEST_DATABASE_PROFILE", "gitlab-ci")
    monkeypatch.setenv("CI", "true")
    for name, attribute in (
        ("HV_DATABASE_URL", "database_url"),
        ("HV_DATABASE_URL_MIGRATOR", "database_url_migrator"),
        ("HV_DATABASE_URL_SYNC", "database_url_sync"),
    ):
        url = getattr(settings, attribute).replace("127.0.0.1:55432", "postgres:5432")
        monkeypatch.setenv(name, url)
        monkeypatch.setattr(settings, attribute, url)
    with pytest.raises(RuntimeError, match="localhost:55432/hausverwaltung"):
        testdb.reset_foreign_test_database(settings.database_url_sync)


@pytest.mark.parametrize("resolution", ["raises", "none"])
def test_foreign_revision_is_checked_before_branch_specific_sql(guarded_environment, monkeypatch, resolution, request):
    from alembic.util import CommandError

    monkeypatch.setattr(harness, "MIGRATOR_SYNC_URL", guarded_environment)
    connection = MagicMock()
    connection.__enter__.return_value = connection
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = ("alembic_version",)
    cursor.fetchall.return_value = [("zzzz",)]

    def only_revision_queries(statement):
        assert statement in (
            "SELECT to_regclass('public.alembic_version')",
            "SELECT version_num FROM public.alembic_version",
        ), "branch-specific SQL attempted before foreign-head recovery"

    cursor.execute.side_effect = only_revision_queries
    monkeypatch.setattr(psycopg2, "connect", lambda url: connection)

    def resolve_revision(self, revision):
        assert revision == "zzzz"
        if resolution == "raises":
            raise CommandError("unknown revision")
        return None

    monkeypatch.setattr(ScriptDirectory, "get_revision", resolve_revision)

    class ResetRequested(Exception):
        pass

    def stop_after_reset(url):
        assert url == guarded_environment
        raise ResetRequested

    monkeypatch.setattr(harness, "reset_foreign_test_database", stop_after_reset, raising=False)
    with pytest.raises(ResetRequested):
        migrate_test_database(request)
    connection.close.assert_called_once()


@pytest.fixture(scope="module")
def migration_config(request):
    config = Config("alembic.ini")
    config.attributes["hv_declaration_test_database"] = True
    config.set_main_option("sqlalchemy.url", MIGRATOR_SYNC_URL)
    migrate_test_database(request)
    return config


def database_revisions():
    with closing(psycopg2.connect(MIGRATOR_SYNC_URL)) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT version_num FROM public.alembic_version")
        return tuple(row[0] for row in cursor.fetchall())


@pytest.mark.skipif(
    os.environ.get("HV_DECLARATION_TEST_DATABASE_PROFILE", "local") != "local",
    reason="foreign-head reset integration requires the local test DB on port 55432",
)
def test_foreign_revision_is_reset_and_upgraded_to_checkout_head(migration_config, monkeypatch, request):
    """Run only while holding /tmp/verwalino-testdb.lock, like every DB test."""
    head = ScriptDirectory.from_config(migration_config).get_current_head()
    assert database_revisions() == (head,)
    with closing(psycopg2.connect(MIGRATOR_SYNC_URL)) as connection, connection, connection.cursor() as cursor:
        cursor.execute("CREATE TABLE public.foreign_branch_sentinel (id integer)")
        cursor.execute("UPDATE public.alembic_version SET version_num='zzzz'")
        cursor.execute("SELECT oid, rolname, rolconfig FROM pg_roles ORDER BY oid")
        roles_before = cursor.fetchall()

    downgrade = command.downgrade
    downgrade_calls = []

    def track_downgrade(config, revision):
        downgrade_calls.append(revision)
        return downgrade(config, revision)

    monkeypatch.setattr(command, "downgrade", track_downgrade)
    terminal_writer = request.config.get_terminal_writer()
    reset_notice = MagicMock(wraps=terminal_writer.line)
    monkeypatch.setattr(terminal_writer, "line", reset_notice)
    try:
        migrate_test_database(request)
        assert database_revisions() == (head,)
        assert downgrade_calls == []
        reset_notice.assert_called_once_with(
            "test database held foreign migration head zzzz; reset to empty before upgrading"
        )
        with closing(psycopg2.connect(MIGRATOR_SYNC_URL)) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.foreign_branch_sentinel')")
            assert cursor.fetchone() == (None,)
            for role in ("app_rw", "app_worker", "app_review"):
                cursor.execute("SELECT has_schema_privilege(%s, 'public', 'USAGE'), "
                               "has_schema_privilege(%s, 'public', 'CREATE')", (role, role))
                assert cursor.fetchone() == (True, False)
            cursor.execute("SELECT oid, rolname, rolconfig FROM pg_roles ORDER BY oid")
            assert cursor.fetchall() == roles_before
    finally:
        # Before recovery exists, only the fake version was changed. Restore that
        # marker if it remains; after a reset, upgrade any real partial revision.
        with closing(psycopg2.connect(MIGRATOR_SYNC_URL)) as connection, connection, connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.alembic_version')")
            if cursor.fetchone()[0] is not None:
                cursor.execute("UPDATE public.alembic_version SET version_num=%s WHERE version_num='zzzz'", (head,))
            cursor.execute("DROP TABLE IF EXISTS public.foreign_branch_sentinel")
        command.upgrade(migration_config, "head")
        assert database_revisions() == (head,)


def test_known_revision_still_downgrades_to_base(migration_config, monkeypatch, request):
    assert database_revisions() == (ScriptDirectory.from_config(migration_config).get_current_head(),)
    downgrade = command.downgrade
    calls = []

    def track_downgrade(config, revision):
        calls.append(revision)
        return downgrade(config, revision)

    monkeypatch.setattr(command, "downgrade", track_downgrade)
    terminal_writer = request.config.get_terminal_writer()
    reset_notice = MagicMock(wraps=terminal_writer.line)
    monkeypatch.setattr(terminal_writer, "line", reset_notice)
    migrate_test_database(request)
    assert calls == ["base"]
    assert database_revisions() == (ScriptDirectory.from_config(migration_config).get_current_head(),)
    reset_notice.assert_not_called()
