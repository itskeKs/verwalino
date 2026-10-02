"""Sanitized immutable accounting records for property knowledge Q&A runs."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PropertyKnowledgeQueryRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A reservation can only be finalized or expired; it stores no content."""

    __tablename__ = "property_knowledge_query_run"

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False)
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    initiated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    budget_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="RESERVED")
    provider: Mapped[str] = mapped_column(String(120), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    reserved_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, server_default="0")
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reservation_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "initiated_by"], ["user.organization_id", "user.id"], name="fk_knowledge_query_actor", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "property_id"], ["property.organization_id", "property.id"], name="fk_property_knowledge_query_run_property_tenant", ondelete="RESTRICT"),
        CheckConstraint("status IN ('RESERVED', 'FINALIZED', 'EXPIRED')", name=conv("ck_property_knowledge_query_run_status")),
        CheckConstraint("reserved_cost_usd >= 0", name=conv("ck_property_knowledge_query_run_reserved_cost_nonnegative")),
        CheckConstraint("estimated_cost_usd >= 0", name=conv("ck_property_knowledge_query_run_estimated_cost_nonnegative")),
        CheckConstraint("input_tokens >= 0", name=conv("ck_property_knowledge_query_run_input_tokens_nonnegative")),
        CheckConstraint("output_tokens >= 0", name=conv("ck_property_knowledge_query_run_output_tokens_nonnegative")),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name=conv("ck_property_knowledge_query_run_latency_nonnegative")),
        Index("ix_property_knowledge_query_run_org_property", "organization_id", "property_id"),
        Index("ix_property_knowledge_query_run_org_window_status", "organization_id", "budget_window_start", "status"),
    )
