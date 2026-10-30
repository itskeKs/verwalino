"""Shared dispatch is serial, fair across stage/tenant pairs and text-safe."""
import asyncio
import uuid
import pytest
from tests.test_invoice_extraction_worker import worker_tenant,pdf_bytes  # noqa: F401
from tests.test_invoice_source_custody import store  # noqa: F401

pytestmark=pytest.mark.asyncio


async def test_dispatch_rotates_tenants_and_stages_without_duplicate_slots():
    from app.workers.workflow_runner import WorkflowRunner
    first,second=uuid.uuid4(),uuid.uuid4();seen=[]
    async def organizations():return (first,second,first)
    def adapter(stage):
        async def run(org):seen.append((org,stage));return 'PROCESSED'
        return run
    runner=WorkflowRunner(organizations,{'inspection':adapter('inspection'),'invoice':adapter('invoice')})
    for _ in range(5):await runner.run_once()
    assert seen==[(first,'inspection'),(first,'invoice'),(second,'inspection'),(second,'invoice'),(first,'inspection')]


async def test_idle_and_failed_stages_do_not_starve_work_or_log_source_text(caplog):
    from app.workers.workflow_runner import WorkflowRunner
    org=uuid.uuid4();seen=[]
    async def organizations():return (org,)
    async def idle(org):seen.append('idle');return 'IDLE'
    async def broken(org):seen.append('broken');raise RuntimeError('PRIVATE DOCUMENT FRAGMENT')
    async def working(org):seen.append('working');return 'PROCESSED'
    runner=WorkflowRunner(organizations,{'idle':idle,'broken':broken,'working':working})
    assert (await runner.run_once()).code=='STAGE_ERROR'
    assert (await runner.run_once()).code=='PROCESSED'
    assert seen==['idle','broken','working']
    assert 'PRIVATE DOCUMENT FRAGMENT' not in caplog.text


async def test_parallel_ticks_remain_serial_and_cancellation_propagates():
    from app.workers.workflow_runner import WorkflowRunner
    active=0;peak=0;started=asyncio.Event();release=asyncio.Event()
    async def organizations():return (uuid.UUID(int=1),)
    async def run(org):
        nonlocal active,peak
        active+=1;peak=max(peak,active);started.set()
        try:await release.wait();return 'PROCESSED'
        finally:active-=1
    runner=WorkflowRunner(organizations,{'one':run})
    first=asyncio.create_task(runner.run_once());await started.wait()
    second=asyncio.create_task(runner.run_once());first.cancel()
    with pytest.raises(asyncio.CancelledError):await first
    release.set();assert (await second).code=='PROCESSED'
    assert peak==1 and active==0


async def test_shared_invoice_adapter_preserves_live_lease_then_reconciles_expired_attempt(worker_tenant,store):
    from datetime import datetime,timedelta,timezone
    from sqlalchemy import text
    from app.core.db import get_worker_session_factory,set_tenant_context
    from app.core.invoice_ocr import extract_invoice
    from app.services.invoice_processing_service import InvoiceProcessingService
    from app.workers.workflow_runner import WorkflowRunner,build_adapters
    api,ids=worker_tenant;org=ids['org_a_id']
    document=(await api.post('/api/invoice-documents/upload',files={'file':('invoice.pdf',pdf_bytes(),'application/pdf')})).json()
    factory=get_worker_session_factory()
    async with factory() as db,db.begin():
        await set_tenant_context(db,org);old_claim=await InvoiceProcessingService(db,org).claim()
    async def organizations():return (org,)
    runner=WorkflowRunner(organizations,build_adapters(['invoice_extraction'],session_factory=factory))
    assert (await runner.run_once()).code=='IDLE'
    async with factory() as db,db.begin():
        await set_tenant_context(db,org)
        await db.execute(text('UPDATE invoice_processing_attempt SET lease_until=:past WHERE id=:id'),{'past':datetime.now(timezone.utc)-timedelta(seconds=1),'id':old_claim['attempt_id']})
    assert (await runner.run_once()).code=='PROCESSED'
    attempts=(await api.get('/api/invoice-documents/'+document['id']+'/processing-attempts')).json()
    assert [item['status'] for item in attempts]==['succeeded','timeout']
    async with factory() as db,db.begin():
        await set_tenant_context(db,org)
        assert not await InvoiceProcessingService(db,org).finish(old_claim,extract_invoice(pdf_bytes(),'application/pdf'))
    ref=(await api.get('/api/document-refs/page',params={'source_id':document['id']})).json()['items'][0]
    assert len((await api.get('/api/document-refs/'+ref['id']+'/extractions')).json()['items'])==1


