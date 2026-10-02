"""Bounded extraction history retains source RLS and never changes reviewed costs."""
import uuid
from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.tenant import AuthContext,get_current_user
from app.core.live_principal import get_live_member_db

router=APIRouter(tags=['extraction_revisions'])
HEADERS='id,document_ref_id,invoice_attempt_id,content_sha256,processor_version,schema_version,status,error_code,validation_status,created_at'


@router.get('/api/document-refs/{ref_id}/extractions')
async def history(ref_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    params={'org':auth.organization_id,'ref':ref_id}
    if await db.scalar(text('SELECT id FROM document_ref WHERE organization_id=:org AND id=:ref'),params) is None:
        raise HTTPException(404,'Original reference inaccessible')
    rows=(await db.execute(text(f'SELECT {HEADERS} FROM extraction_revision WHERE organization_id=:org AND document_ref_id=:ref ORDER BY created_at DESC,id DESC LIMIT 200'),params)).mappings()
    return {'items':[dict(row) for row in rows]}


@router.get('/api/extraction-revisions/{revision_id}')
async def detail(revision_id:uuid.UUID,auth:AuthContext=Depends(get_current_user),db:AsyncSession=Depends(get_live_member_db)):
    params={'org':auth.organization_id,'id':revision_id}
    row=(await db.execute(text(f'SELECT {HEADERS},payload FROM extraction_revision WHERE organization_id=:org AND id=:id'),params)).mappings().first()
    if row is None:raise HTTPException(404,'Extraction revision inaccessible')
    fields=(await db.execute(text('''SELECT field_name,value_type,value,confidence,locator_status,validation_status,page_number,bbox,quotation,xml_path
      FROM field_evidence WHERE organization_id=:org AND extraction_revision_id=:id ORDER BY field_name'''),params)).mappings()
    return {**row,'fields':[dict(field) for field in fields]}
