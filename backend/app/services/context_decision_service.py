"""Human context assertions expose only their complete current audience."""
from fastapi import HTTPException
from sqlalchemy import text
from app.services.intake_service import IntakeService

FIELDS=','.join('d.'+name for name in 'id,document_ref_id,logical_document_id,scope_id,property_id,assignment_proposal_id,invoice_review_id,case_id,dependency_id,previous_revision_id,revision_number,method,snapshot,snapshot_digest,quotation,page_number,xml_path,comparison_note,context_reviewed,actor_id,created_at'.split(','))


class ContextDecisionService(IntakeService):
    async def preview(self,proposal):
        preview=await self.call("""WITH value AS MATERIALIZED (SELECT context_comparison_snapshot(:org,:proposal) AS snapshot)
          SELECT jsonb_build_object('snapshot',snapshot,'snapshot_digest',encode(sha256(convert_to(snapshot::text,'UTF8')),'hex')) FROM value""",proposal=proposal)
        # Immutable proposal/dependency metadata is still filtered by its own RLS.
        basis=(await self.db.execute(text('''SELECT p.logical_document_id,p.id AS assignment_proposal_id,l.page_start,l.page_end,
          (source_partition_access(p.organization_id,p.document_ref_id,true) AND has_scope_capability(p.organization_id,p.scope_id,'prepare')) AS can_review,
          (SELECT id FROM invoice_review_revision r WHERE r.organization_id=p.organization_id AND r.logical_document_id=p.logical_document_id ORDER BY revision_number DESC LIMIT 1) AS invoice_review_id,
          d.case_id,d.id AS dependency_id,
          (SELECT id FROM exception_case_event e WHERE e.organization_id=d.organization_id AND e.case_id=d.case_id ORDER BY revision_number DESC LIMIT 1) AS case_event_id
          FROM assignment_proposal p JOIN logical_document l ON l.organization_id=p.organization_id AND l.id=p.logical_document_id LEFT JOIN exception_dependency d ON d.organization_id=p.organization_id AND d.assignment_proposal_id=p.id AND d.cause='scope_hint_conflict'
          WHERE p.organization_id=:org AND p.id=:proposal'''),{'org':self.auth.organization_id,'proposal':proposal})).mappings().first()
        if basis is None:raise HTTPException(404,'Context comparison inaccessible')
        return {**dict(basis),**preview}

    async def detail(self,target):
        row=(await self.db.execute(text(f'SELECT {FIELDS} FROM context_decision d WHERE d.organization_id=:org AND d.id=:target'),
          {'org':self.auth.organization_id,'target':target})).mappings().first()
        if row is None:raise HTTPException(404,'Context comparison inaccessible')
        return dict(row)

    async def history(self,logical,before,limit):
        if await self.db.scalar(text('SELECT id FROM logical_document WHERE organization_id=:org AND id=:logical'),
          {'org':self.auth.organization_id,'logical':logical}) is None:raise HTTPException(404,'Logical document inaccessible')
        rows=(await self.db.execute(text(f'''SELECT {FIELDS} FROM context_decision d WHERE d.organization_id=:org AND d.logical_document_id=:logical
          AND (CAST(:before AS integer) IS NULL OR d.revision_number<:before) ORDER BY d.revision_number DESC LIMIT :take'''),
          {'org':self.auth.organization_id,'logical':logical,'before':before,'take':limit+1})).mappings().all()
        return {'items':[dict(row) for row in rows[:limit]],'next_before_revision':rows[limit-1]['revision_number'] if len(rows)>limit else None}

    async def create(self,logical,body,key,expected):
        if body.invoice_review_id is not None:
            await self.require_parents('invoice_review_revision',[body.invoice_review_id])
        created=await self.call('''SELECT create_context_decision(:org,:logical,:proposal,:review,:expected,:request,:digest,:event,:quote,:page,:path,:note,:ack)''',
          logical=logical,proposal=body.assignment_proposal_id,review=body.invoice_review_id,expected=expected,request=key,
          digest=body.snapshot_digest,event=body.expected_case_event_id,quote=body.quotation,page=body.page_number,path=body.xml_path,note=body.comparison_note,ack=body.context_reviewed)
        return await self.detail(created)
