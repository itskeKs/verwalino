"""Read-only registry operations retain source RLS and bounded cursor semantics."""
from fastapi import HTTPException
from sqlalchemy import select,or_
from app.models.document_ref import DocumentRef
from app.repositories.pagination import page_rows


class DocumentRefRepository:
    def __init__(self,session,auth):
        self.session,self.auth=session,auth

    async def page(self,query,source_kind=None,source_id=None):
        if query.q:
            raise HTTPException(422,'Original registry does not search protected content')
        filters=[]
        if source_kind: filters.append(DocumentRef.source_kind==source_kind)
        if source_id:
            filters.append(or_(DocumentRef.invoice_document_id==source_id,DocumentRef.property_document_id==source_id,DocumentRef.mandate_document_id==source_id))
        return await page_rows(self.session,self.auth,DocumentRef,DocumentRef.source_kind,(),query,
            filters=tuple(filters),context=f'{source_kind or ""}:{source_id or ""}')

    async def get(self,ref_id):
        row=await self.session.scalar(select(DocumentRef).where(DocumentRef.organization_id==self.auth.organization_id,DocumentRef.id==ref_id))
        if row is None: raise HTTPException(404,'Original reference not accessible')
        return row
