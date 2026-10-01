"""Durable queue, bounded retry and fencing in short caller-owned transactions."""
import uuid
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
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

    async def enqueue(self, document):
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
        attempt = await self.attempts.create(Attempt(document_id=document.id,
            attempt_no=len(history) + 1, status="queued"))
        document.processing_status = "queued"
        document.processing_error_code = None
        await self.session.flush()
        return attempt

    async def claim(self):
        document_id = await self.attempts.next_document()
        if document_id is None:
            return None
        document = await self.documents.get_by_id_for_update(document_id)
        history = await self.attempts.history(document_id)
        current = history[0]
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
        document = await self.documents.get_by_id_for_update(claim["document_id"])
        attempt = await self.attempts.get_by_id_for_update(claim["attempt_id"])
        now = datetime.now(timezone.utc)
        if attempt is None or attempt.status != "running" or attempt.token != claim["token"] or attempt.lease_until < now:
            return False
        if document.released_at or document.status != "active":
            result = {"status": "canceled", "error_code": "source_no_longer_editable", "payload": None}
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
        return True
