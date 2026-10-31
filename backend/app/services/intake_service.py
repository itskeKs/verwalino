"""Atomic intake commands; the trusted admission attests freshly received bytes."""
from datetime import datetime
import hashlib
import json
import uuid
from fastapi import HTTPException
from itsdangerous import BadData,URLSafeTimedSerializer
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.core.config import settings
from app.core.db import get_review_tenant_session
from app.core.storage import put_document


class IntakeService:
    def __init__(self,db,auth):
        self.db,self.auth=db,auth

    async def require_parents(self,table,parent_ids):
        ids=set(parent_ids)
        if not ids:return
        visible=set((await self.db.scalars(text(
            f'SELECT id FROM {table} WHERE organization_id=:org AND id=ANY(CAST(:ids AS uuid[]))'),
            {'org':self.auth.organization_id,'ids':list(ids)})).all())
        if visible!=ids:raise HTTPException(404,'Intake parent inaccessible')

    async def call(self,sql,**params):
        try:
            return await self.db.scalar(text(sql),{'org':self.auth.organization_id,**params})
        except DBAPIError as error:
            code=getattr(error.orig,'sqlstate',None)
            if code in {'42501','22023','23505','23514','23503'}:
                raise HTTPException({'42501':404,'23505':409}.get(code,422),
                    'Intake context inaccessible' if code=='42501' else 'Intake command conflicts with its manifest or prior receipt') from None
            raise

    async def create(self,body,key):
        return await self.call('SELECT create_intake_batch(:org,:request,:scope,CAST(:items AS jsonb))',request=key,
            scope=body.scope_hint_id,items=json.dumps([item.model_dump() for item in body.items]))

    async def headers(self,limit,cursor):
        signer=URLSafeTimedSerializer(settings.session_secret,salt='hv-intake-headers-v1')
        context=[str(self.auth.organization_id),str(self.auth.user_id)]
        before_time=before_id=None
        if cursor:
            try:
                payload=signer.loads(cursor,max_age=86400)
                if not isinstance(payload,dict) or payload.get('context')!=context:
                    raise ValueError()
                before_time=datetime.fromisoformat(payload['time'])
                if before_time.tzinfo is None:
                    raise ValueError()
                before_id=uuid.UUID(payload['id'])
            except (BadData,ValueError,TypeError,KeyError):
                raise HTTPException(422,'Invalid or expired inbox cursor') from None
        rows=await self.call('SELECT intake_batch_headers(:org,:before_time,:before_id,:take)',before_time=before_time,before_id=before_id,take=limit)
        cursor=None
        if len(rows)>limit:
            rows=rows[:limit];last=rows[-1]
            cursor=signer.dumps({'context':context,'time':last['created_at'],'id':last['id']})
        return {'items':rows,'next_cursor':cursor}

    async def status(self,batch,limit,cursor):
        signer=URLSafeTimedSerializer(settings.session_secret,salt='hv-intake-page-v1')
        context=[str(self.auth.organization_id),str(self.auth.user_id),str(batch)]
        after=0
        if cursor:
            try:
                payload=signer.loads(cursor,max_age=86400)
                if not isinstance(payload,dict) or payload.get('context')!=context or type(payload.get('after')) is not int or not 0<=payload['after']<=100:
                    raise ValueError()
                after=payload['after']
            except (BadData,ValueError,TypeError,KeyError):
                raise HTTPException(422,'Invalid or expired intake cursor') from None
        value=await self.call('SELECT intake_batch_status(:org,:batch,:after,:take)',batch=batch,after=after,take=limit)
        rows=value['items'];value['next_cursor']=None
        if len(rows)>limit:
            rows=rows[:limit];value['next_cursor']=signer.dumps({'context':context,'after':rows[-1]['position']})
        value['items']=rows
        return value

    async def upload(self,batch,key,data,mime):
        target=await self.call('SELECT intake_upload_target(:org,:batch,:key)',batch=batch,key=key)
        digest=hashlib.sha256(data).hexdigest()
        if len(data)!=target['content_byte_size'] or digest!=target['content_sha256'] or mime!=target['mime_type']:
            raise HTTPException(422,'Received bytes or MIME differ from the manifest')
        if target['receipt'] is not None:
            return target['receipt']
        stored=await put_document(self.db,self.auth.organization_id,data,content_type=mime)
        if stored.sha256!=digest or stored.byte_size!=len(data):
            raise HTTPException(409,'Stored original integrity mismatch')
        original=uuid.uuid4()
        payload={'id':str(original),'organization_id':str(self.auth.organization_id),'item_id':target['id'],
            'uploaded_by':str(self.auth.user_id),'content_sha256':digest,'content_byte_size':len(data),'mime_type':mime,
            'storage_uri':stored.uri,'storage_backend':stored.backend,'storage_version':stored.version}
        async with get_review_tenant_session(self.auth.organization_id) as review:
            await review.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(self.auth.user_id)})
            await review.execute(text('SELECT admit_intake_upload(:org,:original,CAST(:payload AS jsonb))'),
                {'org':self.auth.organization_id,'original':original,'payload':json.dumps(payload)})
        return await self.call('SELECT receive_intake_original(:org,:batch,:key,CAST(:payload AS jsonb))',batch=batch,key=key,payload=json.dumps(payload))
