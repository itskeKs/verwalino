"""Bounded, review-first API for declaration-import drafts."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Header, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.tenant import AuthContext, require_write_dep
from app.core.live_principal import get_live_writer_db, get_live_publication_db, get_live_review_db
from app.core.uploads import read_upload_limited
from app.declarations.review_schemas import (
    ApprovalPreviewOut,
    ApprovalRequest,
    BulkCandidateReviewRequest,
    CandidateOut,
    CandidateReviewRequest,
    ReviewImportOut,
)
from app.models.declaration_import import DeclarationImport
from app.models.property_document import PropertyDocument
from app.services.declaration_import_service import (
    DeclarationImportService,
    require_declaration_import_enabled,
)
from app.services.declaration_approval_service import DeclarationApprovalService
from app.services.declaration_review_service import DeclarationReviewService
from app.services.property_document_service import (
    PropertyDocumentService,
    VerifiedPropertyDocumentContent,
    safe_inline_filename,
)

router = APIRouter(prefix="/api/declaration-imports", tags=["declaration-imports"])


class DeclarationImportCreate(BaseModel):
    mode: Literal["NEW_PROPERTY", "EXISTING_PROPERTY"]
    target_property_id: uuid.UUID | None = None


class DeclarationImportOut(BaseModel):
    id: str
    mode: str
    target_property_id: str | None
    applied_property_id: str | None
    status: str
    draft_revision: int
    progress_percent: int
    current_stage: str | None
    error_code: str | None
    error_message: str | None
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: Decimal
    attempt_count: int
    max_cost_usd: Decimal
    queue_request_id: str | None = None
    command_receipt_id: str | None = None


class PropertyDocumentOut(BaseModel):
    id: str
    property_id: str | None
    doc_type: str
    title: str
    original_filename: str
    mime_type: str
    content_byte_size: int
    content_sha256: str
    sequence_no: int | None = None
    document_role: str | None = None


class DocumentMetadataUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    document_role: Literal["DECLARATION", "AMENDMENT", "ANNEX", "OTHER"] | None = None
    sequence_no: int | None = Field(default=None, ge=1)


@router.post("", response_model=DeclarationImportOut, status_code=201)
async def create_import(
    body: DeclarationImportCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    return _import_out(await DeclarationImportService(db, auth.organization_id, auth.user_id).create(
        body.mode, body.target_property_id
    ))


@router.post("/{import_id}/documents", response_model=PropertyDocumentOut, status_code=201)
async def upload_document(
    import_id: uuid.UUID,
    file: UploadFile = File(...),
    document_role: str = Form(...),
    sequence_no: int = Form(...),
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    # Fail before consuming the multipart stream when imports are disabled,
    # then enforce the deploy-time byte limit during the bounded read.
    require_declaration_import_enabled()
    file_bytes = await read_upload_limited(file, settings.declaration_max_pdf_bytes)
    service = DeclarationImportService(db, auth.organization_id, auth.user_id)
    document = await service.upload_document(
        import_id,
        file_bytes,
        original_filename=file.filename or "document.pdf",
        mime_type=file.content_type or "application/octet-stream",
        document_role=document_role,
        sequence_no=sequence_no,
    )
    return _document_out(document, sequence_no=sequence_no, document_role=document_role)


@router.patch("/{import_id}/documents/{document_id}", response_model=PropertyDocumentOut)
async def update_document_metadata(
    import_id: uuid.UUID,
    document_id: uuid.UUID,
    body: DocumentMetadataUpdate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    document, link = await DeclarationImportService(
        db, auth.organization_id, auth.user_id
    ).update_document_metadata(
        import_id,
        document_id,
        title=body.title,
        document_role=body.document_role,
        sequence_no=body.sequence_no,
    )
    return _document_out(
        document,
        sequence_no=link.sequence_no,
        document_role=link.document_role,
    )


@router.get("/{import_id}/documents", response_model=list[PropertyDocumentOut])
async def list_import_documents(
    import_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    rows = await DeclarationImportService(
        db, auth.organization_id, auth.user_id
    ).list_documents(import_id)
    return [
        _document_out(
            document,
            sequence_no=link.sequence_no,
            document_role=link.document_role,
        )
        for link, document in rows
    ]


@router.post("/{import_id}/start", response_model=DeclarationImportOut)
async def start_import(
    import_id: uuid.UUID,
    idempotency_key: uuid.UUID = Header(alias="Idempotency-Key"),
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    return _import_out(await DeclarationImportService(db, auth.organization_id, auth.user_id).start(import_id, idempotency_key))


@router.get("/{import_id}", response_model=DeclarationImportOut)
async def get_import(
    import_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    return _import_out(await DeclarationImportService(db, auth.organization_id, auth.user_id).get(import_id))


@router.get("/{import_id}/documents/{document_id}/content")
async def get_import_document_content(
    import_id: uuid.UUID,
    document_id: uuid.UUID,
    page: int | None = Query(default=None, gt=0),
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    # Accepted for PDF-viewer compatibility only; storage always uses the
    # persisted content-addressed URI from the authorized document row.
    del page
    verified = await PropertyDocumentService(
        db, auth.organization_id, auth.user_id
    ).get_review_draft_content(import_id, document_id)
    return _pdf_response(verified)


@router.get("/{import_id}/candidates", response_model=list[CandidateOut])
async def list_candidates(
    import_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    return await DeclarationReviewService(db, auth.organization_id, auth.user_id).list_candidates(import_id)


@router.patch("/{import_id}/candidates/{candidate_id}", response_model=ReviewImportOut)
async def review_candidate(
    import_id: uuid.UUID,
    candidate_id: uuid.UUID,
    body: CandidateReviewRequest,
    idempotency_key: uuid.UUID | None = Header(default=None, alias="Idempotency-Key"),
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_review_db),
):
    return await DeclarationReviewService(db, auth.organization_id, auth.user_id).review_one(import_id, candidate_id, body, idempotency_key)


@router.post("/{import_id}/candidates/bulk-review", response_model=ReviewImportOut)
async def bulk_review_candidates(
    import_id: uuid.UUID,
    body: BulkCandidateReviewRequest,
    idempotency_key: uuid.UUID | None = Header(default=None, alias="Idempotency-Key"),
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_review_db),
):
    return await DeclarationReviewService(db, auth.organization_id, auth.user_id).review_bulk(import_id, body, idempotency_key)


@router.get("/{import_id}/approval-preview", response_model=ApprovalPreviewOut)
async def approval_preview(
    import_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    return await DeclarationReviewService(db, auth.organization_id, auth.user_id).preview(import_id)


@router.post("/{import_id}/retry", response_model=DeclarationImportOut)
async def retry_import(
    import_id: uuid.UUID,
    idempotency_key: uuid.UUID = Header(alias="Idempotency-Key"),
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    return _import_out(await DeclarationImportService(db, auth.organization_id, auth.user_id).retry(import_id, idempotency_key))


@router.post("/{import_id}/approve")
async def approve_import_boundary(
    import_id: uuid.UUID,
    body: ApprovalRequest,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_publication_db),
):
    return await DeclarationApprovalService(
        db, auth.organization_id, auth.user_id
    ).approve(import_id, body)

@router.post("/{import_id}/cancel", response_model=DeclarationImportOut)
async def cancel_import(
    import_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    return _import_out(await DeclarationImportService(db, auth.organization_id, auth.user_id).cancel(import_id))


def _import_out(item: DeclarationImport) -> DeclarationImportOut:
    return DeclarationImportOut(
        id=str(item.id), mode=item.mode,
        target_property_id=str(item.target_property_id) if item.target_property_id else None,
        applied_property_id=str(item.applied_property_id) if item.applied_property_id else None,
        status=item.status, draft_revision=item.draft_revision,
        progress_percent=item.progress_percent, current_stage=item.current_stage,
        error_code=item.error_code, error_message=item.error_message,
        input_tokens=item.input_tokens, output_tokens=item.output_tokens,
        estimated_cost_usd=item.estimated_cost_usd, attempt_count=item.attempt_count,
        max_cost_usd=item.max_cost_usd,
        queue_request_id=str(item.queue_request_id) if item.queue_request_id else None,
        command_receipt_id=str(item.command_receipt_id) if getattr(item, "command_receipt_id", None) else None,
    )


def _document_out(
    item: PropertyDocument, *, sequence_no: int | None, document_role: str | None
) -> PropertyDocumentOut:
    return PropertyDocumentOut(
        id=str(item.id), property_id=str(item.property_id) if item.property_id else None,
        doc_type=item.doc_type, title=item.title, original_filename=item.original_filename,
        mime_type=item.mime_type, content_byte_size=item.content_byte_size,
        content_sha256=item.content_sha256, sequence_no=sequence_no,
        document_role=document_role,
    )


def _pdf_response(verified: VerifiedPropertyDocumentContent) -> Response:
    filename = safe_inline_filename(verified.document.original_filename)
    return Response(
        content=verified.data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Length": str(len(verified.data)),
            "ETag": f'"{verified.document.content_sha256}"',
        },
    )
