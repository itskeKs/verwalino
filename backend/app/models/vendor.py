"""Vendor -- a service provider / supplier (Dienstleister/Lieferant).

SDD section 6.2: separate from person but references it.
Has vendor-specific fields like tax IDs.
Tenant-behaftet: carries organization_id, RLS active.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class Vendor(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "vendor"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("person.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Vendor-specific fields
    tax_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    vat_id: Mapped[str | None] = mapped_column(String(20), nullable=True)

    notes: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
