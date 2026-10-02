"""Explicit contemporary source review without historical permission inference."""
import uuid
from fastapi import APIRouter,Depends,Header
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.live_principal import get_live_writer_db,get_live_member_db
from app.core.tenant import AuthContext,require_write_dep,get_current_user
from app.repositories.pagination import Page,PageQuery,page_query
from app.schemas.property_source_admission_review import SourceAdmissionReviewCreate,SourceAdmissionReviewOut,PendingSourceReviewOut
from app.services.property_source_admission_review_service import PropertySourceAdmissionReviewService
from app.api.property_documents import _pdf_response

router=APIRouter(prefix='/api/property-source-admission-reviews',tags=['property-source-admission-reviews'])


@router.get('/pending/page',response_model=Page[PendingSourceReviewOut])
async def pending_sources(query: PageQuery=Depends(page_query),auth: AuthContext=Depends(require_write_dep),db: AsyncSession=Depends(get_live_writer_db)):
    items,cursor=await PropertySourceAdmissionReviewService(db,auth).pending(query)
    return Page(items=items,next_cursor=cursor)


@router.get('/originals/{document_id}/content')
async def original_content(document_id: uuid.UUID,auth: AuthContext=Depends(require_write_dep),db: AsyncSession=Depends(get_live_writer_db)):
    return _pdf_response(await PropertySourceAdmissionReviewService(db,auth).content(document_id))


@router.post('',response_model=SourceAdmissionReviewOut,status_code=201)
async def review_source(body: SourceAdmissionReviewCreate,idempotency_key: uuid.UUID=Header(),
    auth: AuthContext=Depends(require_write_dep),db: AsyncSession=Depends(get_live_writer_db)):
    return await PropertySourceAdmissionReviewService(db,auth).review(body,idempotency_key)


@router.get('/{review_id}',response_model=SourceAdmissionReviewOut)
async def get_review(review_id: uuid.UUID,auth: AuthContext=Depends(get_current_user),db: AsyncSession=Depends(get_live_member_db)):
    return await PropertySourceAdmissionReviewService(db,auth).get(review_id)
