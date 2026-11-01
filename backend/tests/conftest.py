"""Test fixtures for M0 tests.

Provides:
  - A real PostgreSQL test database (via docker-compose).
  - Two Organizations (org_a, org_b) with users and memberships.
  - Authenticated HTTPX test clients for each org.
  - Direct DB sessions (app_rw role) for RLS-level tests.
  - A migrator session for setup (bypasses RLS).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from contextlib import closing
from datetime import date
from functools import cache, wraps
import signal
import threading
import weakref
from typing import AsyncGenerator
from unittest.mock import patch

import pytest
import pytest_asyncio
import httpx
import psycopg2
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import async_session
from sqlalchemy.util.concurrency import greenlet_spawn
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Never initialize an outbound tracker from a developer's environment or .env.
# Tracker tests explicitly install their own offline transport after import.
os.environ["HV_ERROR_TRACKING_DSN"] = ""

from app.core.security import hash_password, create_session_token
from app.core.config import settings
from app.core.declaration_test_database import (
    require_disposable_database_settings,
    reset_foreign_test_database,
)
from app.core.db import override_engine, override_worker_engine, override_review_engine
from app.core.locking import is_lock_timeout
from app.main import app
from tests.bdd.world import admin_credentials, target_url

# Approval graph fixtures are shared by API and knowledge-search tests. Keep
# their registration at the suite level so it does not depend on pytest's
# collection order between test modules.
pytest_plugins = (
    "tests.bdd.report",
    "tests.test_declaration_approval_new_property",
    "tests.test_declaration_approval_existing_property",
)

# ---------------------------------------------------------------------------
# Database URLs (must target the disposable declaration test database)
# ---------------------------------------------------------------------------
# External BDD runs override both database autouse fixtures. They need no local
# database configuration; all local test paths retain the disposable-DB guard.
if target_url():
    MIGRATOR_URL = APP_RW_URL = MIGRATOR_SYNC_URL = None
else:
    MIGRATOR_URL, APP_RW_URL, MIGRATOR_SYNC_URL = require_disposable_database_settings(settings)
    # Bound direct psycopg2 connections too, including migration rehearsals.
    os.environ['PGOPTIONS'] = (os.environ.get('PGOPTIONS', '')
                              + ' -c lock_timeout=5s -c statement_timeout=120s')


@event.listens_for(Engine, 'begin')
def bound_test_database_waits(connection):
    """Cover async app, worker, review and ad-hoc test transactions alike."""
    if connection.dialect.name == 'postgresql':
        connection.exec_driver_sql("SET LOCAL lock_timeout='5s'")
        connection.exec_driver_sql("SET LOCAL statement_timeout='120s'")


def _diagnostic_stream():
    """Test diagnostics bypass pytest's sys-level capture (capsys), so tests that
    assert a CLI's exact stderr stay unaffected; fd capture still shows the dump
    in the failure report and in CI logs."""
    return sys.__stderr__


# Retain an unwrapped connector so a diagnostic failure cannot recursively dump.
_diagnostic_connect = psycopg2.connect
_current_test_item = None


def dump_test_database_activity(*, pid=None, include_idle=False):
    """Use an independent connection, even if the test's transaction is aborted."""
    if target_url():
        return
    from psycopg2.extras import RealDictCursor

    try:
        with closing(_diagnostic_connect(MIGRATOR_SYNC_URL, connect_timeout=3,
                     options='-c statement_timeout=2000 -c lock_timeout=1000')) as diagnostic:
            with diagnostic.cursor(cursor_factory=RealDictCursor) as cursor:
                # The timed-out wait is gone from pg_locks. Include all open
                # transactions so its holder remains visible after 55P03.
                cursor.execute("""SELECT pid,state,wait_event_type,wait_event,query,
                    pg_blocking_pids(pid) AS blocking_pids,
                    clock_timestamp()-xact_start AS xact_age
                    FROM pg_stat_activity WHERE datname=current_database()
                    AND pid<>pg_backend_pid() AND (%s OR xact_start IS NOT NULL OR pid=%s)
                    ORDER BY pid""", (include_idle, pid))
                activity = cursor.fetchall()
                cursor.execute("""SELECT pid,locktype,mode,granted,relation::regclass::text,
                    transactionid,classid,objid,objsubid FROM pg_locks
                    WHERE pid=ANY(%s) ORDER BY pid,granted,locktype""",
                    ([row['pid'] for row in activity],))
                print('pg_stat_activity=' + json.dumps(activity, default=str), file=_diagnostic_stream())
                print('pg_locks=' + json.dumps(cursor.fetchall(), default=str), file=_diagnostic_stream())
    except Exception as diagnostic_error:
        # A diagnostic must never replace the original named test failure.
        print(f'Lock diagnostic unavailable: {type(diagnostic_error).__name__}', file=_diagnostic_stream())


def dump_test_lock_timeout(error, *, statement=None, pid=None):
    if target_url() or not is_lock_timeout(error):
        return
    if _current_test_item is not None:
        _current_test_item._lock_dump_emitted = True
    print(f'PostgreSQL lock timeout: pid={pid} waiting_statement={statement!r}', file=_diagnostic_stream())
    dump_test_database_activity(pid=pid)


def _capture_raw_lock_timeout(operation):
    @wraps(operation)
    def captured(self, *args, **kwargs):
        try:
            return operation(self, *args, **kwargs)
        except Exception as error:
            if is_lock_timeout(error):
                connection = getattr(self, 'connection', self)
                query = getattr(self, 'query', None)
                statement = query.decode('utf-8', errors='replace') if isinstance(query, bytes) else query
                dump_test_lock_timeout(error, statement=statement or 'COMMIT', pid=connection.get_backend_pid())
            raise
    return captured


@cache
def _diagnostic_cursor_factory(factory):
    class DiagnosticCursor(factory):
        execute = _capture_raw_lock_timeout(factory.execute)
        executemany = _capture_raw_lock_timeout(factory.executemany)
        callproc = _capture_raw_lock_timeout(factory.callproc)
    return DiagnosticCursor


@cache
def _diagnostic_connection_factory(factory):
    class DiagnosticConnection(factory):
        def cursor(self, name=None, cursor_factory=None, *args, **kwargs):
            cursor_factory = _diagnostic_cursor_factory(cursor_factory or self.cursor_factory or psycopg2.extensions.cursor)
            return super().cursor(name, cursor_factory, *args, **kwargs)

        commit = _capture_raw_lock_timeout(factory.commit)
        __exit__ = _capture_raw_lock_timeout(factory.__exit__)
    return DiagnosticConnection


def pytest_configure(config):
    if target_url():
        return

    @wraps(psycopg2.connect)
    def connect(dsn=None, connection_factory=None, *args, **kwargs):
        # Cover every raw fixture, including export_store's teardown TRUNCATE,
        # while preserving custom connection/cursor factories. Asyncpg test
        # statements currently all go through SQLAlchemy's handle_error below.
        connection = _diagnostic_connect(dsn, _diagnostic_connection_factory(
            connection_factory or psycopg2.extensions.connection), *args, **kwargs)
        _open_raw_connections.add(connection)
        return connection

    config._lock_diagnostic_patch = pytest.MonkeyPatch()
    config._lock_diagnostic_patch.setattr(psycopg2, 'connect', connect)


