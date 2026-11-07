"""Shared settlement allocation rule; the confirmed source cost stays immutable."""
import uuid
from decimal import Decimal
from sqlalchemy import ForeignKey, ForeignKeyConstraint, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CommercialPreallocation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = 'commercial_preallocation'
    __table_args__ = (
        UniqueConstraint('organization_id', 'cost_entry_id'),
        ForeignKeyConstraint(['organization_id', 'cost_entry_id'], ['cost_entry.organization_id', 'cost_entry.id']),
        ForeignKeyConstraint(['organization_id', 'property_id'], ['property.organization_id', 'property.id']),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('organization.id'), nullable=False)
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    cost_entry_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    commercial_share_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    commercial_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    commercial_reason: Mapped[str] = mapped_column(String(2000), nullable=False)
