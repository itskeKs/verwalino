"""BankTransaction repository -- tenant-scoped.

T15: Bankumsätze Tabelle.
Provides CRUD and import-unique queries for bank transactions.
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy.dialects.postgresql import insert

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

    async def existing_import_hashes(
        self, bank_account_id: uuid.UUID, hashes: Sequence[str]
    ) -> set[str]:
        existing: set[str] = set()
        for offset in range(0, len(hashes), 1000):
            statement = self._base_query().with_only_columns(BankTransaction.import_hash).where(
                BankTransaction.bank_account_id == bank_account_id,
                BankTransaction.import_hash.in_(hashes[offset:offset + 1000]),
            )
            existing.update((await self._session.scalars(statement)).all())
        return existing

    async def import_rows(
        self, bank_account_id: uuid.UUID, rows: Sequence[dict]
    ) -> set[str]:
        imported: set[str] = set()
        for offset in range(0, len(rows), 500):
            values = [
                {**row, "organization_id": self._org_id,
                 "bank_account_id": bank_account_id, "status": "NEU"}
                for row in rows[offset:offset + 500]
            ]
            statement = insert(BankTransaction).values(values).on_conflict_do_nothing(
                index_elements=["bank_account_id", "import_hash"],
            ).returning(BankTransaction.import_hash)
            imported.update((await self._session.scalars(statement)).all())
        return imported

    async def update(self, entity: BankTransaction) -> BankTransaction:
        """Flush changes on an already-tracked entity."""
        await self._session.flush()
        return entity
