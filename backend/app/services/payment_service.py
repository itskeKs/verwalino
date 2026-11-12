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
from calendar import monthrange
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from app.core.rbac import ScopeCapability
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.temporal import db_exclusive_to_engine_inclusive
from app.engine.types import OwnerPeriodInput
from app.engine.reserve_ledger import covered_reserve
from app.engine.rounding import distribute_amount
from app.engine.mietkonto import (
    AdvanceFilter,
    AdvanceCredit,
    advance_modes_for_period,
    ExpectedCharge,
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
    tenant_refund_buckets,
)
from app.models.audit_event import AuditEvent
from app.models.economic_plan import EconomicPlan
from app.services.financial_scope_service import FinancialScopeService
from app.models.payment import Payment
from app.services.special_levy_service import SpecialLevyService
from app.repositories.cost_entry_repo import CostEntryRepository
from app.repositories.register_entry_repo import RegisterEntryRepository
from app.repositories.audit_repo import AuditRepository
from app.repositories.bank_account_repo import BankAccountRepository
from app.repositories.bank_transaction_repo import BankTransactionRepository
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
TENANT_TYPES = frozenset({"KALTMIETE", "NK_VORAUSZAHLUNG", "HK_VORAUSZAHLUNG", "KAUTION"})
OWNER_TYPES = frozenset({"HAUSGELD_VORSCHUSS", "RUECKLAGENANTEIL", "SONDERZAHLUNG"})


def payment_counterparty_error(payment_type: str) -> HTTPException:
    if payment_type in TENANT_TYPES:
        message = "Dieser Zahlungstyp gehört zu einem Mietverhältnis, nicht zu einem Eigentümer."
    elif payment_type in OWNER_TYPES:
        message = "Dieser Zahlungstyp erfordert einen Eigentümer als Gegenpartei."
    else:
        message = "Bitte wählen Sie eine gültige Gegenpartei für diesen Zahlungstyp."
    return HTTPException(422, {"code": "PAYMENT_COUNTERPARTY_TYPE_MISMATCH", "message": message})


def validate_payment_counterparty(
    payment_type: str, tenant_profile_id: uuid.UUID | None, owner_profile_id: uuid.UUID | None,
) -> None:
    if (
        payment_type in TENANT_TYPES and (tenant_profile_id is None or owner_profile_id is not None)
        or payment_type in OWNER_TYPES and (owner_profile_id is None or tenant_profile_id is not None)
    ):
        raise payment_counterparty_error(payment_type)


def _compute_retention_until(value_date: date) -> date:
    """Aufbewahrungs-Anker for a booking record (m6 section 5; ADR-0003 #3).

    PROVISIONALLY: end of the value_date's calendar year + 8 years (the same
    provisional rule as cost_entry_service / invoice_document, O-1). The correct
    legal start per record type is to be confirmed by tax advisor before M9.
    """
    year_end = date(value_date.year, 12, 31)
    return year_end.replace(year=year_end.year + 8)


def to_payment_input(
    p: Payment, *, combined_advance: bool = False, unallocated_advance: bool = False,
) -> PaymentInput:
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
        value_date=p.value_date,
        combined_advance=combined_advance,
        unallocated_advance=unallocated_advance,
    )


