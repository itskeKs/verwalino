"""0197 adds the metering ledger of the model-assisted invoice path (issue #500, ADR 0017)."""
import importlib
import uuid
from decimal import Decimal

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app import deployment
from tests.conftest import MIGRATOR_SYNC_URL
from tests.invoice_model_support import discard_invoice_model_data

LEDGER_ROW = """INSERT INTO invoice_model_call(id,organization_id,attempt_id,stage,pdf_page,model_name,reserved_input_tokens,
    reserved_output_tokens,reserved_cost_usd) VALUES (:call,:org,:attempt,:stage,:page,'m',100,50,0.5)"""


def _migration():
    return importlib.import_module('migrations.versions.0197_invoice_model_calls')


def _seed_attempt(db, seed_data, processor_version=None):
    """An invoice original with one attempt of the first organization; returns the attempt's id."""
    document, attempt = uuid.uuid4(), uuid.uuid4()
    db.execute(text("""INSERT INTO invoice_document(id,organization_id,storage_uri,storage_backend,mime_type,content_byte_size,
        content_sha256,version,chain_root_id,status) VALUES (:doc,:org,'synthetic/upgrade.pdf','LOCAL_FS','application/pdf',4,:hash,1,:doc,'active')"""),
        {'doc': document, 'org': seed_data['org_a_id'], 'hash': 'd' * 64})
    db.execute(text("""INSERT INTO invoice_processing_attempt(id,organization_id,document_id,initiated_by,attempt_no,status,processor_version)
        VALUES (:attempt,:org,:doc,:user,1,'queued',:version)"""),
        {'attempt': attempt, 'org': seed_data['org_a_id'], 'doc': document, 'user': seed_data['user_a_admin_id'], 'version': processor_version})
    return attempt


def test_the_invoice_ledger_is_the_release_target_after_0196():
    revision = ScriptDirectory('migrations').get_revision('0197')
    assert revision.down_revision == '0196'
    assert deployment.TARGET == '0197'
    assert ScriptDirectory('migrations').get_heads() == ['0197']


