"""Current authorized exception attention and immutable historical receipts."""
from datetime import datetime
import uuid
from fastapi import HTTPException
from itsdangerous import BadData,URLSafeTimedSerializer
from sqlalchemy import text
from app.core.config import settings
from app.services.intake_service import IntakeService

EVENT_FIELDS='id,case_id,previous_event_id,revision_number,action,resolution_state,workflow_state,claimant_id,snoozed_until,reason,actor_id,dependency_id,created_at'
CASE_SOURCE='''exception_case c JOIN accounting_scope s ON s.organization_id=c.organization_id AND s.id=c.scope_id
  JOIN LATERAL (SELECT * FROM exception_case_event e WHERE e.organization_id=c.organization_id AND e.case_id=c.id ORDER BY revision_number DESC LIMIT 1) e ON true'''
WORKFLOW="CASE WHEN e.snoozed_until>statement_timestamp() THEN 'snoozed' ELSE 'open' END"
CASE_FIELDS=f'''c.id,c.document_ref_id,c.logical_document_id,c.extraction_revision_id,c.scope_id,c.cause,c.created_at,
  s.label AS scope_label,s.scope_type,e.id AS revision_id,e.revision_number,e.resolution_state,{WORKFLOW} AS workflow_state,
  e.claimant_id,e.snoozed_until,e.created_at AS updated_at,
  (SELECT count(*) FROM exception_dependency d WHERE d.organization_id=c.organization_id AND d.case_id=c.id) AS dependency_count,
  (source_partition_access(c.organization_id,c.document_ref_id,true) AND has_scope_capability(c.organization_id,c.scope_id,'prepare')) AS can_work'''


class ExceptionService(IntakeService):
    async def detail(self,target):
        row=(await self.db.execute(text(f'SELECT {CASE_FIELDS} FROM {CASE_SOURCE} WHERE c.organization_id=:org AND c.id=:target'),
            {'org':self.auth.organization_id,'target':target})).mappings().first()
        if row is None:raise HTTPException(404,'Exception inaccessible')
        return dict(row)

    async def listing(self,workflow,limit,cursor):
        signer=URLSafeTimedSerializer(settings.session_secret,salt='hv-exceptions-v1')
        context=[str(self.auth.organization_id),str(self.auth.user_id),workflow]
        before_time=before_id=None
        if cursor:
            try:
                payload=signer.loads(cursor,max_age=86400)
                if not isinstance(payload,dict) or payload.get('context')!=context:raise ValueError()
                before_time=datetime.fromisoformat(payload['time']);before_id=uuid.UUID(payload['id'])
                if before_time.tzinfo is None:raise ValueError()
            except (BadData,ValueError,TypeError,KeyError):raise HTTPException(422,'Invalid or expired exception cursor') from None
        rows=(await self.db.execute(text(f'''SELECT {CASE_FIELDS} FROM {CASE_SOURCE} WHERE c.organization_id=:org
          AND (CAST(:workflow AS text) IS NULL OR {WORKFLOW}=:workflow)
          AND (CAST(:before_time AS timestamptz) IS NULL OR (c.created_at,c.id)<(:before_time,:before_id))
          ORDER BY c.created_at DESC,c.id DESC LIMIT :take'''),
          {'org':self.auth.organization_id,'workflow':workflow,'before_time':before_time,'before_id':before_id,'take':limit+1})).mappings().all()
        cursor=None
        if len(rows)>limit:
            rows=rows[:limit];last=rows[-1]
            cursor=signer.dumps({'context':context,'time':last['created_at'].isoformat(),'id':str(last['id'])})
        return {'items':[dict(row) for row in rows],'next_cursor':cursor}

    async def history(self,target,before,limit,dependencies=False):
        await self.detail(target)
        fields=','.join('e.'+field for field in EVENT_FIELDS.split(','))
        source='exception_case_event e'
        if dependencies:
            fields='d.id,d.case_id,d.assignment_proposal_id,d.extraction_revision_id,d.cause,d.created_at,e.revision_number,p.revision_number AS proposal_revision_number,p.reason AS proposal_reason'
            source+=' JOIN exception_dependency d ON d.organization_id=e.organization_id AND d.id=e.dependency_id AND d.case_id=e.case_id JOIN assignment_proposal p ON p.organization_id=d.organization_id AND p.id=d.assignment_proposal_id'
        rows=(await self.db.execute(text(f'''SELECT {fields} FROM {source} WHERE e.organization_id=:org AND e.case_id=:target
          AND (CAST(:before AS integer) IS NULL OR e.revision_number<:before) ORDER BY e.revision_number DESC LIMIT :take'''),
          {'org':self.auth.organization_id,'target':target,'before':before,'take':limit+1})).mappings().all()
        return {'items':[dict(row) for row in rows[:limit]],'next_before_revision':rows[limit-1]['revision_number'] if len(rows)>limit else None}

    async def append(self,target,body,key,expected):
        created=await self.call('SELECT append_exception_event(:org,:target,:expected,:request,:verb,:reason,:until_time)',
            target=target,expected=expected,request=key,verb=body.action,reason=body.reason,until_time=body.snoozed_until)
        row=(await self.db.execute(text(f'SELECT {EVENT_FIELDS} FROM exception_case_event WHERE organization_id=:org AND id=:id'),
            {'org':self.auth.organization_id,'id':created})).mappings().first()
        if row is None:raise HTTPException(404,'Exception inaccessible')
        return dict(row)
