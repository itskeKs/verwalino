"""BankAccount repository -- tenant-scoped.

M6 reads the account to mirror property_id/management_mandate_id onto a payment
(the Vermoegens-Kohaerenz, ADR-0010): the payment inherits Objekt/Mandat FROM
the account, it is not freely chosen. The Composite-FK enforces it hard; this
repo is how the service learns the account's property to set it.
"""

from __future__ import annotations

import uuid
from typing import Sequence

from app.models.bank_account import BankAccount
from app.repositories.base import TenantRepository


class BankAccountRepository(TenantRepository[BankAccount]):
    model = BankAccount

    async def list_by_property(self, property_id: uuid.UUID) -> Sequence[BankAccount]:
        stmt = self._base_query().where(BankAccount.property_id == property_id)
        result = await self._session.execute(stmt)
        return result.scalars().all()
