"""Property -- a building/object within an Organization.

SDD section 6.1, architecture.md section 5.3.
Verwaltungsart: MIET | WEG | SEV | MISCH.
Tenant-behaftet: carries organization_id, RLS active.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Integer,
    Numeric,
    CheckConstraint,
    ForeignKey,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class Property(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "property"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Address fields
    street: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    zip_code: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    city: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    # Verwaltungsart: MIET | WEG | SEV | MISCH
    management_type: Mapped[str] = mapped_column(String(5), nullable=False)

    # Abrechnungszeitraum convention: month the fiscal year starts (1-12).
    # Default 1 = calendar year (Jan-Dec).
    fiscal_year_start_month: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="1"
    )

    construction_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_area: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    mea_denominator: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mea_scale_status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="LEGACY_UNCONFIRMED"
    )

    __table_args__ = (
        CheckConstraint("construction_year BETWEEN 1 AND 9999", name="ck_property_construction_year"),
        CheckConstraint("total_area >= 0 AND total_area < 1000000000000", name="ck_property_total_area"),
        UniqueConstraint(
            "organization_id", "id", name="uq_property_organization_id_id"
        ),
        CheckConstraint(
            "management_type IN ('MIET', 'WEG', 'SEV', 'MISCH')",
            name="ck_property_management_type",
        ),
        CheckConstraint(
            "fiscal_year_start_month BETWEEN 1 AND 12",
            name="ck_property_fiscal_month",
        ),
    )
