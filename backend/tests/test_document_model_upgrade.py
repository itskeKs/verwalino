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


def _discard_reading_ledger(db):
    """Other tests leave committed synthetic reading-pass records; the downgrade refuses them by design."""
    db.execute(text("DELETE FROM declaration_import_pass WHERE safe_metadata->>'pass_id'='read_page'"))


def _migration():
    return importlib.import_module('migrations.versions.0196_ai_document_model')


def test_the_reading_model_is_the_release_target_after_0195():
    revision = ScriptDirectory('migrations').get_revision('0196')
    assert revision.down_revision == '0195'
    assert deployment.TARGET == '0206'


def test_upgrade_keeps_the_extraction_model_and_never_chooses_a_reading_model(seed_data):
    migration = _migration()
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            savepoint = db.begin_nested()
            try:
                _discard_reading_ledger(db)
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
                _discard_reading_ledger(db)
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


# ---- the reading stage's persistence: ledger validator, page transcripts, job model identity (#499) ---------------

READ_PAGE_METADATA = {
    'pass_id': 'read_page', 'attempt_no': 1, 'stage': 'provider', 'configured_model': 'gpt-5.4-mini',
    'returned_model_id': 'gpt-5.4-mini', 'reasoning_effort': 'none', 'request_id': None,
    'prompt_version': 'reading-v1', 'prompt_sha256': '0' * 64, 'schema_version': 'reading-1', 'schema_sha256': '1' * 64,
    'harness_version': '4', 'harness_sha256': '2' * 64, 'input_tokens': 10, 'cached_input_tokens': 0,
    'cache_write_input_tokens': 0, 'output_tokens': 5, 'estimated_cost_usd': '0.000001', 'latency_ms': 1, 'retry_count': 0,
    'route_action': 'NONE', 'route_reason_codes': [], 'failure_class': None,
}


def _valid(db, metadata):
    import json
    return db.scalar(text('SELECT declaration_import_pass_safe_metadata_is_valid(CAST(:metadata AS jsonb), 1)'),
                     {'metadata': json.dumps(metadata)})


def _seed_read_page_row(db, seed_data):
    import json
    import uuid
    job, run = uuid.uuid4(), uuid.uuid4()
    db.execute(text("""INSERT INTO declaration_import(id,organization_id,mode,initiated_by,max_cost_usd)
        VALUES (:job,:org,'NEW_PROPERTY',:user,1)"""), {'job': job, 'org': seed_data['org_a_id'], 'user': seed_data['user_a_admin_id']})
    db.execute(text("""INSERT INTO declaration_extraction_run(id,organization_id,import_id,provider,model_name,document_model_name,
        prompt_version,schema_version,harness_version) VALUES (:run,:org,:job,'fake','m','r','p','s','h')"""),
        {'run': run, 'org': seed_data['org_a_id'], 'job': job})
    db.execute(text("""INSERT INTO declaration_import_pass(organization_id,declaration_extraction_run_id,attempt_no,safe_metadata)
        VALUES (:org,:run,1,CAST(:metadata AS jsonb))"""),
        {'org': seed_data['org_a_id'], 'run': run, 'metadata': json.dumps(READ_PAGE_METADATA)})


def test_upgrade_accepts_a_read_page_ledger_row_and_downgrade_restores_the_old_validator(seed_data):
    migration = _migration()
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            savepoint = db.begin_nested()
            try:
                assert _valid(db, READ_PAGE_METADATA) is True
                assert _valid(db, READ_PAGE_METADATA | {'pass_id': 'inventory'}) is True
                assert _valid(db, READ_PAGE_METADATA | {'pass_id': 'unknown_pass'}) is False
                _seed_read_page_row(db, seed_data)
                # With a reading record stored the older runtime could not read it: the downgrade refuses.
                with pytest.raises(DBAPIError, match='Reading pass records require this release'):
                    with db.begin_nested():
                        migration.downgrade()
                db.execute(text("DELETE FROM declaration_import_pass WHERE safe_metadata->>'pass_id'='read_page'"))
                migration.downgrade()
                assert _valid(db, READ_PAGE_METADATA) is False, 'the old validator does not know the reading pass'
                assert _valid(db, READ_PAGE_METADATA | {'pass_id': 'recheck'}) is True
                assert db.scalar(text("SELECT to_regclass('public.declaration_page_reading')")) is None
                assert db.scalar(text("""SELECT count(*) FROM information_schema.columns
                    WHERE table_name='declaration_extraction_run' AND column_name='document_model_name'""")) == 0
                migration.upgrade()
                assert _valid(db, READ_PAGE_METADATA) is True
            finally:
                savepoint.rollback()
    finally:
        engine.dispose()


def _monthly_usage_definition(db):
    return db.scalar(text("SELECT pg_get_functiondef('public.organization_ai_monthly_usage(uuid,text[])'::regprocedure)"))


def test_upgrade_holds_a_running_import_by_its_reading_model_too_and_downgrade_restores_the_older_definition(seed_data):
    migration = _migration()
    engine = create_engine(MIGRATOR_SYNC_URL)
    reading_openai = "r.document_model_name=ANY(priced_models)"
    reading_anthropic = "r.document_model_name IN ('claude-haiku-4-5','claude-haiku-4-5-20251001')"
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            savepoint = db.begin_nested()
            try:
                current = _monthly_usage_definition(db)
                assert reading_openai in current and reading_anthropic in current
                # The provider guard of 0179 stays around both models.
                assert "(r.provider='OpenAiDeclarationProvider' AND (r.model_name=ANY(priced_models) OR " in current
                assert "(r.provider='AnthropicDeclarationProvider' AND (r.model_name IN " in current
                _discard_reading_ledger(db)
                db.execute(text('UPDATE organization_ai_settings SET document_model=NULL'))
                migration.downgrade()
                older = _monthly_usage_definition(db)
                assert 'document_model_name' not in older
                assert ("r.status='RUNNING' AND ((r.provider='OpenAiDeclarationProvider' AND r.model_name=ANY(priced_models)) "
                        "OR (r.provider='AnthropicDeclarationProvider' AND r.model_name IN "
                        "('claude-haiku-4-5','claude-haiku-4-5-20251001')))") in older
                migration.upgrade()
                assert _monthly_usage_definition(db) == current
            finally:
                savepoint.rollback()
    finally:
        engine.dispose()


def test_the_page_transcript_table_is_worker_fenced_isolated_and_follows_the_import(seed_data):
    with create_engine(MIGRATOR_SYNC_URL).begin() as db:
        savepoint = db.begin_nested()
        try:
            _seed_read_page_row(db, seed_data)
            job = db.scalar(text('SELECT import_id FROM declaration_extraction_run WHERE document_model_name=:r'), {'r': 'r'})
            policies = {row[0]: (row[1], row[2]) for row in db.execute(text("""SELECT policyname,cmd,roles::text
                FROM pg_policies WHERE tablename='declaration_page_reading'"""))}
            assert set(policies) == {'declaration_page_reading_read', 'declaration_page_reading_insert',
                                     'declaration_page_reading_delete'}
            assert db.scalar(text("""SELECT relrowsecurity AND relforcerowsecurity FROM pg_class
                WHERE oid='public.declaration_page_reading'::regclass""")) is True
            # Every insert is fenced to the live lease by the worker effect receipt, like the other import outputs.
            assert db.scalar(text("""SELECT count(*) FROM pg_trigger WHERE tgrelid='public.declaration_page_reading'::regclass
                AND tgname IN ('g_guard_page_reading','a_organization_lifecycle') AND NOT tgisinternal""")) == 2
            assert job is not None
        finally:
            savepoint.rollback()
