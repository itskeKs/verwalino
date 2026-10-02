"""Explicit accounting spheres and immutable associations for historical money."""
import uuid
from datetime import date, datetime
from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKeyConstraint, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, UUIDPrimaryKeyMixin


class AccountingScope(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "accounting_scope"
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    management_mandate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    scope_type: Mapped[str] = mapped_column(String(4), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    whole_property: Mapped[bool] = mapped_column(nullable=False, default=True)
    origin: Mapped[str] = mapped_column(String(24), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    request_key: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("organization_id", "management_mandate_id", "id", name="uq_scope_mandate_context"),
        CheckConstraint("scope_type IN ('WEG','MIET','SEV')", name="accounting_scope_check_24"),
        CheckConstraint("valid_to IS NULL OR valid_to>=valid_from", name="accounting_scope_check_25"),
        CheckConstraint("length(trim(label))>0", name="accounting_scope_check_26"),
        CheckConstraint("origin IN ('REVIEWED','PROVEN_LEGACY')", name="accounting_scope_check_27"),
        CheckConstraint("origin='PROVEN_LEGACY' OR created_by IS NOT NULL", name="accounting_scope_check_28"),
        UniqueConstraint("organization_id", "id", name="uq_accounting_scope_org_id"),
        UniqueConstraint("organization_id", "property_id", "id", name="uq_accounting_scope_org_property_id"),
        UniqueConstraint("organization_id", "created_by", "request_key", name="uq_accounting_scope_request"),
        ForeignKeyConstraint(["organization_id", "property_id"], ["property.organization_id", "property.id"]),
        ForeignKeyConstraint(["organization_id", "management_mandate_id"], ["management_mandate.organization_id", "management_mandate.id"]),
        ForeignKeyConstraint(["organization_id", "created_by"], ["user.organization_id", "user.id"]),
    )


class AccountingScopeUnit(Base):
    __tablename__ = "accounting_scope_unit"
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    unit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    owner_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "property_id", "scope_id"], ["accounting_scope.organization_id", "accounting_scope.property_id", "accounting_scope.id"]),
        ForeignKeyConstraint(["organization_id", "property_id", "unit_id"], ["unit.organization_id", "unit.property_id", "unit.id"]),
        ForeignKeyConstraint(["organization_id", "owner_profile_id"], ["owner_profile.organization_id", "owner_profile.id"]),
    )


class AccountingScopeBinding(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "accounting_scope_binding"
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    bank_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payment_bank_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    cost_entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    settlement_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    economic_plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    origin: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_reconciliation_binding_org_id"),
        ForeignKeyConstraint(["organization_id", "property_id", "scope_id"], ["accounting_scope.organization_id", "accounting_scope.property_id", "accounting_scope.id"]),
        ForeignKeyConstraint(["organization_id", "reviewed_by"], ["user.organization_id", "user.id"]),
        *[ForeignKeyConstraint(["organization_id", "property_id", column], [f"{table}.organization_id", f"{table}.property_id", f"{table}.id"], deferrable=True, initially="DEFERRED")
          for column, table in [("bank_account_id", "bank_account"), ("payment_id", "payment"), ("cost_entry_id", "cost_entry"), ("settlement_run_id", "settlement_run"), ("economic_plan_id", "economic_plan")]],
        CheckConstraint("num_nonnulls(bank_account_id,payment_id,cost_entry_id,settlement_run_id,economic_plan_id)=1", name="accounting_scope_check_72"),
        CheckConstraint("(payment_id IS NOT NULL)=(payment_bank_account_id IS NOT NULL)", name="accounting_scope_check_73"),
        CheckConstraint("origin IN ('REVIEWED','PROVEN_LEGACY') AND length(trim(reason))>0", name="accounting_scope_check_74"),
        CheckConstraint("origin='PROVEN_LEGACY' OR reviewed_by IS NOT NULL", name="accounting_scope_check_75"),
        UniqueConstraint("organization_id", "bank_account_id", "scope_id", name="uq_scope_binding_bank_scope"),
        ForeignKeyConstraint(["organization_id", "payment_bank_account_id", "scope_id"], ["accounting_scope_binding.organization_id", "accounting_scope_binding.bank_account_id", "accounting_scope_binding.scope_id"]),
        ForeignKeyConstraint(["organization_id", "payment_bank_account_id", "payment_id"], ["payment.organization_id", "payment.bank_account_id", "payment.id"], deferrable=True, initially="DEFERRED"),
    )


class ScopeMigrationException(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "scope_migration_exception"
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    property_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "property_id"], ["property.organization_id", "property.id"]),
        UniqueConstraint("organization_id", "property_id", "code", name="uq_scope_migration_exception"),
    )
