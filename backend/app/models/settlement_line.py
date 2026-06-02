"""SettlementLine -- one ausgewiesene position of a settlement (section 1.5).

Carries ALL per-position BGH-Pflichtangaben structured (not just the end
amount): total cost of the cost type, applied distribution key, key base
numerator/denominator, time share, and the addressee's rounded amount.

Tenant-behaftet (organization_id, RLS). Frozen (no mutation) once the parent
settlement_run is finalized (ADR-0003 trigger checks the parent; migration 0011).

This is the relational, queryable mirror of snapshot_payload.result.lines;
both are written from the SAME EngineResult in the finalize transaction, and a
test asserts they stay consistent (section 1.5 "doppelte Wahrheit, bewusst").
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class SettlementLine(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "settlement_line"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    settlement_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("settlement_run.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    line_type: Mapped[str] = mapped_column(String(24), nullable=False)

    # M4: per-owner addressee of a WEG line (m4 section 4.4). NULL for object-wide
    # lines (RESERVE_*, totals) and for BK_MIETER. Added in migration 0012.
    addressee_owner_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("owner_profile.id", ondelete="RESTRICT"),
        nullable=True,
    )

    cost_category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cost_category.id", ondelete="RESTRICT"),
        nullable=True,
    )
    cost_category_code: Mapped[str | None] = mapped_column(String(50), nullable=True)

    label: Mapped[str] = mapped_column(String(255), nullable=False)

    distribution_key: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Money NUMERIC(14,2); quota/base NUMERIC(12,6) -- never float (I4).
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    distribution_base_total: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6), nullable=True
    )
    distribution_base_share: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6), nullable=True
    )
    time_share_days: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    time_share_total_days: Mapped[int | None] = mapped_column(
        SmallInteger, nullable=True
    )

    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    sort_order: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    # CHECK constraints + indices live in migration 0009.
