"""Resolution -- a decision (Beschluss) made at an owners' meeting.

M8 Eigentümerversammlung & Beschluss-Sammlung; SDD section 6.10.
Tenant-behaftet: carries organization_id, RLS active.

A resolution records the exact wording (Wortlaut), the vote counts, and
the result. It is optionally linked to an agenda item (TOP).

Vote counts are captured as integers (Anzahl der Stimmen). Additionally,
MEA sums (votes_yes_mea, votes_no_mea, votes_abstain_mea) can be recorded
for transparency but are NOT used for automatic majority enforcement.

[section 18/fachlich] Mehrheitsregeln (WEG section 25 einfache Mehrheit,
qualifizierte Mehrheiten, Allstimmigkeit) werden NICHT automatisch erzwungen.
Stimmen + Ergebnis sind Verwalterangabe. Dies ist eine bewusste fachliche
Entscheidung: die konkreten Mehrheitsregeln variieren je nach Beschlussgegenstand,
Teilungserklärung und Gemeinschaftsordnung.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class Resolution(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "resolution"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("owners_meeting.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Optional link to agenda item (TOP)
    agenda_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agenda_item.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # The exact wording of the resolution (Wortlaut)
    wording: Mapped[str] = mapped_column(Text, nullable=False)

    # Vote counts (Anzahl)
    votes_yes: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    votes_no: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    votes_abstain: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    # Optional MEA-based vote sums (for WEG transparency, NOT for enforcement)
    votes_yes_mea: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6), nullable=True
    )
    votes_no_mea: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6), nullable=True
    )
    votes_abstain_mea: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6), nullable=True
    )

    # Result: ANGENOMMEN (accepted) | ABGELEHNT (rejected)
    result: Mapped[str] = mapped_column(String(12), nullable=False)

    # Informational: the type of majority required/applied
    # [section 18/fachlich] purely informational, NOT enforced
    majority_type: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "result IN ('ANGENOMMEN', 'ABGELEHNT')",
            name="ck_resolution_result",
        ),
        CheckConstraint("votes_yes >= 0", name="ck_resolution_votes_yes"),
        CheckConstraint("votes_no >= 0", name="ck_resolution_votes_no"),
        CheckConstraint("votes_abstain >= 0", name="ck_resolution_votes_abstain"),
    )
