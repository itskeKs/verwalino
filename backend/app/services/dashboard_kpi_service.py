"""Read-only rental KPIs, with assigned-period receipts and per-lease arrears."""
from datetime import date
from decimal import Decimal, localcontext
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.temporal import db_exclusive_to_engine_inclusive
from app.engine.mietkonto import (
    LeaseChargeInput, RentChangeInput, account_statement, tenant_expected_charges,
)
from app.models.lease_agreement import LeaseAgreement
from app.models.payment import Payment
from app.models.tenant_profile import TenantProfile
from app.models.unit import Unit
from app.repositories.lease_rent_change_repo import LeaseRentChangeRepository
from app.repositories.payment_repo import PaymentRepository
from app.services.payment_service import _to_payment_input
from app.services.report_service import Money, ReportService

RENT_TYPES = frozenset({'KALTMIETE', 'NK_VORAUSZAHLUNG'})


class DashboardKpis(BaseModel):
    property_id: UUID
    property_name: str
    year: int
    month: int
    period_start: date
    period_end: date
    accounting_scope_id: UUID | None
    occupancy_date: date
    payment_basis: Literal['assigned_rent_period'] = 'assigned_rent_period'
    expected_rent: Money
    received_rent: Money
    arrears: Money
    arrears_count: int
    unit_count: int
    occupied_units: int
    vacant_units: int
    unassigned_rent_count: int
    unassigned_rent_amount: Money
    footnote: str = (
        'Mietsoll und Zahlungseingänge nach zugeordnetem Mietmonat, nicht Wertstellung; '
        'Rückstände kumuliert seit Vertragsbeginn, Guthaben nur innerhalb desselben '
        'Mietvertrags verrechnet, einschließlich beendeter Verträge. Zahlungen ohne '
        'vollständigen Mietmonat werden separat ausgewiesen. Belegung am Monatsende '
        'auf Basis des aktuellen Einheitenbestands aller Nutzungsarten; '
        'der historische Einheitenbestand ist nicht versioniert.'
    )


