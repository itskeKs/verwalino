"""Immutable extraction history preserves prior proposals and source authority."""
import uuid
import pytest
from app.core.db import get_worker_session_factory
from app.core.invoice_ocr import extract_invoice
from app.workers.invoice_extraction import process_once
from tests.test_invoice_extraction_worker import worker_tenant,pdf_bytes  # noqa: F401
from tests.test_invoice_source_custody import store  # noqa: F401
from tests.test_financial_scope_boundary import scope_seed  # noqa: F401

pytestmark=pytest.mark.asyncio


async def test_invoice_retries_append_private_revisions_and_unlocated_field_evidence(worker_tenant,store,client_b_admin):
    api,ids=worker_tenant
    response=await api.post('/api/invoice-documents/upload',files={'file':('invoice.pdf',pdf_bytes(),'application/pdf')})
    assert response.status_code==201,response.text
    document=response.json()
    ref=(await api.get('/api/document-refs/page',params={'source_id':document['id']})).json()['items'][0]
    for value in ['119.00','238.00']:
        def processor(data,mime):
            result=extract_invoice(data,mime);result['payload']['gross_amount']=value
            return result
        assert await process_once(get_worker_session_factory(),ids['org_a_id'],processor=processor)
        revisions=await api.get('/api/document-refs/'+ref['id']+'/extractions')
        assert revisions.status_code==200,revisions.text
        history=revisions.json()['items']
        detail=await api.get('/api/extraction-revisions/'+history[0]['id'])
        assert detail.status_code==200,detail.text
        result=detail.json()
        assert result['content_sha256']==ref['content_sha256']
        assert result['schema_version']=='invoice-fields-v1' and result['processor_version']=='invoice-local-v1'
        amount=next(field for field in result['fields'] if field['field_name']=='gross_amount')
        assert amount['value_type']=='decimal' and amount['value']==value
        assert amount['locator_status']=='unlocated'
        assert amount['page_number'] is None and amount['quotation'] is None and amount['xml_path'] is None
        assert 'payload' not in history[0]
        assert (await client_b_admin.get('/api/extraction-revisions/'+history[0]['id'])).status_code==404
        if value=='119.00':
            first=result
            retried=await api.post('/api/invoice-documents/'+document['id']+'/retry')
            assert retried.status_code==200,retried.text
    assert len(history)==2
    assert (await api.get('/api/extraction-revisions/'+first['id'])).json()==first
    current=(await api.get('/api/invoice-documents/'+document['id'])).json()
    assert current['cost_entry_id'] is None and current['extraction_payload']['gross_amount']=='238.00'


@pytest.mark.parametrize('malformation',['status','amount','pages','version','huge_confidence','surrogate','nul'])
async def test_malformed_processor_output_is_recorded_as_failure_without_proposals(worker_tenant,store,malformation):
    api,ids=worker_tenant
    document=(await api.post('/api/invoice-documents/upload',files={'file':('invoice.pdf',pdf_bytes(),'application/pdf')})).json()
    def processor(data,mime):
        result=extract_invoice(data,mime)
        if malformation=='status':result['status']=[]
        if malformation=='amount':result['payload']['gross_amount']='NaN'
        if malformation=='pages':result['payload']['source_pages']=[0,201]
        if malformation=='version':result['processor_version']='unknown'
        if malformation=='huge_confidence':result['payload']['confidence']=10**1000
        if malformation=='surrogate':result['payload']['vendor_name']='\ud800'
        if malformation=='nul':result['payload']['raw_text']='Invoice\x00text'
        return result
    assert await process_once(get_worker_session_factory(),ids['org_a_id'],processor=processor)
    current=(await api.get('/api/invoice-documents/'+document['id'])).json()
    assert current['processing_status']=='failed' and current['processing_error_code']=='processor_failed'
    ref=(await api.get('/api/document-refs/page',params={'source_id':document['id']})).json()['items'][0]
    history=(await api.get('/api/document-refs/'+ref['id']+'/extractions')).json()['items']
    assert len(history)==1
    detail=(await api.get('/api/extraction-revisions/'+history[0]['id'])).json()
    assert detail['payload'] is None and detail['fields']==[]


