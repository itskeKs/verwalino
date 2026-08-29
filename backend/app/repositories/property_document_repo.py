"""Tenant-scoped repository for immutable property documents."""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import select

from app.models.property_document import PropertyDocument
from app.repositories.base import TenantRepository


class PropertyDocumentRepository(TenantRepository[PropertyDocument]):
    model = PropertyDocument

    async def list_active_for_property(
        self, property_id: uuid.UUID
    ) -> Sequence[PropertyDocument]:
        result = await self._session.execute(
            self._base_query()
            .where(PropertyDocument.property_id == property_id)
            .where(PropertyDocument.archived_at.is_(None))
            .order_by(PropertyDocument.created_at, PropertyDocument.id)
        )
        return result.scalars().all()

    async def get_active_for_property(
        self, property_id: uuid.UUID, document_id: uuid.UUID
    ) -> PropertyDocument | None:
        result = await self._session.execute(
            self._base_query()
            .where(PropertyDocument.property_id == property_id)
            .where(PropertyDocument.id == document_id)
            .where(PropertyDocument.archived_at.is_(None))
        )
        return result.scalar_one_or_none()
