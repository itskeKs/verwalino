"""Append-only receipt for an atomic, replayable operation."""
import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, UUIDPrimaryKeyMixin


class OperationReceipt(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "operation_receipt"
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organization.id"), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("user.id"), nullable=False)
    operation: Mapped[str] = mapped_column(String(80), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    result_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint("organization_id", "actor_user_id", "operation", "key", name="uq_operation_receipt_key"),
        ForeignKeyConstraint(["organization_id", "result_id"], ["cost_entry.organization_id", "cost_entry.id"], name="fk_operation_receipt_cost_tenant", ondelete="RESTRICT"),
    )