async def test_finish_rollback_loses_no_history_and_retry_commits_one_effect(worker_tenant,store):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from app.core.db import set_tenant_context
    from app.services.invoice_processing_service import InvoiceProcessingService
    from tests.test_scope_payment_reconciliation import read_value
    from tests.test_scoped_account_statements import mutate
    api,seed=worker_tenant;org=seed['org_a_id']
    document=(await api.post('/api/invoice-documents/upload',files={'file':('invoice.pdf',pdf_bytes(),'application/pdf')})).json()
    ref=(await api.get('/api/document-refs/page',params={'source_id':document['id']})).json()['items'][0]
    factory=get_worker_session_factory()
    async with factory() as db,db.begin():
        await set_tenant_context(db,org)
        claim=await InvoiceProcessingService(db,org).claim()
    result=extract_invoice(pdf_bytes(),'application/pdf')
    with pytest.raises(RuntimeError,match='synthetic rollback'):
        async with factory() as db,db.begin():
            await set_tenant_context(db,org)
            assert await InvoiceProcessingService(db,org).finish(claim,result)
            raise RuntimeError('synthetic rollback')
    assert (await api.get('/api/document-refs/'+ref['id']+'/extractions')).json()['items']==[]
    assert (await api.get('/api/invoice-documents/'+document['id'])).json()['processing_status']=='running'
    async with factory() as db,db.begin():
        await set_tenant_context(db,org)
        assert await InvoiceProcessingService(db,org).finish(claim,result)
        assert not await InvoiceProcessingService(db,org).finish(claim,result)
    rows=(await api.get('/api/document-refs/'+ref['id']+'/extractions')).json()['items'];assert len(rows)==1
    ids={'org':org,'actor':seed['user_a_admin_id'],'revision':uuid.UUID(rows[0]['id']),'effect':'extraction:'+rows[0]['id']}
    assert await read_value('SELECT count(*) FROM outbox_event WHERE effect_key=:effect',ids)==1
    for table in ['extraction_revision','field_evidence']:
        with pytest.raises(DBAPIError,match='permission denied'):
            await read_value(f'DELETE FROM {table} WHERE organization_id=:org RETURNING id',ids,actor_key='actor')
        with pytest.raises(DBAPIError,match='immutable|Immutable'):
            await mutate(text(f'DELETE FROM {table} WHERE organization_id=:org'),ids)
    with pytest.raises(DBAPIError,match='permission denied'):
        await read_value('SELECT record_invoice_extraction_revision(:org,NULL,NULL,NULL)',ids,actor_key='actor')


async def test_reprocessing_preserves_manual_cost_values_and_revoked_source_hides_evidence(migrator_session,scope_seed,store):
    from sqlalchemy import text
    from tests.test_invoice_source_custody import fixture
    from tests.test_scoped_account_statements import client,mutate
    from tests.test_scope_payment_reconciliation import read_value
    ids=await fixture(migrator_session,scope_seed)
    category=await read_value("SELECT id FROM cost_category WHERE code='GRUNDSTEUER' AND plan_version=1",ids)
    async with client(ids) as api:
        document=(await api.post('/api/invoice-documents/upload',files={'file':('invoice.pdf',pdf_bytes(),'application/pdf')})).json()
        assert await process_once(get_worker_session_factory(),ids['org'],processor=extract_invoice)
        ref=(await api.get('/api/document-refs/page',params={'source_id':document['id']})).json()['items'][0]
        revision=(await api.get('/api/document-refs/'+ref['id']+'/extractions')).json()['items'][0]
        body={'accounting_scope_id':str(ids['scope']),'source_document_id':document['id'],'property_id':str(ids['property']),
              'cost_category_id':str(category),'invoice_date':'2025-06-01','service_period_from':'2025-01-01','service_period_to':'2025-12-31',
              'gross_amount':'119.00','tenant_apportionable':'UMLAGEFAEHIG','source':'MANUAL'}
        cost=await api.post('/api/cost-entries',json=body,headers={'Idempotency-Key':str(uuid.uuid4())})
        assert cost.status_code==201,cost.text
        edited=await api.patch('/api/cost-entries/'+cost.json()['id'],json={'gross_amount':'123.45'})
        assert edited.status_code==200,edited.text
        assert (await api.post('/api/invoice-documents/'+document['id']+'/retry')).status_code==200
        assert await process_once(get_worker_session_factory(),ids['org'],processor=extract_invoice)
        assert (await api.get('/api/cost-entries/'+cost.json()['id'])).json()['gross_amount']=='123.45'
        assert len((await api.get('/api/document-refs/'+ref['id']+'/extractions')).json()['items'])==2
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND revoked_at IS NULL'),ids)
        assert (await api.get('/api/document-refs/'+ref['id']+'/extractions')).status_code==404
        assert (await api.get('/api/extraction-revisions/'+revision['id'])).status_code==404


async def test_extraction_migration_retains_revisions_and_restores_empty_schema(worker_tenant,store):
    import importlib
    from sqlalchemy import create_engine,text
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    api,ids=worker_tenant
    document=(await api.post('/api/invoice-documents/upload',files={'file':('invoice.pdf',pdf_bytes(),'application/pdf')})).json()
    assert await process_once(get_worker_session_factory(),ids['org_a_id'],processor=extract_invoice)
    ref=(await api.get('/api/document-refs/page',params={'source_id':document['id']})).json()['items'][0]
    before=(await api.get('/api/document-refs/'+ref['id']+'/extractions')).json()
    migration=importlib.import_module('migrations.versions.0096_extraction_evidence');engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError,match='Retain extraction'),db.begin_nested():migration.downgrade()
            point=db.begin_nested()
            try:
                db.execute(text('TRUNCATE logical_document,field_evidence,extraction_revision,outbox_event CASCADE'))
                oid=db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))
                importlib.import_module('migrations.versions.0098_intake_extraction_jobs').downgrade()
                importlib.import_module('migrations.versions.0097_internal_outbox_delivery').downgrade()
                migration.downgrade();migration.upgrade()
                assert db.scalar(text("SELECT 'access_coverage_manifest(uuid,jsonb)'::regprocedure::oid"))==oid
            finally:point.rollback()
        assert (await api.get('/api/document-refs/'+ref['id']+'/extractions')).json()==before
    finally:engine.dispose()
