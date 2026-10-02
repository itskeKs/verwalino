"""Explicit current classification of bound originals and safe metadata paging."""
import json
import uuid
from fastapi import HTTPException
from itsdangerous import BadData,URLSafeTimedSerializer
from sqlalchemy import select,text,or_
from sqlalchemy.exc import DBAPIError
from app.core.config import settings
from app.models.property import Property
from app.models.property_document import PropertyDocument
from app.models.property_source_classification import PropertySourceClassification
from app.schemas.property_source_classification import PendingSourceClassificationOut
from app.services.authorization_service import AuthorizationService
from app.services.property_document_service import PropertyDocumentService


class PropertySourceClassificationService(AuthorizationService):
    async def _context(self,document_id,lock=False):
        return (await self.session.execute(text('SELECT * FROM property_source_classification_context(:org,:document,:lock)'),
            {'org':self.auth.organization_id,'document':document_id,'lock':lock})).mappings().one_or_none()

    async def pending(self,query):
        await self._lock()
        signer=URLSafeTimedSerializer(settings.session_secret,salt='hv-source-classification-page-v1')
        scope=[str(self.auth.organization_id),str(self.auth.user_id),query.q]
        after=None
        if query.cursor:
            try:
                payload=signer.loads(query.cursor,max_age=86400)
                if not isinstance(payload,dict) or payload.get('scope')!=scope: raise ValueError('Cursor mismatch')
                after=uuid.UUID(payload['id'])
            except (BadData,ValueError,TypeError,KeyError):
                raise HTTPException(422,'Invalid or expired page cursor') from None
        rows=(await self.session.execute(text('SELECT * FROM property_source_classification_candidates(:org,:after,:limit,:q)'),
            {'org':self.auth.organization_id,'after':after,'limit':query.limit+1,'q':query.q})).mappings().all()
        visible=rows[:query.limit]
        cursor=signer.dumps({'scope':scope,'id':str(visible[-1]['document_id'])}) if len(rows)>query.limit else None
        items=[]
        for row in visible:
            context=await self._context(row.document_id)
            if context is None or await self.session.scalar(select(PropertySourceClassification.id).where(
                PropertySourceClassification.organization_id==self.auth.organization_id,PropertySourceClassification.document_id==row.document_id)):
                continue
            name=await self.session.scalar(select(Property.name).where(Property.organization_id==self.auth.organization_id,Property.id==context.property_id))
            can_preview=await self.session.scalar(text('SELECT property_source_classification_content_access(:org,:document)'),
                {'org':self.auth.organization_id,'document':row.document_id})
            items.append(PendingSourceClassificationOut(id=row.document_id,document_id=row.document_id,property_id=context.property_id,
                property_name=name or 'Unavailable property',content_sha256=context.content_sha256,source_context_digest=context.source_context_digest,
                source_uploaded_by=context.source_uploaded_by,linked_import_count=context.linked_import_count,
                can_admit=context.can_admit,can_preview=can_preview,quiescent=context.quiescent))
        return items,cursor

    async def content(self,document_id):
        await self._lock()
        if not await self.session.scalar(text('SELECT property_source_classification_content_access(:org,:document)'),
            {'org':self.auth.organization_id,'document':document_id}):
            raise HTTPException(404,'Source classification not accessible')
        document=await self.session.scalar(select(PropertyDocument).where(PropertyDocument.organization_id==self.auth.organization_id,PropertyDocument.id==document_id))
        if document is None: raise HTTPException(404,'Source classification not accessible')
        return PropertyDocumentService._read_verified_content(document)

    async def get(self,classification_id):
        await self._lock()
        row=await self.session.scalar(select(PropertySourceClassification).where(
            PropertySourceClassification.organization_id==self.auth.organization_id,PropertySourceClassification.id==classification_id))
        if row is None: raise HTTPException(404,'Source classification not accessible')
        return row

    async def _previous(self,document_id,key):
        return await self.session.scalar(select(PropertySourceClassification).where(
            PropertySourceClassification.organization_id==self.auth.organization_id,
            PropertySourceClassification.reviewed_by==self.auth.user_id,
            or_(PropertySourceClassification.request_key==key,PropertySourceClassification.document_id==document_id)
        ).order_by((PropertySourceClassification.request_key==key).desc()).limit(1))

    async def _replay(self,previous,key,digest):
        if await self._context(previous.document_id) is None:
            raise HTTPException(404,'Source classification not accessible')
        if previous.request_key!=key or previous.request_digest!=digest:
            raise HTTPException(409,'Original already classified or Idempotency-Key used with a different request')
        return previous

    async def classify(self,body,key):
        await self._lock()
        digest=await self.session.scalar(text("SELECT encode(sha256(convert_to(CAST(:body AS jsonb)::text,'UTF8')),'hex')"),
            {'body':json.dumps(body.model_dump(mode='json'))})
        previous=await self._previous(body.document_id,key)
        if previous: return await self._replay(previous,key,digest)
        try:
            context=await self._context(body.document_id,lock=True)
            previous=await self._previous(body.document_id,key)
            if previous: return await self._replay(previous,key,digest)
            if context is None: raise HTTPException(404,'Source classification not accessible')
            if (context.property_id,context.content_sha256,context.source_context_digest)!=(body.property_id,body.content_sha256,body.source_context_digest) or not context.quiescent:
                raise HTTPException(409,'Source context changed; inspect the current original')
            if body.decision=='ADMITTED' and not context.can_admit:
                raise HTTPException(404,'Source custody or topology cannot be admitted')
            row=PropertySourceClassification(organization_id=self.auth.organization_id,domain_id=context.domain_id,grant_id=context.grant_id,
                reviewed_by=self.auth.user_id,request_key=key,**body.model_dump())
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except DBAPIError as exc:
            code=getattr(exc.orig,'sqlstate',None)
            if code=='23505':
                previous=await self._previous(body.document_id,key)
                if previous: return await self._replay(previous,key,digest)
                raise HTTPException(409,'Original already classified') from exc
            if code=='42501': raise HTTPException(404,'Source classification not accessible') from exc
            if code=='40001': raise HTTPException(409,'Source context changed; inspect the current original') from exc
            raise
        return row
