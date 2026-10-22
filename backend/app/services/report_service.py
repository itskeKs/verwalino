"""Read-only object-year overview, not a tax classification or settlement."""
from datetime import date
from decimal import Decimal, localcontext
from typing import Annotated, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.cost_entry_repo import CostEntryRepository
from app.repositories.payment_repo import PaymentRepository
from app.repositories.property_repo import PropertyRepository
from app.services.financial_scope_service import FinancialScopeService

Money = Annotated[str, Field(pattern=r"^-?\d+\.\d{2}$")]


class ReportIncome(BaseModel):
    payment_type: str
    amount: Money


class ReportExpense(BaseModel):
    category_id: UUID
    category_code: str
    category_name: str
    tenant_apportionability: str
    amount: Money


class PropertyYearReport(BaseModel):
    property_id: UUID
    property_name: str
    year: int
    period_start: date
    period_end: date
    accounting_scope_id: UUID | None
    income_date_basis: Literal['value_date'] = 'value_date'
    expense_date_basis: Literal['invoice_date'] = 'invoice_date'
    income: list[ReportIncome]
    expenses: list[ReportExpense]
    income_total: Money
    expense_total: Money
    balance: Money
    footnote: str = (
        'Fachliche Jahresübersicht nach Wertstellungs- und Rechnungsdatum; '
        'keine vollständige Steuererklärung und keine steuerliche Einordnung der Zahlungstypen.'
    )


class ReportService:
    def __init__(self, session: AsyncSession, org_id: UUID, user_id: UUID):
        self._session, self._org_id, self._user_id = session, org_id, user_id

    async def property_year(self, property_id: UUID, year: int,
                            accounting_scope_id: UUID | None = None) -> PropertyYearReport:
        if not 1 <= year <= 9999:
            raise HTTPException(422, 'Year must be between 1 and 9999')
        start, end = date(year, 1, 1), date(year, 12, 31)
        prop, sphere = await self.property_period(property_id, start, end, accounting_scope_id)
        return await self._year_totals(prop, sphere, year, start, end)

    async def property_period(self, property_id: UUID, start: date, end: date,
                              accounting_scope_id: UUID | None = None):
        """Admit a complete property universe before any financial row enumeration."""
        if start > end:
            raise HTTPException(422, 'Report period must not end before it starts')
        context = FinancialScopeService(self._session, self._org_id, self._user_id)
        await context.lock_inputs()
        prop = await PropertyRepository(self._session, self._org_id).get_by_id(property_id)
        if prop is None:
            raise HTTPException(404, 'Property not found')
        if accounting_scope_id is None and await self._session.scalar(text(
            'SELECT property_has_classified_finance(:org,:property)'
        ), {'org': self._org_id, 'property': property_id}):
            raise HTTPException(409, 'Select an exact accounting scope for this property')
        sphere = await context.select_read_scope(accounting_scope_id)
        if sphere is not None:
            if sphere.property_id != property_id:
                raise HTTPException(422, 'Accounting scope belongs to another property')
            if not sphere.whole_property:
                raise HTTPException(422, 'Object-year report requires a whole-property accounting scope')
            if sphere.scope_type not in {'MIET', 'WEG'} or (
                prop.management_type in {'MIET', 'WEG'} and sphere.scope_type != prop.management_type
            ):
                raise HTTPException(422, 'Accounting scope does not support this property report')
            if start < sphere.valid_from or sphere.valid_to is not None and end > sphere.valid_to:
                raise HTTPException(422, 'Report period is outside accounting scope')
        if prop.management_type == 'SEV':
            raise HTTPException(422, 'SEV financial adapters are not available yet')
        if not await self._session.scalar(text(
            "SELECT property_financial_disclosure_access(:org,:property,'read')"
        ), {'org': self._org_id, 'property': property_id}):
            raise HTTPException(404, 'Complete property sources not accessible')

        return prop, sphere

    async def _year_totals(self, prop, sphere, year: int, start: date, end: date) -> PropertyYearReport:
        property_id = prop.id
        income_rows = await PaymentRepository(self._session, self._org_id).property_year_totals(property_id, start, end)
        expense_rows = await CostEntryRepository(self._session, self._org_id).property_year_totals(property_id, start, end)
        amounts = [row.amount for row in [*income_rows, *expense_rows]]
        # SQL sums retain cents; leave room for every further addition even if
        # the caller runs with a lower Decimal precision.
        with localcontext() as arithmetic:
            arithmetic.prec = max(28, max((len(amount.as_tuple().digits) for amount in amounts),
                                         default=1) + len(str(len(amounts) + 1)) + 2)
            income_total = sum((row.amount for row in income_rows), Decimal('0.00'))
            expense_total = sum((row.amount for row in expense_rows), Decimal('0.00'))
            balance = income_total - expense_total
        return PropertyYearReport(
            property_id=prop.id, property_name=prop.name, year=year,
            period_start=start, period_end=end, accounting_scope_id=sphere.id if sphere else None,
            income=[ReportIncome(payment_type=row.payment_type, amount=format(row.amount, '.2f')) for row in income_rows],
            expenses=[ReportExpense(category_id=row.category_id, category_code=row.category_code,
                category_name=row.category_name, tenant_apportionability=row.tenant_apportionability,
                amount=format(row.amount, '.2f')) for row in expense_rows],
            income_total=format(income_total, '.2f'), expense_total=format(expense_total, '.2f'),
            balance=format(balance, '.2f'),
        )
