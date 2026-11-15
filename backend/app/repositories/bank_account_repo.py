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
from app.models.accounting_scope import AccountingScope, AccountingScopeBinding
from app.models.property import Property

from app.models.bank_account import BankAccount
from app.repositories.base import TenantRepository


class BankAccountRepository(TenantRepository[BankAccount]):
    model = BankAccount

    async def list_by_property(self, property_id: uuid.UUID) -> Sequence[BankAccount]:
        stmt = self._base_query().where(BankAccount.property_id == property_id)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def scope_label(self, account_id: uuid.UUID) -> str | None:
        """Label of the accounting scope the account is classified into, None while unclassified."""
        return await self._session.scalar(select(AccountingScope.label).join(AccountingScopeBinding, and_(
            AccountingScopeBinding.organization_id == AccountingScope.organization_id,
            AccountingScopeBinding.scope_id == AccountingScope.id,
        )).where(AccountingScopeBinding.organization_id == self._org_id, AccountingScopeBinding.bank_account_id == account_id))

    async def list_with_property_names(self, property_id: uuid.UUID | None = None):
        statement = select(BankAccount, Property.name, AccountingScope.label).outerjoin(Property, and_(
            Property.organization_id == BankAccount.organization_id,
            Property.id == BankAccount.property_id,
        )).outerjoin(AccountingScopeBinding, and_(
            AccountingScopeBinding.organization_id == BankAccount.organization_id,
            AccountingScopeBinding.bank_account_id == BankAccount.id,
        )).outerjoin(AccountingScope, and_(
            AccountingScope.organization_id == AccountingScopeBinding.organization_id,
            AccountingScope.id == AccountingScopeBinding.scope_id,
        )).where(BankAccount.organization_id == self._org_id).order_by(BankAccount.label, BankAccount.id)
        if property_id is not None:
            statement = statement.where(BankAccount.property_id == property_id)
        return (await self._session.execute(statement)).all()
