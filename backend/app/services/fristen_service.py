"""Fristen service -- assembles the M9 Fristenmonitor from DB state.

Boundary discipline (SDD section 16): this service loads ORM rows, maps them to
the pure ``app.engine.fristen`` inputs, and returns the engine's DeadlineItems.
It never re-implements the date rules (those live in the engine) and the engine
never sees the DB.

Fiscal-year bucketing [section 18/fachlich]: candidate settlement years are
derived from the service periods of a property's confirmed cost entries, bucketed
by the property's ``fiscal_year_start_month`` (1 = calendar year). A cost is
assigned to the fiscal year of its ``service_period_to``. The precise legal year
mapping (stray costs across a year boundary etc.) is a fachlich question; the
monitor's value is the deadline arithmetic, which is exact (§556 III) or clearly
marked provisional (WEG).
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import HTTPException
from app.services.financial_scope_service import FinancialScopeService
from app.core.temporal import db_exclusive_to_engine_inclusive
from app.engine.periods import period_for_date, period_label
from app.engine.dunning import find_overdue_items
from app.engine.fristen import (
    DeadlineItem,
    FiscalYearObligationInput,
    OverdueItemsInput,
    RetentionAnchorInput,
    WirtschaftsplanObligationInput,
    build_fristenmonitor,
)
from app.engine.mietkonto import (
    LeaseChargeInput,
    account_statement,
    tenant_expected_charges,
)
from app.repositories.fristen_repo import FristenRepository
from app.repositories.payment_repo import PaymentRepository
from app.services.dunning_service import _to_payment_input


def _lease_overlaps(lease, period_start: date, period_end: date) -> bool:
    """True iff a lease [valid_from, inclusive_to] intersects [start, end]."""
    if lease.valid_from > period_end:
        return False
    inc_to = db_exclusive_to_engine_inclusive(lease.valid_to)
    if inc_to is not None and inc_to < period_start:
        return False
    return True


def _run_overlaps(run, period_start: date, period_end: date) -> bool:
    return run.period_start <= period_end and run.period_end >= period_start


class FristenService:
    def __init__(self, session, org_id: uuid.UUID, user_id: uuid.UUID | None = None):
        self._session = session
        self._org_id = org_id
        self._user_id = user_id
        self._repo = FristenRepository(session, org_id)
        self._payments = PaymentRepository(session, org_id)

    async def requires_accounting_scope(self) -> bool:
        """Check monitor admission only, without loading financial report rows."""
        sphere = await FinancialScopeService(
            self._session, self._org_id, self._user_id
        ).select_read_scope(None)
        # An inherited database context is never an organization-wide legacy view.
        if sphere is not None:
            return True
        try:
            await self._repo.require_complete_overview()
        except HTTPException as exc:
            if exc.status_code != 409 or exc.detail != (
                'The organization-wide deadline monitor is unavailable for classified financial data; a scoped view is required'
            ):
                raise
            return True
        return False

    async def build_monitor(self, reference_date: date, accounting_scope_id=None) -> list[DeadlineItem]:
        sphere = await FinancialScopeService(self._session, self._org_id, self._user_id).select_read_scope(accounting_scope_id)
        if sphere is None:
            await self._repo.require_complete_overview()
        elif sphere.scope_type not in ('MIET', 'WEG'):
            raise HTTPException(422, 'Deadline monitor does not support this accounting scope type')
        elif reference_date < sphere.valid_from:
            raise HTTPException(422, 'Reference date is before accounting scope')
        properties = await self._repo.list_properties(sphere.property_id if sphere else None)
        fiscal_obligations: list[FiscalYearObligationInput] = []
        wp_obligations: list[WirtschaftsplanObligationInput] = []

        for prop in properties:
            await self._collect_property(prop, fiscal_obligations, wp_obligations, reference_date, sphere)

        overdue = None
        if sphere is None or sphere.scope_type == 'MIET':
            overdue = OverdueItemsInput(overdue_item_count=await self._overdue_count(reference_date, sphere))
        if sphere is None or sphere.scope_type == 'WEG':
            owner_count = await self._owner_overdue_count(reference_date, sphere)
            overdue = OverdueItemsInput(overdue_item_count=(overdue.overdue_item_count if overdue else 0) + owner_count)
        # Upload-first sources have no complete accounting-scope classification
        # yet. Do not turn their RLS invisibility into a zero/partial retention count.
        earliest, doc_count = await self._repo.retention_anchor() if sphere is None else (None, 0)
        retention_anchors = [
            RetentionAnchorInput(
                label="Belege (Rechnungsdokumente)" if sphere is None else f"Belege im Abrechnungskreis {sphere.label}",
                earliest_retention_until=earliest,
                document_count=doc_count,
                coverage_complete=sphere is None,
            )
        ]

        return build_fristenmonitor(
            fiscal_obligations=fiscal_obligations,
            wirtschaftsplan_obligations=wp_obligations,
            overdue=overdue,
            retention_anchors=retention_anchors,
            reference_date=reference_date,
        )

    # ------------------------------------------------------------------
    async def _collect_property(
        self,
        prop,
        fiscal_obligations: list,
        wp_obligations: list,
        reference_date: date,
        sphere=None,
    ) -> None:
        management_type = sphere.scope_type if sphere else prop.management_type
        periods = await self._repo.confirmed_cost_periods(prop.id)
        fy_map: dict[str, tuple[date, date]] = {}
        for _from, _to in periods:
            ps, pe = period_for_date(prop.fiscal_year_start_month, _to)
            label = period_label(ps, pe)
            if sphere and (ps < sphere.valid_from or sphere.valid_to is not None and pe > sphere.valid_to):
                raise HTTPException(409, 'A partial fiscal-year scope requires reviewed deadline coverage before this overview is available')
            fy_map[label] = (ps, pe)

        if fy_map:
            leases = await self._repo.leases_for_property(prop.id)
            bk_runs = await self._repo.finalized_runs(prop.id, "BK_MIETER")
            weg_runs = await self._repo.finalized_runs(prop.id, "WEG_JAHRESABRECHNUNG")
            vb_runs = await self._repo.finalized_runs(prop.id, "VERMOEGENSBERICHT")

            for label, (ps, pe) in fy_map.items():
                overlapping = [l for l in leases if _lease_overlaps(l, ps, pe)]
                has_tenant_lease = len(overlapping) > 0
                # BK done only if EVERY overlapping lease has a finalized BK run
                # overlapping the year (conservative -> reminds rather than misses).
                covered = {
                    r.lease_agreement_id
                    for r in bk_runs
                    if r.lease_agreement_id and _run_overlaps(r, ps, pe)
                    and (sphere is None or r.period_start <= ps and r.period_end >= pe)
                }
                has_finalized_bk = has_tenant_lease and all(
                    l.id in covered for l in overlapping
                )
                fiscal_obligations.append(
                    FiscalYearObligationInput(
                        property_id=prop.id,
                        property_name=prop.name,
                        management_type=management_type,
                        fiscal_year_label=label,
                        period_start=ps,
                        period_end=pe,
                        has_confirmed_costs=True,
                        has_tenant_lease=has_tenant_lease,
                        has_finalized_bk=has_finalized_bk,
                        has_finalized_weg_abrechnung=any(
                            (_run_overlaps(r, ps, pe) if sphere is None else r.period_start <= ps and r.period_end >= pe) for r in weg_runs
                        ),
                        has_finalized_vermoegensbericht=any(
                            (_run_overlaps(r, ps, pe) if sphere is None else r.period_start <= ps and r.period_end >= pe) for r in vb_runs
                        ),
                    )
                )

        if management_type in ("WEG", "MISCH"):
            plan_year = reference_date.year + 1
            if sphere is not None:
                start, end = date(plan_year, 1, 1), date(plan_year, 12, 31)
                if sphere.valid_to is not None and start > sphere.valid_to:
                    return
                if start < sphere.valid_from or sphere.valid_to is not None and end > sphere.valid_to:
                    raise HTTPException(409, 'A partial plan-year scope requires reviewed deadline coverage before this overview is available')
            finalized_years = await self._repo.finalized_plan_years(prop.id)
            wp_obligations.append(
                WirtschaftsplanObligationInput(
                    property_id=prop.id,
                    property_name=prop.name,
                    plan_year=plan_year,
                    has_finalized_plan=(plan_year in finalized_years),
                )
            )

    # ------------------------------------------------------------------
    # Org-wide overdue open-item count (nachrichtlich; reuses the M6 engine).
    # Tenant and owner paths both reuse the ledger's dates and dunning filter.
    # ------------------------------------------------------------------
    async def _overdue_count(self, reference_date: date, sphere=None) -> int:
        leases = await self._repo.all_leases()
        total = 0
        from app.repositories.unit_repo import UnitRepository
        from app.repositories.property_repo import PropertyRepository
        for lease in leases:
            try:
                inc_to = db_exclusive_to_engine_inclusive(lease.valid_to)
                period_start = max(lease.valid_from, sphere.valid_from) if sphere else lease.valid_from
                period_end = min(reference_date, sphere.valid_to) if sphere and sphere.valid_to else reference_date
                if inc_to is not None and inc_to < period_end:
                    period_end = inc_to
                if period_start > period_end:
                    continue
                lc = LeaseChargeInput(
                    lease_agreement_id=lease.id,
                    valid_from=lease.valid_from,
                    valid_to=inc_to,
                    base_rent=lease.base_rent,
                    advance_payment=lease.advance_payment,
                    heating_advance_payment=lease.heating_advance_payment,
                )
                unit = await UnitRepository(self._session, self._org_id).get_by_id(lease.unit_id)
                prop = await PropertyRepository(self._session, self._org_id).get_by_id(unit.property_id) if unit else None
                if prop is None:
                    raise HTTPException(404, "Property not found")
                charges = tenant_expected_charges(lc, period_start, period_end, federal_state=prop.federal_state)
                payments = await self._payments.list_for_lease(lease.id)
                scoped = _scope_payments(payments, period_start, period_end)
                result = account_statement(
                    charges, [_to_payment_input(p) for p in scoped]
                )
                overdue = find_overdue_items(
                    list(result.open_items), reference_date, grace_days=0
                )
                total += len(overdue)
            except HTTPException:
                # Missing access is unknown, never a zero overdue count.
                raise
            except Exception as error:
                # A malformed input cannot be silently omitted from a total.
                raise HTTPException(409, 'Deadline overview cannot calculate all lease inputs') from error
        return total

    async def _owner_overdue_count(self, reference_date: date, sphere=None) -> int:
        from app.services.payment_service import PaymentService

        total = 0
        payments = PaymentService(self._session, self._org_id, self._user_id)
        plans = await self._repo.finalized_plans(reference_date, sphere.property_id if sphere else None)
        period_ends = {}
        for plan in sorted(plans, key=lambda p: (str(p.property_id), p.period_start, p.period_end)):
            previous_end = period_ends.get(plan.property_id)
            if previous_end is not None and plan.period_start <= previous_end:
                raise HTTPException(409, {'code': 'hausgeld_plan_ambiguous',
                    'message': 'Mehrere finalisierte Wirtschaftspläne haben überlappende Zeiträume. Bitte klären Sie den gültigen Plan über eine Korrektur.'})
            period_ends[plan.property_id] = plan.period_end
        for plan in plans:
            for owner in await self._repo.plan_owner_ids(plan):
                result = await payments.account_statement_for_owner(owner, plan.id,
                    plan.period_start, min(reference_date, plan.period_end),
                    sphere.id if sphere else None, property_id=plan.property_id, as_of=reference_date)
                total += len(find_overdue_items(list(result.open_items), reference_date, grace_days=0))
        return total


def _scope_payments(payments, period_start: date, period_end: date):
    """Same period scoping as PaymentService / DunningService."""
    y_lo, y_hi = period_start.year, period_end.year
    scoped = []
    for p in payments:
        if p.period_year is not None:
            if y_lo <= p.period_year <= y_hi:
                scoped.append(p)
        elif period_start <= p.value_date <= period_end:
            scoped.append(p)
    return scoped
