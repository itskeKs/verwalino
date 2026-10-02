"""Typed original metadata; no new source or export authority."""
import uuid
from fastapi import APIRouter,Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.tenant import AuthContext,get_current_user
from app.core.live_principal import get_live_member_db
from app.repositories.document_ref_repo import DocumentRefRepository
from app.repositories.pagination import Page,PageQuery,page_query
from app.schemas.document_ref import DocumentRefOut,SourceKind

router=APIRouter(prefix='/api/document-refs',tags=['document_refs'])


@router.get('/page',response_model=Page[DocumentRefOut])
async def page(source_kind: SourceKind | None=None,source_id: uuid.UUID | None=None,
               query: PageQuery=Depends(page_query),auth: AuthContext=Depends(get_current_user),db: AsyncSession=Depends(get_live_member_db)):
    rows,cursor=await DocumentRefRepository(db,auth).page(query,source_kind,source_id)
    return Page(items=[DocumentRefOut.model_validate(row) for row in rows],next_cursor=cursor)


@router.get('/{ref_id}',response_model=DocumentRefOut)
async def detail(ref_id: uuid.UUID,auth: AuthContext=Depends(get_current_user),db: AsyncSession=Depends(get_live_member_db)):
    return await DocumentRefRepository(db,auth).get(ref_id)