async def test_declaration_adapter_claims_only_the_dispatched_tenant(worker_tenant,seed_data,monkeypatch):
    from app.core.config import settings
    from app.workers.declaration_import import DeclarationImportWorker
    from app.workers.workflow_runner import WorkflowRunner,build_adapters
    from tests.test_declaration_worker import _create_import
    from app.core.db import get_worker_tenant_session
    from app.repositories.declaration_import_repo import DeclarationImportRepository
    monkeypatch.setattr(settings,'declaration_import_enabled',True)
    _,isolated=worker_tenant;org=isolated['org_a_id']
    own=await _create_import(org,isolated['user_a_admin_id'],status='QUEUED')
    foreign=await _create_import(seed_data['org_b_id'],seed_data['user_b_admin_id'],status='QUEUED')
    seen=[]
    class Pipeline:
        async def run(self,lease):
            seen.append((lease.organization_id,lease.import_id))
            async with get_worker_tenant_session(lease.organization_id) as db:
                assert await DeclarationImportRepository(db,lease.organization_id).transition_with_lease(lease,{'QUEUED'},'FAILED',{'current_stage':'failed','error_code':'SYNTHETIC','error_message':'SYNTHETIC','lease_owner':None,'lease_token':None,'lease_expires_at':None})
    worker=DeclarationImportWorker(Pipeline())
    async def forbidden():raise AssertionError('A selected organization must not trigger global enumeration')
    monkeypatch.setattr(worker,'_organization_ids',forbidden)
    async def organizations():return (org,)
    runner=WorkflowRunner(organizations,build_adapters(['declaration_import'],declaration_worker=worker))
    assert (await runner.run_once()).code=='PROCESSED'
    assert seen==[(org,own.id)]
    async with get_worker_tenant_session(seed_data['org_b_id']) as db:
        row=await DeclarationImportRepository(db,seed_data['org_b_id']).get_by_id(foreign.id)
        assert row.lease_token is None and row.status=='QUEUED'
        # This synthetic foreign job is deliberately never dispatched. Leave
        # it terminal so later global-worker tests cannot accidentally claim it.
        row.status='CANCELLED'
        await db.flush()


async def test_disabled_declaration_and_discovery_failure_are_safe(monkeypatch,caplog):
    from app.core.config import settings
    from app.workers.workflow_runner import WorkflowRunner,build_adapters
    monkeypatch.setattr(settings,'declaration_import_enabled',False)
    class ForbiddenWorker:
        async def run_once(self,**kwargs):raise AssertionError('Disabled declaration was invoked')
    async def organizations():return (uuid.UUID(int=1),)
    runner=WorkflowRunner(organizations,build_adapters(['declaration_import'],declaration_worker=ForbiddenWorker()))
    assert (await runner.run_once()).code=='IDLE'
    async def failure():raise RuntimeError('PRIVATE DISCOVERY DETAIL')
    runner=WorkflowRunner(failure,{'stage':lambda org:None})
    assert (await runner.run_once()).code=='ORGANIZATION_DISCOVERY_ERROR'
    assert 'PRIVATE DISCOVERY DETAIL' not in caplog.text


async def test_inbox_healthcheck_fails_closed_without_scanner(monkeypatch,caplog):
    from app.workers import workflow_runner
    async def unavailable(stages):
        raise RuntimeError('PRIVATE SCANNER DETAIL')
    monkeypatch.setattr(workflow_runner,'check_readiness',unavailable)
    assert await workflow_runner.main(['--healthcheck'])==1
    assert 'readiness_failed' in caplog.text
    assert 'PRIVATE SCANNER DETAIL' not in caplog.text


async def test_inbox_healthcheck_checks_dependencies_without_claiming_jobs(monkeypatch):
    from app.workers import workflow_runner
    seen=[]
    async def ready(stages):seen.extend(stages)
    def forbidden(*args,**kwargs):raise AssertionError('Healthcheck must not build or run adapters')
    monkeypatch.setattr(workflow_runner,'check_readiness',ready)
    monkeypatch.setattr(workflow_runner,'build_adapters',forbidden)
    assert await workflow_runner.main(['--healthcheck'])==0
    assert seen==['intake_inspection','outbox','intake_extraction']


