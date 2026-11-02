"""Explicit property/scope source queries for reserve development."""
import json
from decimal import Decimal
from sqlalchemy import select, text, and_, or_, func

from app.models.payment import Payment
from app.models.cost_entry import CostEntry
from app.models.resolution import Resolution
from app.models.resolution_register_entry import ResolutionRegisterEntry
from app.models.reserve_ledger_entry import ReserveLedgerEntry
from app.models.economic_plan import EconomicPlan
from app.models.settlement_run import SettlementRun
from app.models.accounting_scope import AccountingScope, AccountingScopeBinding
from app.models.ownership_history import OwnershipHistory
from app.models.unit import Unit
from app.models.owner_profile import OwnerProfile
from app.models.person import Person
from app.repositories.base import TenantRepository
from app.engine.reserve_ledger import monthly_reserve_entries


class ReserveLedgerRepository(TenantRepository[ReserveLedgerEntry]):
    model = ReserveLedgerEntry

    async def display_entries(self, entries):
        """Enrich this read without changing retained ledger snapshots or amounts.

        Each source query remains subject to its own RLS and organization filter.
        Resolve each source type once rather than once per monthly plan row.
        """
        fields = {}
        for model, reference, display, column in (
            (Payment, 'payment_id', 'payment_note', Payment.note),
            (CostEntry, 'cost_entry_id', 'cost_document_number', CostEntry.document_number),
            (ResolutionRegisterEntry, 'register_entry_id', 'register_sequence_number', ResolutionRegisterEntry.sequence_number),
            (Unit, 'unit_id', 'unit_number', Unit.number),
        ):
            identifiers = {row[reference] for row in entries if row.get(reference)}
            values = await self._session.execute(select(model.id, column).where(
                model.organization_id == self._org_id, model.id.in_(identifiers))) if identifiers else []
            fields[reference] = (display, {str(identifier): value for identifier, value in values})
        owners = {row['owner_profile_id'] for row in entries if row.get('owner_profile_id') not in (None, 'None')}
        people = await self._session.execute(select(OwnerProfile.id, Person.first_name, Person.last_name, Person.company_name)
            .join(Person, (Person.organization_id == OwnerProfile.organization_id) & (Person.id == OwnerProfile.person_id))
            .where(OwnerProfile.organization_id == self._org_id, OwnerProfile.id.in_(owners))) if owners else []
        names = {str(identifier): company or f'{first} {last}'.strip() for identifier, first, last, company in people}
        return [{**row, 'owner_name': names.get(row.get('owner_profile_id')),
                 **{display: values.get(row.get(reference)) for reference, (display, values) in fields.items()
                    if reference != 'unit_id' or row.get(reference)}}
                for row in entries]

    async def require_sources(self, run):
        return await self._session.scalar(text(
            "SELECT property_financial_disclosure_access(:org,:property,'read')"),
            {'org': self._org_id, 'property': run.property_id})

    async def entries(self, run):
        return list((await self._session.scalars(self._base_query().join(SettlementRun,
            (SettlementRun.organization_id == ReserveLedgerEntry.organization_id)
            & (SettlementRun.id == ReserveLedgerEntry.settlement_run_id)).where(
            ReserveLedgerEntry.property_id == run.property_id,
            or_(ReserveLedgerEntry.payment_id.is_not(None), and_(
                SettlementRun.period_start == run.period_start, SettlementRun.period_end == run.period_end)))
            .order_by(ReserveLedgerEntry.entry_date, ReserveLedgerEntry.created_at, ReserveLedgerEntry.id))).all())

    async def payments(self, run, start=None):
        return list((await self._session.scalars(select(Payment).where(
            Payment.organization_id == self._org_id, Payment.property_id == run.property_id,
            Payment.status == 'booked', Payment.cancelled_at.is_(None),
            Payment.payment_type.in_(('HAUSGELD_VORSCHUSS', 'RUECKLAGENANTEIL', 'RUECKLAGENENTNAHME')),
            Payment.value_date.between(start or run.period_start, run.period_end))
            .order_by(Payment.value_date, Payment.created_at, Payment.id))).all())

    async def plans(self, run):
        return list((await self._session.scalars(select(EconomicPlan).where(
            EconomicPlan.organization_id == self._org_id, EconomicPlan.property_id == run.property_id,
            EconomicPlan.status == 'finalized', EconomicPlan.superseded_by_id.is_(None),
            EconomicPlan.period_start == run.period_start, EconomicPlan.period_end == run.period_end)
            .order_by(EconomicPlan.id))).all())

    async def cash_plan(self, run, when):
        return await self._session.scalar(select(EconomicPlan).where(
            EconomicPlan.organization_id == self._org_id,
            EconomicPlan.id == func.reserve_cash_plan(self._org_id, run.property_id,
                run.period_start, run.period_end, run.economic_plan_id, when)))

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

    async def owner_units(self, payments):
        """Resolve all payers' units at their individual cash value dates in one query."""
        if not payments:
            return {}
        rows = await self._session.execute(select(Payment.id, OwnershipHistory.unit_id)
            .join(OwnershipHistory, (OwnershipHistory.organization_id == Payment.organization_id)
                  & (OwnershipHistory.owner_profile_id == Payment.owner_profile_id)
                  & (OwnershipHistory.valid_from <= Payment.value_date)
                  & or_(OwnershipHistory.valid_to.is_(None), OwnershipHistory.valid_to > Payment.value_date))
            .join(Unit, (Unit.organization_id == OwnershipHistory.organization_id)
                  & (Unit.id == OwnershipHistory.unit_id)).where(
                Payment.organization_id == self._org_id, Payment.id.in_([p.id for p in payments]),
                Unit.property_id == Payment.property_id))
        result = {}
        for payment, unit in rows:
            result.setdefault(payment, set()).add(str(unit))
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


    async def evidence_sources(self, cost_id, register_id):
        cost = await self._session.scalar(select(CostEntry).where(
            CostEntry.organization_id == self._org_id, CostEntry.id == cost_id)) if cost_id else None
        register = await self._session.scalar(select(ResolutionRegisterEntry).where(
            ResolutionRegisterEntry.organization_id == self._org_id, ResolutionRegisterEntry.id == register_id)) if register_id else None
        result = await self._session.scalar(select(Resolution.result).where(
            Resolution.organization_id == self._org_id, Resolution.id == register.resolution_id)) if register else None
        return cost, register, result

    async def funded_amount(self, cost_id, exclude_payment_id=None):
        return await self._session.scalar(text('SELECT reserve_funded_amount(:org,:cost,:payment)'),
            {'org': self._org_id, 'cost': cost_id, 'payment': exclude_payment_id})

    async def plan_cells(self, plan):
        return await self._session.scalar(text('SELECT reserve_plan_cells(CAST(:payload AS jsonb),:total,:starts)'),
            {'payload': json.dumps(plan.snapshot_payload), 'total': plan.reserve_contribution_total, 'starts': plan.period_start})

    async def plan_rows(self, plan):
        cells = await self.plan_cells(plan)
        rows = monthly_reserve_entries(
            [(c['unit_id'], c['owner_profile_id'], Decimal(c['amount'])) for c in cells], plan.period_start)
        numbers = {c['unit_id']: c['unit_number'] for c in cells}
        return [{**row, 'unit_number': numbers[row['unit_id']]} for row in rows]

    async def hausgeld_share(self, amount, units, plan):
        return await self._session.scalar(text('SELECT reserve_hausgeld_share(:amount,:units,CAST(:planned AS jsonb),CAST(:owners AS jsonb))'),
            {'amount': amount, 'units': list(units), 'planned': json.dumps(await self.plan_cells(plan)),
             'owners': json.dumps(plan.snapshot_payload['result']['owners'])})

    async def unit(self, run, unit_id):
        return await self._session.scalar(select(Unit).where(Unit.organization_id == self._org_id,
            Unit.property_id == run.property_id, Unit.id == unit_id))

    async def contribution_headroom(self, run, unit_id):
        return await self._session.scalar(text('SELECT reserve_contribution_headroom(:org,:run,:unit)'),
            {'org': self._org_id, 'run': run.id, 'unit': unit_id})

    async def has_unsupported_contributions(self, run):
        return await self._session.scalar(text('''SELECT EXISTS(
            SELECT 1 FROM reserve_ledger_entry e JOIN settlement_run r
              ON r.organization_id=e.organization_id AND r.id=e.settlement_run_id
            WHERE e.organization_id=:org AND e.property_id=:property AND e.unit_id IS NOT NULL
              AND r.period_start=:starts AND r.period_end=:ends
              AND reserve_contribution_headroom(:org,:run,e.unit_id)<0)'''),
            {'org': self._org_id, 'run': run.id, 'property': run.property_id,
             'starts': run.period_start, 'ends': run.period_end})

    async def contribution_options(self, run):
        rows = await self._session.execute(select(Unit.id, Unit.number,
            text('reserve_contribution_headroom(unit.organization_id,:run,unit.id)')).where(
                Unit.organization_id == self._org_id, Unit.property_id == run.property_id).order_by(Unit.number, Unit.id),
            {'run': run.id})
        return [{'unit_id': str(unit), 'unit_number': number, 'remaining': format(max(Decimal('0'), remaining), '.2f')}
                for unit, number, remaining in rows]

    async def _opening_period_exists(self, run, scope_id, query):
        # A selected calculation scope hides compatible prior/next WEG scopes.
        # Keep live organization/source RLS while comparing all readable scopes.
        previous = await self._session.scalar(text("SELECT current_setting('app.current_scope_id',true)"))
        await self._session.execute(text("SELECT set_config('app.current_scope_id','',true)"))
        exists = await self._session.scalar(text(query),
            {'org': self._org_id, 'property': run.property_id, 'start': run.period_start, 'end': run.period_end,
             'scope': scope_id, 'mandate': run.management_mandate_id})
        await self._session.execute(text("SELECT set_config('app.current_scope_id',:scope,true)"), {'scope': previous or ''})
        return exists

    async def has_earlier_ledger(self, run):
        scope = await self.current_scope(run)
        return await self._opening_period_exists(run, scope.id if scope else None,
            """SELECT EXISTS(SELECT 1 FROM settlement_run r
            WHERE r.organization_id=:org AND r.property_id=:property AND r.period_start<:start
              AND r.run_type IN ('WEG_JAHRESABRECHNUNG','VERMOEGENSBERICHT')
              AND r.superseded_by_id IS NULL
              AND reserve_ledger_scope_matches(:org,
                (SELECT b.scope_id FROM accounting_scope_binding b WHERE b.organization_id=r.organization_id AND b.settlement_run_id=r.id),
                CAST(:scope AS uuid),r.management_mandate_id,CAST(:mandate AS uuid)))""")

    async def has_later_opening_import(self, run, scope_id):
        return await self._opening_period_exists(run, scope_id,
            """SELECT EXISTS(SELECT 1 FROM settlement_run r
            JOIN reserve_ledger_entry e ON e.organization_id=r.organization_id AND e.settlement_run_id=r.id
            WHERE r.organization_id=:org AND r.property_id=:property
              AND (r.period_start>:start OR (
                r.period_start<=:end AND r.period_end>=:start
                AND (r.period_start<>:start OR r.period_end<>:end)))
              AND r.run_type IN ('WEG_JAHRESABRECHNUNG','VERMOEGENSBERICHT') AND e.component='OPENING'
              AND reserve_ledger_scope_matches(:org,
                (SELECT b.scope_id FROM accounting_scope_binding b WHERE b.organization_id=r.organization_id AND b.settlement_run_id=r.id),
                CAST(:scope AS uuid),r.management_mandate_id,CAST(:mandate AS uuid)))""")

    async def has_finalized_opening_period(self, run):
        scope = await self.current_scope(run)
        return await self._opening_period_exists(run, scope.id if scope else None,
            """SELECT EXISTS(SELECT 1 FROM settlement_run r
            WHERE r.organization_id=:org AND r.property_id=:property AND r.status='finalized'
              AND r.period_start=:start AND r.period_end=:end
              AND r.run_type IN ('WEG_JAHRESABRECHNUNG','VERMOEGENSBERICHT')
              AND reserve_ledger_scope_matches(:org,
                (SELECT b.scope_id FROM accounting_scope_binding b WHERE b.organization_id=r.organization_id AND b.settlement_run_id=r.id),
                CAST(:scope AS uuid),r.management_mandate_id,CAST(:mandate AS uuid)))""")

    async def has_overlapping_ledger(self, run):
        scope = await self.current_scope(run)
        return await self._opening_period_exists(run, scope.id if scope else None,
            """SELECT EXISTS(SELECT 1 FROM settlement_run r
            WHERE r.organization_id=:org AND r.property_id=:property
              AND r.period_start<=:end AND r.period_end>=:start
              AND (r.period_start<>:start OR r.period_end<>:end)
              AND r.run_type IN ('WEG_JAHRESABRECHNUNG','VERMOEGENSBERICHT')
              AND r.superseded_by_id IS NULL
              AND reserve_ledger_scope_matches(:org,
                (SELECT b.scope_id FROM accounting_scope_binding b WHERE b.organization_id=r.organization_id AND b.settlement_run_id=r.id),
                CAST(:scope AS uuid),r.management_mandate_id,CAST(:mandate AS uuid)))""")
