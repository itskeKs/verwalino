"""Durable internal effects bind exact inspection events and current authority."""
import uuid
import pytest
from sqlalchemy import text
from app.core.db import get_worker_session_factory
from app.core.intake_inspection import result
from app.workers.workflow import process_once as inspect_once
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store,fixture
from tests.test_scoped_account_statements import client,mutate
from tests.test_scope_payment_reconciliation import read_value
from tests.test_invoice_extraction_worker import pdf_bytes
from tests.test_intake_batches import receive,work_sql

pytestmark=pytest.mark.asyncio


async def test_clean_event_creates_one_whole_unreviewed_source_and_effect(migrator_session,scope_seed,store):
    from app.workers.workflow_runner import WorkflowRunner,build_adapters
    ids=await fixture(migrator_session,scope_seed)
    async def organizations():return (ids['org'],)
    runner=WorkflowRunner(organizations,build_adapters(['outbox']))
    async with client(ids) as api:
        _,receipt=await receive(api,pdf_bytes(3))
        assert await inspect_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'clean',mime=mime,pages=3))
        ref=(await api.get('/api/document-refs/page',params={'source_id':receipt['original_id']})).json()['items'][0]
        assert (await api.get('/api/logical-documents',params={'document_ref_id':ref['id']})).json()['items']==[]
        assert (await runner.run_once()).code=='PROCESSED'
        assert (await runner.run_once()).code=='IDLE'
        children=(await api.get('/api/logical-documents',params={'document_ref_id':ref['id']})).json()['items']
        assert len(children)==1
        child=children[0]
        assert (child['page_start'],child['page_end'],child['review_state'],child['origin'])==(1,3,'unreviewed','inspection')
        assert await read_value("SELECT count(*) FROM outbox_event WHERE effect_key=:effect",{**ids,'effect':'logical:'+child['id']})==1


async def test_expired_delivery_is_fenced_and_reconciled_without_duplicate_child(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        _,receipt=await receive(api,pdf_bytes())
        assert await inspect_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'clean',mime=mime,pages=1))
        first=await work_sql(ids,'SELECT claim_outbox_delivery(:org)')
        assert first is not None
        assert await work_sql(ids,'SELECT claim_outbox_delivery(:org)') is None
        await mutate(text("UPDATE outbox_delivery SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:delivery"),{'delivery':uuid.UUID(first['delivery_id'])})
        second=await work_sql(ids,'SELECT claim_outbox_delivery(:org)');assert first['fence']!=second['fence']
        for claim,expected in [(first,False),(second,True),(second,False)]:
            assert await work_sql(ids,'SELECT apply_intake_logical_delivery(:org,:delivery,:fence)',delivery=uuid.UUID(claim['delivery_id']),fence=uuid.UUID(claim['fence'])) is expected
        ref=(await api.get('/api/document-refs/page',params={'source_id':receipt['original_id']})).json()['items'][0]
        assert len((await api.get('/api/logical-documents',params={'document_ref_id':ref['id']})).json()['items'])==1


