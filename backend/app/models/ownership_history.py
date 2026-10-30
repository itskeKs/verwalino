"""OwnershipHistory -- temporal ownership record for a unit.

SDD section 6.2, section 14 -- core of chronological integrity (I3).
Architecture.md section 5.3.

Key constraints:
  - valid_from DATE NOT NULL, valid_to DATE NULL (NULL = open-ended).
  - EXCLUDE constraint (btree_gist): no overlapping periods for the SAME
    (unit_id, owner_profile_id) combination.
  - Multiple co-owners per unit at the same time ARE allowed (different
    owner_profiles with mea_share).  Architecture section 5.3.
  - Gaps (vacancy) are allowed.
  - mea_share sum per unit/time is NOT hard-enforced (soft validation only --
    real-world data, rounding, partial capture).

Tenant-behaftet: carries organization_id, RLS active.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, ForeignKeyConstraint, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class OwnershipHistory(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ownership_history"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "unit_id"], ["unit.organization_id", "unit.id"],
            name="fk_ownership_history_unit_id_tenant", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "owner_profile_id"], ["owner_profile.organization_id", "owner_profile.id"],
            name="fk_ownership_history_owner_profile_id_tenant", ondelete="RESTRICT"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    unit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    owner_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Share of this owner in the unit (for co-ownership / Bruchteilseigentum)
    # NUMERIC(12,6) -- exact, never float (I4)
    mea_share: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)

    # EXCLUDE constraint is defined in the migration (requires btree_gist extension
    # and raw DDL -- cannot be expressed via SQLAlchemy ORM declaratively).
    # See migration 0003_m1_stammdaten.py.
    #
    # The constraint ensures: for the SAME (unit_id, owner_profile_id), date ranges
    # [valid_from, valid_to) do not overlap.
    # Multiple different owner_profiles for the same unit at the same time ARE allowed.
