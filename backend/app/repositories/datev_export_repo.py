"""Current financial source rows for one fully admitted property export."""
from sqlalchemy import select

from app.models.bank_account import BankAccount
from app.models.cost_category import CostCategory
from app.models.cost_entry import CostEntry
from app.models.payment import Payment
from app.repositories.base import TenantRepository


class DatevExportRepository:
    def __init__(self, session, org_id):
        self.session, self.org_id = session, org_id

    async def rows(self, property_id, start, end):
        payments = TenantRepository[Payment](self.session, self.org_id)
        payments.model = Payment
        costs = TenantRepository[CostEntry](self.session, self.org_id)
        costs.model = CostEntry
        # A full current-state projection, not a previously-exported delta.
        payment_rows = (await self.session.scalars(payments._base_query().where(
            Payment.property_id == property_id, Payment.status == 'booked', Payment.cancelled_at.is_(None),
            Payment.reversed_by_id.is_(None), Payment.reverses_id.is_(None),
            Payment.value_date.between(start, end)).order_by(Payment.value_date, Payment.id))).all()
        cost_rows = (await self.session.scalars(costs._base_query().where(
            CostEntry.property_id == property_id, CostEntry.status == 'confirmed', CostEntry.superseded_by_id.is_(None),
            CostEntry.invoice_date.between(start, end)).order_by(CostEntry.invoice_date, CostEntry.id))).all()
        banks = (await self.session.execute(select(BankAccount.id, BankAccount.label).where(
            BankAccount.organization_id == self.org_id,
            BankAccount.id.in_({p.bank_account_id for p in payment_rows})).order_by(BankAccount.id))).all()
        categories = (await self.session.execute(select(CostCategory.id, CostCategory.code, CostCategory.label).where(
            CostCategory.id.in_({c.cost_category_id for c in cost_rows})).order_by(CostCategory.id))).all()
        return payment_rows, cost_rows, banks, categories