def pytest_unconfigure(config):
    if patcher := getattr(config, '_lock_diagnostic_patch', None):
        patcher.undo()


@event.listens_for(Engine, 'handle_error')
def capture_database_lock_timeout(context):
    if not is_lock_timeout(context.original_exception):
        return
    driver = context.connection.connection.driver_connection
    pid = driver.get_server_pid() if hasattr(driver, 'get_server_pid') else driver.get_backend_pid()
    dump_test_lock_timeout(context.original_exception, statement=context.statement, pid=pid)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    # Keep a fallback for exceptions without an instrumented DB connection.
    if call.excinfo is not None and not target_url():
        error = call.excinfo.value
        if is_lock_timeout(error):
            if getattr(item, '_lock_dump_emitted', False):
                print(f'{item.nodeid}: lock timeout dump already emitted', file=_diagnostic_stream())
            else:
                dump_test_lock_timeout(error)
    yield


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item):
    global _current_test_item
    previous, _current_test_item = _current_test_item, item
    item._lock_dump_emitted = False
    try:
        yield
    finally:
        _current_test_item = previous


# ---------------------------------------------------------------------------
# Transactions a FAILED test body leaves open are rolled back before any fixture
# finalizer runs. Otherwise a teardown TRUNCATE or a migration rehearsal's
# LOCK TABLE ... NOWAIT waits on, or fails against, the leaked transaction
# (#180: the CI hang; the #190 rerun's 55P03 on `person`). Covered: ORM sessions,
# SQLAlchemy connections (async and sync) and raw psycopg2 connections opened
# through the instrumented connect above. Fixtures of a PASSING test keep owning
# their transactions: several (app_rw_session, migrator_session) commit at
# teardown, so a passing test is left alone.
# ---------------------------------------------------------------------------
_open_test_sessions = weakref.WeakSet()
_open_test_connections = weakref.WeakSet()
_open_raw_connections = weakref.WeakSet()


@event.listens_for(Session, 'after_begin')
def _track_open_test_session(session, transaction, connection):
    _open_test_sessions.add(session)


@event.listens_for(Engine, 'begin')
def _track_open_test_connection(connection):
    _open_test_connections.add(connection)


@event.listens_for(Engine, 'commit')
@event.listens_for(Engine, 'rollback')
def _forget_ended_test_connection(connection):
    _open_test_connections.discard(connection)


def _raw_in_transaction(connection):
    return not connection.closed and connection.status in (
        psycopg2.extensions.STATUS_BEGIN, psycopg2.extensions.STATUS_IN_TRANSACTION)


async def _end_open_test_transactions():
    ended = []
    for session in list(_open_test_sessions):
        _open_test_sessions.discard(session)
        if not session.in_transaction():
            continue
        try:
            proxy = async_session(session)
            if proxy is not None:
                await proxy.rollback()
            else:
                session.rollback()
            ended.append('session')
        except Exception as error:
            ended.append(f'session (rollback failed: {type(error).__name__})')
    for connection in list(_open_test_connections):
        _open_test_connections.discard(connection)
        if connection.closed or not connection.in_transaction():
            continue
        try:
            if connection.dialect.is_async:
                await greenlet_spawn(connection.rollback)
            else:
                connection.rollback()
            ended.append('connection')
        except Exception as error:
            ended.append(f'connection (rollback failed: {type(error).__name__})')
    for raw in list(_open_raw_connections):
        if not _raw_in_transaction(raw):
            continue
        try:
            raw.rollback()
            ended.append('psycopg2')
        except Exception as error:
            ended.append(f'psycopg2 (rollback failed: {type(error).__name__})')
    return ended


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    outcome = yield
    if outcome.excinfo is None or target_url() or os.environ.get('HV_TEST_KEEP_OPEN_TRANSACTIONS'):
        return
    try:
        loop = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        return
    if loop.is_closed() or loop.is_running():
        return
    ended = loop.run_until_complete(_end_open_test_transactions())
    if ended:
        # Part of the failure report; nothing is printed outside pytest's capture.
        item.add_report_section('call', 'open transactions',
            f'rolled back {len(ended)} open test transaction(s) before teardown: {", ".join(ended)}')


@pytest.hookimpl(tryfirst=True)
def pytest_timeout_set_timer(item, settings):
    """Add database evidence before pytest-timeout's normal stacks/termination."""
    if target_url():
        return None
    import pytest_timeout

    def expired(timeout_func):
        if not settings.disable_debugger_detection and pytest_timeout.is_debugging():
            return
        capture = item.config.pluginmanager.getplugin('capturemanager')
        from contextlib import nullcontext
        with capture.global_and_fixture_disabled() if capture else nullcontext():
            print(f'Pytest timeout: test id={item.nodeid}', file=_diagnostic_stream(), flush=True)
            dump_test_database_activity(include_idle=True)
        timeout_func(item, settings)

    if settings.method == 'signal' and threading.current_thread() is threading.main_thread():
        def cancel():
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, signal.SIG_DFL)
        item.cancel_timeout = cancel
        signal.signal(signal.SIGALRM, lambda signum, frame: expired(pytest_timeout.timeout_sigalrm))
        signal.setitimer(signal.ITIMER_REAL, settings.timeout)
    else:
        timer = threading.Timer(settings.timeout, expired, (pytest_timeout.timeout_timer,))
        timer.name = f'pytest_timeout {item.nodeid}'
        def cancel():
            timer.cancel()
            timer.join()
        item.cancel_timeout = cancel
        timer.start()
    return True


def pytest_sessionstart(session):
    """Verify an external target before any scenario can create records."""
    url = target_url()
    if not url:
        return
    try:
        email, password = admin_credentials()
        with httpx.Client(base_url=url, timeout=60) as client:
            client.post("/api/auth/login", json={"email": email, "password": password}).raise_for_status()
            response = client.get("/api/properties")
            response.raise_for_status()
            properties = response.json()
    except (httpx.HTTPError, httpx.InvalidURL, ValueError):
        raise pytest.UsageError("Cannot verify a fresh external target; refusing to write scenario data") from None
    if properties != []:
        raise pytest.UsageError("target is not a fresh installation; refusing to write scenario data")


def pytest_collection_modifyitems(items):
    # Destructive fixture regressions must finish before session seeds are made.
    items.sort(key=lambda item: item.path.name != "test_testdb_reset.py")


