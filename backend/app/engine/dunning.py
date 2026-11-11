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
  3. Fee calculation: per-level organization fees
     [section 18/fachlich -- must be appropriate/contractually agreed].
  4. Dunning notice assembly: combine overdue items into a DunningInput with
     principal, fee, total, and the included items snapshot.

Money is ALWAYS Decimal, never float.

[section 18/fachlich] Provisionally marked items:
  - Grace period (Karenzfrist): default 0 days (every overdue item qualifies).
    The correct grace period depends on lease/contract terms and is to be
    confirmed by a legal expert before productive use.
  - Due date for the dunning notice: issue_date + 14 days (provisional).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, localcontext

from app.engine.rounding import round_money

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
    2: Decimal("2.50"),   # 1. Mahnung
    3: Decimal("2.50"),   # letzte Mahnung
}

# Default payment deadline offset from issue date.
DEFAULT_DUE_DAYS: int = 14

# Maximum dunning level in the escalation chain.
MAX_LEVEL: int = 3

# Marker in the frozen items lets the renderer distinguish old interest-free
# notices from new ones while both retain a null interest snapshot.
NOTICE_FORMAT_KEY = "_dunning_format"
SEGMENTED_NOTICE_FORMAT = 2

STANDARD_PREMIUM = 5
COMMERCIAL_PREMIUM = 9


class MissingBaseInterestRate(ValueError):
    """A half-year of positive overdue principal has no published base rate."""

    def __init__(self, valid_from: date):
        self.valid_from = valid_from
        super().__init__(f"No published base rate from {valid_from}")


def _money(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("Money must be a finite Decimal")
    if value.copy_abs() >= Decimal("1000000000000"):
        raise ValueError("Dunning amount exceeds Numeric(14,2)")
    with localcontext() as ctx:
        ctx.prec = max(50, len(value.as_tuple().digits) + 5)
        result = round_money(value)
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
    payment_changes: tuple[tuple[date, Decimal], ...] = ()
    combined_advance: bool = False


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
        rows = [
            {
                "period_year": it.period_year,
                "period_month": it.period_month,
                "payment_type": it.payment_type,
                **({"combined_advance": True} if it.combined_advance else {}),
                "soll": str(it.soll),
                "ist": str(it.ist),
                "open_amount": str(it.open_amount),
                **({"due_date": it.due_date.isoformat()} if it.due_date is not None else {}),
            }
            for it in self.overdue_items
        ]
        if rows:
            rows[0][NOTICE_FORMAT_KEY] = SEGMENTED_NOTICE_FORMAT
        return rows


# ---------------------------------------------------------------------------
# Overdue detection
# ---------------------------------------------------------------------------

def _item_due_date(item: OpenItem, current_year: int) -> date:
    """Compute the due date for an open item.

    Tenant rent/operating advances and frozen one-off charges carry an explicit
    due date. Other recurring items retain the provisional first-of-month
    fallback (Jan 1 if period_month is None); this is not the tenant rent rule.
    """
    if item.due_date is not None:
        return item.due_date
    year = item.period_year if item.period_year else current_year
    month = item.period_month if item.period_month else 1
    return date(year, month, 1)


def find_overdue_items(
    open_items: list[OpenItem],
    reference_date: date,
    grace_days: int = DEFAULT_GRACE_DAYS,
    *,
    current_year: int,
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
        item_due = _item_due_date(item, current_year)
        deadline = item_due + timedelta(days=grace_days)
        if reference_date > deadline:
            overdue.append(OverdueItem(
                period_year=item.period_year if item.period_year is not None else 0,
                period_month=item.period_month,
                payment_type=item.payment_type,
                soll=_money(item.soll),
                ist=_money(item.ist),
                open_amount=_money(item.open_amount),
                due_date=item.due_date if item.period_year else item_due,
                payment_changes=item.payment_changes,
                combined_advance=item.combined_advance,
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

    [section 18/fachlich] Uses organization defaults unless custom fees are supplied.
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
    rate_periods: tuple[tuple[date, Decimal], ...] | None = None,
    premium: int = STANDARD_PREMIUM,
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
    with localcontext() as ctx:
        ctx.prec = 50
        principal = _money(sum((_money(it.open_amount) for it in overdue_items), Decimal("0")))
        interest = Decimal("0.00")
        snapshot = None
        if rate_periods is not None:
            rates = dict(rate_periods)
            rows = [_interest_item(item, issue_date, rates, premium) for item in overdue_items]
            interest = _money(sum((Decimal(row["interest"]) for row in rows), Decimal("0")))
            snapshot = {"premium": premium, "reference_date": issue_date.isoformat(), "items": rows}
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


def _half_year(day: date) -> date:
    return date(day.year, 1 if day.month < 7 else 7, 1)


def _interest_item(
    item: OverdueItem, issue_date: date, rates: dict[date, Decimal], premium: int,
) -> dict:
    due = item.due_date or date(item.period_year or issue_date.year, item.period_month or 1, 1)
    first_day = due.toordinal() + 1
    through = issue_date.toordinal() + 1
    after_due = [(day, amount) for day, amount in item.payment_changes if day > due]
    changes = {day.toordinal(): amount for day, amount in after_due if day <= issue_date}
    balance = item.open_amount + sum((amount for _, amount in after_due), Decimal("0"))
    balance -= changes.get(first_day, Decimal("0"))
    boundaries = set(changes) - {first_day}
    half_year = _half_year(due)
    while half_year.toordinal() < through:
        if half_year.toordinal() > first_day:
            boundaries.add(half_year.toordinal())
        if half_year == date(9999, 7, 1):
            break
        half_year = date(half_year.year, 7, 1) if half_year.month == 1 else date(half_year.year + 1, 1, 1)

    spans: list[tuple[int, int, Decimal, Decimal]] = []
    start = first_day
    for end in sorted(boundaries | {through}):
        if end > start and balance > 0:
            valid_from = _half_year(date.fromordinal(start))
            base = rates.get(valid_from)
            if base is None:
                raise MissingBaseInterestRate(valid_from)
            if spans and start not in changes and spans[-1][1:] == (start, balance, base):
                spans[-1] = (spans[-1][0], end, balance, base)
            else:
                spans.append((start, end, balance, base))
        balance -= changes.get(end, Decimal("0"))
        start = end

    segments = []
    accrued = Decimal("0")
    # Round the item once. Differences between rounded running totals let
    # the displayed segment amounts add up exactly to the frozen item amount.
    for start, end, principal, base in spans:
        annual = base + premium
        days = end - start
        previous = round_money(accrued)
        accrued += principal * annual * days / 36500
        segments.append({
            "start": date.fromordinal(start).isoformat(),
            "end": date.fromordinal(end - 1).isoformat(),
            "days": days,
            "principal": str(_money(principal)),
            "base_interest_rate": str(base),
            "annual_rate": str(annual),
            "interest": str(round_money(accrued) - previous),
        })
    return {
        "payment_type": item.payment_type,
        **({"combined_advance": True} if item.combined_advance else {}),
        "period_year": item.period_year, "period_month": item.period_month,
        "due_date": due.isoformat(),
        "interest_start": date.fromordinal(first_day).isoformat() if first_day < through else None,
        "days": sum(row["days"] for row in segments),
        "principal": str(_money(item.open_amount)), "interest": str(_money(accrued)),
        "segments": segments,
    }
