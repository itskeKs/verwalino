"""Mandate source access and retained originals.

Unclassified legacy metadata is retained in quarantine. New originals require
an explicit scope; scoped originals record one-way archive transitions. Neither
quarantine nor a new upload authenticates historical custody or retention duration.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from sqlalchemy import text
from app.core.rbac import ScopeCapability
from app.core.tenant import AuthContext
from app.services.authorization_service import AuthorizationService

from fastapi import HTTPException

from app.core.storage import get_store, lock_document_storage, make_storage_key, put_document, remove_document_after_commit
from app.core.uploads import MAX_UPLOAD_BYTES, require_upload_size
from app.models.audit_event import AuditEvent
from app.models.mandate_document import MandateDocument
from app.repositories.audit_repo import AuditRepository
from app.repositories.mandate_document_repo import MandateDocumentRepository
from app.repositories.mandate_repo import MandateRepository
from app.services.quota_service import lock_quota

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
        accounting_scope_id: uuid.UUID | None = None,
        scope_reason: str | None = None,
    ) -> MandateDocument:
        if doc_type not in DOC_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid doc_type (allowed: {', '.join(sorted(DOC_TYPES))})",
            )
        if not file_bytes:
            raise HTTPException(status_code=422, detail="Empty file")
        require_upload_size(len(file_bytes), max_bytes=MAX_UPLOAD_BYTES)

        await self._require_mandate(mandate_id)
        if accounting_scope_id is None and scope_reason is not None:
            raise HTTPException(422, "Scope reason requires an explicit scope")
        if accounting_scope_id is not None:
            if scope_reason is None or not 1 <= len(scope_reason.strip()) <= 2000:
                raise HTTPException(422, "Scoped sources require a review reason")
            scope_reason = scope_reason.strip()
            scope = await AuthorizationService(self._session, AuthContext(self._user_id, self._org_id, "live database role required")).require(accounting_scope_id, ScopeCapability.PREPARE)
            if scope.management_mandate_id != mandate_id:
                raise HTTPException(422, "Document and scope must reference the same mandate")

        # The same helper guards RLS. Check before writing bytes, including the
        # formerly unscoped compatibility path and organization source pause.
        if not await self._session.scalar(text("SELECT mandate_source_access(:org,:scope,'prepare')"),
                {"org": self._org_id, "scope": accounting_scope_id}):
            raise HTTPException(404, "Document scope not accessible")

        ref = await put_document(self._session, self._org_id, file_bytes, content_type=mime_type)

        doc = MandateDocument(
            organization_id=self._org_id,
            accounting_scope_id=accounting_scope_id,
            scope_reason=scope_reason,
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
                    "scope_id": str(accounting_scope_id) if accounting_scope_id else None,
                    "scope_reason": scope_reason,
                    "doc_type": doc_type,
                    "original_filename": original_filename,
                    "content_byte_size": ref.byte_size,
                },
            )
        )
        return doc

    async def list_for_mandate(self, mandate_id: uuid.UUID, *, include_archived=False, scope_id=None):
        await self._require_mandate(mandate_id)
        if scope_id is not None:
            scope = await AuthorizationService(self._session, AuthContext(self._user_id, self._org_id, "live database role required")).require(scope_id, ScopeCapability.READ)
            if scope.management_mandate_id != mandate_id:
                raise HTTPException(404, "Document scope not accessible")
        return await self._repo.list_by_mandate(mandate_id, include_archived=include_archived, scope_id=scope_id)

    async def get(self, doc_id: uuid.UUID) -> MandateDocument | None:
        return await self._repo.get_by_id(doc_id)

    async def delete(self, doc_id: uuid.UUID) -> None:
        quota_locked = await lock_quota(self._session, self._org_id)
        doc = await self._repo.get_by_id(doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")
        if doc.accounting_scope_id is not None:
            raise HTTPException(409, "Classified originals must be archived with a reason")
        store = get_store()
        directory_locked = await lock_document_storage(self._session, self._org_id, store) if quota_locked else False
        await self._repo.delete(doc)
        # Future accepted deletion classes may unlink only under both hosted
        # locks. Reference counts include unknown sizes and source-hidden rows.
        if (quota_locked and directory_locked
                and doc.storage_backend == "LOCAL_FS"
                and doc.storage_uri == make_storage_key(str(self._org_id), doc.content_sha256)
                and await self._repo.count_storage_references(doc.storage_uri) == 0):
            remove_document_after_commit(self._session, store, doc.storage_uri)
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="mandate_document",
                entity_id=doc_id,
                action="mandate_document.deleted",
                payload_diff={"doc_type": doc.doc_type},
            )
        )

    async def require_document_capability(self, doc, capability):
        allowed = await self._session.scalar(text("SELECT mandate_source_access(:org,:scope,:capability)"),
            {"org": self._org_id, "scope": doc.accounting_scope_id, "capability": capability})
        if not allowed:
            raise HTTPException(404, "Document not accessible")

    async def archive(self, doc_id, mandate_id, reason):
        # ACL before row lock, matching permission changes and ordinary effects.
        await AuthorizationService(self._session, AuthContext(self._user_id, self._org_id, "live database role required"))._lock()
        from sqlalchemy import select
        doc = await self._session.scalar(select(MandateDocument).where(MandateDocument.organization_id == self._org_id,
            MandateDocument.id == doc_id, MandateDocument.management_mandate_id == mandate_id).with_for_update())
        if doc is None:
            raise HTTPException(404, "Document not accessible")
        await self.require_document_capability(doc, "prepare")
        if doc.accounting_scope_id is None:
            raise HTTPException(409, "Legacy document archive requires reviewed classification")
        if doc.archived_at is not None:
            if doc.archive_reason != reason:
                raise HTTPException(409, "Archive already records a different reason")
            return doc
        doc.archived_at = datetime.now(timezone.utc)
        doc.archived_by = self._user_id
        doc.archive_reason = reason
        await self._session.flush()
        await self._session.refresh(doc)
        return doc

    @staticmethod
    def read_bytes(storage_uri: str) -> bytes:
        from app.core.storage import get_store
        return get_store().get(storage_uri)