class DashboardKpiService:
    def __init__(self, session: AsyncSession, org_id: UUID, user_id: UUID):
        self._session, self._org_id = session, org_id
        self._reports = ReportService(session, org_id, user_id)
        self._payments = PaymentRepository(session, org_id)

    async def monthly(self, property_id: UUID, year: int, month: int,
                      accounting_scope_id: UUID | None = None) -> DashboardKpis:
        if not 1 <= year <= 9998 or not 1 <= month <= 12:
            raise HTTPException(422, 'Year must be between 1 and 9998 and month between 1 and 12')
        start = date(year, month, 1)
        next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        end = date.fromordinal(next_month.toordinal() - 1)
        # Admit the complete universe before enumerating any source rows.
        prop, sphere = await self._reports.property_period(property_id, start, end, accounting_scope_id)
        if prop.management_type not in ('MIET', 'MISCH') or sphere is not None and sphere.scope_type != 'MIET':
            raise HTTPException(422, 'Dashboard rent KPIs require a MIET or mixed property and a MIET accounting scope')
        # Same availability gate as FinancialInputPolicy.require; this derived
        # lease report has no attested settlement/plan subject to persist.
        if not settings.financial_computation_enabled:
            raise HTTPException(503, 'Financial computation is temporarily disabled; existing records remain available')
        scope_id = sphere.id if sphere else None
        units = (await self._session.scalars(select(Unit).where(
            Unit.organization_id == self._org_id, Unit.property_id == property_id,
        ))).all()
        leases = (await self._session.scalars(select(LeaseAgreement).join(Unit,
            (Unit.id == LeaseAgreement.unit_id) & (Unit.organization_id == self._org_id),
        ).where(LeaseAgreement.organization_id == self._org_id,
                Unit.property_id == property_id))).all()
        leases_by_id = {lease.id: lease for lease in leases}
        relevant = [lease for lease in leases if lease.valid_from <= end]
        if sphere is not None and any(lease.valid_from < sphere.valid_from for lease in relevant):
            raise HTTPException(422, 'Accounting scope must cover complete lease history for cumulative arrears')

        # Do not join payments to leases: orphan/incoherent assignments must
        # fail closed, not disappear from the property totals.
        payments = (await self._session.scalars(self._payments._booked_active().where(
            Payment.organization_id == self._org_id, Payment.property_id == property_id,
            Payment.cancelled_at.is_(None), Payment.payment_type.in_(RENT_TYPES),
        ))).all()
        profiles = set((await self._session.scalars(select(TenantProfile.id).where(
            TenantProfile.organization_id == self._org_id,
            TenantProfile.id.in_({lease.tenant_profile_id for lease in leases}),
        ))).all())
        for payment in payments:
            lease = leases_by_id.get(payment.lease_agreement_id)
            if (lease is None or lease.tenant_profile_id not in profiles
                    or payment.tenant_profile_id != lease.tenant_profile_id
                    or payment.owner_profile_id is not None):
                raise HTTPException(409, 'Rent payments require complete coherent property, lease and tenant assignments')
        histories = {}
        history_repo = LeaseRentChangeRepository(self._session, self._org_id)
        for lease in leases:
            if lease.tenant_profile_id not in profiles:
                raise HTTPException(409, 'Rent accounts require complete coherent tenant sources')
            await self._payments.require_computation_context(lease_id=lease.id)
            histories[lease.id] = await history_repo.list_for_lease(lease.id)

        amounts = [p.amount for p in payments]
        for lease in leases:
            amounts.extend((lease.base_rent, lease.advance_payment))
            for change in histories[lease.id]:
                amounts.extend((change.base_rent, change.advance_payment))
        # Include room for the full supported calendar, all rows, rate-days
        # and aggregation; never inherit a caller's low Decimal precision.
        with localcontext() as arithmetic:
            arithmetic.prec = max(28, max((len(a.as_tuple().digits) for a in amounts), default=1)
                                  + len(str(len(amounts) + 1)) + 16)
            result = self._calculate(prop, scope_id, leases, histories, payments, units, start, end)
        # A grant can expire during loading/calculation. Recheck live complete
        # source admission before returning any part of this aggregate.
        await self._reports.property_period(property_id, start, end, scope_id)
        return result

    @staticmethod
    def _calculate(prop, scope_id, leases, histories, payments, units, start, end) -> DashboardKpis:
        zero = Decimal('0.00')
        expected = received = arrears = unassigned_amount = zero
        arrears_count = unassigned_count = 0
        assigned = {lease.id: [] for lease in leases}
        for payment in payments:
            if payment.period_year is None or payment.period_month is None:
                unassigned_count += 1
                unassigned_amount += _to_payment_input(payment).signed_amount
                continue
            if (payment.period_year, payment.period_month) <= (end.year, end.month):
                assigned[payment.lease_agreement_id].append(_to_payment_input(payment))
        for lease in leases:
            lease_input = LeaseChargeInput(
                lease_agreement_id=lease.id, valid_from=lease.valid_from,
                valid_to=db_exclusive_to_engine_inclusive(lease.valid_to),
                base_rent=lease.base_rent, advance_payment=lease.advance_payment,
                rent_changes=tuple(RentChangeInput(
                    valid_from=row.valid_from, base_rent=row.base_rent,
                    advance_payment=row.advance_payment,
                ) for row in histories[lease.id]),
            )
            lease_payments = assigned[lease.id]
            current = account_statement(tenant_expected_charges(lease_input, start, end),
                [p for p in lease_payments if (p.period_year, p.period_month) == (start.year, start.month)])
            expected += current.soll_total
            received += current.ist_total
            if lease.valid_from <= end:
                cumulative = account_statement(
                    tenant_expected_charges(lease_input, lease.valid_from, end),
                    [p for p in lease_payments if (p.period_year, p.period_month)
                     >= (lease.valid_from.year, lease.valid_from.month)])
                if cumulative.balance > zero:
                    arrears += cumulative.balance
                    arrears_count += 1
        unit_ids = {unit.id for unit in units}
        occupied = {lease.unit_id for lease in leases if lease.unit_id in unit_ids
                    and lease.valid_from <= end and (lease.valid_to is None or lease.valid_to > end)}
        return DashboardKpis(
            property_id=prop.id, property_name=prop.name, year=start.year, month=start.month,
            period_start=start, period_end=end, occupancy_date=end, accounting_scope_id=scope_id,
            expected_rent=format(expected, '.2f'), received_rent=format(received, '.2f'),
            arrears=format(arrears, '.2f'), arrears_count=arrears_count,
            unit_count=len(unit_ids), occupied_units=len(occupied), vacant_units=len(unit_ids - occupied),
            unassigned_rent_count=unassigned_count, unassigned_rent_amount=format(unassigned_amount, '.2f'),
        )
