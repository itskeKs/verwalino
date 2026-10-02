"""Checked present-day admission; no implicit historical permission or binding."""
import json
from fastapi import HTTPException
from sqlalchemy import func,select,text,or_
from sqlalchemy.exc import DBAPIError
from app.models.property import Property
from app.models.property_document import PropertyDocument
from app.models.property_source_admission_review import PropertySourceAdmissionReview
from app.repositories.pagination import page_rows
from app.schemas.property_source_admission_review import PendingSourceReviewOut
from app.services.authorization_service import AuthorizationService
from app.services.property_document_service import PropertyDocumentService


class PropertySourceAdmissionReviewService(AuthorizationService):
    async def pending(self,query):
        rows,cursor=await page_rows(self.session,self.auth,PropertyDocument,PropertyDocument.title,[PropertyDocument.title],query,
            filters=(func.property_source_review_pending(self.auth.organization_id,PropertyDocument.id),),context='pending-source-review')
        items=[]
        for document in rows:
            context=await self._context(document.id)
            if context is None or not await self.session.scalar(
                text('SELECT property_source_review_pending(:org,:document)'),
                {'org':self.auth.organization_id,'document':document.id}):
                continue  # Recheck pending state after the page query.
            name=await self.session.scalar(select(Property.name).where(Property.organization_id==self.auth.organization_id,Property.id==context.property_id))
            items.append(PendingSourceReviewOut(id=document.id,document_id=document.id,import_id=context.import_id,
                property_id=context.property_id,property_name=name or 'Unavailable property',draft_revision=context.draft_revision,
                title=document.title,original_filename=document.original_filename,content_sha256=document.content_sha256))
        return items,cursor

    async def get(self,review_id):
        await self._lock()
        row=await self.session.scalar(select(PropertySourceAdmissionReview).where(
            PropertySourceAdmissionReview.organization_id==self.auth.organization_id,
            PropertySourceAdmissionReview.id==review_id))
        if row is None:
            raise HTTPException(404,'Source review not accessible')
        return row

    async def content(self,document_id):
        await self._lock()
        if not await self.session.scalar(text('SELECT property_source_review_access(:org,:document)'),
            {'org':self.auth.organization_id,'document':document_id}):
            raise HTTPException(404,'Source review not accessible')
        document=await self.session.scalar(select(PropertyDocument).where(
            PropertyDocument.organization_id==self.auth.organization_id,PropertyDocument.id==document_id))
        if document is None:
            raise HTTPException(404,'Source review not accessible')
        return PropertyDocumentService._read_verified_content(document)

    async def _context(self,document_id,lock=False):
        return (await self.session.execute(text('SELECT * FROM property_source_review_context(:org,:document,:lock)'),
            {'org':self.auth.organization_id,'document':document_id,'lock':lock})).mappings().one_or_none()

    async def _previous(self,document_id,key):
        return await self.session.scalar(select(PropertySourceAdmissionReview).where(
            PropertySourceAdmissionReview.organization_id==self.auth.organization_id,
            PropertySourceAdmissionReview.reviewed_by==self.auth.user_id,
            or_(PropertySourceAdmissionReview.request_key==key,PropertySourceAdmissionReview.document_id==document_id)
        ).order_by((PropertySourceAdmissionReview.request_key==key).desc()).limit(1))

    async def _replay(self,previous,key,digest):
        allowed=await self.session.scalar(text("SELECT has_property_data_capability(:org,:domain,'prepare') "
            "AND has_property_data_capability(:org,:domain,'approve') AND "
            "(property_source_review_access(:org,:document) OR property_source_access(:org,'property_document',:document,'read'))"),
            {'org':self.auth.organization_id,'domain':previous.domain_id,'document':previous.document_id})
        if not allowed:
            raise HTTPException(404,'Source review not accessible')
        if previous.request_key!=key or previous.request_digest!=digest:
            raise HTTPException(409,'Original already reviewed or Idempotency-Key used with a different request')
        return previous

    async def review(self,body,key):
        await self._lock()
        digest=await self.session.scalar(text("SELECT encode(sha256(convert_to(CAST(:body AS jsonb)::text,'UTF8')),'hex')"),
            {'body':json.dumps(body.model_dump(mode='json'))})
        previous=await self._previous(body.document_id,key)
        if previous:
            return await self._replay(previous,key,digest)
        context=await self._context(body.document_id,lock=True)
        previous=await self._previous(body.document_id,key)
        if previous:
            return await self._replay(previous,key,digest)
        if context is None:
            raise HTTPException(404,'Source review not accessible')
        document=await self.session.scalar(select(PropertyDocument).where(PropertyDocument.organization_id==self.auth.organization_id,
            PropertyDocument.id==body.document_id))
        if document is None:
            raise HTTPException(404,'Source review not accessible')
        if context.import_id!=body.import_id or context.draft_revision!=body.draft_revision or document.content_sha256!=body.content_sha256:
            raise HTTPException(409,'Source review context changed; inspect the current original and revision')
        if not await self.session.scalar(text('SELECT property_source_review_pending(:org,:document)'),
            {'org':self.auth.organization_id,'document':body.document_id}):
            raise HTTPException(409,'Original is already admitted or its import is not quiescent')
        row=PropertySourceAdmissionReview(organization_id=self.auth.organization_id,property_id=context.property_id,
            domain_id=context.domain_id,grant_id=context.grant_id,reviewed_by=self.auth.user_id,request_key=key,
            **body.model_dump())
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except DBAPIError as exc:
            code=getattr(exc.orig,'sqlstate',None)
            if code=='23505':
                previous=await self._previous(body.document_id,key)
                if previous:
                    return await self._replay(previous,key,digest)
                raise HTTPException(409,'Original already admitted') from exc
            if code=='42501':
                raise HTTPException(404,'Source review not accessible') from exc
            if code=='40001':
                raise HTTPException(409,'Source review context changed') from exc
            raise
        return row
