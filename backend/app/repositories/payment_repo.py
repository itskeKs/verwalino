"""Payment repository -- tenant-scoped.

Extends TenantRepository with booked/active queries and the DERIVED filter
queries (m6 section 6.1): the per-size payment loads (NK per lease/year,
Hausgeld per owner/year) that feed the abrechnungs-anbindung. The repository
only LOADS the right rows; the cent-summing is the pure engine
(app.engine.mietkonto.derive_advance_total) so the math stays testable.
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment import Payment
from app.models.accounting_scope import AccountingScopeBinding
from app.repositories.base import TenantRepository


class PaymentRepository(TenantRepository[Payment]):
    model = Payment

    # ------------------------------------------------------------------
    # General queries
    # ------------------------------------------------------------------
    async def list_all_ordered(self) -> Sequence[Payment]:
        stmt = self._base_query().order_by(Payment.value_date.desc(), Payment.created_at.desc())
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_by_filters(
        self,
        *,
        bank_account_id: uuid.UUID | None = None,
        lease_agreement_id: uuid.UUID | None = None,
        tenant_profile_id: uuid.UUID | None = None,
        owner_profile_id: uuid.UUID | None = None,
        payment_type: str | None = None,
        period_year: int | None = None,
        status: str | None = None,
    ) -> Sequence[Payment]:
        stmt = self._base_query()
        if bank_account_id is not None:
            stmt = stmt.where(Payment.bank_account_id == bank_account_id)
        if lease_agreement_id is not None:
            stmt = stmt.where(Payment.lease_agreement_id == lease_agreement_id)
        if tenant_profile_id is not None:
            stmt = stmt.where(Payment.tenant_profile_id == tenant_profile_id)
        if owner_profile_id is not None:
            stmt = stmt.where(Payment.owner_profile_id == owner_profile_id)
        if payment_type is not None:
            stmt = stmt.where(Payment.payment_type == payment_type)
        if period_year is not None:
            stmt = stmt.where(Payment.period_year == period_year)
        if status == "cancelled":
            stmt = stmt.where(Payment.cancelled_at.is_not(None))
        elif status is not None:
            stmt = stmt.where(Payment.status == status, Payment.cancelled_at.is_(None))
        stmt = stmt.order_by(Payment.value_date.desc(), Payment.created_at.desc())
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_for_lease(self, lease_agreement_id: uuid.UUID) -> Sequence[Payment]:
        stmt = (
            self._base_query()
            .where(Payment.lease_agreement_id == lease_agreement_id)
            .order_by(Payment.value_date, Payment.created_at)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_for_owner(self, owner_profile_id: uuid.UUID) -> Sequence[Payment]:
        stmt = (
            self._base_query()
            .where(Payment.owner_profile_id == owner_profile_id)
            .order_by(Payment.value_date, Payment.created_at)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    # ------------------------------------------------------------------
    # DERIVED filter queries (m6 section 6.1/6.2/6.3). booked + not reversed +
    # not a reversal row (Storno Variante A, section 6.6). The pure engine then
    # sums; we deliberately load the SAME rows the engine filters so the DB
    # query and the pure derivation are consistent.
    # ------------------------------------------------------------------
    def _booked_active(self):
        return (
            self._base_query()
            .where(Payment.status == "booked")
            .where(Payment.reversed_by_id.is_(None))
            .where(Payment.reverses_id.is_(None))
        )

    async def derived_for_nk(
        self, lease_agreement_id: uuid.UUID, period_year: int
    ) -> Sequence[Payment]:
        """booked, active NK_VORAUSZAHLUNG payments for one lease + year (§6.2)."""
        stmt = (
            self._booked_active()
            .where(Payment.payment_type == "NK_VORAUSZAHLUNG")
            .where(Payment.lease_agreement_id == lease_agreement_id)
            .where(Payment.period_year == period_year)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def derived_for_hausgeld(
        self, owner_profile_id: uuid.UUID, period_year: int
    ) -> Sequence[Payment]:
        """booked, active HAUSGELD_VORSCHUSS payments for one owner + year (§6.3)."""
        stmt = (
            self._booked_active()
            .where(Payment.payment_type == "HAUSGELD_VORSCHUSS")
            .where(Payment.owner_profile_id == owner_profile_id)
            .where(Payment.period_year == period_year)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------
    async def update(self, entity: Payment) -> Payment:
        """Flush changes on an already-tracked entity (trigger blocks booked)."""
        await self._session.flush()
        return entity

    async def delete(self, entity: Payment) -> None:
        """Delete a draft payment. Trigger blocks booked rows."""
        await self._session.delete(entity)
        await self._session.flush()

    async def is_scope_bound(self, payment_id: uuid.UUID) -> bool:
        return await self._session.scalar(select(AccountingScopeBinding.id).where(
            AccountingScopeBinding.organization_id == self._org_id,
            AccountingScopeBinding.payment_id == payment_id,
        )) is not None
