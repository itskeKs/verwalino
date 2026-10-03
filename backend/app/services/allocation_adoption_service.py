"""Atomic cost adoption with trusted verification of bytes actually read by URI."""
import uuid
from fastapi import HTTPException
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool
from app.core.db import get_review_tenant_session
from app.core.storage import get_store,verify_document_integrity_and_size
from app.services.intake_service import IntakeService


class AllocationAdoptionService(IntakeService):
    async def preview(self,target):
        return await self.call('SELECT allocation_adoption_preview(:org,:target)',target=target)

    async def result(self,logical):
        target=await self.call('SELECT allocation_adoption_result(:org,:logical)',logical=logical)
        return {'item':await self.detail(target) if target is not None else None}

    async def detail(self,target):
        row=(await self.db.execute(text('''SELECT a.id,a.kind,a.source_document_id,a.primary_cost_id,a.property_id,a.scope_id,a.cost_count,a.financial_state,a.reason,a.confirmed_at,a.actor_id,
          (SELECT jsonb_agg(jsonb_build_object('cost_entry_id',l.cost_entry_id,'position',l.position,'gross_amount',c.gross_amount::text,
            'vat_amount',c.vat_amount::text,'tenant_amount',c.apportionable_tenant_amount::text) ORDER BY l.position)
           FROM document_cost_link l JOIN cost_entry c ON c.organization_id=l.organization_id AND c.id=l.cost_entry_id
           WHERE l.organization_id=a.organization_id AND l.allocation_set_id=a.id) AS costs
          FROM allocation_set a WHERE a.organization_id=:org AND a.id=:target'''),{'org':self.auth.organization_id,'target':target})).mappings().first()
        if row is None:raise HTTPException(404,'Allocation set inaccessible')
        return dict(row)

    async def create(self,logical,body,key,expected):
        params={'logical':logical,'target':body.treatment_review_id,'expected':expected,'request':key,'preview':body.preview_digest,
          'reason':body.reason,'confirmed':body.costs_confirmed,'disclosed':body.whole_original_disclosure_reviewed}
        arguments=':org,:logical,:target,:expected,:request,:preview,:reason,:confirmed,:disclosed'
        prepared=await self.call('SELECT prepare_allocation_adoption('+arguments+')',**params)
        if prepared['replay_id'] is not None:return await self.detail(uuid.UUID(prepared['replay_id']))
        verified=await run_in_threadpool(verify_document_integrity_and_size,prepared['content_sha256'],prepared['content_byte_size'],prepared['storage_uri'],get_store())
        if not verified:raise HTTPException(409,'Original integrity verification failed; no costs adopted')
        async with get_review_tenant_session(self.auth.organization_id) as review:
            await review.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(self.auth.user_id)})
            proof=await IntakeService(review,self.auth).call('SELECT attest_allocation_integrity(:org,:original,:request,:digest,:pid,:transaction,:hash,:size)',
              original=uuid.UUID(prepared['original_id']),request=key,digest=prepared['request_digest'],pid=prepared['backend_pid'],transaction=prepared['transaction_id'],
              hash=prepared['content_sha256'],size=prepared['content_byte_size'])
        created=await self.call('SELECT adopt_allocation('+arguments+',:proof)',**params,proof=proof)
        return await self.detail(created)
