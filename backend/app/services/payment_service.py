"""Payment service -- create draft / book (atomic) / reverse / Mietkonto views.

m6-zahlungen-mietkonto.md section 8.3; ADR-0009 (Storno statt Edit), ADR-0010.

Boundary discipline (SDD section 16): the service loads ORM rows, maps them to
the engine's frozen dataclasses (app.engine.mietkonto), calls the PURE engine,
and persists / returns. The engine never sees the DB; the service never
re-implements Soll/Ist/Saldo math.

Lifecycle (mirrors cost_entry_service):
  create_draft -> editable draft.
  update_draft -> only while draft.
  book         -> draft -> booked (atomic; sets booked_at/by, retention_until,
                  audit). Immutable from commit (ADR-0003 trigger, 0018).
  reverse_payment -> Storno: a NEW booked OUT/IN row with reverses_id -> original
                  + set the original's reversed_by_id (the SINGLE allowed mutation
                  of a booked row). Correction = Storno + a fresh Neubuchung.

Vermoegens-Kohaerenz (ADR-0010): property_id/management_mandate_id are MIRRORED
from the chosen bank_account (not client-chosen). The Composite-FK enforces it
DB-hard; the service sets the consistent values so a normal write succeeds.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from app.core.rbac import ScopeCapability
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.temporal import db_exclusive_to_engine_inclusive
from app.engine.mietkonto import (
    AdvanceFilter,
    AdvanceCredit,
    LeaseChargeInput,
    RentChangeInput,
    MietkontoResult,
    OwnerAdvanceChargeInput,
    PaymentInput,
    account_statement,
    derive_advance_total,
    derive_advance_total_for_period,
    derive_advance_credits_for_period,
    uncredited_advance_total,
    deposit_statement,
    owner_expected_charges,
    special_levy_expected_charges,
    tenant_expected_charges,
)
from app.models.audit_event import AuditEvent
from app.services.financial_scope_service import FinancialScopeService
from app.models.payment import Payment
from app.services.special_levy_service import SpecialLevyService
from app.repositories.audit_repo import AuditRepository
from app.repositories.bank_account_repo import BankAccountRepository
from app.repositories.economic_plan_repo import (
    EconomicPlanLineRepository,
    EconomicPlanRepository,
)
from app.repositories.lease_agreement_repo import LeaseAgreementRepository
from app.repositories.lease_rent_change_repo import LeaseRentChangeRepository
from app.repositories.payment_repo import PaymentRepository
from app.repositories.person_repo import PersonRepository
from app.repositories.property_repo import PropertyRepository
from app.repositories.tenant_profile_repo import TenantProfileRepository
from app.repositories.owner_profile_repo import OwnerProfileRepository
from app.repositories.settlement_repo import SettlementRunRepository
from app.repositories.unit_repo import UnitRepository

# Tenant-bound vs owner-bound vocabulary (kept in sync with the DB CHECK in 0016
# and the engine vocabulary). The service uses it for the OUT-direction of a
# Storno and for input validation messages.
TENANT_TYPES = frozenset({"KALTMIETE", "NK_VORAUSZAHLUNG", "KAUTION"})
OWNER_TYPES = frozenset({"HAUSGELD_VORSCHUSS", "RUECKLAGENANTEIL", "SONDERZAHLUNG"})


def _compute_retention_until(value_date: date) -> date:
    """Aufbewahrungs-Anker for a booking record (m6 section 5; ADR-0003 #3).

    PROVISIONALLY: end of the value_date's calendar year + 8 years (the same
    provisional rule as cost_entry_service / invoice_document, O-1). The correct
    legal start per record type is to be confirmed by tax advisor before M9.
    """
    year_end = date(value_date.year, 12, 31)
    return year_end.replace(year=year_end.year + 8)


def _to_payment_input(p: Payment) -> PaymentInput:
    """Map a Payment ORM row to the pure engine's frozen PaymentInput."""
    return PaymentInput(
        payment_id=p.id,
        payment_type=p.payment_type,
        direction=p.direction,
        amount=p.amount,
        period_year=p.period_year,
        period_month=p.period_month,
        lease_agreement_id=p.lease_agreement_id,
        tenant_profile_id=p.tenant_profile_id,
        owner_profile_id=p.owner_profile_id,
        is_booked=(p.status == "booked"),
        is_reversed=(p.reversed_by_id is not None),
        is_reversal=(p.reverses_id is not None),
    )


