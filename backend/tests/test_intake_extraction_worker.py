"""Durable logical-source proposals retain exact source and live authority."""
import uuid
import pytest
from sqlalchemy import text
from app.core.db import get_worker_session_factory
from app.core.intake_inspection import result
from app.core.intake_extraction import extract_logical
from app.workers.workflow import process_once as inspect_once
from app.workers.outbox import process_once as deliver_once
from app.workers.intake_extraction import process_once
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store,fixture
from tests.test_scoped_account_statements import client,mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_invoice_extraction_worker import pdf_bytes
from tests.test_intake_batches import receive,work_sql

pytestmark=pytest.mark.asyncio


async def source(api,ids,*,scoped=False):
    _,receipt=await receive(api,pdf_bytes(),**({'scope_hint_id':str(ids['scope'])} if scoped else {}))
    assert await inspect_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'clean',mime=mime,pages=1))
    assert await deliver_once(get_worker_session_factory(),ids['org'])
    ref=(await api.get('/api/document-refs/page',params={'source_id':receipt['original_id']})).json()['items'][0]
    child=(await api.get('/api/logical-documents',params={'document_ref_id':ref['id']})).json()['items'][0]
    return ref,child


async def test_clean_source_has_durable_located_history_and_no_costs(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        ref,child=await source(api,ids)
        status=await api.get('/api/logical-documents/'+child['id']+'/processing')
        assert status.status_code==200 and status.json()['status']=='queued'
        assert await process_once(get_worker_session_factory(),ids['org'],processor=extract_logical)
        assert not await process_once(get_worker_session_factory(),ids['org'],processor=extract_logical)
        history=(await api.get('/api/document-refs/'+ref['id']+'/extractions')).json()['items']
        assert len(history)==1 and history[0]['logical_document_id']==child['id']
        assert history[0]['invoice_attempt_id'] is None and history[0]['intake_attempt_id'] is not None
        detail=(await api.get('/api/extraction-revisions/'+history[0]['id'])).json()
        amount=next(field for field in detail['fields'] if field['field_name']=='gross_amount')
        assert (amount['value'],amount['page_number'],amount['quotation'],amount['locator_status'])==('119.00',1,'Gesamtbetrag: 119,00 EUR','page_quote')
        assert amount['validation_status']=='unverified'
        assert (await api.get('/api/logical-documents/'+child['id']+'/processing')).json()['status']=='succeeded'
        async with client(ids,'actor') as other:
            assert (await other.get('/api/extraction-revisions/'+history[0]['id'])).status_code==404
    assert await read_value('SELECT count(*) FROM cost_entry WHERE organization_id=:org',ids)==0
    assert await read_value("SELECT count(*) FROM outbox_event WHERE organization_id=:org AND event_type='document.extracted'",ids)==1


async def test_expired_claim_is_fenced_and_original_authority_rechecked(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        ref,child=await source(api,ids,scoped=True)
        first=await work_sql(ids,'SELECT claim_intake_extraction(:org)')
        assert await work_sql(ids,'SELECT claim_intake_extraction(:org)') is None
        await mutate(text("UPDATE intake_extraction_job SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE organization_id=:org"),ids)
        second=await work_sql(ids,'SELECT claim_intake_extraction(:org)')
        assert first['fence']!=second['fence']
        assert await work_sql(ids,'SELECT intake_extraction_context(:org,:job,:fence)',job=uuid.UUID(first['job_id']),fence=uuid.UUID(first['fence'])) is None
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND revoked_at IS NULL'),ids)
        assert await work_sql(ids,'SELECT intake_extraction_context(:org,:job,:fence)',job=uuid.UUID(second['job_id']),fence=uuid.UUID(second['fence'])) is None
        assert (await api.get('/api/logical-documents/'+child['id']+'/processing')).status_code==404
    assert await read_value('SELECT count(*) FROM extraction_revision WHERE organization_id=:org',ids)==0
    assert await read_value('SELECT status FROM intake_extraction_job WHERE organization_id=:org',ids)=='canceled'
    assert await read_value('SELECT count(*) FROM intake_extraction_attempt WHERE organization_id=:org AND finished_at IS NOT NULL',ids)==2


async def test_finish_is_atomic_and_stale_replay_has_no_effect(migrator_session,scope_seed,store):
    import json
    from app.core.db import set_tenant_context
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:await source(api,ids)
    claim=await work_sql(ids,'SELECT claim_intake_extraction(:org)')
    from app.core.storage import get_store
    data=get_store().get(claim['storage_uri'])
    answer=extract_logical(data,claim['mime_type'],claim['page_start'],claim['page_end'],claim['verified_page_count'])
    params={**ids,'job':uuid.UUID(claim['job_id']),'fence':uuid.UUID(claim['fence']),'result':json.dumps(answer)}
    sql='SELECT complete_intake_extraction(:org,:job,:fence,CAST(:result AS jsonb))'
    pending="SELECT count(*) FROM jsonb_array_elements(access_disposition_work(:org)) w WHERE w->>'kind'='intake_extraction_job'"
    assert await read_value(pending,ids)==1
    with pytest.raises(RuntimeError,match='simulated crash'):
        async with get_worker_session_factory()() as db,db.begin():
            await set_tenant_context(db,ids['org'])
            assert await db.scalar(text(sql),params)
            raise RuntimeError('simulated crash before commit')
    assert await read_value('SELECT count(*) FROM extraction_revision WHERE organization_id=:org',ids)==0
    assert await read_value(pending,ids)==1
    assert await read_value("SELECT count(*) FROM outbox_event WHERE organization_id=:org AND event_type='document.extracted'",ids)==0
    assert await work_sql(ids,sql,**{key:params[key] for key in ('job','fence','result')})
    assert not await work_sql(ids,sql,**{key:params[key] for key in ('job','fence','result')})
    assert await read_value('SELECT count(*) FROM extraction_revision WHERE organization_id=:org',ids)==1
    assert await read_value(pending,ids)==0


async def test_worker_discards_malformed_result_and_integrity_failure(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        ref,child=await source(api,ids)
        def invalid(*args):
            answer=extract_logical(*args);answer['evidence']['gross_amount']['page_number']=2
            return answer
        assert await process_once(get_worker_session_factory(),ids['org'],processor=invalid)
        revision=(await api.get('/api/document-refs/'+ref['id']+'/extractions')).json()['items'][0]
        detail=(await api.get('/api/extraction-revisions/'+revision['id'])).json()
        assert detail['status']=='failed' and detail['error_code']=='processor_failed'
        assert detail['fields']==[] and detail['payload'] is None


async def test_shared_runner_claim_competition_and_attempt_limit(migrator_session,scope_seed,store):
    import asyncio
    from app.workers.workflow_runner import WorkflowRunner,build_adapters
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:await source(api,ids)
    claims=await asyncio.gather(*[work_sql(ids,'SELECT claim_intake_extraction(:org)') for _ in range(2)])
    assert len([claim for claim in claims if claim is not None])==1
    for number in range(1,4):
        await mutate(text("UPDATE intake_extraction_job SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE organization_id=:org"),ids)
        claim=await work_sql(ids,'SELECT claim_intake_extraction(:org)')
        assert (claim is None)==(number==3)
    assert await read_value('SELECT status FROM intake_extraction_job WHERE organization_id=:org',ids)=='blocked'
    assert await read_value('SELECT count(*) FROM intake_extraction_attempt WHERE organization_id=:org AND finished_at IS NOT NULL',ids)==3
    async with client(ids) as api:
        await source(api,ids)
        async def organizations():return (ids['org'],)
        runner=WorkflowRunner(organizations,build_adapters(['intake_extraction']))
        assert (await runner.run_once()).code=='PROCESSED'
        assert (await runner.run_once()).code=='IDLE'


async def test_actual_worker_boundary_and_private_tables(migrator_session,scope_seed,store):
    from sqlalchemy.exc import DBAPIError
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:await source(api,ids)
    for sql in ['SELECT claim_intake_extraction(:org)','SELECT count(*) FROM intake_extraction_job','DELETE FROM intake_extraction_attempt RETURNING id']:
        with pytest.raises(DBAPIError,match='permission denied|runtime required'):await read_value(sql,ids,actor_key='clerk')


async def test_explicit_retry_replay_keeps_history_and_budget(migrator_session,scope_seed,store):
    from app.core.intake_extraction import outcome
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        ref,child=await source(api,ids)
        def unavailable(data,mime,start,end,count):return outcome(data,mime,start,end,count,'unavailable','ocr_unavailable')
        assert await process_once(get_worker_session_factory(),ids['org'],processor=unavailable)
        current=(await api.get('/api/logical-documents/'+child['id']+'/processing')).json()
        assert current['can_retry']
        headers={'Idempotency-Key':str(uuid.uuid4())};body={'expected_fence':current['fence'],'reason':'Local OCR restored'}
        url='/api/logical-documents/'+child['id']+'/retry-extraction'
        first=await api.post(url,headers=headers,json=body)
        assert first.status_code==202,first.text
        assert await process_once(get_worker_session_factory(),ids['org'],processor=extract_logical)
        replay=await api.post(url,headers=headers,json=body)
        assert replay.status_code==202 and replay.json()==first.json()
        assert (await api.post(url,headers=headers,json={**body,'reason':'Changed body'})).status_code==409
        assert (await api.post(url,headers={'Idempotency-Key':str(uuid.uuid4())},json=body)).status_code==409
        status=(await api.get('/api/logical-documents/'+child['id']+'/processing')).json()
        assert status['status']=='succeeded' and status['attempt_number']==2 and not status['can_retry']
        history=(await api.get('/api/document-refs/'+ref['id']+'/extractions')).json()['items']
        assert [row['status'] for row in history]==['succeeded','unavailable']


async def test_permission_revocation_during_processor_discards_result(migrator_session,scope_seed,store):
    import asyncio
    import threading
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:await source(api,ids,scoped=True)
    started=threading.Event();release=threading.Event()
    def processor(*args):
        started.set()
        assert release.wait(5)
        return extract_logical(*args)
    work=asyncio.create_task(process_once(get_worker_session_factory(),ids['org'],processor=processor))
    try:
        assert await asyncio.to_thread(started.wait,5)
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND revoked_at IS NULL'),ids)
    finally:release.set()
    assert not await work
    assert await read_value('SELECT count(*) FROM extraction_revision WHERE organization_id=:org',ids)==0
    assert await read_value('SELECT status FROM intake_extraction_job WHERE organization_id=:org',ids)=='canceled'


async def test_storage_integrity_failure_is_durable_without_parsing(migrator_session,scope_seed,store,monkeypatch):
    from app.core.storage import get_store
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:await source(api,ids)
    monkeypatch.setattr(get_store(),'iter_chunks',lambda uri:iter([b'altered original']))
    def forbidden(*args):raise AssertionError('Integrity failure must precede parsing')
    assert await process_once(get_worker_session_factory(),ids['org'],processor=forbidden)
    assert await read_value('SELECT error_code FROM extraction_revision WHERE organization_id=:org',ids)=='storage_integrity_mismatch'
    assert await read_value('SELECT count(*) FROM field_evidence WHERE organization_id=:org',ids)==0


async def test_xml_worker_retains_resolvable_locators(migrator_session,scope_seed,store):
    from tests.test_m5_einvoice import UBL
    import xml.etree.ElementTree as ET
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        from tests.test_intake_batches import create,manifest
        item={**manifest(UBL)['items'][0],'filename':'invoice.xml','mime_type':'application/xml'}
        batch=await create(api,UBL,items=[item])
        uploaded=await api.post('/api/intake-batches/'+batch['id']+'/items',files={'file':('invoice.xml',UBL,'application/xml')},data={'client_item_key':'document-1'})
        assert uploaded.status_code==202,uploaded.text
        receipt=uploaded.json()
        assert await inspect_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'clean',mime=mime,pages=None))
        assert await deliver_once(get_worker_session_factory(),ids['org'])
        assert await process_once(get_worker_session_factory(),ids['org'],processor=extract_logical)
        ref=(await api.get('/api/document-refs/page',params={'source_id':receipt['original_id']})).json()['items'][0]
        revision=(await api.get('/api/document-refs/'+ref['id']+'/extractions')).json()['items'][0]
        fields=(await api.get('/api/extraction-revisions/'+revision['id'])).json()['fields']
        assert fields
        for field in fields:assert ET.fromstring(UBL).find(field['xml_path']).text.strip()==field['quotation']


async def test_migration_backfill_guard_and_function_identity(migrator_session,scope_seed,store):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:await source(api,ids)
    migration=importlib.import_module('migrations.versions.0098_intake_extraction_jobs');engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError,match='Retain intake extraction'),db.begin_nested():migration.downgrade()
            point=db.begin_nested()
            try:
                db.execute(text('TRUNCATE intake_extraction_job CASCADE'))
                oid=db.scalar(text("SELECT 'access_disposition_work(uuid)'::regprocedure::oid"))
                db.execute(text('TRUNCATE source_partition_revision CASCADE'))
                db.execute(text('TRUNCATE exception_case CASCADE'))
                db.execute(text('TRUNCATE exception_case CASCADE'))
                db.execute(text('TRUNCATE allocation_treatment_review CASCADE'))
                db.execute(text('TRUNCATE allocation_integrity_attestation CASCADE'))
                db.execute(text('TRUNCATE exception_completion CASCADE'))
                db.execute(text('TRUNCATE financial_computation_attestation CASCADE'))
                db.execute(text('TRUNCATE allocation_correction_context,allocation_correction_completion'))
                db.execute(text('TRUNCATE visual_disclosure_attestation CASCADE'))
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
                migration.downgrade();migration.upgrade()
                assert db.scalar(text("SELECT 'access_disposition_work(uuid)'::regprocedure::oid"))==oid
                assert db.scalar(text('SELECT count(*) FROM intake_extraction_job WHERE organization_id=:org'),ids)==1
            finally:point.rollback()
    finally:engine.dispose()


