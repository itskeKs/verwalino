"""Explicit property/scope source queries for reserve development."""
from sqlalchemy import select, text

from app.models.payment import Payment
from app.models.cost_entry import CostEntry
from app.models.resolution_register_entry import ResolutionRegisterEntry
from app.models.reserve_ledger_entry import ReserveLedgerEntry
from app.models.economic_plan import EconomicPlan
from app.models.settlement_run import SettlementRun
from app.models.accounting_scope import AccountingScope, AccountingScopeBinding
from app.repositories.base import TenantRepository


class ReserveLedgerRepository(TenantRepository[ReserveLedgerEntry]):
    model = ReserveLedgerEntry

    async def require_sources(self, run):
        return await self._session.scalar(text(
            "SELECT property_financial_disclosure_access(:org,:property,'read')"),
            {'org': self._org_id, 'property': run.property_id})

    async def entries(self, run):
        return list((await self._session.scalars(self._base_query().where(
            ReserveLedgerEntry.property_id == run.property_id)
            .order_by(ReserveLedgerEntry.entry_date, ReserveLedgerEntry.id))).all())

    async def payments(self, run):
        return list((await self._session.scalars(select(Payment).where(
            Payment.organization_id == self._org_id, Payment.property_id == run.property_id,
            Payment.status == 'booked', Payment.cancelled_at.is_(None),
            Payment.value_date.between(run.period_start, run.period_end))
            .order_by(Payment.value_date, Payment.id))).all())

    async def plans(self, run):
        return list((await self._session.scalars(select(EconomicPlan).where(
            EconomicPlan.organization_id == self._org_id, EconomicPlan.property_id == run.property_id,
            EconomicPlan.status == 'finalized', EconomicPlan.superseded_by_id.is_(None),
            EconomicPlan.period_start == run.period_start, EconomicPlan.period_end == run.period_end)
            .order_by(EconomicPlan.id))).all())

    async def finalized(self, run):
        # Prior-year scopes need their own live read grant, independently of the
        # selected computation scope. The narrow DB helper fails on hidden history.
        previous = await self._session.scalar(text("SELECT current_setting('app.current_scope_id',true)"))
        await self._session.execute(text("SELECT set_config('app.current_scope_id','',true)"))
        await self._session.execute(text('SELECT reserve_history_access(:org,:property,:end)'),
            {'org': self._org_id, 'property': run.property_id, 'end': run.period_end})
        rows = list((await self._session.execute(select(SettlementRun, AccountingScope)
            .outerjoin(AccountingScopeBinding, (AccountingScopeBinding.organization_id == SettlementRun.organization_id)
                       & (AccountingScopeBinding.settlement_run_id == SettlementRun.id))
            .outerjoin(AccountingScope, (AccountingScope.organization_id == SettlementRun.organization_id)
                       & (AccountingScope.id == AccountingScopeBinding.scope_id)).where(
            SettlementRun.organization_id == self._org_id, SettlementRun.property_id == run.property_id,
            SettlementRun.status == 'finalized', SettlementRun.superseded_by_id.is_(None),
            SettlementRun.run_type.in_(('WEG_JAHRESABRECHNUNG', 'VERMOEGENSBERICHT')),
            SettlementRun.period_end <= run.period_end)
            .order_by(SettlementRun.period_end.desc(), SettlementRun.run_type.desc(), SettlementRun.id))).all())
        await self._session.execute(text("SELECT set_config('app.current_scope_id',:scope,true)"), {'scope': previous or ''})
        return rows

    async def current_scope(self, run):
        return await self._session.scalar(select(AccountingScope).join(AccountingScopeBinding,
            (AccountingScopeBinding.organization_id == AccountingScope.organization_id)
            & (AccountingScopeBinding.scope_id == AccountingScope.id)).where(
                AccountingScopeBinding.organization_id == self._org_id,
                AccountingScopeBinding.settlement_run_id == run.id))

    async def funding_sources(self, payment_id, cost_id, register_id):
        result = []
        for model, identifier in ((Payment, payment_id), (CostEntry, cost_id), (ResolutionRegisterEntry, register_id)):
            result.append(await self._session.scalar(select(model).where(
                model.organization_id == self._org_id, model.id == identifier)))
        return result
