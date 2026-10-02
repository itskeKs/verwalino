"""DSGVO repository -- read-only retention-binding discovery + person lookup.

No mutation here EXCEPT the person row is fetched as a managed ORM instance for
the service to anonymize (the service flushes). All other methods only READ the
records that may bind a person to statutory retention (ADR-0012 section 1):

  * vendor -> confirmed cost entries (Belege)        BINDING_COST_ENTRY_VENDOR
  * tenant_profile -> finalized BK runs              BINDING_SETTLEMENT_ADDRESSEE
  * owner_profile  -> finalized WEG runs (via line)  BINDING_FINALIZED_OWNERSHIP
  * owner_profile  -> finalized Wirtschaftsplaene     BINDING_FINALIZED_OWNERSHIP
  * tenant/owner   -> issued dunning notices          BINDING_DUNNING

Plus ``has_active_lease`` / ``has_active_ownership``: a LIVE (current or future)
contractual relationship -- ongoing processing purpose (Art. 6 I b DSGVO) -- which
blocks erasure entirely (the service raises 409; you must end the relationship
first). This is a SERVICE-layer rule, not a retention binding, hence checked here
against the DB rather than in the pure engine.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Sequence

from sqlalchemy import func, or_, select, text
from fastapi import HTTPException

from app.models.cost_entry import CostEntry
from app.models.dunning_notice import DunningNotice
from app.models.economic_plan import EconomicPlan
from app.models.economic_plan_line import EconomicPlanLine
from app.models.invoice_document import InvoiceDocument
from app.models.lease_agreement import LeaseAgreement
from app.models.owner_profile import OwnerProfile
from app.models.ownership_history import OwnershipHistory
from app.models.person import Person
from app.models.settlement_line import SettlementLine
from app.models.settlement_run import SettlementRun
from app.models.tenant_profile import TenantProfile
from app.models.vendor import Vendor


class DsgvoRepository:
    def __init__(self, session, org_id: uuid.UUID):
        self._session = session
        self._org_id = org_id

    # ------------------------------------------------------------------
    # Person + role profiles
    # ------------------------------------------------------------------
    async def require_complete_financial_context(self, person_id, *, capability='read'):
        complete = await self._session.scalar(text(
            'SELECT privacy_financial_context_complete(:org,:person,:capability)'),
            {'org': self._org_id, 'person': person_id, 'capability': capability})
        if not complete:
            raise HTTPException(409, 'Complete financial access is required to review retention or export this person')

    async def get_person(self, person_id: uuid.UUID) -> Person | None:
        stmt = (
            select(Person)
            .where(Person.organization_id == self._org_id)
            .where(Person.id == person_id)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def _ids(self, model, person_id: uuid.UUID) -> list[uuid.UUID]:
        stmt = (
            select(model.id)
            .where(model.organization_id == self._org_id)
            .where(model.person_id == person_id)
        )
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    async def vendor_ids(self, person_id: uuid.UUID) -> list[uuid.UUID]:
        return await self._ids(Vendor, person_id)

    async def tenant_profile_ids(self, person_id: uuid.UUID) -> list[uuid.UUID]:
        return await self._ids(TenantProfile, person_id)

    async def owner_profile_ids(self, person_id: uuid.UUID) -> list[uuid.UUID]:
        return await self._ids(OwnerProfile, person_id)

    # ------------------------------------------------------------------
    # Retention bindings
    # ------------------------------------------------------------------
    async def confirmed_cost_entries_for_vendors(
        self, vendor_ids: list[uuid.UUID]
    ) -> Sequence[CostEntry]:
        """All CONFIRMED cost entries booked against the person's vendors.

        Superseded entries are intentionally INCLUDED: a corrected-away original
        is still a retained Beleg of the GoBD trail. Binding more (not fewer) is
        the conservative, invariant-preserving direction (ADR-0012 section 6).
        """
        if not vendor_ids:
            return []
        stmt = (
            select(CostEntry)
            .where(CostEntry.organization_id == self._org_id)
            .where(CostEntry.vendor_id.in_(vendor_ids))
            .where(CostEntry.status == "confirmed")
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def max_retention_for_cost_entry(
        self, cost_entry_id: uuid.UUID
    ) -> date | None:
        """The latest retention_until of the cost entry's invoice documents."""
        stmt = (
            select(func.max(InvoiceDocument.retention_until))
            .where(InvoiceDocument.organization_id == self._org_id)
            .where(InvoiceDocument.cost_entry_id == cost_entry_id)
            .where(InvoiceDocument.retention_until.is_not(None))
        )
        return (await self._session.execute(stmt)).scalar_one()

    async def finalized_bk_runs_for_tenants(
        self, tenant_ids: list[uuid.UUID]
    ) -> Sequence[SettlementRun]:
        if not tenant_ids:
            return []
        stmt = (
            select(SettlementRun)
            .where(SettlementRun.organization_id == self._org_id)
            .where(SettlementRun.status == "finalized")
            .where(SettlementRun.run_type == "BK_MIETER")
            .where(SettlementRun.tenant_profile_id.in_(tenant_ids))
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def finalized_weg_runs_for_owners(
        self, owner_ids: list[uuid.UUID]
    ) -> Sequence[SettlementRun]:
        """Finalized WEG runs that carry a line addressed to one of the owners."""
        if not owner_ids:
            return []
        stmt = (
            select(SettlementRun)
            .join(SettlementLine, SettlementLine.settlement_run_id == SettlementRun.id)
            .where(SettlementRun.organization_id == self._org_id)
            .where(SettlementRun.status == "finalized")
            .where(
                SettlementRun.run_type.in_(
                    ["WEG_JAHRESABRECHNUNG", "VERMOEGENSBERICHT"]
                )
            )
            .where(SettlementLine.addressee_owner_profile_id.in_(owner_ids))
            .distinct()
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def finalized_plans_for_owners(
        self, owner_ids: list[uuid.UUID]
    ) -> Sequence[EconomicPlan]:
        """Finalized Wirtschaftsplaene that carry a line for one of the owners."""
        if not owner_ids:
            return []
        stmt = (
            select(EconomicPlan)
            .join(
                EconomicPlanLine,
                EconomicPlanLine.economic_plan_id == EconomicPlan.id,
            )
            .where(EconomicPlan.organization_id == self._org_id)
            .where(EconomicPlan.status == "finalized")
            .where(EconomicPlanLine.owner_profile_id.in_(owner_ids))
            .distinct()
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def issued_dunning(
        self,
        tenant_ids: list[uuid.UUID],
        owner_ids: list[uuid.UUID],
    ) -> Sequence[DunningNotice]:
        """Issued dunning notices addressed to the person's tenant/owner profiles."""
        conds = []
        if tenant_ids:
            conds.append(DunningNotice.tenant_profile_id.in_(tenant_ids))
        if owner_ids:
            conds.append(DunningNotice.owner_profile_id.in_(owner_ids))
        if not conds:
            return []
        stmt = (
            select(DunningNotice)
            .where(DunningNotice.organization_id == self._org_id)
            .where(DunningNotice.status == "issued")
            .where(or_(*conds))
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    # ------------------------------------------------------------------
    # Live contractual relationship (blocks erasure)
    # ------------------------------------------------------------------
    async def has_active_lease(
        self, tenant_ids: list[uuid.UUID], reference_date: date
    ) -> bool:
        """A lease that is current or future: valid_to NULL or exclusive > ref."""
        if not tenant_ids:
            return False
        stmt = (
            select(func.count())
            .select_from(LeaseAgreement)
            .where(LeaseAgreement.organization_id == self._org_id)
            .where(LeaseAgreement.tenant_profile_id.in_(tenant_ids))
            .where(
                or_(
                    LeaseAgreement.valid_to.is_(None),
                    LeaseAgreement.valid_to > reference_date,
                )
            )
        )
        return (await self._session.execute(stmt)).scalar_one() > 0

    async def has_active_ownership(
        self, owner_ids: list[uuid.UUID], reference_date: date
    ) -> bool:
        if not owner_ids:
            return False
        stmt = (
            select(func.count())
            .select_from(OwnershipHistory)
            .where(OwnershipHistory.organization_id == self._org_id)
            .where(OwnershipHistory.owner_profile_id.in_(owner_ids))
            .where(
                or_(
                    OwnershipHistory.valid_to.is_(None),
                    OwnershipHistory.valid_to > reference_date,
                )
            )
        )
        return (await self._session.execute(stmt)).scalar_one() > 0
