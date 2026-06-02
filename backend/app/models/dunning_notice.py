"""DunningNotice -- a dunning letter per person for overdue open items.

M7 Mahnwesen; ADR-0003 trigger pattern (immutable once issued).
Tenant-behaftet (organization_id, RLS). Lifecycle: draft -> issued (frozen).
An issued dunning notice is never edited; the DB trigger (migration 0019)
enforces this -- same mechanic as cost_entry/payment/settlement_run.

The included_items JSONB column is a snapshot of the overdue open items at
creation time. Once issued, the snapshot is frozen and represents the
legally binding content of the dunning letter -- even if the underlying
payments change later (same snapshot philosophy as settlement_run).

Dunning levels (locked decision):
  1 = Zahlungserinnerung (payment reminder)
  2 = Mahnung (dunning)
  3 = letzte Mahnung (final dunning)

Dunning fee per level [section 18/fachlich -- provisional defaults]:
  Level 1: 0.00 EUR
  Level 2: 5.00 EUR
  Level 3: 10.00 EUR
  Fees must be appropriate / contractually agreed; provisionally hardcoded,
  overridable at creation time. Late payment interest (Verzugszinsen) is
  NOT in the MVP [section 18/fachlich].
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, SmallInteger, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class DunningNotice(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "dunning_notice"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Counterparty: exactly one of tenant_profile_id / owner_profile_id set.
    tenant_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant_profile.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    owner_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("owner_profile.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    # Dunning level: 1 = Zahlungserinnerung, 2 = Mahnung, 3 = letzte Mahnung.
    level: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    # Dates
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Amounts (Decimal, 14,2 -- money discipline)
    principal_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False
    )  # Sum of overdue arrears
    fee_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False
    )  # Dunning fee [section 18/fachlich: provisional]
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False
    )  # principal + fee

    # Frozen snapshot of included overdue open items (list of dicts).
    # Each item: {period_year, period_month, payment_type, soll, ist, open_amount}
    # Immutable once issued -- the legally binding arrears breakdown.
    included_items: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Lifecycle: draft -> issued (frozen via ADR-0003 trigger).
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, default="draft"
    )
    issued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    issued_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )

    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # CHECK constraints + trigger live in migration 0019.
