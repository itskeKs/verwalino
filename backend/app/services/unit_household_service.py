"""Read and replace household history under the unit's master-data authority."""
from fastapi import HTTPException
from app.repositories.settlement_repo import SettlementRunRepository
from app.repositories.unit_household_repo import UnitHouseholdRepository
from app.repositories.unit_repo import UnitRepository
from app.services.financial_scope_service import FinancialScopeService
from app.services.property_master_service import require_property_master


def _persons_on(rows, day):
    persons = None
    for row in rows:
        if row.valid_from <= day:
            persons = row.persons
        else:
            break
    return persons


def _changes_within(stored, submitted, period_start, period_end):
    """True when a day inside the period carries a different person count."""
    change_points = {period_start}
    for row in [*stored, *submitted]:
        if period_start < row.valid_from <= period_end:
            change_points.add(row.valid_from)
    for vacancy in (False, True):
        before = sorted((r for r in stored if r.is_vacancy == vacancy), key=lambda r: r.valid_from)
        after = sorted((r for r in submitted if r.is_vacancy == vacancy), key=lambda r: r.valid_from)
        if any(_persons_on(before, day) != _persons_on(after, day) for day in sorted(change_points)):
            return True
    return False


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
        # The lock finalization takes, so no run finalizes between check and replace.
        await FinancialScopeService(self.db, self.auth.organization_id, self.auth.user_id).lock_inputs()
        runs = await SettlementRunRepository(self.db, self.auth.organization_id).finalized_persons_key_runs(unit.property_id)
        if runs:
            stored = await self.rows.for_units([unit_id])
            for run in runs:
                if _changes_within(stored, rows, run.period_start, run.period_end):
                    raise HTTPException(409, {'code': 'household_finalized', 'message':
                        f'Die Nebenkostenabrechnung für den Zeitraum {run.period_start:%d.%m.%Y} bis '
                        f'{run.period_end:%d.%m.%Y} ist bereits finalisiert. '
                        'Die Personenzahl im Abrechnungszeitraum kann nicht mehr geändert werden.'})
        return await self.rows.replace(unit_id, rows)
