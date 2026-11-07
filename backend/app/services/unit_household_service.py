"""Read and replace household history under the unit's master-data authority."""
from fastapi import HTTPException
from app.repositories.unit_household_repo import UnitHouseholdRepository
from app.repositories.unit_repo import UnitRepository
from app.services.property_master_service import require_property_master


class UnitHouseholdService:
    def __init__(self, db, auth):
        self.db, self.auth = db, auth
        self.units = UnitRepository(db, auth.organization_id)
        self.rows = UnitHouseholdRepository(db, auth.organization_id)

    async def get(self, unit_id):
        if await self.units.get_by_id(unit_id) is None:
            raise HTTPException(404, 'Einheit nicht gefunden.')
        return await self.rows.for_units([unit_id])

    async def replace(self, unit_id, rows):
        # The parent lock serializes concurrent replacements, including empty histories.
        unit = await self.units.get_by_id_for_update(unit_id)
        if unit is None:
            raise HTTPException(404, 'Einheit nicht gefunden.')
        await require_property_master(self.db, self.auth.organization_id, unit.property_id)
        keys = [(r.is_vacancy, r.valid_from) for r in rows]
        if len(set(keys)) != len(keys):
            raise HTTPException(422, 'Pro Haushalt und Datum ist nur eine Personenzahl zulässig.')
        return await self.rows.replace(unit_id, rows)
