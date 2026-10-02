"""AuditEvent repository -- tenant-scoped, append-only."""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent


class AuditRepository:
    """Append-only: only create() and list. No update/delete."""

    def __init__(self, session: AsyncSession, org_id: uuid.UUID):
        self._session = session
        self._org_id = org_id

    async def create(self, event: AuditEvent) -> None:
        # Auditing a revocation/cancellation must remain possible even when the
        # resulting audit row is unreadable. Preserve server time and append-only
        # checks, without ORM INSERT RETURNING implicitly requiring SELECT access.
        await self._session.flush()
        event.organization_id = self._org_id
        event.id = event.id or uuid.uuid4()
        await self._session.execute(insert(AuditEvent.__table__).inline().values(
            id=event.id, organization_id=self._org_id,
            actor_user_id=event.actor_user_id, entity_type=event.entity_type,
            entity_id=event.entity_id, action=event.action,
            payload_diff=event.payload_diff, prev_event_hash=event.prev_event_hash,
        ))

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
