"""Integrate independent occupied/vacancy household streams, without guesses."""
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

from app.engine.rounding import DistributionError, _floor_to_cent
from app.engine.types import UnitInput


class MissingHouseholdError(DistributionError):
    """An actual occupied/vacancy day lacks its own saved household input."""


def person_days(unit: UnitInput, start: date, end: date, *, is_vacancy: bool) -> int:
    if start > end:
        return 0
    rows = sorted((r for r in unit.household if r.is_vacancy == is_vacancy), key=lambda r: r.valid_from)
    if not rows or rows[0].valid_from > start:
        raise MissingHouseholdError(
            f"Handeingabe erforderlich: Personenzahl für Einheit {unit.number} ab {start:%d.%m.%Y} fehlt."
        )
    result = 0
    for index, row in enumerate(rows):
        if type(row.persons) is not int or row.persons < 0:
            raise DistributionError(f"Personenzahl für Einheit {unit.number} muss eine nichtnegative ganze Zahl sein.")
        if index and row.valid_from == rows[index - 1].valid_from:
            raise DistributionError(f"Doppelte Haushaltsangabe für Einheit {unit.number} ab {row.valid_from:%d.%m.%Y}.")
        until = rows[index + 1].valid_from - timedelta(days=1) if index + 1 < len(rows) else end
        a, b = max(start, row.valid_from), min(end, until)
        if a <= b:
            result += row.persons * ((b - a).days + 1)
    return result


def person_day_share(amount: Decimal, unit: UnitInput, source_start: date, source_end: date,
                     start: date, end: date, *, is_vacancy: bool) -> Decimal:
    """Select cumulative weighted cents so adjacent windows reconcile exactly."""
    whole = person_days(unit, source_start, source_end, is_vacancy=is_vacancy)
    if not whole:
        return Decimal('0.00')
    before = person_days(unit, source_start, start - timedelta(days=1), is_vacancy=is_vacancy) if start > source_start else 0
    through = person_days(unit, source_start, end, is_vacancy=is_vacancy)
    return (_floor_to_cent(Fraction(amount) * Fraction(through, whole))
            - _floor_to_cent(Fraction(amount) * Fraction(before, whole)))
