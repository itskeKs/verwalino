"""Unit -- an individual unit within a Property.

SDD section 6.1, architecture.md section 5.3.
Usage types: WOHNUNG | GEWERBE | STELLPLATZ | SONSTIGE.
Tenant-behaftet: carries organization_id, RLS active.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class Unit(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "unit"

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

    number: Mapped[str] = mapped_column(String(50), nullable=False)
    location: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    # Area in sqm -- NUMERIC(10,2), never float (I4)
    area: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    heated_area: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)

    # Usage type
    usage_type: Mapped[str] = mapped_column(String(12), nullable=False)

    # Miteigentumsanteil (MEA) -- exact, NUMERIC(12,6) (I4)
    mea: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "usage_type IN ('WOHNUNG', 'GEWERBE', 'STELLPLATZ', 'SONSTIGE')",
            name="ck_unit_usage_type",
        ),
    )
