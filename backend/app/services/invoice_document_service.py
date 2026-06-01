"""InvoiceDocument service -- upload, versioning, integrity verification.

m2-gobd-belegfluss.md section 4/5, section 8.4.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.extraction import ExtractionResult, get_extractor
from app.core.storage import (
    DocumentStore,
    compute_sha256,
    get_store,
    make_storage_key,
    verify_document_integrity,
)
from app.models.audit_event import AuditEvent
from app.models.invoice_document import InvoiceDocument
from app.repositories.audit_repo import AuditRepository
from app.repositories.invoice_document_repo import InvoiceDocumentRepository


class InvoiceDocumentService:
    def __init__(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        self._session = session
        self._org_id = org_id
        self._user_id = user_id
        self._repo = InvoiceDocumentRepository(session, org_id)
        self._audit = AuditRepository(session, org_id)

    async def upload(
        self,
        file_bytes: bytes,
        *,
        original_filename: str | None = None,
        mime_type: str = "application/pdf",
        cost_entry_id: uuid.UUID | None = None,
        store: DocumentStore | None = None,
    ) -> tuple[InvoiceDocument, ExtractionResult]:
        """Upload a document, store it, extract data, create DB record.

        Returns (InvoiceDocument, ExtractionResult).
        """
        if store is None:
            store = get_store()

        sha256 = compute_sha256(file_bytes)
        key = make_storage_key(str(self._org_id), sha256)
        ref = store.put(key, file_bytes, content_type=mime_type)

        # Run extraction (stub in this run; real OCR is the next step)
        extractor = get_extractor()
        extraction = extractor.extract(file_bytes, mime_type)

        doc_id = uuid.uuid4()
        doc = InvoiceDocument(
            id=doc_id,
            organization_id=self._org_id,
            cost_entry_id=cost_entry_id,
            storage_uri=ref.uri,
            storage_backend=ref.backend,
            storage_version=ref.version,
            original_filename=original_filename,
            mime_type=mime_type,
            content_byte_size=ref.byte_size,
            content_sha256=ref.sha256,
            version=1,
            supersedes_id=None,
            chain_root_id=doc_id,  # first version: chain root = self
            status="active",
            released_at=None,
            released_by=None,
            retention_until=None,
            extraction_payload=extraction.to_dict(),
        )

        doc = await self._repo.create(doc)

        # Audit event
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="invoice_document",
                entity_id=doc.id,
                action="document.uploaded",
                payload_diff={
                    "filename": original_filename,
                    "mime_type": mime_type,
                    "sha256": sha256,
                    "byte_size": ref.byte_size,
                },
            )
        )

        return doc, extraction

    async def create_new_version(
        self,
        chain_root_id: uuid.UUID,
        file_bytes: bytes,
        *,
        original_filename: str | None = None,
        mime_type: str = "application/pdf",
        cost_entry_id: uuid.UUID | None = None,
        store: DocumentStore | None = None,
    ) -> InvoiceDocument:
        """Create a new version of an existing document (correction).

        m2-gobd-belegfluss.md section 4.4:
          1. Store the new file.
          2. Create new invoice_document row (version+1, supersedes_id=old, chain_root_id unchanged).
          3. Mark old row as superseded.
        """
        if store is None:
            store = get_store()

        old_active = await self._repo.get_active_by_chain(chain_root_id)
        if old_active is None:
            raise ValueError(f"No active document found for chain {chain_root_id}")

        sha256 = compute_sha256(file_bytes)
        key = make_storage_key(str(self._org_id), sha256)
        ref = store.put(key, file_bytes, content_type=mime_type)

        # Create new version
        new_doc = InvoiceDocument(
            organization_id=self._org_id,
            cost_entry_id=cost_entry_id or old_active.cost_entry_id,
            storage_uri=ref.uri,
            storage_backend=ref.backend,
            storage_version=ref.version,
            original_filename=original_filename,
            mime_type=mime_type,
            content_byte_size=ref.byte_size,
            content_sha256=ref.sha256,
            version=old_active.version + 1,
            supersedes_id=old_active.id,
            chain_root_id=chain_root_id,
            status="active",
            released_at=None,
            released_by=None,
            retention_until=None,
        )
        new_doc = await self._repo.create(new_doc)

        # Mark old as superseded (trigger allows active->superseded on released rows)
        now = datetime.now(timezone.utc)
        old_active.status = "superseded"
        old_active.superseded_at = now
        await self._repo.update(old_active)

        # Audit events
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="invoice_document",
                entity_id=old_active.id,
                action="document.superseded",
                payload_diff={
                    "new_version_id": str(new_doc.id),
                    "version": old_active.version,
                },
            )
        )
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="invoice_document",
                entity_id=new_doc.id,
                action="document.uploaded",
                payload_diff={
                    "filename": original_filename,
                    "mime_type": mime_type,
                    "sha256": sha256,
                    "byte_size": ref.byte_size,
                    "version": new_doc.version,
                    "supersedes_id": str(old_active.id),
                },
            )
        )

        return new_doc

    async def verify_integrity(
        self,
        doc: InvoiceDocument,
        store: DocumentStore | None = None,
    ) -> bool:
        """Verify stored file matches the recorded hash."""
        return verify_document_integrity(
            doc.content_sha256, doc.storage_uri, store
        )