# ---------------------------------------------------------------------------
# Run Alembic migrations before tests
# ---------------------------------------------------------------------------
def clear_disposable_source_context(cursor):
    """Only for destructive migration rehearsals in the guarded disposable DB."""
    from psycopg2 import sql
    from sqlalchemy.engine import make_url

    _, _, sync_url = require_disposable_database_settings(settings)
    expected = make_url(sync_url)
    connection = cursor.connection.info
    if (connection.host, connection.port, connection.dbname, connection.user) != (
        expected.host, expected.port, expected.database, expected.username
    ):
        raise RuntimeError("Migration rehearsal cursor is not the guarded disposable connection")
    cursor.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()")
    if cursor.fetchone() != ('app_migrator',):
        raise RuntimeError("Migration rehearsal requires the owned disposable database")
    # Reset only synthetic session invalidations in this owned disposable DB.
    # The production downgrade still refuses any positive generation.
    cursor.execute("SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='user' AND column_name='session_version')")
    if cursor.fetchone()[0]:
        cursor.execute('UPDATE public."user" SET session_version=0 WHERE session_version<>0')
    cursor.execute("SELECT to_regprocedure('public.guard_property_source_context()')")
    if not cursor.fetchone()[0]:
        return

    # Modern receipts can be independent of property/document foreign keys.
    # No CASCADE: a new reverse FK must not silently erase the shared seeds.
    retained = ['alembic_version', 'organization', 'user', 'membership',
                'management_mandate', 'audit_event', 'cost_category']
    cursor.execute("""SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relname<>ALL(%s)
      AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass
        AND d.objid=c.oid AND d.deptype='e') ORDER BY c.relname""", (retained,))
    tables = [sql.Identifier('public', row[0]) for row in cursor.fetchall()]
    if tables:
        cursor.execute(sql.SQL('TRUNCATE {}').format(sql.SQL(',').join(tables)))


async def fixture_has_registered_documents(session: AsyncSession, document_ids: list[uuid.UUID]) -> bool:
    if not await session.scalar(text("SELECT to_regclass('public.document_ref')")):
        return False
    return bool(await session.scalar(
        text("SELECT EXISTS(SELECT 1 FROM public.document_ref "
             "WHERE property_document_id=ANY(CAST(:document_ids AS uuid[])))"),
        {"document_ids": document_ids},
    ))


def migrate_test_database(request):
    # Migration CLI logging must not disable the already imported HTTP app's
    # loggers or replace its scrubber/formatter inside this shared test process;
    # tests call this helper directly, so the patch lives here as well as in the
    # session fixture that keeps it active for later migration rehearsals.
    with patch("logging.config.fileConfig"):
        return _migrate_test_database(request)


def _migrate_test_database(request):
    """Run Alembic migrations using the migrator role."""
    if MIGRATOR_SYNC_URL is None:
        raise RuntimeError("VERWALINO_TARGET_URL is supported only by tests/bdd; unset it for local database tests")
    from alembic.config import Config
    from alembic import command
    from alembic.script import ScriptDirectory
    from alembic.util import CommandError
    import psycopg2

    alembic_cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", MIGRATOR_SYNC_URL)
    alembic_cfg.attributes["hv_declaration_test_database"] = True
    alembic_cfg.set_main_option(
        "script_location",
        os.path.join(os.path.dirname(__file__), "..", "migrations"),
    )

    scripts = ScriptDirectory.from_config(alembic_cfg)
    with closing(psycopg2.connect(MIGRATOR_SYNC_URL)) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.alembic_version')")
        revisions = []
        if cursor.fetchone()[0] is not None:
            cursor.execute("SELECT version_num FROM public.alembic_version")
            revisions = [row[0] for row in cursor.fetchall()]
    for revision in revisions:
        try:
            known_revision = scripts.get_revision(revision)
        except CommandError:
            known_revision = None
        if known_revision is None:
            reset_foreign_test_database(MIGRATOR_SYNC_URL)
            notice = f"test database held foreign migration head {revision}; reset to empty before upgrading"
            if request is not None:
                capture_manager = request.config.pluginmanager.getplugin("capturemanager")
                with capture_manager.global_and_fixture_disabled():
                    request.config.get_terminal_writer().line(notice)
            return _upgrade_test_database(alembic_cfg)

    # This harness is already restricted to the disposable test database above.
    # Production downgrade intentionally refuses to remove a populated dunning
    # boundary. Discard only these synthetic associations before the full reset.
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("""DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='app_worker') THEN
            CREATE ROLE app_worker LOGIN PASSWORD 'worker_secret' INHERIT NOBYPASSRLS NOCREATEDB NOCREATEROLE;
            END IF; END $$; GRANT app_rw TO app_worker;""")
        cursor.execute("""DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='app_review') THEN
            CREATE ROLE app_review LOGIN PASSWORD 'review_secret' NOINHERIT NOBYPASSRLS NOCREATEDB NOCREATEROLE;
            END IF; END $$;""")
        cursor.execute("SELECT to_regclass('public.allocation_source_claim')")
        if cursor.fetchone()[0] is not None:
            cursor.execute("TRUNCATE public.allocation_source_claim CASCADE")
        cursor.execute("SELECT to_regclass('public.visual_disclosure_attestation')")
        if cursor.fetchone()[0] is not None:
            cursor.execute("TRUNCATE public.visual_disclosure_attestation CASCADE")
        cursor.execute("SELECT to_regclass('public.visual_extract_attestation')")
        if cursor.fetchone()[0] is not None:
            cursor.execute("TRUNCATE public.visual_extract_attestation CASCADE")
        cursor.execute("SELECT to_regclass('public.financial_computation_attestation')")
        if cursor.fetchone()[0]:
            cursor.execute("TRUNCATE public.financial_computation_attestation CASCADE")
        cursor.execute("SELECT to_regclass('public.exception_completion')")
        if cursor.fetchone()[0]:
            cursor.execute("TRUNCATE public.exception_completion CASCADE")
        cursor.execute("SELECT to_regclass('public.allocation_integrity_attestation')")
        if cursor.fetchone()[0]:
            cursor.execute("TRUNCATE public.allocation_integrity_attestation CASCADE")
        cursor.execute("SELECT to_regclass('public.document_ref')")
        if cursor.fetchone()[0]:
            cursor.execute("TRUNCATE public.document_ref CASCADE")
        cursor.execute("SELECT to_regclass('public.intake_batch')")
        if cursor.fetchone()[0]:
            cursor.execute("TRUNCATE public.intake_batch,public.intake_upload_admission CASCADE")
        clear_disposable_source_context(cursor)
        cursor.execute("SELECT to_regclass('public.source_security_transition')")
        if cursor.fetchone()[0]:
            cursor.execute("TRUNCATE public.source_security_transition,public.source_security_authorization")
        cursor.execute("SELECT to_regclass('public.access_disposition_acceptance')")
        if cursor.fetchone()[0]:
            cursor.execute("TRUNCATE public.access_disposition_acceptance CASCADE")
        cursor.execute("SELECT to_regclass('public.runtime_budget_attestation')")
        if cursor.fetchone()[0]:
            cursor.execute("TRUNCATE public.runtime_budget_review,public.runtime_budget_attestation CASCADE")
        cursor.execute("SELECT to_regclass('public.knowledge_budget_policy')")
        if cursor.fetchone()[0]:
            cursor.execute("TRUNCATE public.knowledge_budget_policy")
        cursor.execute("SELECT to_regclass('public.property_source_security')")
        if cursor.fetchone()[0]:
            cursor.execute("TRUNCATE public.property_source_security")
        cursor.execute("SELECT to_regclass('public.property_data_domain')")
        if cursor.fetchone()[0]:
            cursor.execute("TRUNCATE public.property_data_grant,public.property_data_domain CASCADE")
        cursor.execute("SELECT to_regclass('public.person_data_domain')")
        if cursor.fetchone()[0]:
            cursor.execute("TRUNCATE public.person_data_grant,public.person_data_domain")
        cursor.execute("SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='person' AND column_name='created_by')")
        if cursor.fetchone()[0]:
            cursor.execute("TRUNCATE public.person CASCADE")
        cursor.execute("SELECT to_regclass('public.property_knowledge_query_run')")
        if cursor.fetchone()[0]:
            cursor.execute("TRUNCATE public.property_knowledge_query_run")
        cursor.execute("SELECT to_regclass('public.declaration_queue_request')")
        if cursor.fetchone()[0] is not None:
            cursor.execute("TRUNCATE public.declaration_queue_request CASCADE")
        cursor.execute("SELECT to_regclass('public.mandate_document')")
        if cursor.fetchone()[0] is not None:
            cursor.execute("TRUNCATE public.mandate_document CASCADE")
        cursor.execute("SELECT to_regclass('public.scope_control_event')")
        if cursor.fetchone()[0] is not None:
            cursor.execute("TRUNCATE public.scope_control_event")
        cursor.execute("SELECT to_regclass('public.scope_payment_reconciliation')")
        if cursor.fetchone()[0] is not None:
            cursor.execute("TRUNCATE public.scope_payment_reconciliation_item, public.scope_payment_reconciliation")
        cursor.execute("SELECT to_regclass('public.access_grant_proposal')")
        if cursor.fetchone()[0] is not None:
            cursor.execute("TRUNCATE public.access_grant_acceptance,public.access_grant_proposal CASCADE")
        cursor.execute("SELECT to_regclass('public.access_coverage_snapshot')")
        if cursor.fetchone()[0] is not None:
            cursor.execute("TRUNCATE public.access_coverage_review,public.access_coverage_snapshot CASCADE")
        cursor.execute("SELECT to_regclass('public.invoice_upload_admission')")
        if cursor.fetchone()[0] is not None:
            cursor.execute("TRUNCATE public.invoice_upload_admission,public.invoice_document CASCADE")
        cursor.execute("SELECT to_regclass('public.dunning_quarantine_disposition')")
        if cursor.fetchone()[0] is not None:
            cursor.execute("TRUNCATE public.dunning_quarantine_disposition")
        cursor.execute("SELECT to_regclass('public.dunning_scope_context')")
        if cursor.fetchone()[0] is not None:
            cursor.execute("TRUNCATE public.dunning_scope_context")

        cursor.execute("SELECT to_regclass('public.declaration_review_receipt')")
        if cursor.fetchone()[0] is not None:
            cursor.execute("TRUNCATE public.declaration_review_receipt")
        cursor.execute("SELECT to_regclass('public.organization_deletion')")
        if cursor.fetchone()[0] is not None:
            cursor.execute("TRUNCATE public.organization_deletion")
        cursor.execute("SELECT to_regclass('public.auth_throttle')")
        if cursor.fetchone()[0] is not None:
            cursor.execute("TRUNCATE public.auth_throttle")

    command.downgrade(alembic_cfg, "base")
    return _upgrade_test_database(alembic_cfg)


