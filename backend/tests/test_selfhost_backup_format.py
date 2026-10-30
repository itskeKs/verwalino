"""Real archive round trip, exclusively on the locked disposable test database.

Run separately from the repository root (ordinary suites skip without this lock):
    until mkdir /tmp/verwalino-testdb.lock 2>/dev/null; do sleep 10; done
    trap 'rmdir /tmp/verwalino-testdb.lock' EXIT
    source scripts/test-env.sh
    cd backend && uv run pytest -q tests/test_selfhost_backup_format.py
"""
from pathlib import Path
import subprocess
import tarfile
import uuid

import psycopg2
import pytest

from app.core.config import settings
from app.core.declaration_test_database import require_disposable_database_settings
from app.selfhost_backup import pack, replace_documents, verify


@pytest.fixture(scope='session', autouse=True)
def run_migrations():
    # This format rehearsal preserves whatever schema another checkout left in
    # the shared test DB; it must not downgrade it to this checkout's target.
    require_disposable_database_settings(settings)


def test_custom_dump_and_paired_documents_survive_schema_wipe(tmp_path, monkeypatch):
    _, _, url = require_disposable_database_settings(settings)
    if not Path('/tmp/verwalino-testdb.lock').is_dir():
        pytest.skip('Standalone format rehearsal requires the documented test DB lock')
    port = subprocess.run(['docker', 'port', 'infra-postgres-1', '5432'],
                          check=True, capture_output=True, text=True).stdout.strip()
    assert port == '127.0.0.1:55432'

    def docker(*args, **kwargs):
        return subprocess.run(['docker', 'exec', '-i', 'infra-postgres-1', *args],
                              check=True, capture_output=True, **kwargs)

    def sql(statement):
        return docker('psql', '-X', '--set', 'ON_ERROR_STOP=1', '-U', 'app_migrator',
                      '-d', 'hausverwaltung', '-Atc', statement).stdout.decode().strip()

    # Keep root-only container ownership changes out of the local filesystem.
    ownership = []
    monkeypatch.setattr('app.selfhost_backup.os.chown', lambda *args: ownership.append(args))
    schema = sql('SELECT version_num FROM alembic_version')
    rows = [(str(uuid.uuid4()), name) for name in
            ('Backup round trip', 'Ümlauts and spaces', 'Long organization ' + 'x' * 180)]
    with psycopg2.connect(url) as connection, connection.cursor() as cursor:
        cursor.executemany('INSERT INTO organization(id,name) VALUES (%s,%s)', rows)
    documents = tmp_path / 'documents'
    documents.mkdir()
    (documents / 'nested').mkdir()
    (documents / 'nested/original.pdf').write_bytes(b'synthetic retained original\x00\xff')
    (documents / '.hidden').write_bytes(b'also retained')
    work = tmp_path / 'backup'
    work.mkdir()
    dump = docker('pg_dump', '-U', 'app_migrator', '-d', 'hausverwaltung', '-Fc').stdout
    assert dump.startswith(b'PGDMP')
    (work / 'db.dump').write_bytes(dump)
    pack(work, documents, schema, 'verwalino:local', 'a' * 64)
    restored = tmp_path / 'restore'
    restored.mkdir()
    (restored / 'archive.tar').write_bytes((work / 'archive.tar').read_bytes())
    verify(restored, schema, 'a' * 64)
    with tarfile.open(restored / 'archive.tar') as archive:
        assert set(archive.getnames()) == {'db.dump', 'documents.tar.gz', 'manifest.json'}
    # Match the empty public schema supplied by createdb -T template0.
    sql('DROP SCHEMA public CASCADE; CREATE SCHEMA public AUTHORIZATION pg_database_owner')
    # Match self-host.sh's restore arguments exactly, including failure/atomicity.
    docker('pg_restore', '-U', 'app_migrator', '-d', 'hausverwaltung',
           '--exit-on-error', '--single-transaction', input=(restored / 'db.dump').read_bytes())
    with psycopg2.connect(url) as connection, connection.cursor() as cursor:
        cursor.execute('SELECT id::text,name FROM organization WHERE id=ANY(%s::uuid[]) ORDER BY name',
                       ([row[0] for row in rows],))
        assert cursor.fetchall() == sorted(rows, key=lambda row: row[1])
        cursor.execute('DELETE FROM organization WHERE id=ANY(%s::uuid[])', ([row[0] for row in rows],))
    assert sql('SELECT version_num FROM alembic_version') == schema
    (documents / 'nested/original.pdf').unlink()
    (documents / 'not-in-backup').write_bytes(b'remove on restore')
    replace_documents(restored, documents)
    assert (documents / 'nested/original.pdf').read_bytes() == b'synthetic retained original\x00\xff'
    assert (documents / '.hidden').read_bytes() == b'also retained'
    assert not (documents / 'not-in-backup').exists()
    assert ownership and all(call[1:] == (10001, 10001) for call in ownership)
