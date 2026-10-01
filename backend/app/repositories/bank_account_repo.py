"""BankAccount repository -- tenant-scoped.

M6 reads the account to mirror property_id/management_mandate_id onto a payment
(the Vermoegens-Kohaerenz, ADR-0010): the payment inherits Objekt/Mandat FROM
the account, it is not freely chosen. The Composite-FK enforces it hard; this
repo is how the service learns the account's property to set it.
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import and_, select
from app.models.property import Property

from app.models.bank_account import BankAccount
from app.repositories.base import TenantRepository


class BankAccountRepository(TenantRepository[BankAccount]):
    model = BankAccount

    async def list_by_property(self, property_id: uuid.UUID) -> Sequence[BankAccount]:
        stmt = self._base_query().where(BankAccount.property_id == property_id)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_with_property_names(self, property_id: uuid.UUID | None = None):
        statement = select(BankAccount, Property.name).outerjoin(Property, and_(
            Property.organization_id == BankAccount.organization_id,
            Property.id == BankAccount.property_id,
        )).where(BankAccount.organization_id == self._org_id).order_by(BankAccount.label, BankAccount.id)
        if property_id is not None:
            statement = statement.where(BankAccount.property_id == property_id)
        return (await self._session.execute(statement)).all()
