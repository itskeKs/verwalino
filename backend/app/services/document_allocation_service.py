"""Balanced proposals are separate from financial acceptance and source release."""
import json
from fastapi import HTTPException
from sqlalchemy import text
from app.services.intake_service import IntakeService

FIELDS=','.join('a.'+name for name in 'id,document_ref_id,logical_document_id,scope_id,property_id,assignment_proposal_id,invoice_review_id,context_decision_id,previous_revision_id,revision_number,line_count,allocation_kind,financial_state,reason,allocation_reviewed,actor_id,created_at'.split(','))+''',r.gross_amount::text AS gross_amount,r.vat_amount::text AS vat_amount,r.currency,
  (SELECT jsonb_agg(jsonb_build_object('id',l.id,'position',l.position,'description',l.description,'cost_category_id',l.cost_category_id,
    'cost_category_plan_version',l.cost_category_plan_version,'category_code',l.category_code,'category_label',l.category_label,
    'gross_amount',round(l.gross_amount,2)::text,'vat_amount',round(l.vat_amount,2)::text) ORDER BY l.position)
    FROM allocation_proposal_line l WHERE l.organization_id=a.organization_id AND l.allocation_proposal_id=a.id) AS lines'''
SOURCE='allocation_proposal a JOIN invoice_review_revision r ON r.organization_id=a.organization_id AND r.id=a.invoice_review_id'


class DocumentAllocationService(IntakeService):
    async def preview(self,decision):
        row=(await self.db.execute(text("""SELECT d.id AS context_decision_id,d.logical_document_id,d.assignment_proposal_id,d.invoice_review_id,
          d.snapshot->'scope'->>'label' AS scope_label,r.gross_amount::text AS gross_amount,r.vat_amount::text AS vat_amount,r.currency,
          (source_partition_access(d.organization_id,d.document_ref_id,true) AND has_scope_capability(d.organization_id,d.scope_id,'prepare')) AS can_propose
          FROM context_decision d JOIN invoice_review_revision r ON r.organization_id=d.organization_id AND r.id=d.invoice_review_id
          WHERE d.organization_id=:org AND d.id=:decision"""),{'org':self.auth.organization_id,'decision':decision})).mappings().first()
        if row is None:raise HTTPException(404,'Allocation context inaccessible')
        return dict(row)

    async def detail(self,target):
        row=(await self.db.execute(text(f'SELECT {FIELDS} FROM {SOURCE} WHERE a.organization_id=:org AND a.id=:target'),
          {'org':self.auth.organization_id,'target':target})).mappings().first()
        if row is None:raise HTTPException(404,'Allocation proposal inaccessible')
        return dict(row)

    async def history(self,logical,before,limit):
        if await self.db.scalar(text('SELECT id FROM logical_document WHERE organization_id=:org AND id=:logical'),
          {'org':self.auth.organization_id,'logical':logical}) is None:raise HTTPException(404,'Logical document inaccessible')
        rows=(await self.db.execute(text(f'''SELECT {FIELDS} FROM {SOURCE} WHERE a.organization_id=:org AND a.logical_document_id=:logical
          AND (CAST(:before AS integer) IS NULL OR a.revision_number<:before) ORDER BY a.revision_number DESC LIMIT :take'''),
          {'org':self.auth.organization_id,'logical':logical,'before':before,'take':limit+1})).mappings().all()
        return {'items':[dict(row) for row in rows[:limit]],'next_before_revision':rows[limit-1]['revision_number'] if len(rows)>limit else None}

    async def create(self,logical,body,key,expected):
        created=await self.call('SELECT create_allocation_proposal(:org,:logical,:decision,:expected,:request,CAST(:lines AS jsonb),:reason,:ack)',
          logical=logical,decision=body.context_decision_id,expected=expected,request=key,lines=json.dumps([line.model_dump(mode='json') for line in body.lines]),reason=body.reason,ack=body.allocation_reviewed)
        return await self.detail(created)
