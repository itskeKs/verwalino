"""Append-only, effective-dated full rental rates (T23 / RF07)."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, ForeignKeyConstraint, Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class LeaseRentChange(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "lease_rent_change"
    __table_args__ = (
        UniqueConstraint("lease_agreement_id", "valid_from", name="uq_lease_rent_change_lease_date"),
        ForeignKeyConstraint(
            ["organization_id", "lease_agreement_id"],
            ["lease_agreement.organization_id", "lease_agreement.id"],
            name="fk_lease_rent_change_lease_org", ondelete="RESTRICT",
        ),
        CheckConstraint("base_rent >= 0 AND base_rent < 'Infinity'::numeric", name="base_rent_finite_nonnegative"),
        CheckConstraint("advance_payment >= 0 AND advance_payment < 'Infinity'::numeric", name="advance_finite_nonnegative"),
        CheckConstraint("heating_advance_payment >= 0 AND heating_advance_payment < 'Infinity'::numeric", name="heating_advance_finite_nonnegative"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    lease_agreement_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    base_rent: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    advance_payment: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    heating_advance_payment: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal('0.00'), server_default='0')
