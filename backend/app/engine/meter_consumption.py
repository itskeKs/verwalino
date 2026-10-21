"""Exact consumption from one counter's unambiguous boundary readings.

No IO, interpolation, rollover assumption or fallback past an ambiguous nearest
reading. Selection is within 31 calendar days on either side of each boundary.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from uuid import UUID


class MeterConsumptionError(ValueError):
    """The available measurements do not establish a consumption quantity."""


@dataclass(frozen=True)
class MeterReadingInput:
    id: UUID
    reading_date: date
    value: Decimal


@dataclass(frozen=True)
class MeterConsumption:
    consumption: Decimal
    start_reading: MeterReadingInput
    end_reading: MeterReadingInput


def _nearest(readings: Sequence[MeterReadingInput], boundary: date) -> MeterReadingInput:
    candidates = [(abs((row.reading_date - boundary).days), row) for row in readings
                  if abs((row.reading_date - boundary).days) <= 31]
    if not candidates:
        raise MeterConsumptionError("Handeingabe erforderlich: Keine Ablesung innerhalb ±31 Tagen.")
    distance = min(item[0] for item in candidates)
    nearest = [row for days, row in candidates if days == distance]
    if len(nearest) != 1:
        raise MeterConsumptionError("Handeingabe erforderlich: Nächste Ablesung ist mehrdeutig.")
    return nearest[0]


def _validate_value(value: Decimal) -> None:
    if (not isinstance(value, Decimal) or not value.is_finite()
            or value < 0 or value >= Decimal("10000000000")
            or value.as_tuple().exponent < -4):
        raise MeterConsumptionError("Handeingabe erforderlich: Ungültiger oder ungenauer Zählerstand.")


def derive_consumption(
    readings: Sequence[MeterReadingInput], period_start: date, period_end: date,
) -> MeterConsumption:
    if period_start > period_end:
        raise MeterConsumptionError("Handeingabe erforderlich: Ungültiger Abrechnungszeitraum.")
    first, last = _nearest(readings, period_start), _nearest(readings, period_end)
    if first.id == last.id or first.reading_date >= last.reading_date:
        raise MeterConsumptionError("Handeingabe erforderlich: Keine zeitlich eindeutige Ablesepaarung.")
    _validate_value(first.value)
    _validate_value(last.value)
    if last.value < first.value:
        raise MeterConsumptionError("Handeingabe erforderlich: Zählerstand ist rückläufig; Austausch oder Überlauf prüfen.")
    with localcontext() as context:
        context.prec = 32
        quantity = last.value - first.value
    return MeterConsumption(quantity, first, last)
