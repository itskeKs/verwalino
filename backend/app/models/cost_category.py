"""CostCategory -- global, versioned standard chart of accounts.

SDD section 7, m2-gobd-belegfluss.md section 1.
Global table (no organization_id, no RLS).
plan_version enables legal-change versioning without mutating existing rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class CostCategory(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "cost_category"

    plan_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    cost_group: Mapped[str] = mapped_column(String(20), nullable=False)

    tenant_apportionability_default: Mapped[str] = mapped_column(
        String(30), nullable=False
    )
    owner_billing_relevant_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False
    )
    owner_distribution_key_default: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    tenant_distribution_key_default: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )

    reserve_relevant: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    legal_hint: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sort_order: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
