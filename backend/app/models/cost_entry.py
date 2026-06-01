"""CostEntry -- a cost position (Kostenposition).

SDD section 5.3/6.4, m2-gobd-belegfluss.md section 2.
Tenant-behaftet (organization_id, RLS).
Immutable once status='confirmed' (ADR-0003 trigger).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import DateTime, Date, ForeignKey, Numeric, SmallInteger, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class CostEntry(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "cost_entry"

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

    cost_category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cost_category.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    cost_category_plan_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False
    )

    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vendor.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    service_period_from: Mapped[date] = mapped_column(Date, nullable=False)
    service_period_to: Mapped[date] = mapped_column(Date, nullable=False)

    # Money fields -- NUMERIC(14,2), never float (I4)
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    vat_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    vat_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    document_number: Mapped[str | None] = mapped_column(String(100), nullable=True)

    owner_distribution_key: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    tenant_distribution_key: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )

    tenant_apportionable: Mapped[str] = mapped_column(String(30), nullable=False)

    apportionable_tenant_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True
    )

    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(12), nullable=False)

    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Correction chain -- forward pointer (decision E-4)
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cost_entry.id", ondelete="RESTRICT"),
        nullable=True,
    )
