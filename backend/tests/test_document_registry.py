"""The common original registry inherits each canonical source boundary."""
import uuid
import pytest
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store,upload  # noqa: F401
from tests.test_declaration_import_api import isolated_document_store,enable_declaration_imports  # noqa: F401
from tests.test_scoped_account_statements import client
from tests.test_scope_payment_reconciliation import read_value
from tests.test_source_security_lifecycle import accepted,status,transition,transition_body
from tests.test_declaration_job_principal import new_draft

pytestmark=pytest.mark.asyncio


async def test_registry_automatically_registers_intake_without_widening_custody(migrator_session,scope_seed,store,isolated_document_store,enable_declaration_imports,client_b_admin):
    await migrator_session.commit()
    ids={'org':scope_seed['org_a_id'],'actor':scope_seed['user_a_admin_id'],'clerk':scope_seed['user_a_clerk_id']}
    async with client(ids,'actor') as admin,client(ids) as clerk:
        decision=await accepted(admin)
        assert (await transition(admin,transition_body(await status(admin),'ENFORCED',decision))).status_code==201
        invoice=await upload(clerk)
        job=uuid.UUID(await new_draft({**ids,'actor':ids['clerk']}))
        original=await read_value('SELECT document_id FROM declaration_import_document WHERE import_id=:job',{**ids,'job':job})
        response=await clerk.get('/api/document-refs/page',params={'limit':1})
        assert response.status_code==200,response.text
        first=response.json();assert len(first['items'])==1 and first['next_cursor']
        second=(await clerk.get('/api/document-refs/page',params={'limit':1,'cursor':first['next_cursor']})).json()
        rows=first['items']+second['items'];assert second['next_cursor'] is None
        assert {row['source_id'] for row in rows}=={str(invoice),str(original)}
        assert {row['source_kind'] for row in rows}=={'invoice_document','property_document'}
        for row in rows:
            assert len(row['content_sha256'])==64 and 'storage_uri' not in row
            assert (await clerk.get(f"/api/document-refs/{row['id']}")).status_code==200
            assert (await admin.get(f"/api/document-refs/{row['id']}")).status_code==404
            assert (await client_b_admin.get(f"/api/document-refs/{row['id']}")).status_code==404
        assert (await admin.get('/api/document-refs/page')).json()['items']==[]
        assert (await client_b_admin.get('/api/document-refs/page',params={'cursor':first['next_cursor']})).status_code==422


async def test_mandate_reference_tracks_selected_scope_archive_and_revocation(migrator_session,scope_seed,store):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from tests.conftest import APP_RW_URL
    from tests.test_scoped_account_statements import mutate
    from tests.test_scoped_mandate_documents import document_fixture,upload as mandate_upload
    ids=await document_fixture(migrator_session,scope_seed)
    endpoint=f"/api/mandates/{ids['mandate']}/documents"
    async with client(ids) as api:
        source=await api.post(endpoint,**mandate_upload(ids))
        assert source.status_code==201,source.text
        ids['document']=uuid.UUID(source.json()['id'])
        rows=(await api.get('/api/document-refs/page',params={'source_kind':'mandate_document','source_id':str(ids['document'])})).json()['items']
        assert len(rows)==1
        ref=rows[0];ids['ref']=uuid.UUID(ref['id'])
        assert ref['source_id']==str(ids['document'])
        engine=create_async_engine(APP_RW_URL)
        try:
            async with engine.begin() as db:
                await db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),{'org':str(ids['org']),'actor':str(ids['clerk'])})
                for selected,expected in [(ids['source_scope'],1),(ids['scope'],0)]:
                    await db.execute(text("SELECT set_config('app.current_scope_id',:scope,true)"),{'scope':str(selected)})
                    assert await db.scalar(text('SELECT count(*) FROM mandate_document WHERE id=:document'),ids)==expected
                    assert await db.scalar(text('SELECT count(*) FROM document_ref WHERE id=:ref'),ids)==expected
        finally:
            await engine.dispose()
        archived=await api.post(endpoint+f"/{ids['document']}/archive",json={'reason':'Retain replaced original'})
        assert archived.status_code==200,archived.text
        assert (await api.get('/api/document-refs/'+ref['id'])).json()==ref
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE scope_id=:source_scope AND revoked_at IS NULL'),ids)
        assert (await api.get('/api/document-refs/'+ref['id'])).status_code==404
        assert (await api.get('/api/document-refs/page',params={'source_id':str(ids['document'])})).json()['items']==[]


