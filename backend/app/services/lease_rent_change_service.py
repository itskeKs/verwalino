"""The single append-only rental-rate mutation path (T23)."""
from __future__ import annotations

import uuid
from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.models.audit_event import AuditEvent
from app.models.lease_rent_change import LeaseRentChange
from app.repositories.audit_repo import AuditRepository
from app.repositories.lease_agreement_repo import LeaseAgreementRepository
from app.repositories.lease_rent_change_repo import LeaseRentChangeRepository
from app.services.financial_scope_service import FinancialScopeService
from app.services.history_source_access import require_history_prepare


class LeaseRentChangeService:
    def __init__(self, session, org_id, user_id):
        self.session, self.org_id, self.user_id = session, org_id, user_id

    async def create(self, lease_id: uuid.UUID, valid_from: date,
                     advance_payment: Decimal, *, base_rent: Decimal | None = None):
        # All callers serialize financial inputs BEFORE locking a contract.
        await FinancialScopeService(self.session, self.org_id, self.user_id).lock_inputs()
        lease = await LeaseAgreementRepository(self.session, self.org_id).get_by_id_for_update(lease_id)
        if lease is None:
            raise HTTPException(404, "Lease agreement not found")
        await require_history_prepare(self.session, self.org_id, "lease_agreement", lease)
        if valid_from < lease.valid_from or (lease.valid_to is not None and valid_from >= lease.valid_to):
            raise HTTPException(422, "Rent change must be within the lease period")
        if base_rent is None:
            base_rent = lease.base_rent
            for change in await LeaseRentChangeRepository(self.session, self.org_id).list_for_lease(lease_id):
                if change.valid_from <= valid_from:
                    base_rent = change.base_rent
        row = LeaseRentChange(
            id=uuid.uuid4(), lease_agreement_id=lease.id, valid_from=valid_from,
            base_rent=base_rent, advance_payment=advance_payment,
        )
        try:
            async with self.session.begin_nested():
                await LeaseRentChangeRepository(self.session, self.org_id).create(row)
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == "23505":
                raise HTTPException(409, "A rent change already exists for this lease and date") from exc
            raise
        await AuditRepository(self.session, self.org_id).create(AuditEvent(
            actor_user_id=self.user_id, entity_type="lease_agreement", entity_id=lease.id,
            action="lease.rent_change_created", payload_diff={
                "rent_change_id": str(row.id), "valid_from": row.valid_from.isoformat(),
                "base_rent": f"{row.base_rent:.2f}", "advance_payment": f"{row.advance_payment:.2f}",
            },
        ))
        return row

    async def create_staffel(self, lease_id: uuid.UUID, start: date, interval: int, steps: int,
                             *, increase_amount: Decimal | None, increase_percent: Decimal | None):
        # Lock before reading history; otherwise concurrent batches could use a stale base rate.
        await FinancialScopeService(self.session, self.org_id, self.user_id).lock_inputs()
        lease = await LeaseAgreementRepository(self.session, self.org_id).get_by_id_for_update(lease_id)
        if lease is None:
            raise HTTPException(404, "Lease agreement not found")
        await require_history_prepare(self.session, self.org_id, "lease_agreement", lease)
        history = await LeaseRentChangeRepository(self.session, self.org_id).list_for_lease(lease_id)
        if any(row.valid_from >= start for row in history):
            raise HTTPException(409, "Rent history already exists at or after the staffel start")
        source = next((row for row in reversed(history) if row.valid_from < start), lease)
        rent, advance = source.base_rent, source.advance_payment
        try:
            earliest = source.valid_from.replace(
                year=source.valid_from.year + 1,
                day=min(source.valid_from.day, monthrange(source.valid_from.year + 1, source.valid_from.month)[1]),
            )
        except ValueError as exc:
            raise HTTPException(422, "Staffel dates exceed supported calendar range") from exc
        if start < earliest:
            raise HTTPException(422, "Staffel rent must remain unchanged for at least one year")
        planned = []
        for step in range(steps):
            # Anchor every date to the requested start, not to a previously clamped month.
            absolute_month = start.year * 12 + start.month - 1 + step * interval
            year, month = divmod(absolute_month, 12)
            month += 1
            try:
                effective = date(year, month, min(start.day, monthrange(year, month)[1]))
            except ValueError as exc:
                raise HTTPException(422, "Staffel dates exceed supported calendar range") from exc
            if effective < lease.valid_from or (lease.valid_to is not None and effective >= lease.valid_to):
                raise HTTPException(422, "Rent change must be within the lease period")
            rent = (rent + increase_amount if increase_amount is not None else
                    rent * (Decimal("1") + increase_percent)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if rent >= Decimal("1000000000000"):
                raise HTTPException(422, "Generated rent exceeds the supported amount")
            planned.append((effective, rent))
        rows = []
        for effective, rent in planned:
            rows.append(await self.create(lease_id, effective, advance, base_rent=rent))
        return rows
