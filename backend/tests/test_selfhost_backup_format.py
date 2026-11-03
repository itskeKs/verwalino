"""Opt-in archive round trip in scratch databases in the allowed test container.

Run `make test-selfhost-format`; the target opts in and owns the test DB lock.
Ordinary suites skip this rehearsal, including when Docker is unavailable.
"""
import os
from contextlib import closing
from decimal import Decimal
import shutil
import subprocess
import tarfile
import uuid
from types import SimpleNamespace

import psycopg2
import pytest
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.core.declaration_test_database import require_disposable_database_settings
from app.selfhost_backup import discard_documents, pack, stage_documents, swap_documents, verify

pytestmark = [
    pytest.mark.skipif(os.environ.get('VERWALINO_FORMAT_TEST') != '1',
                       reason='Opt-in rehearsal: run make test-selfhost-format (sets VERWALINO_FORMAT_TEST=1 and takes the lock)'),
    pytest.mark.skipif(shutil.which('docker') is None, reason='Docker is required for the format rehearsal'),
]


@pytest.fixture(scope='session', autouse=True)
def run_migrations(request):
    # The format process never loads the regular conftest's migrations or engine.
    if not request.config.getoption('noconftest'):
        raise pytest.UsageError('Format rehearsal requires --noconftest; use make test-selfhost-format')
    require_disposable_database_settings(settings)


@pytest.fixture
def scratch_databases(monkeypatch):
    _, _, sync_url = require_disposable_database_settings(settings)
    port = subprocess.run(['docker', 'port', 'infra-postgres-1', '5432'],
                          check=True, capture_output=True, text=True).stdout.strip()
    assert port == '127.0.0.1:55432'
    created = []

    def execute(*args, **kwargs):
        return subprocess.run(['docker', 'exec', '-i', 'infra-postgres-1', *args],
                              check=True, capture_output=True, **kwargs)

    def docker(*args, **kwargs):
        if args[0] not in ('psql', 'pg_dump', 'pg_restore') or args[args.index('-d') + 1] not in created:
            raise RuntimeError('Format commands must target a database created by this test')
        return execute(*args, **kwargs)

    def sql(database, statement):
        return docker('psql', '-X', '--set', 'ON_ERROR_STOP=1', '-U', 'app_migrator',
                      '-d', database, '-Atc', statement).stdout.decode().strip()

    def create():
        database = 'selfhost_format_' + uuid.uuid4().hex
        execute('createdb', '-U', 'app_migrator', '-T', 'template0', database)
        created.append(database)
        assert sql(database, "SELECT current_database() || '|' || pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()") == database + '|app_migrator'
        assert sql(database, "SELECT count(*) FROM pg_class WHERE relnamespace='public'::regnamespace AND relkind='r'") == '0'
        return database

    def validate_connection(dsn=None, **kwargs):
        parsed = psycopg2.extensions.parse_dsn(dsn) if dsn else {}
        parsed.update(kwargs)
        if ((parsed.get('host'), int(parsed.get('port', 0))) != ('127.0.0.1', 55432)
                or parsed.get('dbname', parsed.get('database')) not in created):
            raise RuntimeError('Format connections must target a database created by this test')

    connect = psycopg2.connect

    def scratch_connect(dsn=None, *args, **kwargs):
        validate_connection(dsn, **kwargs)
        kwargs.setdefault('options', '-c lock_timeout=5s -c statement_timeout=120s')
        return connect(dsn, *args, **kwargs)

    import asyncpg
    async_connect = asyncpg.connect

    async def scratch_async_connect(dsn=None, *args, **kwargs):
        validate_connection(dsn, **kwargs)
        kwargs['server_settings'] = {**kwargs.get('server_settings', {}),
            'lock_timeout': '5s', 'statement_timeout': '120s'}
        return await async_connect(dsn, *args, **kwargs)

    monkeypatch.setattr(psycopg2, 'connect', scratch_connect)
    monkeypatch.setattr(asyncpg, 'connect', scratch_async_connect)
    try:
        yield SimpleNamespace(create=create, docker=docker, sql=sql,
            url=lambda database: make_url(sync_url).set(database=database).render_as_string(hide_password=False))
    finally:
        for database in reversed(created):
            execute('dropdb', '-U', 'app_migrator', database)


