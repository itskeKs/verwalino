"""Explicit actor capabilities; revocation retains the original grant evidence."""
import uuid
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, UUIDPrimaryKeyMixin


class ScopeGrant(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "scope_grant"
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(String(16)), nullable=False)
    granted_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        CheckConstraint("cardinality(capabilities)>0 AND array_position(capabilities,NULL) IS NULL AND capabilities <@ ARRAY['read','prepare','confirm','approve','send','export']::varchar[] AND capabilities @> ARRAY['read']::varchar[]", name="scope_grant_check_22"),
        CheckConstraint("(revoked_at IS NULL)=(revoked_by IS NULL)", name="scope_grant_check_23"),
        CheckConstraint("expires_at IS NULL OR expires_at>created_at", name="scope_grant_check_24"),
        CheckConstraint("length(trim(reason))>0", name="scope_grant_check_25"),
        ForeignKeyConstraint(["organization_id", "scope_id"], ["accounting_scope.organization_id", "accounting_scope.id"]),
        ForeignKeyConstraint(["organization_id", "user_id"], ["membership.organization_id", "membership.user_id"]),
        ForeignKeyConstraint(["organization_id", "granted_by"], ["user.organization_id", "user.id"]),
        ForeignKeyConstraint(["organization_id", "revoked_by"], ["user.organization_id", "user.id"]),
    )
