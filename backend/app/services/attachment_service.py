"""Source-authorized metadata and retained, content-addressed originals."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from app.core.locking import StorageLockTimeout
from app.core.storage import compute_sha256, get_store, make_storage_key, put_document
from app.core.uploads import MAX_UPLOAD_BYTES, require_upload_size
from app.models.attachment import Attachment
from app.models.audit_event import AuditEvent
from app.repositories.attachment_repo import AttachmentRepository
from app.repositories.audit_repo import AuditRepository
from app.repositories.property_repo import PropertyRepository
from app.repositories.unit_repo import UnitRepository
from app.repositories.lease_agreement_repo import LeaseAgreementRepository
from app.repositories.person_repo import PersonRepository
from app.services.property_master_service import require_property_master


class AttachmentService:
    def __init__(self, db, org_id, user_id):
        self.db, self.org_id, self.user_id = db, org_id, user_id
        self.repo = AttachmentRepository(db, org_id)
        self.audit = AuditRepository(db, org_id)

    async def _parent(self, repository, entity_id):
        entity = await repository(self.db, self.org_id).get_by_id(entity_id)
        if entity is None:
            raise HTTPException(404, "Document parent not accessible")
        return entity

    async def require_parents(self, property_id=None, *, unit_id=None,
                              lease_agreement_id=None, person_id=None, capability="read"):
        checked_property = property_id
        if property_id:
            await self._parent(PropertyRepository, property_id)
            await require_property_master(self.db, self.org_id, property_id, capability)
        unit = await self._parent(UnitRepository, unit_id) if unit_id else None
        if lease_agreement_id:
            lease = await self._parent(LeaseAgreementRepository, lease_agreement_id)
            lease_unit = await self._parent(UnitRepository, lease.unit_id)
            if unit and unit.id != lease_unit.id:
                raise HTTPException(422, "Lease and unit do not match")
            unit = lease_unit
            allowed = await self.db.scalar(text(
                "SELECT property_history_row_access(l.organization_id,'lease_agreement',to_jsonb(l),:capability) "
                "FROM lease_agreement l WHERE l.organization_id=:org AND l.id=:lease"),
                {"org": self.org_id, "lease": lease.id, "capability": capability})
            if not allowed:
                raise HTTPException(404, "Document relationship not accessible")
        if unit:
            if property_id and unit.property_id != property_id:
                raise HTTPException(422, "Document unit and property do not match")
            property_id = unit.property_id
        if property_id and checked_property is None:
            await self._parent(PropertyRepository, property_id)
            await require_property_master(self.db, self.org_id, property_id, capability)
        if person_id:
            await self._parent(PersonRepository, person_id)
            allowed = await self.db.scalar(text(
                "SELECT person_source_access(:org,:person,:capability)"),
                {"org": self.org_id, "person": person_id, "capability": capability})
            if not allowed:
                raise HTTPException(404, "Document contact not accessible")

    async def get(self, attachment_id, *, capability="read", lock=False):
        item = await (self.repo.get_by_id_for_update(attachment_id) if lock
                      else self.repo.get_by_id(attachment_id))
        if item is None:
            raise HTTPException(404, "Attachment not found")
        await self.require_parents(item.property_id, unit_id=item.unit_id,
                                  lease_agreement_id=item.lease_agreement_id,
                                  person_id=item.person_id, capability=capability)
        return item

    async def list_by_parent(self, property_id=None, *, unit_id=None,
                             lease_agreement_id=None, person_id=None,
                             include_archived=False, offset=0, limit=50):
        await self.require_parents(property_id, unit_id=unit_id,
                                  lease_agreement_id=lease_agreement_id, person_id=person_id)
        return await self.repo.list_by_parent(property_id, unit_id=unit_id,
            lease_agreement_id=lease_agreement_id, person_id=person_id,
            include_archived=include_archived, offset=offset, limit=limit)

    @staticmethod
    def detected_mime(data):
        if data.startswith(b"%PDF-"):
            return "application/pdf"
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        return "application/octet-stream"

    async def upload(self, property_id, file_bytes, *, title="", original_filename=None,
                     unit_id=None, lease_agreement_id=None, person_id=None):
        if not file_bytes:
            raise HTTPException(422, "Empty file")
        require_upload_size(len(file_bytes), max_bytes=MAX_UPLOAD_BYTES)
        filename = original_filename or ""
        if (not filename.strip() or len(filename) > 255 or filename in {".", ".."}
                or any(c in filename for c in "/\\")
                or any(ord(c) < 32 or ord(c) == 127 for c in filename)):
            raise HTTPException(422, "Invalid original filename")
        if len(title) > 255 or "\x00" in title:
            raise HTTPException(422, "Invalid document title")
        await self.require_parents(property_id, unit_id=unit_id,
            lease_agreement_id=lease_agreement_id, person_id=person_id, capability="prepare")
        digest = compute_sha256(file_bytes)
        mime = self.detected_mime(file_bytes)
        key = make_storage_key(str(self.org_id), digest)
        try:
            ref = await put_document(self.db, self.org_id, file_bytes, content_type=mime, store=get_store())
        except StorageLockTimeout:
            raise
        except (OSError, ValueError) as exc:
            raise HTTPException(503, "Document storage unavailable") from exc
        if ref.uri != key or ref.sha256 != digest or ref.byte_size != len(file_bytes):
            raise HTTPException(503, "Document storage verification failed")
        item = await self.repo.create(Attachment(
            organization_id=self.org_id, property_id=property_id, unit_id=unit_id,
            lease_agreement_id=lease_agreement_id, person_id=person_id,
            title=title.strip(), original_filename=filename, mime_type=mime,
            content_byte_size=ref.byte_size, content_sha256=digest,
            storage_backend=ref.backend, storage_uri=ref.uri, uploaded_by=self.user_id))
        await self.audit.create(AuditEvent(actor_user_id=self.user_id, entity_type="attachment",
            entity_id=item.id, action="attachment.uploaded",
            payload_diff={"property_id": str(property_id), "original_filename": filename,
                          "content_byte_size": len(file_bytes)}))
        return item

    async def archive(self, attachment_id, reason):
        item = await self.get(attachment_id, capability="prepare", lock=True)
        if item.archived_at is not None:
            if item.archive_reason != reason:
                raise HTTPException(409, "Archive already records a different reason")
            return item
        item.archived_at = datetime.now(timezone.utc)
        item.archived_by, item.archive_reason = self.user_id, reason
        await self.db.flush()
        await self.audit.create(AuditEvent(actor_user_id=self.user_id, entity_type="attachment",
            entity_id=item.id, action="attachment.archived", payload_diff={"reason": reason}))
        return item

    @staticmethod
    def _read_verified(item):
        key = make_storage_key(str(item.organization_id), item.content_sha256)
        if item.storage_uri != key:
            raise HTTPException(409, "Document storage reference invalid")
        digest, chunks, size = hashlib.sha256(), [], 0
        try:
            for chunk in get_store().iter_bytes(key):
                size += len(chunk)
                if size > min(item.content_byte_size, MAX_UPLOAD_BYTES):
                    raise HTTPException(409, "Document integrity check failed")
                digest.update(chunk)
                chunks.append(chunk)
        except (FileNotFoundError, PermissionError, ValueError) as exc:
            raise HTTPException(404, "Document original unavailable") from exc
        if size != item.content_byte_size or digest.hexdigest() != item.content_sha256:
            raise HTTPException(409, "Document integrity check failed")
        return b"".join(chunks)

    async def download(self, attachment_id):
        item = await self.get(attachment_id, capability="export")
        data = await run_in_threadpool(self._read_verified, item)
        return item, data
