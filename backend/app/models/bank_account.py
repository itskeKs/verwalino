"""BankAccount -- model anchor for Vermoegenstrennungs (asset separation).

SDD section 6.3, architecture.md section 5.3.
Account types: LAUFEND | RUECKLAGE | KAUTION.
Full implementation in M6; this is a model anchor (schema + FK only).
Tenant-behaftet: carries organization_id, RLS active.
"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class BankAccount(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "bank_account"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Link to the property (Vermoegenstrennungs anchor)
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("property.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    account_type: Mapped[str] = mapped_column(String(10), nullable=False)

    label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    iban: Mapped[str | None] = mapped_column(String(34), nullable=True)
    bic: Mapped[str | None] = mapped_column(String(11), nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_bank_account_org_id"),
        CheckConstraint(
            "account_type IN ('LAUFEND', 'RUECKLAGE', 'KAUTION')",
            name="ck_bank_account_type",
        ),
    )
