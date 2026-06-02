"""Day-exact (actual/actual) time arithmetic and period splitting (ADR-0004).

All periods are TAG-INCLUSIVE at both ends: [from, to] includes `from` and
`to`.  Length in days = (to - from).days + 1.  This is the unangefochtene
method for unterjaehrige Mietabrechnung and is the only one whose time factors
sum EXACTLY to 1 over a gap-free, overlap-free cover -- which (with ADR-0005)
makes "no cent lost via time split" a structural property.

The DB EXCLUDE constraints (M1) use half-open `daterange [from, to)`, but
valid_to / service_period_to are fachlich INCLUSIVE days.  The conversion
happens at the repository read-edge; inside the engine everything is
tag-inclusive (section 3.4).  A tenant change "Auszug 30.06. / Einzug 01.07."
therefore produces NO gap and NO overlap -- this is an explicit edge test.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.engine.types import LeasePeriodInput, OwnerPeriodInput, PeriodOverlapError


# Period kinds in the unit-internal time cover.
KIND_TENANT = "TENANT"
KIND_VACANCY = "VACANCY"


@dataclass(frozen=True)
class CoverPeriod:
    """A slice of a unit's timeline within the settlement period.

    Either a tenant occupancy (KIND_TENANT, carries lease_agreement_id +
    tenant_profile_id) or a vacancy (KIND_VACANCY, carries owner_profile_id
    where known). Tag-inclusive [start, end].
    """

    unit_id: uuid.UUID
    kind: str
    start: date
    end: date
    lease_agreement_id: uuid.UUID | None = None
    tenant_profile_id: uuid.UUID | None = None
    owner_profile_id: uuid.UUID | None = None

    @property
    def days(self) -> int:
        return day_count(self.start, self.end)


def day_count(from_date: date, to_date: date) -> int:
    """Number of tag-inclusive days in [from_date, to_date]. 0 if to < from."""
    if to_date < from_date:
        return 0
    return (to_date - from_date).days + 1


def overlap_days(
    a_from: date, a_to: date, b_from: date, b_to: date
) -> int:
    """Tag-inclusive overlap (in days) of [a_from, a_to] and [b_from, b_to]."""
    lo = max(a_from, b_from)
    hi = min(a_to, b_to)
    return day_count(lo, hi)


def time_factor(
    sp_from: date,
    sp_to: date,
    p_from: date,
    p_to: date,
) -> Decimal:
    """Exact time fraction of period [p_from, p_to] within a service period.

    = days(serviceperiod ∩ period) / days(serviceperiod)    (exact Decimal)

    Returns 0 if no overlap.  Never pre-rounded (ADR-0004 #3).
    """
    sp_total = day_count(sp_from, sp_to)
    if sp_total == 0:
        return Decimal(0)
    ov = overlap_days(sp_from, sp_to, p_from, p_to)
    if ov == 0:
        return Decimal(0)
    return Decimal(ov) / Decimal(sp_total)


def _clamp(
    p_from: date,
    p_to: date | None,
    lo: date,
    hi: date,
) -> tuple[date, date] | None:
    """Clamp a (possibly open-ended) period to [lo, hi]. None if no overlap."""
    start = max(p_from, lo)
    end = hi if p_to is None else min(p_to, hi)
    if end < start:
        return None
    return (start, end)


def _owner_at(
    owner_periods: list[OwnerPeriodInput],
    unit_id: uuid.UUID,
    day: date,
) -> uuid.UUID | None:
    """The owner_profile_id owning `unit_id` on `day` (first match), or None.

    Co-ownership: multiple owners may apply; for vacancy attribution in the
    Mieter-BK the engine records the first owner deterministically (sorted by
    owner_profile_id) -- the exact owner *charge* split is M4. None if no
    ownership record covers the day (data gap; carried as None, not invented).
    """
    candidates = [
        op
        for op in owner_periods
        if op.unit_id == unit_id
        and op.valid_from <= day
        and (op.valid_to is None or op.valid_to >= day)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda op: str(op.owner_profile_id))
    return candidates[0].owner_profile_id


def build_unit_cover(
    unit_id: uuid.UUID,
    period_start: date,
    period_end: date,
    leases: list[LeasePeriodInput],
    owner_periods: list[OwnerPeriodInput],
) -> list[CoverPeriod]:
    """Build the gap-free, overlap-free tenant/vacancy cover of one unit.

    Steps:
      1. Clip each lease for this unit to [period_start, period_end].
      2. Sort by start. (M1 EXCLUDE guarantees no overlap; we assert it.)
      3. Fill the gaps before/between/after leases with VACANCY periods,
         attributed to the owner (split further if ownership changes within
         a vacancy gap, so each vacancy slice has a single owner).

    Invariant (checked): the returned slices exactly cover
    [period_start, period_end] with no gap and no overlap, so the sum of their
    time factors against any service period covering the unit is exactly the
    service-period-vs-settlement-period overlap fraction (ADR-0004 #4).
    """
    clipped: list[CoverPeriod] = []
    for lease in leases:
        if lease.unit_id != unit_id:
            continue
        c = _clamp(lease.valid_from, lease.valid_to, period_start, period_end)
        if c is None:
            continue
        clipped.append(
            CoverPeriod(
                unit_id=unit_id,
                kind=KIND_TENANT,
                start=c[0],
                end=c[1],
                lease_agreement_id=lease.lease_agreement_id,
                tenant_profile_id=lease.tenant_profile_id,
            )
        )

    clipped.sort(key=lambda p: p.start)

    # Defensive overlap check (M1 EXCLUDE should prevent this; the engine is
    # deliberately strict and escalates rather than mis-attribute -- section 2.6).
    # Raised as PeriodOverlapError (a ValueError subclass) so the service surfaces
    # it as a clean 4xx, not a 500.
    for prev, nxt in zip(clipped, clipped[1:]):
        if nxt.start <= prev.end:
            raise PeriodOverlapError(
                f"Overlapping lease periods for unit {unit_id}: "
                f"[{prev.start}..{prev.end}] and [{nxt.start}..{nxt.end}]"
            )

    cover: list[CoverPeriod] = []
    cursor = period_start

    def add_vacancy(v_start: date, v_end: date) -> None:
        """Add vacancy slices [v_start, v_end], split per ownership change.

        A vacancy gap is attributed to the owner of the unit on each day
        (section 3.3). If ownership changes within the gap, the gap is split
        so every vacancy slice has a single owner. Owner-change boundaries are
        the ownership-period start days that fall strictly inside the gap.
        """
        if v_end < v_start:
            return
        seg_start = v_start
        cur_owner = _owner_at(owner_periods, unit_id, v_start)
        boundaries = sorted(
            op.valid_from
            for op in owner_periods
            if op.unit_id == unit_id and v_start < op.valid_from <= v_end
        )
        for b in boundaries:
            owner_here = _owner_at(owner_periods, unit_id, b)
            if owner_here != cur_owner:
                # Close [seg_start, b-1] under the previous owner; start new at b.
                cover.append(
                    CoverPeriod(
                        unit_id=unit_id,
                        kind=KIND_VACANCY,
                        start=seg_start,
                        end=_prev_day(b),
                        owner_profile_id=cur_owner,
                    )
                )
                seg_start = b
                cur_owner = owner_here
        cover.append(
            CoverPeriod(
                unit_id=unit_id,
                kind=KIND_VACANCY,
                start=seg_start,
                end=v_end,
                owner_profile_id=cur_owner,
            )
        )

    for tenant_slice in clipped:
        if tenant_slice.start > cursor:
            add_vacancy(cursor, _prev_day(tenant_slice.start))
        cover.append(tenant_slice)
        cursor = _next_day(tenant_slice.end)

    if cursor <= period_end:
        add_vacancy(cursor, period_end)

    _assert_full_cover(cover, unit_id, period_start, period_end)
    return cover


def _prev_day(d: date) -> date:
    return date.fromordinal(d.toordinal() - 1)


def _next_day(d: date) -> date:
    return date.fromordinal(d.toordinal() + 1)


def _assert_full_cover(
    cover: list[CoverPeriod],
    unit_id: uuid.UUID,
    period_start: date,
    period_end: date,
) -> None:
    """Assert the cover is gap-free, overlap-free, exactly [start, end]."""
    ordered = sorted(cover, key=lambda p: p.start)
    if not ordered:
        # Empty settlement period would mean start > end -- caller validates.
        if period_start <= period_end:
            raise ValueError(
                f"Empty cover for unit {unit_id} over "
                f"[{period_start}..{period_end}]"
            )
        return
    if ordered[0].start != period_start:
        raise ValueError(
            f"Cover for unit {unit_id} does not start at {period_start} "
            f"(starts at {ordered[0].start})"
        )
    if ordered[-1].end != period_end:
        raise ValueError(
            f"Cover for unit {unit_id} does not end at {period_end} "
            f"(ends at {ordered[-1].end})"
        )
    for prev, nxt in zip(ordered, ordered[1:]):
        if nxt.start != _next_day(prev.end):
            raise ValueError(
                f"Gap or overlap in cover for unit {unit_id}: "
                f"[..{prev.end}] then [{nxt.start}..]"
            )
