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
from calendar import monthrange
from decimal import Decimal

from app.engine.types import LeasePeriodInput, OwnerPeriodInput, PeriodOverlapError


def period_starting_in(start_month: int, year: int) -> tuple[date, date]:
    """Twelve calendar months, starting on day one; both bounds are inclusive."""
    start = date(year, start_month, 1)
    end_year, end_month = (year, 12) if start_month == 1 else (year + 1, start_month - 1)
    return start, date(end_year, end_month, monthrange(end_year, end_month)[1])


def period_for_date(start_month: int, day: date) -> tuple[date, date]:
    """The property's period containing day, without consulting a clock."""
    return period_starting_in(start_month, day.year if day.month >= start_month else day.year - 1)


def period_label(start: date, end_inclusive: date) -> str:
    """Calendar-year label, or both years for a period crossing January."""
    return str(start.year) if start.year == end_inclusive.year else f"{start.year}/{end_inclusive.year}"


def validate_settlement_period(start: date, end_inclusive: date) -> None:
    """Accept inclusive periods up to twelve calendar months, including short ones.

    The last day is strictly before the next anniversary. For 29 February the
    anniversary is clamped to 28 February, so the last admissible day is the 27th.
    """
    if end_inclusive < start:
        raise ValueError("Der Abrechnungszeitraum darf nicht vor seinem Beginn enden.")
    if start.year == 9999:
        return
    anniversary = date(start.year + 1, start.month,
        min(start.day, monthrange(start.year + 1, start.month)[1]))
    if end_inclusive >= anniversary:
        raise ValueError("Der Abrechnungszeitraum darf höchstens zwölf Monate umfassen.")


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


# ===========================================================================
# M4 (WEG): owner cover -- the mirrored cover of build_unit_cover (m4 §4.4).
#
# Differences vs the M3 tenant/vacancy cover:
#   - The slices are OWNER periods (no tenant, no vacancy concept): in a WEG a
#     unit ALWAYS has an owner. A genuine ownership GAP is a data error and is
#     escalated fail-loud (an Eigentums-"Leerstand" would mean a unit owned by
#     nobody -- never correct, m4 section 4.4), unlike a tenant vacancy which is
#     a normal state attributed to the owner.
#   - CO-OWNERSHIP: multiple owners may own a unit at the SAME time (different
#     owner_profiles with mea_share). The cover therefore can carry SEVERAL
#     overlapping slices per day, one per co-owner; the time arithmetic is per
#     (owner, slice) and the co-owners' mea_share weights split the unit's share
#     among them (the second, exact stage over M3 -- m4 section 4.3).
# ===========================================================================


@dataclass(frozen=True)
class OwnerCoverPeriod:
    """A slice of one unit's ownership timeline within the settlement period.

    Tag-inclusive [start, end]. Carries the owner and the owner's mea_share of
    the unit (for co-ownership). For a sole owner mea_share is None/the full
    unit and the second-stage split is a no-op.
    """

    unit_id: uuid.UUID
    owner_profile_id: uuid.UUID
    start: date
    end: date
    mea_share: Decimal | None = None

    @property
    def days(self) -> int:
        return day_count(self.start, self.end)


def build_owner_cover(
    unit_id: uuid.UUID,
    period_start: date,
    period_end: date,
    owner_periods: list[OwnerPeriodInput],
) -> list[OwnerCoverPeriod]:
    """Build the gap-free owner cover of one unit (m4 section 4.4).

    Unlike build_unit_cover this does NOT fill gaps with vacancy: every day of
    [period_start, period_end] MUST be covered by at least one ownership record,
    else the data is wrong and we escalate (a WEG unit is never owner-less).
    Co-ownership: several owners may cover the same day; each becomes its own
    slice carrying its mea_share.

    Invariant (checked): the UNION of the returned slices (ignoring co-owner
    multiplicity) exactly covers [period_start, period_end] with no gap. With a
    single owner per instant the slices are also non-overlapping; with co-owners
    overlaps are EXPECTED (one slice per co-owner over the same span).
    """
    clipped: list[OwnerCoverPeriod] = []
    for op in owner_periods:
        if op.unit_id != unit_id:
            continue
        c = _clamp(op.valid_from, op.valid_to, period_start, period_end)
        if c is None:
            continue
        clipped.append(
            OwnerCoverPeriod(
                unit_id=unit_id,
                owner_profile_id=op.owner_profile_id,
                start=c[0],
                end=c[1],
                mea_share=op.mea_share,
            )
        )

    if not clipped:
        raise PeriodOverlapError(
            f"No ownership record covers unit {unit_id} in "
            f"[{period_start}..{period_end}] -- a WEG unit must always have an "
            f"owner; escalate rather than invent one (m4 section 4.4)."
        )

    _assert_owner_cover_no_gap(clipped, unit_id, period_start, period_end)
    # Deterministic order: by start, then owner id (stable tie-break).
    clipped.sort(key=lambda s: (s.start, str(s.owner_profile_id)))
    return clipped


def _assert_owner_cover_no_gap(
    cover: list[OwnerCoverPeriod],
    unit_id: uuid.UUID,
    period_start: date,
    period_end: date,
) -> None:
    """Assert the owner cover leaves NO day uncovered (co-owner overlaps OK).

    We project the slices onto the timeline and verify the union covers
    [period_start, period_end] contiguously. Overlap is allowed (co-ownership);
    a gap is a fail-loud data error.
    """
    if period_start > period_end:
        return
    intervals = sorted(((s.start, s.end) for s in cover), key=lambda x: x[0])
    cursor = period_start
    for lo, hi in intervals:
        if lo > cursor:
            # There is a day with no owner between cursor and lo.
            raise PeriodOverlapError(
                f"Ownership gap for unit {unit_id}: no owner on "
                f"[{cursor}..{_prev_day(lo)}] within [{period_start}..{period_end}] "
                f"-- escalate (m4 section 4.4)."
            )
        if hi >= cursor:
            cursor = _next_day(hi)
        if cursor > period_end:
            break
    if cursor <= period_end:
        raise PeriodOverlapError(
            f"Ownership gap for unit {unit_id}: no owner on "
            f"[{cursor}..{period_end}] within [{period_start}..{period_end}] "
            f"-- escalate (m4 section 4.4)."
        )


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
