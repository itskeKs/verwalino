"""Unreviewed logical sources retain their container's original authorization."""
import uuid
from fastapi import APIRouter,Depends,Header,HTTPException
from pydantic import BaseModel,ConfigDict,Field,model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.tenant import AuthContext,get_current_user
from app.core.live_principal import get_live_member_db,get_live_writer_db
from app.services.intake_service import IntakeService

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
