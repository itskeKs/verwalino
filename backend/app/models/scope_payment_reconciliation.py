"""Immutable receipts linked to the exact proven bank/payment sidecars."""
import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKeyConstraint, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, UUIDPrimaryKeyMixin


class ScopePaymentReconciliation(Base, UUIDPrimaryKeyMixin):
    __tablename__ = 'scope_payment_reconciliation'
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    request_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    request_digest: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint('organization_id', 'id'),
        UniqueConstraint('organization_id', 'actor_user_id', 'request_key'),
        ForeignKeyConstraint(['organization_id','scope_id'], ['accounting_scope.organization_id','accounting_scope.id']),
        ForeignKeyConstraint(['organization_id','actor_user_id'], ['user.organization_id','user.id']),
    )


class ScopePaymentReconciliationItem(Base):
    __tablename__ = 'scope_payment_reconciliation_item'
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    reconciliation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    binding_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source_digest: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(['organization_id','reconciliation_id'], ['scope_payment_reconciliation.organization_id','scope_payment_reconciliation.id']),
        ForeignKeyConstraint(['organization_id','binding_id'], ['accounting_scope_binding.organization_id','accounting_scope_binding.id']),
    )
