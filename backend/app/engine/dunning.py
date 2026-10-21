"""M7 Dunning engine -- pure functions for overdue detection and escalation.

HARD RULE (same as the rest of app/engine, SDD section 16):
  This module contains ONLY pure computation. It MUST NOT import from
  app.models, app.repositories, app.services, app.api, sqlalchemy, fastapi,
  or perform any DB / HTTP / file I/O.

Responsibilities:
  1. Overdue detection: given open items + a reference date, determine which
     items are overdue (Soll > Ist, past grace period).
  2. Escalation level: given the count of already-issued dunning notices for
     a person, compute the next dunning level (1 -> 2 -> 3).
  3. Fee calculation: per-level provisional default fees
     [section 18/fachlich -- must be appropriate/contractually agreed].
  4. Dunning notice assembly: combine overdue items into a DunningInput with
     principal, fee, total, and the included items snapshot.

Money is ALWAYS Decimal, never float.

[section 18/fachlich] Provisionally marked items:
  - Grace period (Karenzfrist): default 0 days (every overdue item qualifies).
    The correct grace period depends on lease/contract terms and is to be
    confirmed by a legal expert before productive use.
  - Due date for the dunning notice: issue_date + 14 days (provisional).
  - Dunning fee defaults: Level 1: 0.00, Level 2: 5.00, Level 3: 10.00 EUR.
  - Interest uses an explicitly supplied base percentage; no market rate is assumed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext

from app.engine.mietkonto import OpenItem

# ---------------------------------------------------------------------------
# [section 18/fachlich] Provisional defaults -- must be validated by legal.
# ---------------------------------------------------------------------------

# Default grace period in days after the Soll due date before an item is
# considered overdue. 0 = no grace, item is overdue on the first day after
# its period ends without full payment. Overridable per call.
DEFAULT_GRACE_DAYS: int = 0

# Dunning fee per level. Overridable at dunning creation time.
# [section 18/fachlich]: fees must be appropriate and contractually agreed.
DEFAULT_FEES: dict[int, Decimal] = {
    1: Decimal("0.00"),   # Zahlungserinnerung -- no fee
    2: Decimal("5.00"),   # 1. Mahnung
    3: Decimal("10.00"),  # letzte Mahnung
}

# Default payment deadline offset from issue date.
DEFAULT_DUE_DAYS: int = 14

# Maximum dunning level in the escalation chain.
MAX_LEVEL: int = 3

CENT = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("Money must be a finite Decimal")
    if value.copy_abs() >= Decimal("1000000000000"):
        raise ValueError("Dunning amount exceeds Numeric(14,2)")
    with localcontext() as ctx:
        ctx.prec = max(50, len(value.as_tuple().digits) + 5)
        result = value.quantize(CENT, rounding=ROUND_HALF_UP)
    if result.copy_abs() > Decimal("999999999999.99"):
        raise ValueError("Dunning amount exceeds Numeric(14,2)")
    return result


# ---------------------------------------------------------------------------
# Inputs / Outputs (frozen copies)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OverdueItem:
    """An open item that is overdue (arrears, past grace period)."""
    period_year: int
    period_month: int | None
    payment_type: str
    soll: Decimal
    ist: Decimal
    open_amount: Decimal  # > 0 (arrears)
    due_date: date | None = None


@dataclass(frozen=True)
class DunningInput:
    """Assembled dunning notice data (ready for persistence)."""
    level: int
    issue_date: date
    due_date: date
    principal_amount: Decimal  # Sum of overdue arrears
    fee_amount: Decimal        # Dunning fee for this level
    total_amount: Decimal      # principal + fee + interest
    overdue_items: tuple[OverdueItem, ...]

    interest_amount: Decimal = Decimal("0.00")
    interest_snapshot: dict | None = None

    # Serializable snapshot for the JSONB column.
    def items_snapshot(self) -> list[dict]:
        return [
            {
                "period_year": it.period_year,
                "period_month": it.period_month,
                "payment_type": it.payment_type,
                "soll": str(it.soll),
                "ist": str(it.ist),
                "open_amount": str(it.open_amount),
                **({"due_date": it.due_date.isoformat()} if it.due_date is not None else {}),
            }
            for it in self.overdue_items
        ]


# ---------------------------------------------------------------------------
# Overdue detection
# ---------------------------------------------------------------------------

def _item_due_date(item: OpenItem) -> date:
    """Compute the due date for an open item.

    Frozen one-off charges carry their actual due date. For recurring items,
    [section 18/fachlich] provisionally use the 1st of the period month (or
    Jan 1 if period_month is None), pending lease-specific due terms.
    """
    if item.due_date is not None:
        return item.due_date
    year = item.period_year if item.period_year else date.today().year
    month = item.period_month if item.period_month else 1
    return date(year, month, 1)


def find_overdue_items(
    open_items: list[OpenItem],
    reference_date: date,
    grace_days: int = DEFAULT_GRACE_DAYS,
) -> list[OverdueItem]:
    """Filter open items to those that are overdue as of reference_date.

    An item is overdue if:
      - open_amount > 0 (arrears, not credit/balanced)
      - its due date + grace_days < reference_date

    Returns items sorted by (period_year, period_month, payment_type).
    """
    overdue: list[OverdueItem] = []
    for item in open_items:
        if item.open_amount <= Decimal("0"):
            continue
        item_due = _item_due_date(item)
        deadline = item_due + timedelta(days=grace_days)
        if reference_date > deadline:
            overdue.append(OverdueItem(
                period_year=item.period_year if item.period_year is not None else 0,
                period_month=item.period_month,
                payment_type=item.payment_type,
                soll=_money(item.soll),
                ist=_money(item.ist),
                open_amount=_money(item.open_amount),
                due_date=item.due_date,
            ))
    overdue.sort(key=lambda x: (x.period_year, x.period_month or 0, x.payment_type))
    return overdue


# ---------------------------------------------------------------------------
# Escalation level
# ---------------------------------------------------------------------------

def next_dunning_level(existing_notice_count: int) -> int:
    """Compute the next dunning level from the count of already-issued notices.

    Level 1 (Zahlungserinnerung) -> Level 2 (Mahnung) -> Level 3 (letzte Mahnung).
    Capped at MAX_LEVEL; further calls still return MAX_LEVEL (the service
    may decide whether to proceed or escalate to legal).
    """
    next_level = existing_notice_count + 1
    return min(next_level, MAX_LEVEL)


# ---------------------------------------------------------------------------
# Fee calculation
# ---------------------------------------------------------------------------

def dunning_fee(level: int, custom_fees: dict[int, Decimal] | None = None) -> Decimal:
    """Return the dunning fee for a given level.

    [section 18/fachlich] Uses provisional defaults; custom_fees override them.
    """
    fees = custom_fees if custom_fees is not None else DEFAULT_FEES
    return _money(fees.get(level, Decimal("0.00")))


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def assemble_dunning(
    overdue_items: list[OverdueItem],
    existing_notice_count: int,
    issue_date: date,
    due_days: int = DEFAULT_DUE_DAYS,
    custom_fees: dict[int, Decimal] | None = None,
    override_level: int | None = None,
    override_fee: Decimal | None = None,
    base_interest_rate: Decimal | None = None,
) -> DunningInput | None:
    """Assemble a dunning notice from overdue items.

    Returns None if there are no overdue items (nothing to dun).

    The level is derived from existing_notice_count (escalation) unless
    override_level is given. The fee is derived from the level unless
    override_fee is given.
    """
    if not overdue_items:
        return None

    level = override_level if override_level is not None else next_dunning_level(existing_notice_count)
    fee = _money(override_fee) if override_fee is not None else dunning_fee(level, custom_fees)
    # Do not depend on the caller's Decimal context or round a percentage first.
    if base_interest_rate is not None:
        if not isinstance(base_interest_rate, Decimal) or not base_interest_rate.is_finite():
            raise ValueError("Base interest rate must be a finite Decimal")
        if len(base_interest_rate.as_tuple().digits) > 100 or abs(base_interest_rate.as_tuple().exponent) > 100:
            raise ValueError("Base interest rate exceeds supported precision")
    with localcontext() as ctx:
        ctx.prec = 250
        principal = _money(sum((_money(it.open_amount) for it in overdue_items), Decimal("0")))
        interest = Decimal("0.00")
        snapshot = None
        if base_interest_rate is not None:
            annual_rate = base_interest_rate + Decimal("5")
            rows = []
            for item in overdue_items:
                due = item.due_date or date(item.period_year, item.period_month or 1, 1)
                days = max(0, (issue_date - due).days)
                item_principal = _money(item.open_amount)
                amount = _money(item_principal * annual_rate / Decimal("100") * Decimal(days) / Decimal("365"))
                interest += amount
                rows.append({
                    "payment_type": item.payment_type,
                    "period_year": item.period_year, "period_month": item.period_month,
                    "due_date": due.isoformat(), "days": days,
                    "principal": str(item_principal), "interest": str(amount),
                })
            interest = _money(interest)
            snapshot = {"base_interest_rate": str(base_interest_rate),
                        "annual_rate": str(annual_rate),
                        "reference_date": issue_date.isoformat(), "items": rows}
        total = _money(principal + fee + interest)
    due_date = issue_date + timedelta(days=due_days)

    return DunningInput(
        level=level,
        issue_date=issue_date,
        due_date=due_date,
        principal_amount=principal,
        fee_amount=fee,
        total_amount=total,
        overdue_items=tuple(overdue_items),
        interest_amount=interest,
        interest_snapshot=snapshot,
    )
