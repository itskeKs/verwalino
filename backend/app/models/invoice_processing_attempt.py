"""Durable, fenced invoice extraction attempts; originals stay independent."""
import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKeyConstraint, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, UUIDPrimaryKeyMixin


class InvoiceProcessingAttempt(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "invoice_processing_attempt"
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    initiated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    requested_scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    processor_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(['organization_id','initiated_by'], ['user.organization_id','user.id'], name='fk_invoice_attempt_initiator'),
        ForeignKeyConstraint(['organization_id','requested_scope_id'], ['accounting_scope.organization_id','accounting_scope.id'], name='fk_invoice_attempt_scope'),
        ForeignKeyConstraint(["organization_id", "document_id"], ["invoice_document.organization_id", "invoice_document.id"],
                             name="fk_invoice_processing_document_tenant", ondelete="RESTRICT"),
        UniqueConstraint("organization_id", "document_id", "attempt_no", name="uq_invoice_processing_attempt"),
    )
