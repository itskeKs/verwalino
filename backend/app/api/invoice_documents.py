"""Invoice document endpoints -- upload, list, verify, version, file stream.

RBAC: GET for all roles; POST/version require write role.
No DELETE endpoint (ADR-0003).
File stream is authenticated + tenant-checked (no public URL).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage import get_store
from app.core.tenant import AuthContext, get_current_user, get_db, require_write_dep
from app.models.invoice_document import InvoiceDocument
from app.repositories.invoice_document_repo import InvoiceDocumentRepository
from app.services.invoice_document_service import InvoiceDocumentService

router = APIRouter(prefix="/api/invoice-documents", tags=["invoice-documents"])


class InvoiceDocumentOut(BaseModel):
    id: str
    organization_id: str
    cost_entry_id: str | None
    storage_backend: str
    original_filename: str | None
    mime_type: str
    content_byte_size: int
    content_sha256: str
    version: int
    supersedes_id: str | None
    chain_root_id: str
    status: str
    released_at: str | None
    released_by: str | None
    retention_until: str | None
    superseded_at: str | None
    extraction_payload: dict | None = None

    model_config = {"from_attributes": True}


class VerifyResult(BaseModel):
    document_id: str
    integrity_ok: bool
    content_sha256: str


@router.get("", response_model=list[InvoiceDocumentOut])
async def list_documents(
    cost_entry_id: uuid.UUID | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = InvoiceDocumentRepository(db, auth.organization_id)
    if cost_entry_id:
        items = await repo.list_by_cost_entry(cost_entry_id)
    else:
        items = await repo.list_all()
    return [_to_out(d) for d in items]


@router.get("/{doc_id}", response_model=InvoiceDocumentOut)
async def get_document(
    doc_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = InvoiceDocumentRepository(db, auth.organization_id)
    doc = await repo.get_by_id(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return _to_out(doc)


@router.post("/upload", response_model=InvoiceDocumentOut, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    cost_entry_id: str | None = Form(None),
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    """Upload a document (PDF, XML, etc.). Creates invoice_document record."""
    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise HTTPException(status_code=422, detail="Empty file")

    svc = InvoiceDocumentService(db, auth.organization_id, auth.user_id)
    doc, _extraction = await svc.upload(
        file_bytes,
        original_filename=file.filename,
        mime_type=file.content_type or "application/octet-stream",
        cost_entry_id=uuid.UUID(cost_entry_id) if cost_entry_id else None,
    )
    return _to_out(doc)


@router.get("/{doc_id}/content")
async def stream_document_content(
    doc_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Stream the document file content (authenticated, tenant-checked)."""
    repo = InvoiceDocumentRepository(db, auth.organization_id)
    doc = await repo.get_by_id(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    store = get_store()
    try:
        data = store.get(doc.storage_uri)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Document file not found in storage")

    return StreamingResponse(
        iter([data]),
        media_type=doc.mime_type,
        headers={
            "Content-Disposition": f'inline; filename="{doc.original_filename or "document"}"',
            "Content-Length": str(doc.content_byte_size),
        },
    )


@router.get("/{doc_id}/verify", response_model=VerifyResult)
async def verify_document(
    doc_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Verify document integrity (hash check)."""
    repo = InvoiceDocumentRepository(db, auth.organization_id)
    doc = await repo.get_by_id(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    svc = InvoiceDocumentService(db, auth.organization_id, auth.user_id)
    ok = await svc.verify_integrity(doc)
    return VerifyResult(
        document_id=str(doc.id),
        integrity_ok=ok,
        content_sha256=doc.content_sha256,
    )


@router.post("/{doc_id}/version", response_model=InvoiceDocumentOut, status_code=201)
async def create_document_version(
    doc_id: uuid.UUID,
    file: UploadFile = File(...),
    cost_entry_id: str | None = Form(None),
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    """Create a new version of an existing document (correction)."""
    repo = InvoiceDocumentRepository(db, auth.organization_id)
    doc = await repo.get_by_id(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise HTTPException(status_code=422, detail="Empty file")

    svc = InvoiceDocumentService(db, auth.organization_id, auth.user_id)
    new_doc = await svc.create_new_version(
        doc.chain_root_id,
        file_bytes,
        original_filename=file.filename,
        mime_type=file.content_type or "application/octet-stream",
        cost_entry_id=uuid.UUID(cost_entry_id) if cost_entry_id else None,
    )
    return _to_out(new_doc)


def _to_out(d: InvoiceDocument) -> InvoiceDocumentOut:
    return InvoiceDocumentOut(
        id=str(d.id),
        organization_id=str(d.organization_id),
        cost_entry_id=str(d.cost_entry_id) if d.cost_entry_id else None,
        storage_backend=d.storage_backend,
        original_filename=d.original_filename,
        mime_type=d.mime_type,
        content_byte_size=d.content_byte_size,
        content_sha256=d.content_sha256,
        version=d.version,
        supersedes_id=str(d.supersedes_id) if d.supersedes_id else None,
        chain_root_id=str(d.chain_root_id),
        status=d.status,
        released_at=str(d.released_at) if d.released_at else None,
        released_by=str(d.released_by) if d.released_by else None,
        retention_until=str(d.retention_until) if d.retention_until else None,
        superseded_at=str(d.superseded_at) if d.superseded_at else None,
        extraction_payload=d.extraction_payload,
    )
