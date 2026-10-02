"""Read-only administrative inventory, independent from source-data disclosure."""
import uuid
from fastapi import APIRouter, Depends, Header
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.live_principal import get_live_admin_db
from app.core.tenant import AuthContext, require_admin_dep, get_db
from app.core.live_principal import require_live_principal
from app.repositories.pagination import Page, PageQuery, page_query
from app.schemas.access_coverage import CoverageCreate, CoverageSnapshot, CoverageItem, CoverageReviewCreate, CoverageReview, CoverageSummary
from app.services.access_coverage_service import AccessCoverageService
from app.schemas.access_preparation import AccessPreparationOut

router = APIRouter(prefix='/api/access-preparation', tags=['access_preparation'])


@router.get('', response_model=AccessPreparationOut)
async def preparation_inventory(auth: AuthContext = Depends(require_admin_dep),
                                db: AsyncSession = Depends(get_live_admin_db)):
    return await db.scalar(text('SELECT access_preparation_inventory(:org)'), {'org': auth.organization_id})


async def coverage_writer(auth: AuthContext = Depends(require_admin_dep), db: AsyncSession = Depends(get_db)):
    # Never upgrade a shared ACL lock after another capture has done the same.
    await require_live_principal(db, auth, writer=True, exclusive=True, administrator=True)
    return db


@router.post('/snapshots', response_model=CoverageSnapshot, status_code=201)
async def capture(body: CoverageCreate, idempotency_key: uuid.UUID = Header(),
                  auth: AuthContext = Depends(require_admin_dep), db: AsyncSession = Depends(coverage_writer)):
    return await AccessCoverageService(db, auth).capture(body,idempotency_key)


@router.get('/snapshots', response_model=Page[CoverageSummary])
async def snapshots(query: PageQuery = Depends(page_query), auth: AuthContext = Depends(require_admin_dep),
                    db: AsyncSession = Depends(get_live_admin_db)):
    rows,cursor = await AccessCoverageService(db,auth).headers(query)
    return Page(items=rows,next_cursor=cursor)


@router.get('/snapshots/{snapshot_id}/items', response_model=Page[CoverageItem])
async def items(snapshot_id: uuid.UUID, query: PageQuery = Depends(page_query),
                auth: AuthContext = Depends(require_admin_dep), db: AsyncSession = Depends(get_live_admin_db)):
    rows,cursor = await AccessCoverageService(db,auth).page(snapshot_id,query)
    return Page(items=rows,next_cursor=cursor)


@router.post('/snapshots/{snapshot_id}/review', response_model=CoverageReview, status_code=201)
async def review(snapshot_id: uuid.UUID, body: CoverageReviewCreate, idempotency_key: uuid.UUID = Header(),
                 auth: AuthContext = Depends(require_admin_dep), db: AsyncSession = Depends(coverage_writer)):
    return await AccessCoverageService(db,auth).review(snapshot_id,body,idempotency_key)
