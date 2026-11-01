"""In-process database diagnostics, including a deliberately timed-out child."""
from contextlib import closing
import json
from pathlib import Path
import re
from types import SimpleNamespace
import uuid

import psycopg2
import pytest

from tests import conftest as harness
from tests.bdd.world import target_url

pytest_plugins = ['pytester']
pytestmark = pytest.mark.skipif(bool(target_url()), reason='requires the in-process test database')


@pytest.fixture(scope='session')
def run_migrations():
    """Advisory-lock probes need no schema or destructive migration rehearsal."""


@pytest.fixture
def inject_test_engine():
    """Only explicitly opened disposable connections are used."""


def test_raw_lock_timeout_dumps_before_the_holder_transaction_is_closed(capfd):
    statement = f"SELECT pg_advisory_xact_lock(hashtextextended('raw-diagnostic:{uuid.uuid4()}',0))"
    with closing(psycopg2.connect(harness.MIGRATOR_SYNC_URL)) as holder:
        with holder.cursor() as cursor:
            cursor.execute(statement)
        with closing(psycopg2.connect(harness.MIGRATOR_SYNC_URL)) as waiter:
            with waiter.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout='200ms'")
                with pytest.raises(psycopg2.errors.LockNotAvailable) as failure:
                    cursor.execute(statement)
            assert failure.value.pgcode == '55P03'
            diagnostic = capfd.readouterr().err
            assert f'pid={waiter.get_backend_pid()} waiting_statement={statement!r}' in diagnostic
            activity = json.loads(diagnostic.split('pg_stat_activity=', 1)[1].splitlines()[0])
            assert any(row['pid'] == holder.get_backend_pid() and row['xact_age'] and row['query'] == statement
                       and row['state'] == 'idle in transaction' for row in activity)
            locks = json.loads(diagnostic.split('pg_locks=', 1)[1].splitlines()[0])
            assert any(row['pid'] == holder.get_backend_pid() and row['locktype'] == 'advisory' for row in locks)


def test_report_does_not_replace_an_immediate_dump_with_an_empty_one(capfd, monkeypatch):
    from app.core.locking import LockTimeoutError

    def late_dump(*args, **kwargs):
        pytest.fail('report tried to dump after the holder could have closed')
    monkeypatch.setattr(harness, 'dump_test_lock_timeout', late_dump)
    item = SimpleNamespace(nodeid='synthetic::teardown', _lock_dump_emitted=True)
    call = SimpleNamespace(excinfo=SimpleNamespace(value=LockTimeoutError()))
    list(harness.pytest_runtest_makereport(item, call))
    assert 'synthetic::teardown: lock timeout dump already emitted' in capfd.readouterr().err


def test_pytest_timeout_dumps_database_activity_with_the_test_id(pytester, monkeypatch):
    # The child alone accesses the DB; it never migrates/truncates the parent's data.
    monkeypatch.setenv('PYTHONPATH', str(Path(__file__).resolve().parents[1]))
    pytester.makeconftest('''
import pytest
@pytest.fixture(scope='session')
def run_migrations():
    pass
@pytest.fixture
def inject_test_engine():
    pass
''')
    probe = pytester.makepyfile(test_timeout_probe='''
import sys
import psycopg2
import pytest
from tests.conftest import MIGRATOR_SYNC_URL

@pytest.mark.timeout(2, method='thread')
def test_blocked_transaction():
    holder = psycopg2.connect(MIGRATOR_SYNC_URL)
    waiter = psycopg2.connect(MIGRATOR_SYNC_URL)
    print(f'timeout_holder_pid={holder.get_backend_pid()}', file=sys.stderr, flush=True)
    with holder.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(180181)')
    with waiter.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout='0'")
        cursor.execute('SELECT pg_advisory_xact_lock(180181)')
''')
    result = pytester.runpytest_subprocess('-p', 'tests.conftest', '-p', 'no:cacheprovider',
                                         f'{probe}::test_blocked_transaction', '-x', '-q', timeout=30)
    assert result.ret == 1
    captured = result.stdout.str() + result.stderr.str()
    holder_pid = int(re.search(r'timeout_holder_pid=(\d+)', captured)[1])
    diagnostic = result.stderr.str()
    assert 'test_timeout_probe.py::test_blocked_transaction' in diagnostic
    assert 'pg_stat_activity=' in diagnostic
    activity = json.loads(diagnostic.split('pg_stat_activity=', 1)[1].splitlines()[0])
    assert any(row['pid'] == holder_pid for row in activity)
    assert all({'state', 'wait_event_type', 'wait_event', 'xact_age', 'query', 'blocking_pids'} <= row.keys()
               for row in activity)
    locks = json.loads(diagnostic.split('pg_locks=', 1)[1].splitlines()[0])
    assert any(row['pid'] == holder_pid for row in locks)


