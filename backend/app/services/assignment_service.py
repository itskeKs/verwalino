"""Immutable manual context suggestions; no financial or sharing effects."""
from fastapi import HTTPException
from sqlalchemy import text
from app.services.intake_service import IntakeService

FIELDS=','.join('p.'+field for field in 'id,document_ref_id,logical_document_id,partition_revision_id,extraction_revision_id,scope_id,scope_hint_id,previous_proposal_id,revision_number,method,state,blockers,reason,created_at'.split(','))+',s.label AS scope_label,s.scope_type'
SOURCE='assignment_proposal p JOIN accounting_scope s ON s.organization_id=p.organization_id AND s.id=p.scope_id'


class AssignmentService(IntakeService):
    async def history(self,logical,before,limit):
        ref=await self.db.scalar(text('SELECT document_ref_id FROM logical_document WHERE organization_id=:org AND id=:logical'),
            {'org':self.auth.organization_id,'logical':logical})
        if ref is None:raise HTTPException(404,'Logical document inaccessible')
        rows=(await self.db.execute(text(f'''SELECT {FIELDS} FROM {SOURCE} WHERE p.organization_id=:org AND p.logical_document_id=:logical
          AND (CAST(:before AS integer) IS NULL OR p.revision_number<:before) ORDER BY p.revision_number DESC LIMIT :take'''),
          {'org':self.auth.organization_id,'logical':logical,'before':before,'take':limit+1})).mappings().all()
        return {'items':[dict(row) for row in rows[:limit]],'next_before_revision':rows[limit-1]['revision_number'] if len(rows)>limit else None}

    async def create(self,logical,body,key,expected):
        created=await self.call('SELECT create_assignment_proposal(:org,:logical,:partition,:extraction,:scope,:expected,:request,:reason)',
            logical=logical,partition=body.partition_revision_id,extraction=body.extraction_revision_id,scope=body.scope_id,
            expected=expected,request=key,reason=body.reason)
        row=(await self.db.execute(text(f'SELECT {FIELDS} FROM {SOURCE} WHERE p.organization_id=:org AND p.id=:id'),
            {'org':self.auth.organization_id,'id':created})).mappings().first()
        if row is None:raise HTTPException(404,'Assignment context inaccessible')
        return dict(row)
