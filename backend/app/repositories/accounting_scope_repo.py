"""Tenant-filtered access to scope setup and immutable associations."""
import uuid

from sqlalchemy import select

from app.models.accounting_scope import AccountingScope, AccountingScopeBinding, AccountingScopeUnit
from app.models.bank_account import BankAccount
from app.models.cost_entry import CostEntry
from app.models.economic_plan import EconomicPlan
from app.models.payment import Payment
from app.models.settlement_run import SettlementRun
from app.repositories.base import TenantRepository

RESOURCE_MODELS = {'bank_account': BankAccount, 'payment': Payment, 'cost_entry': CostEntry,
                   'settlement_run': SettlementRun, 'economic_plan': EconomicPlan}


class AccountingScopeRepository(TenantRepository[AccountingScope]):
    model = AccountingScope

    async def by_request(self, actor_id: uuid.UUID, key: uuid.UUID):
        return await self._session.scalar(self._base_query().where(
            AccountingScope.created_by == actor_id, AccountingScope.request_key == key))

    async def units(self, scope_id: uuid.UUID):
        return (await self._session.scalars(select(AccountingScopeUnit).where(
            AccountingScopeUnit.organization_id == self._org_id,
            AccountingScopeUnit.scope_id == scope_id).order_by(AccountingScopeUnit.unit_id))).all()

    async def resource(self, kind: str, resource_id: uuid.UUID):
        model = RESOURCE_MODELS[kind]
        return await self._session.scalar(select(model).where(
            model.organization_id == self._org_id, model.id == resource_id).with_for_update())

    async def binding(self, kind: str, resource_id: uuid.UUID):
        return await self._session.scalar(select(AccountingScopeBinding).where(
            AccountingScopeBinding.organization_id == self._org_id,
            getattr(AccountingScopeBinding, kind + '_id') == resource_id))