LEAKY_FIXTURES = {
    'session': '''
@pytest_asyncio.fixture
async def leaky(engine_url):
    engine = create_async_engine(engine_url)
    try:
        async with AsyncSession(engine) as db:
            yield db
    finally:
        await engine.dispose()
''',
    'connection': '''
@pytest_asyncio.fixture
async def leaky(engine_url):
    engine = create_async_engine(engine_url)
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()
''',
    'psycopg2': '''
class _Raw:
    def __init__(self, connection):
        self.connection = connection
    async def execute(self, statement):
        with self.connection.cursor() as cursor:
            cursor.execute(str(statement))

@pytest.fixture
def leaky():
    connection = psycopg2.connect(MIGRATOR_SYNC_URL)
    try:
        yield _Raw(connection)
    finally:
        connection.close()
''',
}


@pytest.mark.parametrize('guard, leak', [('on', 'session'), ('on', 'connection'), ('on', 'psycopg2'), ('off', 'session')])
def test_open_transactions_end_before_fixture_teardown(pytester, monkeypatch, guard, leak):
    # The child alone accesses the DB; it never migrates/truncates the parent's data.
    monkeypatch.setenv('PYTHONPATH', str(Path(__file__).resolve().parents[1]))
    if guard == 'off':
        monkeypatch.setenv('HV_TEST_KEEP_OPEN_TRANSACTIONS', '1')
    else:
        monkeypatch.delenv('HV_TEST_KEEP_OPEN_TRANSACTIONS', raising=False)
    pytester.makeconftest('''
import pytest
@pytest.fixture(scope='session')
def run_migrations():
    pass
@pytest.fixture
def inject_test_engine():
    pass
''')
    probe = pytester.makepyfile(test_leak_probe='''
import psycopg2
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tests.conftest import MIGRATOR_SYNC_URL, MIGRATOR_URL

@pytest.fixture
def engine_url():
    return MIGRATOR_URL
''' + LEAKY_FIXTURES[leak] + '''

@pytest.fixture
def rehearsal():
    yield
    # Like a migration rehearsal or a TRUNCATE teardown: needs the table exclusively.
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute('LOCK TABLE person IN ACCESS EXCLUSIVE MODE NOWAIT')
        connection.rollback()

@pytest.mark.asyncio
async def test_body_fails_with_an_open_transaction(leaky, rehearsal):
    await leaky.execute(text('SELECT count(*) FROM person'))
    assert False, 'the test body fails while its transaction is open'
''')
    result = pytester.runpytest_subprocess('-p', 'tests.conftest', '-p', 'no:cacheprovider',
                                         f'{probe}::test_body_fails_with_an_open_transaction', '-q', timeout=60)
    assert result.ret == 1
    summary = result.stdout.str()
    assert 'the test body fails while its transaction is open' in summary
    if guard == 'off':
        assert '1 failed, 1 error' in summary and 'LockNotAvailable' in summary
    else:
        assert '1 failed' in summary and ' error' not in summary and 'LockNotAvailable' not in summary
        assert f'rolled back 1 open test transaction(s) before teardown: {leak}' in summary
