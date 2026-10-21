"""Append-only special levy and frozen due-date allocation basis."""
from datetime import date
from decimal import Decimal
import uuid

from sqlalchemy import Date, ForeignKeyConstraint, Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class SpecialLevy(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "special_levy"
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    accounting_scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    resolution_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    __table_args__ = (
        UniqueConstraint("organization_id", "property_id", "id", name="uq_special_levy_context"),
        ForeignKeyConstraint(["organization_id", "property_id", "accounting_scope_id"],
            ["accounting_scope.organization_id", "accounting_scope.property_id", "accounting_scope.id"]),
    )


class SpecialLevyAllocation(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "special_levy_allocation"
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    special_levy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    owner_profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    unit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    ownership_history_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    mea: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "property_id", "special_levy_id"],
            ["special_levy.organization_id", "special_levy.property_id", "special_levy.id"]),
        ForeignKeyConstraint(["organization_id", "property_id", "unit_id"],
            ["unit.organization_id", "unit.property_id", "unit.id"]),
        ForeignKeyConstraint(["organization_id", "owner_profile_id"],
            ["owner_profile.organization_id", "owner_profile.id"]),
        UniqueConstraint("special_levy_id", "unit_id", "owner_profile_id", name="uq_special_levy_owner_unit"),
    )
