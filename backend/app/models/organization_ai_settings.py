"""One encrypted AI configuration per organization."""
import uuid
from decimal import Decimal
from sqlalchemy import String, Text, Boolean, Numeric, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class OrganizationAiSettings(Base):
    __tablename__ = 'organization_ai_settings'
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('organization.id'), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), default='openai')
    base_url: Mapped[str | None] = mapped_column(String(2048))
    encrypted_api_key: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(255))
    import_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    qa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    monthly_limit_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
