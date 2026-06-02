"""EconomicPlan -- a forward-looking Wirtschaftsplan for one WEG / year.

m4-weg-abrechnung.md section 1.2/1.3 (decision E4-2). SDD section 11/13 lists
EconomicPlan / EconomicPlanLine as their OWN entities (NOT a settlement_run
run_type): the plan is forward-looking, has no Ist costs and no Soll-Ist.

Tenant-behaftet (organization_id, RLS). Immutable once status='finalized'
(ADR-0003 trigger, migration 0015) -- same snapshot/hash/correction mechanic as
settlement_run, reused not duplicated.

Lifecycle: draft --finalize--> finalized (frozen snapshot + hash).
Correction = a NEW plan (superseded_by_id forward pointer, E-4), never a mutation.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, SmallInteger, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class EconomicPlan(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "economic_plan"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("property.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    management_mandate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("management_mandate.id", ondelete="RESTRICT"),
        nullable=True,
    )

    plan_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    # Fälligkeitstag of the monthly Hausgeld (display / future Sollstellung M6).
    monthly_due_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    # Planned TOTAL reserve contribution of the year (Vermoegensbildung, NOT a
    # cost umlage; m4 section 3). Money NUMERIC(14,2), never float (I4).
    reserve_contribution_total: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=Decimal("0.00")
    )

    # Snapshot / immutability (ADR-0003), mirror of settlement_run.
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="draft")
    snapshot_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    snapshot_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    engine_version: Mapped[str] = mapped_column(String(20), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finalized_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Correction chain -- forward pointer (decision E-4).
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("economic_plan.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # CHECK constraints + indices live in migration 0013.
