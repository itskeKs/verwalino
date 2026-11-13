"""UserRecoveryCode -- one-time second-factor fallback codes (issue #208).

Ten codes per confirmed enrolment, shown once and stored only as keyed hashes.
Each code works exactly once; used_at records when.

Tenant-behaftet: carries organization_id, RLS active.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class UserRecoveryCode(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "user_recovery_code"

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

    position: Mapped[int] = mapped_column(Integer, nullable=False)

    # HMAC-SHA256 keyed with the deployment secret; never the plaintext code.
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", "position", name="uq_user_recovery_code_org_user_position"),
    )
