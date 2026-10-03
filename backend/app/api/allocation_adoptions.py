"""Confirm one complete reviewed allocation and explicitly disclose its original."""
import uuid
from fastapi import APIRouter,Depends,Header
from pydantic import BaseModel,ConfigDict,Field,field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.tenant import AuthContext,get_current_user
from app.core.live_principal import get_live_member_db,get_live_writer_db
from app.api.preconditions import expected_revision
from app.services.allocation_adoption_service import AllocationAdoptionService

router=APIRouter(tags=['allocation_adoptions'])


class AdoptionCreate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    treatment_review_id:uuid.UUID
    preview_digest:str=Field(pattern=r'^[0-9a-f]{64}$')
    reason:str=Field(min_length=1,max_length=2000)
    costs_confirmed:bool=Field(strict=True)
    whole_original_disclosure_reviewed:bool=Field(strict=True)

    @field_validator('costs_confirmed','whole_original_disclosure_reviewed')
    @classmethod
    def acknowledged(cls,value):
        if value is not True:raise ValueError('Explicit financial and source disclosure review required')
        return value


@router.get('/api/allocation-treatments/{review_id}/adoption-preview')
async def preview(review_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await AllocationAdoptionService(db,auth).preview(review_id)


@router.get('/api/allocation-sets/{set_id}')
async def detail(set_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await AllocationAdoptionService(db,auth).detail(set_id)


@router.get('/api/logical-documents/{logical_id}/allocation-adoptions')
async def result(logical_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await AllocationAdoptionService(db,auth).result(logical_id)


@router.post('/api/logical-documents/{logical_id}/allocation-adoptions',status_code=201)
async def create(logical_id:uuid.UUID,body:AdoptionCreate,idempotency_key:uuid.UUID=Header(),expected:uuid.UUID|None=Depends(expected_revision),
                 auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_writer_db)):
    return await AllocationAdoptionService(db,auth).create(logical_id,body,idempotency_key,expected)


@router.get('/api/allocation-sets/{set_id}/correction-preview')
async def correction_preview(set_id:uuid.UUID,treatment_review_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await AllocationAdoptionService(db,auth).correction_preview(set_id,treatment_review_id)