async def test_inbox_stage_failure_is_parked_then_explicitly_retried_without_duplicate_work(worker_tenant,store,monkeypatch):
    from app.core.intake_inspection import result
    from app.workers import workflow
    from app.workers.workflow_runner import WorkflowRunner,build_adapters
    from tests.test_intake_batches import receive
    api,ids=worker_tenant;org=ids['org_a_id']
    batch,receipt=await receive(api,pdf_bytes())
    real_process=workflow.process_once;failed=True
    def inspector(data,mime):
        if failed:raise RuntimeError('PRIVATE SCANNER FAILURE')
        return result(data,'clean',mime=mime,pages=1)
    async def inspection(factory,org):return await real_process(factory,org,inspector=inspector)
    monkeypatch.setattr(workflow,'process_once',inspection)
    async def organizations():return (org,)
    runner=WorkflowRunner(organizations,build_adapters(['intake_inspection','outbox']))
    assert (await runner.run_once()).code=='PROCESSED'
    state=(await api.get('/api/intake-batches/'+batch['id'])).json()
    assert state['items'][0]['error_code']=='inspector_failed' and state['counts']['failed']==1
    # Failure notifications are durably delivered as skipped; they create no child.
    assert (await runner.run_once()).stage=='outbox'
    assert (await runner.run_once()).code=='IDLE'
    assert (await api.get('/api/document-refs/page',params={'source_id':receipt['original_id']})).json()['items']==[]
    failed=False
    retried=await api.post('/api/intake-batches/retry',headers={'Idempotency-Key':str(uuid.uuid4())},json={
        'job_id':receipt['job_id'],'expected_fence':state['items'][0]['fence'],'reason':'Scanner restored'})
    assert retried.status_code==202,retried.text
    assert (await runner.run_once()).stage=='intake_inspection'
    assert (await runner.run_once()).stage=='outbox'
    assert (await runner.run_once()).code=='IDLE'
    refs=(await api.get('/api/document-refs/page',params={'source_id':receipt['original_id']})).json()['items']
    assert len(refs)==1
    assert len((await api.get('/api/logical-documents',params={'document_ref_id':refs[0]['id']})).json()['items'])==1
    assert (await api.get('/api/cost-entries')).json()==[]


async def test_cancelled_local_processor_drains_real_thread_before_next_tick(monkeypatch):
    import threading
    from app.workers.workflow_runner import WorkflowRunner,build_adapters
    from app.workers import invoice_extraction
    from app.core.worker_process import run_bounded_processor
    started=threading.Event();release=threading.Event();active=0;peak=0
    def blocking_processor():
        nonlocal active,peak
        active+=1;peak=max(peak,active);started.set()
        try:assert release.wait(5)
        finally:active-=1
    async def process(factory,org):await run_bounded_processor(blocking_processor);return True
    monkeypatch.setattr(invoice_extraction,'process_once',process)
    async def organizations():return (uuid.UUID(int=1),)
    runner=WorkflowRunner(organizations,build_adapters(['invoice_extraction'],session_factory=object()))
    first=asyncio.create_task(runner.run_once())
    try:
        assert await asyncio.to_thread(started.wait,2)
        first.cancel();await asyncio.sleep(0)
        second=asyncio.create_task(runner.run_once())
        first.cancel();await asyncio.sleep(0)
        assert not first.done() and not second.done() and active==1
    finally:release.set()
    with pytest.raises(asyncio.CancelledError):await first
    assert (await second).code=='PROCESSED'
    assert peak==1 and active==0


async def test_queue_lock_wait_is_cancellable_before_any_processor_starts(worker_tenant,store):
    from sqlalchemy import text
    from app.core.db import get_worker_session_factory,set_tenant_context
    from app.workers.workflow_runner import WorkflowRunner,build_adapters
    from tests.test_invoice_source_custody import wait_for_advisory_waiter
    api,seed=worker_tenant;org=seed['org_a_id']
    document=(await api.post('/api/invoice-documents/upload',files={'file':('invoice.pdf',pdf_bytes(),'application/pdf')})).json()
    factory=get_worker_session_factory()
    async def organizations():return (org,)
    runner=WorkflowRunner(organizations,build_adapters(['invoice_extraction'],session_factory=factory))
    tick=None
    try:
        async with factory() as holder,holder.begin():
            await set_tenant_context(holder,org)
            await holder.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('financial-inputs:'||CAST(:org AS text),0))"),{'org':str(org)})
            tick=asyncio.create_task(runner.run_once())
            await wait_for_advisory_waiter({'org':org},await holder.scalar(text('SELECT pg_backend_pid()')))
            tick.cancel()
            done,_=await asyncio.wait({tick},timeout=1)
            assert tick in done,'Cancellation must not wait for the held queue lock'
            with pytest.raises(asyncio.CancelledError):await tick
    finally:
        # Release the holder before draining even if the assertion fails.
        if tick is not None and not tick.done():tick.cancel();await asyncio.gather(tick,return_exceptions=True)
    current=(await api.get('/api/invoice-documents/'+document['id'])).json()
    assert current['processing_status']=='queued'
    assert (await runner.run_once()).code=='PROCESSED'
