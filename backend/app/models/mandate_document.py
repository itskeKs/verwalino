"""MandateDocument -- a document attached to a management mandate.

Verwaltervertrag, Bankkonto-Unterlagen, Vollmachten, Versicherungen etc. These
are contract/admin documents, NOT GoBD Buchungsbelege -- they are mutable and
deletable (no immutability trigger). File bytes live in the content-addressed
document store (app.core.storage); this row holds metadata + the storage ref.
Tenant-behaftet: carries organization_id, RLS active.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class MandateDocument(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "mandate_document"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    management_mandate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("management_mandate.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # VERWALTERVERTRAG | BANKKONTO | VOLLMACHT | VERSICHERUNG | SONSTIGES
    doc_type: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    content_byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    storage_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(16), nullable=False)

    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
