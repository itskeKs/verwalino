"""Organization dunning policy and the global statutory base-rate schedule."""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DunningSettings(Base):
    __tablename__ = "dunning_settings"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organization.id", ondelete="RESTRICT"), primary_key=True,
    )
    fee_level_1: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    fee_level_2: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    fee_level_3: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    default_interest_enabled: Mapped[bool]

    @property
    def fees(self) -> dict[int, Decimal]:
        return {1: self.fee_level_1, 2: self.fee_level_2, 3: self.fee_level_3}


class BaseInterestRate(Base):
    __tablename__ = "base_interest_rate"

    valid_from: Mapped[date] = mapped_column(primary_key=True)
    rate_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2))