def test_custom_dump_and_paired_documents_survive_scratch_database_restore(tmp_path, monkeypatch, scratch_databases):
    docker, sql = scratch_databases.docker, scratch_databases.sql
    ownership = []
    monkeypatch.setattr('app.selfhost_backup.os.chown', lambda *args: ownership.append(args))
    rows = ['Backup round trip', 'Ümlauts and spaces', 'Long organization ' + 'x' * 180]
    source = scratch_databases.create()
    sql(source, "CREATE TABLE alembic_version(version_num varchar(32) PRIMARY KEY); "
                "INSERT INTO alembic_version VALUES ('0139'); "
                "CREATE TABLE organization(id integer PRIMARY KEY, name text)")
    for number, name in enumerate(rows):
        sql(source, f"INSERT INTO organization VALUES ({number}, '{name}')")
    schema = sql(source, 'SELECT version_num FROM alembic_version')
    documents = tmp_path / 'documents'
    (documents / 'nested').mkdir(parents=True)
    (documents / 'nested/original.pdf').write_bytes(b'synthetic retained original\x00\xff')
    (documents / '.hidden').write_bytes(b'also retained')
    work = tmp_path / 'backup'
    work.mkdir()
    dump = docker('pg_dump', '-U', 'app_migrator', '-d', source, '-Fc').stdout
    assert dump.startswith(b'PGDMP')
    (work / 'db.dump').write_bytes(dump)
    pack(work, documents, schema, 'verwalino:local', 'a' * 64)
    restored = tmp_path / 'restore'
    restored.mkdir()
    (restored / 'archive.tar').write_bytes((work / 'archive.tar').read_bytes())
    manifest = verify(restored, schema, 'a' * 64)
    with tarfile.open(restored / 'archive.tar') as archive:
        assert set(archive.getnames()) == {'db.dump', 'documents.tar.gz', 'manifest.json'}
    destination = scratch_databases.create()
    docker('pg_restore', '-U', 'app_migrator', '-d', destination,
           '--exit-on-error', '--single-transaction', input=(restored / 'db.dump').read_bytes())
    assert sql(destination, 'SELECT name FROM organization ORDER BY id').splitlines() == rows
    assert sql(destination, 'SELECT version_num FROM alembic_version') == manifest['schema_head']
    (documents / 'nested/original.pdf').unlink()
    (documents / 'not-in-backup').write_bytes(b'remove on restore')
    stage_documents(restored, documents)
    temporary = documents / (restored / 'documents-stage').read_text()
    swap_documents(restored, documents)
    assert (documents / 'nested/original.pdf').read_bytes() == b'synthetic retained original\x00\xff'
    assert (documents / '.hidden').read_bytes() == b'also retained'
    assert not (documents / 'not-in-backup').exists()
    assert (temporary / 'previous/not-in-backup').read_bytes() == b'remove on restore'
    discard_documents(restored, documents)
    assert not temporary.exists()
    assert ownership and all(call[1:] == (10001, 10001) for call in ownership)


@pytest.mark.asyncio
async def test_scratch_harness_refuses_shared_and_unowned_database_connections(scratch_databases):
    import asyncpg

    _, _, shared_url = require_disposable_database_settings(settings)
    for database in (make_url(shared_url).database, 'selfhost_format_not_owned'):
        url = make_url(shared_url).set(database=database).render_as_string(hide_password=False)
        with pytest.raises(RuntimeError, match='created by this test'):
            psycopg2.connect(url)
        with pytest.raises(RuntimeError, match='created by this test'):
            await asyncpg.connect(url)
        with pytest.raises(RuntimeError, match='created by this test'):
            scratch_databases.docker('pg_dump', '-U', 'app_migrator', '-d', database, '-Fc')


