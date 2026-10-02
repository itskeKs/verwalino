"""Exact calculation context; visibility alone must never define financial totals."""
from fastapi import HTTPException
from sqlalchemy import select, text

from app.core.rbac import ScopeCapability
from app.core.tenant import AuthContext
from app.models.accounting_scope import AccountingScopeBinding
from app.services.authorization_service import AuthorizationService


class FinancialScopeService:
    def __init__(self, session, org_id, user_id):
        self.session, self.org_id, self.user_id = session, org_id, user_id

    async def lock_inputs(self):
        # Stable order: scope ACL, financial sources, then financial row locks.
        await self.session.execute(text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:key,0))"),
            {'key': f'scope-acl:{self.org_id}'})
        await self.session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {'key': f'financial-inputs:{self.org_id}'})

    async def select_read_scope(self, scope_id=None):
        """Install one live, complete read context; omitted means legacy only."""
        await self.lock_inputs()
        selected = await self.session.scalar(text("SELECT NULLIF(current_setting('app.current_scope_id',true),'')::uuid"))
        if selected is not None and scope_id is not None and selected != scope_id:
            raise HTTPException(409, 'Report scope differs from its financial source')
        scope_id = scope_id or selected
        if scope_id is None:
            await self.require_report_sources(None)
            return None
        sphere = await AuthorizationService(self.session,
            AuthContext(self.user_id, self.org_id, 'live database role required')).require(scope_id, ScopeCapability.READ)
        await self.session.execute(text("SELECT set_config('app.current_scope_id',:scope,true)"), {'scope': str(scope_id)})
        if not await self.session.scalar(text('SELECT scope_financial_inputs_classified(:org,:scope)'),
                {'org': self.org_id, 'scope': scope_id}):
            raise HTTPException(409, 'Review unclassified financial inputs before calculating this accounting scope')
        await self.require_report_sources(scope_id)
        return sphere

    async def require_report_sources(self, scope_id):
        if not await self.session.scalar(text("SELECT report_scope_source_access(:org,:scope,'read')"),
                {'org': self.org_id, 'scope': scope_id}):
            raise HTTPException(404, 'Complete report sources not accessible')

    async def select_report_scope(self, scope_id, *, scope_type, period_start, period_end):
        """Read-only statement context, including an inherited plan context."""
        if period_start > period_end:
            raise HTTPException(422, 'Report period must not end before it starts')
        sphere = await self.select_read_scope(scope_id)
        if sphere is None:
            return None
        if sphere.scope_type != scope_type:
            raise HTTPException(422, 'Accounting scope does not support this report type')
        if period_start < sphere.valid_from or sphere.valid_to is not None and period_end > sphere.valid_to:
            raise HTTPException(422, 'Report period is outside accounting scope')
        return sphere

    async def prepare_new(self, subject, scope_id=None):
        await self.lock_inputs()
        selected = await self.session.scalar(text("SELECT NULLIF(current_setting('app.current_scope_id',true),'')::uuid"))
        if scope_id is not None and selected is not None and scope_id != selected:
            raise HTTPException(409, 'Creation scope differs from its financial source')
        scope_id = scope_id or selected
        if scope_id is None:
            if await self.session.scalar(text('SELECT property_has_classified_finance(:org,:property)'),
                    {'org': self.org_id, 'property': subject.property_id}):
                raise HTTPException(409, 'Select an exact accounting scope for this property')
            return None
        sphere = await AuthorizationService(self.session, AuthContext(self.user_id, self.org_id, 'live database role required')).require(scope_id, ScopeCapability.PREPARE)
        if sphere.property_id != subject.property_id:
            raise HTTPException(422, 'Accounting scope belongs to another property')
        if sphere.scope_type == 'SEV':
            raise HTTPException(422, 'SEV financial adapters are not available yet')
        if hasattr(subject, 'management_mandate_id'):
            if subject.management_mandate_id is not None and subject.management_mandate_id != sphere.management_mandate_id:
                raise HTTPException(422, 'Financial mandate must match accounting scope')
            subject.management_mandate_id = sphere.management_mandate_id
        start = getattr(subject, 'service_period_from', getattr(subject, 'period_start', None))
        end = getattr(subject, 'service_period_to', getattr(subject, 'period_end', None))
        if start is not None and (start < sphere.valid_from or sphere.valid_to is not None and end > sphere.valid_to):
            raise HTTPException(422, 'Financial period is outside accounting scope')
        kind = subject.__tablename__
        incompatible = (kind == 'economic_plan' and sphere.scope_type != 'WEG'
            or kind == 'settlement_run' and (subject.run_type == 'BK_MIETER') == (sphere.scope_type == 'WEG')
            or kind == 'bank_account' and (sphere.scope_type == 'WEG' and subject.account_type == 'KAUTION'
                or sphere.scope_type != 'WEG' and subject.account_type == 'RUECKLAGE'))
        if incompatible:
            raise HTTPException(422, 'Financial accounting type differs from scope')
        await self.session.execute(text("SELECT set_config('app.current_scope_id',:scope,true)"), {'scope': str(scope_id)})
        unit_id = getattr(subject, 'unit_id', None)
        if unit_id is not None and not await self.session.scalar(text('SELECT unit_in_financial_context(:org,:unit)'),
                {'org': self.org_id, 'unit': unit_id}):
            raise HTTPException(422, 'Financial unit is outside accounting scope')
        return sphere

    async def enter(self, subject, *, capability=ScopeCapability.READ, computation=False):
        # Keep classification stable too: setup takes this lock exclusively.
        await self.session.execute(text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:key,0))"),
            {'key': f'scope-acl:{self.org_id}'})
        kind = subject.__tablename__
        if kind not in {'bank_account', 'payment', 'cost_entry', 'settlement_run', 'economic_plan'}:
            raise ValueError('Unknown financial subject')
        if computation and not await self.session.scalar(text('SELECT property_financial_source_access(:org,:property)'),
                {'org': self.org_id, 'property': subject.property_id}):
            raise HTTPException(404, 'Complete property sources not accessible')
        scope_id = await self.session.scalar(select(AccountingScopeBinding.scope_id).where(
            AccountingScopeBinding.organization_id == self.org_id,
            getattr(AccountingScopeBinding, kind + '_id') == subject.id))
        if scope_id is None:
            if computation and await self.session.scalar(text(
                'SELECT property_has_classified_finance(:org,:property)'),
                {'org': self.org_id, 'property': subject.property_id}):
                raise HTTPException(409, 'Classify this financial draft before calculating a scoped property')
            return None
        auth = AuthContext(self.user_id, self.org_id, 'live database role required')
        await AuthorizationService(self.session, auth).require(scope_id, capability)
        await self.session.execute(text("SELECT set_config('app.current_scope_id',:scope,true)"), {'scope': str(scope_id)})
        if computation:
            # Source writers share this existing lock. Acquire it before the
            # completeness check, not only later when hashing visible rows.
            await self.lock_inputs()
        if computation and not await self.session.scalar(text('SELECT scope_financial_inputs_classified(:org,:scope)'),
                {'org': self.org_id, 'scope': scope_id}):
            raise HTTPException(409, 'Review unclassified financial inputs before calculating this accounting scope')
        return scope_id

    async def require_export(self, subject):
        """Copied source facts need both financial and property export rights."""
        scope_id = await self.enter(subject, capability=ScopeCapability.EXPORT)
        if scope_id is None and await self.session.scalar(text(
                'SELECT property_source_security_mode(:org)'), {'org': self.org_id}) != 'PREPARATION':
            raise HTTPException(404, 'Financial export requires a reviewed accounting scope')
        if not await self.session.scalar(text("SELECT property_financial_disclosure_access(:org,:property,'export')"),
                {'org': self.org_id, 'property': subject.property_id}):
            raise HTTPException(404, 'Financial source export not accessible')
