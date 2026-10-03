"""Private exact visual artifacts for subsequent deliberate disclosure review."""
import uuid
from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.tenant import AuthContext, get_current_user
from app.core.live_principal import get_live_member_db, get_live_writer_db
from app.services.visual_extract_service import VisualExtractService

router = APIRouter(prefix='/api', tags=['visual_extracts'])


class VisualExtractCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    partition_revision_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=1000)


@router.post('/logical-documents/{logical_id}/visual-extracts', status_code=201)
async def create(logical_id: uuid.UUID, body: VisualExtractCreate, idempotency_key: uuid.UUID = Header(),
                 auth: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_live_writer_db)):
    return await VisualExtractService(db, auth).create(logical_id, body, idempotency_key)


@router.get('/logical-documents/{logical_id}/visual-extracts')
async def history(logical_id: uuid.UUID, limit: int = Query(default=20, ge=1, le=50), cursor: str | None = None,
                  auth: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_live_member_db)):
    return await VisualExtractService(db, auth).history(logical_id, limit, cursor)


@router.get('/visual-extracts/{extract_id}/content')
async def content(extract_id: uuid.UUID, auth: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_live_member_db)):
    data = await VisualExtractService(db, auth).content(extract_id)
    return Response(data, media_type='application/pdf', headers={
        'Content-Disposition': 'inline; filename="visual-extract.pdf"',
        'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "sandbox; default-src 'none'",
        'Cache-Control': 'private, no-store'})
