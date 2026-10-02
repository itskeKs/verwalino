"""Exact provenance and live capabilities for dunning effects."""
import uuid

from fastapi import HTTPException
from sqlalchemy import select, text

from app.core.rbac import ScopeCapability
from app.core.tenant import AuthContext
from app.models.dunning_scope_context import DunningScopeContext
from app.services.authorization_service import AuthorizationService
from app.services.financial_scope_service import FinancialScopeService


class DunningScopeService:
    def __init__(self, session, org_id, user_id):
        self.session, self.org_id, self.user_id = session, org_id, user_id
        self.financial = FinancialScopeService(session, org_id, user_id)
        self.auth = AuthorizationService(session, AuthContext(user_id, org_id, 'live database role required'))

    async def prepare(self, scope_id, scope_type, period_start, period_end):
        sphere = await self.financial.select_report_scope(scope_id, scope_type=scope_type,
            period_start=period_start, period_end=period_end)
        if sphere is not None:
            await self.auth.require(sphere.id, ScopeCapability.PREPARE)
        if not await self.session.scalar(text("SELECT report_scope_source_access(:org,:scope,'prepare')"),
                {'org': self.org_id, 'scope': sphere.id if sphere is not None else None}):
            raise HTTPException(404, 'Complete dunning sources not accessible')
        return sphere

    async def history(self, *, tenant_id=None, owner_id=None):
        if not await self.session.scalar(text('SELECT dunning_history_complete(:org,:tenant,:owner)'),
                {'org': self.org_id, 'tenant': tenant_id, 'owner': owner_id}):
            raise HTTPException(409, 'Review unclassified dunning history before calculating an escalation level')

    async def attach(self, notice, sphere, period_start, period_end, *, lease_id=None, plan_id=None):
        if sphere is None:
            return
        notice.id = uuid.uuid4()
        context = DunningScopeContext(organization_id=self.org_id, dunning_notice_id=notice.id,
            scope_id=sphere.id, lease_agreement_id=lease_id, economic_plan_id=plan_id,
            period_start=period_start, period_end=period_end, created_by=self.user_id,
            reason='Prepared from exact scoped financial statement')
        self.session.add(context)
        # Notice FK is deferred: provenance must exist before the INSERT guard.
        await self.session.flush()

    async def enter(self, notice, capability):
        await self.financial.lock_inputs()
        contact_capability = 'export' if capability == ScopeCapability.EXPORT else 'read' if capability == ScopeCapability.READ else 'prepare'
        if not await self.session.scalar(text('SELECT dunning_source_access(:org,:notice,:capability)'),
                {'org': self.org_id, 'notice': notice.id, 'capability': contact_capability}):
            raise HTTPException(404, 'Dunning property sources not accessible')
        if not await self.session.scalar(text('SELECT dunning_contact_access(:org,:tenant,:owner,:capability)'),
                {'org':self.org_id, 'tenant':notice.tenant_profile_id, 'owner':notice.owner_profile_id, 'capability':contact_capability}):
            raise HTTPException(404, 'Dunning recipient source not accessible')
        if not await self.session.scalar(text('SELECT dunning_scope_access(:org,:notice,:capability)'),
                {'org': self.org_id, 'notice': notice.id, 'capability': capability.value}):
            raise HTTPException(404, 'Dunning notice not accessible')
        context = await self.session.scalar(select(DunningScopeContext).where(
            DunningScopeContext.organization_id == self.org_id,
            DunningScopeContext.dunning_notice_id == notice.id))
        if context is not None:
            await self.auth.require(context.scope_id, capability)
            await self.session.execute(text("SELECT set_config('app.current_scope_id',:scope,true)"),
                {'scope': str(context.scope_id)})
        return context
