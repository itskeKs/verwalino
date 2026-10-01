"""Append-only attestations of complete financial input coverage."""
import uuid
from datetime import date, datetime
from sqlalchemy import Date, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, UUIDPrimaryKeyMixin


class CoverageDeclaration(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "coverage_declaration"
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    settlement_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    economic_plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    component: Mapped[str] = mapped_column(String(40), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    values: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_document_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    source_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    attestor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("user.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = tuple(ForeignKeyConstraint(["organization_id", column], [f"{target}.organization_id", f"{target}.id"],
        name=f"fk_coverage_{column}_tenant", ondelete="RESTRICT") for column, target in [
        ("settlement_run_id", "settlement_run"), ("economic_plan_id", "economic_plan"), ("property_id", "property")])
