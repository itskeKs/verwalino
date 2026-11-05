"""Dated household counts, with independent occupied and vacancy streams."""
from __future__ import annotations

import uuid
from datetime import date
from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, ForeignKeyConstraint, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class UnitHousehold(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = 'unit_household'
    __table_args__ = (
        ForeignKeyConstraint(['organization_id', 'unit_id'], ['unit.organization_id', 'unit.id'],
            name='fk_unit_household_unit_org', ondelete='RESTRICT'),
        UniqueConstraint('unit_id', 'is_vacancy', 'valid_from', name='uq_unit_household_stream_date'),
        CheckConstraint('persons >= 0', name='persons_nonnegative'),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('organization.id', ondelete='RESTRICT'), nullable=False, index=True)
    unit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    persons: Mapped[int] = mapped_column(Integer, nullable=False)
    is_vacancy: Mapped[bool] = mapped_column(Boolean, nullable=False)
