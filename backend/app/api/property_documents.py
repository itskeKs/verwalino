"""Authorized property-document metadata and verified PDF content routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import AuthContext, get_current_user, get_db
from app.models.property_document import PropertyDocument
from app.repositories.property_repo import PropertyRepository
from app.services.property_document_service import (
    PropertyDocumentService,
    VerifiedPropertyDocumentContent,
    safe_inline_filename,
)

router = APIRouter(prefix="/api/properties", tags=["property-documents"])


class PropertyDocumentOut(BaseModel):
    id: str
    property_id: str
    doc_type: str
    title: str
    original_filename: str
    mime_type: str
    content_byte_size: int
    content_sha256: str
    page_count: int


@router.get("/{property_id}/documents", response_model=list[PropertyDocumentOut])
async def list_property_documents(
    property_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_property(db, auth, property_id)
    items = await PropertyDocumentService(db, auth.organization_id, auth.user_id).list_approved_for_property(
        property_id
    )
    return [_document_out(item) for item in items]


@router.get("/{property_id}/documents/{document_id}/content")
async def get_property_document_content(
    property_id: uuid.UUID,
    document_id: uuid.UUID,
    page: int | None = Query(default=None, gt=0),
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # The PDF viewer may preserve a page in links.  It intentionally never
    # affects storage key resolution or response selection.
    del page
    await _require_property(db, auth, property_id)
    verified = await PropertyDocumentService(
        db, auth.organization_id, auth.user_id
    ).get_approved_content(property_id, document_id)
    return _pdf_response(verified)


async def _require_property(
    db: AsyncSession, auth: AuthContext, property_id: uuid.UUID
) -> None:
    if await PropertyRepository(db, auth.organization_id).get_by_id(property_id) is None:
        raise HTTPException(status_code=404, detail="Property not found")


def _document_out(document: PropertyDocument) -> PropertyDocumentOut:
    assert document.property_id is not None
    return PropertyDocumentOut(
        id=str(document.id),
        property_id=str(document.property_id),
        doc_type=document.doc_type,
        title=document.title,
        original_filename=document.original_filename,
        mime_type=document.mime_type,
        content_byte_size=document.content_byte_size,
        content_sha256=document.content_sha256,
        page_count=document.page_count,
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
