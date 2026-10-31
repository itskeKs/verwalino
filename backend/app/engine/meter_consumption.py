"""Exact consumption from one counter's unambiguous boundary readings.

No IO, interpolation, rollover assumption or fallback past an ambiguous nearest
reading. Selection is within 31 calendar days on either side of each boundary.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, localcontext
from uuid import UUID

from app.engine.periods import KIND_TENANT


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


@dataclass(frozen=True)
class MeterConsumptionSplit:
    measured: MeterConsumption
    periods: tuple[tuple[date, date, Decimal], ...]
    reason: str | None
    bases: tuple[tuple[MeterReadingInput, MeterReadingInput, int], ...]


def boundary_reading(readings: Sequence[MeterReadingInput]) -> MeterReadingInput:
    """Collapse consistent boundary duplicates in stable date/ID order."""
    if not readings:
        raise MeterConsumptionError("Handeingabe erforderlich: exakte Grenz-/Zwischenablesung fehlt.")
    for row in readings:
        _validate_value(row.value)
    if any(row.value != readings[0].value for row in readings):
        raise MeterConsumptionError("Handeingabe erforderlich: Zwischenablesung an der Mietgrenze ist mehrdeutig.")
    return min(readings, key=lambda row: (row.reading_date, row.id))


def _nearest(readings: Sequence[MeterReadingInput], boundary: date) -> MeterReadingInput:
    candidates = [(abs((row.reading_date - boundary).days), row) for row in readings
                  if abs((row.reading_date - boundary).days) <= 31]
    if not candidates:
        raise MeterConsumptionError("Handeingabe erforderlich: Keine Ablesung innerhalb ±31 Tagen.")
    distance = min(item[0] for item in candidates)
    nearest = [row for days, row in candidates if days == distance]
    if len({row.reading_date for row in nearest}) != 1:
        raise MeterConsumptionError("Handeingabe erforderlich: Nächste Ablesung ist mehrdeutig.")
    return boundary_reading(nearest)


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


def split_consumption(readings, start, end, covers):
    """Split a known unit-period quantity at available exact interim readings.

    Between measured boundaries, missing occupant readings fall back to days.
    Only this unit's boundaries participate; another unit needs no interim reading.
    Each segment's last occupant receives the exact quantity remainder.
    """
    boundaries = [(c.end, c.end + timedelta(days=1)) for c in covers[:-1]]
    interim_dates = {day for previous, following in zip(covers, covers[1:])
                     if previous.kind == following.kind == KIND_TENANT
                     and previous.lease_agreement_id != following.lease_agreement_id
                     for day in (previous.end, following.start)}
    # A tenant-change measurement cannot establish the property's opening or
    # closing quantity. Missing period readings require manual input, never 0.
    period_readings = [r for r in readings if r.reading_date not in interim_dates
                       or r.reading_date in {start, end}]
    try:
        measured = derive_consumption(period_readings, start, end)
    except MeterConsumptionError as exc:
        raise MeterConsumptionError(f"Periodenablesung: {exc}") from exc
    cuts = [(start, measured.start_reading)]
    reason = None
    for last_day, first_day in boundaries:
        rows = [r for r in readings if r.reading_date in {last_day, first_day}
                and r.reading_date not in {start, end}]
        if rows:
            cuts.append((first_day, boundary_reading(rows)))
        else:
            reason = "Zwischenablesung fehlt: Aufteilung des Einheitsverbrauchs nach Tagen."
    cuts.append((end + timedelta(days=1), measured.end_reading))
    amounts = [Decimal(0) for _ in covers]
    bases = [None for _ in covers]
    with localcontext() as context:
        context.prec = 50
        for (a, first), (b, last) in zip(cuts, cuts[1:]):
            _validate_value(first.value)
            _validate_value(last.value)
            quantity = last.value - first.value
            if quantity < 0:
                raise MeterConsumptionError("Handeingabe erforderlich: Zwischenablesung ist rückläufig.")
            participants = [(i, max(0, (min(c.end + timedelta(days=1), b) - max(c.start, a)).days))
                            for i, c in enumerate(covers)]
            participants = [(i, days) for i, days in participants if days]
            left = quantity
            for j, (i, days) in enumerate(participants):
                part = left if j == len(participants) - 1 else quantity * days / (b-a).days
                amounts[i] += part
                bases[i] = (first, last, (b-a).days)
                left -= part
    return MeterConsumptionSplit(measured, tuple((c.start, c.end, amount) for c, amount in zip(covers, amounts)), reason, tuple(bases))
