"""BankTransaction -- one imported bank statement line.

T15: Bankumsätze Tabelle.
Bank statement fields (the concrete import profile is selected separately): account (FK to bank_account),
booking_date, value_date, amount, counterparty_name, counterparty_iban,
purpose, import_hash (unique per account), status (NEU/ZUGEORDNET/IGNORIERT).
Tenant-behaftet (organization_id, RLS).

Optional link to payment via payment.bank_transaction_id.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, ForeignKeyConstraint, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class BankTransaction(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "bank_transaction"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Link to the bank account this transaction belongs to.
    bank_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bank_account.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Booking and value dates.
    booking_date: Mapped[date] = mapped_column(Date, nullable=False)
    value_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Signed amount: positive = credit to account, negative = debit from account.
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    # Counterparty information.
    counterparty_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    counterparty_iban: Mapped[str | None] = mapped_column(String(34), nullable=True)

    # Purpose/reference text.
    purpose: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Import hash for idempotent imports (unique per account).
    import_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # Status: NEU (new), ZUGEORDNET (assigned to payment), IGNORIERT (ignored).
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, default="NEU"
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "bank_account_id"],
            ["bank_account.organization_id", "bank_account.id"],
            name="fk_bank_transaction_account_org", ondelete="RESTRICT",
        ),
        UniqueConstraint("organization_id", "bank_account_id", "id", name="uq_bank_transaction_org_account_id"),
        # Unique import_hash per account prevents duplicate imports.
        UniqueConstraint(
            "bank_account_id",
            "import_hash",
            name="uq_bank_transaction_account_import",
        ),
        # Status must be one of the allowed values.
        CheckConstraint(
            "status IN ('NEU', 'ZUGEORDNET', 'IGNORIERT')",
            name="ck_bank_transaction_status",
        ),
    )
