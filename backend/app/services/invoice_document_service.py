"""InvoiceDocument service -- upload, versioning, integrity verification.

m2-gobd-belegfluss.md section 4/5, section 8.4.
"""

from __future__ import annotations

import uuid
import json
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException
from app.repositories.cost_entry_repo import CostEntryRepository

from app.core.extraction import ExtractionResult
from app.core.storage import (
    DocumentStore,
    compute_sha256,
    get_store,
    make_storage_key,
    verify_document_integrity,
)
from app.models.audit_event import AuditEvent
from app.models.invoice_document import InvoiceDocument
from app.services.invoice_processing_service import InvoiceProcessingService
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

    async def require_source(self, document_id, capability):
        if not await self._session.scalar(text('SELECT invoice_source_access(:org,:document,:capability)'),
                {'org':self._org_id, 'document':document_id, 'capability':capability}):
            raise HTTPException(404, 'Invoice source not accessible')

    async def prepare_storage_write(self, cost_entry_id=None):
        from app.services.financial_scope_service import FinancialScopeService
        await FinancialScopeService(self._session, self._org_id, self._user_id).lock_inputs()
        if not await self._session.scalar(text("SELECT scope_actor_role(:org) IN ('OWNER_ADMIN','CLERK') AND property_source_security_mode(:org)<>'PAUSED'"), {'org':self._org_id}):
            raise HTTPException(404, 'Invoice preparation not accessible')
        if cost_entry_id is not None and not await self._session.scalar(text("SELECT financial_scope_access(:org,'cost_entry',:cost,'prepare')"), {'org':self._org_id,'cost':cost_entry_id}):
            raise HTTPException(404, 'Invoice destination not accessible')

    async def admit_uploaded_document(self, document):
        from app.core.db import get_review_tenant_session
        fields = ('id','organization_id','uploaded_by','cost_entry_id','storage_uri','storage_backend','storage_version',
                  'original_filename','mime_type','content_byte_size','content_sha256','version','supersedes_id','chain_root_id')
        document.uploaded_by = self._user_id
        payload = {field: str(value) if isinstance(value, uuid.UUID) else value
                   for field in fields for value in [getattr(document, field)]}
        async with get_review_tenant_session(self._org_id) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor':str(self._user_id)})
            await db.execute(text('SELECT admit_invoice_upload(:org,:document,CAST(:payload AS jsonb))'),
                             {'org':self._org_id,'document':document.id,'payload':json.dumps(payload)})

    async def _lock_cost(self, cost_entry_id):
        entry = await CostEntryRepository(self._session, self._org_id).get_by_id_for_update(cost_entry_id)
        if entry is None:
            raise HTTPException(404, "Cost entry not found")
        return entry

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
        await self.prepare_storage_write(cost_entry_id)
        if cost_entry_id:
            entry = await self._lock_cost(cost_entry_id)
            if entry.status == "confirmed":
                raise HTTPException(409, "Confirmed costs require a correction version")
            if await self._repo.get_active_for_cost_entry(cost_entry_id):
                raise HTTPException(409, "Cost entry already has an active source; create a document version")
        if store is None:
            store = get_store()

        sha256 = compute_sha256(file_bytes)
        key = make_storage_key(str(self._org_id), sha256)
        ref = store.put(key, file_bytes, content_type=mime_type)

        # Persist a durable queue entry; the worker extracts after this transaction.
        extraction = ExtractionResult()

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

        await self.admit_uploaded_document(doc)
        doc = await self._repo.create(doc)
        await InvoiceProcessingService(self._session, self._org_id).enqueue(doc, initiated_by=self._user_id)

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

        await self.prepare_storage_write(cost_entry_id)
        # Serialize the whole chain, including replacement of an unattached upload.
        root = await self._repo.get_by_id(chain_root_id)
        if root is None:
            raise HTTPException(404, "Document chain not found")
        # Lock cost before documents, consistently with confirmation/upload.
        active = await self._repo.get_active_by_chain(chain_root_id)
        if active is None:
            raise HTTPException(409, "Document chain has no active version")
        await self.require_source(active.id, 'prepare')
        if active.released_at is not None:
            await self.require_source(active.id, 'confirm')
        if active.cost_entry_id is None and active.uploaded_by != self._user_id:
            raise HTTPException(404, 'Known standalone invoice custody required')
        expected_revision = (active.id, active.cost_entry_id)
        original_cost = await self._lock_cost(active.cost_entry_id) if active.cost_entry_id else None
        destination_id = cost_entry_id or active.cost_entry_id
        destination = original_cost
        if destination_id and (original_cost is None or destination_id != original_cost.id):
            destination = await self._lock_cost(destination_id)
            if original_cost and original_cost.superseded_by_id != destination_id:
                raise HTTPException(409, "Document can only move to the linked cost correction")
        await self._repo.get_by_id_for_update(chain_root_id)
        old_active = await self._repo.get_active_by_chain(chain_root_id, lock=True)
        if old_active is None or (old_active.id, old_active.cost_entry_id) != expected_revision:
            raise HTTPException(409, "Document revision changed; reload before replacing")
        if destination and destination.status == "confirmed":
            raise HTTPException(409, "Create a cost correction before replacing its source")
        if destination_id:
            attached = await self._repo.get_active_for_cost_entry(destination_id)
            if attached and attached.id != old_active.id:
                raise HTTPException(409, "Destination cost already has an active source")

        sha256 = compute_sha256(file_bytes)
        key = make_storage_key(str(self._org_id), sha256)
        ref = store.put(key, file_bytes, content_type=mime_type)

        # Create new version
        new_doc = InvoiceDocument(
            id=uuid.uuid4(),
            organization_id=self._org_id,
            cost_entry_id=destination_id,
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
        await self.admit_uploaded_document(new_doc)
        # Mark old as superseded (trigger allows active->superseded on released rows)
        now = datetime.now(timezone.utc)
        old_active.status = "superseded"
        old_active.superseded_at = now
        await self._repo.update(old_active)
        new_doc = await self._repo.create(new_doc)
        await InvoiceProcessingService(self._session, self._org_id).enqueue(new_doc, initiated_by=self._user_id)

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
