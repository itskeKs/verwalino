"""Visual page review never confirms extracted fields, attachments or money."""
import uuid
from typing import Literal
from fastapi import APIRouter,Depends,Header
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.tenant import AuthContext,get_current_user
from app.core.live_principal import get_live_member_db,get_live_writer_db
from app.services.intake_service import IntakeService
from app.api.preconditions import expected_revision

router=APIRouter(prefix='/api/document-refs',tags=['source_partitions'])


class PartitionReview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    logical_document_ids:list[uuid.UUID]=Field(min_length=1,max_length=200)
    reason:str=Field(min_length=1,max_length=1000)
    visual_pages_reviewed:Literal[True]


@router.get('/{document_ref_id}/partition')
async def status(document_ref_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    value=await IntakeService(db,auth).call('SELECT source_partition_status(:org,:ref,NULL)',ref=document_ref_id)
    value['can_export']=bool(await db.scalar(text('SELECT intake_original_export_access(:org,intake_original_id) FROM document_ref WHERE organization_id=:org AND id=:ref'),
        {'org':auth.organization_id,'ref':document_ref_id}))
    return value


@router.get('/{document_ref_id}/partition/{partition_id}')
async def history(document_ref_id:uuid.UUID,partition_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await IntakeService(db,auth).call('SELECT source_partition_status(:org,:ref,:partition)',ref=document_ref_id,partition=partition_id)


@router.post('/{document_ref_id}/partition',status_code=201)
async def review(document_ref_id:uuid.UUID,body:PartitionReview,idempotency_key:uuid.UUID=Header(),expected:uuid.UUID|None=Depends(expected_revision),
                 auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_writer_db)):
    return await IntakeService(db,auth).call('SELECT review_source_partition(:org,:ref,:request,:expected,CAST(:selected AS uuid[]),:reason,:acknowledged)',
        ref=document_ref_id,request=idempotency_key,expected=expected,selected=body.logical_document_ids,reason=body.reason,acknowledged=body.visual_pages_reviewed)
