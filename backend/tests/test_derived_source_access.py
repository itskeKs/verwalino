"""Derived facts, review rows and stored Q&A retain the original source audience."""
import json
import uuid
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.core.db import get_tenant_session
from tests.test_financial_scope_boundary import scope_seed, worker_tenant  # noqa: F401
from tests.test_scoped_account_statements import client
from tests.test_property_source_context import graph
from tests.test_property_source_enforcement import synthetic_enforcement, change_mode
from tests.test_knowledge_source_enforcement import granted_graph
from tests.test_declaration_import_api import isolated_document_store, enable_declaration_imports  # noqa: F401

pytestmark=pytest.mark.asyncio
CANONICAL=('approved_fact_source','property_legal_reference','property_assignment','property_rule',
           'property_rule_source','property_rule_relation','property_document_relation')


@pytest_asyncio.fixture
async def committed_scope(scope_seed,migrator_session):
    await migrator_session.commit()
    return scope_seed


async def actor_session(db,actor):
    await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(actor)})


async def test_review_rows_are_hidden_from_other_creator_and_pause_blocks_raw_changes(committed_scope):
    seed=committed_scope
    org,rows=await graph(seed)
    from tests.test_declaration_pipeline import _safe_attempt_metadata
    async with get_tenant_session(org) as db:
        rows['declaration_queue_request']=await db.scalar(text('SELECT queue_request_id FROM declaration_import WHERE id=:id'),{'id':rows['declaration_import']})
        rows['declaration_import_pass']=await db.scalar(text('INSERT INTO declaration_import_pass(organization_id,declaration_extraction_run_id,attempt_no,safe_metadata) VALUES(:org,:run,1,CAST(:metadata AS jsonb)) RETURNING id'),
            {'org':org,'run':rows['declaration_extraction_run'],'metadata':json.dumps(_safe_attempt_metadata().as_safe_dict())})
    tables=[name for name in rows if name not in ('property','unit','property_document','declaration_import')]
    async with synthetic_enforcement(seed),get_tenant_session(org) as db:
        await actor_session(db,seed['user_a_clerk_id'])
        for table in tables:
            assert await db.scalar(text(f'SELECT count(*) FROM {table} WHERE id=:id'),{'id':rows[table]})==0,table
        await actor_session(db,seed['user_a_admin_id'])
        for table in tables:
            assert await db.scalar(text(f'SELECT count(*) FROM {table} WHERE id=:id'),{'id':rows[table]})==1,table
        with pytest.raises(DBAPIError,match='Atomic declaration review command required'):
            async with db.begin_nested():
                await db.execute(text("UPDATE declaration_candidate SET review_comment='Current creator' WHERE id=:id"),
                    {'id':rows['declaration_candidate']})
    async with synthetic_enforcement(seed):
        await change_mode(seed,'PAUSED')
        async with get_tenant_session(org) as db:
            await actor_session(db,seed['user_a_admin_id'])
            assert await db.scalar(text('SELECT count(*) FROM declaration_candidate WHERE id=:id'),{'id':rows['declaration_candidate']})==1
            assert (await db.execute(text("UPDATE declaration_candidate SET review_comment='Paused' WHERE id=:id"),
                {'id':rows['declaration_candidate']})).rowcount==0


async def test_review_api_does_not_treat_hidden_candidates_as_an_empty_draft(committed_scope):
    seed=committed_scope
    org,rows=await graph(seed)
    ids={'org':org,'actor':seed['user_a_admin_id'],'clerk':seed['user_a_clerk_id']}
    async with synthetic_enforcement(seed),client(ids) as other:
        assert (await other.get(f"/api/declaration-imports/{rows['declaration_import']}/candidates")).status_code==404


async def test_derived_rows_are_not_readable_without_current_source_authority(approved_knowledge_graph,seed_data):
    source=approved_knowledge_graph
    async with get_tenant_session(source.organization_id) as db:
        for table in CANONICAL:
            assert await db.scalar(text(f'SELECT count(*) FROM {table} WHERE property_id=:property'),{'property':source.property_id})>0,table
    async with synthetic_enforcement(seed_data),get_tenant_session(source.organization_id) as db:
        await actor_session(db,seed_data['user_a_admin_id'])
        for table in CANONICAL:
            assert await db.scalar(text(f'SELECT count(*) FROM {table} WHERE property_id=:property'),{'property':source.property_id})==0,table


