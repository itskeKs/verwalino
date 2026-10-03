"""Immutable human comparisons of two exact reviewed sources."""
import uuid
from typing import Literal
from fastapi import APIRouter,Depends,Header,Query
from pydantic import BaseModel,ConfigDict,Field,field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.tenant import AuthContext,get_current_user
from app.core.live_principal import get_live_member_db,get_live_writer_db
from app.api.preconditions import expected_revision
from app.services.duplicate_comparison_service import DuplicateComparisonService

router=APIRouter(tags=['duplicate_comparisons'])


class DuplicateComparisonCreate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reference_review_id:uuid.UUID
    verdict:Literal['same_document','distinct_documents','replacement_requested']
    reason:str=Field(min_length=1,max_length=2000)
    comparison_reviewed:bool=Field(strict=True)

    @field_validator('comparison_reviewed')
    @classmethod
    def acknowledged(cls,value):
        if value is not True:raise ValueError('Explicit comparison review required')
        return value


@router.get('/api/invoice-reviews/{review_id}/duplicate-candidates')
async def candidates(review_id:uuid.UUID,limit:int=Query(default=20,ge=1,le=50),cursor:str|None=Query(default=None,max_length=2000),
                     auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await DuplicateComparisonService(db,auth).candidates(review_id,limit,cursor)


@router.get('/api/duplicate-comparisons/{comparison_id}')
async def detail(comparison_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await DuplicateComparisonService(db,auth).detail(comparison_id)


@router.get('/api/invoice-reviews/{review_id}/duplicate-comparisons')
async def history(review_id:uuid.UUID,reference_review_id:uuid.UUID,before_revision:int|None=Query(default=None,ge=1),limit:int=Query(default=20,ge=1,le=50),
                  auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await DuplicateComparisonService(db,auth).history(review_id,reference_review_id,before_revision,limit)


@router.post('/api/invoice-reviews/{review_id}/duplicate-comparisons',status_code=201)
async def create(review_id:uuid.UUID,body:DuplicateComparisonCreate,idempotency_key:uuid.UUID=Header(),expected:uuid.UUID|None=Depends(expected_revision),
                 auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_writer_db)):
    return await DuplicateComparisonService(db,auth).create(review_id,body,idempotency_key,expected)
