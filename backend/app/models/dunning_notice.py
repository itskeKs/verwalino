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
  overridable at creation time. Interest is frozen only when a base rate is supplied.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, SmallInteger, String, select
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, column_property

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin
from app.models.dunning_scope_context import DunningScopeContext


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
    )  # principal + fee + interest

    interest_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )
    interest_snapshot: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)

    # Frozen snapshot of included overdue open items (list of dicts).
    # Each item: {period_year, period_month, payment_type, soll, ist, open_amount}
    # Immutable once issued -- the legally binding arrears breakdown.
    included_items: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Eingefrorene Anzeige-Namen des Adressaten (ADR-0012). Beim Ausstellen
    # (issue) gefüllt: {"schema_version": 1, "values": {person_name,
    # person_address, organization_name}}. Danach vom Trigger (0019/0022)
    # unveränderbar. NULL bei Altbestand -> Renderer fällt auf Live-Auflösung
    # zurück. Verhindert, dass ein späteres DSGVO-Anonymisieren der Person das
    # bereits ausgestellte, aufbewahrungspflichtige Mahn-PDF verändert.
    frozen_display_names: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Lifecycle: draft -> issued (frozen via ADR-0003 trigger).
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, default="draft"
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


# Read-only canonical scope; never a mutable attribute of the frozen letter.
DunningNotice.accounting_scope_id = column_property(
    select(DunningScopeContext.scope_id).where(
        DunningScopeContext.organization_id == DunningNotice.organization_id,
        DunningScopeContext.dunning_notice_id == DunningNotice.id,
    ).correlate_except(DunningScopeContext).scalar_subquery(), expire_on_flush=False
)
