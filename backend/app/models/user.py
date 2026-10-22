"""User -- an authentication account, scoped to one Organization (assumption A-2).

Tenant-behaftet: carries organization_id, RLS active.
Constraint: UNIQUE(organization_id, email) -- email unique per org, not globally.
"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "user"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    session_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        CheckConstraint("session_version >= 0", name="ck_user_session_version_nonnegative"),
        UniqueConstraint("organization_id", "id", name="uq_user_organization_id_id"),
        UniqueConstraint("organization_id", "email", name="uq_user_org_email"),
    )