def dated_plan_soll(
    shares: dict[tuple[uuid.UUID, uuid.UUID], tuple[Decimal, Decimal]],
) -> dict[uuid.UUID, Decimal]:
    """Per-owner plan Soll: each unit's line amount divided by its dated charges.

    The owners' Soll of a unit always sums to the unit's OWNER_ADVANCE
    planned_amount to the cent (central largest-remainder, remainder exactly
    once); the dated charges decide only the division, so a plan period shorter
    than twelve months keeps its full planned amount. A unit whose dated
    charges are all zero keeps its plan-line amounts (#402). An owner whose
    dated Soll is 0.00 is without Soll — no entry — for the settlement, the
    1(b) reserve-coverage cap and the reserve ledger alike.
    """
    per_owner: dict[uuid.UUID, Decimal] = {}
    by_unit: dict[uuid.UUID, list[tuple[uuid.UUID, Decimal, Decimal]]] = {}
    for (unit_id, owner_id), (line_amount, dated) in shares.items():
        by_unit.setdefault(unit_id, []).append((owner_id, line_amount, dated))
    for rows in by_unit.values():
        rows = sorted(rows, key=lambda row: str(row[0]))
        planned = sum((line_amount for _, line_amount, _ in rows), Decimal('0.00'))
        if any(dated for _, _, dated in rows):
            for (owner_id, _, _), share in zip(rows, distribute_amount(
                    planned, [dated for _, _, dated in rows])):
                per_owner[owner_id] = per_owner.get(owner_id, Decimal('0.00')) + share
        else:
            for owner_id, line_amount, _ in rows:
                per_owner[owner_id] = per_owner.get(owner_id, Decimal('0.00')) + line_amount
    return {owner: amount for owner, amount in per_owner.items() if amount != Decimal('0.00')}


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
        if payment.payment_type == 'RUECKLAGENENTNAHME':
            from app.repositories.accounting_scope_repo import AccountingScopeRepository
            from app.repositories.property_repo import PropertyRepository
            account = await self._accounts.get_by_id(payment.bank_account_id)
            if account is None:
                raise HTTPException(404, {'code': 'RESERVE_FUNDING_SOURCE_NOT_FOUND', 'message': 'Bankkonto nicht gefunden.'})
            scope_id = await FinancialScopeService(self._session, self._org_id, self._user_id).enter(account)
            if scope_id:
                sphere = await AccountingScopeRepository(self._session, self._org_id).get_by_id(scope_id)
                if sphere is None or sphere.scope_type != 'WEG':
                    raise HTTPException(422, {'code': 'RESERVE_PAYOUT_WEG_REQUIRED', 'message': 'Rücklagenentnahmen benötigen einen WEG-Abrechnungskreis.'})
            else:
                prop = await PropertyRepository(self._session, self._org_id).get_by_id(account.property_id)
                if prop is None or prop.management_type not in ('WEG', 'MISCH'):
                    raise HTTPException(422, {'code': 'RESERVE_PAYOUT_WEG_REQUIRED', 'message': 'Rücklagenentnahmen benötigen ein WEG-Objekt.'})
        if payment.payment_type == 'RUECKLAGENENTNAHME' and (
                payment.direction != 'OUT' or payment.tenant_profile_id or payment.owner_profile_id or payment.lease_agreement_id):
            raise HTTPException(422, {'code': 'RESERVE_PAYOUT_DIRECTION_REQUIRED', 'message': 'Eine Rücklagenentnahme ist eine Auszahlung ohne Mieter- oder Eigentümerzuordnung.'})
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

    async def _reserve_evidence(self, payment, *, complete):
        from app.services.reserve_ledger_service import ReserveLedgerService
        if payment.payment_type != 'RUECKLAGENENTNAHME':
            if payment.reserve_cost_entry_id or payment.reserve_register_entry_id:
                raise HTTPException(422, {'code': 'RESERVE_FUNDING_LINKS_INVALID', 'message': 'Diese Belege sind nur für eine Rücklagenentnahme zulässig.'})
            return
        await ReserveLedgerService(self._session, self._org_id, self._user_id).require_evidence(
            payment.property_id, payment.reserve_cost_entry_id, payment.reserve_register_entry_id,
            complete=complete, payment=payment if complete else None)

    # ------------------------------------------------------------------
    # create / update draft
    # ------------------------------------------------------------------
    async def _resolve_parents(self, links):
        parents = {}
        for field, repository, detail in (
            ("reserve_cost_entry_id", CostEntryRepository, {"code": "RESERVE_FUNDING_SOURCE_NOT_FOUND", "message": "Finanzierungsbeleg nicht gefunden."}),
            ("reserve_register_entry_id", RegisterEntryRepository, {"code": "RESERVE_FUNDING_SOURCE_NOT_FOUND", "message": "Finanzierungsbeleg nicht gefunden."}),
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
        reserve_cost_entry_id: uuid.UUID | None = None,
        reserve_register_entry_id: uuid.UUID | None = None,
    ) -> Payment:
        """Create an editable draft payment (m6 section 5; ADR-0009 E6-3).

        property_id/management_mandate_id are MIRRORED from the bank_account
        (ADR-0010) -- never client-chosen. A draft is freely editable; the
        bewusste `book` step freezes it.
        """
        context = FinancialScopeService(self._session, self._org_id, self._user_id)
        await context.lock_inputs()
        parents = await self._resolve_parents({
            "bank_account_id": bank_account_id, "tenant_profile_id": tenant_profile_id,
            "owner_profile_id": owner_profile_id, "lease_agreement_id": lease_agreement_id,
            "settlement_run_id": settlement_run_id, "reserve_cost_entry_id": reserve_cost_entry_id,
            "reserve_register_entry_id": reserve_register_entry_id,
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
            reserve_cost_entry_id=reserve_cost_entry_id,
            reserve_register_entry_id=reserve_register_entry_id,
        )
        with self._session.no_autoflush:
            await self._require_deposit_sources(payment)
            await self._reserve_evidence(payment, complete=False)
        try:
            validate_payment_counterparty(payment_type, tenant_profile_id, owner_profile_id)
        except HTTPException:
            # A scoped INSERT would check prepare access before the CHECK.
            # Preserve that precedence when reporting the error before the write.
            await context.enter(account, capability=ScopeCapability.PREPARE)
            raise
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

        if payment.bank_transaction_id and any(
                field in updates and updates[field] != getattr(payment, field)
                for field in ('amount', 'direction', 'value_date', 'bank_account_id')):
            raise HTTPException(409, {'code': 'RESERVE_IMPORTED_DRAFT_BOUND',
                'message': 'Betrag, Richtung, Wertstellung und Konto folgen dem importierten Bankumsatz. Bitte ergänzen Sie die Belege im Entwurf.'})

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

        with self._session.no_autoflush:
            await self._require_deposit_sources(payment)
            await self._reserve_evidence(payment, complete=False)
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
        if payment.bank_transaction_id:
            if (payment.payment_type != 'RUECKLAGENENTNAHME' or payment.status != 'draft'
                    or payment.cancelled_at is not None or payment.booked_at is not None or payment.booked_by is not None
                    or (payment.reserve_cost_entry_id and payment.reserve_register_entry_id)
                    or await self._repo.has_reserve_entry(payment.id)
                    or await self._repo.has_other_bank_payments(payment)):
                raise HTTPException(409, {'code': 'RESERVE_IMPORTED_DRAFT_BOUND',
                    'message': 'Diese Zuordnung bleibt wegen Buchung, vollständiger Belege oder weiterer verknüpfter Einträge gebunden.'})
            transactions = BankTransactionRepository(self._session, self._org_id)
            transaction = await transactions.get_by_id_for_update(payment.bank_transaction_id)
            if transaction is None:
                raise HTTPException(404, 'Bank transaction not found')
            previous_status = transaction.status
            transaction.status = 'NEU'
            await transactions.update(transaction)
            await self._audit.create(AuditEvent(actor_user_id=self._user_id,
                entity_type='bank_transaction', entity_id=transaction.id,
                action='bank_transaction.released', payload_diff={
                    'payment_id': str(payment.id), 'previous_status': previous_status}))
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

        assigned_modes = None
        if (payment.payment_type in {"NK_VORAUSZAHLUNG", "HK_VORAUSZAHLUNG"}
                and payment.lease_agreement_id is not None and payment.period_year is not None):
            if not 1 <= payment.period_year <= 9999:
                raise HTTPException(422, "Das Zuordnungsjahr muss zwischen 1 und 9999 liegen.")
            lease = await self._leases.get_by_id(payment.lease_agreement_id)
            if lease is None:
                raise HTTPException(404, "Lease agreement not found")
            history = await LeaseRentChangeRepository(self._session, self._org_id).list_for_lease(lease.id)
            modes = self._assigned_advance_modes(lease, history, payment)
            if payment.payment_type == "HK_VORAUSZAHLUNG" and modes == {"COMBINED"}:
                raise HTTPException(422, "Für diesen Zeitraum enthält die Betriebskosten-Vorauszahlung bereits die Heizkosten.")
            if payment.payment_type == "NK_VORAUSZAHLUNG":
                assigned_modes = sorted(mode or "LEGACY" for mode in modes)

        await self._require_deposit_sources(payment)
        await self._reserve_evidence(payment, complete=True)
        reserve_plan_unresolved = False
        if payment.payment_type == 'HAUSGELD_VORSCHUSS':
            from app.repositories.reserve_ledger_repo import ReserveLedgerRepository
            await FinancialScopeService(self._session, self._org_id, self._user_id).enter(payment)
            reserves = ReserveLedgerRepository(self._session, self._org_id)
            reserve_plan_unresolved = True
            for plan in (await reserves.payment_plans(payment))[:1]:
                # Historical plans without allocation inputs cannot establish a
                # reserve ratio. Booking still succeeds with an unresolved audit.
                if not plan.snapshot_payload or not {'inputs', 'result'} <= plan.snapshot_payload.keys():
                    continue
                units = (await reserves.owner_units([payment])).get(payment.id, set())
                reserve_plan_unresolved = await reserves.hausgeld_share(payment.amount, units, plan) is None

        now = datetime.now(timezone.utc)
        payment.status = "booked"
        payment.booked_at = now
        payment.booked_by = self._user_id
        payment.retention_until = _compute_retention_until(payment.value_date)
        with self._session.no_autoflush:
            await self._require_deposit_sources(payment)
            await self._reserve_evidence(payment, complete=False)
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
                    **({"assigned_advance_modes": assigned_modes} if assigned_modes is not None else {}),
                    **({'reserve_plan_unresolved': True} if reserve_plan_unresolved else {}),
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

        if original.payment_type == 'RUECKLAGENENTNAHME':
            from app.services.reserve_ledger_service import ReserveLedgerService
            await ReserveLedgerService(self._session, self._org_id, self._user_id).require_payout_reversal(original)

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
            reserve_cost_entry_id=original.reserve_cost_entry_id,
            reserve_register_entry_id=original.reserve_register_entry_id,
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
                                       [to_payment_input(p) for p in payments])
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
        *, as_of: date | None = None, include_payment_dates: bool = False,
    ) -> MietkontoResult:
        """Soll/Ist/Saldo + offene Posten of one lease over a period (§3.1)."""
        await FinancialScopeService(self._session, self._org_id, self._user_id).select_report_scope(
            accounting_scope_id, scope_type='MIET', period_start=period_start, period_end=period_end)
        lease = await self._leases.get_by_id(lease_agreement_id)
        if lease is None:
            raise HTTPException(status_code=404, detail="Lease agreement not found")

        return await self.tenant_statement_in_current_scope(
            lease, period_start, period_end, as_of=as_of, include_payment_dates=include_payment_dates)

    async def tenant_statement_in_current_scope(
        self, lease, period_start: date, period_end: date,
        *, as_of: date | None = None, include_payment_dates: bool = False,
    ) -> MietkontoResult:
        """Compute a visible lease in the caller's already admitted reporting scope."""
        rent_changes = await LeaseRentChangeRepository(self._session, self._org_id).list_for_lease(lease.id)
        lc = LeaseChargeInput(
            lease_agreement_id=lease.id,
            valid_from=lease.valid_from,
            valid_to=db_exclusive_to_engine_inclusive(lease.valid_to),
            base_rent=lease.base_rent,
            advance_payment=lease.advance_payment, heating_advance_payment=lease.heating_advance_payment,
            advance_mode=lease.advance_mode, rent_changes=tuple(RentChangeInput(
                valid_from=row.valid_from, base_rent=row.base_rent, advance_payment=row.advance_payment, heating_advance_payment=row.heating_advance_payment,
                advance_mode=row.advance_mode,
            ) for row in rent_changes),
        )
        unit = await UnitRepository(self._session, self._org_id).get_by_id(lease.unit_id)
        prop = await PropertyRepository(self._session, self._org_id).get_by_id(unit.property_id) if unit else None
        if prop is None:
            raise HTTPException(404, "Property not found")
        charges = tenant_expected_charges(lc, period_start, period_end, federal_state=prop.federal_state)
        payments = await self._repo.list_for_lease(lease.id)
        scoped = self._scope_payments_to_period(payments, period_start, period_end)
        inputs = await self._tenant_payment_inputs(lease, rent_changes, [p for p in scoped if p.payment_type != "KAUTION"])
        return account_statement(charges, inputs, as_of=as_of or date.today(), include_payment_dates=include_payment_dates)

    async def _owner_plan_inputs(self, plan: EconomicPlan):
        """Frozen owner advances, dated ownership and the property of a plan's Hausgeld Soll."""
        lines = await self._plan_lines.list_for_plan(plan.id)
        advance_lines = [
            OwnerAdvanceChargeInput(
                owner_profile_id=ln.owner_profile_id, unit_id=ln.unit_id,
                annual_amount=ln.planned_amount,
            )
            for ln in lines
            if ln.line_type == "OWNER_ADVANCE"
            and ln.owner_profile_id is not None
        ]
        history = [OwnerPeriodInput(o.unit_id, o.owner_profile_id, o.valid_from,
            db_exclusive_to_engine_inclusive(o.valid_to), o.mea_share)
            for o in await SettlementRunRepository(self._session, self._org_id).ownership_for_property(plan.property_id)]
        prop = await PropertyRepository(self._session, self._org_id).get_by_id(plan.property_id)
        if prop is None:
            raise HTTPException(404, 'Property not found')
        return advance_lines, history, prop

    @staticmethod
    def _owner_charges(plan, prop, advance_lines, history, owner_profile_id, period_start, period_end):
        try:
            return owner_expected_charges(advance_lines, owner_profile_id, period_start, period_end,
                owner_periods=history, plan_start=plan.period_start,
                monthly_due_day=plan.monthly_due_day, federal_state=prop.federal_state)
        except ValueError as exc:
            raise HTTPException(422, {"code": "hausgeld_ownership_invalid",
                "message": "Die Eigentumszeiträume oder Miteigentumsanteile für das Hausgeld sind unvollständig."}) from exc

    async def owner_plan_charges(
        self, plan: EconomicPlan, owner_profile_id: uuid.UUID,
        period_start: date, period_end: date,
    ) -> list[ExpectedCharge]:
        """Dated advances for an authorized finalized plan, shared with dunning."""
        advance_lines, history, prop = await self._owner_plan_inputs(plan)
        return self._owner_charges(plan, prop, advance_lines, history, owner_profile_id,
            max(period_start, plan.period_start), min(period_end, plan.period_end))

    async def plan_hausgeld_charges(self, plan: EconomicPlan) -> list[ExpectedCharge]:
        """The Hausgeld Soll of every owner of the plan's units over the whole plan period.

        Candidates are the plan line owners and everybody whose ownership period of a planned
        unit overlaps the plan period. Each is computed by the owner statement's own call, on
        the lines of that owner's units only: the engine treats units independently, so the work
        grows with the units, not with owners times units.
        """
        advance_lines, history, prop = await self._owner_plan_inputs(plan)
        planned_units = {ln.unit_id for ln in advance_lines}
        units_of: dict[uuid.UUID, set[uuid.UUID]] = {}
        for ln in advance_lines:
            units_of.setdefault(ln.owner_profile_id, set()).add(ln.unit_id)
        for period in history:
            if (period.unit_id in planned_units and period.valid_from <= plan.period_end
                    and (period.valid_to is None or period.valid_to >= plan.period_start)):
                units_of.setdefault(period.owner_profile_id, set()).add(period.unit_id)
        charges: list[ExpectedCharge] = []
        for owner_profile_id, units in units_of.items():
            # A line without a unit stays in every subset, so the engine refuses it as for a statement.
            lines = [ln for ln in advance_lines if ln.unit_id is None or ln.unit_id in units]
            charges += self._owner_charges(plan, prop, lines, history, owner_profile_id,
                plan.period_start, plan.period_end)
        return charges

    async def owner_plan_charge_totals(
        self, plan: EconomicPlan, period_start: date, period_end: date,
    ) -> dict[tuple[uuid.UUID, uuid.UUID], tuple[Decimal, Decimal]]:
        """Frozen line amount and dated charge per (unit, owner) over the clamped period.

        One load of plan lines, ownership history and property for the whole
        run (#402); the engine walks each unit's own lines, so units stay
        independent. The settlement divides each unit's line amount by these
        dated charges, so a short plan's Soll is not truncated to the charged
        instalments. A history the ledger refuses raises its 422 here exactly
        as in the owner account.
        """
        advance_lines, history, prop = await self._owner_plan_inputs(plan)
        start, end = max(period_start, plan.period_start), min(period_end, plan.period_end)
        totals: dict[tuple[uuid.UUID, uuid.UUID], tuple[Decimal, Decimal]] = {}
        for unit_id in sorted({ln.unit_id for ln in advance_lines}, key=str):
            lines = [ln for ln in advance_lines if ln.unit_id == unit_id]
            owners = {ln.owner_profile_id for ln in lines}
            owners |= {p.owner_profile_id for p in history
                       if p.unit_id == unit_id and p.valid_from <= end
                       and (p.valid_to is None or p.valid_to >= start)}
            for owner_id in sorted(owners, key=str):
                charges = self._owner_charges(plan, prop, lines, history, owner_id, start, end)
                line_amount = sum((ln.annual_amount for ln in lines
                                   if ln.owner_profile_id == owner_id), Decimal('0.00'))
                totals[(unit_id, owner_id)] = (line_amount,
                    sum((c.amount for c in charges), Decimal('0.00')))
        return totals

    async def booked_hausgeld_months(
        self, plan: EconomicPlan, owner_profile_ids,
    ) -> set[tuple[uuid.UUID, int, int]]:
        """(owner, year, month) of the plan period that a booked Hausgeld payment already covers.

        The payments are the ones the owner statement counts as Ist: the owner's payments of the
        plan's property (`list_for_owner`), kept by `_scope_payments_to_period` over the plan
        period, and only booked, not reversed, non-Storno rows (the engine's own Ist filter). The
        month assignment is this action's own rule, the statement never files a payment without a
        month under a month: a Hausgeld payment received (IN) covers the month it names; one
        without year and month, or without one of them, covers the month of its value date. A
        payment that names the plan year but no month and whose value date lies outside that year
        therefore covers no month of the plan although the statement counts it (issue #566).
        """
        covered: set[tuple[uuid.UUID, int, int]] = set()
        for owner_profile_id in owner_profile_ids:
            payments = await self._repo.list_for_owner(owner_profile_id, property_id=plan.property_id)
            for p in self._scope_payments_to_period(payments, plan.period_start, plan.period_end):
                ist = to_payment_input(p)
                if (p.payment_type != "HAUSGELD_VORSCHUSS" or p.direction != "IN"
                        or not ist.is_booked or ist.is_reversed or ist.is_reversal):
                    continue
                if p.period_year is not None and p.period_month is not None:
                    covered.add((owner_profile_id, p.period_year, p.period_month))
                else:
                    covered.add((owner_profile_id, p.value_date.year, p.value_date.month))
        return covered

    async def account_statement_for_owner(
        self,
        owner_profile_id: uuid.UUID,
        economic_plan_id: uuid.UUID | None,
        period_start: date,
        period_end: date,
        accounting_scope_id: uuid.UUID | None = None,
        *, property_id: uuid.UUID | None = None, as_of: date | None = None,
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
                charges = await self.owner_plan_charges(plan, owner_profile_id, period_start, period_end)

        if sphere is not None and property_id is not None and sphere.property_id != property_id:
            raise HTTPException(422, 'Property differs from accounting scope')
        allocations = await SpecialLevyService(self._session, self._org_id, self._user_id).owner_allocations(
            owner_profile_id, period_start, period_end, sphere.id if sphere is not None else None, property_id)
        charges.extend(special_levy_expected_charges(
            [(due, allocation.amount) for allocation, due in allocations], owner_profile_id, period_start, period_end))

        payments = await self._repo.list_for_owner(owner_profile_id, property_id=property_id)
        scoped = self._scope_payments_to_period(payments, period_start, period_end)
        return account_statement(charges, [to_payment_input(p) for p in scoped], as_of=as_of or date.today())

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
    def _nk_credit_buckets_change(lease, inputs, start, end, old_end, new_end, payment_type="NK_VORAUSZAHLUNG"):
        def buckets(lease_end):
            return tuple(credit
                for year in range(start.year, end.year + 1)
                for credit in derive_advance_credits_for_period(inputs, AdvanceFilter(
                    frozenset({payment_type}), year, lease.id), start, end,
                    lease_start=lease.valid_from, lease_end=lease_end))

        return buckets(old_end) != buckets(new_end)

    async def lease_end_conflicts_finalized_credit(self, lease_id: uuid.UUID, last_day: date) -> bool:
        """Protect derived buckets changed by an end before the statement's last day."""
        runs = await self._leases.finalized_derived_statements_ending_after(lease_id, last_day)
        if not runs:
            return False
        for payment_type, source in (("NK_VORAUSZAHLUNG", "advance_payments_source"),
                ("HK_VORAUSZAHLUNG", "heating_advance_payments_source")):
            lease, inputs = await self._nk_advance_inputs(lease_id, payment_type=payment_type)
            old_end = db_exclusive_to_engine_inclusive(lease.valid_to)
            if any(getattr(run, source) == "DERIVED" and self._nk_credit_buckets_change(
                    lease, inputs, run.period_start, run.period_end, old_end, last_day, payment_type) for run in runs):
                return True
        return False

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
        return derive_advance_total([to_payment_input(p) for p in rows], filt)

    async def derive_nk_advance_total_for_period(
        self, lease_agreement_id: uuid.UUID, period_start: date, period_end: date,
        *, payment_type: str = "NK_VORAUSZAHLUNG",
    ) -> Decimal:
        """Derive one component total, retaining earlier frozen credits after edits."""
        credits, _excluded = await self.nk_advance_credits_for_period(lease_agreement_id, period_start, period_end, payment_type=payment_type)
        return sum((credit.amount for credit in credits), Decimal("0.00"))

    async def nk_advance_credits_for_period(
        self, lease_agreement_id: uuid.UUID, period_start: date, period_end: date,
        *, payment_type: str = "NK_VORAUSZAHLUNG",
    ) -> tuple[tuple[AdvanceCredit, ...], Decimal]:
        """Return auditable year/month buckets plus wholly outside-lease bookings."""
        from app.repositories.settlement_repo import SettlementRunRepository

        lease, inputs = await self._nk_advance_inputs(
            lease_agreement_id, payment_type=payment_type, classify=True)
        lease_end = db_exclusive_to_engine_inclusive(lease.valid_to)
        finalized = await SettlementRunRepository(self._session, self._org_id).finalized_bk_for_advance_years(
            lease_agreement_id, period_start, period_end)
        prefix = "heating_" if payment_type == "HK_VORAUSZAHLUNG" else ""
        frozen, known_windows, legacy_windows, seen = [], [], [], set()
        for run in finalized:
            payload = run.snapshot_payload or {}
            period = payload.get("period", {})
            bounds = (period.get("start"), period.get("end"))
            if bounds in seen:
                continue
            seen.add(bounds)
            data = payload.get("inputs", {})
            if data.get(prefix + "advance_payments_source") != "DERIVED":
                continue
            start, end = map(date.fromisoformat, bounds)
            buckets = data.get(prefix + "advance_payment_credits")
            if buckets is None:
                old_lease = next((row for row in data.get("lease_periods", [])
                    if row["lease_agreement_id"] == str(lease_agreement_id)), {})
                old_end = date.fromisoformat(old_lease["to"]) if old_lease.get("to") else None
                old_total = Decimal(payload.get("result", {}).get(prefix + "advance_payments_total", "0.00"))
                legacy_windows.append((start, end, old_end, old_total))
                continue
            known_windows.append((start, end, {(row["year"], row["month"]) for row in buckets}))
            frozen.extend(AdvanceCredit(row["year"], row["month"], start, end, Decimal(row["amount"]),
                Decimal(row.get("combined_amount", "0.00")), Decimal(row.get("unallocated_amount", "0.00")))
                          for row in buckets)

        signed_buckets = {(year, month) for year, month in tenant_refund_buckets(inputs, frozen)
                          if period_start.year <= year <= period_end.year}
        for start, end, old_end, old_total in legacy_windows:
            affected_by_refund = any(
                (start.year, start.month) <= (year, month or 12)
                and (year, month or 1) <= (end.year, end.month)
                for year, month in signed_buckets
            )
            if affected_by_refund or (
                old_end != lease_end and old_total
                and self._nk_credit_buckets_change(lease, inputs, start, end, old_end, lease_end, payment_type)
            ):
                raise HTTPException(409, {"code": "advance_credit_history_missing",
                    "message": "Der eingefrorene Vorauszahlungsnachweis fehlt. Bitte die frühere Abrechnung korrigieren."})

        # A recorded breakdown can omit buckets that credited zero at finalization.
        for start, end, recorded in known_windows:
            frozen.extend(AdvanceCredit(year, month, start, end, Decimal("0.00"))
                          for year, month in signed_buckets - recorded if start.year <= year <= end.year)
        credits = tuple(credit
            for year in range(period_start.year, period_end.year + 1)
            for credit in derive_advance_credits_for_period(inputs, AdvanceFilter(
                types=frozenset({payment_type}),
                year=year,
                lease_agreement_id=lease_agreement_id,
            ), period_start, period_end, lease_start=lease.valid_from,
                lease_end=lease_end, finalized_credits=tuple(frozen)))
        return credits, self._uncredited_nk_total(lease, inputs, period_start, period_end, payment_type)

    async def _nk_advance_inputs(self, lease_agreement_id, *, payment_type="NK_VORAUSZAHLUNG", classify=False):
        lease = await self._leases.get_by_id(lease_agreement_id)
        if lease is None:
            raise HTTPException(404, "Lease agreement not found")
        rows = await self._repo.derived_for_nk_for_lease(lease_agreement_id, payment_type=payment_type)
        if not classify or not rows or payment_type != "NK_VORAUSZAHLUNG":
            return lease, [to_payment_input(p) for p in rows]
        history = await LeaseRentChangeRepository(self._session, self._org_id).list_for_lease(lease.id)
        return lease, await self._tenant_payment_inputs(lease, history, rows)

    async def _tenant_payment_inputs(self, lease, history, rows):
        candidates = [p.id for p in rows if p.payment_type == "NK_VORAUSZAHLUNG"
            and p.period_year is not None and 1 <= p.period_year <= 9999 and p.booked_at is not None]
        frozen = await self._audit.booked_advance_modes(candidates) if candidates else {}
        valid_ids = set(candidates)
        inputs = []
        for payment in rows:
            modes = ()
            if payment.id in valid_ids:
                modes = (frozen[payment.id] if payment.id in frozen
                         else self._assigned_advance_modes(lease, history, payment))
            includes_combined = "COMBINED" in modes
            inputs.append(to_payment_input(payment,
                combined_advance=includes_combined and len(modes) == 1,
                unallocated_advance=includes_combined and len(modes) > 1))
        return inputs

    @staticmethod
    def _assigned_advance_modes(lease, history, payment):
        # A backdated rate inserted after booking cannot change its meaning.
        if payment.booked_at is not None:
            history = [row for row in history if row.created_at <= payment.booked_at]
        if payment.period_month is None:
            start, end = date(payment.period_year, 1, 1), date(payment.period_year, 12, 31)
        else:
            year, month = payment.period_year, payment.period_month
            start, end = date(year, month, 1), date(year, month, monthrange(year, month)[1])
        start = max(start, lease.valid_from)
        end = min(end, db_exclusive_to_engine_inclusive(lease.valid_to) or end)
        return advance_modes_for_period(lease, history, start, end) if start <= end else frozenset()

    @staticmethod
    def _uncredited_nk_total(lease, inputs, period_start, period_end, payment_type="NK_VORAUSZAHLUNG"):
        return sum((uncredited_advance_total(inputs, AdvanceFilter(
            frozenset({payment_type}), year, lease.id), lease.valid_from,
            db_exclusive_to_engine_inclusive(lease.valid_to), period_start=period_start, period_end=period_end)
            for year in sorted({p.period_year for p in inputs if p.period_year is not None})), Decimal("0.00"))

    async def uncredited_nk_advance_total(
        self, lease_agreement_id: uuid.UUID, period_start: date, period_end: date,
        *, payment_type: str = "NK_VORAUSZAHLUNG",
    ) -> Decimal:
        """Notice-only derivation; manual statement totals need no frozen history."""
        lease, inputs = await self._nk_advance_inputs(lease_agreement_id, payment_type=payment_type)
        return self._uncredited_nk_total(lease, inputs, period_start, period_end, payment_type)

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
        return derive_advance_total([to_payment_input(p) for p in rows], filt)

    async def derive_hausgeld_total_for_period(
        self, owner_profile_id: uuid.UUID, period_start: date, period_end: date,
        *, property_id: uuid.UUID | None = None, plan_soll: Decimal | None = None,
    ) -> Decimal:
        """Read net Hausgeld; separate WEG reserve covers only unpaid plan Soll.

        An absent plan Soll never credits reserve, including against negative
        Hausgeld. The draft ledger shares Rc = min(max(R, 0), max(Soll-H, 0)),
        using in-period value-date cash and the owner Soll of the run's plan (its
        economic_plan_id, else the single current finalized plan of exactly that
        period; none means no correction).
        Only with owner Soll and Rc > 0, it computes correction =
        max(0, Rc + sum(credited Hausgeld reserve) - planned owner reserve);
        a positive correction adds one negative contribution row at period end.
        Excess R-Rc stays in full. Frozen ledgers keep their stored amounts;
        late cash books the difference to the correction stored for that period.
        """
        rows = await self._repo.list_for_owner(owner_profile_id, property_id=property_id)
        assigned = [to_payment_input(p) for p in rows if p.period_year is not None]
        scoped = self._scope_payments_to_period(
            [p for p in rows if p.period_year is None], period_start, period_end)
        totals = {}
        types = ('HAUSGELD_VORSCHUSS', 'RUECKLAGENANTEIL') if plan_soll is not None else ('HAUSGELD_VORSCHUSS',)
        for payment_type in types:
            assigned_total = sum((sign * derive_advance_total_for_period(
                [p for p in assigned if p.direction == direction], AdvanceFilter(
                    types=frozenset({payment_type}), year=year,
                    owner_profile_id=owner_profile_id,
                ), period_start, period_end)
                for direction, sign in (("IN", 1), ("OUT", -1))
                for year in range(period_start.year, period_end.year + 1)), Decimal("0.00"))
            totals[payment_type] = assigned_total + account_statement(
                [], [to_payment_input(p) for p in scoped if p.payment_type == payment_type]).ist_total
        hausgeld = totals['HAUSGELD_VORSCHUSS']
        if plan_soll is None:
            return hausgeld
        return hausgeld + covered_reserve(hausgeld, totals['RUECKLAGENANTEIL'], plan_soll)
