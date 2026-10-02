"""Immutable provenance for a financial letter, separate from its frozen body."""
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKeyConstraint, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class DunningScopeContext(Base):
    __tablename__ = 'dunning_scope_context'
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    dunning_notice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    lease_agreement_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    economic_plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False, server_default="PREPARED")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(['organization_id','dunning_notice_id'], ['dunning_notice.organization_id','dunning_notice.id'], deferrable=True, initially='DEFERRED'),
        ForeignKeyConstraint(['organization_id','scope_id'], ['accounting_scope.organization_id','accounting_scope.id']),
        ForeignKeyConstraint(['organization_id','lease_agreement_id'], ['lease_agreement.organization_id','lease_agreement.id']),
        ForeignKeyConstraint(['organization_id','economic_plan_id'], ['economic_plan.organization_id','economic_plan.id']),
        ForeignKeyConstraint(['organization_id','created_by'], ['user.organization_id','user.id']),
    )
