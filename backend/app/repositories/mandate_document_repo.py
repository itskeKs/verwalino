"""MandateDocument repository -- tenant-scoped."""

from __future__ import annotations

import uuid
from typing import Sequence

from app.models.mandate_document import MandateDocument
from app.repositories.base import TenantRepository


class MandateDocumentRepository(TenantRepository[MandateDocument]):
    model = MandateDocument

    async def list_by_mandate(
        self, mandate_id: uuid.UUID, *, include_archived=False, scope_id=None
    ) -> Sequence[MandateDocument]:
        stmt = (
            self._base_query()
            .where(MandateDocument.management_mandate_id == mandate_id)
            .order_by(MandateDocument.created_at.desc())
        )
        if not include_archived:
            stmt = stmt.where(MandateDocument.archived_at.is_(None))
        if scope_id is not None:
            stmt = stmt.where(MandateDocument.accounting_scope_id == scope_id)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def delete(self, entity: MandateDocument) -> None:
        await self._session.delete(entity)
        await self._session.flush()
