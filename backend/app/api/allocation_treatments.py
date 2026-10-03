"""Human treatment assertions; no financial adoption."""
import uuid
from typing import Literal
from fastapi import APIRouter,Depends,Header,Query
from pydantic import BaseModel,ConfigDict,Field,field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.tenant import AuthContext,get_current_user
from app.core.live_principal import get_live_member_db,get_live_writer_db
from app.api.preconditions import expected_revision
from app.api.invoice_reviews import Money
from app.services.allocation_treatment_service import AllocationTreatmentService

router=APIRouter(tags=['allocation_treatments'])
DistributionKey=Literal['MEA','WOHNFLAECHE','BEHEIZTE_FLAECHE','EINHEIT','PERSONENZAHL','VERBRAUCH','EXTERNE_ABRECHNUNG','MANUELLER_BETRAG']


class TreatmentLine(BaseModel):
    model_config=ConfigDict(extra='forbid')
    allocation_line_id:uuid.UUID
    expense_intent:Literal['operating_expense','reserve_related','capitalization_related','unresolved']
    tax_intent:Literal['gross_without_input_vat','input_vat_requested','unresolved']
    tenant_treatment:Literal['full','partial','none','unresolved']
    tenant_amount:Money|None
    tenant_distribution_key:DistributionKey|None
    owner_distribution_key:DistributionKey|Literal['unresolved','not_applicable']
    documentary_basis:str=Field(min_length=1,max_length=2000)


class TreatmentCreate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    allocation_proposal_id:uuid.UUID
    preview_digest:str=Field(pattern=r'^[0-9a-f]{64}$')
    reason:str=Field(min_length=1,max_length=2000)
    treatment_reviewed:bool=Field(strict=True)
    lines:list[TreatmentLine]=Field(min_length=1,max_length=100)

    @field_validator('treatment_reviewed')
    @classmethod
    def acknowledged(cls,value):
        if value is not True:raise ValueError('Explicit treatment review required')
        return value


@router.get('/api/allocation-proposals/{proposal_id}/treatment-preview')
async def preview(proposal_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await AllocationTreatmentService(db,auth).preview(proposal_id)


@router.get('/api/allocation-treatments/{review_id}')
async def detail(review_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await AllocationTreatmentService(db,auth).detail(review_id)


@router.get('/api/logical-documents/{logical_id}/allocation-treatments')
async def history(logical_id:uuid.UUID,before_revision:int|None=Query(default=None,ge=1),limit:int=Query(default=20,ge=1,le=50),
                  auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await AllocationTreatmentService(db,auth).history(logical_id,before_revision,limit)


@router.post('/api/logical-documents/{logical_id}/allocation-treatments',status_code=201)
async def create(logical_id:uuid.UUID,body:TreatmentCreate,idempotency_key:uuid.UUID=Header(),expected:uuid.UUID|None=Depends(expected_revision),
                 auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_writer_db)):
    return await AllocationTreatmentService(db,auth).create(logical_id,body,idempotency_key,expected)
