"""Inbox state follows durable preparation and extraction, not inspection alone."""
import uuid
import pytest
from app.core.db import get_worker_session_factory
from app.core.intake_inspection import result
from app.core.intake_extraction import extract_logical,outcome
from app.workers.workflow import process_once as inspect_once
from app.workers.outbox import process_once as deliver_once
from app.workers.intake_extraction import process_once as extract_once
from tests.test_financial_scope_boundary import worker_tenant,scope_seed  # noqa: F401
from tests.test_invoice_source_custody import store,fixture
from tests.test_scoped_account_statements import client
from tests.test_invoice_extraction_worker import pdf_bytes
from tests.test_intake_batches import receive

pytestmark=pytest.mark.asyncio


async def test_inbox_counts_follow_preparation_extraction_retry_and_review(migrator_session,scope_seed,store):
    ids=await fixture(migrator_session,scope_seed)
    async with client(ids) as api:
        batch,_=await receive(api,pdf_bytes())
        url='/api/intake-batches/'+batch['id']
        assert (await api.get(url)).json()['counts']['received']==1
        assert await inspect_once(get_worker_session_factory(),ids['org'],inspector=lambda data,mime:result(data,'clean',mime=mime,pages=1))
        prepared=(await api.get(url)).json()
        assert prepared['counts']['processing']==1 and prepared['counts']['review']==0
        assert prepared['items'][0]['stage']=='preparation' and prepared['items'][0]['can_export']
        assert await deliver_once(get_worker_session_factory(),ids['org'])
        extracting=(await api.get(url)).json()
        assert extracting['counts']['processing']==1 and extracting['items'][0]['stage']=='extraction'
        assert extracting['items'][0]['logical_counts']=={'total':1,'processing':1,'review':0,'failed':0}
        assert prepared['revision']!=extracting['revision']
        def unavailable(data,mime,start,end,count):return outcome(data,mime,start,end,count,'unavailable','ocr_unavailable')
        assert await extract_once(get_worker_session_factory(),ids['org'],processor=unavailable)
        failed=(await api.get(url)).json()
        assert failed['counts']['failed']==1 and failed['items'][0]['error_code']=='ocr_unavailable'
        ref=failed['items'][0]['document_ref_id']
        child=(await api.get('/api/logical-documents',params={'document_ref_id':ref})).json()['items'][0]
        status=(await api.get('/api/logical-documents/'+child['id']+'/processing')).json()
        assert (await api.post('/api/logical-documents/'+child['id']+'/retry-extraction',json={'expected_fence':status['fence'],'reason':'Local OCR restored'},headers={'Idempotency-Key':str(uuid.uuid4())})).status_code==202
        assert (await api.get(url)).json()['counts']['processing']==1
        assert await extract_once(get_worker_session_factory(),ids['org'],processor=extract_logical)
        complete=(await api.get(url)).json()
        assert complete['counts']['review']==1 and complete['counts']['confirmed']==0
        assert complete['items'][0]['stage']=='review'
        assert complete['revision']!=failed['revision']
        async with client(ids,'actor') as other:assert (await other.get(url)).status_code==404
