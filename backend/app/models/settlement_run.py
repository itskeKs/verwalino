"""SettlementRun -- one billing run for one addressee in one period.

m3-abrechnungs-engine.md section 1.4.
Tenant-behaftet (organization_id, RLS).
Immutable once status='finalized' (ADR-0003 trigger, migration 0011).

Lifecycle: draft --finalize--> finalized (frozen snapshot + hash).
Correction = a NEW run (superseded_by_id forward pointer, E-4), never a mutation.

run_type discriminator: BK_MIETER implemented in M3; WEG/Wirtschaftsplan/
Vermoegensbericht reserved in the vocabulary for M4 (same hull, section 1.3).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class SettlementRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "settlement_run"

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

    run_type: Mapped[str] = mapped_column(String(24), nullable=False)

    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    # Addressee (BK: exactly one lease/tenant)
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("unit.id", ondelete="RESTRICT"),
        nullable=True,
    )
    lease_agreement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lease_agreement.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    tenant_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant_profile.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # Soll/Ist (section 7). Money NUMERIC(14,2), never float (I4).
    advance_payments_total: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    advance_payments_source: Mapped[str] = mapped_column(
        String(12), nullable=False, default="MANUAL"
    )
    total_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    balance: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    # Snapshot / immutability (ADR-0003)
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, default="draft"
    )
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

    # Correction chain -- forward pointer (decision E-4)
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("settlement_run.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # CHECK constraints + indices live in migration 0009.
