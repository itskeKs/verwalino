"""Opt-in archive round trip in scratch databases in the allowed test container.

Run `make test-selfhost-format`; the target opts in and owns the test DB lock.
Ordinary suites skip this rehearsal, including when Docker is unavailable.
"""
import os
import shutil
import subprocess
import tarfile
import uuid

import pytest

from app.core.config import settings
from app.core.declaration_test_database import require_disposable_database_settings
from app.selfhost_backup import pack, replace_documents, verify

pytestmark = [
    pytest.mark.skipif(os.environ.get('VERWALINO_FORMAT_TEST') != '1',
                       reason='Opt-in rehearsal: run make test-selfhost-format (sets VERWALINO_FORMAT_TEST=1 and takes the lock)'),
    pytest.mark.skipif(shutil.which('docker') is None, reason='Docker is required for the format rehearsal'),
]


@pytest.fixture(scope='session', autouse=True)
def run_migrations():
    # Only scratch databases are written; preserve the shared database's schema.
    require_disposable_database_settings(settings)


def test_custom_dump_and_paired_documents_survive_scratch_database_restore(tmp_path, monkeypatch):
    port = subprocess.run(['docker', 'port', 'infra-postgres-1', '5432'],
                          check=True, capture_output=True, text=True).stdout.strip()
    assert port == '127.0.0.1:55432'

    def docker(*args, **kwargs):
        return subprocess.run(['docker', 'exec', '-i', 'infra-postgres-1', *args],
                              check=True, capture_output=True, **kwargs)

    def sql(database, statement):
        return docker('psql', '-X', '--set', 'ON_ERROR_STOP=1', '-U', 'app_migrator',
                      '-d', database, '-Atc', statement).stdout.decode().strip()

    ownership = []
    monkeypatch.setattr('app.selfhost_backup.os.chown', lambda *args: ownership.append(args))
    source = 'selfhost_format_' + uuid.uuid4().hex
    destination = source + '_restore'
    created = []
    rows = ['Backup round trip', 'Ümlauts and spaces', 'Long organization ' + 'x' * 180]
    try:
        docker('createdb', '-U', 'app_migrator', '-T', 'template0', source)
        created.append(source)
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
        docker('createdb', '-U', 'app_migrator', '-T', 'template0', destination)
        created.append(destination)
        docker('pg_restore', '-U', 'app_migrator', '-d', destination,
               '--exit-on-error', '--single-transaction', input=(restored / 'db.dump').read_bytes())
        assert sql(destination, 'SELECT name FROM organization ORDER BY id').splitlines() == rows
        assert sql(destination, 'SELECT version_num FROM alembic_version') == manifest['schema_head']
        (documents / 'nested/original.pdf').unlink()
        (documents / 'not-in-backup').write_bytes(b'remove on restore')
        replace_documents(restored, documents)
        assert (documents / 'nested/original.pdf').read_bytes() == b'synthetic retained original\x00\xff'
        assert (documents / '.hidden').read_bytes() == b'also retained'
        assert not (documents / 'not-in-backup').exists()
        assert ownership and all(call[1:] == (10001, 10001) for call in ownership)
    finally:
        for database in reversed(created):
            docker('dropdb', '-U', 'app_migrator', '--if-exists', database)
