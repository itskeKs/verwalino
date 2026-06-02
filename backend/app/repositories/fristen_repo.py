"""Fristen repository -- read-only aggregation for the M9 Fristenmonitor.

No mutation. Every method is a tenant-scoped SELECT that feeds the pure
``app.engine.fristen`` engine (the service maps these ORM rows to the engine's
frozen dataclasses). Keeping the queries here, explicit and auditable, mirrors
``settlement_repo``'s read-aggregation role (SDD section 16).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Sequence

from sqlalchemy import func, or_, select

from app.models.cost_entry import CostEntry
from app.models.economic_plan import EconomicPlan
from app.models.invoice_document import InvoiceDocument
from app.models.lease_agreement import LeaseAgreement
from app.models.property import Property
from app.models.settlement_run import SettlementRun
from app.models.unit import Unit


class FristenRepository:
    def __init__(self, session, org_id: uuid.UUID):
        self._session = session
        self._org_id = org_id

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    async def list_properties(self) -> Sequence[Property]:
        stmt = (
            select(Property)
            .where(Property.organization_id == self._org_id)
            .order_by(Property.name)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    # ------------------------------------------------------------------
    # Confirmed cost service-periods (to derive candidate fiscal years)
    # ------------------------------------------------------------------
    async def confirmed_cost_periods(
        self, property_id: uuid.UUID
    ) -> list[tuple[date, date]]:
        """Service periods of the active+confirmed cost entries of a property.

        Active chain = status='confirmed' AND superseded_by_id IS NULL (m2 3.4).
        The service buckets these into fiscal years (property convention).
        """
        stmt = (
            select(CostEntry.service_period_from, CostEntry.service_period_to)
            .where(CostEntry.organization_id == self._org_id)
            .where(CostEntry.property_id == property_id)
            .where(CostEntry.status == "confirmed")
            .where(CostEntry.superseded_by_id.is_(None))
        )
        result = await self._session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    # ------------------------------------------------------------------
    # Finalized settlement runs by type (BK / WEG / Vermoegensbericht)
    # ------------------------------------------------------------------
    async def finalized_runs(
        self, property_id: uuid.UUID, run_type: str
    ) -> Sequence[SettlementRun]:
        """Active (non-superseded) FINALIZED runs of a type for a property."""
        stmt = (
            select(SettlementRun)
            .where(SettlementRun.organization_id == self._org_id)
            .where(SettlementRun.property_id == property_id)
            .where(SettlementRun.run_type == run_type)
            .where(SettlementRun.status == "finalized")
            .where(SettlementRun.superseded_by_id.is_(None))
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    # ------------------------------------------------------------------
    # Leases of a property (for BK relevance + coverage)
    # ------------------------------------------------------------------
    async def leases_for_property(
        self, property_id: uuid.UUID
    ) -> Sequence[LeaseAgreement]:
        stmt = (
            select(LeaseAgreement)
            .join(Unit, Unit.id == LeaseAgreement.unit_id)
            .where(LeaseAgreement.organization_id == self._org_id)
            .where(Unit.property_id == property_id)
            .order_by(LeaseAgreement.valid_from)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def all_leases(self) -> Sequence[LeaseAgreement]:
        """Every lease of the org (for the org-wide overdue-items count)."""
        stmt = select(LeaseAgreement).where(
            LeaseAgreement.organization_id == self._org_id
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    # ------------------------------------------------------------------
    # Finalized Wirtschaftsplaene
    # ------------------------------------------------------------------
    async def finalized_plan_years(self, property_id: uuid.UUID) -> set[int]:
        stmt = (
            select(EconomicPlan.plan_year)
            .where(EconomicPlan.organization_id == self._org_id)
            .where(EconomicPlan.property_id == property_id)
            .where(EconomicPlan.status == "finalized")
            .where(EconomicPlan.superseded_by_id.is_(None))
        )
        result = await self._session.execute(stmt)
        return {row[0] for row in result.all()}

    # ------------------------------------------------------------------
    # Retention anchor (nachrichtlich INFO)
    # ------------------------------------------------------------------
    async def retention_anchor(self) -> tuple[date | None, int]:
        """(earliest retention_until, count) over invoice_documents with a date.

        Surfaced as a single INFO row in the monitor (retention expiry ENABLES a
        later DSGVO purge, ADR-0012 -- never an action item).
        """
        count_stmt = (
            select(func.count())
            .select_from(InvoiceDocument)
            .where(InvoiceDocument.organization_id == self._org_id)
            .where(InvoiceDocument.retention_until.is_not(None))
        )
        count = (await self._session.execute(count_stmt)).scalar_one()
        min_stmt = (
            select(func.min(InvoiceDocument.retention_until))
            .where(InvoiceDocument.organization_id == self._org_id)
            .where(InvoiceDocument.retention_until.is_not(None))
        )
        earliest = (await self._session.execute(min_stmt)).scalar_one()
        return earliest, int(count)