def _upgrade_test_database(alembic_cfg):
    # Rehearse the supported upgrade before any retained test originals exist.
    # The rehearsal uses one rollback-only transaction; never clear later rows
    # or weaken a populated downgrade guard to make a historical test pass.
    from alembic import command
    from tests.test_deployment_upgrade import rehearse_initial_upgrade

    command.upgrade(alembic_cfg, "0028")
    upgraded_0028_tables = rehearse_initial_upgrade('0028')
    command.upgrade(alembic_cfg, "0122")
    upgraded_tables = rehearse_initial_upgrade('0122')
    command.upgrade(alembic_cfg, "0139")
    upgraded_0139_tables = rehearse_initial_upgrade('0139')
    command.upgrade(alembic_cfg, '0140')  # accepted source
    upgraded_parent_tables = rehearse_initial_upgrade('0140')  # accepted source
    command.upgrade(alembic_cfg, '0141')  # accepted source
    upgraded_setup_tables = rehearse_initial_upgrade('0141')  # accepted source
    command.upgrade(alembic_cfg, '0142')  # accepted source
    upgraded_throttle_tables = rehearse_initial_upgrade('0142')  # accepted source
    command.upgrade(alembic_cfg, '0143')  # accepted source
    upgraded_lease_tables = rehearse_initial_upgrade('0143')  # accepted source
    command.upgrade(alembic_cfg, '0144')  # accepted source
    upgraded_signup_tables = rehearse_initial_upgrade('0144')  # accepted source
    command.upgrade(alembic_cfg, '0145')  # accepted source
    upgraded_reset_tables = rehearse_initial_upgrade('0145')  # accepted source
    command.upgrade(alembic_cfg, '0146')  # accepted source
    upgraded_quota_tables = rehearse_initial_upgrade('0146')
    command.upgrade(alembic_cfg, '0147')  # accepted source
    upgraded_lifecycle_tables = rehearse_initial_upgrade('0147')
    command.upgrade(alembic_cfg, "head")
    return {'upgrade_0028_to_0148_tables': upgraded_0028_tables,
            'upgrade_0122_to_0148_tables': upgraded_tables,
            'upgrade_0139_to_0148_tables': upgraded_0139_tables,
            'upgrade_0140_to_0148_tables': upgraded_parent_tables,
            'upgrade_0141_to_0148_tables': upgraded_setup_tables,
            'upgrade_0142_to_0148_tables': upgraded_throttle_tables,
            'upgrade_0143_to_0148_tables': upgraded_lease_tables,
            'upgrade_0144_to_0148_tables': upgraded_signup_tables,
            'upgrade_0145_to_0148_tables': upgraded_reset_tables,
            'upgrade_0146_to_0148_tables': upgraded_quota_tables,
            'upgrade_0147_to_0148_tables': upgraded_lifecycle_tables}  # accepted source


@pytest.fixture(scope="session", autouse=True)
def run_migrations(request=None):
    # Keep the fileConfig patch active for later migration rehearsals during the session.
    with patch("logging.config.fileConfig"):
        yield migrate_test_database(request)


# ---------------------------------------------------------------------------
# Inject test engine into the app's DB module (once, at session start)
# ---------------------------------------------------------------------------
_test_app_rw_engine = None


@pytest.fixture(autouse=True)
def isolate_diagnostics_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'diagnostics_dir', str(tmp_path / 'diagnostics'))


