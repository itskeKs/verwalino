"""Meter and MeterReading -- optional consumption meters.

SDD section 6.2.1.
Model anchor; logic comes later with distribution engine (M3).
Tenant-behaftet: carries organization_id, RLS active.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class Meter(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "meter"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # A meter can belong to a property (common area) or a specific unit
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("property.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("unit.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    meter_number: Mapped[str] = mapped_column(String(100), nullable=False)
    meter_type: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. KALTWASSER, STROM
    location: Mapped[str] = mapped_column(String(255), nullable=False, default="")


class MeterReading(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "meter_reading"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    meter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("meter.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    reading_date: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    period: Mapped[str | None] = mapped_column(String(50), nullable=True)  # e.g. "2025"
