"""Explicit property/scope source queries for reserve development."""
from sqlalchemy import select, text, and_, or_

from app.models.payment import Payment
from app.models.cost_entry import CostEntry
from app.models.resolution_register_entry import ResolutionRegisterEntry
from app.models.reserve_ledger_entry import ReserveLedgerEntry
from app.models.economic_plan import EconomicPlan
from app.models.settlement_run import SettlementRun
from app.models.accounting_scope import AccountingScope, AccountingScopeBinding
from app.models.ownership_history import OwnershipHistory
from app.models.unit import Unit
from app.repositories.base import TenantRepository


class ReserveLedgerRepository(TenantRepository[ReserveLedgerEntry]):
    model = ReserveLedgerEntry

    async def require_sources(self, run):
        return await self._session.scalar(text(
            "SELECT property_financial_disclosure_access(:org,:property,'read')"),
            {'org': self._org_id, 'property': run.property_id})

    async def entries(self, run):
        return list((await self._session.scalars(self._base_query().join(SettlementRun,
            (SettlementRun.organization_id == ReserveLedgerEntry.organization_id)
            & (SettlementRun.id == ReserveLedgerEntry.settlement_run_id)).where(
            ReserveLedgerEntry.property_id == run.property_id,
            or_(ReserveLedgerEntry.component == 'FUNDING', and_(
                SettlementRun.period_start == run.period_start, SettlementRun.period_end == run.period_end)))
            .order_by(ReserveLedgerEntry.entry_date, ReserveLedgerEntry.id))).all())

    async def payments(self, run, start=None):
        return list((await self._session.scalars(select(Payment).where(
            Payment.organization_id == self._org_id, Payment.property_id == run.property_id,
            Payment.status == 'booked', Payment.cancelled_at.is_(None),
            Payment.payment_type.in_(('HAUSGELD_VORSCHUSS', 'RUECKLAGENANTEIL', 'RUECKLAGENENTNAHME')),
            Payment.value_date.between(start or run.period_start, run.period_end))
            .order_by(Payment.value_date, Payment.id))).all())

    async def plans(self, run):
        return list((await self._session.scalars(select(EconomicPlan).where(
            EconomicPlan.organization_id == self._org_id, EconomicPlan.property_id == run.property_id,
            EconomicPlan.status == 'finalized', EconomicPlan.superseded_by_id.is_(None),
            EconomicPlan.period_start == run.period_start, EconomicPlan.period_end == run.period_end)
            .order_by(EconomicPlan.id))).all())

    async def payment_plans(self, payment):
        return list((await self._session.scalars(select(EconomicPlan).where(
            EconomicPlan.organization_id == self._org_id, EconomicPlan.property_id == payment.property_id,
            EconomicPlan.status == 'finalized',
            EconomicPlan.period_start <= payment.value_date, EconomicPlan.period_end >= payment.value_date)
            .order_by(EconomicPlan.superseded_by_id.is_not(None), EconomicPlan.created_at.desc(), EconomicPlan.id))).all())

    async def cash_plans(self, run, start):
        return list((await self._session.scalars(select(EconomicPlan).where(
            EconomicPlan.organization_id == self._org_id, EconomicPlan.property_id == run.property_id,
            EconomicPlan.status == 'finalized',
            EconomicPlan.period_start <= run.period_end, EconomicPlan.period_end >= start)
            .order_by(EconomicPlan.superseded_by_id.is_not(None), EconomicPlan.period_start.desc(), EconomicPlan.id))).all())

    async def owner_units(self, subject):
        # Historical owners remain attributable when they pay arrears after a sale.
        rows = await self._session.execute(select(OwnershipHistory.owner_profile_id, OwnershipHistory.unit_id)
            .join(Unit, (Unit.organization_id == OwnershipHistory.organization_id)
                  & (Unit.id == OwnershipHistory.unit_id)).where(
                OwnershipHistory.organization_id == self._org_id, Unit.property_id == subject.property_id))
        result = {}
        for owner, unit in rows:
            result.setdefault(str(owner), set()).add(str(unit))
        return result

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
