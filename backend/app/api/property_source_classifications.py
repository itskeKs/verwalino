"""Explicit current review of already-bound originals, separate from old custody."""
import uuid
from fastapi import APIRouter,Depends,Header
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.live_principal import get_live_writer_db,get_live_member_db,get_live_publication_db
from app.core.tenant import AuthContext,require_admin_dep,get_current_user
from app.repositories.pagination import Page,PageQuery,page_query
from app.schemas.property_source_classification import SourceClassificationCreate,SourceClassificationOut,PendingSourceClassificationOut
from app.services.property_source_classification_service import PropertySourceClassificationService
from app.api.property_documents import _pdf_response

router=APIRouter(prefix='/api/property-source-classifications',tags=['property-source-classifications'])


@router.get('/pending/page',response_model=Page[PendingSourceClassificationOut])
async def pending_sources(query: PageQuery=Depends(page_query),auth: AuthContext=Depends(require_admin_dep),db: AsyncSession=Depends(get_live_writer_db)):
    items,cursor=await PropertySourceClassificationService(db,auth).pending(query)
    return Page(items=items,next_cursor=cursor)


@router.get('/originals/{document_id}/content')
async def original_content(document_id: uuid.UUID,auth: AuthContext=Depends(require_admin_dep),db: AsyncSession=Depends(get_live_writer_db)):
    return _pdf_response(await PropertySourceClassificationService(db,auth).content(document_id))


@router.post('',response_model=SourceClassificationOut,status_code=201)
async def classify_source(body: SourceClassificationCreate,idempotency_key: uuid.UUID=Header(),
    auth: AuthContext=Depends(require_admin_dep),db: AsyncSession=Depends(get_live_publication_db)):
    return await PropertySourceClassificationService(db,auth).classify(body,idempotency_key)


@router.get('/{classification_id}',response_model=SourceClassificationOut)
async def get_classification(classification_id: uuid.UUID,auth: AuthContext=Depends(get_current_user),db: AsyncSession=Depends(get_live_member_db)):
    return await PropertySourceClassificationService(db,auth).get(classification_id)
