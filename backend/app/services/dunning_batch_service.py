"""Atomic tenant draft preparation and actor-scoped idempotent replay."""
import hashlib
import json
import uuid
from dataclasses import asdict
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select, text

from app.core.rbac import ScopeCapability
from app.engine.dunning import DEFAULT_DUE_DAYS, DEFAULT_FEES, MAX_LEVEL
from app.models.dunning_batch import DunningBatch
from app.models.dunning_notice import DunningNotice
from app.repositories.lease_rent_change_repo import LeaseRentChangeRepository
from app.services.dunning_scope_service import DunningScopeService
from app.services.dunning_service import DunningService


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, default=str).encode()).hexdigest()


def _row(row):
    return {column.key: getattr(row, column.key) for column in row.__table__.columns}


class DunningBatchService:
    def __init__(self, session, org_id, user_id):
        self.session, self.org_id, self.user_id = session, org_id, user_id
        self.dunning = DunningService(session, org_id, user_id)
        self.context = DunningScopeService(session, org_id, user_id)

    @staticmethod
    def _arguments(body):
        return dict(period_start=body.period_start, period_end=body.period_end,
            reference_date=body.reference_date, grace_days=body.grace_days,
            accounting_scope_id=body.accounting_scope_id,
            override_fee=Decimal(body.override_fee) if body.override_fee is not None else None,
            base_interest_rate=body.resolved_base_rate())

    async def preview(self, body):
        args = self._arguments(body)
        sphere = await self.context.prepare(body.accounting_scope_id, 'MIET',
            body.period_start, body.period_end)
        # Same full lease/statement path as /payments/open-items. The complete
        # report-source check above precedes RLS enumeration; denials aren't skips.
        leases = await self.dunning._leases.list_all()
        candidates, sources = [], []
        for lease in sorted(leases, key=lambda item: str(item.id)):
            if sphere is not None and not await self.session.scalar(text(
                    "SELECT unit_in_financial_context(:org,:unit)"),
                    {'org': self.org_id, 'unit': lease.unit_id}):
                continue
            if lease.valid_from > body.period_end or (lease.valid_to is not None and lease.valid_to <= body.period_start):
                continue
            lease, tenant, statement, assembled = await self.dunning.tenant_basis(
                tenant_profile_id=lease.tenant_profile_id, lease_agreement_id=lease.id, **args)
            _, _, person, unit, prop = await self.dunning.require_tenant_sources(
                lease.id, tenant.id, sphere)
            payments = await self.dunning._payments.list_for_lease(lease.id)
            changes = await LeaseRentChangeRepository(self.session, self.org_id).list_for_lease(lease.id)
            issued = (await self.session.scalars(select(DunningNotice).where(
                DunningNotice.organization_id == self.org_id,
                DunningNotice.tenant_profile_id == tenant.id,
                DunningNotice.status == 'issued').order_by(DunningNotice.id))).all()
            sources.append({'lease': _row(lease), 'rent_changes': [_row(row) for row in changes],
                'payments': [_row(row) for row in sorted(payments, key=lambda row: str(row.id))],
                'statement': asdict(statement), 'issued': [_row(row) for row in issued],
                'dunning': asdict(assembled) if assembled is not None else None})
            if assembled is None or assembled.principal_amount <= Decimal(body.threshold):
                continue
            candidates.append({
                'lease_agreement_id': str(lease.id), 'tenant_profile_id': str(tenant.id),
                'person_name': person.company_name or ' '.join(part for part in
                    (person.first_name, person.last_name) if part),
                'unit_number': unit.number, 'property_name': prop.name,
                'principal_amount': str(assembled.principal_amount),
                'fee_amount': str(assembled.fee_amount),
                'interest_amount': str(assembled.interest_amount),
                'total_amount': str(assembled.total_amount), 'level': assembled.level,
            })
            if len(candidates) > 500:
                raise HTTPException(422, 'Dunning preview exceeds 500 candidates; narrow the accounting scope or period')
        parameters = {key: value for key, value in body.model_dump(mode='json').items()
            if key not in {'lease_agreement_ids', 'expected_source_digest', 'request_id'}}
        return {'candidates': candidates, 'source_digest': _digest({
            'parameters': parameters, 'resolved_arguments': args,
            'scope': _row(sphere) if sphere is not None else None,
            'defaults': {'fees': DEFAULT_FEES, 'due_days': DEFAULT_DUE_DAYS, 'max_level': MAX_LEVEL},
            'sources': sources, 'candidates': candidates,
        })}

    async def _authorize_results(self, results):
        notices = []
        for result in results:
            notice = await self.dunning._repo.get_by_id(uuid.UUID(result['notice_id']))
            if notice is None:
                raise HTTPException(404, 'Original dunning notice not accessible')
            context = await self.context.enter(notice, ScopeCapability.PREPARE)
            lease_id = uuid.UUID(result['lease_agreement_id'])
            if context is not None and context.lease_agreement_id != lease_id:
                raise HTTPException(409, 'Original dunning source differs from batch')
            await self.dunning.require_tenant_sources(lease_id, notice.tenant_profile_id, context)
            notices.append(notice)
        return notices

    async def apply(self, body):
        # All operations use the global order ACL -> financial -> request.
        await self.context.financial.lock_inputs()
        await self.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
            {'key': f'dunning-batch:{self.org_id}:{self.user_id}:{body.request_id}'})
        payload = body.model_dump(mode='json')
        payload['base_interest_rate_supplied'] = 'base_interest_rate' in body.model_fields_set
        request_digest = _digest(payload)
        batch = await self.session.get(DunningBatch, (self.org_id, self.user_id, body.request_id))
        if batch is not None:
            if batch.request_digest != request_digest:
                raise HTTPException(409, 'Request ID already used with a different payload')
            return await self._authorize_results(batch.result)

        preview = await self.preview(body)
        if preview['source_digest'] != body.expected_source_digest:
            raise HTTPException(409, 'Dunning sources changed; refresh the preview')
        eligible = {row['lease_agreement_id']: row for row in preview['candidates']}
        if any(str(lease_id) not in eligible for lease_id in body.lease_agreement_ids):
            raise HTTPException(404, 'Selected lease is not an eligible dunning candidate')
        results = []
        args = self._arguments(body)
        for lease_id in body.lease_agreement_ids:
            candidate = eligible[str(lease_id)]
            notice = await self.dunning.create_dunning_for_tenant(
                tenant_profile_id=uuid.UUID(candidate['tenant_profile_id']),
                lease_agreement_id=lease_id, **args)
            results.append({'notice_id': str(notice.id), 'lease_agreement_id': str(lease_id)})
        self.session.add(DunningBatch(organization_id=self.org_id, actor_user_id=self.user_id,
            request_id=body.request_id, request_digest=request_digest, result=results))
        await self.session.flush()
        # No commit here: all drafts, audits, provenance and result identities
        # belong to the function-scoped HTTP transaction, including late failure.
        return await self._authorize_results(results)
