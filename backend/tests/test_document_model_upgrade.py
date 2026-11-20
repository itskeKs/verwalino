"""0196 adds the nullable reading model of the organization AI settings (issue #499, ADR 0017)."""
import importlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app import deployment
from tests.conftest import MIGRATOR_SYNC_URL

COLUMNS = 'provider,base_url,request_timeout_seconds,encrypted_api_key,model,import_enabled,qa_enabled,monthly_limit_usd'


def _migration():
    return importlib.import_module('migrations.versions.0196_ai_document_model')


def test_the_reading_model_is_the_release_target_after_0195():
    revision = ScriptDirectory('migrations').get_revision('0196')
    assert revision.down_revision == '0195'
    assert deployment.TARGET == '0196'


def test_upgrade_keeps_the_extraction_model_and_never_chooses_a_reading_model(seed_data):
    migration = _migration()
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            savepoint = db.begin_nested()
            try:
                migration.downgrade()
                db.execute(text("""INSERT INTO organization_ai_settings(organization_id,provider,model,import_enabled,qa_enabled,
                    monthly_limit_usd,encrypted_api_key,request_timeout_seconds)
                    VALUES (:org,'openai','gpt-5.6-luna',true,true,5,'synthetic-ciphertext',90)"""), {'org': seed_data['org_a_id']})
                before = db.execute(text(f'SELECT {COLUMNS} FROM organization_ai_settings WHERE organization_id=:org'),
                                    {'org': seed_data['org_a_id']}).one()
                migration.upgrade()
                after = db.execute(text(f'SELECT {COLUMNS},document_model FROM organization_ai_settings WHERE organization_id=:org'),
                                   {'org': seed_data['org_a_id']}).one()
                # The extraction choice and every other column are as they were; the reading model is unset.
                assert tuple(after[:-1]) == tuple(before) and after[4] == 'gpt-5.6-luna'
                assert after[-1] is None
            finally:
                savepoint.rollback()
    finally:
        engine.dispose()


def test_downgrade_refuses_to_discard_a_chosen_reading_model_and_otherwise_drops_the_column(seed_data):
    migration = _migration()
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            savepoint = db.begin_nested()
            try:
                db.execute(text("""INSERT INTO organization_ai_settings(organization_id,model,document_model)
                    VALUES (:org,'gpt-5.6-luna','gpt-5.4-mini')"""), {'org': seed_data['org_a_id']})
                with pytest.raises(DBAPIError, match='Configured document reading models require this release'):
                    with db.begin_nested():
                        migration.downgrade()
                assert db.scalar(text('SELECT document_model FROM organization_ai_settings WHERE organization_id=:org'),
                                 {'org': seed_data['org_a_id']}) == 'gpt-5.4-mini'
                db.execute(text('UPDATE organization_ai_settings SET document_model=NULL WHERE organization_id=:org'),
                           {'org': seed_data['org_a_id']})
                migration.downgrade()
                assert db.scalar(text("""SELECT count(*) FROM information_schema.columns
                    WHERE table_name='organization_ai_settings' AND column_name='document_model'""")) == 0
            finally:
                savepoint.rollback()
    finally:
        engine.dispose()
