"""Organization- and source-scoped position rules shared by tenant statements."""
from app.models.commercial_preallocation import CommercialPreallocation
from app.repositories.base import TenantRepository


class CommercialPreallocationRepository(TenantRepository[CommercialPreallocation]):
    model = CommercialPreallocation

    async def for_costs(self, cost_ids):
        if not cost_ids:
            return {}
        rows = (await self._session.execute(self._base_query().where(
            CommercialPreallocation.cost_entry_id.in_(cost_ids)).order_by(CommercialPreallocation.cost_entry_id))).scalars().all()
        return {row.cost_entry_id: row for row in rows}

    async def save(self, entry, values):
        row = (await self.for_costs([entry.id])).get(entry.id)
        if values['commercial_share_percent'] is None and values['commercial_amount'] is None:
            if row is not None:
                await self._session.delete(row)
        elif row is None:
            row = CommercialPreallocation(organization_id=self._org_id, property_id=entry.property_id,
                cost_entry_id=entry.id, **values)
            self._session.add(row)
        else:
            for name, value in values.items():
                setattr(row, name, value)
        await self._session.flush()
