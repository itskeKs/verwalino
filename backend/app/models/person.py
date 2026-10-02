"""Person -- base contact entity with DSGVO fields.

SDD section 6.2, architecture.md section 5.3 / 3.3.
A person can have multiple roles (owner, tenant, vendor) via profile tables.
Tenant-behaftet: carries organization_id, RLS active.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class Person(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "person"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Actual creator only; historical custody is never inferred.
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # Contact data
    first_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    last_name: Mapped[str] = mapped_column(String(255), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Address
    street: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    zip_code: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    city: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    # Optional bank data (DSGVO-relevant, minimiert)
    iban: Mapped[str | None] = mapped_column(String(34), nullable=True)
    bic: Mapped[str | None] = mapped_column(String(11), nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # DSGVO fields (architecture.md section 3.3)
    deletion_requested_at: Mapped[datetime | None] = mapped_column(nullable=True)
    processing_restricted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    anonymized_at: Mapped[datetime | None] = mapped_column(nullable=True)
