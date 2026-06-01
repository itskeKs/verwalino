"""Organization -- the tenant root.

Does NOT carry organization_id itself (it IS the tenant).
Global table -- no RLS.
"""

from __future__ import annotations

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "organization"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
