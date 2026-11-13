"""UserSecondFactor -- TOTP enrolment state of a staff account (issue #208).

Tenant-behaftet: carries organization_id, RLS active.
One row per user: the confirmed factor, or the pending enrolment secret
(confirmed=false) until the user confirms with one code.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class UserSecondFactor(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "user_second_factor"

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

    # Fernet ciphertext (app.services.second_factor_service); never plaintext.
    secret_encrypted: Mapped[str] = mapped_column(String(255), nullable=False)

    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Replay protection: the newest accepted time step; a code of this or an
    # older step is refused.
    last_totp_step: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_user_second_factor_org_user"),
    )
