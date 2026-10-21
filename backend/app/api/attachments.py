"""General retained documents; originals are never replaced or deleted."""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.live_principal import get_live_member_db, require_live_principal
from app.core.storage import attachment_content_disposition
from app.core.tenant import AuthContext, get_current_user, get_db, require_write_dep
from app.core.uploads import MAX_UPLOAD_BYTES, read_upload_limited
from app.services.attachment_service import AttachmentService

router = APIRouter(prefix="/api/attachments", tags=["attachments"])


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    property_id: uuid.UUID
    unit_id: uuid.UUID | None
    lease_agreement_id: uuid.UUID | None
    person_id: uuid.UUID | None
    title: str
    original_filename: str
    mime_type: str
    content_byte_size: int
    content_sha256: str
    uploaded_by: uuid.UUID
    created_at: datetime
    archived_at: datetime | None
    archived_by: uuid.UUID | None
    archive_reason: str | None


class AttachmentListOut(BaseModel):
    items: list[AttachmentOut]
    next_offset: int | None


class ArchiveAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=2000, pattern=r"^[^\x00]*$")


async def attachment_writer_db(auth: AuthContext = Depends(require_write_dep),
                               db: AsyncSession = Depends(get_db, scope="function")):
    await require_live_principal(db, auth, writer=True)
    return db


@router.get("", response_model=AttachmentListOut)
async def list_attachments(property_id: uuid.UUID | None = None,
                           unit_id: uuid.UUID | None = None,
                           lease_agreement_id: uuid.UUID | None = None,
                           person_id: uuid.UUID | None = None,
                           include_archived: bool = False,
                           offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                           auth: AuthContext = Depends(get_current_user),
                           db: AsyncSession = Depends(get_live_member_db)):
    rows, next_offset = await AttachmentService(db, auth.organization_id, auth.user_id).list_by_parent(
        property_id, unit_id=unit_id, lease_agreement_id=lease_agreement_id,
        person_id=person_id, include_archived=include_archived, offset=offset, limit=limit)
    return AttachmentListOut(items=[AttachmentOut.model_validate(row) for row in rows], next_offset=next_offset)


@router.post("", response_model=AttachmentOut, status_code=201)
async def upload_attachment(property_id: uuid.UUID = Form(...),
                            file: UploadFile = File(...), title: str = Form("", max_length=255),
                            unit_id: uuid.UUID | None = Form(None),
                            lease_agreement_id: uuid.UUID | None = Form(None),
                            person_id: uuid.UUID | None = Form(None),
                            auth: AuthContext = Depends(require_write_dep),
                            db: AsyncSession = Depends(attachment_writer_db)):
    try:
        data = await read_upload_limited(file, max_bytes=MAX_UPLOAD_BYTES)
        item = await AttachmentService(db, auth.organization_id, auth.user_id).upload(
            property_id, data, title=title, original_filename=file.filename,
            unit_id=unit_id, lease_agreement_id=lease_agreement_id, person_id=person_id)
        return AttachmentOut.model_validate(item)
    finally:
        await file.close()


@router.get("/{attachment_id}", response_model=AttachmentOut)
async def get_attachment(attachment_id: uuid.UUID, auth: AuthContext = Depends(get_current_user),
                         db: AsyncSession = Depends(get_live_member_db)):
    item = await AttachmentService(db, auth.organization_id, auth.user_id).get(attachment_id)
    return AttachmentOut.model_validate(item)


@router.get("/{attachment_id}/download")
async def download_attachment(attachment_id: uuid.UUID, inline: bool = False,
                              auth: AuthContext = Depends(get_current_user),
                              db: AsyncSession = Depends(get_live_member_db)):
    item, data = await AttachmentService(db, auth.organization_id, auth.user_id).download(attachment_id)
    disposition = attachment_content_disposition(item.original_filename)
    if inline and item.mime_type in {"application/pdf", "image/png", "image/jpeg"}:
        disposition = disposition.replace("attachment;", "inline;", 1)
    return Response(data, media_type=item.mime_type, headers={
        "Content-Disposition": disposition, "Content-Length": str(len(data)),
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


@router.post("/{attachment_id}/archive", response_model=AttachmentOut)
async def archive_attachment(attachment_id: uuid.UUID, body: ArchiveAttachment,
                             auth: AuthContext = Depends(require_write_dep),
                             db: AsyncSession = Depends(attachment_writer_db)):
    item = await AttachmentService(db, auth.organization_id, auth.user_id).archive(attachment_id, body.reason)
    return AttachmentOut.model_validate(item)
