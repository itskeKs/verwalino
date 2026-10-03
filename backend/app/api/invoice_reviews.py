"""Versioned human invoice fields are separate from allocation and acceptance."""
from datetime import date
import re
from typing import Annotated,Literal
import uuid
from fastapi import APIRouter,Depends,Header,Query
from pydantic import BaseModel,ConfigDict,Field,field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.tenant import AuthContext,get_current_user
from app.core.live_principal import get_live_member_db,get_live_writer_db
from app.api.preconditions import expected_revision
from app.services.invoice_review_service import InvoiceReviewService

router=APIRouter(tags=['invoice_reviews'])
Money=Annotated[str,Field(strict=True,pattern=r'^-?(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?$')]
Rate=Annotated[str,Field(strict=True,pattern=r'^(?:(?:0|[1-9][0-9]?)(?:\.[0-9]{1,2})?|100(?:\.0{1,2})?)$')]


class InvoiceReviewCreate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    assignment_proposal_id:uuid.UUID
    seller_name:str=Field(min_length=1,max_length=255)
    document_number:str=Field(min_length=1,max_length=100)
    document_kind:Literal['invoice','credit_note']
    currency:Literal['EUR']
    invoice_date:date
    service_period_from:date
    service_period_to:date
    gross_amount:Money
    vat_amount:Money|None=None
    vat_rate:Rate|None=None
    fields_reviewed:bool=Field(strict=True)
    reason:str=Field(min_length=1,max_length=1000)

    @field_validator('fields_reviewed')
    @classmethod
    def acknowledged(cls,value):
        if value is not True:raise ValueError('Explicit field review acknowledgement required')
        return value

    @field_validator('invoice_date','service_period_from','service_period_to',mode='before')
    @classmethod
    def iso_date(cls,value):
        if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise ValueError('An explicit ISO date is required')
        return value


@router.get('/api/invoice-reviews/{review_id}')
async def detail(review_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await InvoiceReviewService(db,auth).detail(review_id)


@router.get('/api/logical-documents/{logical_id}/invoice-reviews')
async def history(logical_id:uuid.UUID,before_revision:int|None=Query(default=None,ge=1),limit:int=Query(default=50,ge=1,le=200),
                  auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await InvoiceReviewService(db,auth).history(logical_id,before_revision,limit)


@router.post('/api/logical-documents/{logical_id}/invoice-reviews',status_code=201)
async def create(logical_id:uuid.UUID,body:InvoiceReviewCreate,idempotency_key:uuid.UUID=Header(),expected:uuid.UUID|None=Depends(expected_revision),
                 auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_writer_db)):
    return await InvoiceReviewService(db,auth).create(logical_id,body,idempotency_key,expected)
