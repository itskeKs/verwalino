"""Fenced logical-source extraction; no transaction spans processor work or a model request.

This stage reaches the organization's provider, so it runs in the declaration worker (``core/invoice_claimant.py``,
``workers/declaration_import.py``) and in no other process: the inbox worker has no session secret and no route to a
provider.

A structured original (XRechnung XML, a PDF with valid embedded ZUGFeRD/Factur-X data) is parsed by the deterministic
local stage and never reaches a model, and needs no decision how it divides. The selected pages of an unstructured PDF,
PNG or JPEG go to the organization's two models only when the organization has opted in and, for an original of more than
one page, a person has decided how it divides (a confirmed source partition lists the document: one document each, with
its own pages): the document reading model reads them and the extraction model fills the proposal
(``declarations/intake_pipeline.py``, issue #501). Until that decision the job is parked (``awaiting_split``) and nothing
is sent; without the opt-in the original stays and the extraction ends with the structured reason. Every outcome is a
proposal that a person reviews.
"""
import hashlib
import json
import shutil
import uuid

from app.workers.error_tracking import report_recorded_failure, tracked_job
from app.core.locking import retry_worker_completion
from sqlalchemy import text
from app.core.db import set_tenant_context
from app.core.intake_extraction import UNSTRUCTURED,run_isolated_extraction,valid_inspection,valid_result,outcome
from app.core.storage import get_store
from app.core.worker_process import run_bounded_processor
from app.declarations.intake_pipeline import IntakeModelPipeline
from app.declarations.invoice_pipeline import (
    INVOICE_MAX_PAGES,INVOICE_PAGE_LIMIT,InvoicePage,close_invoice_providers,create_invoice_providers,pages_need_reading)
from app.declarations.provider import InvoiceCategory
from app.repositories.cost_category_repo import CostCategoryRepository
from app.services.ai_settings_service import (
    INVOICE_AI_KEY_UNREADABLE,AiKeyUnreadableError,effective_ai_settings,invoice_import_blocker)


# The state of the decision how the original divides that the claim carries (``intake_split_state``).
SPLIT_DECIDED='decided'


def check_readiness():
    """The local tool this worker needs: the PDF renderer for pages without a text layer. No provider is called."""
    if not shutil.which("pdftoppm"):
        raise RuntimeError("pdf_renderer_unavailable")


async def model_outcome(session_factory,org_id,claim,data,inspected,providers):
    """The outcome for an unstructured original: through the organization's two models, or the reason it cannot go there.

    No provider is constructed and nothing leaves the host unless the organization's AI import switch, confirmed key
    and models allow it; a refusal keeps the original and ends the extraction without a proposal. Such a refusal is a
    local decision and carries the local processor version; only a run of the models carries ``intake-model-v1``.

    ``None`` means that the job is to be parked instead: the original has more than one page and nobody has decided yet
    how it divides, or the latest decision leaves this document out. Nothing is sent then either. A run that was under way
    when somebody divided the original differently stops before its next request and ends the same way: the job is parked
    (or queued again, if the new division lists the document), not failed.
    """
    mime=claim['mime_type']
    def refusal(status,code):
        return outcome(data,mime,claim['page_start'],claim['page_end'],claim['verified_page_count'],status,code)
    pages=[InvoicePage(number=page['number'],text=page['text'],width=float(page['width']),height=float(page['height']))
        for page in inspected['pages']]
    async with session_factory() as db,db.begin():
        await set_tenant_context(db,org_id)
        try:effective=await effective_ai_settings(db,org_id)
        except AiKeyUnreadableError:return refusal('unavailable',INVOICE_AI_KEY_UNREADABLE)
        categories=[InvoiceCategory(item.code,item.label) for item in await CostCategoryRepository(db).list_active()]
    blocker=invoice_import_blocker(effective,needs_reading=pages_need_reading(pages),inbox=True)
    if blocker is not None:return refusal('unavailable',blocker)
    if claim['split_state']!=SPLIT_DECIDED:return None
    if len(pages)>INVOICE_MAX_PAGES:return refusal('needs_review',INVOICE_PAGE_LIMIT)
    owned=providers(effective)
    try:
        pipeline=IntakeModelPipeline(session_factory=session_factory,org_id=org_id,claim=claim,data=data,mime=mime,
            effective=effective,providers=owned,categories=categories)
        answer=await pipeline.run(pages)
        return None if pipeline.division_changed else answer
    finally:await close_invoice_providers(owned)


async def process_once(session_factory,org_id,*,processor=run_isolated_extraction,providers=create_invoice_providers):
    async with session_factory() as db,db.begin():
        await set_tenant_context(db,org_id)
        claim=await db.scalar(text('SELECT claim_intake_extraction(:org)'),{'org':org_id})
    if claim is None:return False
    with tracked_job('intake_extraction',claim['job_id']):
        params={'org':org_id,'job':uuid.UUID(claim['job_id']),'fence':uuid.UUID(claim['fence'])}
        def failure(code):
            answer=outcome(b'',claim['mime_type'],claim['page_start'],claim['page_end'],claim['verified_page_count'],'failed',code)
            answer.update(content_sha256=claim['content_sha256'],content_byte_size=claim['content_byte_size'])
            return answer
        chunks=[];total=0;error=None;crash=None
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
                if isinstance(answer,dict) and answer.get('status')==UNSTRUCTURED:
                    answer=await model_outcome(session_factory,org_id,claim,data,answer,providers) if valid_inspection(answer,*args) else failure('processor_failed')
                if answer is not None and not valid_result(answer,*args):answer=failure('processor_failed')
            except Exception as caught:
                crash=caught;answer=failure('processor_failed')
        if answer is None:
            # Parked until a person decides how the original divides: no outcome, no proposal, nothing sent.
            async def park():
                async with session_factory() as db,db.begin():
                    await set_tenant_context(db,org_id)
                    return bool(await db.scalar(text('SELECT hold_intake_extraction(:org,:job,:fence)'),params))
            return await retry_worker_completion(park)
        report_recorded_failure(answer.get('error_code'),crash)
        async def finish():
            async with session_factory() as db,db.begin():
                await set_tenant_context(db,org_id)
                return bool(await db.scalar(text('SELECT complete_intake_extraction(:org,:job,:fence,CAST(:result AS jsonb))'),
                    {**params,'result':json.dumps(answer)}))
        # On exhaustion, WorkflowRunner reports STAGE_ERROR; claim retries the expired lease, then blocks at its cap.
        return await retry_worker_completion(finish)
