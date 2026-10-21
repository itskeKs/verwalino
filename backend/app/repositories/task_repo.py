"""Tenant-scoped task persistence; source authorization additionally lives in RLS."""
from app.models.task import Task
from app.repositories.base import TenantRepository


class TaskRepository(TenantRepository[Task]):
    model = Task

    async def list_filtered(self, *, offset=0, limit=50, due_until=None, **filters):
        query = self._base_query()
        for name, value in filters.items():
            if value is not None:
                query = query.where(getattr(Task, name) == value)
        if due_until is not None:
            query = query.where(Task.due_date <= due_until)
        rows = list((await self._session.scalars(query.order_by(
            Task.created_at.desc(), Task.id.desc()).offset(offset).limit(limit + 1))).all())
        return rows[:limit], offset + limit if len(rows) > limit else None

    async def update(self, item, values):
        for name, value in values.items():
            setattr(item, name, value)
        await self._session.flush()
        await self._session.refresh(item)
        return item

    async def delete(self, item):
        await self._session.delete(item)
        await self._session.flush()