@pytest_asyncio.fixture(autouse=True)
async def inject_test_engine(run_migrations):
    """Replace the app's DB engine with a fresh one per test function.

    This avoids asyncpg event-loop mismatch: each test function gets
    its own event loop (pytest-asyncio default), so the engine must be
    recreated to use the current loop.
    """
    global _test_app_rw_engine
    _test_app_rw_engine = create_async_engine(
        APP_RW_URL, pool_pre_ping=True, pool_size=10, max_overflow=5
    )
    override_engine(_test_app_rw_engine)
    from sqlalchemy.engine import make_url
    worker_engine = create_async_engine(make_url(APP_RW_URL).set(username='app_worker',password='worker_secret'), pool_pre_ping=True)
    override_worker_engine(worker_engine)
    review_engine = create_async_engine(make_url(APP_RW_URL).set(username='app_review',password='review_secret'), pool_pre_ping=True)
    override_review_engine(review_engine)
    # Each test owns synthetic throttle state. The restart tests open new app
    # sessions within one test, so durability remains exercised.
    async with _test_app_rw_engine.begin() as connection:
        await connection.execute(text("DELETE FROM public.auth_throttle"))
    yield
    await review_engine.dispose()
    override_review_engine(None)
    await worker_engine.dispose()
    override_worker_engine(None)
    await _test_app_rw_engine.dispose()


# ---------------------------------------------------------------------------
# Seed data: two organizations with users and memberships
# ---------------------------------------------------------------------------
_seed_cache: dict | None = None


@pytest.fixture(scope="session")
def seed_data(run_migrations):
    """Create two orgs, users, memberships, and sample mandates using the migrator role."""
    global _seed_cache
    if _seed_cache is not None:
        return _seed_cache

    org_a_id = uuid.uuid4()
    org_b_id = uuid.uuid4()
    user_a_admin_id = uuid.uuid4()
    user_a_clerk_id = uuid.uuid4()
    user_a_readonly_id = uuid.uuid4()
    user_b_admin_id = uuid.uuid4()
    mandate_a_id = uuid.uuid4()
    mandate_b_id = uuid.uuid4()

    pw_hash = hash_password("testpass1234")

    # Use synchronous psycopg2 for seeding (avoids async event loop issues in session fixture)
    import psycopg2
    sync_url = MIGRATOR_SYNC_URL
    # Parse: postgresql://user:pass@host:port/db
    conn = psycopg2.connect(sync_url)
    conn.autocommit = False
    try:
        cur = conn.cursor()

        # Organizations
        cur.execute(
            "INSERT INTO organization (id, name) VALUES (%s, %s)",
            (str(org_a_id), "Verwaltung Alpha"),
        )
        cur.execute(
            "INSERT INTO organization (id, name) VALUES (%s, %s)",
            (str(org_b_id), "Verwaltung Beta"),
        )

        # Users for Org A
        for uid, email, name in [
            (user_a_admin_id, "admin@alpha.de", "Admin Alpha"),
            (user_a_clerk_id, "clerk@alpha.de", "Clerk Alpha"),
            (user_a_readonly_id, "reader@alpha.de", "Reader Alpha"),
        ]:
            cur.execute(
                'INSERT INTO "user" (id, organization_id, email, password_hash, display_name) '
                "VALUES (%s, %s, %s, %s, %s)",
                (str(uid), str(org_a_id), email, pw_hash, name),
            )

        # User for Org B
        cur.execute(
            'INSERT INTO "user" (id, organization_id, email, password_hash, display_name) '
            "VALUES (%s, %s, %s, %s, %s)",
            (str(user_b_admin_id), str(org_b_id), "admin@beta.de", pw_hash, "Admin Beta"),
        )

        # Memberships
        for uid, org, role in [
            (user_a_admin_id, org_a_id, "OWNER_ADMIN"),
            (user_a_clerk_id, org_a_id, "CLERK"),
            (user_a_readonly_id, org_a_id, "READ_ONLY"),
            (user_b_admin_id, org_b_id, "OWNER_ADMIN"),
        ]:
            cur.execute(
                "INSERT INTO membership (id, organization_id, user_id, role) "
                "VALUES (%s, %s, %s, %s)",
                (str(uuid.uuid4()), str(org), str(uid), role),
            )

        # Mandates
        cur.execute(
            "INSERT INTO management_mandate (id, organization_id, mandate_type, label, start_date) "
            "VALUES (%s, %s, %s, %s, %s)",
            (str(mandate_a_id), str(org_a_id), "WEG", "WEG Hauptstr 5", date(2025, 1, 1)),
        )
        cur.execute(
            "INSERT INTO management_mandate (id, organization_id, mandate_type, label, start_date) "
            "VALUES (%s, %s, %s, %s, %s)",
            (str(mandate_b_id), str(org_b_id), "MIET", "Miet Bahnhofstr 12", date(2025, 6, 1)),
        )

        # Audit events
        cur.execute(
            "INSERT INTO audit_event (id, organization_id, actor_user_id, entity_type, entity_id, action) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (str(uuid.uuid4()), str(org_a_id), str(user_a_admin_id), "organization", str(org_a_id), "org.created"),
        )
        cur.execute(
            "INSERT INTO audit_event (id, organization_id, actor_user_id, entity_type, entity_id, action) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (str(uuid.uuid4()), str(org_b_id), str(user_b_admin_id), "organization", str(org_b_id), "org.created"),
        )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    _seed_cache = {
        "org_a_id": org_a_id,
        "org_b_id": org_b_id,
        "user_a_admin_id": user_a_admin_id,
        "user_a_clerk_id": user_a_clerk_id,
        "user_a_readonly_id": user_a_readonly_id,
        "user_b_admin_id": user_b_admin_id,
        "mandate_a_id": mandate_a_id,
        "mandate_b_id": mandate_b_id,
    }
    return _seed_cache


# ---------------------------------------------------------------------------
# Authenticated test clients
# ---------------------------------------------------------------------------
def _make_session_cookie(user_id: uuid.UUID, org_id: uuid.UUID) -> str:
    """A newly authenticated test client uses the current durable generation.

    Existing client cookies remain untouched so revocation tests stay meaningful.
    """
    import psycopg2

    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT session_version FROM public."user" WHERE id=%s AND organization_id=%s',
                           (str(user_id), str(org_id)))
            row = cursor.fetchone()
    return create_session_token(user_id, org_id, row[0] if row else 0)


def _api_test_client(**kwargs) -> httpx.AsyncClient:
    """Opt-in smoke tests can only target the local development API."""
    if os.environ.get("HV_TEST_LOCAL_API") == "1":
        return httpx.AsyncClient(base_url="http://127.0.0.1:18000", timeout=60, **kwargs)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", **kwargs)


@pytest_asyncio.fixture
async def client_a_admin(seed_data) -> AsyncGenerator[httpx.AsyncClient, None]:
    token = _make_session_cookie(
        seed_data["user_a_admin_id"], seed_data["org_a_id"]
    )
    async with _api_test_client(
        cookies={settings.session_cookie_name: token},
    ) as client:
        yield client


