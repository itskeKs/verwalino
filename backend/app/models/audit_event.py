"""AuditEvent -- append-only event log.

Architecture.md section 3.2(d):
  - Append-only (INSERT only; no UPDATE/DELETE for app_rw).
  - Tenant-behaftet (organization_id), RLS active.
  - occurred_at is server-set.
  - Optional prev_event_hash for hash-chain integrity.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class AuditEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "audit_event"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )

    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    action: Mapped[str] = mapped_column(String(100), nullable=False)

    occurred_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        nullable=False,
    )

    payload_diff: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Optional hash-chain field for tamper detection
    prev_event_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
