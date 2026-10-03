"""Review exact signed line amounts without creating financial effects."""
import uuid
from fastapi import APIRouter,Depends,Header,Query
from pydantic import BaseModel,ConfigDict,Field,field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.tenant import AuthContext,get_current_user
from app.core.live_principal import get_live_member_db,get_live_writer_db
from app.api.preconditions import expected_revision
from app.api.invoice_reviews import Money
from app.services.document_allocation_service import DocumentAllocationService

router=APIRouter(tags=['allocation_proposals'])


class AllocationLine(BaseModel):
    model_config=ConfigDict(extra='forbid')
    cost_category_id:uuid.UUID
    cost_category_plan_version:int=Field(strict=True,ge=1,le=32767)
    description:str=Field(min_length=1,max_length=1000)
    gross_amount:Money
    vat_amount:Money|None=None


class AllocationProposalCreate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    context_decision_id:uuid.UUID
    lines:list[AllocationLine]=Field(min_length=1,max_length=100)
    reason:str=Field(min_length=1,max_length=2000)
    allocation_reviewed:bool=Field(strict=True)

    @field_validator('allocation_reviewed')
    @classmethod
    def acknowledged(cls,value):
        if value is not True:raise ValueError('Explicit allocation review required')
        return value


@router.get('/api/context-decisions/{decision_id}/allocation-preview')
async def preview(decision_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await DocumentAllocationService(db,auth).preview(decision_id)


@router.get('/api/allocation-proposals/{proposal_id}')
async def detail(proposal_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await DocumentAllocationService(db,auth).detail(proposal_id)


@router.get('/api/logical-documents/{logical_id}/allocation-proposals')
async def history(logical_id:uuid.UUID,before_revision:int|None=Query(default=None,ge=1),limit:int=Query(default=20,ge=1,le=50),
                  auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await DocumentAllocationService(db,auth).history(logical_id,before_revision,limit)


@router.post('/api/logical-documents/{logical_id}/allocation-proposals',status_code=201)
async def create(logical_id:uuid.UUID,body:AllocationProposalCreate,idempotency_key:uuid.UUID=Header(),expected:uuid.UUID|None=Depends(expected_revision),
                 auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_writer_db)):
    return await DocumentAllocationService(db,auth).create(logical_id,body,idempotency_key,expected)
