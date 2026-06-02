"""AgendaItem -- a numbered item on the agenda (Tagesordnungspunkt / TOP).

M8 Eigentümerversammlung & Beschluss-Sammlung; SDD section 6.10.
Tenant-behaftet: carries organization_id, RLS active.

Each agenda item has a sequential item_number within its meeting.
The number is assigned by the application (service layer) and enforced
as UNIQUE (meeting_id, item_number) by the DB.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class AgendaItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "agenda_item"

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

    # Sequential number within the meeting (TOP 1, TOP 2, ...)
    item_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("meeting_id", "item_number", name="uq_agenda_item_meeting_number"),
    )
