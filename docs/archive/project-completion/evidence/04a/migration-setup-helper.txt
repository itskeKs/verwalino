"""Isolated historical rehearsals without changing the guarded database URL."""

from contextlib import closing
from pathlib import Path
import shutil
import uuid

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import psycopg2
from psycopg2 import sql
import pytest
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.core.declaration_test_database import require_disposable_database_settings


@pytest.fixture
def legacy_auth_database(tmp_path, run_migrations):
    """Preserve the shared head while rehearsing the original 0028 schema boundary."""
    _, _, sync_url = require_disposable_database_settings(settings)
    backend = Path(__file__).resolve().parents[1]
    scripts = tmp_path / 'migrations'
    (scripts / 'versions').mkdir(parents=True)
    shutil.copy2(backend / 'migrations' / 'env.py', scripts / 'env.py')
    history = ScriptDirectory(str(backend / 'migrations'))
    for revision in history.iterate_revisions('0028', 'base'):
        shutil.copy2(revision.path, scripts / 'versions' / Path(revision.path).name)

    config = Config(str(backend / 'alembic.ini'))
    config.set_main_option('script_location', str(scripts))
    config.set_main_option('sqlalchemy.url', sync_url)
    config.attributes['hv_declaration_test_database'] = True

    saved_name = 'hausverwaltung_legacy_saved_' + uuid.uuid4().hex
    control_url = make_url(sync_url).set(database='postgres').render_as_string(hide_password=False)
    with closing(psycopg2.connect(control_url)) as control:
        control.autocommit = True
        with control.cursor() as cursor:
            cursor.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='hausverwaltung'")
            if cursor.fetchone() != ('app_migrator',):
                raise RuntimeError('Historical rehearsal requires the owned disposable database')

            def disconnect():
                cursor.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                               "WHERE datname='hausverwaltung' AND pid<>pg_backend_pid()")

            # Keep the original DB, including every preceding test's synthetic evidence.
            # Only the empty rehearsal DB is discarded, even when a migration fails.
            disconnect()
            cursor.execute(sql.SQL('ALTER DATABASE hausverwaltung RENAME TO {}').format(sql.Identifier(saved_name)))
            created = False
            try:
                cursor.execute('CREATE DATABASE hausverwaltung OWNER app_migrator TEMPLATE template0')
                created = True
                command.upgrade(config, 'head')
                yield scripts
            finally:
                if created:
                    disconnect()
                    cursor.execute('DROP DATABASE hausverwaltung')
                cursor.execute(sql.SQL('ALTER DATABASE {} RENAME TO hausverwaltung').format(sql.Identifier(saved_name)))
