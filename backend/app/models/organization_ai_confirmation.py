"""Recorded provider confirmations for the organization's own AI key."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class OrganizationAiConfirmation(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "organization_ai_confirmation"
    __table_args__ = (
        Index("ix_organization_ai_confirmation_org", "organization_id", "confirmed_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organization.id"), nullable=False
    )
    confirmed_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(2048))
    text_version: Mapped[str] = mapped_column(String(32), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.statement_timestamp(), nullable=False
    )