async def test_child_filter_recovers_history_beyond_source_limit(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        _,receipt=await receive(api,pdf_bytes(20))
        assert await inspect_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'clean',mime=mime,pages=20))
        assert await deliver_once(get_worker_session_factory(),ids['org'])
        ref=(await api.get('/api/document-refs/page',params={'source_id':receipt['original_id']})).json()['items'][0]
        ranges=[(first,last) for first in range(1,21) for last in range(first,21) if (first,last)!=(1,20)]
        for first,last in ranges[:99]:
            response=await api.post('/api/logical-documents',json={'document_ref_id':ref['id'],'page_start':first,'page_end':last,'reason':'Synthetic history corpus'},headers={'Idempotency-Key':str(uuid.uuid4())})
            assert response.status_code==201,response.text
        # A large synthetic retained ledger isolates discovery behavior from
        # processor performance. No fake financial values or confirmations.
        await mutate(text('''WITH attempts AS (
          INSERT INTO intake_extraction_attempt(organization_id,job_id,attempt_number,fence,finished_at,result)
            SELECT organization_id,id,n,gen_random_uuid(),clock_timestamp(),jsonb_build_object('status','failed')
            FROM intake_extraction_job CROSS JOIN generate_series(1,2) n WHERE organization_id=:org RETURNING *)
          INSERT INTO extraction_revision(organization_id,document_ref_id,intake_attempt_id,logical_document_id,content_sha256,processor_version,status,error_code)
            SELECT a.organization_id,l.document_ref_id,a.id,l.id,l.content_sha256,'intake-local-v1','failed','synthetic_history'
            FROM attempts a JOIN intake_extraction_job j ON j.id=a.job_id JOIN logical_document l ON l.id=j.logical_document_id'''),ids)
        oldest=await read_value('''SELECT jsonb_build_object('id',r.id,'logical',r.logical_document_id,'job',a.job_id)
          FROM extraction_revision r JOIN intake_extraction_attempt a ON a.id=r.intake_attempt_id
          WHERE r.organization_id=:org ORDER BY r.created_at,r.id LIMIT 1''',ids)
        await mutate(text('''WITH attempt AS (
          INSERT INTO intake_extraction_attempt(organization_id,job_id,attempt_number,fence,finished_at,result)
          VALUES(:org,:job,3,gen_random_uuid(),clock_timestamp(),jsonb_build_object('status','failed')) RETURNING *)
          INSERT INTO extraction_revision(organization_id,document_ref_id,intake_attempt_id,logical_document_id,content_sha256,processor_version,status,error_code)
          SELECT a.organization_id,l.document_ref_id,a.id,l.id,l.content_sha256,'intake-local-v1','failed','synthetic_history'
          FROM attempt a JOIN logical_document l ON l.id=:logical'''),{**ids,'job':uuid.UUID(oldest['job']),'logical':uuid.UUID(oldest['logical'])})
        url='/api/document-refs/'+ref['id']+'/extractions'
        recent=(await api.get(url)).json()['items']
        assert len(recent)==200 and oldest['id'] not in {row['id'] for row in recent}
        complete=(await api.get(url,params={'logical_document_id':oldest['logical']})).json()['items']
        assert len(complete)==3 and oldest['id'] in {row['id'] for row in complete}
        other_ref,_=await source(api,ids)
        assert (await api.get('/api/document-refs/'+other_ref['id']+'/extractions',params={'logical_document_id':oldest['logical']})).status_code==404


