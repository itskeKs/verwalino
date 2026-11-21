"""One model request of the model-assisted invoice path: its reservation and, once it finished, its usage.

The ledger of the two stages (``read_page`` for a page, ``extract_invoice`` for the document). The worker reserves a
call before it leaves and settles it with the consumed usage afterwards; the organization's monthly total counts a
RESERVED row at its reservation and a SETTLED row at its cost (migration 0197). Working and accounting data of the
runtime: not part of the organization export.
"""
import uuid
from datetime import datetime
from decimal import Decimal
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKeyConstraint, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, UUIDPrimaryKeyMixin


class InvoiceModelCall(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "invoice_model_call"
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    attempt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    stage: Mapped[str] = mapped_column(String, nullable=False)
    pdf_page: Mapped[int | None] = mapped_column(Integer)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="RESERVED")
    reserved_input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 10), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 10))
    usage_complete: Mapped[bool | None] = mapped_column(Boolean)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "attempt_id"], ["invoice_processing_attempt.organization_id", "invoice_processing_attempt.id"],
                             name="fk_invoice_model_call_attempt", ondelete="RESTRICT"),
        UniqueConstraint("organization_id", "id", name="uq_invoice_model_call_org_id"),
    )