async def test_read_only_source_grants_cannot_insert_or_change_approved_facts(approved_knowledge_graph,seed_data,client_a_admin):
    source=approved_knowledge_graph
    async with granted_graph(client_a_admin,seed_data,source):
        async with synthetic_enforcement(seed_data),get_tenant_session(source.organization_id) as db:
            await actor_session(db,seed_data['user_a_admin_id'])
            for table in CANONICAL:
                record=await db.scalar(text(f'SELECT to_jsonb(r) FROM {table} r WHERE property_id=:property LIMIT 1'),{'property':source.property_id})
                assert record,table
                writable=await db.scalar(text("SELECT attname FROM pg_attribute WHERE attrelid=CAST(:table AS regclass) AND attnum>0 AND NOT attisdropped AND has_column_privilege('app_rw',attrelid,attnum,'UPDATE') ORDER BY attnum LIMIT 1"),{'table':table})
                if writable:
                    assert (await db.execute(text(f'UPDATE {table} SET {writable}={writable} WHERE property_id=:property'),{'property':source.property_id})).rowcount==0,table
                columns=(await db.scalars(text("SELECT attname FROM pg_attribute WHERE attrelid=CAST(:table AS regclass) AND attnum>0 AND NOT attisdropped AND attgenerated='' ORDER BY attnum"),{'table':table})).all()
                column_list=','.join(columns)
                with pytest.raises(DBAPIError) as denied:
                    async with db.begin_nested():
                        await db.execute(text(f'INSERT INTO {table} ({column_list}) SELECT {column_list} FROM jsonb_populate_record(NULL::{table},CAST(:record AS jsonb))'),
                            {'record':json.dumps({**record,'id':str(uuid.uuid4())})})
                assert denied.value.orig.sqlstate=='42501',table


async def test_strict_upload_and_pipeline_create_visible_children(committed_scope):
    from tests.test_declaration_job_principal import new_draft
    from tests.test_declaration_pipeline import _seed_pipeline_import, _RecordingProvider, make_text_pdf
    from app.declarations.pipeline import DeclarationPipeline
    seed=committed_scope
    ids={'org':seed['org_a_id'],'actor':seed['user_a_admin_id'],'clerk':seed['user_a_clerk_id']}
    async with synthetic_enforcement(seed):
        job=await new_draft(ids)
        async with client(ids) as other:
            assert (await other.get(f'/api/declaration-imports/{job}/documents')).status_code==404
        # A claimed worker carries the immutable queue principal into its short
        # transactions; insert/returning cannot depend on a preexisting child.
        async with get_tenant_session(ids['org']) as db:
            await actor_session(db,ids['actor'])
            assert await db.scalar(text('SELECT count(*) FROM declaration_import_document WHERE import_id=:job'),{'job':uuid.UUID(job)})==1
    lease,key,store=await _seed_pipeline_import(seed,make_text_pdf('Synthetic strict extraction'))
    async with synthetic_enforcement(seed):
        await DeclarationPipeline(_RecordingProvider(key),store=store).run(lease)
        async with get_tenant_session(ids['org']) as db:
            await actor_session(db,ids['actor'])
            assert await db.scalar(text('SELECT count(*) FROM declaration_candidate WHERE import_id=:job'),{'job':lease.import_id})>0
            assert await db.scalar(text('SELECT count(*) FROM declaration_extraction_run WHERE import_id=:job'),{'job':lease.import_id})>0


async def test_denied_direct_upload_never_writes_storage(committed_scope):
    from app.services.property_document_service import PropertyDocumentService
    from fastapi import HTTPException
    seed=committed_scope
    org,rows=await graph(seed)
    class ForbiddenStore:
        def put(self,*args,**kwargs): raise AssertionError('Denied source upload reached storage')
    async with synthetic_enforcement(seed),get_tenant_session(org) as db:
        await actor_session(db,seed['user_a_clerk_id'])
        with pytest.raises(HTTPException) as denied:
            await PropertyDocumentService(db,org,seed['user_a_clerk_id']).upload(rows['declaration_import'],b'%PDF-1.4',
                original_filename='denied.pdf',mime_type='application/pdf',document_role='ANNEX',sequence_no=2,store=ForbiddenStore())
        assert denied.value.status_code==404


