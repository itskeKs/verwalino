"""LeaseAgreement -- a rental contract for a unit.

SDD section 6.2, section 14 -- chronological integrity (I3).
Architecture.md section 5.3.

Key constraints:
  - valid_from DATE NOT NULL, valid_to DATE NULL (NULL = open-ended).
  - EXCLUDE constraint (btree_gist): no overlapping lease periods PER UNIT
    (one unit is rented to at most one party at a time).
  - Gaps (vacancy) are allowed.

Tenant-behaftet: carries organization_id, RLS active.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class LeaseAgreement(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "lease_agreement"
    __table_args__ = (UniqueConstraint("organization_id", "id", name="uq_lease_agreement_org_id"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    unit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("unit.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    tenant_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant_profile.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Money fields -- NUMERIC(14,2), never float (I4)
    base_rent: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    advance_payment: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    deposit: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    notes: Mapped[str] = mapped_column(String(2000), nullable=False, default="")

    # EXCLUDE constraint is defined in the migration (requires btree_gist extension
    # and raw DDL).  See migration 0003_m1_stammdaten.py.
    #
    # The constraint ensures: for the SAME unit_id, date ranges
    # [valid_from, valid_to) do not overlap.
    # One unit can have at most one active lease at any point in time.
