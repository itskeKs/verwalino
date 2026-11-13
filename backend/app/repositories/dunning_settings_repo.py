"""Load or atomically replace one organization's dunning policy."""
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.engine.dunning import DEFAULT_FEES
from app.models.dunning_settings import BaseInterestRate, DunningSettings


class DunningSettingsRepository:
    def __init__(self, session, organization_id):
        self._session = session
        self._organization_id = organization_id

    async def get(self) -> DunningSettings:
        stored = await self._session.get(DunningSettings, self._organization_id)
        if stored is not None:
            return stored
        return DunningSettings(
            organization_id=self._organization_id,
            fee_level_1=DEFAULT_FEES[1], fee_level_2=DEFAULT_FEES[2], fee_level_3=DEFAULT_FEES[3],
            default_interest_enabled=False,
        )

    async def save(self, values) -> DunningSettings:
        stmt = insert(DunningSettings).values(organization_id=self._organization_id, **values)
        stmt = stmt.on_conflict_do_update(index_elements=[DunningSettings.organization_id], set_=values)
        return (await self._session.scalars(
            stmt.returning(DunningSettings), execution_options={"populate_existing": True},
        )).one()

    async def base_rate_covered_until(self) -> date | None:
        """End of the last stored half-year of the statutory base-rate table."""
        latest = await self._session.scalar(select(func.max(BaseInterestRate.valid_from)))
        if latest is None:
            return None
        if latest.month == 1:
            return date(latest.year, 6, 30)
        return date(latest.year, 12, 31)

    async def rates(self):
        return tuple((row.valid_from, row.rate_percent) for row in await self._session.scalars(
            select(BaseInterestRate).order_by(BaseInterestRate.valid_from)
        ))
