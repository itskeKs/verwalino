"""AuditEvent repository -- tenant-scoped, append-only."""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent


class AuditRepository:
    """Append-only: only create() and list. No update/delete."""

    def __init__(self, session: AsyncSession, org_id: uuid.UUID):
        self._session = session
        self._org_id = org_id

    async def create(self, event: AuditEvent) -> AuditEvent:
        event.organization_id = self._org_id
        self._session.add(event)
        await self._session.flush()
        return event

    async def list_for_entity(
        self, entity_type: str, entity_id: uuid.UUID
    ) -> Sequence[AuditEvent]:
        stmt = (
            select(AuditEvent)
            .where(AuditEvent.organization_id == self._org_id)
            .where(AuditEvent.entity_type == entity_type)
            .where(AuditEvent.entity_id == entity_id)
            .order_by(AuditEvent.occurred_at)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()
