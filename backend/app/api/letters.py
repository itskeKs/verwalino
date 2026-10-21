"""Saved plain-text templates and per-recipient retained PDF originals."""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.live_principal import get_live_member_db, get_live_writer_db
from app.core.tenant import AuthContext, get_current_user, require_write_dep
from app.services.letter_service import LetterService, validate_template

router = APIRouter(prefix="/api/letters", tags=["letters"])


class TemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=20000)

    @field_validator("subject", "body")
    @classmethod
    def safe_template(cls, value):
        return validate_template(value)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Name darf nicht leer sein")
        return value.strip()


class IssueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: uuid.UUID
    property_id: uuid.UUID
    request_id: uuid.UUID
    as_of: date


@router.get("/templates")
async def templates(auth: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_live_member_db)):
    return (await db.execute(text("""SELECT id,name,subject,body,created_at,updated_at FROM letter_template
        WHERE organization_id=:org ORDER BY name,id"""), {"org": auth.organization_id})).mappings().all()


@router.post("/templates", status_code=201)
async def create_template(body: TemplateRequest, auth: AuthContext = Depends(require_write_dep),
                          db: AsyncSession = Depends(get_live_writer_db)):
    return (await db.execute(text("""INSERT INTO letter_template(organization_id,name,subject,body)
        VALUES (:org,:name,:subject,:body) RETURNING id,name,subject,body,created_at,updated_at"""),
        {"org": auth.organization_id, **body.model_dump()})).mappings().one()


@router.put("/templates/{template_id}")
async def update_template(template_id: uuid.UUID, body: TemplateRequest, auth: AuthContext = Depends(require_write_dep),
                          db: AsyncSession = Depends(get_live_writer_db)):
    result = (await db.execute(text("""UPDATE letter_template SET name=:name,subject=:subject,body=:body,updated_at=clock_timestamp()
        WHERE organization_id=:org AND id=:id RETURNING id,name,subject,body,created_at,updated_at"""),
        {"org": auth.organization_id, "id": template_id, **body.model_dump()})).mappings().first()
    if result is None:
        raise HTTPException(404, "Vorlage nicht zugänglich")
    return result


@router.get("/documents")
async def documents(property_id: uuid.UUID, auth: AuthContext = Depends(get_current_user),
                    db: AsyncSession = Depends(get_live_member_db)):
    return await LetterService(db, auth).list_documents(property_id)


@router.post("/issue", status_code=201)
async def issue(body: IssueRequest, auth: AuthContext = Depends(require_write_dep),
                db: AsyncSession = Depends(get_live_writer_db, scope="function")):
    return await LetterService(db, auth).issue(body)


@router.get("/documents/{document_id}/pdf")
async def pdf(document_id: uuid.UUID, auth: AuthContext = Depends(get_current_user),
              db: AsyncSession = Depends(get_live_member_db)):
    content = await LetterService(db, auth).pdf(document_id)
    return Response(content, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="brief-{document_id}.pdf"', "X-Content-Type-Options": "nosniff"})