@pytest_asyncio.fixture
async def client_a_clerk(seed_data) -> AsyncGenerator[httpx.AsyncClient, None]:
    token = _make_session_cookie(
        seed_data["user_a_clerk_id"], seed_data["org_a_id"]
    )
    async with _api_test_client(
        cookies={settings.session_cookie_name: token},
    ) as client:
        yield client


@pytest_asyncio.fixture
async def client_a_readonly(seed_data) -> AsyncGenerator[httpx.AsyncClient, None]:
    token = _make_session_cookie(
        seed_data["user_a_readonly_id"], seed_data["org_a_id"]
    )
    async with _api_test_client(
        cookies={settings.session_cookie_name: token},
    ) as client:
        yield client


@pytest_asyncio.fixture
async def client_b_admin(seed_data) -> AsyncGenerator[httpx.AsyncClient, None]:
    token = _make_session_cookie(
        seed_data["user_b_admin_id"], seed_data["org_b_id"]
    )
    async with _api_test_client(
        cookies={settings.session_cookie_name: token},
    ) as client:
        yield client


@pytest_asyncio.fixture
async def client_unauth() -> AsyncGenerator[httpx.AsyncClient, None]:
    async with _api_test_client(
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Direct DB sessions for RLS-level tests
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def app_rw_session(seed_data) -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(APP_RW_URL, pool_size=2)
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                yield session
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def migrator_session(seed_data) -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(MIGRATOR_URL, pool_size=2)
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                yield session
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Declaration provider fixtures (no mutable module-level provider state)
# ---------------------------------------------------------------------------
@pytest.fixture
def fake_declaration_provider():
    """A strict, deterministic provider fixture with real page evidence."""
    from app.declarations.fake_provider import FakeDeclarationProvider
    from app.declarations.schemas import (
        CoreExtractionOutput,
        DocumentInventoryOutput,
        EvidencePayload,
        FactPayload,
        ReconciliationOutput,
        RuleEffect,
        RuleExtractionOutput,
        RulePayload,
        RuleScope,
        TargetedRecheckOutput,
        TextValue,
    )

    evidence = EvidencePayload(
        document_key="declaration-main",
        pdf_page=1,
        section="§ 1",
        quote="Hauptstraße 1, 40213 Düsseldorf",
    )
    address = FactPayload(
        subject_key="property:main",
        field="street",
        raw_value="Hauptstraße 1",
        normalized_value=TextValue(kind="TEXT", value="Hauptstraße 1"),
        confidence=0.99,
        evidence=[evidence],
    )
    unit_number = FactPayload(
        subject_key="unit:1",
        field="number",
        raw_value="1",
        normalized_value=TextValue(kind="TEXT", value="1"),
        confidence=0.98,
        evidence=[evidence],
    )
    heating_rule = RulePayload(
        subject_key="rule:heating-costs",
        title="Heizkostenverteilung",
        topic="heating_costs",
        summary="Heizkosten werden nach Verbrauch und Fläche verteilt.",
        scope=RuleScope(all_units=True),
        effect=RuleEffect(
            responsibility=["Eigentümergemeinschaft"],
            administrative_note="Bei Abrechnung beachten.",
        ),
        evidence=[evidence],
        confidence=0.97,
        clarification_needed=False,
    )
    inventory = DocumentInventoryOutput(
        documents=[
            {
                "document_key": "declaration-main",
                "document_type": "DECLARATION",
                "title": "Teilungserklärung",
            }
        ]
    )
    core = CoreExtractionOutput(facts=[address, unit_number])
    rules = RuleExtractionOutput(rules=[heating_rule])
    return FakeDeclarationProvider(
        inventory_output=inventory,
        core_output=core,
        rules_output=rules,
        reconciliation_output=ReconciliationOutput(
            facts=[address, unit_number], rules=[heating_rule]
        ),
        recheck_output=TargetedRecheckOutput(),
    )


# ---------------------------------------------------------------------------
# Approved knowledge graph (synthetic retrieval fixture)
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def approved_knowledge_graph(seed_data):
    """A compact approved graph with current, historic, and conflicted knowledge."""
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal
    from types import SimpleNamespace

    from app.declarations.approval import value_fingerprint
    from app.models.approved_fact_source import ApprovedFactSource
    from app.models.declaration_import import DeclarationImport
    from app.models.property import Property
    from app.models.property_assignment import PropertyAssignment
    from app.models.property_document import PropertyDocument
    from app.models.property_document_relation import PropertyDocumentRelation
    from app.models.property_legal_reference import PropertyLegalReference
    from app.models.property_rule import PropertyRule, PropertyRuleRelation, PropertyRuleSource
    from app.models.unit import Unit

    org_id = seed_data["org_a_id"]
    user_id = seed_data["user_a_clerk_id"]
    created: dict[str, uuid.UUID] = {name: uuid.uuid4() for name in (
        "property", "import", "main", "amendment", "unit_one", "unit_two",
        "assignment", "relation", "superseded_relation", "legal", "heating", "elevator_old", "elevator_new", "repair", "foreign",
    )}
    now = datetime.now(timezone.utc)
    engine = create_async_engine(APP_RW_URL)
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                await session.execute(text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(org_id)})
                property_row = Property(
                    id=created["property"], organization_id=org_id, name="WEG Beispielhof",
                    street="Neue Straße 2", zip_code="40213", city="Düsseldorf",
                    management_type="WEG", mea_denominator=1_000_000, mea_scale_status="CONFIRMED",
                )
                session.add(property_row)
                await session.flush()
                item = DeclarationImport(
                    id=created["import"], organization_id=org_id, mode="EXISTING_PROPERTY",
                    target_property_id=property_row.id, applied_property_id=property_row.id,
                    status="APPLIED", draft_revision=1, initiated_by=user_id,
                    approved_by=user_id, approved_at=now, max_cost_usd=Decimal("1.00"),
                    approval_idempotency_key="knowledge-graph-fixture",
                    approval_result_json={"property_id": str(property_row.id)},
                )
                main = PropertyDocument(
                    id=created["main"], organization_id=org_id, property_id=property_row.id,
                    doc_type="DECLARATION", title="Teilungserklärung", original_filename="synthetic-main.pdf",
                    mime_type="application/pdf", content_byte_size=1, content_sha256="1" * 64,
                    storage_uri="test/synthetic-main.pdf", storage_backend="LOCAL", page_count=4, uploaded_by=seed_data['user_a_admin_id'],
                )
                amendment = PropertyDocument(
                    id=created["amendment"], organization_id=org_id, property_id=property_row.id,
                    doc_type="AMENDMENT", title="Nachtrag", original_filename="synthetic-amendment.pdf",
                    mime_type="application/pdf", content_byte_size=1, content_sha256="2" * 64,
                    storage_uri="test/synthetic-amendment.pdf", storage_backend="LOCAL", page_count=3, uploaded_by=seed_data['user_a_admin_id'],
                )
                unit_one = Unit(id=created["unit_one"], organization_id=org_id, property_id=property_row.id, number="1", location="EG", building_part="Haus A", area=Decimal("50"), heated_area=Decimal("50.0"), usage_type="WOHNUNG", mea=Decimal("47732"))
                unit_two = Unit(id=created["unit_two"], organization_id=org_id, property_id=property_row.id, number="2", location="OG", building_part="Haus A", usage_type="WOHNUNG", mea=Decimal("47732"))
                assignment = PropertyAssignment(id=created["assignment"], organization_id=org_id, property_id=property_row.id, unit_id=unit_two.id, stable_key="assignment:parking", assignment_type="SPECIAL_USE", designation="Stellplatz 2", location="Hof", description="synthetisch", details_json={}, source_import_id=item.id)
                legal = PropertyLegalReference(id=created["legal"], organization_id=org_id, property_id=property_row.id, reference_type="LAND_REGISTER", district="Düsseldorf", cadastral_district=None, field_number=None, parcel=None, sheet="1234", details_json={"value": "Akte 17"}, source_import_id=item.id)
                relation = PropertyDocumentRelation(id=created["relation"], organization_id=org_id, property_id=property_row.id, source_document_id=amendment.id, target_document_id=main.id, relation_type="AMENDS", source_import_id=item.id)
                superseded_relation = PropertyDocumentRelation(id=created["superseded_relation"], organization_id=org_id, property_id=property_row.id, source_document_id=main.id, target_document_id=amendment.id, relation_type="SUPPLEMENTS", status="SUPERSEDED", source_import_id=item.id)
                heating = PropertyRule(id=created["heating"], organization_id=org_id, property_id=property_row.id, stable_key="rule:heating", topic="heating_costs", title="Heizkostenverteilung", summary="Heizkosten werden nach Verbrauch und Fläche verteilt.", scope_json={"all_units": True}, effect_json={"verbrauch": "70 Prozent", "flaeche": "30 Prozent"}, status="EFFECTIVE", source_import_id=item.id, approved_by=user_id, approved_at=now, search_text="Heizkosten Verbrauch Fläche 70 Prozent 30 Prozent")
                elevator_old = PropertyRule(id=created["elevator_old"], organization_id=org_id, property_id=property_row.id, stable_key="rule:elevator-v1", topic="elevator", title="Aufzug", summary="Kosten zu einem Sechstel.", scope_json={"all_units": True}, effect_json={"share": "1/6"}, status="SUPERSEDED", valid_from=now - timedelta(days=2), valid_to=now - timedelta(days=1), source_import_id=item.id, approved_by=user_id, approved_at=now - timedelta(days=2), search_text="Aufzug 1/6")
                elevator_new = PropertyRule(id=created["elevator_new"], organization_id=org_id, property_id=property_row.id, stable_key="rule:elevator-v2", topic="elevator", title="Aufzug", summary="Kosten zu einem Siebtel.", scope_json={"all_units": True}, effect_json={"share": "1/7"}, status="EFFECTIVE", source_import_id=item.id, approved_by=user_id, approved_at=now, search_text="Aufzug 1/7")
                repair = PropertyRule(id=created["repair"], organization_id=org_id, property_id=property_row.id, stable_key="rule:repair-conflict", topic="repair", title="Reparaturzuständigkeit", summary="Wartelerähnliche Reparaturzuständigkeit ist widersprüchlich.", scope_json={"all_units": True}, effect_json={"conflict": True}, status="NEEDS_CLARIFICATION", source_import_id=item.id, approved_by=user_id, approved_at=now, search_text="Reparaturzuständigkeit Wartelerähnliche Verantwortung Konflikt")
                session.add_all([item, main, amendment, unit_one, unit_two])
                await session.flush()
                session.add_all([assignment, legal, relation, superseded_relation, heating, elevator_old, elevator_new, repair])
                await session.flush()
                session.add_all([
                    PropertyRuleRelation(organization_id=org_id, property_id=property_row.id, source_rule_id=elevator_new.id, target_rule_id=elevator_old.id, relation_type="SUPERSEDES", source_import_id=item.id),
                    PropertyRuleSource(organization_id=org_id, property_id=property_row.id, property_rule_id=heating.id, property_document_id=main.id, pdf_page=2, printed_page="2", section="§ 8", quote="70 Prozent Verbrauch und 30 Prozent Fläche."),
                    PropertyRuleSource(organization_id=org_id, property_id=property_row.id, property_rule_id=elevator_old.id, property_document_id=main.id, pdf_page=3, section="§ 9", quote="Aufzugskosten 1/6."),
                    PropertyRuleSource(organization_id=org_id, property_id=property_row.id, property_rule_id=elevator_new.id, property_document_id=amendment.id, pdf_page=1, section="§ 9", quote="Aufzugskosten 1/7."),
                    PropertyRuleSource(organization_id=org_id, property_id=property_row.id, property_rule_id=repair.id, property_document_id=amendment.id, pdf_page=2, section="§ 10", quote="Die Reparaturzuständigkeit bleibt offen."),
                ])
                current_street = {"kind": "TEXT", "value": "Neue Straße 2"}
                old_street = {"kind": "TEXT", "value": "Alte Straße 1"}
                mea = {"kind": "MEA", "numerator": 47732, "denominator": 1000000}
                opaque_assignment_unit = {"kind": "TEXT", "value": "unit:2"}
                unit_area = {"kind": "DECIMAL", "value": "50", "unit": "m²"}
                unit_heated_area = {"kind": "DECIMAL", "value": "50.0", "unit": "m²"}
                legal_district = {"kind": "TEXT", "value": "Düsseldorf"}
                legal_details = {"kind": "TEXT", "value": "Akte 17"}
                document_relation = {"relation_type": "AMENDS", "target_document_key": str(main.id)}
                tie_one = {"kind": "TEXT", "value": "1"}
                tie_two = {"kind": "TEXT", "value": "2"}
                session.add_all([
                    ApprovedFactSource(organization_id=org_id, property_id=property_row.id, subject_kind="PROPERTY", subject_id=property_row.id, field_key="street", approved_value_json=current_street, value_fingerprint=value_fingerprint(current_street), status="EFFECTIVE", valid_from=now, property_document_id=amendment.id, pdf_page=1, section="§ 1", quote="Neue Straße 2", source_import_id=item.id, search_text="Objekt Straße Neue Straße 2"),
                    ApprovedFactSource(organization_id=org_id, property_id=property_row.id, subject_kind="PROPERTY", subject_id=property_row.id, field_key="street", approved_value_json=old_street, value_fingerprint=value_fingerprint(old_street), status="SUPERSEDED", valid_from=now - timedelta(days=2), valid_to=now - timedelta(days=1), property_document_id=main.id, pdf_page=1, section="§ 1", quote="Alte Straße 1", source_import_id=item.id, search_text="Objekt Straße Alte Straße 1"),
                    ApprovedFactSource(organization_id=org_id, property_id=property_row.id, subject_kind="UNIT", subject_id=unit_one.id, field_key="mea", approved_value_json=mea, value_fingerprint=value_fingerprint(mea), status="EFFECTIVE", valid_from=now, property_document_id=main.id, pdf_page=2, section="§ 3", quote="Einheit 1 mit 47.732/1.000.000", source_import_id=item.id, search_text="Einheit 1 Miteigentumsanteil 47.732/1.000.000"),
                    ApprovedFactSource(organization_id=org_id, property_id=property_row.id, subject_kind="UNIT", subject_id=unit_one.id, field_key="area", approved_value_json=unit_area, value_fingerprint=value_fingerprint(unit_area), status="EFFECTIVE", valid_from=now, property_document_id=main.id, pdf_page=2, section="§ 3", quote="Einheit 1 mit 50 m²", source_import_id=item.id, search_text="Einheit 1 Wohnfläche 50 m²"),
                    ApprovedFactSource(organization_id=org_id, property_id=property_row.id, subject_kind="UNIT", subject_id=unit_one.id, field_key="heated_area", approved_value_json=unit_heated_area, value_fingerprint=value_fingerprint(unit_heated_area), status="EFFECTIVE", valid_from=now, property_document_id=main.id, pdf_page=2, section="§ 3", quote="Einheit 1 mit 50,0 m² beheizter Fläche", source_import_id=item.id, search_text="Einheit 1 Beheizte Fläche 50.0 m²"),
                    ApprovedFactSource(organization_id=org_id, property_id=property_row.id, subject_kind="LEGAL_REFERENCE", subject_id=legal.id, field_key="district", approved_value_json=legal_district, value_fingerprint=value_fingerprint(legal_district), status="EFFECTIVE", valid_from=now, property_document_id=main.id, pdf_page=3, section="§ 5", quote="Amtsgericht Düsseldorf", source_import_id=item.id, search_text="Rechtsreferenz Amtsgericht Düsseldorf"),
                    ApprovedFactSource(organization_id=org_id, property_id=property_row.id, subject_kind="LEGAL_REFERENCE", subject_id=legal.id, field_key="details", approved_value_json=legal_details, value_fingerprint=value_fingerprint(legal_details), status="EFFECTIVE", valid_from=now, property_document_id=main.id, pdf_page=3, section="§ 5", quote="Akte 17", source_import_id=item.id, search_text="Rechtsreferenz Details Akte 17"),
                    ApprovedFactSource(organization_id=org_id, property_id=property_row.id, subject_kind="DOCUMENT_RELATION", subject_id=relation.id, field_key="relation", approved_value_json=document_relation, value_fingerprint=value_fingerprint(document_relation), status="EFFECTIVE", valid_from=now, property_document_id=amendment.id, pdf_page=1, section="§ 1", quote="Der Nachtrag ändert die Teilungserklärung.", source_import_id=item.id, search_text="Dokumentbeziehung AMENDS"),
                    ApprovedFactSource(organization_id=org_id, property_id=property_row.id, subject_kind="ASSIGNMENT", subject_id=assignment.id, field_key="unit_subject_key", approved_value_json=opaque_assignment_unit, value_fingerprint=value_fingerprint(opaque_assignment_unit), status="EFFECTIVE", valid_from=now, property_document_id=main.id, pdf_page=4, section="§ 4", quote="Zuordnung zu unit:2", source_import_id=item.id, search_text="Zuordnung Einheit unit:2"),
                    ApprovedFactSource(organization_id=org_id, property_id=property_row.id, subject_kind="UNIT", subject_id=unit_one.id, field_key="number", approved_value_json=tie_one, value_fingerprint=value_fingerprint(tie_one), status="EFFECTIVE", valid_from=now, property_document_id=main.id, pdf_page=2, section="§ 3", quote="Einheit 1", source_import_id=item.id, search_text="Gleichstand"),
                    ApprovedFactSource(organization_id=org_id, property_id=property_row.id, subject_kind="UNIT", subject_id=unit_two.id, field_key="number", approved_value_json=tie_two, value_fingerprint=value_fingerprint(tie_two), status="EFFECTIVE", valid_from=now, property_document_id=main.id, pdf_page=2, section="§ 3", quote="Einheit 2", source_import_id=item.id, search_text="Gleichstand"),
                ])
        foreign_engine = create_async_engine(MIGRATOR_URL)
        try:
            async with async_sessionmaker(foreign_engine, class_=AsyncSession)() as foreign_session:
                async with foreign_session.begin():
                    foreign_session.add(Property(
                        id=created["foreign"], organization_id=seed_data["org_b_id"],
                        name="Fremdes Objekt", street="Anderer Weg 1", zip_code="10115",
                        city="Berlin", management_type="WEG",
                    ))
        finally:
            await foreign_engine.dispose()
        yield SimpleNamespace(organization_id=org_id, property_id=created["property"], foreign_property_id=created["foreign"], unknown_property_id=uuid.uuid4(), legal_id=created["legal"], heating_rule_id=created["heating"], elevator_old_id=created["elevator_old"], elevator_new_id=created["elevator_new"], tie_unit_ids=(created["unit_one"], created["unit_two"]), current_street_fingerprint=value_fingerprint({"kind": "TEXT", "value": "Neue Straße 2"}))
    finally:
        await engine.dispose()
        cleanup_engine = create_async_engine(MIGRATOR_URL)
        try:
            async with async_sessionmaker(cleanup_engine, class_=AsyncSession)() as cleanup:
                async with cleanup.begin():
                    registered = await fixture_has_registered_documents(
                        cleanup, [created["main"], created["amendment"]]
                    )
                    # Registered originals are retained until the guarded session reset.
                    # Do not bypass their append-only protection for fixture teardown.
                    if not registered:
                        for statement in (
                            "DELETE FROM approved_fact_source WHERE source_import_id = :import_id",
                            "DELETE FROM property_rule_source WHERE property_rule_id IN (:heating, :old, :new, :repair)",
                            "DELETE FROM property_rule_relation WHERE source_import_id = :import_id",
                            "DELETE FROM property_rule WHERE source_import_id = :import_id",
                            "DELETE FROM property_document_relation WHERE source_import_id = :import_id",
                            "DELETE FROM property_assignment WHERE source_import_id = :import_id",
                            "DELETE FROM property_legal_reference WHERE source_import_id = :import_id",
                            "DELETE FROM declaration_import WHERE id = :import_id",
                            "DELETE FROM property_document WHERE id IN (:main, :amendment)",
                            "DELETE FROM unit WHERE id IN (:unit_one, :unit_two)",
                            "DELETE FROM property WHERE id IN (:property_id, :foreign)",
                        ):
                            await cleanup.execute(
                                text(statement),
                                {
                                    **created,
                                    "import_id": created["import"],
                                    "property_id": created["property"],
                                    "heating": created["heating"],
                                    "old": created["elevator_old"],
                                    "new": created["elevator_new"],
                                    "repair": created["repair"],
                                },
                            )
        finally:
            await cleanup_engine.dispose()
