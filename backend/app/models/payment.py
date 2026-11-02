"""Payment -- one captured money movement (Geldbewegung), a GoBD booking record.

m6-zahlungen-mietkonto.md section 1.2; ADR-0009 (unveraenderbar ab booked,
Storno statt Edit), ADR-0010 (Vermoegens-Kohaerenz per Composite-FK).
Tenant-behaftet (organization_id, RLS). Immutable once status='booked'
(ADR-0003 trigger, migration 0018) -- same mechanic as cost_entry/settlement_run.

Lifecycle: draft --book--> booked (frozen). A booked payment is never edited;
correction = Storno (a new booked row with reverses_id) + Neubuchung. The single
allowed mutation of a booked row is setting reversed_by_id (the Storno chain,
forward pointer like cost_entry.superseded_by_id) + updated_at.

Vermoegenstrennung (ADR-0010): a payment runs against exactly one bank_account;
property_id/management_mandate_id FOLLOW from the account (not freely chosen) and
are bound to it by the Composite-FK
  (organization_id, bank_account_id, property_id)
  -> bank_account(organization_id, id, property_id)
so "payment on account X claims property Y though X belongs to Z" is DB-impossible.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, ForeignKeyConstraint, Numeric, SmallInteger, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class Payment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "payment"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Vermoegenstrennung anchor: a payment runs against exactly one account.
    bank_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bank_account.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # Mirrored from the account; bound by the Composite-FK (migration 0016).
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("property.id", ondelete="RESTRICT"),
        nullable=False,
    )
    management_mandate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("management_mandate.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # Money movement (I4). amount always positive; direction carries the sign.
    direction: Mapped[str] = mapped_column(String(3), nullable=False)  # IN | OUT
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    value_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Counterparty (exactly one role for personenbezogene types; CHECK in 0016).
    tenant_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    owner_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Zweck/Typ -- the steering key for the abrechnungs-anbindung (section 6).
    payment_type: Mapped[str] = mapped_column(String(24), nullable=False)

    # Zuordnung (what the payment settles).
    lease_agreement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    period_year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    period_month: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    settlement_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    reserve_cost_entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reserve_register_entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # GoBD / immutability (ADR-0003/0009).
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, default="draft"
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    booked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    booked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Storno chain (self-FK). reverses_id on the Storno row; reversed_by_id is
    # the forward pointer on the reversed original (E-4 convention).
    reverses_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payment.id", ondelete="RESTRICT"),
        nullable=True,
    )
    reversed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payment.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # Aufbewahrungs-Anker (8 Jahre, architecture.md section 3.3 / ADR-0003 #3).
    retention_until: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Optional link to bank transaction for import tracking (T15).
    bank_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(['organization_id', 'reserve_cost_entry_id'], ['cost_entry.organization_id', 'cost_entry.id'], name='fk_reserve_payment_cost'),
        ForeignKeyConstraint(['organization_id', 'reserve_register_entry_id'], ['resolution_register_entry.organization_id', 'resolution_register_entry.id'], name='fk_reserve_payment_register'),
        ForeignKeyConstraint(["organization_id", "tenant_profile_id"], ["tenant_profile.organization_id", "tenant_profile.id"],
            name="fk_payment_tenant_profile_id_tenant", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "owner_profile_id"], ["owner_profile.organization_id", "owner_profile.id"],
            name="fk_payment_owner_profile_id_tenant", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "lease_agreement_id"], ["lease_agreement.organization_id", "lease_agreement.id"],
            name="fk_payment_lease_agreement_id_tenant", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "settlement_run_id"], ["settlement_run.organization_id", "settlement_run.id"],
            name="fk_payment_settlement_run_id_tenant", ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["organization_id", "bank_account_id", "bank_transaction_id"],
            ["bank_transaction.organization_id", "bank_transaction.bank_account_id", "bank_transaction.id"],
            name="fk_payment_bank_transaction_coherence", ondelete="RESTRICT",
        ),
    )

    # Further CHECK constraints, Composite-FK + indices live in migration 0016.
