"""Direct-debit consent metadata; debtor PII remains on the existing person."""
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SepaCreditor(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = 'sepa_creditor'
    __table_args__ = (
        ForeignKeyConstraint(['organization_id','bank_account_id'], ['bank_account.organization_id','bank_account.id'], ondelete='RESTRICT'),
        UniqueConstraint('organization_id','bank_account_id'),
        UniqueConstraint('organization_id','bank_account_id','creditor_id'),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('organization.id',ondelete='RESTRICT'))
    bank_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    creditor_id: Mapped[str] = mapped_column(String(35))
    name: Mapped[str] = mapped_column(String(70))
    country: Mapped[str] = mapped_column(String(2))
    town: Mapped[str] = mapped_column(String(35))
    bank_fingerprint: Mapped[str] = mapped_column(String(64))
    bank_authorized: Mapped[bool] = mapped_column(Boolean)


class SepaMandate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = 'sepa_mandate'
    __table_args__ = (
        ForeignKeyConstraint(['organization_id','bank_account_id','creditor_id'],
            ['sepa_creditor.organization_id','sepa_creditor.bank_account_id','sepa_creditor.creditor_id'],ondelete='RESTRICT'),
        UniqueConstraint('organization_id','creditor_id','reference'),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('organization.id',ondelete='RESTRICT'))
    bank_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    tenant_profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('tenant_profile.id',ondelete='RESTRICT'))
    creditor_id: Mapped[str] = mapped_column(String(35))
    reference: Mapped[str] = mapped_column(String(35))
    signed_on: Mapped[date] = mapped_column(Date)
    sequence_type: Mapped[str] = mapped_column(String(4))
    last_used_on: Mapped[date | None] = mapped_column(Date)
    debtor_country: Mapped[str] = mapped_column(String(2))
    debtor_bic: Mapped[str | None] = mapped_column(String(11))
    source_fingerprint: Mapped[str] = mapped_column(String(64))
    original_reference: Mapped[str] = mapped_column(String(255))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
