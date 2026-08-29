"""Approved land-register and cadastral references for a property."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PropertyLegalReference(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "property_legal_reference"

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False)
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reference_type: Mapped[str] = mapped_column(String(48), nullable=False)
    district: Mapped[str | None] = mapped_column(String(255))
    cadastral_district: Mapped[str | None] = mapped_column(String(255))
    field_number: Mapped[str | None] = mapped_column(String(100))
    parcel: Mapped[str | None] = mapped_column(String(100))
    sheet: Mapped[str | None] = mapped_column(String(100))
    details_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="EFFECTIVE")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_import_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "property_id"], ["property.organization_id", "property.id"], name="fk_property_legal_reference_property_tenant", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "source_import_id"], ["declaration_import.organization_id", "declaration_import.id"], name="fk_property_legal_reference_import_tenant", ondelete="RESTRICT"),
        CheckConstraint("status IN ('EFFECTIVE', 'SUPERSEDED', 'NEEDS_CLARIFICATION')", name=conv("ck_property_legal_reference_status")),
        CheckConstraint("valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from", name=conv("ck_property_legal_reference_validity")),
        Index("ix_property_legal_reference_org_property", "organization_id", "property_id"),
        Index("ix_property_legal_reference_source_import", "source_import_id"),
    )
