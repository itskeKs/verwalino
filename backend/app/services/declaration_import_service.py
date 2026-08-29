"""Draft lifecycle rules for declaration imports; no extraction runs in HTTP."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.declaration_import import DeclarationImport, DeclarationImportDocument
from app.core.config import settings
from app.models.property_document import PropertyDocument
from app.repositories.declaration_candidate_repo import DeclarationCandidateRepository
from app.repositories.declaration_import_repo import DeclarationImportRepository
from app.repositories.property_document_repo import PropertyDocumentRepository
from app.repositories.property_repo import PropertyRepository
from app.services.property_document_service import PropertyDocumentService

DECLARATION_IMPORT_DISABLED = "DECLARATION_IMPORT_DISABLED"
ACTIVE_OR_COMPLETE_STATUSES = {
    "QUEUED", "PREFLIGHT", "EXTRACTING", "RECONCILING", "RETRY_WAIT",
    "NEEDS_REVIEW", "APPLYING", "APPLIED",
}


def require_declaration_import_enabled() -> None:
    """Reject write-side import work when the deployment switch is off."""
    if not settings.declaration_import_enabled:
        raise HTTPException(status_code=503, detail=DECLARATION_IMPORT_DISABLED)


class DeclarationImportService:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID):
        self._session = session
        self._org_id = org_id
        self._user_id = user_id
        self._imports = DeclarationImportRepository(session, org_id)
        self._documents = PropertyDocumentRepository(session, org_id)
        self._candidates = DeclarationCandidateRepository(session, org_id)

    async def create(
        self, mode: str, target_property_id: uuid.UUID | None
    ) -> DeclarationImport:
        require_declaration_import_enabled()
        if mode not in {"NEW_PROPERTY", "EXISTING_PROPERTY"}:
            raise HTTPException(status_code=422, detail="Invalid import mode")
        if mode == "EXISTING_PROPERTY" and target_property_id is None:
            raise HTTPException(status_code=422, detail="target_property_id is required")
        if mode == "NEW_PROPERTY" and target_property_id is not None:
            raise HTTPException(status_code=422, detail="target_property_id is not allowed")
        if target_property_id is not None and await PropertyRepository(
            self._session, self._org_id
        ).get_by_id(target_property_id) is None:
            raise HTTPException(status_code=404, detail="Property not found")
        return await self._imports.create(
            DeclarationImport(
                organization_id=self._org_id,
                mode=mode,
                target_property_id=target_property_id,
                applied_property_id=None,
                status="DRAFT",
                initiated_by=self._user_id,
                max_cost_usd=settings.declaration_max_cost_usd,
            )
        )

    async def get(self, import_id: uuid.UUID) -> DeclarationImport:
        item = await self._imports.get_by_id(import_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Declaration import not found")
        return item

    async def list_documents(
        self, import_id: uuid.UUID
    ) -> list[tuple[DeclarationImportDocument, PropertyDocument]]:
        await self.get(import_id)
        return list(await self._imports.list_documents(import_id))

    async def upload_document(
        self,
        import_id: uuid.UUID,
        file_bytes: bytes,
        *,
        original_filename: str,
        mime_type: str,
        document_role: str,
        sequence_no: int,
    ) -> PropertyDocument:
        require_declaration_import_enabled()
        item = await self._get_for_update(import_id)
        self._require_draft(item)
        if await self._imports.count_documents(import_id) >= settings.declaration_max_documents:
            raise HTTPException(status_code=422, detail="DOCUMENT_LIMIT_REACHED")
        if await self._imports.get_by_sequence(import_id, sequence_no) is not None:
            raise HTTPException(status_code=409, detail="DUPLICATE_DOCUMENT_SEQUENCE")
        try:
            async with self._session.begin_nested():
                document, _link = await PropertyDocumentService(
                    self._session, self._org_id, self._user_id
                ).upload(
                    import_id,
                    file_bytes,
                    original_filename=original_filename,
                    mime_type=mime_type,
                    document_role=document_role,
                    sequence_no=sequence_no,
                )
        except IntegrityError as exc:
            if "uq_declaration_import_sequence" in str(exc.orig):
                raise HTTPException(status_code=409, detail="DUPLICATE_DOCUMENT_SEQUENCE") from None
            raise
        return document

    async def update_document_metadata(
        self,
        import_id: uuid.UUID,
        document_id: uuid.UUID,
        *,
        title: str | None,
        document_role: str | None,
        sequence_no: int | None,
    ) -> tuple[PropertyDocument, DeclarationImportDocument]:
        item = await self._get_for_update(import_id)
        self._require_draft(item)
        link = await self._imports.get_document_link(import_id, document_id)
        document = await self._documents.get_by_id(document_id)
        if link is None or document is None:
            raise HTTPException(status_code=404, detail="Import document not found")
        if document_role is not None:
            from app.services.property_document_service import DOCUMENT_ROLES

            if document_role not in DOCUMENT_ROLES:
                raise HTTPException(status_code=422, detail="Invalid document_role")
        next_document_role = document_role or link.document_role
        next_title = title if title is not None else document.title
        next_sequence_no = link.sequence_no
        if sequence_no is not None:
            if sequence_no < 1:
                raise HTTPException(status_code=422, detail="sequence_no must be positive")
            other = await self._imports.get_by_sequence(import_id, sequence_no)
            if other is not None and other.id != link.id:
                raise HTTPException(status_code=409, detail="DUPLICATE_DOCUMENT_SEQUENCE")
            next_sequence_no = sequence_no
        try:
            async with self._session.begin_nested():
                link.document_role = next_document_role
                document.doc_type = next_document_role
                document.title = next_title
                link.sequence_no = next_sequence_no
                await self._session.flush()
        except IntegrityError as exc:
            if "uq_declaration_import_sequence" in str(exc.orig):
                raise HTTPException(status_code=409, detail="DUPLICATE_DOCUMENT_SEQUENCE") from None
            raise
        return document, link

    async def start(self, import_id: uuid.UUID) -> DeclarationImport:
        require_declaration_import_enabled()
        item = await self._get_for_update(import_id)
        if item.status in ACTIVE_OR_COMPLETE_STATUSES:
            return item
        if item.status == "FAILED":
            raise HTTPException(status_code=409, detail="USE_RETRY")
        if item.status == "CANCELLED":
            raise HTTPException(status_code=409, detail="IMPORT_CANCELLED")
        if item.status != "DRAFT":
            raise HTTPException(status_code=409, detail="INVALID_IMPORT_STATUS")
        if await self._imports.count_documents(import_id) == 0:
            raise HTTPException(status_code=422, detail="IMPORT_REQUIRES_DOCUMENT")
        item.status = "QUEUED"
        item.current_stage = "queued"
        await self._session.flush()
        return item

    async def cancel(self, import_id: uuid.UUID) -> DeclarationImport:
        item = await self._get_for_update(import_id)
        if item.status == "CANCELLED":
            return item
        if item.status in {"APPLYING", "APPLIED"}:
            raise HTTPException(status_code=409, detail="IMPORT_CANNOT_BE_CANCELLED")
        # Fences any worker which observed the former lease before cancellation.
        item.lease_generation = int(item.lease_generation) + 1
        item.status = "CANCELLED"
        item.current_stage = None
        await self._imports.close_running_runs_for_import(
            import_id,
            error_code="IMPORT_CANCELLED",
            completed_at=datetime.now(timezone.utc),
        )
        item.lease_owner = None
        item.lease_token = None
        item.lease_expires_at = None
        await self._session.flush()
        return item

    async def retry(self, import_id: uuid.UUID) -> DeclarationImport:
        require_declaration_import_enabled()
        item = await self._get_for_update(import_id)
        if item.status in {"QUEUED", "PREFLIGHT", "EXTRACTING", "RECONCILING"}:
            return item
        allowed_errors = {
            "PROVIDER_TIMEOUT", "PROVIDER_RATE_LIMIT", "PROVIDER_SERVER_ERROR",
            "OCR_TIMEOUT", "WORKER_INTERRUPTED",
        }
        if item.status not in {"FAILED", "RETRY_WAIT"} or item.error_code not in allowed_errors:
            raise HTTPException(status_code=409, detail="IMPORT_NOT_RETRYABLE")
        # Provider calls are batched and do not represent worker-run retries.
        # Keep this in lockstep with the worker's tenant-scoped run budget.
        if (
            await self._imports.count_extraction_runs(import_id)
            >= settings.declaration_max_attempts
            or item.estimated_cost_usd >= item.max_cost_usd
        ):
            raise HTTPException(status_code=409, detail="RETRY_BUDGET_EXHAUSTED")
        item.status = "QUEUED"
        item.current_stage = "queued"
        item.next_attempt_at = None
        item.lease_owner = None
        item.lease_token = None
        item.lease_expires_at = None
        await self._session.flush()
        return item

    async def list_candidates(self, import_id: uuid.UUID):
        await self.get(import_id)
        return await self._candidates.list_by_import(import_id)

    @staticmethod
    def _require_draft(item: DeclarationImport) -> None:
        if item.status != "DRAFT":
            raise HTTPException(status_code=409, detail="IMPORT_NOT_DRAFT")

    async def _get_for_update(self, import_id: uuid.UUID) -> DeclarationImport:
        item = await self._imports.get_by_id_for_update(import_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Declaration import not found")
        return item
