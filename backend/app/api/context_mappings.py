"""Explicit advisory mapping commands; all source checks execute in the database."""
import uuid
from datetime import date
from typing import Literal
from fastapi import APIRouter,Depends,Header
from pydantic import BaseModel,ConfigDict,Field,field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.tenant import AuthContext,get_current_user
from app.core.live_principal import get_live_member_db,get_live_writer_db
from app.api.preconditions import expected_revision
from app.services.intake_service import IntakeService

router=APIRouter(tags=['context_mappings'])


class MappingCreate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    allocation_set_id:uuid.UUID
    identifier_kind:Literal['property_id','meter_number']
    identifier_value:str=Field(min_length=1,max_length=100)
    page_number:int=Field(ge=1,strict=True)
    valid_from:date
    valid_to:date
    reason:str=Field(min_length=1,max_length=2000)
    reuse_consented:bool=Field(strict=True)

    @field_validator('reuse_consented')
    @classmethod
    def acknowledged(cls,value):
        if value is not True:raise ValueError('Explicit narrow context reuse consent required')
        return value


class MappingRevoke(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reason:str=Field(min_length=1,max_length=2000)


@router.get('/api/allocation-sets/{set_id}/mapping-options')
async def options(set_id:uuid.UUID,identifier_kind:Literal['property_id','meter_number']|None=None,identifier_value:str|None=None,
                  auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await IntakeService(db,auth).call('SELECT context_mapping_options(:org,:target,:kind,:value)',target=set_id,kind=identifier_kind,value=identifier_value)


@router.get('/api/context-mappings/{version_id}')
async def status(version_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await IntakeService(db,auth).call('SELECT context_mapping_status(:org,:target)',target=version_id)


@router.post('/api/context-mappings',status_code=201)
async def activate(body:MappingCreate,idempotency_key:uuid.UUID=Header(),expected:uuid.UUID|None=Depends(expected_revision),
                   auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_writer_db)):
    return await IntakeService(db,auth).call('SELECT activate_context_mapping(:org,:target,:expected,:request,:kind,:value,:page,:start,:end,:reason,:consent)',
      target=body.allocation_set_id,expected=expected,request=idempotency_key,kind=body.identifier_kind,value=body.identifier_value,
      page=body.page_number,start=body.valid_from,end=body.valid_to,reason=body.reason,consent=body.reuse_consented)


@router.post('/api/context-mappings/{version_id}/revocations',status_code=201)
async def revoke(version_id:uuid.UUID,body:MappingRevoke,idempotency_key:uuid.UUID=Header(),expected:uuid.UUID|None=Depends(expected_revision),
                 auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_writer_db)):
    return await IntakeService(db,auth).call('SELECT revoke_context_mapping(:org,:target,:expected,:request,:reason)',
      target=version_id,expected=expected,request=idempotency_key,reason=body.reason)
