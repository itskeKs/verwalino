"""Read and replace household history under the unit's master-data authority."""
from datetime import date

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


def _changes_within(stored, submitted, window_start, window_end):
    """True when a day inside the window carries a different person count."""
    change_points = {window_start}
    for row in [*stored, *submitted]:
        if window_start < row.valid_from <= window_end:
            change_points.add(row.valid_from)
    for vacancy in (False, True):
        before = sorted((r for r in stored if r.is_vacancy == vacancy), key=lambda r: r.valid_from)
        after = sorted((r for r in submitted if r.is_vacancy == vacancy), key=lambda r: r.valid_from)
        if any(_persons_on(before, day) != _persons_on(after, day) for day in sorted(change_points)):
            return True
    return False


def _persons_key_windows(payload):
    """Service periods of a run's PERSONENZAHL positions, across all property years.

    The engine counts person-days over each cost's service period, so that
    period -- not the statement period -- is the locked window. A statement
    spanning two property years keeps the first year's positions at the top
    level and every year's under inputs.property_periods.
    """
    windows = []

    def collect(positions):
        for position in positions:
            if position.get('tenant_distribution_key') == 'PERSONENZAHL':
                service_period = position['service_period']
                windows.append((date.fromisoformat(service_period['from']), date.fromisoformat(service_period['to'])))

    inputs = (payload or {}).get('inputs') or {}
    collect(inputs.get('cost_positions') or [])
    for part in inputs.get('property_periods') or []:
        collect((part.get('inputs') or {}).get('cost_positions') or [])
    return windows


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
                if any(_changes_within(stored, rows, start, end)
                        for start, end in _persons_key_windows(run.snapshot_payload)):
                    raise HTTPException(409, {'code': 'household_finalized', 'message':
                        f'Die Nebenkostenabrechnung für den Zeitraum {run.period_start:%d.%m.%Y} bis '
                        f'{run.period_end:%d.%m.%Y} ist bereits finalisiert. '
                        'Die Personenzahl im Abrechnungszeitraum kann nicht mehr geändert werden.'})
        return await self.rows.replace(unit_id, rows)
