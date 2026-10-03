"""Advisory exception work does not resolve domain blockers."""
import uuid
from typing import Literal
from fastapi import APIRouter,Depends,Header,Query
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.tenant import AuthContext,get_current_user
from app.core.live_principal import get_live_member_db,get_live_writer_db
from app.api.preconditions import expected_revision
from app.services.exception_service import ExceptionService

router=APIRouter(prefix='/api/exceptions',tags=['exceptions'])


class ExceptionEventCreate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:Literal['claim','release','snooze','reopen']
    reason:str=Field(min_length=1,max_length=1000)
    snoozed_until:AwareDatetime|None=None


@router.get('')
async def listing(workflow_state:Literal['open','snoozed','completed']|None=None,limit:int=Query(default=50,ge=1,le=200),cursor:str|None=Query(default=None,max_length=4096),
                  auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await ExceptionService(db,auth).listing(workflow_state,limit,cursor)


@router.get('/{case_id}')
async def detail(case_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await ExceptionService(db,auth).detail(case_id)


@router.get('/{case_id}/events')
async def events(case_id:uuid.UUID,before_revision:int|None=Query(default=None,ge=1),limit:int=Query(default=50,ge=1,le=200),
                 auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await ExceptionService(db,auth).history(case_id,before_revision,limit)


@router.get('/{case_id}/dependencies')
async def dependencies(case_id:uuid.UUID,before_revision:int|None=Query(default=None,ge=1),limit:int=Query(default=50,ge=1,le=200),
                       auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await ExceptionService(db,auth).history(case_id,before_revision,limit,dependencies=True)


@router.get('/{case_id}/duplicate-dependencies')
async def duplicate_dependencies(case_id:uuid.UUID,before_revision:int|None=Query(default=None,ge=1),limit:int=Query(default=20,ge=1,le=50),
                                 auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await ExceptionService(db,auth).duplicate_dependencies(case_id,before_revision,limit)


@router.post('/{case_id}/events',status_code=201)
async def append(case_id:uuid.UUID,body:ExceptionEventCreate,idempotency_key:uuid.UUID=Header(),expected:uuid.UUID|None=Depends(expected_revision),
                 auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_writer_db)):
    return await ExceptionService(db,auth).append(case_id,body,idempotency_key,expected)


class ExceptionCompletionCreate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    preview_digest:str=Field(pattern=r'^[0-9a-f]{64}$')
    reason:str=Field(min_length=1,max_length=1000)
    completion_reviewed:bool=Field(strict=True)

    @field_validator('completion_reviewed')
    @classmethod
    def acknowledged(cls,value):
        if value is not True:raise ValueError('Explicit completion review required')
        return value


@router.get('/{case_id}/completion-preview')
async def completion_preview(case_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await ExceptionService(db,auth).completion_preview(case_id)


@router.get('/{case_id}/completions/{completion_id}')
async def completion(case_id:uuid.UUID,completion_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await ExceptionService(db,auth).completion(case_id,completion_id)


@router.post('/{case_id}/completions',status_code=201)
async def complete(case_id:uuid.UUID,body:ExceptionCompletionCreate,idempotency_key:uuid.UUID=Header(),expected:uuid.UUID|None=Depends(expected_revision),
                   auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_writer_db)):
    return await ExceptionService(db,auth).complete(case_id,body,idempotency_key,expected)
