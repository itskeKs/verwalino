"""Unreviewed logical sources retain their container's original authorization."""
import uuid
from decimal import Decimal
from types import SimpleNamespace
from fastapi import APIRouter,Depends,Header,HTTPException
from pydantic import BaseModel,ConfigDict,Field,model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.tenant import AuthContext,get_current_user
from app.core.live_principal import get_live_member_db,get_live_writer_db
from app.services.intake_service import IntakeService
from app.services.invoice_model_usage import InvoiceModelUsageOut,summarize

router=APIRouter(prefix='/api/logical-documents',tags=['logical_documents'])
FIELDS='id,document_ref_id,content_sha256,page_start,page_end,verified_page_count,review_state,origin,reason,created_at'


class LogicalDocumentCreate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    document_ref_id:uuid.UUID
    page_start:int|None=Field(default=None,ge=1,le=200,strict=True)
    page_end:int|None=Field(default=None,ge=1,le=200,strict=True)
    reason:str=Field(min_length=1,max_length=1000)

    @model_validator(mode='after')
    def valid_range(self):
        if (self.page_start is None)!=(self.page_end is None) or self.page_start is not None and self.page_end<self.page_start:
            raise ValueError('Both page bounds must form an inclusive range')
        if not self.reason.strip():
            raise ValueError('A reason is required')
        return self


async def read_document(db,org,document):
    row=(await db.execute(text(f'SELECT {FIELDS} FROM logical_document WHERE organization_id=:org AND id=:id'),{'org':org,'id':document})).mappings().first()
    if row is None:
        raise HTTPException(404,'Logical document inaccessible')
    return dict(row)


@router.post('',status_code=201)
async def create(body:LogicalDocumentCreate,idempotency_key:uuid.UUID=Header(),
                 auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_writer_db)):
    document=await IntakeService(db,auth).call('SELECT create_logical_document(:org,:ref,:request,:first,:last,:reason)',
        ref=body.document_ref_id,request=idempotency_key,first=body.page_start,last=body.page_end,reason=body.reason)
    return await read_document(db,auth.organization_id,document)


@router.get('')
async def children(document_ref_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    params={'org':auth.organization_id,'ref':document_ref_id}
    if await db.scalar(text('SELECT id FROM document_ref WHERE organization_id=:org AND id=:ref'),params) is None:
        raise HTTPException(404,'Original reference inaccessible')
    rows=(await db.execute(text(f'SELECT {FIELDS} FROM logical_document WHERE organization_id=:org AND document_ref_id=:ref ORDER BY created_at,id LIMIT 200'),params)).mappings()
    return {'items':[dict(row) for row in rows]}


@router.get('/{document_id}')
async def detail(document_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    return await read_document(db,auth.organization_id,document_id)


@router.get('/{document_id}/processing')
async def processing(document_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    status=await db.scalar(text('SELECT intake_extraction_status(:org,:document)'),{'org':auth.organization_id,'document':document_id})
    if status is None:raise HTTPException(404,'Logical document inaccessible')
    return status


@router.get('/{document_id}/model-usage',response_model=InvoiceModelUsageOut)
async def model_usage(document_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    """What the model-assisted extraction of this logical document has cost so far, over all its attempts (issue #501).

    The ledger rows come from ``intake_model_usage``, which answers only for those who may read the original: for anyone
    else, and for another organization's document, there is no such document. The summary is the one of the invoices."""
    rows=await db.scalar(text('SELECT intake_model_usage(:org,:document)'),{'org':auth.organization_id,'document':document_id})
    if rows is None:raise HTTPException(404,'Logical document inaccessible')
    def amount(value):return None if value is None else Decimal(value)
    return summarize([SimpleNamespace(stage=row['stage'],model_name=row['model_name'],status=row['status'],usage_complete=row['usage_complete'],error_code=row['error_code'],
        input_tokens=row['input_tokens'],output_tokens=row['output_tokens'],cost_usd=amount(row['cost_usd']),
        reserved_input_tokens=row['reserved_input_tokens'],reserved_output_tokens=row['reserved_output_tokens'],
        reserved_cost_usd=amount(row['reserved_cost_usd'])) for row in rows])


class ExtractionRetry(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_fence:uuid.UUID
    reason:str=Field(min_length=1,max_length=1000)


@router.post('/{document_id}/retry-extraction',status_code=202)
async def retry_extraction(document_id:uuid.UUID,body:ExtractionRetry,idempotency_key:uuid.UUID=Header(),
                           auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_writer_db)):
    return await IntakeService(db,auth).call('SELECT retry_intake_extraction(:org,:document,:request,:fence,:reason)',
        document=document_id,request=idempotency_key,fence=body.expected_fence,reason=body.reason)