async def test_database_completion_rejects_invalid_identity_pages_and_fields(migrator_session,scope_seed,store):
    import copy
    import json
    from sqlalchemy.exc import DBAPIError
    from app.core.storage import get_store
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:await source(api,ids)
    claim=await work_sql(ids,'SELECT claim_intake_extraction(:org)')
    answer=extract_logical(get_store().get(claim['storage_uri']),claim['mime_type'],claim['page_start'],claim['page_end'],claim['verified_page_count'])
    for corrupt in (
        lambda r:r.update(content_sha256='0'*64),
        lambda r:r.update(page_end=2),
        lambda r:r['payload'].update(gross_amount='not-a-number'),
        lambda r:r['payload'].update(source_pages=[2]),
        lambda r:r['evidence']['gross_amount'].update(page_number=None),
        lambda r:r['evidence']['gross_amount'].update(quotation='fabricated'),
        lambda r:r['evidence']['gross_amount'].update(locator_status='xml_path',xml_path='./ID',page_number=None,method='xml'),
        lambda r:r['pages'][0].update(method=None),
    ):
        forged=copy.deepcopy(answer);corrupt(forged)
        with pytest.raises(DBAPIError):
            await work_sql(ids,'SELECT complete_intake_extraction(:org,:job,:fence,CAST(:result AS jsonb))',
                job=uuid.UUID(claim['job_id']),fence=uuid.UUID(claim['fence']),result=json.dumps(forged))
    assert await read_value('SELECT count(*) FROM extraction_revision WHERE organization_id=:org',ids)==0
    assert await read_value('SELECT status FROM intake_extraction_job WHERE organization_id=:org',ids)=='running'
