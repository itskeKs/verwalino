"""Tenant-scoped levy authority and frozen allocation reads under native RLS."""
from sqlalchemy import select, text

from app.models.owners_meeting import OwnersMeeting
from app.models.resolution import Resolution
from app.models.special_levy import SpecialLevy, SpecialLevyAllocation


class SpecialLevyRepository:
    def __init__(self, session, org_id):
        self.db, self.org = session, org_id

    async def get(self, levy_id, *, lock=False):
        statement = select(SpecialLevy).where(SpecialLevy.organization_id == self.org, SpecialLevy.id == levy_id)
        if lock:
            await self.db.execute(select(SpecialLevy.resolution_id).where(
                SpecialLevy.organization_id == self.org, SpecialLevy.id == levy_id).with_for_update())
            statement = statement.execution_options(populate_existing=True)
        return await self.db.scalar(statement)

    async def link_resolution(self, levy, resolution_id):
        # ORM onupdate would also change updated_at. The financial evidence
        # contract permits only the reference column to change.
        await self.db.execute(text("UPDATE special_levy SET resolution_id=:resolution WHERE organization_id=:org AND id=:levy"),
            {"org": self.org, "levy": levy.id, "resolution": resolution_id})
        await self.db.refresh(levy)

    async def allocations(self, levy_id):
        return list((await self.db.scalars(select(SpecialLevyAllocation).where(
            SpecialLevyAllocation.organization_id == self.org, SpecialLevyAllocation.special_levy_id == levy_id,
        ).order_by(SpecialLevyAllocation.unit_id, SpecialLevyAllocation.owner_profile_id))).all())

    async def resolution(self, resolution_id, property_id):
        return await self.db.scalar(select(Resolution).join(OwnersMeeting,
            (OwnersMeeting.id == Resolution.meeting_id) & (OwnersMeeting.organization_id == Resolution.organization_id),
        ).where(Resolution.organization_id == self.org, Resolution.id == resolution_id,
            OwnersMeeting.property_id == property_id).with_for_update())

    async def for_resolutions(self, resolution_ids):
        if not resolution_ids:
            return []
        return list((await self.db.scalars(select(SpecialLevy).where(
            SpecialLevy.organization_id == self.org, SpecialLevy.resolution_id.in_(resolution_ids),
        ).order_by(SpecialLevy.due_date, SpecialLevy.id))).all())
