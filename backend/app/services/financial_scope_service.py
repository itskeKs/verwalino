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

    async def enter(self, subject, *, capability=ScopeCapability.READ, computation=False):
        # Keep classification stable too: setup takes this lock exclusively.
        await self.session.execute(text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:key,0))"),
            {'key': f'scope-acl:{self.org_id}'})
        kind = subject.__tablename__
        if kind not in {'bank_account', 'payment', 'cost_entry', 'settlement_run', 'economic_plan'}:
            raise ValueError('Unknown financial subject')
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
            await self.session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {'key': f'financial-inputs:{self.org_id}'})
        if computation and not await self.session.scalar(text('SELECT scope_financial_inputs_classified(:org,:scope)'),
                {'org': self.org_id, 'scope': scope_id}):
            raise HTTPException(409, 'Review unclassified financial inputs before calculating this accounting scope')
        return scope_id
