"""Source authorization must never depend on a mutable parent or creator."""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.db import get_tenant_session
from app.models.declaration_candidate import DeclarationCandidate, DeclarationCandidateEvidence, DeclarationCandidateIssue
from app.models.declaration_import import DeclarationExtractionRun
from app.models.property import Property
from app.models.unit import Unit
from tests.test_declaration_pipeline import _seed_pipeline_import, make_text_pdf

pytestmark = pytest.mark.asyncio


async def graph(seed_data):
    lease, document, _ = await _seed_pipeline_import(seed_data, make_text_pdf('Synthetic context'))
    async with get_tenant_session(lease.organization_id) as db:
        prop = Property(organization_id=lease.organization_id, name='Source context', management_type='WEG')
        db.add(prop)
        await db.flush()
        unit = Unit(organization_id=lease.organization_id, property_id=prop.id, number='1', usage_type='WOHNUNG')
        candidate = DeclarationCandidate(organization_id=lease.organization_id, import_id=lease.import_id,
            stable_key='source-context', candidate_type='PROPERTY_FIELD', subject_key='property:new',
            field_key='name', operation='CREATE', proposed_value_json={'kind':'TEXT','value':'Test'},
            confidence=Decimal('.9'), risk_class='LOW')
        run = DeclarationExtractionRun(organization_id=lease.organization_id, import_id=lease.import_id,
            provider='test', model_name='test', prompt_version='test', schema_version='test', harness_version='test')
        db.add_all([unit,candidate,run])
        await db.flush()
        evidence = DeclarationCandidateEvidence(organization_id=lease.organization_id,
            candidate_id=candidate.id,document_id=uuid.UUID(document),pdf_page=1,quote='Synthetic')
        issue = DeclarationCandidateIssue(organization_id=lease.organization_id,import_id=lease.import_id,
            candidate_id=candidate.id,code='TEST',severity='WARNING',message='Synthetic')
        db.add_all([evidence,issue])
        await db.flush()
        link = await db.scalar(text('SELECT id FROM declaration_import_document WHERE import_id=:id'),{'id':lease.import_id})
        return lease.organization_id, {
            'property':prop.id,'unit':unit.id,'property_document':uuid.UUID(document),
            'declaration_import':lease.import_id,'declaration_import_document':link,
            'declaration_candidate':candidate.id,'declaration_candidate_evidence':evidence.id,
            'declaration_candidate_issue':issue.id,'declaration_extraction_run':run.id,
        }


@pytest.mark.parametrize('table,column',[
    ('property','id'),('property','organization_id'),('unit','property_id'),
    ('property_document','uploaded_by'),('property_document','id'),
    ('declaration_import','mode'),('declaration_import','target_property_id'),
    ('declaration_import','initiated_by'),('declaration_import_document','import_id'),
    ('declaration_import_document','document_id'),('declaration_candidate','import_id'),
    ('declaration_candidate_evidence','candidate_id'),('declaration_candidate_evidence','document_id'),
    ('declaration_candidate_issue','import_id'),('declaration_candidate_issue','candidate_id'),
    ('declaration_extraction_run','import_id'),
])
async def test_source_context_cannot_be_reparented(seed_data,table,column):
    org,rows = await graph(seed_data)
    value = 'EXISTING_PROPERTY' if column == 'mode' else uuid.uuid4()
    async with get_tenant_session(org) as db:
        with pytest.raises(DBAPIError,match='source context is immutable'):
            async with db.begin_nested():
                await db.execute(text(f'UPDATE {table} SET {column}=:value WHERE id=:id'),{'id':rows[table],'value':value})
        # Routine metadata/lifecycle updates and no-op context assignments remain legal.
        await db.execute(text(f'UPDATE {table} SET {column}={column} WHERE id=:id'),{'id':rows[table]})
        await db.execute(text("UPDATE property_document SET title='Reviewed title' WHERE id=:id"),{'id':rows['property_document']})
        await db.execute(text("UPDATE declaration_import SET progress_percent=1 WHERE id=:id"),{'id':rows['declaration_import']})


