"""Visible candidate pages never establish global duplicate clearance."""
import uuid
from fastapi import HTTPException
from itsdangerous import BadData,URLSafeTimedSerializer
from sqlalchemy import text
from app.core.config import settings
from app.services.intake_service import IntakeService

FIELDS="to_jsonb(d)-ARRAY['organization_id','request_key','request_digest']"
VERSION='pg16-lower-space-v1'


class DuplicateComparisonService(IntakeService):
    async def review(self,target):
        row=(await self.db.execute(text('SELECT id,scope_id,logical_document_id FROM invoice_review_revision WHERE organization_id=:org AND id=:id'),
          {'org':self.auth.organization_id,'id':target})).mappings().first()
        if row is None:raise HTTPException(404,'Review inaccessible')
        return row

    async def candidates(self,subject,limit,cursor):
        review=await self.review(subject)
        signer=URLSafeTimedSerializer(settings.session_secret,salt='hv-duplicate-candidates-v1')
        context=[str(self.auth.organization_id),str(self.auth.user_id),str(subject),str(review['scope_id']),VERSION]
        after=None
        if cursor:
            try:
                payload=signer.loads(cursor,max_age=86400)
                if not isinstance(payload,dict) or payload.get('context')!=context:raise ValueError()
                after=uuid.UUID(payload['after'])
            except (BadData,ValueError,TypeError,KeyError):
                raise HTTPException(422,'Invalid or expired candidate cursor') from None
        value=await self.call('SELECT duplicate_candidates(:org,:subject,:after,:take)',subject=subject,after=after,take=limit)
        rows=value['items'];value['next_cursor']=None
        if len(rows)>limit:
            rows=rows[:limit];value['next_cursor']=signer.dumps({'context':context,'after':rows[-1]['logical_document_id']})
        value['items']=rows
        return value

    async def detail(self,target):
        result=await self.db.scalar(text(f'SELECT {FIELDS} FROM duplicate_comparison d WHERE d.organization_id=:org AND d.id=:id'),
          {'org':self.auth.organization_id,'id':target})
        if result is None:raise HTTPException(404,'Comparison inaccessible')
        return result

    async def history(self,subject,reference,before,limit):
        a=await self.review(subject);b=await self.review(reference)
        if a['scope_id']!=b['scope_id'] or a['logical_document_id']==b['logical_document_id']:raise HTTPException(422,'Distinct same-scope sources required')
        rows=(await self.db.execute(text(f'''SELECT {FIELDS} FROM duplicate_comparison d WHERE d.organization_id=:org AND d.scope_id=:scope
          AND d.pair_low=least(CAST(:a AS uuid),CAST(:b AS uuid)) AND d.pair_high=greatest(CAST(:a AS uuid),CAST(:b AS uuid))
          AND (CAST(:before AS integer) IS NULL OR d.revision_number<:before) ORDER BY d.revision_number DESC LIMIT :take'''),
          {'org':self.auth.organization_id,'scope':a['scope_id'],'a':a['logical_document_id'],'b':b['logical_document_id'],'before':before,'take':limit+1})).scalars().all()
        return {'items':rows[:limit],'next_before_revision':rows[limit-1]['revision_number'] if len(rows)>limit else None}

    async def create(self,subject,body,key,expected):
        created=await self.call('SELECT create_duplicate_comparison(:org,:subject,:reference,:expected,:request,:verdict,:reason,:ack)',
          subject=subject,reference=body.reference_review_id,expected=expected,request=key,verdict=body.verdict,reason=body.reason,ack=body.comparison_reviewed)
        return await self.detail(created)
