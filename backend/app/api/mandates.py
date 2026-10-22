"""ManagementMandate endpoints -- CRUD within the current organization."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import Response, APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Literal
from app.core.live_principal import get_live_admin_db
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import DBAPIError

from app.core.storage import attachment_content_disposition, get_store
from app.core.tenant import (
    AuthContext,
    get_current_user,
    get_db,
    require_admin_dep,
    require_write_dep,
)
from app.core.uploads import MAX_UPLOAD_BYTES, read_upload_limited
from app.repositories.mandate_repo import MandateRepository
from app.models.management_mandate import ManagementMandate
from app.models.mandate_document import MandateDocument
from app.services.mandate_document_service import MandateDocumentService

from app.repositories.pagination import Page, PageQuery, page_query, page_rows

router = APIRouter(prefix="/api/mandates", tags=["mandates"])


class MandateOut(BaseModel):
    id: str
    mandate_type: str
    label: str
    start_date: date
    end_date: date | None

    model_config = {"from_attributes": True}


class MandateCreate(BaseModel):
    mandate_type: str
    label: str = ""
    start_date: date
    end_date: date | None = None


class MandateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str | None = Field(default=None, max_length=255)
    mandate_type: Literal["WEG", "MIET", "SEV"] | None = None
    start_date: date | None = None
    end_date: date | None = None

    @field_validator("label", "mandate_type", "start_date")
    @classmethod
    def reject_null(cls, value):
        if value is None:
            raise ValueError("Field cannot be null")
        return value


@router.get("", response_model=list[MandateOut])
async def list_mandates(
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = MandateRepository(db, auth.organization_id)
    mandates = await repo.list_all()
    return [
        MandateOut(
            id=str(m.id),
            mandate_type=m.mandate_type,
            label=m.label,
            start_date=m.start_date,
            end_date=m.end_date,
        )
        for m in mandates
    ]


@router.get('/page', response_model=Page[MandateOut])
async def page_mandates(query: PageQuery = Depends(page_query),
                       auth: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    items, cursor = await page_rows(db, auth, ManagementMandate, ManagementMandate.label,
                                   [ManagementMandate.label], query)
    return Page(items=[MandateOut(id=str(m.id), mandate_type=m.mandate_type, label=m.label,
                                 start_date=m.start_date, end_date=m.end_date) for m in items], next_cursor=cursor)


@router.get("/{mandate_id}", response_model=MandateOut)
async def get_mandate(
    mandate_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = MandateRepository(db, auth.organization_id)
    m = await repo.get_by_id(mandate_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Mandate not found")
    return MandateOut(
        id=str(m.id),
        mandate_type=m.mandate_type,
        label=m.label,
        start_date=m.start_date,
        end_date=m.end_date,
    )


@router.post("", response_model=MandateOut, status_code=201)
async def create_mandate(
    body: MandateCreate,
    auth: AuthContext = Depends(require_admin_dep),
    db: AsyncSession = Depends(get_db),
):
    """Create a mandate -- OWNER_ADMIN only."""
    if body.mandate_type not in ("WEG", "MIET", "SEV"):
        raise HTTPException(status_code=422, detail="Invalid mandate_type")

    repo = MandateRepository(db, auth.organization_id)
    mandate = ManagementMandate(
        organization_id=auth.organization_id,
        mandate_type=body.mandate_type,
        label=body.label,
        start_date=body.start_date,
        end_date=body.end_date,
    )
    mandate = await repo.create(mandate)
    return MandateOut(
        id=str(mandate.id),
        mandate_type=mandate.mandate_type,
        label=mandate.label,
        start_date=mandate.start_date,
        end_date=mandate.end_date,
    )


@router.patch("/{mandate_id}", response_model=MandateOut)
async def update_mandate(
    mandate_id: uuid.UUID,
    body: MandateUpdate,
    auth: AuthContext = Depends(require_admin_dep),
    db: AsyncSession = Depends(get_live_admin_db),
):
    from app.services.financial_scope_service import FinancialScopeService
    await FinancialScopeService(db, auth.organization_id, auth.user_id).lock_inputs()
    mandate = await MandateRepository(db, auth.organization_id).get_by_id_for_update(mandate_id)
    if mandate is None:
        raise HTTPException(404, "Mandate not found")
    updates = body.model_dump(exclude_unset=True)
    start = updates.get("start_date", mandate.start_date)
    end = updates.get("end_date", mandate.end_date)
    if end is not None and end < start:
        raise HTTPException(422, "end_date must not precede start_date")
    for key, value in updates.items():
        setattr(mandate, key, value)
    try:
        await db.flush()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "23514":
            raise HTTPException(409, "A scoped mandate definition cannot be changed; create a reviewed successor mandate") from exc
        raise
    return MandateOut(id=str(mandate.id), mandate_type=mandate.mandate_type,
        label=mandate.label, start_date=mandate.start_date, end_date=mandate.end_date)


# ===========================================================================
# Mandate documents (Verträge & Unterlagen am Mandat)
# ===========================================================================
class MandateDocumentOut(BaseModel):
    id: str
    management_mandate_id: str
    doc_type: str
    title: str
    original_filename: str | None
    mime_type: str
    content_byte_size: int
    uploaded_at: str | None
    uploaded_by: str | None
    accounting_scope_id: uuid.UUID | None
    scope_reason: str | None
    archived_at: str | None
    archive_reason: str | None

    model_config = {"from_attributes": True}


def _doc_to_out(d: MandateDocument) -> MandateDocumentOut:
    return MandateDocumentOut(
        id=str(d.id),
        accounting_scope_id=d.accounting_scope_id,
        scope_reason=d.scope_reason,
        archived_at=d.archived_at.isoformat() if d.archived_at else None,
        archive_reason=d.archive_reason,
        management_mandate_id=str(d.management_mandate_id),
        doc_type=d.doc_type,
        title=d.title,
        original_filename=d.original_filename,
        mime_type=d.mime_type,
        content_byte_size=d.content_byte_size,
        uploaded_at=str(d.created_at) if d.created_at else None,
        uploaded_by=str(d.uploaded_by) if d.uploaded_by else None,
    )


@router.get("/{mandate_id}/documents", response_model=list[MandateDocumentOut])
async def list_mandate_documents(
    mandate_id: uuid.UUID,
    response: Response,
    include_archived: bool = False,
    accounting_scope_id: uuid.UUID | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = MandateDocumentService(db, auth.organization_id, auth.user_id)
    response.headers["X-Document-Visibility"] = "accessible-only"
    docs = await svc.list_for_mandate(mandate_id, include_archived=include_archived, scope_id=accounting_scope_id)
    return [_doc_to_out(d) for d in docs]


@router.post(
    "/{mandate_id}/documents", response_model=MandateDocumentOut, status_code=201
)
async def upload_mandate_document(
    mandate_id: uuid.UUID,
    file: UploadFile = File(...),
    doc_type: str = Form(...),
    title: str = Form(""),
    accounting_scope_id: uuid.UUID | None = Form(None),
    scope_reason: str | None = Form(None),
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    """Upload a contract/admin document to a mandate (write role)."""
    file_bytes = await read_upload_limited(file, max_bytes=MAX_UPLOAD_BYTES)
    svc = MandateDocumentService(db, auth.organization_id, auth.user_id)
    try:
        doc = await svc.upload(
            mandate_id,
            file_bytes,
            doc_type=doc_type,
            title=title,
            original_filename=file.filename,
            mime_type=file.content_type or "application/octet-stream",
            accounting_scope_id=accounting_scope_id,
            scope_reason=scope_reason,
        )
    except DBAPIError as error:
        if getattr(error.orig, 'sqlstate', None) == '42501':
            raise HTTPException(404, "Document scope no longer accessible") from None
        raise
    return _doc_to_out(doc)


@router.get("/{mandate_id}/documents/{doc_id}/download")
async def download_mandate_document(
    mandate_id: uuid.UUID,
    doc_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Stream a mandate document's file (authenticated, tenant-checked)."""
    svc = MandateDocumentService(db, auth.organization_id, auth.user_id)
    doc = await svc.get(doc_id)
    if doc is None or doc.management_mandate_id != mandate_id:
        raise HTTPException(status_code=404, detail="Document not found")
    await svc.require_document_capability(doc, "export")
    try:
        store = get_store()
        chunks = store.iter_chunks(doc.storage_uri)
    except (FileNotFoundError, PermissionError, ValueError):
        raise HTTPException(status_code=404, detail="Document file not found in storage")
    return StreamingResponse(
        chunks,
        media_type=doc.mime_type,
        headers={
            "Content-Disposition": attachment_content_disposition(
                doc.original_filename or "dokument"
            ),
            "Content-Length": str(doc.content_byte_size),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/{mandate_id}/documents/{doc_id}", status_code=204)
async def delete_mandate_document(
    mandate_id: uuid.UUID,
    doc_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    """Delete a mandate document record (write role)."""
    svc = MandateDocumentService(db, auth.organization_id, auth.user_id)
    doc = await svc.get(doc_id)
    if doc is None or doc.management_mandate_id != mandate_id:
        raise HTTPException(status_code=404, detail="Document not found")
    await svc.delete(doc_id)


class ArchiveMandateDocument(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=2000)


@router.post("/{mandate_id}/documents/{doc_id}/archive", response_model=MandateDocumentOut)
async def archive_mandate_document(mandate_id: uuid.UUID, doc_id: uuid.UUID, body: ArchiveMandateDocument,
                                  auth: AuthContext = Depends(require_write_dep), db: AsyncSession = Depends(get_db)):
    try:
        return _doc_to_out(await MandateDocumentService(db, auth.organization_id, auth.user_id).archive(doc_id, mandate_id, body.reason))
    except DBAPIError as error:
        if getattr(error.orig, 'sqlstate', None) == '42501':
            raise HTTPException(404, "Document scope no longer accessible") from None
        raise
