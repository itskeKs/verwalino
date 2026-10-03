"""Expose retained treatment evidence through the allocation audience."""
import json
from fastapi import HTTPException
from sqlalchemy import text
from app.services.intake_service import IntakeService

FIELDS=','.join('a.'+name for name in 'id,logical_document_id,allocation_proposal_id,previous_revision_id,revision_number,financial_state,marker_policy,snapshot,snapshot_digest,reason,treatment_reviewed,actor_id,created_at'.split(','))+''',
  (SELECT jsonb_agg(jsonb_build_object('id',l.id,'allocation_line_id',l.allocation_line_id,'expense_intent',l.expense_intent,'tax_intent',l.tax_intent,
    'tenant_treatment',l.tenant_treatment,'tenant_amount',round(l.tenant_amount,2)::text,'tenant_distribution_key',l.tenant_distribution_key,
    'owner_distribution_key',l.owner_distribution_key,'documentary_basis',l.documentary_basis,'markers',l.markers) ORDER BY b.position)
   FROM allocation_treatment_line l JOIN allocation_proposal_line b ON b.organization_id=l.organization_id AND b.id=l.allocation_line_id
   WHERE l.organization_id=a.organization_id AND l.treatment_review_id=a.id) AS lines'''


class AllocationTreatmentService(IntakeService):
    async def preview(self,target):
        row=(await self.db.execute(text('''SELECT snapshot,encode(sha256(convert_to(snapshot::text,'UTF8')),'hex') AS snapshot_digest,
          (source_partition_access(:org,a.document_ref_id,true) AND has_scope_capability(:org,a.scope_id,'prepare')) AS can_review
          FROM allocation_proposal a CROSS JOIN LATERAL (SELECT allocation_treatment_snapshot(:org,a.id) AS snapshot) s
          WHERE a.organization_id=:org AND a.id=:target'''),{'org':self.auth.organization_id,'target':target})).mappings().first()
        if row is None or row['snapshot'] is None:raise HTTPException(404,'Treatment basis inaccessible')
        return dict(row)

    async def detail(self,target):
        row=(await self.db.execute(text(f'SELECT {FIELDS} FROM allocation_treatment_review a WHERE a.organization_id=:org AND a.id=:target'),
          {'org':self.auth.organization_id,'target':target})).mappings().first()
        if row is None:raise HTTPException(404,'Treatment review inaccessible')
        return dict(row)

    async def history(self,logical,before,limit):
        if await self.db.scalar(text('SELECT id FROM logical_document WHERE organization_id=:org AND id=:logical'),
          {'org':self.auth.organization_id,'logical':logical}) is None:raise HTTPException(404,'Logical document inaccessible')
        rows=(await self.db.execute(text(f'''SELECT {FIELDS} FROM allocation_treatment_review a WHERE a.organization_id=:org AND a.logical_document_id=:logical
          AND (CAST(:before AS integer) IS NULL OR a.revision_number<:before) ORDER BY a.revision_number DESC LIMIT :take'''),
          {'org':self.auth.organization_id,'logical':logical,'before':before,'take':limit+1})).mappings().all()
        return {'items':[dict(row) for row in rows[:limit]],'next_before_revision':rows[limit-1]['revision_number'] if len(rows)>limit else None}

    async def create(self,logical,body,key,expected):
        created=await self.call('SELECT create_allocation_treatment(:org,:logical,:allocation,:expected,:request,:preview,CAST(:lines AS jsonb),:reason,:ack)',
          logical=logical,allocation=body.allocation_proposal_id,expected=expected,request=key,preview=body.preview_digest,
          lines=json.dumps([line.model_dump(mode='json') for line in body.lines]),reason=body.reason,ack=body.treatment_reviewed)
        return await self.detail(created)