class PaymentService:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID):
        self._session = session
        self._org_id = org_id
        self._user_id = user_id
        self._repo = PaymentRepository(session, org_id)
        self._accounts = BankAccountRepository(session, org_id)
        self._leases = LeaseAgreementRepository(session, org_id)
        self._plans = EconomicPlanRepository(session, org_id)
        self._plan_lines = EconomicPlanLineRepository(session, org_id)
        self._audit = AuditRepository(session, org_id)

    async def _require_deposit_sources(self, payment: Payment) -> None:
        """A deposit must be attributable to its visible contract, tenant and account."""
        if payment.payment_type != "KAUTION":
            return
        if payment.lease_agreement_id is None:
            raise HTTPException(422, "Deposit payments require a lease agreement")
        with self._session.no_autoflush:
            account = await self._accounts.get_by_id(payment.bank_account_id)
            if account is None:
                raise HTTPException(404, "Bank account not found")
            await FinancialScopeService(self._session, self._org_id, self._user_id).enter(
                account, capability=ScopeCapability.READ)
            lease = await self._leases.get_by_id(payment.lease_agreement_id)
            tenant = await TenantProfileRepository(self._session, self._org_id).get_by_id(payment.tenant_profile_id)
            if lease is None or tenant is None:
                raise HTTPException(404, "Deposit sources not accessible")
            unit = await UnitRepository(self._session, self._org_id).get_by_id(lease.unit_id)
            if unit is None:
                raise HTTPException(404, "Deposit sources not accessible")
            if unit.property_id != account.property_id or lease.tenant_profile_id != tenant.id:
                raise HTTPException(422, "Deposit lease, tenant and account must belong together")

    # ------------------------------------------------------------------
    # create / update draft
    # ------------------------------------------------------------------
    async def _resolve_parents(self, links):
        parents = {}
        for field, repository, detail in (
            ("bank_account_id", BankAccountRepository, "Bank account not found"),
            ("tenant_profile_id", TenantProfileRepository, "Tenant profile not found"),
            ("owner_profile_id", OwnerProfileRepository, "Owner profile not found"),
            ("lease_agreement_id", LeaseAgreementRepository, "Lease agreement not found"),
            ("settlement_run_id", SettlementRunRepository, "Settlement run not found"),
        ):
            if links.get(field) is not None:
                parent = await repository(self._session, self._org_id).get_by_id(links[field])
                if parent is None:
                    raise HTTPException(404, detail)
                parents[field] = parent
        return parents

    async def create_draft(
        self,
        *,
        bank_account_id: uuid.UUID,
        direction: str,
        amount: Decimal,
        value_date: date,
        payment_type: str,
        tenant_profile_id: uuid.UUID | None = None,
        owner_profile_id: uuid.UUID | None = None,
        lease_agreement_id: uuid.UUID | None = None,
        period_year: int | None = None,
        period_month: int | None = None,
        settlement_run_id: uuid.UUID | None = None,
        note: str | None = None,
        bank_transaction_id: uuid.UUID | None = None,
    ) -> Payment:
        """Create an editable draft payment (m6 section 5; ADR-0009 E6-3).

        property_id/management_mandate_id are MIRRORED from the bank_account
        (ADR-0010) -- never client-chosen. A draft is freely editable; the
        bewusste `book` step freezes it.
        """
        await FinancialScopeService(self._session, self._org_id, self._user_id).lock_inputs()
        parents = await self._resolve_parents({
            "bank_account_id": bank_account_id, "tenant_profile_id": tenant_profile_id,
            "owner_profile_id": owner_profile_id, "lease_agreement_id": lease_agreement_id,
            "settlement_run_id": settlement_run_id,
        })
        account = parents["bank_account_id"]

        if not account.is_active:
            raise HTTPException(409, "Bank account is inactive; use an active account for new payments")

        payment = Payment(
            organization_id=self._org_id,
            bank_account_id=account.id,
            bank_transaction_id=bank_transaction_id,
            property_id=account.property_id,  # mirrored (ADR-0010)
            management_mandate_id=None,  # DB derives this from the canonical account scope when bound
            direction=direction,
            amount=amount,
            value_date=value_date,
            payment_type=payment_type,
            tenant_profile_id=tenant_profile_id,
            owner_profile_id=owner_profile_id,
            lease_agreement_id=lease_agreement_id,
            period_year=period_year,
            period_month=period_month,
            settlement_run_id=settlement_run_id,
            status="draft",
            note=note,
        )
        await self._require_deposit_sources(payment)
        payment = await self._repo.create(payment)
        await self._session.refresh(payment, ["management_mandate_id"])
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="payment",
                entity_id=payment.id,
                action="payment.created",
                payload_diff={
                    "payment_type": payment_type,
                    "direction": direction,
                    "amount": str(amount),
                    "bank_account_id": str(account.id),
                    "status": "draft",
                },
            )
        )
        return payment

    async def update_draft(self, payment_id: uuid.UUID, updates: dict) -> Payment:
        """Update a draft payment. Booked payments cannot be edited (ADR-0009)."""
        await FinancialScopeService(self._session, self._org_id, self._user_id).lock_inputs()
        payment = await self._repo.get_by_id(payment_id)
        if payment is None:
            raise HTTPException(status_code=404, detail="Payment not found")
        parents = await self._resolve_parents(updates)
        if payment.status == "booked" or payment.cancelled_at is not None:
            raise HTTPException(
                status_code=409,
                detail="Cannot edit a booked payment; correct via Storno + Neubuchung",
            )

        # If the bank account changes, re-mirror property_id (ADR-0010).
        if "bank_account_id" in updates and updates["bank_account_id"] is not None:
            if updates["bank_account_id"] != payment.bank_account_id and await self._repo.is_scope_bound(payment.id):
                raise HTTPException(409, "Discard the scoped draft and create a replacement to change its account")
            account = parents["bank_account_id"]
            if account.id != payment.bank_account_id and not account.is_active:
                raise HTTPException(409, "Bank account is inactive; use an active account")
            payment.bank_account_id = account.id
            payment.property_id = account.property_id

        changed: dict = {}
        for field_name, value in updates.items():
            if field_name in ("bank_account_id", "property_id"):
                continue  # handled above / not directly settable
            if hasattr(payment, field_name):
                old_val = getattr(payment, field_name)
                setattr(payment, field_name, value)
                changed[field_name] = {"old": str(old_val), "new": str(value)}

        await self._require_deposit_sources(payment)
        payment = await self._repo.update(payment)
        if changed:
            await self._audit.create(
                AuditEvent(
                    actor_user_id=self._user_id,
                    entity_type="payment",
                    entity_id=payment.id,
                    action="payment.updated",
                    payload_diff=changed,
                )
            )
        return payment

    async def delete_draft(self, payment_id: uuid.UUID) -> None:
        """Discard a scope-bound draft; preserve its canonical evidence. Legacy drafts may be deleted."""
        await FinancialScopeService(self._session, self._org_id, self._user_id).lock_inputs()
        payment = await self._repo.get_by_id(payment_id)
        if payment is None:
            raise HTTPException(status_code=404, detail="Payment not found")
        if payment.status == "booked":
            raise HTTPException(
                status_code=409,
                detail="Cannot delete a booked payment (GoBD); reverse it via Storno",
            )
        if payment.cancelled_at is not None:
            return
        bound = await self._repo.is_scope_bound(payment.id)
        if bound:
            payment.cancelled_at = datetime.now(timezone.utc)
            await self._repo.update(payment)
        else:
            await self._repo.delete(payment)
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="payment",
                entity_id=payment_id,
                action="payment.discarded" if bound else "payment.deleted",
                payload_diff={"status": "cancelled" if bound else "draft"},
            )
        )

    # ------------------------------------------------------------------
    # book (atomic, freeze)
    # ------------------------------------------------------------------
    async def book(self, payment_id: uuid.UUID) -> Payment:
        """Book a draft payment -- atomic freeze (ADR-0009 / section 5).

        Sets status='booked', booked_at/by, retention_until + audit, all in the
        active transaction. The ADR-0003 trigger (0018) freezes it from commit.
        """
        await FinancialScopeService(self._session, self._org_id, self._user_id).lock_inputs()
        payment = await self._repo.get_by_id(payment_id)
        if payment is None:
            raise HTTPException(status_code=404, detail="Payment not found")
        if payment.status != "draft" or payment.cancelled_at is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot book from status '{payment.status}'",
            )

        now = datetime.now(timezone.utc)
        payment.status = "booked"
        payment.booked_at = now
        payment.booked_by = self._user_id
        payment.retention_until = _compute_retention_until(payment.value_date)
        await self._require_deposit_sources(payment)
        payment = await self._repo.update(payment)

        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="payment",
                entity_id=payment.id,
                action="payment.booked",
                payload_diff={
                    "booked_by": str(self._user_id),
                    "amount": str(payment.amount),
                    "payment_type": payment.payment_type,
                    "retention_until": str(payment.retention_until),
                },
            )
        )
        return payment

    # ------------------------------------------------------------------
    # reverse (Storno + Neubuchung is two steps; this is the Storno)
    # ------------------------------------------------------------------
    async def reverse_payment(self, payment_id: uuid.UUID, note: str | None = None) -> Payment:
        """Storno a booked payment (m6 section 5; ADR-0009).

        Creates a NEW booked row with the OPPOSITE direction, reverses_id ->
        original, and sets the original's reversed_by_id (the SINGLE allowed
        mutation of a booked row, analogous to create_correction_run). The
        corrected Neubuchung, if any, is a separate create_draft + book.
        """
        await FinancialScopeService(self._session, self._org_id, self._user_id).lock_inputs()
        original = await self._repo.get_by_id(payment_id)
        if original is None:
            raise HTTPException(status_code=404, detail="Payment not found")
        if original.status != "booked":
            raise HTTPException(
                status_code=409,
                detail="Only booked payments are reversed via Storno; delete a draft instead",
            )
        if original.reversed_by_id is not None:
            raise HTTPException(status_code=409, detail="This payment has already been reversed")
        if original.reverses_id is not None:
            raise HTTPException(
                status_code=409, detail="A Storno row cannot itself be reversed"
            )

        now = datetime.now(timezone.utc)
        opposite = "OUT" if original.direction == "IN" else "IN"
        storno = Payment(
            organization_id=self._org_id,
            bank_account_id=original.bank_account_id,
            property_id=original.property_id,
            management_mandate_id=original.management_mandate_id,
            direction=opposite,
            amount=original.amount,
            value_date=original.value_date,
            payment_type=original.payment_type,
            tenant_profile_id=original.tenant_profile_id,
            owner_profile_id=original.owner_profile_id,
            lease_agreement_id=original.lease_agreement_id,
            period_year=original.period_year,
            period_month=original.period_month,
            settlement_run_id=original.settlement_run_id,
            status="booked",
            booked_at=now,
            booked_by=self._user_id,
            retention_until=_compute_retention_until(original.value_date),
            reverses_id=original.id,
            note=note or f"Storno zu {original.id}",
        )
        storno = await self._repo.create(storno)
        await self._session.refresh(storno, ["management_mandate_id"])

        # The single allowed mutation on the booked original.
        original.reversed_by_id = storno.id
        await self._repo.update(original)

        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="payment",
                entity_id=original.id,
                action="payment.reversed",
                payload_diff={"reversed_by_id": str(storno.id)},
            )
        )
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="payment",
                entity_id=storno.id,
                action="payment.created",
                payload_diff={
                    "reverses_id": str(original.id),
                    "direction": opposite,
                    "amount": str(original.amount),
                    "status": "booked",
                },
            )
        )
        return storno

    # ------------------------------------------------------------------
    # Mietkonto views (read-only, pure-engine-backed)
    # ------------------------------------------------------------------
    async def deposits(self, accounting_scope_id: uuid.UUID | None = None) -> list[dict]:
        """Complete deposit view in one rental source context, without date/account filters."""
        sphere = await FinancialScopeService(self._session, self._org_id, self._user_id).select_read_scope(accounting_scope_id)
        if sphere is not None and sphere.scope_type != "MIET":
            raise HTTPException(422, "Accounting scope does not support this report type")
        leases = await self._leases.list_all()
        lease_ids = {lease.id for lease in leases}
        deposit_rows = await self._repo.list_by_filters(payment_type="KAUTION", status="booked")
        if any(p.reversed_by_id is None and p.reverses_id is None
               and p.lease_agreement_id not in lease_ids for p in deposit_rows):
            raise HTTPException(409, "Deposit payments require complete coherent lease sources")
        rows = []
        for lease in sorted(leases, key=lambda item: str(item.id)):
            payments = await self._repo.list_for_lease(lease.id)
            result = deposit_statement(lease.deposit or Decimal("0.00"),
                                       [_to_payment_input(p) for p in payments])
            if result.expected_amount == 0 and result.paid_amount == 0 and result.refunded_amount == 0:
                continue
            unit = await UnitRepository(self._session, self._org_id).get_by_id(lease.unit_id)
            profile = await TenantProfileRepository(self._session, self._org_id).get_by_id(lease.tenant_profile_id)
            if unit is None or profile is None:
                raise HTTPException(404, "Complete deposit sources not accessible")
            if any(p.payment_type == "KAUTION" and p.status == "booked"
                   and p.reversed_by_id is None and p.reverses_id is None
                   and (p.property_id != unit.property_id or p.tenant_profile_id != lease.tenant_profile_id)
                   for p in payments):
                raise HTTPException(409, "Deposit payments require complete coherent lease sources")
            prop = await PropertyRepository(self._session, self._org_id).get_by_id(unit.property_id)
            person = await PersonRepository(self._session, self._org_id).get_by_id(profile.person_id)
            if prop is None or person is None:
                raise HTTPException(404, "Complete deposit sources not accessible")
            rows.append({
                "lease_agreement_id": str(lease.id),
                "tenant_profile_id": str(lease.tenant_profile_id),
                "unit_id": str(unit.id), "property_id": str(prop.id),
                "tenant_name": person.company_name or " ".join(
                    part for part in (person.first_name, person.last_name) if part),
                "unit_label": unit.number, "property_name": prop.name,
                "expected_amount": result.expected_amount, "paid_amount": result.paid_amount,
                "refunded_amount": result.refunded_amount, "held_amount": result.held_amount,
            })
        return rows

    async def account_statement_for_lease(
        self, lease_agreement_id: uuid.UUID, period_start: date, period_end: date,
        accounting_scope_id: uuid.UUID | None = None,
    ) -> MietkontoResult:
        """Soll/Ist/Saldo + offene Posten of one lease over a period (§3.1)."""
        await FinancialScopeService(self._session, self._org_id, self._user_id).select_report_scope(
            accounting_scope_id, scope_type='MIET', period_start=period_start, period_end=period_end)
        lease = await self._leases.get_by_id(lease_agreement_id)
        if lease is None:
            raise HTTPException(status_code=404, detail="Lease agreement not found")

        rent_changes = await LeaseRentChangeRepository(self._session, self._org_id).list_for_lease(lease.id)
        lc = LeaseChargeInput(
            lease_agreement_id=lease.id,
            valid_from=lease.valid_from,
            valid_to=db_exclusive_to_engine_inclusive(lease.valid_to),
            base_rent=lease.base_rent,
            advance_payment=lease.advance_payment,
            rent_changes=tuple(RentChangeInput(
                valid_from=row.valid_from, base_rent=row.base_rent, advance_payment=row.advance_payment,
            ) for row in rent_changes),
        )
        charges = tenant_expected_charges(lc, period_start, period_end)
        payments = await self._repo.list_for_lease(lease_agreement_id)
        scoped = self._scope_payments_to_period(payments, period_start, period_end)
        return account_statement(charges, [_to_payment_input(p) for p in scoped
                                           if p.payment_type != "KAUTION"])

    async def account_statement_for_owner(
        self,
        owner_profile_id: uuid.UUID,
        economic_plan_id: uuid.UUID | None,
        period_start: date,
        period_end: date,
        accounting_scope_id: uuid.UUID | None = None,
        *, property_id: uuid.UUID | None = None,
    ) -> MietkontoResult:
        """Hausgeld Soll/Ist/Saldo of one owner over a period (§2.2/§3.1).

        [F6-5] Soll only from a FINALIZED plan (a draft plan is no beschlossenes
        Soll). Frozen special levies add one-off SONDERZAHLUNG obligations in
        their due month, independently of whether an economic plan exists.
        """
        context = FinancialScopeService(self._session, self._org_id, self._user_id)
        if economic_plan_id is not None and accounting_scope_id is None:
            inherited_plan = await self._plans.get_by_id(economic_plan_id)
            if inherited_plan is None:
                raise HTTPException(404, 'Economic plan not found')
            await context.enter(inherited_plan, computation=True)
        sphere = await context.select_report_scope(accounting_scope_id,
            scope_type='WEG', period_start=period_start, period_end=period_end)
        if not await self._session.scalar(text("SELECT property_source_security_mode(:org)='PREPARATION' OR profile_source_access(:org,'owner_profile',:profile,'read')"),
                {'org': self._org_id, 'profile': owner_profile_id}):
            raise HTTPException(404, 'Owner profile not accessible')
        charges = []
        if economic_plan_id is not None:
            plan = await self._plans.get_by_id(economic_plan_id)
            if plan is None:
                raise HTTPException(status_code=404, detail="Economic plan not found")
            await context.enter(plan, computation=True)
            sphere = await context.select_report_scope(accounting_scope_id,
                scope_type='WEG', period_start=period_start, period_end=period_end)
            if sphere is not None and (period_start < plan.period_start or period_end > plan.period_end):
                raise HTTPException(422, 'Report period is outside economic plan')
            if plan.status == "finalized":
                lines = await self._plan_lines.list_for_plan(economic_plan_id)
                advance_lines = [
                    OwnerAdvanceChargeInput(
                        owner_profile_id=ln.owner_profile_id,
                        unit_id=ln.unit_id,
                        monthly_amount=ln.monthly_amount,
                    )
                    for ln in lines
                    if ln.line_type == "OWNER_ADVANCE"
                    and ln.owner_profile_id is not None
                    and ln.monthly_amount is not None
                ]
                charges = owner_expected_charges(
                    advance_lines, owner_profile_id, period_start, period_end
                )

        if sphere is not None and property_id is not None and sphere.property_id != property_id:
            raise HTTPException(422, 'Property differs from accounting scope')
        allocations = await SpecialLevyService(self._session, self._org_id, self._user_id).owner_allocations(
            owner_profile_id, period_start, period_end, sphere.id if sphere is not None else None, property_id)
        charges.extend(special_levy_expected_charges(
            [(due, allocation.amount) for allocation, due in allocations], owner_profile_id, period_start, period_end))

        payments = await self._repo.list_for_owner(owner_profile_id, property_id=property_id)
        scoped = self._scope_payments_to_period(payments, period_start, period_end)
        return account_statement(charges, [_to_payment_input(p) for p in scoped])

    @staticmethod
    def _scope_payments_to_period(payments, period_start: date, period_end: date):
        """Keep payments belonging to the statement period (m6 §3.1, "Zeitraum").

        A statement over a period must align Ist to that period (the Soll already
        is). A payment belongs to the period if its erfasster period_year falls in
        [period_start.year, period_end.year]. If period_month is present, match
        the exact year/month range (Zuordnung, M6-A8), not the value date.
        Payments WITHOUT a period_year fall back to value_date
        within [period_start, period_end]. Payments tagged to other years are
        excluded so the Saldo is not polluted by a different period's Ist.
        """
        y_lo, y_hi = period_start.year, period_end.year
        scoped = []
        for p in payments:
            if p.period_year is not None:
                if p.period_month is not None:
                    if (period_start.year, period_start.month) <= (p.period_year, p.period_month) <= (period_end.year, period_end.month):
                        scoped.append(p)
                elif y_lo <= p.period_year <= y_hi:
                    scoped.append(p)
            elif period_start <= p.value_date <= period_end:
                scoped.append(p)
        return scoped

    # ------------------------------------------------------------------
    # DERIVED advance totals (the abrechnungs-anbindung, section 6) -- used by
    # the M3/M4 services. Loads the right rows via the repo, sums via the pure
    # engine, so the value is bit-identical to the MANUAL path (I6-E).
    # ------------------------------------------------------------------
    @staticmethod
    def _nk_credit_buckets_change(lease, inputs, start, end, old_end, new_end):
        def buckets(lease_end):
            return tuple(credit
                for year in range(start.year, end.year + 1)
                for credit in derive_advance_credits_for_period(inputs, AdvanceFilter(
                    frozenset({"NK_VORAUSZAHLUNG"}), year, lease.id), start, end,
                    lease_start=lease.valid_from, lease_end=lease_end))

        return buckets(old_end) != buckets(new_end)

    async def lease_end_conflicts_finalized_credit(self, lease_id: uuid.UUID, last_day: date) -> bool:
        """Protect only derived buckets changed by an end strictly inside their span."""
        runs = await self._leases.finalized_derived_statements_containing(lease_id, last_day)
        if not runs:
            return False
        lease, inputs = await self._nk_advance_inputs(lease_id)
        old_end = db_exclusive_to_engine_inclusive(lease.valid_to)
        return any(self._nk_credit_buckets_change(lease, inputs, run.period_start, run.period_end,
            old_end, last_day) for run in runs)

    async def derive_nk_advance_total(
        self, lease_agreement_id: uuid.UUID, period_year: int
    ) -> Decimal:
        """DERIVED NK-Vorauszahlung sum for the BK abrechnung (§6.2)."""
        rows = await self._repo.derived_for_nk(lease_agreement_id, period_year)
        filt = AdvanceFilter(
            types=frozenset({"NK_VORAUSZAHLUNG"}),
            year=period_year,
            lease_agreement_id=lease_agreement_id,
        )
        return derive_advance_total([_to_payment_input(p) for p in rows], filt)

    async def derive_nk_advance_total_for_period(
        self, lease_agreement_id: uuid.UUID, period_start: date, period_end: date
    ) -> Decimal:
        """DERIVED NK total with earlier snapshot credits retained after edits."""
        credits, _excluded = await self.nk_advance_credits_for_period(lease_agreement_id, period_start, period_end)
        return sum((credit.amount for credit in credits), Decimal("0.00"))

    async def nk_advance_credits_for_period(
        self, lease_agreement_id: uuid.UUID, period_start: date, period_end: date,
    ) -> tuple[tuple[AdvanceCredit, ...], Decimal]:
        """Return auditable year/month buckets plus wholly outside-lease bookings."""
        from app.repositories.settlement_repo import SettlementRunRepository

        lease, inputs = await self._nk_advance_inputs(lease_agreement_id)
        lease_end = db_exclusive_to_engine_inclusive(lease.valid_to)
        finalized = await SettlementRunRepository(self._session, self._org_id).finalized_bk_for_advance_years(
            lease_agreement_id, period_start, period_end)
        frozen, seen = [], set()
        for run in finalized:
            payload = run.snapshot_payload or {}
            period = payload.get("period", {})
            bounds = (period.get("start"), period.get("end"))
            if bounds in seen:
                continue
            seen.add(bounds)
            data = payload.get("inputs", {})
            if data.get("advance_payments_source") != "DERIVED":
                continue
            buckets = data.get("advance_payment_credits")
            if buckets is None:
                old_lease = next((row for row in data.get("lease_periods", [])
                    if row["lease_agreement_id"] == str(lease_agreement_id)), {})
                old_end = date.fromisoformat(old_lease["to"]) if old_lease.get("to") else None
                if (old_end != lease_end and Decimal(payload["result"]["advance_payments_total"])
                        and self._nk_credit_buckets_change(lease, inputs,
                            date.fromisoformat(bounds[0]), date.fromisoformat(bounds[1]), old_end, lease_end)):
                    raise HTTPException(409, {"code": "advance_credit_history_missing",
                        "message": "Der eingefrorene Vorauszahlungsnachweis fehlt. Bitte die frühere Abrechnung korrigieren."})
                continue
            frozen.extend(AdvanceCredit(row["year"], row["month"], date.fromisoformat(bounds[0]),
                date.fromisoformat(bounds[1]), Decimal(row["amount"])) for row in buckets)
        credits = tuple(credit
            for year in range(period_start.year, period_end.year + 1)
            for credit in derive_advance_credits_for_period(inputs, AdvanceFilter(
                types=frozenset({"NK_VORAUSZAHLUNG"}),
                year=year,
                lease_agreement_id=lease_agreement_id,
            ), period_start, period_end, lease_start=lease.valid_from,
                lease_end=lease_end, finalized_credits=tuple(frozen)))
        return credits, self._uncredited_nk_total(lease, inputs, period_start, period_end)

    async def _nk_advance_inputs(self, lease_agreement_id):
        lease = await self._leases.get_by_id(lease_agreement_id)
        if lease is None:
            raise HTTPException(404, "Lease agreement not found")
        rows = await self._repo.derived_for_nk_for_lease(lease_agreement_id)
        return lease, [_to_payment_input(p) for p in rows]

    @staticmethod
    def _uncredited_nk_total(lease, inputs, period_start, period_end):
        return sum((uncredited_advance_total(inputs, AdvanceFilter(
            frozenset({"NK_VORAUSZAHLUNG"}), year, lease.id), lease.valid_from,
            db_exclusive_to_engine_inclusive(lease.valid_to), period_start=period_start, period_end=period_end)
            for year in range(period_start.year, period_end.year + 1)), Decimal("0.00"))

    async def uncredited_nk_advance_total(
        self, lease_agreement_id: uuid.UUID, period_start: date, period_end: date,
    ) -> Decimal:
        """Notice-only derivation; manual statement totals need no frozen history."""
        lease, inputs = await self._nk_advance_inputs(lease_agreement_id)
        return self._uncredited_nk_total(lease, inputs, period_start, period_end)

    async def derive_hausgeld_total(
        self, owner_profile_id: uuid.UUID, period_year: int
    ) -> Decimal:
        """DERIVED Hausgeld-Vorschuss sum (nachrichtlich, NOT the Spitze) (§6.3)."""
        rows = await self._repo.derived_for_hausgeld(owner_profile_id, period_year)
        filt = AdvanceFilter(
            types=frozenset({"HAUSGELD_VORSCHUSS"}),
            year=period_year,
            owner_profile_id=owner_profile_id,
        )
        return derive_advance_total([_to_payment_input(p) for p in rows], filt)

    async def derive_hausgeld_total_for_period(
        self, owner_profile_id: uuid.UUID, period_start: date, period_end: date
    ) -> Decimal:
        """Paid Hausgeld with the same month/day proration as NK advances."""
        rows = []
        for year in range(period_start.year, period_end.year + 1):
            rows.extend(await self._repo.derived_for_hausgeld(owner_profile_id, year))
        inputs = [_to_payment_input(p) for p in rows]
        return sum((derive_advance_total_for_period(inputs, AdvanceFilter(
            types=frozenset({"HAUSGELD_VORSCHUSS"}), year=year,
            owner_profile_id=owner_profile_id,
        ), period_start, period_end) for year in range(period_start.year, period_end.year + 1)), Decimal("0.00"))
