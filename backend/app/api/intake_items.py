"""Logical intake context proposals precede any domain adoption or resolution."""
import uuid
from fastapi import APIRouter,Depends,Header,Query
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.tenant import AuthContext,get_current_user
from app.core.live_principal import get_live_member_db,get_live_writer_db
from app.api.preconditions import expected_revision
from app.services.assignment_service import AssignmentService

router=APIRouter(prefix='/api/logical-documents',tags=['assignment_proposals'])


class AssignmentCreate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    partition_revision_id:uuid.UUID
    extraction_revision_id:uuid.UUID
    scope_id:uuid.UUID
    reason:str=Field(min_length=1,max_length=1000)


@router.get('/{logical_id}/assignment-proposals')
async def history(logical_id:uuid.UUID,before_revision:int|None=Query(default=None,ge=1),limit:int=Query(default=50,ge=1,le=200),
                  auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await AssignmentService(db,auth).history(logical_id,before_revision,limit)


@router.post('/{logical_id}/assignment-proposals',status_code=201)
async def create(logical_id:uuid.UUID,body:AssignmentCreate,idempotency_key:uuid.UUID=Header(),expected:uuid.UUID|None=Depends(expected_revision),
                 auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_writer_db)):
    return await AssignmentService(db,auth).create(logical_id,body,idempotency_key,expected)
