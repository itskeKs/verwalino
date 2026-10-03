"""Atomic cost adoption with trusted verification of bytes actually read by URI."""
import uuid
from fastapi import HTTPException
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool
from app.core.db import get_review_tenant_session
from app.core.storage import get_store,verify_document_integrity_and_size
from app.services.intake_service import IntakeService
from app.services.visual_extract_service import verified_bytes


class AllocationAdoptionService(IntakeService):
    async def preview(self,target):
        return await self.call('SELECT allocation_adoption_preview(:org,:target)',target=target)

    async def correction_preview(self,predecessor,target):
        return await self.call('SELECT allocation_correction_preview(:org,:predecessor,:target)',predecessor=predecessor,target=target)

    async def result(self,logical):
        target=await self.call('SELECT allocation_adoption_result(:org,:logical)',logical=logical)
        receipt=await self.call('SELECT allocation_correction_for_set(:org,:target)',target=target) if target is not None else None
        return {'item':await self.detail(target) if target is not None else None,
          'correction':await self.correction_detail(receipt) if receipt is not None else None}

    async def detail(self,target):
        row=(await self.db.execute(text('''SELECT a.id,a.kind,a.source_variant,a.source_document_id,a.primary_cost_id,a.property_id,a.scope_id,a.cost_count,a.financial_state,a.reason,a.confirmed_at,a.actor_id,
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

    async def visual_preview(self,target,disclosure):
        return await self.call('SELECT visual_allocation_preview(:org,:target,:disclosure)',target=target,disclosure=disclosure)

    async def _visual_proofs(self,prepared,key):
        original,visual=prepared['original'],prepared['visual']
        verified=await run_in_threadpool(verify_document_integrity_and_size,original['content_sha256'],original['content_byte_size'],original['storage_uri'],get_store())
        if not verified:raise HTTPException(409,'Original integrity verification failed; no financial effect')
        await run_in_threadpool(verified_bytes,get_store(),visual)
        async with get_review_tenant_session(self.auth.organization_id) as review:
            await review.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(self.auth.user_id)})
            issuer=IntakeService(review,self.auth)
            binding={'request':key,'digest':prepared['request_digest'],'pid':prepared['backend_pid'],'transaction':prepared['transaction_id']}
            original_proof=await issuer.call('SELECT attest_allocation_integrity(:org,:original,:request,:digest,:pid,:transaction,:hash,:size)',
                **binding,original=uuid.UUID(prepared['original_id']),hash=original['content_sha256'],size=original['content_byte_size'])
            visual_proof=await issuer.call('SELECT attest_visual_disclosure(:org,:artifact,:request,:digest,:pid,:transaction,:hash,:size)',
                **binding,artifact=uuid.UUID(prepared['artifact_id']),hash=visual['content_sha256'],size=visual['content_byte_size'])
        return original_proof,visual_proof

    async def create_visual(self,logical,body,key,expected):
        params={'logical':logical,'target':body.treatment_review_id,'disclosure':body.disclosure_id,'expected':expected,
            'hash':body.visual_content_sha256,'request':key,'preview':body.preview_digest,'reason':body.reason,
            'confirmed':body.costs_confirmed,'retained':body.retained_visual_source_reviewed}
        args=':org,:logical,:target,:disclosure,:expected,:hash,:request,:preview,:reason,:confirmed,:retained'
        prepared=await self.call('SELECT prepare_visual_allocation('+args+')',**params)
        if prepared['replay_id'] is not None:return await self.detail(uuid.UUID(prepared['replay_id']))
        original_proof,visual_proof=await self._visual_proofs(prepared,key)
        created=await self.call('SELECT adopt_visual_allocation('+args+',:original_proof,:visual_proof)',
            **params,original_proof=original_proof,visual_proof=visual_proof)
        return await self.detail(created)

    async def visual_correction_preview(self,predecessor,target):
        return await self.call('SELECT visual_allocation_correction_preview(:org,:predecessor,:target)',predecessor=predecessor,target=target)

    async def correct_visual(self,predecessor,body,key,expected):
        params={'predecessor':predecessor,'target':body.treatment_review_id,'hash':body.visual_content_sha256,'expected':expected,
            'request':key,'preview':body.preview_digest,'reason':body.reason,'confirmed':body.costs_confirmed,
            'retained':body.retained_visual_source_reviewed,'followups':body.downstream_followups_reviewed}
        args=':org,:predecessor,:target,:hash,:expected,:request,:preview,:reason,:confirmed,:retained,:followups'
        prepared=await self.call('SELECT prepare_visual_allocation_correction('+args+')',**params)
        if prepared['replay_id'] is not None:return await self.correction_detail(uuid.UUID(prepared['replay_id']))
        original_proof,visual_proof=await self._visual_proofs(prepared,key)
        receipt=await self.call('SELECT correct_visual_allocation('+args+',:original_proof,:visual_proof)',
            **params,original_proof=original_proof,visual_proof=visual_proof)
        return await self.correction_detail(receipt)


    async def correction_detail(self,target):
        receipt=await self.call('SELECT allocation_correction_detail(:org,:target)',target=target)
        return {'receipt':receipt,'allocation':await self.detail(uuid.UUID(receipt['successor_id']))}

    async def correct(self,predecessor,body,key,expected):
        params={'predecessor':predecessor,'target':body.treatment_review_id,'expected':expected,'request':key,'preview':body.preview_digest,
          'reason':body.reason,'confirmed':body.costs_confirmed,'disclosed':body.whole_original_disclosure_reviewed,
          'followups':body.downstream_followups_reviewed}
        arguments=':org,:predecessor,:target,:expected,:request,:preview,:reason,:confirmed,:disclosed,:followups'
        prepared=await self.call('SELECT prepare_allocation_correction('+arguments+')',**params)
        if prepared['replay_id'] is not None:return await self.correction_detail(uuid.UUID(prepared['replay_id']))
        verified=await run_in_threadpool(verify_document_integrity_and_size,prepared['content_sha256'],prepared['content_byte_size'],prepared['storage_uri'],get_store())
        if not verified:raise HTTPException(409,'Original integrity verification failed; no costs replaced')
        async with get_review_tenant_session(self.auth.organization_id) as review:
            await review.execute(text("SELECT set_config('app.current_user_id',:actor,true)"),{'actor':str(self.auth.user_id)})
            proof=await IntakeService(review,self.auth).call('SELECT attest_allocation_integrity(:org,:original,:request,:digest,:pid,:transaction,:hash,:size)',
              original=uuid.UUID(prepared['original_id']),request=key,digest=prepared['request_digest'],pid=prepared['backend_pid'],transaction=prepared['transaction_id'],
              hash=prepared['content_sha256'],size=prepared['content_byte_size'])
        receipt=await self.call('SELECT correct_allocation('+arguments+',:proof)',**params,proof=proof)
        return await self.correction_detail(receipt)
