"""Explicit human fields retain their proposal basis and do not adopt costs."""
import json
from fastapi import HTTPException
from sqlalchemy import text
from app.services.intake_service import IntakeService

FIELDS=','.join('r.'+field for field in 'id,document_ref_id,logical_document_id,partition_revision_id,extraction_revision_id,assignment_proposal_id,scope_id,content_sha256,previous_revision_id,revision_number,seller_name,document_number,document_kind,currency,invoice_date,service_period_from,service_period_to,fields_reviewed,review_scope,reason,actor_id,created_at'.split(','))+',r.gross_amount::text AS gross_amount,r.vat_amount::text AS vat_amount,r.vat_rate::text AS vat_rate,p.state AS assignment_state,p.blockers AS assignment_blockers'
SOURCE='invoice_review_revision r JOIN assignment_proposal p ON p.organization_id=r.organization_id AND p.id=r.assignment_proposal_id'


class InvoiceReviewService(IntakeService):
    async def detail(self,review):
        row=(await self.db.execute(text(f'SELECT {FIELDS} FROM {SOURCE} WHERE r.organization_id=:org AND r.id=:id'),
            {'org':self.auth.organization_id,'id':review})).mappings().first()
        if row is None:raise HTTPException(404,'Invoice review inaccessible')
        return dict(row)

    async def history(self,logical,before,limit):
        if await self.db.scalar(text('SELECT id FROM logical_document WHERE organization_id=:org AND id=:logical'),
            {'org':self.auth.organization_id,'logical':logical}) is None:raise HTTPException(404,'Logical document inaccessible')
        rows=(await self.db.execute(text(f'''SELECT {FIELDS} FROM {SOURCE} WHERE r.organization_id=:org AND r.logical_document_id=:logical
          AND (CAST(:before AS integer) IS NULL OR r.revision_number<:before) ORDER BY r.revision_number DESC LIMIT :take'''),
          {'org':self.auth.organization_id,'logical':logical,'before':before,'take':limit+1})).mappings().all()
        reviewable=await self.db.scalar(text('''SELECT CASE WHEN source_partition_access(organization_id,document_ref_id,true)
          AND has_scope_capability(organization_id,scope_id,'prepare') THEN id END FROM assignment_proposal
          WHERE organization_id=:org AND logical_document_id=:logical ORDER BY revision_number DESC LIMIT 1'''),
          {'org':self.auth.organization_id,'logical':logical})
        return {'items':[dict(row) for row in rows[:limit]],'next_before_revision':rows[limit-1]['revision_number'] if len(rows)>limit else None,
                'reviewable_assignment_id':reviewable}

    async def create(self,logical,body,key,expected):
        fields=body.model_dump(mode='json',exclude={'assignment_proposal_id','reason','fields_reviewed'})
        created=await self.call('SELECT create_invoice_review(:org,:logical,:proposal,:expected,:request,CAST(:fields AS jsonb),:reason,:ack)',
            logical=logical,proposal=body.assignment_proposal_id,expected=expected,request=key,fields=json.dumps(fields),reason=body.reason,ack=body.fields_reviewed)
        return await self.detail(created)
