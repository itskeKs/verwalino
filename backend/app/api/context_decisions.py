"""Explicit manual context comparison; no financial acceptance."""
import uuid
from fastapi import APIRouter,Depends,Header,Query
from pydantic import BaseModel,ConfigDict,Field,field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.tenant import AuthContext,get_current_user
from app.core.live_principal import get_live_member_db,get_live_writer_db
from app.api.preconditions import expected_revision
from app.services.context_decision_service import ContextDecisionService

router=APIRouter(tags=['context_decisions'])


class ContextDecisionCreate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    assignment_proposal_id:uuid.UUID
    invoice_review_id:uuid.UUID
    snapshot_digest:str=Field(pattern=r'^[0-9a-f]{64}$')
    expected_case_event_id:uuid.UUID|None=None
    quotation:str=Field(min_length=1,max_length=2000)
    page_number:int|None=Field(default=None,strict=True,ge=1,le=200)
    xml_path:str|None=Field(default=None,min_length=1,max_length=500)
    comparison_note:str=Field(min_length=1,max_length=2000)
    context_reviewed:bool=Field(strict=True)

    @field_validator('context_reviewed')
    @classmethod
    def acknowledged(cls,value):
        if value is not True:raise ValueError('Explicit context comparison required')
        return value


@router.get('/api/assignment-proposals/{proposal_id}/context-preview')
async def preview(proposal_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await ContextDecisionService(db,auth).preview(proposal_id)


@router.get('/api/context-decisions/{decision_id}')
async def detail(decision_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await ContextDecisionService(db,auth).detail(decision_id)


@router.get('/api/logical-documents/{logical_id}/context-decisions')
async def history(logical_id:uuid.UUID,before_revision:int|None=Query(default=None,ge=1),limit:int=Query(default=50,ge=1,le=200),
                  auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await ContextDecisionService(db,auth).history(logical_id,before_revision,limit)


@router.post('/api/logical-documents/{logical_id}/context-decisions',status_code=201)
async def create(logical_id:uuid.UUID,body:ContextDecisionCreate,idempotency_key:uuid.UUID=Header(),expected:uuid.UUID|None=Depends(expected_revision),
                 auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_writer_db)):
    return await ContextDecisionService(db,auth).create(logical_id,body,idempotency_key,expected)