async def test_stored_answer_reads_survive_pause_but_not_revocation(approved_knowledge_graph,seed_data,client_a_admin):
    from app.api.property_knowledge import get_knowledge_provider
    from app.declarations.fake_knowledge_provider import FakeKnowledgeProvider
    from app.main import app
    source=approved_knowledge_graph
    app.dependency_overrides[get_knowledge_provider]=lambda:FakeKnowledgeProvider()
    try:
        async with granted_graph(client_a_admin,seed_data,source) as (domain,receipt),synthetic_enforcement(seed_data):
            response=await client_a_admin.post(f'/api/properties/{source.property_id}/knowledge/query',json={'question':'Heizkosten'})
            assert response.status_code==200,response.text
            async def count():
                async with get_tenant_session(source.organization_id) as db:
                    await actor_session(db,seed_data['user_a_admin_id'])
                    return await db.scalar(text('SELECT count(*) FROM property_knowledge_query_run WHERE property_id=:property'),{'property':source.property_id})
            assert await count()==1
            await change_mode(seed_data,'PAUSED')
            assert await count()==1
            assert (await client_a_admin.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code==204
            assert await count()==0
    finally: app.dependency_overrides.pop(get_knowledge_provider,None)


async def test_derived_migration_is_private_fail_closed_and_roundtrips(committed_scope):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0062_derived_source_access')
    seed=committed_scope
    async with synthetic_enforcement(seed),get_tenant_session(seed['org_a_id']) as db:
        await actor_session(db,seed['user_a_admin_id'])
        for kind,context,cap in [('unknown',{},'read'),('property_rule',{},'read'),('property_rule',{'id':str(uuid.uuid4()),'organization_id':str(uuid.uuid4())},'read'),('property_rule',{},'owner')]:
            assert not await db.scalar(text('SELECT property_derived_row_access(:org,:kind,CAST(:context AS jsonb),:cap)'),
                {'org':seed['org_a_id'],'kind':kind,'context':json.dumps(context),'cap':cap})
    async with synthetic_enforcement(seed):
        engine=create_engine(MIGRATOR_SYNC_URL)
        try:
            with engine.begin() as connection,Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(DBAPIError,match='Retain strict derived'):
                    with connection.begin_nested(): migration.downgrade()
                savepoint=connection.begin_nested()
                try:
                    connection.execute(text('TRUNCATE property_source_security'))
                    migration.downgrade()
                    migration.upgrade()
                    assert connection.scalar(text("SELECT count(*) FROM pg_policy WHERE polname='derived_source_read'"))==15
                    assert connection.scalar(text("SELECT count(*) FROM pg_trigger WHERE tgname='a_lock_derived_source'"))==14
                    for signature in ['property_import_runtime_access(uuid,uuid,text)','property_derived_row_access(uuid,text,jsonb,text)']:
                        config=connection.scalar(text('SELECT proconfig FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)'),{'signature':signature})
                        assert 'row_security=off' in config
                        assert 'search_path=pg_catalog, pg_temp' in config
                finally: savepoint.rollback()
        finally: engine.dispose()


@pytest.mark.parametrize('table',[
    'property_source_security','declaration_import_document','declaration_candidate','declaration_candidate_evidence',
    'declaration_candidate_issue','declaration_extraction_run','declaration_queue_request','declaration_import_pass',
    *CANONICAL,'property_knowledge_query_run'])
async def test_derived_migration_refuses_busy_tables(committed_scope,table):
    import importlib
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    migration=importlib.import_module('migrations.versions.0062_derived_source_access')
    engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as reader:
            reader.execute(text(f'SELECT 1 FROM {table} LIMIT 1'))
            with pytest.raises(DBAPIError) as denied:
                with engine.begin() as connection,Operations.context(MigrationContext.configure(connection)):
                    connection.execute(text("SET LOCAL lock_timeout='1s'"))
                    migration.upgrade()
            assert denied.value.orig.pgcode=='55P03'
    finally: engine.dispose()


async def test_strict_existing_amendment_updates_facts_and_replay_requires_approve(
    seed_data,client_a_admin,fully_reviewed_new_property_import
):
    from datetime import datetime,timezone
    from app.models.declaration_candidate import DeclarationCandidate,DeclarationCandidateEvidence
    from tests.test_declaration_publication_audience import command
    item=fully_reviewed_new_property_import.item
    async with synthetic_enforcement(seed_data):
        published=await client_a_admin.post(f'/api/declaration-imports/{item.id}/approve',json=command(seed_data,item))
        assert published.status_code==200,published.text
        property_id=published.json()['property_id']
        master=await client_a_admin.get(f'/api/properties/{property_id}')
        assert master.status_code==200,master.text
        draft=await client_a_admin.post('/api/declaration-imports',json={'mode':'EXISTING_PROPERTY','target_property_id':property_id})
        assert draft.status_code==201,draft.text
        job=uuid.UUID(draft.json()['id'])
        upload=await client_a_admin.post(f'/api/declaration-imports/{job}/documents',
            files={'file':('amendment.pdf',b'%PDF-1.4\n%%EOF','application/pdf')},data={'document_role':'AMENDMENT','sequence_no':'1'})
        assert upload.status_code==201,upload.text
        # Seed extraction output as migration-owned fixture data, then exercise
        # the real strict human review command before canonical publication.
        from sqlalchemy.ext.asyncio import AsyncSession,create_async_engine
        from tests.conftest import MIGRATOR_URL
        engine=create_async_engine(MIGRATOR_URL)
        try:
            async with AsyncSession(engine,expire_on_commit=False) as db,db.begin():
                await actor_session(db,seed_data['user_a_admin_id'])
                candidate=DeclarationCandidate(organization_id=seed_data['org_a_id'],import_id=job,stable_key='property:street',
                    candidate_type='PROPERTY_FIELD',subject_key='property:current',field_key='street',operation='UPDATE',
                    previous_value_json={'kind':'TEXT','value':master.json()['street']},
                    proposed_value_json={'kind':'TEXT','value':'Reviewed amendment street'},confidence=1,risk_class='CRITICAL',requires_individual_review=True)
                db.add(candidate)
                await db.flush()
                db.add(DeclarationCandidateEvidence(organization_id=seed_data['org_a_id'],candidate_id=candidate.id,
                    document_id=uuid.UUID(upload.json()['id']),pdf_page=1,quote='Reviewed amendment street'))
                await db.flush()
                await db.execute(text("UPDATE declaration_import SET status='NEEDS_REVIEW',draft_revision=1 WHERE id=:job"),{'job':job})
        finally: await engine.dispose()
        reviewed=await client_a_admin.patch(f'/api/declaration-imports/{job}/candidates/{candidate.id}',
            json={'draft_revision':1,'decision':'ACCEPTED'})
        assert reviewed.status_code==200,reviewed.text
        body={'draft_revision':reviewed.json()['draft_revision'],'idempotency_key':str(uuid.uuid4())}
        response=await client_a_admin.post(f'/api/declaration-imports/{job}/approve',json=body)
        assert response.status_code==200,response.text
        assert (await client_a_admin.get(f'/api/properties/{property_id}')).json()['street']=='Reviewed amendment street'
        assert (await client_a_admin.post(f'/api/declaration-imports/{job}/approve',json=body)).json()==response.json()
        async with get_tenant_session(seed_data['org_a_id']) as db:
            await actor_session(db,seed_data['user_a_admin_id'])
            assert await db.scalar(text('SELECT count(*) FROM approved_fact_source WHERE source_import_id=:job'),{'job':job})>0
            domain,receipt=(await db.execute(text('SELECT publication_domain_id,publication_grant_id FROM declaration_import WHERE id=:id'),{'id':item.id})).one()
        assert (await client_a_admin.post(f'/api/property-data-domains/{domain}/grants/{receipt}/revoke')).status_code==204
        assert (await client_a_admin.post(f'/api/property-data-domains/{domain}/grants',headers={'Idempotency-Key':str(uuid.uuid4())},
            json={'user_id':str(seed_data['user_a_admin_id']),'capabilities':['read','prepare'],'reason':'Preparation without approval'})).status_code==201
        assert (await client_a_admin.post(f'/api/declaration-imports/{job}/approve',json=body)).status_code==404
