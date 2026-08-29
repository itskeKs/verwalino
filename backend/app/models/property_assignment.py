"""Approved special-use rights and physical assignments."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PropertyAssignment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "property_assignment"

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False)
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    unit_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False)
    assignment_type: Mapped[str] = mapped_column(String(64), nullable=False)
    designation: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(4000))
    details_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="EFFECTIVE")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_import_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("organization_id", "property_id", "stable_key", name="uq_property_assignment_property_stable_key"),
        ForeignKeyConstraint(["organization_id", "property_id"], ["property.organization_id", "property.id"], name="fk_property_assignment_property_tenant", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "property_id", "unit_id"], ["unit.organization_id", "unit.property_id", "unit.id"], name="fk_property_assignment_unit_property_tenant", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "source_import_id"], ["declaration_import.organization_id", "declaration_import.id"], name="fk_property_assignment_import_tenant", ondelete="RESTRICT"),
        CheckConstraint("status IN ('EFFECTIVE', 'SUPERSEDED', 'NEEDS_CLARIFICATION')", name=conv("ck_property_assignment_status")),
        CheckConstraint("valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from", name=conv("ck_property_assignment_validity")),
        Index("ix_property_assignment_org_property", "organization_id", "property_id"),
        Index("ix_property_assignment_unit_property", "organization_id", "property_id", "unit_id"),
        Index("ix_property_assignment_source_import", "source_import_id"),
    )