async def test_delivery_skips_blocked_inspection_and_cancels_revoked_clean_source(migrator_session,scope_seed,store):
    from app.workers.outbox import process_once
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        _,blocked=await receive(api,pdf_bytes())
        assert await inspect_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'blocked','scanner_unavailable',mime=mime))
        assert await process_once(get_worker_session_factory(),ids['org'])
        _,clean=await receive(api,pdf_bytes(),scope_hint_id=str(ids['scope']))
        assert await inspect_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'clean',mime=mime,pages=1))
        await mutate(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:actor WHERE organization_id=:org AND scope_id=:scope AND revoked_at IS NULL'),ids)
        assert await process_once(get_worker_session_factory(),ids['org'])
    assert await read_value('SELECT count(*) FROM logical_document WHERE organization_id=:org',ids)==0
    for receipt,state in [(blocked,'skipped'),(clean,'cancelled')]:
        assert await read_value('SELECT d.status FROM outbox_delivery d JOIN outbox_event e ON e.id=d.event_id WHERE e.resource_id=:original',
          {**ids,'original':uuid.UUID(receipt['original_id'])})==state


async def test_delivery_reuses_manual_whole_source_under_concurrent_consumers(migrator_session,scope_seed,store):
    import asyncio
    from app.workers.outbox import process_once
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        _,receipt=await receive(api,pdf_bytes())
        assert await inspect_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'clean',mime=mime,pages=1))
        ref=(await api.get('/api/document-refs/page',params={'source_id':receipt['original_id']})).json()['items'][0]
        manual=await api.post('/api/logical-documents',json={'document_ref_id':ref['id'],'page_start':1,'page_end':1,'reason':'Manual candidate'},headers={'Idempotency-Key':str(uuid.uuid4())})
        assert manual.status_code==201,manual.text
        assert sorted(await asyncio.gather(*[process_once(get_worker_session_factory(),ids['org']) for _ in range(2)]))==[False,True]
        children=(await api.get('/api/logical-documents',params={'document_ref_id':ref['id']})).json()['items']
        assert children==[manual.json()] and children[0]['origin']=='manual'
        assert await read_value('SELECT logical_document_id FROM outbox_delivery WHERE organization_id=:org',ids)==uuid.UUID(manual.json()['id'])


async def test_delivery_transaction_rollback_retains_pending_work_and_retry_is_atomic(migrator_session,scope_seed,store):
    from app.core.db import set_tenant_context
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        await receive(api,pdf_bytes())
        assert await inspect_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'clean',mime=mime,pages=1))
    claim=await work_sql(ids,'SELECT claim_outbox_delivery(:org)')
    params={**ids,'delivery':uuid.UUID(claim['delivery_id']),'fence':uuid.UUID(claim['fence'])}
    statement='SELECT apply_intake_logical_delivery(:org,:delivery,:fence)'
    pending="SELECT count(*) FROM jsonb_array_elements(access_disposition_work(:org)) w WHERE w->>'kind'='outbox_delivery'"
    assert await read_value(pending,ids)==1
    with pytest.raises(RuntimeError,match='simulated crash'):
        async with get_worker_session_factory()() as db,db.begin():
            await set_tenant_context(db,ids['org'])
            assert await db.scalar(text(statement),params)
            raise RuntimeError('simulated crash before commit')
    assert await read_value('SELECT count(*) FROM logical_document WHERE organization_id=:org',ids)==0
    assert await read_value(pending,ids)==1
    assert await work_sql(ids,statement,delivery=params['delivery'],fence=params['fence'])
    assert await read_value(pending,ids)==0
    assert await read_value('SELECT count(*) FROM logical_document WHERE organization_id=:org',ids)==1


async def test_outbox_attempt_budget_and_raw_runtime_boundary(migrator_session,scope_seed,store):
    from sqlalchemy.exc import DBAPIError
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        await receive(api,pdf_bytes())
        assert await inspect_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'clean',mime=mime,pages=1))
    for statement in ['SELECT claim_outbox_delivery(:org)','SELECT count(*) FROM outbox_delivery','DELETE FROM outbox_delivery_attempt RETURNING id']:
        with pytest.raises(DBAPIError,match='permission denied|runtime required'):
            await read_value(statement,ids,actor_key='clerk')
    for _ in range(3):
        claim=await work_sql(ids,'SELECT claim_outbox_delivery(:org)');assert claim is not None
        await mutate(text("UPDATE outbox_delivery SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:delivery"),{'delivery':uuid.UUID(claim['delivery_id'])})
    assert await work_sql(ids,'SELECT claim_outbox_delivery(:org)') is None
    assert await read_value('SELECT status FROM outbox_delivery WHERE organization_id=:org',ids)=='blocked'
    assert await read_value('SELECT count(*) FROM outbox_delivery_attempt WHERE organization_id=:org AND finished_at IS NOT NULL',ids)==3
    assert await read_value('SELECT count(*) FROM logical_document WHERE organization_id=:org',ids)==0


