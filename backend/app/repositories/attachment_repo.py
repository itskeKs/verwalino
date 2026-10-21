"""Tenant-filtered attachment metadata; database policies retain source checks."""
from __future__ import annotations

from app.models.attachment import Attachment
from app.repositories.base import TenantRepository


class AttachmentRepository(TenantRepository[Attachment]):
    model = Attachment

    async def list_by_parent(self, property_id=None, *, unit_id=None,
                             lease_agreement_id=None, person_id=None,
                             include_archived=False, offset=0, limit=50):
        query = self._base_query()
        for column, value in ((Attachment.property_id, property_id),
                              (Attachment.unit_id, unit_id),
                              (Attachment.lease_agreement_id, lease_agreement_id),
                              (Attachment.person_id, person_id)):
            if value is not None:
                query = query.where(column == value)
        if not include_archived:
            query = query.where(Attachment.archived_at.is_(None))
        rows = list((await self._session.scalars(query.order_by(
            Attachment.created_at.desc(), Attachment.id.desc()).offset(offset).limit(limit + 1))).all())
        return rows[:limit], offset + limit if len(rows) > limit else None
