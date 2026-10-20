"""BankTransaction repository -- tenant-scoped.

T15: Bankumsätze Tabelle.
Provides CRUD and import-unique queries for bank transactions.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank_transaction import BankTransaction
from app.repositories.base import TenantRepository


class BankTransactionRepository(TenantRepository[BankTransaction]):
    model = BankTransaction

    async def list_by_account(
        self,
        bank_account_id: uuid.UUID,
        *,
        status: str | None = None,
    ) -> Sequence[BankTransaction]:
        """Load all transactions for one account, optionally filtered by status."""
        stmt = self._base_query().where(
            BankTransaction.bank_account_id == bank_account_id
        )
        if status is not None:
            stmt = stmt.where(BankTransaction.status == status)
        stmt = stmt.order_by(BankTransaction.value_date, BankTransaction.created_at)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_by_import_hash(
        self, bank_account_id: uuid.UUID, import_hash: str
    ) -> BankTransaction | None:
        """Check whether this import_hash is already present on this account."""
        stmt = self._base_query().where(
            BankTransaction.bank_account_id == bank_account_id,
            BankTransaction.import_hash == import_hash,
        )
        return await self._session.scalar(stmt)

    async def list_unassigned(self) -> Sequence[BankTransaction]:
        """All transactions still in status NEU."""
        stmt = (
            self._base_query()
            .where(BankTransaction.status == "NEU")
            .order_by(BankTransaction.value_date, BankTransaction.created_at)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def update(self, entity: BankTransaction) -> BankTransaction:
        """Flush changes on an already-tracked entity."""
        await self._session.flush()
        return entity
