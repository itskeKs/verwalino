"""Tenant-scoped statement preview and atomic, idempotent import."""
from __future__ import annotations

import uuid
from dataclasses import asdict
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.engine.bank_import import BankImportError, parse_camt053, parse_csv
from app.repositories.bank_account_repo import BankAccountRepository
from app.repositories.bank_transaction_repo import BankTransactionRepository

PREVIEW_LIMIT = 100


class BankImportService:
    def __init__(self, session: AsyncSession, organization_id: uuid.UUID):
        self._accounts = BankAccountRepository(session, organization_id)
        self._transactions = BankTransactionRepository(session, organization_id)

    async def import_statement(
        self, account_id: uuid.UUID, content: bytes, filename: str, *, dry_run: bool
    ) -> dict:
        # Serialize imports on the same account. RLS + explicit org filter apply
        # to this lock too; another tenant's account is never a writable target.
        account = await (
            self._accounts.get_by_id(account_id) if dry_run
            else self._accounts.get_by_id_for_update(account_id)
        )
        if account is None:
            raise HTTPException(status_code=404, detail="Bank account not found")
        extension = filename.rsplit(".", 1)[-1].lower()
        if extension == "csv":
            parser = parse_csv
        elif extension in {"xml", "zip"}:
            parser = parse_camt053
        else:
            raise BankImportError("Unbekanntes Dateiformat: CSV, CAMT-XML oder ZIP erwartet.")
        rows = await run_in_threadpool(parser, content, account_iban=account.iban)
        existing = await self._transactions.existing_import_hashes(
            account_id, [row.import_hash for row in rows],
        )
        new_rows = [row for row in rows if row.import_hash not in existing]
        imported: set[str] = set()
        if not dry_run:
            imported = await self._transactions.import_rows(account_id, [asdict(row) for row in new_rows])
            # ON CONFLICT remains the final safeguard if a different writer
            # bypasses our account-lock protocol. Report actual inserts only.
            existing.update(row.import_hash for row in new_rows if row.import_hash not in imported)
            new_rows = [row for row in new_rows if row.import_hash in imported]
        dates = [row.booking_date for row in rows]
        return {
            "count": len(rows), "new_count": len(new_rows),
            "duplicate_count": len(rows) - len(new_rows), "imported_count": len(imported),
            "total_amount": sum((row.amount for row in rows), Decimal("0.00")),
            "new_total_amount": sum((row.amount for row in new_rows), Decimal("0.00")),
            "date_from": min(dates) if dates else None,
            "date_to": max(dates) if dates else None,
            "transactions": [
                {**asdict(row), "is_duplicate": row.import_hash in existing}
                for row in rows[:PREVIEW_LIMIT]
            ],
            "preview_truncated": len(rows) > PREVIEW_LIMIT,
        }
