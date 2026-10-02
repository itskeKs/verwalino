"""Administrative retention decisions; no original content endpoint."""
import uuid
from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.live_principal import get_live_admin_db
from app.core.tenant import AuthContext, require_admin_dep
from app.repositories.pagination import Page, PageQuery, page_query
from app.schemas.mandate_quarantine import QuarantineObservation, QuarantineReviewCreate, QuarantineDisposition
from app.services.mandate_quarantine_service import MandateQuarantineService

router = APIRouter(prefix='/api/mandate-quarantine', tags=['mandate-quarantine'])


@router.get('/page', response_model=Page[QuarantineObservation])
async def observations(query: PageQuery = Depends(page_query), auth: AuthContext = Depends(require_admin_dep),
                       db: AsyncSession = Depends(get_live_admin_db)):
    rows, cursor = await MandateQuarantineService(db, auth).page(query)
    return Page(items=rows, next_cursor=cursor)


@router.post('', response_model=QuarantineDisposition, status_code=201)
async def review(body: QuarantineReviewCreate, idempotency_key: uuid.UUID = Header(),
                 auth: AuthContext = Depends(require_admin_dep), db: AsyncSession = Depends(get_live_admin_db)):
    return await MandateQuarantineService(db, auth).review(body, idempotency_key)