def test_the_ledger_table_is_isolated_forced_and_erasable():
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as db:
            flags = db.execute(text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid='public.invoice_model_call'::regclass")).one()
            assert tuple(flags) == (True, True)
            policies = {row[0]: row[1] for row in db.execute(text("SELECT policyname, cmd FROM pg_policies WHERE tablename='invoice_model_call'"))}
            assert policies == {'invoice_model_call_read': 'SELECT', 'invoice_model_call_insert': 'INSERT',
                                'invoice_model_call_update': 'UPDATE', 'invoice_model_call_delete': 'DELETE'}
            privileges = {row[0] for row in db.execute(text("""SELECT privilege_type FROM information_schema.table_privileges
                WHERE table_name='invoice_model_call' AND grantee='app_rw'"""))}
            assert privileges == {'SELECT', 'INSERT', 'UPDATE', 'DELETE'}
            others = db.execute(text("""SELECT grantee FROM information_schema.table_privileges WHERE table_name='invoice_model_call'
                AND grantee IN ('app_review','PUBLIC')""")).all()
            assert others == []
            # The retention catalog classifies the ledger erasable: it holds no DELETE trigger of its own.
            assert db.scalar(text("SELECT disposition FROM public.organization_retention_catalog() WHERE entity='invoice_model_call'")) == 'delete'
            assert db.scalar(text("""SELECT count(*) FROM pg_trigger WHERE tgrelid='public.invoice_model_call'::regclass AND NOT tgisinternal
                AND (tgtype & 8) > 0 AND tgfoid::regproc::text <> 'organization_lifecycle_guard'""")) == 0
    finally:
        engine.dispose()


def test_a_reserved_call_is_settled_once_and_its_reservation_never_changes(seed_data):
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db:
            savepoint = db.begin_nested()
            try:
                attempt, call = _seed_attempt(db, seed_data), uuid.uuid4()
                db.execute(text("SELECT set_config('app.current_org_id',:org,true)"), {'org': str(seed_data['org_a_id'])})
                db.execute(text(LEDGER_ROW), {'call': call, 'org': seed_data['org_a_id'], 'attempt': attempt, 'stage': 'read_page', 'page': 1})
                # The table owner may do anything; the runtime roles are held to the guard.
                db.execute(text("SET LOCAL SESSION AUTHORIZATION app_worker"))
                db.execute(text("SELECT set_config('app.current_org_id',:org,true)"), {'org': str(seed_data['org_a_id'])})
                with pytest.raises(DBAPIError, match='settled once'):
                    with db.begin_nested():
                        db.execute(text("UPDATE invoice_model_call SET reserved_cost_usd=0 WHERE id=:call"), {'call': call})
                db.execute(text("""UPDATE invoice_model_call SET status='SETTLED',input_tokens=80,output_tokens=40,cost_usd=0.1,
                    usage_complete=true,settled_at=now() WHERE id=:call"""), {'call': call})
                with pytest.raises(DBAPIError, match='settled once'):
                    with db.begin_nested():
                        db.execute(text("UPDATE invoice_model_call SET cost_usd=0 WHERE id=:call"), {'call': call})
            finally:
                savepoint.rollback()
    finally:
        engine.dispose()


@pytest.mark.parametrize('stage,page', [('read_page', None), ('extract_invoice', 1), ('summarize', None)], ids=['page-needs-a-number', 'extraction-has-none', 'unknown-stage'])
def test_the_ledger_refuses_a_stage_without_its_page_identity(seed_data, stage, page):
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db:
            savepoint = db.begin_nested()
            try:
                attempt = _seed_attempt(db, seed_data)
                with pytest.raises(DBAPIError):
                    with db.begin_nested():
                        db.execute(text(LEDGER_ROW), {'call': uuid.uuid4(), 'org': seed_data['org_a_id'], 'attempt': attempt, 'stage': stage, 'page': page})
            finally:
                savepoint.rollback()
    finally:
        engine.dispose()


def test_the_monthly_total_counts_a_reservation_and_a_settlement_and_downgrade_restores_the_old_total(seed_data):
    migration = _migration()
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            savepoint = db.begin_nested()
            try:
                discard_invoice_model_data(lambda statement: db.execute(text(statement)))
                attempt = _seed_attempt(db, seed_data)
                db.execute(text("SELECT set_config('app.current_org_id',:org,true)"), {'org': str(seed_data['org_a_id'])})
                def total():
                    # The function answers the worker role (or a member of the organization), not the migrator.
                    db.execute(text("SET LOCAL SESSION AUTHORIZATION app_worker"))
                    try:
                        return db.scalar(text("SELECT organization_ai_monthly_usage(:org, ARRAY['gpt-5.4-mini'])"), {'org': seed_data['org_a_id']})
                    finally:
                        db.execute(text("RESET SESSION AUTHORIZATION"))
                before = total()
                reserved, settled = uuid.uuid4(), uuid.uuid4()
                db.execute(text(LEDGER_ROW), {'call': reserved, 'org': seed_data['org_a_id'], 'attempt': attempt, 'stage': 'read_page', 'page': 1})
                assert total() == before + Decimal('0.5'), 'a reservation counts at its reserved cost'
                db.execute(text(LEDGER_ROW), {'call': settled, 'org': seed_data['org_a_id'], 'attempt': attempt, 'stage': 'read_page', 'page': 2})
                db.execute(text("""UPDATE invoice_model_call SET status='SETTLED',input_tokens=80,output_tokens=40,cost_usd=0.125,
                    usage_complete=true,settled_at=now() WHERE id=:call"""), {'call': settled})
                assert total() == before + Decimal('0.5') + Decimal('0.125'), 'a settled call counts at what it consumed'
                with pytest.raises(DBAPIError, match='Invoice model call records require this release'):
                    with db.begin_nested():
                        migration.downgrade()
                db.execute(text('DELETE FROM invoice_model_call'))
                migration.downgrade()
                assert db.scalar(text("SELECT to_regclass('public.invoice_model_call')")) is None
                assert 'invoice_model_call' not in db.scalar(text("SELECT pg_get_functiondef('public.organization_ai_monthly_usage(uuid,text[])'::regprocedure)"))
                migration.upgrade()
                assert db.scalar(text("SELECT to_regclass('public.invoice_model_call')")) is not None
                assert 'invoice_model_call' in db.scalar(text("SELECT pg_get_functiondef('public.organization_ai_monthly_usage(uuid,text[])'::regprocedure)"))
            finally:
                savepoint.rollback()
    finally:
        engine.dispose()


def test_a_model_read_proposal_is_accepted_for_an_invoice_attempt_and_downgrade_refuses_to_orphan_one(seed_data):
    migration = _migration()
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            savepoint = db.begin_nested()
            try:
                discard_invoice_model_data(lambda statement: db.execute(text(statement)))
                constraint = lambda: db.scalar(text("""SELECT pg_get_constraintdef(oid) FROM pg_constraint
                    WHERE conname='extraction_revision_typed_attempt'"""))
                record = lambda: db.scalar(text("SELECT pg_get_functiondef('public.record_invoice_extraction_revision(uuid,uuid,uuid,jsonb)'::regprocedure)"))
                assert 'invoice-model-v1' in constraint() and 'invoice-model-v1' in record()
                assert 'intake-local-v1' in constraint(), 'the inbox processor version is untouched'
                assert "key<>'vendor_iban'" in record() and "IF field.key NOT IN ('vendor_iban'," in record(), 'the tenth key and its certainty'
                intake = db.scalar(text("SELECT pg_get_functiondef(oid) FROM pg_proc WHERE proname='complete_intake_extraction'"))
                assert 'vendor_iban' not in intake, 'the inbox keeps the nine fields'
                _seed_attempt(db, seed_data, processor_version='invoice-model-v1')
                with pytest.raises(DBAPIError, match='Model-read invoice proposals require this release'):
                    with db.begin_nested():
                        migration.downgrade()
                db.execute(text("UPDATE invoice_processing_attempt SET processor_version='invoice-local-v1' WHERE processor_version='invoice-model-v1'"))
                migration.downgrade()
                assert 'invoice-model-v1' not in constraint() and 'invoice-model-v1' not in record() and 'vendor_iban' not in record()
                assert 'invoice-local-v1' in constraint() and 'invoice-local-v1' in record() and 'intake-local-v1' in constraint()
                migration.upgrade()
                assert 'invoice-model-v1' in constraint() and 'invoice-model-v1' in record() and "key<>'vendor_iban'" in record()
            finally:
                savepoint.rollback()
    finally:
        engine.dispose()
