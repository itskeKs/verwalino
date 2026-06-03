"""ManagementMandate endpoints -- CRUD within the current organization."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import (
    AuthContext,
    get_current_user,
    get_db,
    require_admin_dep,
    require_write_dep,
)
from app.repositories.mandate_repo import MandateRepository
from app.models.management_mandate import ManagementMandate
from app.models.mandate_document import MandateDocument
from app.services.mandate_document_service import MandateDocumentService

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

    model_config = {"from_attributes": True}


def _doc_to_out(d: MandateDocument) -> MandateDocumentOut:
    return MandateDocumentOut(
        id=str(d.id),
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
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    svc = MandateDocumentService(db, auth.organization_id, auth.user_id)
    docs = await svc.list_for_mandate(mandate_id)
    return [_doc_to_out(d) for d in docs]


@router.post(
    "/{mandate_id}/documents", response_model=MandateDocumentOut, status_code=201
)
async def upload_mandate_document(
    mandate_id: uuid.UUID,
    file: UploadFile = File(...),
    doc_type: str = Form(...),
    title: str = Form(""),
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    """Upload a contract/admin document to a mandate (write role)."""
    file_bytes = await file.read()
    svc = MandateDocumentService(db, auth.organization_id, auth.user_id)
    doc = await svc.upload(
        mandate_id,
        file_bytes,
        doc_type=doc_type,
        title=title,
        original_filename=file.filename,
        mime_type=file.content_type or "application/octet-stream",
    )
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
    try:
        data = MandateDocumentService.read_bytes(doc.storage_uri)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Document file not found in storage")
    return StreamingResponse(
        iter([data]),
        media_type=doc.mime_type,
        headers={
            "Content-Disposition": f'inline; filename="{doc.original_filename or "dokument"}"',
            "Content-Length": str(doc.content_byte_size),
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
