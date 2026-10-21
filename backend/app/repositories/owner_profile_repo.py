"""OwnerProfile repository -- tenant-scoped."""

from sqlalchemy import and_, or_, select

from app.models.owner_profile import OwnerProfile
from app.models.person import Person
from app.models.ownership_history import OwnershipHistory
from app.models.unit import Unit
from app.repositories.base import TenantRepository


class OwnerProfileRepository(TenantRepository[OwnerProfile]):
    model = OwnerProfile

    async def matching_targets(self, property_id, period_start, period_end):
        statement = select(OwnerProfile, Person).join(Person, and_(
            Person.id == OwnerProfile.person_id, Person.organization_id == self._org_id,
        )).join(OwnershipHistory, and_(
            OwnershipHistory.owner_profile_id == OwnerProfile.id,
            OwnershipHistory.organization_id == self._org_id,
        )).join(Unit, and_(
            Unit.id == OwnershipHistory.unit_id, Unit.organization_id == self._org_id,
        )).where(
            OwnerProfile.organization_id == self._org_id, Unit.property_id == property_id,
            OwnershipHistory.valid_from <= period_end,
            or_(OwnershipHistory.valid_to.is_(None), OwnershipHistory.valid_to > period_start),
        ).distinct().order_by(OwnerProfile.id)
        return (await self._session.execute(statement)).all()
