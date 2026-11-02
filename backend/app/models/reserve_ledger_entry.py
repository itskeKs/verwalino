"""Append-only reserve adjustments and explicit funding evidence."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, ForeignKeyConstraint, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class ReserveLedgerEntry(Base, UUIDPrimaryKeyMixin):
    __tablename__ = 'reserve_ledger_entry'
    __table_args__ = tuple(ForeignKeyConstraint(
        ['organization_id', column], [f'{target}.organization_id', f'{target}.id'],
        ondelete='RESTRICT') for column, target in (
            ('property_id', 'property'), ('settlement_run_id', 'settlement_run'),
            ('payment_id', 'payment'), ('unit_id', 'unit'), ('cost_entry_id', 'cost_entry'),
            ('register_entry_id', 'resolution_register_entry')))

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('organization.id'), nullable=False)
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    settlement_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    component: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    payment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    unit_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    cost_entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    register_entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('user.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
