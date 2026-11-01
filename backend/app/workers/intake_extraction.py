"""Fenced logical-source extraction; no transaction spans processor work."""
import hashlib
import json
import uuid

from app.core.request_id import correlation_context
from app.core.locking import retry_worker_completion
from sqlalchemy import text
from app.core.db import set_tenant_context
from app.core.intake_extraction import run_isolated_extraction,valid_result,outcome
from app.core.storage import get_store
from app.core.worker_process import run_bounded_processor


async def process_once(session_factory,org_id,*,processor=run_isolated_extraction):
    async with session_factory() as db,db.begin():
        await set_tenant_context(db,org_id)
        claim=await db.scalar(text('SELECT claim_intake_extraction(:org)'),{'org':org_id})
    if claim is None:return False
    with correlation_context(claim['job_id']):
        params={'org':org_id,'job':uuid.UUID(claim['job_id']),'fence':uuid.UUID(claim['fence'])}
        def failure(code):
            answer=outcome(b'',claim['mime_type'],claim['page_start'],claim['page_end'],claim['verified_page_count'],'failed',code)
            answer.update(content_sha256=claim['content_sha256'],content_byte_size=claim['content_byte_size'])
            return answer
        chunks=[];total=0;error=None
        async with session_factory() as db,db.begin():
            await set_tenant_context(db,org_id)
            context=await db.scalar(text('SELECT intake_extraction_context(:org,:job,:fence)'),params)
            if context is None:return False
            try:
                for chunk in get_store().iter_chunks(context['storage_uri']):
                    total+=len(chunk)
                    if total>context['content_byte_size']:
                        error='storage_integrity_mismatch';break
                    chunks.append(chunk)
            except (OSError,ValueError):error='stored_original_unavailable'
        data=b''.join(chunks)
        if error is None and (total!=claim['content_byte_size'] or hashlib.sha256(data).hexdigest()!=claim['content_sha256']):
            error='storage_integrity_mismatch'
        if error:answer=failure(error)
        else:
            args=(data,claim['mime_type'],claim['page_start'],claim['page_end'],claim['verified_page_count'])
            try:
                answer=await run_bounded_processor(processor,*args)
                if not valid_result(answer,*args):answer=failure('processor_failed')
            except Exception:answer=failure('processor_failed')
        async def finish():
            async with session_factory() as db,db.begin():
                await set_tenant_context(db,org_id)
                return bool(await db.scalar(text('SELECT complete_intake_extraction(:org,:job,:fence,CAST(:result AS jsonb))'),
                    {**params,'result':json.dumps(answer)}))
        # On exhaustion, WorkflowRunner reports STAGE_ERROR; claim retries the expired lease, then blocks at its cap.
        return await retry_worker_completion(finish)
