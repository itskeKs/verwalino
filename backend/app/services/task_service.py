"""Operational tasks with live, source-authorized parents and assignees."""
from fastapi import HTTPException
from sqlalchemy import text

from app.models.task import Task
from app.repositories.task_repo import TaskRepository
from app.repositories.property_repo import PropertyRepository
from app.repositories.unit_repo import UnitRepository
from app.repositories.lease_agreement_repo import LeaseAgreementRepository
from app.repositories.user_repo import UserRepository
from app.repositories.membership_repo import MembershipRepository
from app.services.property_master_service import require_property_master


class TaskService:
    def __init__(self, db, org_id, user_id):
        self.db, self.org_id, self.user_id = db, org_id, user_id
        self.repo = TaskRepository(db, org_id)

    async def _parent(self, repo, entity_id):
        row = await repo(self.db, self.org_id).get_by_id(entity_id)
        if row is None:
            raise HTTPException(404, 'Task parent not accessible')
        return row

    async def parents(self, links, capability='read'):
        property_id, unit_id, lease_id = (links.get(key) for key in (
            'property_id', 'unit_id', 'lease_agreement_id'))
        if property_id:
            await self._parent(PropertyRepository, property_id)
            await require_property_master(self.db, self.org_id, property_id, capability)
        unit = await self._parent(UnitRepository, unit_id) if unit_id else None
        lease = await self._parent(LeaseAgreementRepository, lease_id) if lease_id else None
        if lease:
            lease_unit = await self._parent(UnitRepository, lease.unit_id)
            if unit and unit.id != lease_unit.id:
                raise HTTPException(422, 'Lease and unit do not match')
            unit = lease_unit
            allowed = await self.db.scalar(text(
                "SELECT property_history_row_access(l.organization_id,'lease_agreement',to_jsonb(l),:cap) "
                "FROM lease_agreement l WHERE l.organization_id=:org AND l.id=:id"),
                {'cap': capability, 'org': self.org_id, 'id': lease.id})
            if not allowed:
                raise HTTPException(404, 'Task lease not accessible')
        if unit:
            if property_id and property_id != unit.property_id:
                # Resolve supplied IDs first: foreign parents remain 404, not 422.
                await self._parent(PropertyRepository, property_id)
                raise HTTPException(422, 'Task unit and property do not match')
            property_id = unit.property_id
            unit_id = unit.id
        if property_id:
            await self._parent(PropertyRepository, property_id)
            await require_property_master(self.db, self.org_id, property_id, capability)
        return {'property_id': property_id, 'unit_id': unit_id, 'lease_agreement_id': lease_id}

    async def assignee(self, user_id):
        user = await UserRepository(self.db, self.org_id).get_by_id(user_id)
        member = await MembershipRepository(self.db, self.org_id).get_by_user_id(user_id)
        if not user or not user.is_active or not member or member.role not in {
                'OWNER_ADMIN', 'CLERK', 'READ_ONLY'}:
            raise HTTPException(404, 'Task assignee not accessible')

    async def get(self, task_id, *, write=False):
        row = await (self.repo.get_by_id_for_update(task_id) if write else self.repo.get_by_id(task_id))
        if row is None:
            raise HTTPException(404, 'Task not found')
        await self.parents(self.links(row), 'prepare' if write else 'read')
        return row

    @staticmethod
    def links(row):
        return {key: getattr(row, key) for key in ('property_id', 'unit_id', 'lease_agreement_id')}

    async def create(self, values):
        values = dict(values)
        values.update(await self.parents(values, 'prepare'))
        values['assigned_user_id'] = values.get('assigned_user_id') or self.user_id
        await self.assignee(values['assigned_user_id'])
        return await self.repo.create(Task(organization_id=self.org_id, created_by=self.user_id, **values))

    async def update(self, task_id, values):
        row = await self.get(task_id, write=True)
        values = dict(values)
        links = self.links(row) | {key: values[key] for key in self.links(row) if key in values}
        # Changing a child replaces implicit ancestors; explicitly supplied IDs
        # must still agree. Detaching all three links is an explicit PATCH.
        if values.get('lease_agreement_id') is not None:
            if 'unit_id' not in values:
                links['unit_id'] = None
            if 'property_id' not in values:
                links['property_id'] = None
        elif values.get('unit_id') is not None and 'property_id' not in values:
            links['property_id'] = None
        values.update(await self.parents(links, 'prepare'))
        if 'assigned_user_id' in values and values['assigned_user_id'] != row.assigned_user_id:
            await self.assignee(values['assigned_user_id'])
        return await self.repo.update(row, values)

    async def delete(self, task_id):
        await self.repo.delete(await self.get(task_id, write=True))

    async def list(self, **filters):
        await self.parents(filters)
        return await self.repo.list_filtered(**filters)

    async def output(self, row):
        # Authorize all sources before looking up any human-readable labels.
        await self.parents(self.links(row))
        prop = await self._parent(PropertyRepository, row.property_id) if row.property_id else None
        unit = await self._parent(UnitRepository, row.unit_id) if row.unit_id else None
        lease = await self._parent(LeaseAgreementRepository, row.lease_agreement_id) if row.lease_agreement_id else None
        user = await UserRepository(self.db, self.org_id).get_by_id(row.assigned_user_id)
        return {key: getattr(row, key) for key in (
            'id', 'organization_id', 'created_by', 'assigned_user_id', 'property_id', 'unit_id',
            'lease_agreement_id', 'title', 'note', 'due_date', 'completed', 'created_at', 'updated_at')} | {
            'property_name': prop.name if prop else None,
            'unit_number': unit.number if unit else None,
            'lease_label': f'Vertrag ab {lease.valid_from.isoformat()} · Einheit {unit.number}' if lease else None,
            'assigned_user_name': user.display_name or 'Benutzer' if user else None,
        }
