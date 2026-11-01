"""Pure calculation aid for §557b BGB; no notice or delivery is performed."""
from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, localcontext


@dataclass(frozen=True)
class IndexRentResult:
    valid_from: date
    previous_valid_from: date
    previous_base_rent: Decimal
    base_rent: Decimal
    advance_payment: Decimal
    heating_advance_payment: Decimal = Decimal("0.00")


def _decimal(value: Decimal, name: str, places: int, *, positive: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if value < 0 or (positive and value == 0) or value >= Decimal("1000000000000"):
        raise ValueError(f"{name} is outside the supported range")
    if value != value.quantize(Decimal(1).scaleb(-places)):
        raise ValueError(f"{name} has too many decimal places")


def index_effective_date(declaration_received_on: date) -> date:
    if not isinstance(declaration_received_on, date):
        raise ValueError("Calendar date required")
    absolute_month = declaration_received_on.year * 12 + declaration_received_on.month - 1 + 2
    year, month = divmod(absolute_month, 12)
    try:
        return date(year, month + 1, 1)
    except ValueError as exc:
        raise ValueError("Index rent exceeds supported calendar range") from exc


def calculate_index_rent(*, base_rent: Decimal, advance_payment: Decimal,
                         old_index: Decimal, new_index: Decimal,
                         previous_valid_from: date, declaration_received_on: date,
                         heating_advance_payment: Decimal = Decimal("0.00")) -> IndexRentResult:
    _decimal(base_rent, "base rent", 2)
    _decimal(advance_payment, "advance payment", 2)
    _decimal(heating_advance_payment, "heating advance payment", 2)
    _decimal(old_index, "old index", 6, positive=True)
    _decimal(new_index, "new index", 6, positive=True)
    if old_index == new_index:
        raise ValueError("Index has not changed")
    if not isinstance(previous_valid_from, date) or not isinstance(declaration_received_on, date):
        raise ValueError("Calendar dates required")
    try:
        effective = index_effective_date(declaration_received_on)
        anniversary = previous_valid_from.replace(
            year=previous_valid_from.year + 1,
            day=min(previous_valid_from.day, monthrange(previous_valid_from.year + 1, previous_valid_from.month)[1]),
        )
    except ValueError as exc:
        raise ValueError("Index rent exceeds supported calendar range") from exc
    if effective < anniversary:
        raise ValueError("Rent must remain unchanged for at least one year")
    with localcontext() as context:
        context.prec = 50
        new_rent = (base_rent * new_index / old_index).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    _decimal(new_rent, "new rent", 2)
    return IndexRentResult(effective, previous_valid_from, base_rent, new_rent, advance_payment, heating_advance_payment)
