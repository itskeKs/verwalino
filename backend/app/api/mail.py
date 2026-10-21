"""Explicit document SMTP submission and source-bound attempt history."""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import AuthContext, get_current_user, require_write_dep
from app.core.live_principal import get_live_member_db, get_live_writer_db
from app.services.document_mail_service import DocumentMailService

router = APIRouter(prefix="/api/mail", tags=["mail"])
MailKind = Literal["dunning", "settlement", "invitation"]


class MailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person_id: uuid.UUID
    request_id: uuid.UUID


@router.get("/{kind}/{document_id}")
async def document_mail_info(kind: MailKind, document_id: uuid.UUID,
                             auth: AuthContext = Depends(get_current_user),
                             db: AsyncSession = Depends(get_live_member_db)):
    return await DocumentMailService(db, auth).info(kind, document_id)


@router.get("/{kind}/{document_id}/{attempt_id}/pdf")
async def retained_mail_pdf(kind: MailKind, document_id: uuid.UUID, attempt_id: uuid.UUID,
                            auth: AuthContext = Depends(get_current_user),
                            db: AsyncSession = Depends(get_live_member_db)):
    pdf = await DocumentMailService(db, auth).retained_pdf(kind, document_id, attempt_id)
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="versand-{attempt_id}.pdf"'})


@router.post("/{kind}/{document_id}")
async def send_document_mail(kind: MailKind, document_id: uuid.UUID, body: MailRequest,
                             auth: AuthContext = Depends(require_write_dep),
                             db: AsyncSession = Depends(get_live_writer_db)):
    return await DocumentMailService(db, auth).send(kind, document_id, body.person_id, body.request_id)
