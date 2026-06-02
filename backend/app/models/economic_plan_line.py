"""EconomicPlanLine -- one planned position of a Wirtschaftsplan (m4 §1.4).

Either a planned cost category, the reserve contribution, or a per-owner annual/
monthly advance. The relational, queryable mirror of the plan's
snapshot_payload.result; both are written from the same PlanResult in the
finalize transaction.

Tenant-behaftet (organization_id, RLS). Frozen (no mutation) once the parent
economic_plan is finalized (ADR-0003 trigger checks the parent; migration 0015).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class EconomicPlanLine(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "economic_plan_line"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    economic_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("economic_plan.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # PLANNED_COST | RESERVE_CONTRIBUTION | OWNER_ADVANCE | SUBTOTAL
    line_type: Mapped[str] = mapped_column(String(24), nullable=False)

    cost_category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cost_category.id", ondelete="RESTRICT"),
        nullable=True,
    )
    cost_category_code: Mapped[str | None] = mapped_column(String(50), nullable=True)

    owner_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("owner_profile.id", ondelete="RESTRICT"),
        nullable=True,
    )
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("unit.id", ondelete="RESTRICT"),
        nullable=True,
    )

    label: Mapped[str] = mapped_column(String(255), nullable=False)

    distribution_key: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Money NUMERIC(14,2); quota/base NUMERIC(12,6) -- never float (I4).
    distribution_base_total: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6), nullable=True
    )
    distribution_base_share: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6), nullable=True
    )

    planned_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    # For OWNER_ADVANCE: the monthly Hausgeld amount (E4-3b, monatsbasiert).
    monthly_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    cost_group: Mapped[str | None] = mapped_column(String(20), nullable=True)

    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    # CHECK constraints + indices live in migration 0013.
