"""Durable queue, bounded retry and fencing in short caller-owned transactions."""
import uuid
import json
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from sqlalchemy import select, text
from app.models.accounting_scope import AccountingScopeBinding
from app.core.config import settings
from app.models.invoice_processing_attempt import InvoiceProcessingAttempt as Attempt
from app.repositories.invoice_document_repo import InvoiceDocumentRepository
from app.repositories.invoice_processing_repo import InvoiceProcessingRepository


class InvoiceProcessingService:
    def __init__(self, session, org_id):
        self.session = session
        self.org_id = org_id
        self.documents = InvoiceDocumentRepository(session, org_id)
        self.attempts = InvoiceProcessingRepository(session, org_id)

    async def enqueue(self, document, *, initiated_by):
        history = await self.attempts.history(document.id)
        if history and history[0].status in {"queued", "running"}:
            return history[0]
        if document.released_at or document.status != "active":
            raise HTTPException(409, "Released or superseded originals cannot be reprocessed")
        if len(history) >= 3:
            raise HTTPException(409, "Extraction retry budget exhausted; review the original manually")
        if not settings.invoice_extraction_enabled:
            document.processing_status = "unavailable"
            document.processing_error_code = "processing_disabled"
            await self.session.flush()
            return None
        if await self.attempts.queue_count_locked() >= settings.invoice_queue_limit:
            # Preserve the saved original and a visible manual-review outcome.
            document.processing_status = "needs_review"
            document.processing_error_code = "queue_full"
            await self.session.flush()
            return None
        requested_scope = await self.session.scalar(select(AccountingScopeBinding.scope_id).where(
            AccountingScopeBinding.organization_id == self.org_id, AccountingScopeBinding.cost_entry_id == document.cost_entry_id)) if document.cost_entry_id else None
        attempt = await self.attempts.create(Attempt(document_id=document.id, initiated_by=initiated_by,
            requested_scope_id=requested_scope, attempt_no=len(history) + 1, status="queued"))
        document.processing_status = "queued"
        document.processing_error_code = None
        await self.session.flush()
        return attempt

    async def claim(self):
        from app.services.financial_scope_service import FinancialScopeService
        await FinancialScopeService(self.session, self.org_id, None).lock_inputs()
        current = await self.attempts.principal_metadata()
        if current is None:
            return None
        document_id = current.document_id
        if not await self._enter_principal(current):
            await self._cancel_denied(current)
            return None
        document = await self.documents.get_by_id_for_update(document_id)
        if document is None:
            await self._cancel_denied(current)
            return None
        # Refresh after waiting on the document lock; another worker may have claimed it.
        history = await self.attempts.history(document_id)
        current = history[0]
        if not await self._enter_principal(current):
            await self._cancel_denied(current)
            return None
        now = datetime.now(timezone.utc)
        if current.status == "running" and current.lease_until >= now:
            return None
        if current.status not in {"running", "queued"}:
            return None
        if document.released_at or document.status != "active":
            current.status = "canceled"
            current.error_code = "source_no_longer_editable"
            current.completed_at = now
            document.processing_status = "canceled"
            await self.session.flush()
            return None
        if current.status == "running":
            current.status = "timeout"
            current.error_code = "worker_lease_expired"
            current.completed_at = now
            await self.session.flush()
            if current.attempt_no >= 3:
                document.processing_status = "failed"
                document.processing_error_code = "retry_budget_exhausted"
                await self.session.flush()
                return None
            current = await self.attempts.create(Attempt(document_id=document_id,
                initiated_by=current.initiated_by, requested_scope_id=current.requested_scope_id,
                attempt_no=current.attempt_no + 1, status="queued"))
        current.status = "running"
        current.token = uuid.uuid4()
        current.lease_until = now + timedelta(seconds=150)
        document.processing_status = "running"
        await self.session.flush()
        return {"attempt_id": current.id, "token": current.token, "document_id": document.id,
                "storage_uri": document.storage_uri, "sha256": document.content_sha256,
                "mime_type": document.mime_type}

    async def finish(self, claim, result):
        from app.services.financial_scope_service import FinancialScopeService
        await FinancialScopeService(self.session, self.org_id, None).lock_inputs()
        pending = await self.attempts.principal_metadata(claim['attempt_id'])
        if pending is None or pending.document_id != claim['document_id'] or pending.token != claim['token']:
            return False
        if not await self._enter_principal(pending):
            await self._cancel_denied(pending, fence=claim['token'])
            return False
        document = await self.documents.get_by_id_for_update(claim["document_id"])
        if document is None:
            await self._cancel_denied(pending, fence=claim['token'])
            return False
        attempt = await self.attempts.get_by_id_for_update(claim["attempt_id"])
        now = datetime.now(timezone.utc)
        if attempt is None or attempt.status != "running" or attempt.token != claim["token"] or attempt.lease_until < now:
            return False
        if document.released_at or document.status != "active":
            from app.core.invoice_ocr import outcome
            result = outcome("canceled", "source_no_longer_editable")
        from app.core.extraction_evidence import valid_invoice_outcome
        if not valid_invoice_outcome(result):
            from app.core.invoice_ocr import outcome
            result = outcome("failed", "processor_failed")
        attempt.status = result["status"]
        attempt.error_code = result.get("error_code")
        attempt.processor_version = result.get("processor_version")
        attempt.completed_at = now
        document.processing_status = attempt.status
        document.processing_error_code = attempt.error_code
        # Proposals never update a cost draft, so human edits survive every retry.
        if result.get("payload") is not None:
            document.extraction_payload = {**result["payload"], "attempt_id": str(attempt.id),
                                          "processor_version": attempt.processor_version}
        await self.session.flush()
        await self.session.execute(text('SELECT record_invoice_extraction_revision(:org,:attempt,:token,CAST(:result AS jsonb))'),
            {'org':self.org_id,'attempt':attempt.id,'token':claim['token'],'result':json.dumps(result)})
        return True

    async def _enter_principal(self, attempt):
        await self.session.execute(text("SELECT set_config('app.current_user_id',:actor,true),set_config('app.current_scope_id','',true)"),
            {'actor': str(attempt.initiated_by) if attempt.initiated_by else ''})
        return bool(await self.session.scalar(text('SELECT invoice_attempt_authorized(:org,:attempt)'),
            {'org': self.org_id, 'attempt': attempt.id}))

    async def _cancel_denied(self, attempt, *, fence=None):
        return await self.session.scalar(text('SELECT cancel_unauthorized_invoice_attempt(:org,:attempt,:fence)'),
            {'org': self.org_id, 'attempt': attempt.id, 'fence': fence if fence is not None else attempt.token})
