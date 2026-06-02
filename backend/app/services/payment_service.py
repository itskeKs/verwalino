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
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.temporal import db_exclusive_to_engine_inclusive
from app.engine.mietkonto import (
    AdvanceFilter,
    LeaseChargeInput,
    MietkontoResult,
    OwnerAdvanceChargeInput,
    PaymentInput,
    account_statement,
    derive_advance_total,
    owner_expected_charges,
    tenant_expected_charges,
)
from app.models.audit_event import AuditEvent
from app.models.payment import Payment
from app.repositories.audit_repo import AuditRepository
from app.repositories.bank_account_repo import BankAccountRepository
from app.repositories.economic_plan_repo import (
    EconomicPlanLineRepository,
    EconomicPlanRepository,
)
from app.repositories.lease_agreement_repo import LeaseAgreementRepository
from app.repositories.payment_repo import PaymentRepository

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

    # ------------------------------------------------------------------
    # create / update draft
    # ------------------------------------------------------------------
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
    ) -> Payment:
        """Create an editable draft payment (m6 section 5; ADR-0009 E6-3).

        property_id/management_mandate_id are MIRRORED from the bank_account
        (ADR-0010) -- never client-chosen. A draft is freely editable; the
        bewusste `book` step freezes it.
        """
        account = await self._accounts.get_by_id(bank_account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="Bank account not found")

        payment = Payment(
            organization_id=self._org_id,
            bank_account_id=account.id,
            property_id=account.property_id,  # mirrored (ADR-0010)
            management_mandate_id=None,  # bank_account has no mandate link in M1
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
        payment = await self._repo.create(payment)
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
        payment = await self._repo.get_by_id(payment_id)
        if payment is None:
            raise HTTPException(status_code=404, detail="Payment not found")
        if payment.status == "booked":
            raise HTTPException(
                status_code=409,
                detail="Cannot edit a booked payment; correct via Storno + Neubuchung",
            )

        # If the bank account changes, re-mirror property_id (ADR-0010).
        if "bank_account_id" in updates and updates["bank_account_id"] is not None:
            account = await self._accounts.get_by_id(updates["bank_account_id"])
            if account is None:
                raise HTTPException(status_code=404, detail="Bank account not found")
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
        """Hard-delete a draft payment. Booked payments are protected (trigger)."""
        payment = await self._repo.get_by_id(payment_id)
        if payment is None:
            raise HTTPException(status_code=404, detail="Payment not found")
        if payment.status == "booked":
            raise HTTPException(
                status_code=409,
                detail="Cannot delete a booked payment (GoBD); reverse it via Storno",
            )
        await self._repo.delete(payment)
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="payment",
                entity_id=payment_id,
                action="payment.deleted",
                payload_diff={"status": "draft"},
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
        payment = await self._repo.get_by_id(payment_id)
        if payment is None:
            raise HTTPException(status_code=404, detail="Payment not found")
        if payment.status != "draft":
            raise HTTPException(
                status_code=409,
                detail=f"Cannot book from status '{payment.status}'",
            )

        now = datetime.now(timezone.utc)
        payment.status = "booked"
        payment.booked_at = now
        payment.booked_by = self._user_id
        payment.retention_until = _compute_retention_until(payment.value_date)
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
    async def account_statement_for_lease(
        self, lease_agreement_id: uuid.UUID, period_start: date, period_end: date
    ) -> MietkontoResult:
        """Soll/Ist/Saldo + offene Posten of one lease over a period (§3.1)."""
        lease = await self._leases.get_by_id(lease_agreement_id)
        if lease is None:
            raise HTTPException(status_code=404, detail="Lease agreement not found")

        lc = LeaseChargeInput(
            lease_agreement_id=lease.id,
            valid_from=lease.valid_from,
            valid_to=db_exclusive_to_engine_inclusive(lease.valid_to),
            base_rent=lease.base_rent,
            advance_payment=lease.advance_payment,
        )
        charges = tenant_expected_charges(lc, period_start, period_end)
        payments = await self._repo.list_for_lease(lease_agreement_id)
        scoped = self._scope_payments_to_period(payments, period_start, period_end)
        return account_statement(charges, [_to_payment_input(p) for p in scoped])

    async def account_statement_for_owner(
        self,
        owner_profile_id: uuid.UUID,
        economic_plan_id: uuid.UUID | None,
        period_start: date,
        period_end: date,
    ) -> MietkontoResult:
        """Hausgeld Soll/Ist/Saldo of one owner over a period (§2.2/§3.1).

        [F6-5] Soll only from a FINALIZED plan (a draft plan is no beschlossenes
        Soll). Without a finalized plan the statement shows only Ist (no Soll
        abgleich) -- the engine does not invent a Soll.
        """
        charges = []
        if economic_plan_id is not None:
            plan = await self._plans.get_by_id(economic_plan_id)
            if plan is None:
                raise HTTPException(status_code=404, detail="Economic plan not found")
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

        payments = await self._repo.list_for_owner(owner_profile_id)
        scoped = self._scope_payments_to_period(payments, period_start, period_end)
        return account_statement(charges, [_to_payment_input(p) for p in scoped])

    @staticmethod
    def _scope_payments_to_period(payments, period_start: date, period_end: date):
        """Keep payments belonging to the statement period (m6 §3.1, "Zeitraum").

        A statement over a period must align Ist to that period (the Soll already
        is). A payment belongs to the period if its erfasster period_year falls in
        [period_start.year, period_end.year] (the period_year is the maßgebliche
        Zuordnung, M6-A8). Payments WITHOUT a period_year fall back to value_date
        within [period_start, period_end]. Payments tagged to other years are
        excluded so the Saldo is not polluted by a different period's Ist.
        """
        y_lo, y_hi = period_start.year, period_end.year
        scoped = []
        for p in payments:
            if p.period_year is not None:
                if y_lo <= p.period_year <= y_hi:
                    scoped.append(p)
            elif period_start <= p.value_date <= period_end:
                scoped.append(p)
        return scoped

    # ------------------------------------------------------------------
    # DERIVED advance totals (the abrechnungs-anbindung, section 6) -- used by
    # the M3/M4 services. Loads the right rows via the repo, sums via the pure
    # engine, so the value is bit-identical to the MANUAL path (I6-E).
    # ------------------------------------------------------------------
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
