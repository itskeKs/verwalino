"""Organization-scoped household reads and atomic replace-all storage."""
import uuid
from sqlalchemy import delete
from app.models.unit_household import UnitHousehold
from app.repositories.base import TenantRepository


class UnitHouseholdRepository(TenantRepository[UnitHousehold]):
    model = UnitHousehold

    async def for_units(self, unit_ids):
        if not unit_ids:
            return []
        result = await self._session.execute(self._base_query().where(UnitHousehold.unit_id.in_(unit_ids))
            .order_by(UnitHousehold.unit_id, UnitHousehold.is_vacancy, UnitHousehold.valid_from))
        return list(result.scalars())

    async def replace(self, unit_id: uuid.UUID, rows):
        await self._session.execute(delete(UnitHousehold).where(
            UnitHousehold.organization_id == self._org_id, UnitHousehold.unit_id == unit_id))
        self._session.add_all([UnitHousehold(organization_id=self._org_id, unit_id=unit_id,
            valid_from=r.valid_from, persons=r.persons, is_vacancy=r.is_vacancy) for r in rows])
        await self._session.flush()
        return await self.for_units([unit_id])
