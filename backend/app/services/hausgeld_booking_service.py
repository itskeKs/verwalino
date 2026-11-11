"""Book the monthly Hausgeld of a finalized economic plan in one step (#186).

The plan names owner and monthly amount, the ownership periods name who owns a unit in each
month: `offer` lists one row per owner and month, built by the same engine call as the owner
statement (so a booked month leaves no one-cent open item). `book` creates and books the chosen
rows through the ordinary single-payment path (`PaymentService.create_draft` and `book`), in one
transaction: one refused row books nothing.

A month counts as booked when a booked, not reversed Hausgeld payment of that owner exists for
it on the plan's property; it is neither offered nor bookable again. `book` takes the
organization-wide financial input lock before it reads that state, so two identical requests at
the same moment book once and the second one is refused.
"""

from __future__ import annotations

import uuid
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Sequence

from fastapi import HTTPException

from app.core.rbac import ScopeCapability
from app.models.economic_plan import EconomicPlan
from app.models.payment import Payment
from app.repositories.bank_account_repo import BankAccountRepository
from app.repositories.economic_plan_repo import EconomicPlanRepository
from app.repositories.owner_profile_repo import OwnerProfileRepository
from app.repositories.payment_repo import PaymentRepository
from app.services.financial_scope_service import FinancialScopeService
from app.services.payment_service import PaymentService

# One request books at most twelve owners for a whole year; the request holds the organization-wide
# financial input lock while it runs. The page books one owner (twelve rows) at a time.
MAX_ROWS = 144
_MAX_AMOUNT = Decimal("1e12")  # NUMERIC(14,2): at most twelve integer digits


@dataclass(frozen=True)
class HausgeldRow:
    owner_profile_id: uuid.UUID
    period_year: int
    period_month: int
    amount: Decimal
    value_date: date
    booked: bool


@dataclass(frozen=True)
class RequestedRow:
    owner_profile_id: uuid.UUID
    period_year: int
    period_month: int
    amount: str
    value_date: date


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message})


def _amount(value: str) -> Decimal:
    """A positive money amount with at most two decimals; never a float, NaN or infinity."""
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError):
        amount = None
    if (amount is None or not amount.is_finite() or amount <= 0 or amount >= _MAX_AMOUNT
            or amount.as_tuple().exponent < -2):
        raise _error(422, "HAUSGELD_AMOUNT_INVALID",
                     "Bitte geben Sie je Monat einen Betrag über 0,00 € mit höchstens zwei Nachkommastellen an.")
    return amount.quantize(Decimal("0.01"))


class HausgeldBookingService:
    def __init__(self, session, org_id: uuid.UUID, user_id: uuid.UUID):
        self._session = session
        self._org_id = org_id
        self._user_id = user_id
        self._scope = FinancialScopeService(session, org_id, user_id)

    async def offer(self, plan_id: uuid.UUID) -> list[HausgeldRow]:
        plan = await self._open_plan(plan_id, ScopeCapability.READ)
        return await self._rows(plan)

    async def book(
        self, plan_id: uuid.UUID, bank_account_id: uuid.UUID, requested: Sequence[RequestedRow],
    ) -> list[Payment]:
        # Everything below reads the booked months: serialize with every other financial writer first.
        await self._scope.lock_inputs()
        plan = await self._open_plan(plan_id, ScopeCapability.PREPARE)
        account = await BankAccountRepository(self._session, self._org_id).get_by_id(bank_account_id)
        if account is None:
            raise _error(404, "BANK_ACCOUNT_NOT_FOUND", "Bankkonto nicht gefunden.")
        if account.property_id != plan.property_id:
            raise _error(422, "HAUSGELD_ACCOUNT_PROPERTY_MISMATCH",
                         "Das Bankkonto gehört nicht zum Objekt des Wirtschaftsplans.")
        amounts = [_amount(row.amount) for row in requested]
        keys = [(row.owner_profile_id, row.period_year, row.period_month) for row in requested]
        if len(set(keys)) != len(keys):
            raise _error(422, "HAUSGELD_ROW_DUPLICATE", "Jeder Monat darf nur einmal je Eigentümer gebucht werden.")
        offer = {(row.owner_profile_id, row.period_year, row.period_month): row for row in await self._rows(plan)}
        owners = OwnerProfileRepository(self._session, self._org_id)
        for owner_id in {key[0] for key in keys} - {key[0] for key in offer}:
            if await owners.get_by_id(owner_id) is None:
                raise _error(404, "OWNER_PROFILE_NOT_FOUND", "Eigentümer nicht gefunden.")
        for key in keys:
            if key not in offer:
                raise _error(422, "HAUSGELD_ROW_NOT_OFFERED",
                             "Für diesen Eigentümer und Monat enthält der Wirtschaftsplan kein Hausgeld.")
        for key in keys:
            if offer[key].booked:
                raise _error(409, "HAUSGELD_MONTH_BOOKED",
                             f"Für diesen Eigentümer ist der Monat {key[2]:02d}/{key[1]} bereits gebucht.")

        payments = PaymentService(self._session, self._org_id, self._user_id)
        booked: list[Payment] = []
        for row, amount in sorted(zip(requested, amounts), key=lambda pair: (
                pair[0].period_year, pair[0].period_month, str(pair[0].owner_profile_id))):
            draft = await payments.create_draft(
                bank_account_id=account.id, direction="IN", amount=amount, value_date=row.value_date,
                payment_type="HAUSGELD_VORSCHUSS", owner_profile_id=row.owner_profile_id,
                period_year=row.period_year, period_month=row.period_month)
            booked.append(await payments.book(draft.id))
        return booked

    async def _open_plan(self, plan_id: uuid.UUID, capability: ScopeCapability) -> EconomicPlan:
        plan = await EconomicPlanRepository(self._session, self._org_id).get_by_id(plan_id)
        if plan is None:
            raise HTTPException(404, "Wirtschaftsplan nicht gefunden.")
        await self._scope.enter(plan, capability=capability, computation=True)
        if plan.status != "finalized":
            raise _error(409, "ECONOMIC_PLAN_NOT_FINALIZED",
                         "Nur ein finalisierter Wirtschaftsplan kann als Hausgeld gebucht werden.")
        if plan.superseded_by_id is not None:
            raise _error(409, "ECONOMIC_PLAN_SUPERSEDED",
                         "Der Wirtschaftsplan wurde durch eine Korrektur ersetzt. Bitte buchen Sie mit der Korrektur.")
        return plan

    async def _rows(self, plan: EconomicPlan) -> list[HausgeldRow]:
        charges = await PaymentService(self._session, self._org_id, self._user_id).plan_hausgeld_charges(plan)
        booked = await PaymentRepository(self._session, self._org_id).booked_hausgeld_months(
            plan.property_id, plan.period_start.year, plan.period_end.year)
        due_day = plan.monthly_due_day or 1
        return sorted((
            HausgeldRow(
                owner_profile_id=charge.owner_profile_id, period_year=charge.period_year,
                period_month=charge.period_month, amount=charge.amount,
                # The plan's due day of the month, clamped to its last day; the user may change it per row.
                value_date=date(charge.period_year, charge.period_month,
                                min(due_day, monthrange(charge.period_year, charge.period_month)[1])),
                booked=(charge.owner_profile_id, charge.period_year, charge.period_month) in booked)
            for charge in charges
        ), key=lambda row: (row.period_year, row.period_month, str(row.owner_profile_id)))