async def test_unknown_historical_uploader_is_not_inferred(seed_data):
    from app.models.property_document import PropertyDocument
    org=seed_data['org_a_id']
    async with get_tenant_session(org) as db:
        document=PropertyDocument(organization_id=org,doc_type='DECLARATION',title='Historical',
            original_filename='history.pdf',mime_type='application/pdf',content_byte_size=1,
            content_sha256='a'*64,storage_uri='synthetic/history',storage_backend='TEST',page_count=1)
        db.add(document)
        await db.flush()
        with pytest.raises(DBAPIError,match='source context is immutable'):
            async with db.begin_nested():
                await db.execute(text('UPDATE property_document SET uploaded_by=:actor WHERE id=:id'),
                    {'actor':seed_data['user_a_admin_id'],'id':document.id})


@pytest.mark.parametrize('column,value',[
    ('applied_property_id',None),('approved_by',None),('approved_at',None),
    ('approval_idempotency_key','changed'),('approval_result_json','{}'),('status','NEEDS_REVIEW'),
])
async def test_applied_destination_and_receipt_are_frozen(seed_data,column,value):
    org,rows=await graph(seed_data)
    async with get_tenant_session(org) as db:
        await db.execute(text("UPDATE declaration_import SET status='APPLIED',applied_property_id=:property,"
            "approved_by=:actor,approved_at=clock_timestamp(),approval_idempotency_key='context-test',"
            "approval_result_json='{}' WHERE id=:id"),
            {'property':rows['property'],'actor':seed_data['user_a_admin_id'],'id':rows['declaration_import']})
        # The normal first publication binds the preserved original exactly once.
        await db.execute(text('UPDATE property_document SET property_id=:property WHERE id=:id'),
            {'property':rows['property'],'id':rows['property_document']})
        if column=='approval_result_json':
            value='{"changed":true}'
        with pytest.raises(DBAPIError,match='applied context is immutable'):
            async with db.begin_nested():
                await db.execute(text(f'UPDATE declaration_import SET {column}=:value WHERE id=:id'),
                    {'value':value,'id':rows['declaration_import']})


async def test_existing_import_cannot_publish_to_another_property(seed_data):
    org,rows=await graph(seed_data)
    lease,_,_=await _seed_pipeline_import(seed_data,make_text_pdf('Existing target'),
        mode='EXISTING_PROPERTY',target_property_id=rows['property'])
    async with get_tenant_session(org) as db:
        other=Property(organization_id=org,name='Other destination',management_type='WEG')
        db.add(other)
        await db.flush()
        with pytest.raises(DBAPIError,match='applied property must equal its existing target'):
            async with db.begin_nested():
                await db.execute(text("UPDATE declaration_import SET status='APPLIED',applied_property_id=:property,"
                    "approved_by=:actor,approved_at=clock_timestamp(),approval_idempotency_key='bad-target',"
                    "approval_result_json='{}' WHERE id=:id"),
                    {'property':other.id,'actor':seed_data['user_a_admin_id'],'id':lease.import_id})


async def test_source_guard_migration_preserves_populated_context(seed_data):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    await graph(seed_data)
    migration=importlib.import_module('migrations.versions.0047_property_source_context')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(DBAPIError,match='cannot remove populated'):
                    with connection.begin_nested():
                        migration.downgrade()
                nested=connection.begin_nested()
                try:
                    # Disposable nested rollback proves empty downgrade/re-upgrade;
                    # committed source rows and their guards survive the rehearsal.
                    connection.execute(text('TRUNCATE '+','.join(migration.CONTEXT)+' CASCADE'))
                    migration.downgrade()
                    assert connection.scalar(text("SELECT to_regprocedure('guard_property_source_context()')")) is None
                    migration.upgrade()
                    assert connection.scalar(text("SELECT count(*) FROM pg_trigger WHERE tgname='a_guard_property_source_context'"))==len(migration.CONTEXT)
                finally:
                    nested.rollback()
    finally:
        engine.dispose()
