"""ManagementMandate -- a business mandate within an Organization.

Types (SDD section 4): WEG, MIET, SEV.
Tenant-behaftet: carries organization_id, RLS active.
Constraint: end_date IS NULL OR end_date >= start_date.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import CheckConstraint, ForeignKey, String, Date, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class ManagementMandate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "management_mandate"
    __table_args__ = (UniqueConstraint("organization_id", "id", name="uq_scope_management_mandate_org_id"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Mandate type: WEG | MIET | SEV
    mandate_type: Mapped[str] = mapped_column(String(4), nullable=False)

    label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name="ck_mandate_dates",
        ),
        CheckConstraint(
            "mandate_type IN ('WEG', 'MIET', 'SEV')",
            name="ck_mandate_type",
        ),
    )
