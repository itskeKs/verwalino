"""Dunning service -- create / issue / list / PDF for dunning notices (M7).

Boundary discipline (SDD section 16): the service loads ORM rows, calls the
pure engine (app.engine.dunning + app.engine.mietkonto), and persists.
The engine never sees the DB; the service never re-implements the math.

Lifecycle (mirrors payment_service / cost_entry_service):
  create_dunning_for_tenant / create_dunning_for_owner
    -> draft dunning notice (editable).
  issue -> draft -> issued (atomic; sets issued_at/by, audit).
    Immutable from commit (ADR-0003 trigger, 0019).
  Only issued notices can be rendered as PDF.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.temporal import db_exclusive_to_engine_inclusive
from app.engine.dunning import (
    DEFAULT_DUE_DAYS,
    OverdueItem,
    assemble_dunning,
    dunning_fee,
    find_overdue_items,
    next_dunning_level,
)
from app.engine.mietkonto import (
    LeaseChargeInput,
    OwnerAdvanceChargeInput,
    PaymentInput,
    account_statement,
    owner_expected_charges,
    tenant_expected_charges,
)
from app.models.audit_event import AuditEvent
from app.models.dunning_notice import DunningNotice
from app.repositories.audit_repo import AuditRepository
from app.repositories.dunning_notice_repo import DunningNoticeRepository
from app.repositories.economic_plan_repo import (
    EconomicPlanLineRepository,
    EconomicPlanRepository,
)
from app.repositories.lease_agreement_repo import LeaseAgreementRepository
from app.repositories.payment_repo import PaymentRepository
from app.repositories.person_repo import PersonRepository
from app.repositories.tenant_profile_repo import TenantProfileRepository
from app.repositories.owner_profile_repo import OwnerProfileRepository
from app.repositories.property_repo import PropertyRepository
from app.services.render_names import freeze_block, read_frozen_values


def _to_payment_input(p) -> PaymentInput:
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


class DunningService:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID):
        self._session = session
        self._org_id = org_id
        self._user_id = user_id
        self._repo = DunningNoticeRepository(session, org_id)
        self._leases = LeaseAgreementRepository(session, org_id)
        self._payments = PaymentRepository(session, org_id)
        self._plans = EconomicPlanRepository(session, org_id)
        self._plan_lines = EconomicPlanLineRepository(session, org_id)
        self._persons = PersonRepository(session, org_id)
        self._tenants = TenantProfileRepository(session, org_id)
        self._owners = OwnerProfileRepository(session, org_id)
        self._properties = PropertyRepository(session, org_id)
        self._audit = AuditRepository(session, org_id)

    # ------------------------------------------------------------------
    # Create dunning for tenant (from lease open items)
    # ------------------------------------------------------------------
    async def create_dunning_for_tenant(
        self,
        tenant_profile_id: uuid.UUID,
        lease_agreement_id: uuid.UUID,
        period_start: date,
        period_end: date,
        reference_date: date | None = None,
        grace_days: int = 0,
        override_fee: Decimal | None = None,
    ) -> DunningNotice:
        """Create a draft dunning notice for a tenant based on overdue open items.

        Reuses the M6 account_statement derivation -- does NOT duplicate it.
        """
        ref_date = reference_date or date.today()

        # Resolve lease
        lease = await self._leases.get_by_id(lease_agreement_id)
        if lease is None:
            raise HTTPException(status_code=404, detail="Lease agreement not found")

        # Verify tenant exists
        tenant = await self._tenants.get_by_id(tenant_profile_id)
        if tenant is None:
            raise HTTPException(status_code=404, detail="Tenant profile not found")

        # Compute Soll/Ist via M6 engine
        lc = LeaseChargeInput(
            lease_agreement_id=lease.id,
            valid_from=lease.valid_from,
            valid_to=db_exclusive_to_engine_inclusive(lease.valid_to),
            base_rent=lease.base_rent,
            advance_payment=lease.advance_payment,
        )
        charges = tenant_expected_charges(lc, period_start, period_end)
        payments = await self._payments.list_for_lease(lease_agreement_id)
        scoped = self._scope_payments_to_period(payments, period_start, period_end)
        result = account_statement(charges, [_to_payment_input(p) for p in scoped])

        # Detect overdue items
        overdue = find_overdue_items(
            list(result.open_items), ref_date, grace_days=grace_days
        )
        if not overdue:
            raise HTTPException(
                status_code=422,
                detail="No overdue items found for this tenant in the given period",
            )

        # Escalation level
        issued_count = await self._repo.count_issued_for_tenant(tenant_profile_id)

        # Assemble
        dunning = assemble_dunning(
            overdue_items=overdue,
            existing_notice_count=issued_count,
            issue_date=ref_date,
            override_fee=override_fee,
        )
        if dunning is None:
            raise HTTPException(status_code=422, detail="Could not assemble dunning notice")

        # Persist as draft
        notice = DunningNotice(
            organization_id=self._org_id,
            tenant_profile_id=tenant_profile_id,
            owner_profile_id=None,
            level=dunning.level,
            issue_date=dunning.issue_date,
            due_date=dunning.due_date,
            principal_amount=dunning.principal_amount,
            fee_amount=dunning.fee_amount,
            total_amount=dunning.total_amount,
            included_items=dunning.items_snapshot(),
            status="draft",
        )
        notice = await self._repo.create(notice)

        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="dunning_notice",
                entity_id=notice.id,
                action="dunning_notice.created",
                payload_diff={
                    "tenant_profile_id": str(tenant_profile_id),
                    "level": dunning.level,
                    "principal_amount": str(dunning.principal_amount),
                    "fee_amount": str(dunning.fee_amount),
                    "total_amount": str(dunning.total_amount),
                    "status": "draft",
                },
            )
        )
        return notice

    # ------------------------------------------------------------------
    # Create dunning for owner
    # ------------------------------------------------------------------
    async def create_dunning_for_owner(
        self,
        owner_profile_id: uuid.UUID,
        period_start: date,
        period_end: date,
        economic_plan_id: uuid.UUID | None = None,
        reference_date: date | None = None,
        grace_days: int = 0,
        override_fee: Decimal | None = None,
    ) -> DunningNotice:
        """Create a draft dunning notice for an owner based on overdue open items."""
        ref_date = reference_date or date.today()

        owner = await self._owners.get_by_id(owner_profile_id)
        if owner is None:
            raise HTTPException(status_code=404, detail="Owner profile not found")

        # Compute Soll from finalized plan (if available)
        charges = []
        if economic_plan_id is not None:
            plan = await self._plans.get_by_id(economic_plan_id)
            if plan is not None and plan.status == "finalized":
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

        payments = await self._payments.list_for_owner(owner_profile_id)
        scoped = self._scope_payments_to_period(payments, period_start, period_end)
        result = account_statement(charges, [_to_payment_input(p) for p in scoped])

        overdue = find_overdue_items(
            list(result.open_items), ref_date, grace_days=grace_days
        )
        if not overdue:
            raise HTTPException(
                status_code=422,
                detail="No overdue items found for this owner in the given period",
            )

        issued_count = await self._repo.count_issued_for_owner(owner_profile_id)
        dunning = assemble_dunning(
            overdue_items=overdue,
            existing_notice_count=issued_count,
            issue_date=ref_date,
            override_fee=override_fee,
        )
        if dunning is None:
            raise HTTPException(status_code=422, detail="Could not assemble dunning notice")

        notice = DunningNotice(
            organization_id=self._org_id,
            owner_profile_id=owner_profile_id,
            tenant_profile_id=None,
            level=dunning.level,
            issue_date=dunning.issue_date,
            due_date=dunning.due_date,
            principal_amount=dunning.principal_amount,
            fee_amount=dunning.fee_amount,
            total_amount=dunning.total_amount,
            included_items=dunning.items_snapshot(),
            status="draft",
        )
        notice = await self._repo.create(notice)

        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="dunning_notice",
                entity_id=notice.id,
                action="dunning_notice.created",
                payload_diff={
                    "owner_profile_id": str(owner_profile_id),
                    "level": dunning.level,
                    "principal_amount": str(dunning.principal_amount),
                    "fee_amount": str(dunning.fee_amount),
                    "total_amount": str(dunning.total_amount),
                    "status": "draft",
                },
            )
        )
        return notice

    # ------------------------------------------------------------------
    # Issue (freeze)
    # ------------------------------------------------------------------
    async def issue(self, dunning_id: uuid.UUID) -> DunningNotice:
        """Issue a draft dunning notice -- atomic freeze (ADR-0003 pattern).

        Sets status='issued', issued_at/by + audit. The trigger (0019)
        freezes it from commit. Only issued notices can be rendered as PDF.
        """
        notice = await self._repo.get_by_id(dunning_id)
        if notice is None:
            raise HTTPException(status_code=404, detail="Dunning notice not found")
        if notice.status != "draft":
            raise HTTPException(
                status_code=409,
                detail=f"Cannot issue from status '{notice.status}'",
            )

        now = datetime.now(timezone.utc)
        # Adressat-Anzeige-Namen einfrieren (ADR-0012): noch als Entwurf
        # (OLD.status='draft', Trigger lässt das Schreiben zu) in derselben
        # Ausstell-UPDATE. Danach unveränderbar; ein späteres DSGVO-Anonymisieren
        # der Person verändert das ausgestellte Mahn-PDF nicht mehr.
        notice.frozen_display_names = freeze_block(await self._display_values(notice))
        notice.status = "issued"
        notice.issued_at = now
        notice.issued_by = self._user_id
        notice = await self._repo.update(notice)

        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="dunning_notice",
                entity_id=notice.id,
                action="dunning_notice.issued",
                payload_diff={
                    "issued_by": str(self._user_id),
                    "level": notice.level,
                    "total_amount": str(notice.total_amount),
                },
            )
        )
        return notice

    # ------------------------------------------------------------------
    # Delete draft
    # ------------------------------------------------------------------
    async def delete_draft(self, dunning_id: uuid.UUID) -> None:
        """Delete a draft dunning notice. Issued notices are protected by trigger."""
        notice = await self._repo.get_by_id(dunning_id)
        if notice is None:
            raise HTTPException(status_code=404, detail="Dunning notice not found")
        if notice.status == "issued":
            raise HTTPException(
                status_code=409,
                detail="Cannot delete an issued dunning notice",
            )
        await self._repo.delete(notice)
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="dunning_notice",
                entity_id=dunning_id,
                action="dunning_notice.deleted",
                payload_diff={"status": "draft"},
            )
        )

    # ------------------------------------------------------------------
    # Name resolution for PDF
    # ------------------------------------------------------------------
    async def resolve_person_name(self, notice: DunningNotice) -> str:
        """Resolve the person name behind a dunning notice's counterparty."""
        if notice.tenant_profile_id:
            tp = await self._tenants.get_by_id(notice.tenant_profile_id)
            if tp:
                person = await self._persons.get_by_id(tp.person_id)
                if person:
                    parts = [person.first_name, person.last_name]
                    return " ".join(p for p in parts if p).strip() or "---"
        if notice.owner_profile_id:
            op = await self._owners.get_by_id(notice.owner_profile_id)
            if op:
                person = await self._persons.get_by_id(op.person_id)
                if person:
                    parts = [person.first_name, person.last_name]
                    return " ".join(p for p in parts if p).strip() or "---"
        return "---"

    async def resolve_person_address(self, notice: DunningNotice) -> str:
        """Resolve the address of the counterparty for the PDF."""
        profile_id = notice.tenant_profile_id or notice.owner_profile_id
        if not profile_id:
            return ""
        if notice.tenant_profile_id:
            tp = await self._tenants.get_by_id(notice.tenant_profile_id)
            person_id = tp.person_id if tp else None
        else:
            op = await self._owners.get_by_id(notice.owner_profile_id)
            person_id = op.person_id if op else None
        if not person_id:
            return ""
        person = await self._persons.get_by_id(person_id)
        if not person:
            return ""
        parts = []
        if person.street:
            parts.append(person.street)
        if person.zip_code or person.city:
            parts.append(f"{person.zip_code} {person.city}".strip())
        return ", ".join(parts)

    async def _display_values(self, notice: DunningNotice) -> dict:
        """Aufgelöste Adressat-Anzeige-Werte -- EINE Quelle für Einfrieren + Live.

        Die eingefrorene und die live aufgelöste Form haben exakt dieselbe
        Struktur (person_name, person_address, organization_name); Einfrieren
        heißt nur, dieses dict beim Ausstellen zu persistieren.
        """
        return {
            "person_name": await self.resolve_person_name(notice),
            "person_address": await self.resolve_person_address(notice),
            "organization_name": "",
        }

    async def render_display_names(self, notice: DunningNotice):
        """DunningDisplayNames für den Renderer: eingefroren (issued), sonst live.

        Ausgestellte Mahnung mit eingefrorenen Namen -> eingefrorene Werte
        (selbsttragend, immun gegen spätere Anonymisierung). Entwurf oder
        Altbestand ohne eingefrorene Namen -> Live-Auflösung wie bisher.
        """
        from app.services.dunning_pdf import DunningDisplayNames

        frozen = (
            read_frozen_values(notice.frozen_display_names)
            if notice.status == "issued"
            else None
        )
        values = frozen if frozen is not None else await self._display_values(notice)
        allowed = {"person_name", "person_address", "organization_name"}
        return DunningDisplayNames(**{k: v for k, v in values.items() if k in allowed})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _scope_payments_to_period(payments, period_start: date, period_end: date):
        """Reuse the same scoping logic as PaymentService."""
        y_lo, y_hi = period_start.year, period_end.year
        scoped = []
        for p in payments:
            if p.period_year is not None:
                if y_lo <= p.period_year <= y_hi:
                    scoped.append(p)
            elif period_start <= p.value_date <= period_end:
                scoped.append(p)
        return scoped