async def test_old_blocked_event_cannot_adopt_later_clean_inspection(migrator_session,scope_seed,store):
    from app.workers.outbox import process_once
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        batch,receipt=await receive(api,pdf_bytes())
        assert await inspect_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'blocked','scanner_unavailable',mime=mime))
        item=(await api.get('/api/intake-batches/'+batch['id'])).json()['items'][0]
        assert (await api.post('/api/intake-batches/retry',json={'job_id':receipt['job_id'],'expected_fence':item['fence'],'reason':'Scanner restored'},headers={'Idempotency-Key':str(uuid.uuid4())})).status_code==202
        assert await inspect_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'clean',mime=mime,pages=1))
        assert await process_once(get_worker_session_factory(),ids['org'])
        assert await read_value('SELECT count(*) FROM logical_document WHERE organization_id=:org',ids)==0
        assert await process_once(get_worker_session_factory(),ids['org'])
        assert await read_value('SELECT count(*) FROM logical_document WHERE organization_id=:org',ids)==1


async def test_outbox_migration_backfills_exact_events_and_retains_delivery_history(migrator_session,scope_seed,store):
    import importlib
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from tests.conftest import MIGRATOR_SYNC_URL
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        await receive(api,pdf_bytes())
        assert await inspect_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'clean',mime=mime,pages=1))
    migration=importlib.import_module('migrations.versions.0097_internal_outbox_delivery');engine=create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.begin() as db,Operations.context(MigrationContext.configure(db)):
            with pytest.raises(DBAPIError,match='Retain outbox'),db.begin_nested():migration.downgrade()
            point=db.begin_nested()
            try:
                db.execute(text('TRUNCATE logical_document,outbox_delivery_attempt,outbox_delivery CASCADE'))
                before=db.scalar(text("SELECT count(*) FROM outbox_event WHERE organization_id=:org AND event_type='intake.inspected'"),ids)
                oid=db.scalar(text("SELECT 'access_disposition_work(uuid)'::regprocedure::oid"))
                db.execute(text('TRUNCATE source_partition_revision CASCADE'))
                importlib.import_module('migrations.versions.0100_source_partitions').downgrade()
                importlib.import_module('migrations.versions.0099_intake_extraction_status').downgrade()
                importlib.import_module('migrations.versions.0098_intake_extraction_jobs').downgrade()
                migration.downgrade();migration.upgrade()
                assert db.scalar(text("SELECT 'access_disposition_work(uuid)'::regprocedure::oid"))==oid
                assert db.scalar(text('SELECT count(*) FROM outbox_delivery WHERE organization_id=:org'),ids)==before==1
            finally:point.rollback()
        assert await read_value('SELECT count(*) FROM outbox_delivery WHERE organization_id=:org',ids)==1
    finally:engine.dispose()


async def test_automatic_delivery_obeys_the_shared_logical_source_cap(migrator_session,scope_seed,store):
    from app.workers.outbox import process_once
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        _,receipt=await receive(api,pdf_bytes(20))
        assert await inspect_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'clean',mime=mime,pages=20))
        ref=(await api.get('/api/document-refs/page',params={'source_id':receipt['original_id']})).json()['items'][0]
        ranges=[(start,end) for start in range(1,21) for end in range(start,21) if (start,end)!=(1,20)]
        for start,end in ranges[:200]:
            reply=await api.post('/api/logical-documents',json={'document_ref_id':ref['id'],'page_start':start,'page_end':end,'reason':'Synthetic saturation candidate'},headers={'Idempotency-Key':str(uuid.uuid4())})
            assert reply.status_code==201,reply.text
        assert await process_once(get_worker_session_factory(),ids['org'])
        assert await read_value('SELECT status FROM outbox_delivery WHERE organization_id=:org',ids)=='blocked'
        assert await read_value('SELECT error_code FROM outbox_delivery WHERE organization_id=:org',ids)=='logical_child_limit'
        assert await read_value('SELECT count(*) FROM logical_document WHERE organization_id=:org',ids)==200
        assert len((await api.get('/api/logical-documents',params={'document_ref_id':ref['id']})).json()['items'])==200
