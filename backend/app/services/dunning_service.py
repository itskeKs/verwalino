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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.dunning import (
    DEFAULT_DUE_DAYS,
    OverdueItem,
    assemble_dunning,
    dunning_fee,
    find_overdue_items,
    next_dunning_level,
)
from app.engine.mietkonto import (
    OwnerAdvanceChargeInput,
    PaymentInput,
    account_statement,
    owner_expected_charges,
    special_levy_expected_charges,
)
from app.models.audit_event import AuditEvent
from app.models.dunning_notice import DunningNotice
from app.repositories.audit_repo import AuditRepository
from app.core.rbac import ScopeCapability
from app.services.dunning_scope_service import DunningScopeService
from app.services.special_levy_service import SpecialLevyService
from app.services.payment_service import PaymentService
from app.repositories.unit_repo import UnitRepository
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
        accounting_scope_id: uuid.UUID | None = None,
        base_interest_rate: Decimal | None = None,
    ) -> DunningNotice:
        """Create a draft dunning notice for a tenant based on overdue open items.

        Reuses the M6 account_statement derivation -- does NOT duplicate it.
        """
        ref_date = reference_date or date.today()
        context = DunningScopeService(self._session, self._org_id, self._user_id)
        sphere = await context.prepare(accounting_scope_id, 'MIET', period_start, period_end)

        lease, tenant, result, dunning = await self.tenant_basis(
            tenant_profile_id, lease_agreement_id, period_start, period_end,
            ref_date, grace_days, override_fee, accounting_scope_id, base_interest_rate,
        )
        if dunning is None:
            raise HTTPException(422, "No overdue items found for this tenant in the given period")

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
            interest_amount=dunning.interest_amount,
            interest_snapshot=dunning.interest_snapshot,
            included_items=dunning.items_snapshot(),
            status="draft",
        )
        await context.attach(notice, sphere, period_start, period_end, lease_id=lease_agreement_id)
        notice = await self._repo.create(notice)
        await self._session.refresh(notice)

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
                    "interest_amount": str(dunning.interest_amount),
                    "total_amount": str(dunning.total_amount),
                    "status": "draft",
                },
            )
        )
        return notice

    async def require_tenant_sources(self, lease_id, tenant_id, sphere):
        """Check the complete contract and active recipient, including legacy mode."""
        lease = await self._leases.get_by_id(lease_id)
        tenant = await self._tenants.get_by_id(tenant_id)
        if lease is None or tenant is None:
            raise HTTPException(404, "Complete dunning sources not accessible")
        if lease.tenant_profile_id != tenant_id:
            raise HTTPException(422, "Lease and dunning recipient must match")
        if sphere is not None and not await self._session.scalar(text(
                "SELECT unit_in_financial_context(:org,:unit)"),
                {'org': self._org_id, 'unit': lease.unit_id}):
            raise HTTPException(404, "Lease is outside the accounting scope")
        if not await self._session.scalar(text(
                "SELECT dunning_contact_access(:org,:tenant,NULL,'prepare')"),
                {'org': self._org_id, 'tenant': tenant.id}):
            raise HTTPException(404, "Dunning recipient source not accessible")
        unit = await UnitRepository(self._session, self._org_id).get_by_id(lease.unit_id)
        person = await self._persons.get_by_id(tenant.person_id)
        if unit is None or person is None:
            raise HTTPException(404, "Complete dunning sources not accessible")
        if person.processing_restricted or person.anonymized_at is not None:
            raise HTTPException(409, "Dunning recipient is restricted or anonymized")
        prop = await self._properties.get_by_id(unit.property_id)
        if prop is None:
            raise HTTPException(404, "Dunning property source not accessible")
        return lease, tenant, person, unit, prop

    async def tenant_basis(
        self, tenant_profile_id, lease_agreement_id, period_start, period_end,
        reference_date, grace_days=0, override_fee=None, accounting_scope_id=None,
        base_interest_rate=None,
    ):
        """One history-aware canonical statement and engine assembly, without writes."""
        context = DunningScopeService(self._session, self._org_id, self._user_id)
        sphere = await context.prepare(accounting_scope_id, 'MIET', period_start, period_end)
        lease, tenant, _, _, _ = await self.require_tenant_sources(
            lease_agreement_id, tenant_profile_id, sphere)
        if lease.valid_from > period_end or (lease.valid_to is not None and lease.valid_to <= period_start):
            raise HTTPException(422, "Lease must overlap the dunning period")
        result = await PaymentService(self._session, self._org_id, self._user_id).account_statement_for_lease(
            lease.id, period_start, period_end, accounting_scope_id)
        overdue = self._engine_result(find_overdue_items, list(result.open_items),
            reference_date, grace_days=grace_days)
        if not overdue:
            return lease, tenant, result, None
        await context.history(tenant_id=tenant.id)
        issued_count = await self._repo.count_issued_for_tenant(tenant.id)
        dunning = self._engine_result(assemble_dunning, overdue_items=overdue,
            existing_notice_count=issued_count, issue_date=reference_date,
            override_fee=override_fee, base_interest_rate=base_interest_rate)
        return lease, tenant, result, dunning

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
        accounting_scope_id: uuid.UUID | None = None,
        base_interest_rate: Decimal | None = None,
    ) -> DunningNotice:
        """Create a draft dunning notice for an owner based on overdue open items."""
        ref_date = reference_date or date.today()
        context = DunningScopeService(self._session, self._org_id, self._user_id)
        sphere = await context.prepare(accounting_scope_id, 'WEG', period_start, period_end)

        owner = await self._owners.get_by_id(owner_profile_id)
        if owner is None:
            raise HTTPException(status_code=404, detail="Owner profile not found")

        # Compute Soll from finalized plan (if available)
        charges = []
        if economic_plan_id is not None:
            plan = await self._plans.get_by_id(economic_plan_id)
            if plan is None:
                raise HTTPException(404, "Economic plan not found")
            plan_scope = await context.financial.enter(plan, computation=True)
            sphere = await context.prepare(accounting_scope_id, 'WEG', period_start, period_end)
            if sphere is not None and plan_scope is None:
                raise HTTPException(422, 'Economic plan must be bound to the selected scope')
            if sphere is not None and (plan.status != 'finalized' or period_start < plan.period_start or period_end > plan.period_end):
                raise HTTPException(422, 'Scoped owner dunning requires a finalized plan covering its period')
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

        allocations = await SpecialLevyService(self._session, self._org_id, self._user_id).owner_allocations(
            owner_profile_id, period_start, period_end, sphere.id if sphere is not None else None)
        if sphere is not None and economic_plan_id is None and not allocations:
            raise HTTPException(422, 'Scoped owner dunning requires a finalized economic plan or frozen special levies')
        charges.extend(special_levy_expected_charges(
            [(due, allocation.amount) for allocation, due in allocations],
            owner_profile_id, period_start, period_end))
        payments = await self._payments.list_for_owner(owner_profile_id)
        scoped = self._scope_payments_to_period(payments, period_start, period_end)
        result = account_statement(charges, [_to_payment_input(p) for p in scoped])

        overdue = self._engine_result(
            find_overdue_items, list(result.open_items), ref_date, grace_days=grace_days
        )
        if not overdue:
            raise HTTPException(
                status_code=422,
                detail="No overdue items found for this owner in the given period",
            )

        await context.history(owner_id=owner_profile_id)
        issued_count = await self._repo.count_issued_for_owner(owner_profile_id)
        dunning = self._engine_result(
            assemble_dunning,
            overdue_items=overdue,
            existing_notice_count=issued_count,
            issue_date=ref_date,
            override_fee=override_fee,
            base_interest_rate=base_interest_rate,
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
            interest_amount=dunning.interest_amount,
            interest_snapshot=dunning.interest_snapshot,
            included_items=dunning.items_snapshot(),
            status="draft",
        )
        await context.attach(notice, sphere, period_start, period_end, plan_id=economic_plan_id,
            levy_ids=sorted({allocation.special_levy_id for allocation, _ in allocations}, key=str))
        notice = await self._repo.create(notice)
        await self._session.refresh(notice)

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
                    "interest_amount": str(dunning.interest_amount),
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
        context = DunningScopeService(self._session, self._org_id, self._user_id)
        await context.financial.lock_inputs()
        notice = await self._repo.get_by_id_for_update(dunning_id)
        if notice is None:
            raise HTTPException(status_code=404, detail="Dunning notice not found")
        await context.enter(notice, ScopeCapability.APPROVE)
        if notice.status != "draft" or notice.cancelled_at is not None:
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
        context = DunningScopeService(self._session, self._org_id, self._user_id)
        await context.financial.lock_inputs()
        notice = await self._repo.get_by_id_for_update(dunning_id)
        if notice is None:
            raise HTTPException(status_code=404, detail="Dunning notice not found")
        scope = await context.enter(notice, ScopeCapability.PREPARE)
        if notice.status == "issued":
            raise HTTPException(
                status_code=409,
                detail="Cannot delete an issued dunning notice",
            )
        if scope is not None:
            if notice.cancelled_at is not None:
                return
            notice.cancelled_at = datetime.now(timezone.utc)
            await self._repo.update(notice)
        await self._audit.create(
            AuditEvent(
                actor_user_id=self._user_id,
                entity_type="dunning_notice",
                entity_id=dunning_id,
                action="dunning_notice.cancelled" if scope is not None else "dunning_notice.deleted",
                payload_diff={"status": "draft"},
            )
        )
        if scope is None:
            # INSERT RETURNING audits need the original recipient for the live
            # read policy; persist the event before removing its legacy source.
            await self._repo.delete(notice)

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

    @staticmethod
    def _engine_result(compute, *args, **kwargs):
        try:
            return compute(*args, **kwargs)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

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
