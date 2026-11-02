"""Tenant-scoped statement preview and atomic, idempotent import."""
from __future__ import annotations

import uuid
from dataclasses import asdict
from decimal import Decimal
from datetime import date
from calendar import monthrange

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.engine.bank_import import BankImportError, BankImportRow, parse_camt053, parse_csv
from app.engine.bank_matching import MatchTarget, matching_period, suggest_matches
from app.core.rbac import ScopeCapability
from app.core.tenant import AuthContext
from app.repositories.lease_agreement_repo import LeaseAgreementRepository
from app.repositories.owner_profile_repo import OwnerProfileRepository
from app.repositories.tenant_profile_repo import TenantProfileRepository
from app.repositories.unit_repo import UnitRepository
from app.repositories.economic_plan_repo import EconomicPlanRepository
from app.services.authorization_service import AuthorizationService
from app.services.financial_scope_service import FinancialScopeService
from app.services.payment_service import PaymentService, TENANT_TYPES
from app.repositories.bank_account_repo import BankAccountRepository
from app.repositories.bank_transaction_repo import BankTransactionRepository

PREVIEW_LIMIT = 100


class BankImportService:
    def __init__(self, session: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID | None = None):
        self._session = session
        self._org_id = organization_id
        self._user_id = user_id
        self._accounts = BankAccountRepository(session, organization_id)
        self._transactions = BankTransactionRepository(session, organization_id)

    async def import_statement(
        self, account_id: uuid.UUID, content: bytes, filename: str, *, dry_run: bool
    ) -> dict:
        # Follow the same lock order as account edits before taking row locks.
        await FinancialScopeService(self._session, self._org_id, self._user_id).lock_inputs()
        # Serialize imports on the same account. RLS + explicit org filter apply
        # to this lock too; another tenant's account is never a writable target.
        account = await (
            self._accounts.get_by_id(account_id) if dry_run
            else self._accounts.get_by_id_for_update(account_id)
        )
        if account is None:
            raise HTTPException(status_code=404, detail="Bank account not found")
        if not account.is_active:
            raise HTTPException(409, "Bank account is inactive; use an active account for new imports")
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

    def _context(self):
        if self._user_id is None:
            raise ValueError("Matching and booking require an authenticated actor")
        return FinancialScopeService(self._session, self._org_id, self._user_id)

    async def list_transactions(self, *, bank_account_id=None, status="NEU", limit=50, offset=0):
        if bank_account_id is not None and await self._accounts.get_by_id(bank_account_id) is None:
            raise HTTPException(404, "Bank account not found")
        rows = await self._transactions.list_page(
            bank_account_id=bank_account_id, status=status, limit=limit, offset=offset)
        items = []
        for row in rows[:limit]:
            suggestions = await self._suggestions(row) if row.status == "NEU" else ()
            items.append({key: getattr(row, key) for key in (
                "id", "bank_account_id", "booking_date", "value_date", "amount",
                "counterparty_name", "counterparty_iban", "purpose", "status")}
                | {"suggestions": suggestions})
        return {"items": items, "next_offset": offset + limit if len(rows) > limit else None}

    async def _suggestions(self, transaction):
        row = BankImportRow(**{key: getattr(transaction, key) for key in (
            "booking_date", "value_date", "amount", "counterparty_name",
            "counterparty_iban", "purpose", "import_hash")})
        period = matching_period(row)
        if period is None or row.amount <= 0:
            return ()
        year, month, _ = period
        month_start = date(year, month, 1)
        month_end = date(year, month, monthrange(year, month)[1])
        account = await self._accounts.get_by_id(transaction.bank_account_id)
        if account is None:
            raise HTTPException(404, "Bank account not found")
        context = self._context()
        payments = PaymentService(self._session, self._org_id, self._user_id)
        targets = []
        async with context.using(account, computation=True) as scope_id:
            sphere = await context.select_read_scope(scope_id)
            if sphere is not None and (month_end < sphere.valid_from
                    or sphere.valid_to is not None and month_start > sphere.valid_to):
                return ()
            start, end = date(year, 1, 1), date(year, 12, 31)
            if sphere is not None:
                start = max(start, sphere.valid_from)
                end = min(end, sphere.valid_to or end)
            if sphere is None or sphere.scope_type == "MIET":
                leases = await LeaseAgreementRepository(self._session, self._org_id).matching_targets(
                    account.property_id, month_start, month_end)
                for lease, person in leases:
                    statement = await payments.account_statement_for_lease(lease.id, start, end, scope_id)
                    targets.append(MatchTarget(
                        name=f"{person.first_name} {person.last_name}".strip(), iban=person.iban,
                        open_items=statement.open_items, lease_agreement_id=lease.id,
                        tenant_profile_id=lease.tenant_profile_id))
            if sphere is None or sphere.scope_type == "WEG":
                plans = [plan for plan in await EconomicPlanRepository(
                    self._session, self._org_id).list_by_property(account.property_id)
                    if plan.status == "finalized" and plan.superseded_by_id is None
                    and plan.period_start <= month_end and plan.period_end >= month_start]
                if len(plans) > 1:
                    raise HTTPException(409, "Ambiguous finalized economic plans for matching period")
                if plans:
                    plan = plans[0]
                    # A visible plan in another authorized sphere must never
                    # replace the bank account's selected calculation context.
                    async with context.using(plan) as plan_scope:
                        if plan_scope != scope_id:
                            raise HTTPException(409, "Economic plan differs from bank account scope")
                    owners = await OwnerProfileRepository(self._session, self._org_id).matching_targets(
                        account.property_id, month_start, month_end)
                    for owner, person in owners:
                        statement = await payments.account_statement_for_owner(
                            owner.id, plan.id, max(start, plan.period_start), min(end, plan.period_end),
                            scope_id, property_id=account.property_id)
                        targets.append(MatchTarget(
                            name=f"{person.first_name} {person.last_name}".strip(), iban=person.iban,
                            open_items=statement.open_items, owner_profile_id=owner.id))
        return suggest_matches(row, targets)

    async def _pending_transaction(self, transaction_id):
        # Tenant filter + populate_existing in the shared repository are
        # essential after waiting for a concurrent assignment/ignore to commit.
        transaction = await self._transactions.get_by_id_for_update(transaction_id)
        if transaction is None:
            raise HTTPException(404, "Bank transaction not found")
        account = await self._accounts.get_by_id(transaction.bank_account_id)
        if account is None:
            raise HTTPException(404, "Bank account not found")
        if transaction.status != "NEU":
            raise HTTPException(409, "Bank transaction has already been processed")
        return transaction, account

    async def _validate_target(self, account, line):
        if line["payment_type"] == "RUECKLAGENENTNAHME":
            return
        start = date(line["period_year"], line["period_month"], 1)
        end = date(start.year, start.month, monthrange(start.year, start.month)[1])
        if line.get("lease_agreement_id") is not None:
            lease = await LeaseAgreementRepository(self._session, self._org_id).get_by_id(line["lease_agreement_id"])
            if lease is None:
                raise HTTPException(404, "Lease agreement not found")
            profile_id = line.get("tenant_profile_id") or lease.tenant_profile_id
            if await TenantProfileRepository(self._session, self._org_id).get_by_id(profile_id) is None:
                raise HTTPException(404, "Tenant profile not found")
            unit = await UnitRepository(self._session, self._org_id).get_by_id(lease.unit_id)
            if unit is None:
                raise HTTPException(404, "Unit not found")
            if unit.property_id != account.property_id or profile_id != lease.tenant_profile_id:
                raise HTTPException(422, "Lease, tenant and bank account must belong to the same target")
            if lease.valid_from > end or lease.valid_to is not None and lease.valid_to <= start:
                raise HTTPException(422, "Payment period is outside lease agreement")
            line["tenant_profile_id"] = profile_id
        else:
            owners = OwnerProfileRepository(self._session, self._org_id)
            if await owners.get_by_id(line["owner_profile_id"]) is None:
                raise HTTPException(404, "Owner profile not found")
            eligible = await owners.matching_targets(account.property_id, start, end)
            if not any(owner.id == line["owner_profile_id"] for owner, _ in eligible):
                raise HTTPException(422, "Owner is outside bank account property or payment period")

    async def assign(self, transaction_id, lines):
        transaction, account = await self._pending_transaction(transaction_id)
        if sum((line["amount"] for line in lines), Decimal("0.00")) != abs(transaction.amount):
            raise HTTPException(422, "Split amounts must cover the bank transaction exactly")
        context = self._context()
        scope_id = await context.enter(account, capability=ScopeCapability.PREPARE)
        if scope_id is not None:
            sphere = await AuthorizationService(self._session, AuthContext(
                self._user_id, self._org_id, "live database role required")).require(scope_id, ScopeCapability.CONFIRM)
            if sphere.scope_type not in {"MIET", "WEG"}:
                raise HTTPException(422, "Unsupported accounting scope for bank assignment")
            if transaction.value_date < sphere.valid_from or sphere.valid_to is not None and transaction.value_date > sphere.valid_to:
                raise HTTPException(422, "Bank value date is outside accounting scope")
            for line in lines:
                tenant_line = line["payment_type"] in TENANT_TYPES or line.get("lease_agreement_id") is not None
                if tenant_line != (sphere.scope_type == "MIET"):
                    raise HTTPException(422, "Payment type differs from bank account scope")
                start = date(line["period_year"], line["period_month"], 1)
                end = date(start.year, start.month, monthrange(start.year, start.month)[1])
                if end < sphere.valid_from or sphere.valid_to is not None and start > sphere.valid_to:
                    raise HTTPException(422, "Payment period is outside accounting scope")
        for line in lines:
            await self._validate_target(account, line)
        payments = PaymentService(self._session, self._org_id, self._user_id)
        booked = []
        for line in lines:
            payment = await payments.create_draft(
                **line, bank_account_id=account.id, bank_transaction_id=transaction.id,
                direction="IN" if transaction.amount > 0 else "OUT", value_date=transaction.value_date)
            if payment.payment_type == 'RUECKLAGENENTNAHME' and not (payment.reserve_cost_entry_id and payment.reserve_register_entry_id):
                booked.append(payment)
            else:
                booked.append(await payments.book(payment.id))
        transaction.status = "ZUGEORDNET"
        await self._transactions.update(transaction)
        return transaction, booked

    async def ignore(self, transaction_id):
        transaction, account = await self._pending_transaction(transaction_id)
        await self._context().enter(account, capability=ScopeCapability.PREPARE)
        transaction.status = "IGNORIERT"
        await self._transactions.update(transaction)
        return transaction
