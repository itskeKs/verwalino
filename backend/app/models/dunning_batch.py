"""Immutable actor-scoped identities for atomically prepared dunning drafts."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKeyConstraint, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DunningBatch(Base):
    __tablename__ = "dunning_batch"

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp())

    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "actor_user_id"], ["user.organization_id", "user.id"]),
    )
