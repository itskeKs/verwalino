"""Safe upload and immutable persistence for declaration source PDFs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
import re

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.storage import DocumentStore, compute_sha256, get_store, put_document
from app.models.audit_event import AuditEvent
from app.models.declaration_import import DeclarationImportDocument
from app.models.property_document import PropertyDocument
from app.repositories.audit_repo import AuditRepository
from app.repositories.declaration_import_repo import DeclarationImportRepository
from app.repositories.property_document_repo import PropertyDocumentRepository

PDF_MIME_TYPES = {"application/pdf", "application/x-pdf"}
DOCUMENT_ROLES = {"DECLARATION", "AMENDMENT", "ANNEX", "OTHER"}
MAX_DOCUMENT_TEXT_LENGTH = 255


@dataclass(frozen=True, slots=True)
class VerifiedPropertyDocumentContent:
    """A single verified object read, safe to return without a second lookup."""

    document: PropertyDocument
    data: bytes


def safe_inline_filename(original_filename: str) -> str:
    """Produce an ASCII PDF filename without paths, controls, or delimiters."""
    basename = original_filename.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    filename = re.sub(r"[^A-Za-z0-9._ -]", "_", basename).strip(" .")
    return filename or "document.pdf"


class PropertyDocumentService:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID):
        self._session = session
        self._org_id = org_id
        self._user_id = user_id
        self._documents = PropertyDocumentRepository(session, org_id)
        self._imports = DeclarationImportRepository(session, org_id)
        self._audit = AuditRepository(session, org_id)

    async def list_approved_for_property(
        self, property_id: uuid.UUID
    ) -> list[PropertyDocument]:
        """List active property-bound source documents in stable order."""
        return list(await self._documents.list_active_for_property(property_id))

    async def get_approved_content(
        self,
        property_id: uuid.UUID,
        document_id: uuid.UUID,
        *,
        store: DocumentStore | None = None,
    ) -> VerifiedPropertyDocumentContent:
        await self._require_export(document_id)
        document = await self._documents.get_active_for_property(property_id, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return self._read_verified_content(document, store=store)

    async def get_review_draft_content(
        self,
        import_id: uuid.UUID,
        document_id: uuid.UUID,
        *,
        store: DocumentStore | None = None,
    ) -> VerifiedPropertyDocumentContent:
        """Return only an active, unbound document linked to this review draft."""
        await self._require_export(document_id)
        document = await self._imports.get_active_review_draft_document(import_id, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return self._read_verified_content(document, store=store)

    async def _require_export(self, document_id: uuid.UUID) -> None:
        # Holds the organization ACL through the single verified storage read.
        if not await self._session.scalar(text("SELECT property_source_export_access(:org,:document,'SOURCE')"),
            {'org': self._org_id, 'document': document_id}):
            raise HTTPException(status_code=404, detail="Document not found")

    @staticmethod
    def _read_verified_content(
        document: PropertyDocument,
        *,
        store: DocumentStore | None = None,
    ) -> VerifiedPropertyDocumentContent:
        """Read once and verify the exact bytes that will be returned.

        A second storage read after integrity verification would re-open a
        verify-then-use race. Declaration PDFs are upload-bounded by runtime
        configuration, so an in-memory response is deliberately safe here.
        """
        try:
            data = (store or get_store()).get(document.storage_uri)
        except FileNotFoundError:
            raise HTTPException(status_code=409, detail="DOCUMENT_INTEGRITY_FAILED") from None
        if (
            len(data) != document.content_byte_size
            or compute_sha256(data) != document.content_sha256
        ):
            raise HTTPException(status_code=409, detail="DOCUMENT_INTEGRITY_FAILED")
        return VerifiedPropertyDocumentContent(document=document, data=data)

    async def upload(
        self,
        import_id: uuid.UUID,
        file_bytes: bytes,
        *,
        original_filename: str,
        mime_type: str,
        document_role: str,
        sequence_no: int,
        store: DocumentStore | None = None,
    ) -> tuple[PropertyDocument, DeclarationImportDocument]:
        if len(file_bytes) > settings.declaration_max_pdf_bytes:
            from app.core.quotas import FREE_PLAN, check_quota
            raise HTTPException(
                status_code=413,
                detail=check_quota(0, FREE_PLAN, "upload_bytes", len(file_bytes)).message
                or f"Datei zu groß (maximal {settings.declaration_max_pdf_bytes} Bytes). Bitte wählen Sie eine kleinere Datei.",
            )
        if mime_type not in PDF_MIME_TYPES:
            raise HTTPException(status_code=422, detail="Only PDF documents are supported")
        if not file_bytes.startswith(b"%PDF-"):
            raise HTTPException(status_code=422, detail="File content is not a PDF")
        if document_role not in DOCUMENT_ROLES:
            raise HTTPException(status_code=422, detail="Invalid document_role")
        if sequence_no < 1:
            raise HTTPException(status_code=422, detail="sequence_no must be positive")
        if len(original_filename) > MAX_DOCUMENT_TEXT_LENGTH:
            raise HTTPException(
                status_code=422,
                detail="original_filename must be at most 255 characters",
            )

        await self._imports.lock_acl()
        if not await self._session.scalar(text("SELECT property_import_runtime_access(:org,:job,'prepare')"),
                {'org': self._org_id, 'job': import_id}):
            raise HTTPException(status_code=404, detail="Declaration import not found")
        active_store = store or get_store()
        stored = await put_document(self._session, self._org_id, file_bytes,
                                    content_type=mime_type, store=active_store)
        document = await self._documents.create(
            PropertyDocument(
                organization_id=self._org_id,
                property_id=None,
                doc_type=document_role,
                title=original_filename,
                original_filename=original_filename,
                mime_type=mime_type,
                content_byte_size=stored.byte_size,
                content_sha256=stored.sha256,
                storage_uri=stored.uri,
                storage_backend=stored.backend,
                page_count=1,
                uploaded_by=self._user_id,
            )
        )
        link = await self._imports.create_document_link(
            DeclarationImportDocument(
                organization_id=self._org_id,
                import_id=import_id,
                document_id=document.id,
                sequence_no=sequence_no,
                document_role=document_role,
            )
        )
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="property_document",
                entity_id=document.id,
                action="property_document.uploaded",
                payload_diff={
                    "import_id": str(import_id),
                    "filename": original_filename,
                    "mime_type": mime_type,
                    "sha256": stored.sha256,
                    "byte_size": stored.byte_size,
                },
            )
        )
        return document, link
