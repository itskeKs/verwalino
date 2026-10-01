"""InvoiceDocument repository -- tenant-scoped, no delete.

Extends TenantRepository. No delete() method (ADR-0003: documents are never deleted).
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice_document import InvoiceDocument
from app.repositories.base import TenantRepository


class InvoiceDocumentRepository(TenantRepository[InvoiceDocument]):
    model = InvoiceDocument

    async def get_active_for_cost_entry(
        self, cost_entry_id: uuid.UUID
    ) -> InvoiceDocument | None:
        """Get the active (non-superseded) document for a cost entry."""
        stmt = (
            self._base_query()
            .where(InvoiceDocument.cost_entry_id == cost_entry_id)
            .where(InvoiceDocument.status == "active")
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_by_chain(
        self, chain_root_id: uuid.UUID, *, lock: bool = False
    ) -> InvoiceDocument | None:
        """Get the active document in a version chain."""
        stmt = (
            self._base_query()
            .where(InvoiceDocument.chain_root_id == chain_root_id)
            .where(InvoiceDocument.status == "active")
        )
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_cost_entry(
        self, cost_entry_id: uuid.UUID
    ) -> Sequence[InvoiceDocument]:
        """List all versions (including superseded) for a cost entry."""
        stmt = (
            self._base_query()
            .where(InvoiceDocument.cost_entry_id == cost_entry_id)
            .order_by(InvoiceDocument.version)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def update(self, entity: InvoiceDocument) -> InvoiceDocument:
        """Flush changes on an already-tracked entity. Trigger guards released rows."""
        await self._session.flush()
        return entity

    # Deliberately NO delete() method (ADR-0003: documents are never deleted).
