"""Mandate document service -- upload / list / delete contract documents.

Reuses the content-addressed document store (app.core.storage), the same
infrastructure as the M2 invoice documents. Unlike invoice_document, mandate
documents are NOT GoBD Buchungsbelege: they are deletable (the DB row; the
content-addressed blob stays in the store, which is harmless and GoBD-neutral).
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException

from app.core.storage import compute_sha256, get_store, make_storage_key
from app.core.uploads import MAX_UPLOAD_BYTES
from app.models.audit_event import AuditEvent
from app.models.mandate_document import MandateDocument
from app.repositories.audit_repo import AuditRepository
from app.repositories.mandate_document_repo import MandateDocumentRepository
from app.repositories.mandate_repo import MandateRepository

DOC_TYPES = frozenset(
    {"VERWALTERVERTRAG", "BANKKONTO", "VOLLMACHT", "VERSICHERUNG", "SONSTIGES"}
)
class MandateDocumentService:
    def __init__(self, session, org_id: uuid.UUID, user_id: uuid.UUID):
        self._session = session
        self._org_id = org_id
        self._user_id = user_id
        self._repo = MandateDocumentRepository(session, org_id)
        self._mandates = MandateRepository(session, org_id)
        self._audit = AuditRepository(session, org_id)

    async def _require_mandate(self, mandate_id: uuid.UUID):
        mandate = await self._mandates.get_by_id(mandate_id)
        if mandate is None:
            raise HTTPException(status_code=404, detail="Mandate not found")
        return mandate

    async def upload(
        self,
        mandate_id: uuid.UUID,
        file_bytes: bytes,
        *,
        doc_type: str,
        title: str,
        original_filename: str | None,
        mime_type: str,
    ) -> MandateDocument:
        if doc_type not in DOC_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid doc_type (allowed: {', '.join(sorted(DOC_TYPES))})",
            )
        if not file_bytes:
            raise HTTPException(status_code=422, detail="Empty file")
        if len(file_bytes) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="File too large (max 25 MB)")

        await self._require_mandate(mandate_id)

        sha = compute_sha256(file_bytes)
        key = make_storage_key(str(self._org_id), sha)
        ref = get_store().put(key, file_bytes, content_type=mime_type)

        doc = MandateDocument(
            organization_id=self._org_id,
            management_mandate_id=mandate_id,
            doc_type=doc_type,
            title=title or "",
            original_filename=original_filename,
            mime_type=mime_type,
            content_byte_size=ref.byte_size,
            content_sha256=ref.sha256,
            storage_uri=ref.uri,
            storage_backend=ref.backend,
            uploaded_by=self._user_id,
        )
        doc = await self._repo.create(doc)
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="mandate_document",
                entity_id=doc.id,
                action="mandate_document.uploaded",
                payload_diff={
                    "management_mandate_id": str(mandate_id),
                    "doc_type": doc_type,
                    "original_filename": original_filename,
                    "content_byte_size": ref.byte_size,
                },
            )
        )
        return doc

    async def list_for_mandate(self, mandate_id: uuid.UUID):
        await self._require_mandate(mandate_id)
        return await self._repo.list_by_mandate(mandate_id)

    async def get(self, doc_id: uuid.UUID) -> MandateDocument | None:
        return await self._repo.get_by_id(doc_id)

    async def delete(self, doc_id: uuid.UUID) -> None:
        doc = await self._repo.get_by_id(doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")
        await self._repo.delete(doc)
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="mandate_document",
                entity_id=doc_id,
                action="mandate_document.deleted",
                payload_diff={"doc_type": doc.doc_type},
            )
        )

    @staticmethod
    def read_bytes(storage_uri: str) -> bytes:
        return get_store().get(storage_uri)
