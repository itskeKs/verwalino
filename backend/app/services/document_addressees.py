"""Resolve dated document recipients before freezing the existing snapshot."""
from datetime import date
import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.temporal import db_exclusive_to_engine_inclusive
from app.engine.periods import owner_at
from app.engine.types import OwnerPeriodInput
from app.repositories.settlement_repo import SettlementRunRepository


async def document_addressees(db: AsyncSession, org: uuid.UUID, property_id: uuid.UUID, day: date) -> list[dict]:
    repo = SettlementRunRepository(db, org)
    units = await repo.units_for_property(property_id)
    history = [OwnerPeriodInput(o.unit_id, o.owner_profile_id, o.valid_from,
        db_exclusive_to_engine_inclusive(o.valid_to), o.mea_share)
        for o in await repo.ownership_for_property(property_id)]
    names = {op.id: ' '.join(filter(None, [p.first_name, p.last_name]))
        for op, p in await repo.owner_profiles_for_property(property_id) if p}
    result = []
    for unit in units:
        owners = owner_at(history, unit.id, day)
        if not owners or any(not names.get(o.owner_profile_id) for o in owners):
            raise HTTPException(422, {'code': 'document_owner_missing',
                'message': f'Für Einheit {unit.number} fehlt ein Eigentümer mit Namen am {day.strftime("%d.%m.%Y")}.'})
        for owner in owners:
            result.append({'unit_id': str(unit.id), 'unit_number': unit.number,
                'owner_profile_id': str(owner.owner_profile_id), 'owner_name': names[owner.owner_profile_id],
                'key_date': day.isoformat()})
    if not result:
        raise HTTPException(422, {'code': 'document_owner_missing', 'message': 'Für das Dokument fehlen Einheiten und Eigentümer.'})
    return result
