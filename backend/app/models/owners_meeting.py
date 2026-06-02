"""OwnersMeeting -- an owners' assembly (Eigentümerversammlung) for a WEG property.

M8 Eigentümerversammlung & Beschluss-Sammlung; SDD section 6.10.
Tenant-behaftet: carries organization_id, RLS active.

meeting_type: ORDENTLICH (regular annual) | AUSSERORDENTLICH (extraordinary).
status: PLANNED | HELD.

A PLANNED meeting can be freely edited. Once marked HELD, the meeting date,
type, and property are conceptually frozen (the protocol/minutes can still be
updated until the resolutions are entered into the Beschluss-Sammlung).

[section 18/fachlich] Ladungsfristen (WEG section 24 Abs. 4 -- mindestens 3 Wochen),
Einladungsform, und Stimmrechtsausschlüsse sind fachlich/juristisch abzustimmen.
Provisorisch NICHT automatisch erzwungen.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class OwnersMeeting(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "owners_meeting"

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

    meeting_date: Mapped[date] = mapped_column(Date, nullable=False)
    location: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    # ORDENTLICH = regular annual meeting, AUSSERORDENTLICH = extraordinary
    meeting_type: Mapped[str] = mapped_column(String(20), nullable=False)

    # PLANNED = upcoming, HELD = took place
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="PLANNED")

    # Invitation text (Einladungstext)
    invitation_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Protocol / minutes text (Niederschrift)
    protocol_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "meeting_type IN ('ORDENTLICH', 'AUSSERORDENTLICH')",
            name="ck_owners_meeting_type",
        ),
        CheckConstraint(
            "status IN ('PLANNED', 'HELD')",
            name="ck_owners_meeting_status",
        ),
    )