async def test_registry_constraints_backfill_and_guarded_downgrade(migrator_session,scope_seed,store,isolated_document_store,enable_declaration_imports):
    import importlib
    from sqlalchemy import create_engine,text
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_scoped_mandate_documents import document_fixture,upload as mandate_upload
    from tests.test_property_source_enforcement import synthetic_enforcement
    ids=await document_fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        ids['invoice']=await upload(api)
        # Equal content from distinct originals must retain distinct identities.
        second=await upload(api)
        response=await api.post(f"/api/mandates/{ids['mandate']}/documents",**mandate_upload(ids))
        assert response.status_code==201,response.text
        job=uuid.UUID(await new_draft({**ids,'actor':ids['clerk']}))
        ids['property_doc']=await read_value('SELECT document_id FROM declaration_import_document WHERE import_id=:job',{**ids,'job':job})
    engine=create_engine(MIGRATOR_SYNC_URL)
    migration=importlib.import_module('migrations.versions.0092_document_registry')
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            originals={source:db.execute(text(f'SELECT id,to_jsonb(d) FROM {source} d WHERE organization_id=:org'),ids).all() for source in migration.SOURCES}
            refs=db.scalar(text('SELECT count(*) FROM document_ref WHERE organization_id=:org'),ids)
            assert refs==4
            assert db.scalar(text('SELECT count(DISTINCT id) FROM document_ref WHERE invoice_document_id IN (:invoice,:second)'),{**ids,'second':second})==2
            for role in ('app_rw','app_worker','app_review'):
                for privilege in ('INSERT','UPDATE','DELETE','TRUNCATE'):
                    assert not db.scalar(text("SELECT has_table_privilege(:role,'document_ref',:privilege)"),{'role':role,'privilege':privilege})
                for fn in ('validate_document_ref()','register_original_document()','preserve_registered_original()'):
                    assert not db.scalar(text('SELECT has_function_privilege(:role,:fn,\'EXECUTE\')'),{'role':role,'fn':fn})
            template="""INSERT INTO document_ref(organization_id,source_kind,invoice_document_id,content_sha256,mime_type,content_byte_size)
                SELECT {org},source_kind,invoice_document_id,{digest},mime_type,content_byte_size FROM document_ref WHERE invoice_document_id=:invoice"""
            for org,digest,error in [('organization_id','content_sha256','duplicate key'),('organization_id',"repeat('b',64)",'exact source'),(':foreign','content_sha256','exact source')]:
                with pytest.raises(DBAPIError,match=error),db.begin_nested():
                    db.execute(text(template.format(org=org,digest=digest)),{**ids,'foreign':uuid.uuid4()})
            for source,rows in originals.items():
                with pytest.raises(DBAPIError,match='immutable'),db.begin_nested():
                    db.execute(text(f"UPDATE {source} SET content_byte_size=content_byte_size+1 WHERE id=:id"),{'id':rows[0][0]})
            with pytest.raises(DBAPIError,match='immutable'),db.begin_nested():
                db.execute(text('DELETE FROM document_ref WHERE organization_id=:org'),ids)
            with pytest.raises(DBAPIError,match='Retain original registry'),db.begin_nested():
                migration.downgrade()
            point=db.begin_nested()
            try:
                db.execute(text('TRUNCATE document_ref,intake_batch,intake_upload_admission CASCADE'))
                db.execute(text('TRUNCATE source_partition_revision CASCADE'))
                db.execute(text('TRUNCATE exception_case CASCADE'))
                db.execute(text('TRUNCATE exception_case CASCADE'))
                db.execute(text('TRUNCATE allocation_treatment_review CASCADE'))
                db.execute(text('TRUNCATE allocation_integrity_attestation CASCADE'))
                db.execute(text('TRUNCATE exception_completion CASCADE'))
                db.execute(text('TRUNCATE financial_computation_attestation CASCADE'))
                db.execute(text('TRUNCATE allocation_correction_context,allocation_correction_completion'))
                importlib.import_module('migrations.versions.0123_replacement_exception_outcomes').downgrade()
                importlib.import_module('migrations.versions.0122_allocation_source_replacements').downgrade()
                importlib.import_module('migrations.versions.0121_visual_allocation_sources').downgrade()
                db.execute(text('TRUNCATE visual_disclosure_attestation CASCADE'))
                db.execute(text('TRUNCATE allocation_source_claim CASCADE'))
                db.execute(text('TRUNCATE visual_extract_attestation CASCADE'))
                importlib.import_module('migrations.versions.0120_allocation_source_aliases').downgrade()
                importlib.import_module('migrations.versions.0119_allocation_source_claims').downgrade()
                importlib.import_module('migrations.versions.0118_visual_disclosures').downgrade()
                importlib.import_module('migrations.versions.0117_visual_extract_artifacts').downgrade()
                importlib.import_module('migrations.versions.0116_correction_followup_completions').downgrade()
                importlib.import_module('migrations.versions.0115_financial_computation_evidence').downgrade()
                importlib.import_module('migrations.versions.0114_allocation_corrections').downgrade()
                importlib.import_module('migrations.versions.0113_allocation_correction_preview').downgrade()
                importlib.import_module('migrations.versions.0112_context_mappings').downgrade()
                importlib.import_module('migrations.versions.0111_context_candidate_source').downgrade()
                importlib.import_module('migrations.versions.0110_exception_completions').downgrade()
                importlib.import_module('migrations.versions.0109_allocation_adoptions').downgrade()
                importlib.import_module('migrations.versions.0108_allocation_treatments').downgrade()
                importlib.import_module('migrations.versions.0107_duplicate_exceptions').downgrade()
                db.execute(text('TRUNCATE duplicate_comparison CASCADE'))
                importlib.import_module('migrations.versions.0106_duplicate_comparisons').downgrade()
                db.execute(text('TRUNCATE allocation_proposal CASCADE'))
                importlib.import_module('migrations.versions.0105_allocation_proposals').downgrade()
                db.execute(text('TRUNCATE context_decision CASCADE'))
                importlib.import_module('migrations.versions.0104_context_decisions').downgrade()
                db.execute(text('TRUNCATE invoice_review_revision'))
                importlib.import_module('migrations.versions.0103_invoice_field_reviews').downgrade()
                importlib.import_module('migrations.versions.0102_exception_cases').downgrade()
                importlib.import_module('migrations.versions.0101_assignment_proposals').downgrade()
                importlib.import_module('migrations.versions.0100_source_partitions').downgrade()
                importlib.import_module('migrations.versions.0099_intake_extraction_status').downgrade()
                importlib.import_module('migrations.versions.0098_intake_extraction_jobs').downgrade()
                importlib.import_module('migrations.versions.0097_internal_outbox_delivery').downgrade()
                importlib.import_module('migrations.versions.0096_extraction_evidence').downgrade()
                importlib.import_module('migrations.versions.0095_logical_documents').downgrade()
                importlib.import_module('migrations.versions.0094_intake_inbox_status').downgrade()
                importlib.import_module('migrations.versions.0093_intake_quarantine').downgrade()
                migration.downgrade()
                migration.upgrade()
                assert db.scalar(text('SELECT count(*) FROM document_ref WHERE organization_id=:org'),ids)==refs
                for source,rows in originals.items():
                    assert db.execute(text(f'SELECT id,to_jsonb(d) FROM {source} d WHERE organization_id=:org'),ids).all()==rows
                    assert db.scalar(text(f'''SELECT count(*) FROM {source} s JOIN document_ref r ON r.{source}_id=s.id
                        WHERE s.organization_id=:org AND (r.content_sha256,r.mime_type,r.content_byte_size)=(s.content_sha256,s.mime_type,s.content_byte_size)'''),ids)==len(rows)
            finally:
                point.rollback()
        # Backfilled references cannot broaden strict custody, even for admins.
        async with synthetic_enforcement(scope_seed),client(ids,'actor') as admin:
            assert (await admin.get('/api/document-refs/page',params={'source_id':str(ids['invoice'])})).json()['items']==[]
    finally:
        engine.dispose()


async def test_registry_failure_rolls_back_original_insert(migrator_session,scope_seed,store):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from tests.test_scoped_account_statements import mutate
    await migrator_session.commit()
    ids={'org':scope_seed['org_a_id'],'actor':scope_seed['user_a_admin_id'],'clerk':scope_seed['user_a_clerk_id']}
    await mutate(text("CREATE FUNCTION test_reject_registry() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Synthetic registry failure'; END $$"),ids)
    await mutate(text("CREATE TRIGGER test_reject_registry BEFORE INSERT ON document_ref FOR EACH ROW EXECUTE FUNCTION test_reject_registry()"),ids)
    try:
        async with client(ids) as api:
            with pytest.raises(DBAPIError,match='Synthetic registry failure'):
                await upload(api)
        assert await read_value('SELECT count(*) FROM invoice_document WHERE organization_id=:org',ids)==0
        assert await read_value('SELECT count(*) FROM document_ref WHERE organization_id=:org',ids)==0
    finally:
        await mutate(text('DROP TRIGGER test_reject_registry ON document_ref'),ids)
        await mutate(text('DROP FUNCTION test_reject_registry()'),ids)
