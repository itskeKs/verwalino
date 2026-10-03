"""Scoped, reviewed derivative disclosure; never a financial adoption endpoint."""
import uuid
from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.preconditions import expected_revision
from app.core.tenant import AuthContext, get_current_user
from app.core.live_principal import get_live_member_db, get_live_writer_db
from app.services.visual_disclosure_service import VisualDisclosureService

router = APIRouter(prefix='/api', tags=['visual_disclosures'])


class PublishVisualDisclosure(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope_id: uuid.UUID
    partition_revision_id: uuid.UUID
    content_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    reason: str = Field(min_length=1, max_length=1000)
    visible_content_reviewed: bool = Field(strict=True)

    @field_validator('visible_content_reviewed')
    @classmethod
    def acknowledged(cls, value):
        if value is not True:
            raise ValueError('Explicit visual disclosure review required')
        return value


class WithdrawVisualDisclosure(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: str = Field(min_length=1, max_length=1000)
    withdrawal_confirmed: bool = Field(strict=True)

    @field_validator('withdrawal_confirmed')
    @classmethod
    def acknowledged(cls, value):
        if value is not True:
            raise ValueError('Explicit withdrawal required')
        return value


@router.post('/visual-extracts/{artifact_id}/disclosures', status_code=201)
async def publish(artifact_id: uuid.UUID, body: PublishVisualDisclosure, idempotency_key: uuid.UUID = Header(),
                  expected: uuid.UUID | None = Depends(expected_revision), auth: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_live_writer_db)):
    return await VisualDisclosureService(db, auth).publish(artifact_id, body, idempotency_key, expected)


@router.get('/visual-extracts/{artifact_id}/disclosure-target')
async def target(artifact_id: uuid.UUID, scope_id: uuid.UUID, auth: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_live_member_db)):
    return await VisualDisclosureService(db, auth).target(artifact_id, scope_id)


@router.get('/visual-extracts/{artifact_id}/disclosures')
async def source_list(artifact_id: uuid.UUID, limit: int = Query(default=20, ge=1, le=50), cursor: str | None = None,
                      auth: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_live_member_db)):
    return await VisualDisclosureService(db, auth).listing(None, artifact_id, limit, cursor)


@router.get('/accounting-scopes/{scope_id}/visual-disclosures')
async def destination_list(scope_id: uuid.UUID, limit: int = Query(default=20, ge=1, le=50), cursor: str | None = None,
                           auth: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_live_member_db)):
    return await VisualDisclosureService(db, auth).listing(scope_id, None, limit, cursor)


@router.get('/visual-disclosures/outgoing')
async def outgoing(limit: int = Query(default=20, ge=1, le=50), cursor: str | None = None,
                   auth: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_live_member_db)):
    return await VisualDisclosureService(db, auth).listing(None, None, limit, cursor)


@router.get('/visual-disclosures/{disclosure_id}')
async def detail(disclosure_id: uuid.UUID, auth: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_live_member_db)):
    return await VisualDisclosureService(db, auth).detail(disclosure_id)


@router.post('/visual-disclosures/{disclosure_id}/withdrawals', status_code=201)
async def withdraw(disclosure_id: uuid.UUID, body: WithdrawVisualDisclosure, idempotency_key: uuid.UUID = Header(),
                   expected: uuid.UUID | None = Depends(expected_revision), auth: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_live_writer_db)):
    return await VisualDisclosureService(db, auth).withdraw(disclosure_id, body, idempotency_key, expected)


@router.get('/visual-disclosures/{disclosure_id}/content')
async def content(disclosure_id: uuid.UUID, auth: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_live_member_db)):
    data = await VisualDisclosureService(db, auth).content(disclosure_id)
    return Response(data, media_type='application/pdf', headers={
        'Content-Disposition': 'inline; filename="reviewed-visual-extract.pdf"', 'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': "sandbox; default-src 'none'", 'Cache-Control': 'private, no-store'})