@pytest.mark.asyncio
async def test_reserve_pair_survives_backup_restore_with_replica_guard(tmp_path, monkeypatch, scratch_databases):
    import httpx
    from pydantic import SecretStr
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, text
    from sqlalchemy.ext.asyncio import create_async_engine
    from app import deployment
    from app.core import db
    from app.core.security import create_session_token
    from app.models.base import Base

    monkeypatch.setenv('HV_ERROR_TRACKING_DSN', '')
    monkeypatch.setattr(settings, 'error_tracking_dsn', SecretStr(''))
    monkeypatch.setattr('sentry_sdk.init', lambda **kwargs: pytest.fail('Format rehearsal must not initialize outbound tracking'))
    from app.main import app
    from tests import test_m4_weg

    source = scratch_databases.create()
    source_url = scratch_databases.url(source)
    graph = ScriptDirectory('migrations')
    assert deployment.release_head(graph) == deployment.TARGET
    org, actor = str(uuid.uuid4()), str(uuid.uuid4())
    engine = create_engine(source_url)
    try:
        with engine.begin() as connection:
            assert connection.scalar(text('SELECT current_database()')) == source
            membership = connection.execute(text('SELECT * FROM pg_auth_members ORDER BY roleid,member,grantor')).all()
            context = MigrationContext.configure(connection, opts={'target_metadata': Base.metadata})
            with Operations.context(context):
                for revision in reversed(list(graph.iterate_revisions(deployment.TARGET, 'base'))):
                    revision.module.upgrade()
                context.stamp(graph, deployment.TARGET)
            assert connection.execute(text('SELECT * FROM pg_auth_members ORDER BY roleid,member,grantor')).all() == membership
            connection.execute(text("INSERT INTO organization(id,name) VALUES (:org,'Synthetic reserve restore')"), {'org': org})
            connection.execute(text('''INSERT INTO "user"(id,organization_id,email,password_hash,display_name)
                VALUES (:actor,:org,'restore@example.invalid','unused synthetic hash','Restore operator')'''),
                {'org': org, 'actor': actor})
            connection.execute(text("INSERT INTO membership(organization_id,user_id,role) VALUES (:org,:actor,'OWNER_ADMIN')"),
                {'org': org, 'actor': actor})
    finally:
        engine.dispose()

    # Reuse the real WEG fixture and API flow, bound only to the owned source.
    monkeypatch.setattr(test_m4_weg, 'SYNC_URL', source_url)
    w = test_m4_weg._seed_weg({'org_a_id': org, 'user_a_admin_id': actor})
    _, runtime_url, _ = require_disposable_database_settings(settings)
    runtime = make_url(runtime_url).set(database=source)
    engines = [create_async_engine(runtime),
        create_async_engine(runtime.set(username='app_worker', password='worker_secret')),
        create_async_engine(runtime.set(username='app_review', password='review_secret'))]
    db.override_engine(engines[0])
    db.override_worker_engine(engines[1])
    db.override_review_engine(engines[2])
    try:
        token = create_session_token(uuid.UUID(actor), uuid.UUID(org))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                cookies={settings.session_cookie_name: token}) as api:
            assert (await api.put(f"/api/units/{w['u1']}", json={'number': '3'})).status_code == 200
            payment = await api.post('/api/payments', json={'bank_account_id': w['bank_giro'],
                'direction': 'IN', 'amount': '1300.00', 'value_date': '2025-02-01',
                'payment_type': 'HAUSGELD_VORSCHUSS', 'owner_profile_id': w['owner_a'],
                'period_year': 2025, 'period_month': 2})
            assert payment.status_code == 201, payment.text
            assert (await api.post(f"/api/payments/{payment.json()['id']}/book")).status_code == 200
            report = await api.post('/api/weg-settlements/asset-reports', json={
                'property_id': w['property'], 'period_start': '2025-01-01', 'period_end': '2025-12-31'})
            assert report.status_code == 201, report.text
            w['run'] = report.json()['id']
            path = f"/api/weg-settlements/{w['run']}/reserve-ledger"
            cash = await api.post(path + '/overrides', json={'component': 'CONTRIBUTION',
                'payment_id': payment.json()['id'], 'amount': '100.00', 'reason': 'Partial cash assignment'})
            assert cash.status_code == 201, cash.text
            await test_m4_weg._create_and_finalize_plan(api, w)
            original = await api.post(path + '/overrides', json={
                'component': 'CONTRIBUTION', 'unit_id': w['u1'], 'amount': '300.00',
                'reason': 'Reconcile unit 3 contribution for restore rehearsal'})
            assert original.status_code == 201, original.text
            original_id = original.json()['id']
            counter = await api.post(path + f'/overrides/{original_id}/reduce', json={
                'reduced_to': '100.00', 'reason': 'Correct unit 3 contribution to 100 euros'})
            assert counter.status_code == 201, counter.text
            pair = [original_id, counter.json()['id']]
    finally:
        db.override_review_engine(None)
        db.override_worker_engine(None)
        db.override_engine(None)
        for engine in engines:
            await engine.dispose()

    rows_sql = """SELECT e.id::text,e.organization_id::text,e.property_id::text,
        e.settlement_run_id::text,e.unit_id::text,e.reverses_entry_id::text,e.amount,u.number
        FROM reserve_ledger_entry e LEFT JOIN unit u ON u.id=e.unit_id
        AND u.organization_id=e.organization_id WHERE e.id=ANY(%s::uuid[]) ORDER BY e.amount DESC"""
    context = (org, str(w['property']), str(w['run']), str(w['u1']))
    expected = [(pair[0], *context, None, Decimal('300.00'), '3'),
                (pair[1], *context, pair[0], Decimal('-200.00'), '3')]
    with closing(psycopg2.connect(source_url)) as connection, connection.cursor() as cursor:
        cursor.execute(rows_sql, (pair,))
        assert cursor.fetchall() == expected

    documents, work, staging = (tmp_path / name for name in ('documents', 'backup', 'restore'))
    for directory in (documents, work, staging):
        directory.mkdir()
    (documents / 'synthetic.txt').write_text('Synthetic unit 3 reserve backup')
    (work / 'db.dump').write_bytes(scratch_databases.docker('pg_dump', '-U', 'app_migrator', '-d', source, '-Fc').stdout)
    pack(work, documents, deployment.TARGET, 'verwalino:local', 'a' * 64)
    (staging / 'archive.tar').write_bytes((work / 'archive.tar').read_bytes())
    assert verify(staging, deployment.TARGET, 'a' * 64)['schema_head'] == deployment.TARGET
    destination = scratch_databases.create()
    scratch_databases.docker('pg_restore', '-U', 'app_migrator', '-d', destination,
        '--exit-on-error', '--single-transaction', input=(staging / 'db.dump').read_bytes())
    with closing(psycopg2.connect(scratch_databases.url(destination))) as restored, restored.cursor() as cursor:
        cursor.execute('SELECT current_database(),version_num FROM alembic_version')
        assert cursor.fetchone() == (destination, deployment.TARGET)
        cursor.execute(rows_sql, (pair,))
        actual = cursor.fetchall()
        assert actual == expected
        assert sum(row[6] for row in actual) == Decimal('100.00')
        cursor.execute("SELECT tgenabled::text FROM pg_trigger WHERE tgrelid='reserve_ledger_entry'::regclass AND tgname='guard_reserve_ledger'")
        assert cursor.fetchone() == ('A',)
        cursor.execute('SELECT current_user,session_user,rolsuper FROM pg_roles WHERE rolname=current_user')
        assert cursor.fetchone() == ('app_migrator', 'app_migrator', True)
        cursor.execute("SET LOCAL session_replication_role='replica'")
        cursor.execute('SHOW session_replication_role')
        assert cursor.fetchone() == ('replica',)
        with pytest.raises(psycopg2.errors.CheckViolation) as refused:
            cursor.execute('DELETE FROM reserve_ledger_entry WHERE id=ANY(%s::uuid[])', (pair,))
        assert refused.value.pgcode == '23514'
        assert refused.value.diag.constraint_name == 'reserve_ledger_append_only'
        assert 'GoBD: reserve ledger is append-only' in refused.value.diag.message_primary
        restored.rollback()
        cursor.execute(rows_sql, (pair,))
        assert cursor.fetchall() == expected
