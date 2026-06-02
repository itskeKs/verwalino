"""ResolutionRegisterEntry -- Beschluss-Sammlung (WEG section 24 Abs. 7).

M8 Eigentümerversammlung & Beschluss-Sammlung; SDD section 6.10.
ADR-0011: Register-Unveränderbarkeit + Lückenlose Nummerierung.
Tenant-behaftet: carries organization_id, RLS active.

KEY INVARIANTS (DB-enforced):
  1. UNIQUE (organization_id, property_id, sequence_number) -- no duplicates,
     no gaps (gaps prevented by service-layer max+1; uniqueness prevents duplicates;
     collision => error/retry).
  2. IMMUTABLE once created: wording, sequence_number, entry_date, resolution_id,
     property_id, organization_id, location are NEVER changed.
     The ONLY allowed mutation is setting/updating the challenge_annotation
     (Anfechtungs-/Aufhebungsvermerk).
  3. DELETE is ALWAYS forbidden (DB trigger).

The immutability trigger (migration 0020) follows ADR-0003 pattern but with
a stricter variant: the entry is immutable from INSERT, not from a status
transition. Only challenge_annotation and updated_at can be mutated.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class ResolutionRegisterEntry(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "resolution_register_entry"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("property.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Lückenlos fortlaufende Nummer je WEG (property)
    sequence_number: Mapped[int] = mapped_column(nullable=False)

    # Date the entry was added to the register
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Location of the meeting (copied from the meeting)
    location: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    # The exact wording (Wortlaut) -- COPY from resolution, immutable
    wording: Mapped[str] = mapped_column(Text, nullable=False)

    # Reference back to the resolution
    resolution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resolution.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Anfechtungs-/Aufhebungsvermerk -- the ONLY mutable field after creation.
    # NULL = no challenge; set to a text describing the challenge/annulment.
    challenge_annotation: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "property_id", "sequence_number",
            name="uq_register_entry_org_prop_seq",
        ),
    )
