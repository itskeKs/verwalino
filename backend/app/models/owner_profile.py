"""OwnerProfile -- role profile linking a Person as a property owner.

SDD section 6.2, architecture.md section 5.3.
One person can have multiple role profiles (owner + tenant).
Tenant-behaftet: carries organization_id, RLS active.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class OwnerProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "owner_profile"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("person.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Optional notes about this owner role
    notes: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
