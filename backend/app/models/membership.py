"""Membership -- connects a User with an Organization and a Role.

Under assumption A-2 (one user = one org), the role could live on User directly.
We model it as a separate entity to keep the n:m extension path open.

Tenant-behaftet: carries organization_id, RLS active.
Constraint: UNIQUE(organization_id, user_id) under A-2 (one role per user per org).
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class Membership(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "membership"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Fixed enum: OWNER_ADMIN | CLERK | READ_ONLY
    role: Mapped[str] = mapped_column(String(20), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "user_id", name="uq_membership_org_user"
        ),
    )
