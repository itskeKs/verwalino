"""Orchestrates one objektweite WEG-Jahresabrechnung -> WegResult (pure).

m4-weg-abrechnung.md section 2/3/4, ADR-0007.

This is the MIRRORED addressee focus of M3 (m4 section 4.4), NOT a new
distribution algorithm:
  - M3 (Mieter-BK): distribute each position over all (unit, tenant/vacancy)
    cells, pick the TARGET LEASE's cells -> the tenant's COST line.
  - M4 (WEG):       distribute each position over all (unit, OWNER) cells by
    `owner_distribution_key`, SUM per owner_profile -> each owner's cost lines.

Flow (m4 section 10.1):
  1. validate inputs fail-loud (ownership gap, missing MEA, missing plan-soll,
     inconsistent reserve) -- escalate, never emit a plausible-but-wrong bill.
  2. build each unit's gap-free OWNER cover (build_owner_cover).
  3. for every cost position:
       - if reserve_relevant: it is a RESERVE CONTRIBUTION (Vermoegensbildung),
         distributed to owners by key but summed into the reserve subtotal, NOT
         into OWNER_COST_TOTAL (m4 section 3).
       - else: a normal cost; distribute over all (unit, owner-slice) cells via
         the SINGLE central Largest-Remainder pass (distribute_amount, ADR-0005),
         with the co-ownership mea_share split folded into the cell weights so a
         co-owned unit's share is split exactly among co-owners.
  4. per owner: sum the rounded position shares into three subtotals
     (umlagefaehig / nicht umlagefaehig / reserve), OWNER_COST_TOTAL = the first
     two; ABRECHNUNGSSPITZE = OWNER_COST_TOTAL - PLAN_ADVANCE (WEMoG, ADR-0007).
  5. build the reserve development (opening + Σ contributions - withdrawals ==
     closing, checked fail-loud).

The Decimal arithmetic for the per-cell weight and the single rounding pass is
the SAME as M3's distribution.py -- only the cell universe (owner slices) and
the addressee aggregation (per owner) differ.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.engine.periods import (
    OwnerCoverPeriod,
    build_owner_cover,
    day_count,
    overlap_days,
    time_factor,
)
from app.engine.rounding import CENT, DistributionError, distribute_amount
from app.engine.types import (
    ALL_KEYS,
    COST_GROUP_NICHT_UMLAGEFAEHIG,
    COST_GROUP_UMLAGEFAEHIG,
    DIRECT_KEYS,
    OwnerAdvanceInput,
    OwnerCostStep,
    OwnerResult,
    PeriodOverlapError,
    ReserveDevelopment,
    ResultLine,
    UnitInput,
    WegCostPositionInput,
    WegResult,
    WegSettlementInput,
    WEIGHTED_KEYS,
)


class WegValidationError(ValueError):
    """A fail-loud validation error before/while computing a WEG run (m4 §10.1).

    Carries position/unit/owner context so the service surfaces exactly what is
    wrong (clean 4xx) instead of persisting a partial/wrong WEG snapshot.
    """


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT)


# ---------------------------------------------------------------------------
# Per-unit weight for a weighted owner distribution key (mirrors M3
# distribution._unit_weight; kept here so the WEG engine has the owner-side
# semantics in one place and never silently 0-distributes).
# ---------------------------------------------------------------------------
def _unit_weight(unit: UnitInput, key: str) -> Decimal:
    if key == "MEA":
        val = unit.mea
    elif key == "WOHNFLAECHE":
        val = unit.area
    elif key == "BEHEIZTE_FLAECHE":
        val = unit.heated_area
    elif key == "EINHEIT":
        return Decimal(1)
    elif key == "PERSONENZAHL":
        val = None if unit.person_count is None else Decimal(unit.person_count)
    elif key == "VERBRAUCH":
        val = unit.consumption
    else:
        raise DistributionError(f"Unknown weighted key: {key}")
    if val is None:
        raise DistributionError(
            f"Unit {unit.number} ({unit.id}) is missing the base quantity for "
            f"key {key}; cannot distribute owner share (escalate -- no silent 0)."
        )
    if val < 0:
        raise DistributionError(
            f"Unit {unit.number} ({unit.id}) has negative base for key {key}: {val}"
        )
    return val


def _participates(unit: UnitInput, key: str) -> bool:
    """Whether a unit participates in a weighted key (mirror of M3)."""
    if key == "EINHEIT":
        return True
    if key == "MEA":
        return unit.mea is not None and unit.mea > 0
    if key == "WOHNFLAECHE":
        return unit.area is not None and unit.area > 0
    if key == "BEHEIZTE_FLAECHE":
        return unit.heated_area is not None and unit.heated_area > 0
    if key == "PERSONENZAHL":
        return unit.person_count is not None and unit.person_count > 0
    if key == "VERBRAUCH":
        return unit.consumption is not None and unit.consumption > 0
    return False


def _effective_total(pos: WegCostPositionInput) -> Decimal:
    """The owner-relevant total to distribute (m4 section 4.5, [F4-3]).

    The owner sphere distributes the GROSS amount (the owner bears the full cost
    by MEA). For direct keys the per-unit amounts sum to the total.
    """
    key = pos.owner_distribution_key
    if key == "EXTERNE_ABRECHNUNG":
        return sum((e.tenant_amount for e in pos.external_amounts), Decimal(0))
    if key == "MANUELLER_BETRAG":
        return sum((m.amount for m in pos.manual_amounts), Decimal(0))
    return pos.gross_amount


def _co_owner_shares(
    unit_id: uuid.UUID,
    owner_slice: OwnerCoverPeriod,
    all_slices_for_unit: list[OwnerCoverPeriod],
) -> Decimal:
    """The mea_share weight of a co-owner slice within its unit (m4 §4.3 stage 2).

    For a SOLE owner (only one owner covering the slice's days) this returns 1
    (the whole unit share goes to that owner). For CO-OWNERSHIP the unit's share
    on overlapping days is split by mea_share; we return this slice's mea_share,
    defensively normalised against the co-owners covering the SAME span. A zero
    or absent total co-owner mea_share with genuine co-ownership is escalated
    ([F4-7], m4 section 4.3).

    We treat the slice's own [start,end] as the reference span and find the
    sibling slices (other owners) that overlap it. If there is exactly one owner
    over the span, weight = 1. If several, weight = this slice's mea_share. The
    caller normalises by the sum of co-owner weights for that span via the
    single distribute_amount pass, so absolute mea_share scale is irrelevant as
    long as the RATIO among co-owners is correct.
    """
    siblings = [
        s
        for s in all_slices_for_unit
        if s.owner_profile_id != owner_slice.owner_profile_id
        and overlap_days(s.start, s.end, owner_slice.start, owner_slice.end) > 0
    ]
    if not siblings:
        # Sole owner over this slice -> the whole unit share.
        return Decimal(1)
    # Co-ownership: this slice's mea_share is its weight. Escalate if absent.
    if owner_slice.mea_share is None:
        raise WegValidationError(
            f"Co-ownership of unit {unit_id} but owner "
            f"{owner_slice.owner_profile_id} has no mea_share on "
            f"[{owner_slice.start}..{owner_slice.end}] -- cannot split the unit "
            f"share among co-owners (escalate, [F4-7] m4 section 4.3)."
        )
    if owner_slice.mea_share <= 0:
        raise WegValidationError(
            f"Co-ownership of unit {unit_id}: owner {owner_slice.owner_profile_id} "
            f"has non-positive mea_share {owner_slice.mea_share} (escalate)."
        )
    return owner_slice.mea_share


def _validate(inp: WegSettlementInput) -> None:
    """Pre-computation validation (m4 section 10.1). Escalate, never guess."""
    if inp.period_end < inp.period_start:
        raise WegValidationError(
            f"period_end ({inp.period_end}) < period_start ({inp.period_start})"
        )
    if not inp.units:
        raise WegValidationError("WEG settlement has no units")

    for pos in inp.cost_positions:
        if (
            day_count(
                max(pos.service_period_from, inp.period_start),
                min(pos.service_period_to, inp.period_end),
            )
            == 0
        ):
            raise WegValidationError(
                f"Cost position {pos.category_code} ({pos.cost_entry_id}) service "
                f"period [{pos.service_period_from}..{pos.service_period_to}] does "
                f"not overlap settlement period "
                f"[{inp.period_start}..{inp.period_end}]"
            )
        key = pos.owner_distribution_key
        if key is None or key not in ALL_KEYS:
            raise WegValidationError(
                f"Cost position {pos.category_code} ({pos.cost_entry_id}) has "
                f"invalid/absent owner_distribution_key: {key}"
            )
        if pos.cost_group not in (
            COST_GROUP_UMLAGEFAEHIG,
            COST_GROUP_NICHT_UMLAGEFAEHIG,
            "GEMISCHT_PRUEFEN",
        ):
            raise WegValidationError(
                f"Cost position {pos.category_code} ({pos.cost_entry_id}) has "
                f"unknown cost_group: {pos.cost_group}"
            )

    # Plan advances: at most one entry per owner; non-negative is not required
    # (a plan could in theory be 0). Duplicate owner entries are a data error.
    seen: set[uuid.UUID] = set()
    for adv in inp.plan_advances:
        if adv.owner_profile_id in seen:
            raise WegValidationError(
                f"Duplicate plan advance for owner {adv.owner_profile_id}"
            )
        seen.add(adv.owner_profile_id)


def _distribute_one_position(
    pos: WegCostPositionInput,
    units: list[UnitInput],
    owner_covers: dict[uuid.UUID, list[OwnerCoverPeriod]],
) -> list[tuple[uuid.UUID, OwnerCoverPeriod, Decimal, Decimal | None, Decimal | None, int, int]]:
    """Build the rounded (owner-slice) cells for ONE position.

    Returns tuples
      (unit_id, owner_slice, amount, key_base_share, key_base_total,
       billed_days, sp_total_days)
    one per (unit, owner-slice) cell with a non-zero time factor. The single
    Largest-Remainder pass (distribute_amount, ADR-0005) runs over ALL cells of
    the position so Σ rounded == effective total exactly.

    Cell exact weight (mirrors M3 distribution.py, plus the co-owner stage):
       weight = (unit_key_share) * time_factor(slice vs service period)
                                 * (co_owner_weight)
    where co_owner_weight = 1 for a sole owner, else the slice's mea_share (the
    distribute_amount normaliser turns the co-owner weights into exact fractions
    of the unit's share -- m4 section 4.3 stage 2).
    """
    key = pos.owner_distribution_key
    total = _effective_total(pos)
    sp_from = pos.service_period_from
    sp_to = pos.service_period_to
    sp_total = day_count(sp_from, sp_to)

    # spec: (unit_id, slice, exact_weight, base_share, base_total, billed_days)
    cell_specs: list[
        tuple[uuid.UUID, OwnerCoverPeriod, Decimal, Decimal | None, Decimal | None, int]
    ] = []

    if key in DIRECT_KEYS:
        # Direct per-unit amounts (EXTERNE_ABRECHNUNG / MANUELLER_BETRAG): the
        # owner side still attributes the per-unit amount to that unit's owner(s),
        # split by time and co-ownership.
        if key == "EXTERNE_ABRECHNUNG":
            items = [(e.unit_id, e.tenant_amount) for e in pos.external_amounts]
        else:
            items = [(m.unit_id, m.amount) for m in pos.manual_amounts]
        if not items:
            raise DistributionError(
                f"Position {pos.category_code} ({pos.cost_entry_id}) uses key "
                f"{key} but carries no per-unit amounts -- escalate."
            )
        per_unit: dict[uuid.UUID, Decimal] = {}
        for uid, amt in items:
            per_unit[uid] = per_unit.get(uid, Decimal(0)) + amt
        s = sum(per_unit.values(), Decimal(0))
        if s != total:
            raise DistributionError(
                f"Position {pos.category_code} ({pos.cost_entry_id}): sum of "
                f"per-unit {key} amounts ({s}) != effective total ({total})."
            )
        for unit in units:
            amt = per_unit.get(unit.id)
            if amt is None:
                continue
            slices = owner_covers.get(unit.id, [])
            for sl in slices:
                f = time_factor(sp_from, sp_to, sl.start, sl.end)
                if f == 0:
                    continue
                co_w = _co_owner_shares(unit.id, sl, slices)
                weight = amt * f * co_w
                billed = overlap_days(sp_from, sp_to, sl.start, sl.end)
                cell_specs.append((unit.id, sl, weight, amt, None, billed))
    elif key in WEIGHTED_KEYS:
        participating = [u for u in units if _participates(u, key)]
        weights = {u.id: _unit_weight(u, key) for u in participating}
        weight_sum = sum(weights.values(), Decimal(0))
        if weight_sum == 0:
            raise DistributionError(
                f"Position {pos.category_code} ({pos.cost_entry_id}): sum of "
                f"weights for key {key} is zero (no participating unit with a "
                f"positive base) -- escalate."
            )
        for unit in participating:
            w = weights[unit.id]
            key_share = w / weight_sum
            slices = owner_covers.get(unit.id, [])
            for sl in slices:
                f = time_factor(sp_from, sp_to, sl.start, sl.end)
                if f == 0:
                    continue
                co_w = _co_owner_shares(unit.id, sl, slices)
                weight = key_share * f * co_w
                billed = overlap_days(sp_from, sp_to, sl.start, sl.end)
                cell_specs.append((unit.id, sl, weight, w, weight_sum, billed))
    else:
        raise DistributionError(f"Unsupported distribution key: {key}")

    if not cell_specs:
        return []

    cell_weights = [spec[2] for spec in cell_specs]
    rounded = distribute_amount(total, cell_weights)

    out = []
    for (unit_id, sl, _w, base_share, base_total, billed), amount in zip(
        cell_specs, rounded
    ):
        out.append((unit_id, sl, amount, base_share, base_total, billed, sp_total))
    return out


def compute_weg_jahresabrechnung(inp: WegSettlementInput) -> WegResult:
    """Compute the objektweite WEG-Jahresabrechnung (m4 section 2, ADR-0007)."""
    _validate(inp)

    units = list(inp.units)
    owner_periods = list(inp.owner_periods)

    # 2. Build each unit's gap-free owner cover (escalates on an ownership gap).
    #    build_owner_cover raises PeriodOverlapError (a ValueError subclass) on a
    #    genuine ownership gap; surface it as a WegValidationError so the service
    #    maps it to a clean 4xx (m4 section 4.4), not an opaque 500.
    owner_covers: dict[uuid.UUID, list[OwnerCoverPeriod]] = {}
    try:
        for unit in units:
            owner_covers[unit.id] = build_owner_cover(
                unit.id, inp.period_start, inp.period_end, owner_periods
            )
    except (PeriodOverlapError, DistributionError) as exc:
        raise WegValidationError(str(exc)) from exc

    # Deterministic position order (by category_code then id) -> reproducible
    # sort_order + snapshot.
    positions = sorted(
        inp.cost_positions, key=lambda p: (p.category_code, str(p.cost_entry_id))
    )

    # Accumulators per owner.
    owner_cost_lines: dict[uuid.UUID, list[ResultLine]] = {}
    owner_umlagefaehig: dict[uuid.UUID, Decimal] = {}
    owner_nicht: dict[uuid.UUID, Decimal] = {}
    owner_reserve: dict[uuid.UUID, Decimal] = {}
    computation: list[OwnerCostStep] = []

    cost_total = Decimal("0.00")
    reserve_contrib_total = Decimal("0.00")

    sort_order = 0
    for pos in positions:
        cells = _distribute_one_position(pos, units, owner_covers)
        if not cells:
            continue

        # Aggregate the cells per owner for THIS position (a co-owner or an
        # unterjaehriger Eigentuemerwechsel yields several cells per owner; sum
        # them -- already cent-exact per cell, add without re-rounding).
        per_owner: dict[
            uuid.UUID,
            tuple[Decimal, Decimal | None, Decimal | None, int, int],
        ] = {}
        # value: (amount, base_share, base_total, billed_days, sp_total)
        for unit_id, sl, amount, base_share, base_total, billed, sp_total in cells:
            owner_id = sl.owner_profile_id
            prev = per_owner.get(owner_id)
            if prev is None:
                per_owner[owner_id] = (amount, base_share, base_total, billed, sp_total)
            else:
                p_amt, p_bs, p_bt, p_billed, p_sp = prev
                per_owner[owner_id] = (
                    p_amt + amount,
                    p_bs,
                    p_bt,
                    max(p_billed, billed),
                    p_sp,
                )

        eff_total = _money(_effective_total(pos))
        is_reserve = pos.reserve_relevant
        if is_reserve:
            reserve_contrib_total += eff_total
        else:
            cost_total += eff_total

        for owner_id, (amount, base_share, base_total, billed, sp_total) in sorted(
            per_owner.items(), key=lambda kv: str(kv[0])
        ):
            amount = _money(amount)
            line_type = "RESERVE_CONTRIBUTION" if is_reserve else "COST"
            line = ResultLine(
                line_type=line_type,
                label=pos.label,
                amount=amount,
                sort_order=sort_order,
                cost_entry_id=pos.cost_entry_id,
                category_id=pos.category_id,
                category_code=pos.category_code,
                distribution_key=pos.owner_distribution_key,
                total_amount=eff_total,
                distribution_base_total=base_total,
                distribution_base_share=base_share,
                time_share_days=billed,
                time_share_total_days=sp_total,
                note=_position_note(pos),
            )
            owner_cost_lines.setdefault(owner_id, []).append(line)

            if is_reserve:
                owner_reserve[owner_id] = owner_reserve.get(
                    owner_id, Decimal("0.00")
                ) + amount
            elif pos.cost_group == COST_GROUP_NICHT_UMLAGEFAEHIG:
                owner_nicht[owner_id] = owner_nicht.get(
                    owner_id, Decimal("0.00")
                ) + amount
            else:
                # UMLAGEFAEHIG + GEMISCHT_PRUEFEN -> umlagefaehig subtotal (the
                # owner bears the gross; the umlagefaehig/nicht split for the
                # TENANT is M3, m4 section 4.5).
                owner_umlagefaehig[owner_id] = owner_umlagefaehig.get(
                    owner_id, Decimal("0.00")
                ) + amount

            computation.append(
                OwnerCostStep(
                    cost_entry_id=pos.cost_entry_id,
                    owner_profile_id=owner_id,
                    category_code=pos.category_code,
                    distribution_key=pos.owner_distribution_key or "",
                    cost_group=pos.cost_group,
                    is_reserve=is_reserve,
                    total_amount=eff_total,
                    key_base_total=base_total,
                    key_base_share=base_share,
                    time_share_days=billed,
                    time_share_total_days=sp_total,
                    exact_share=amount,
                    rounded_amount=amount,
                    note=_position_note(pos),
                )
            )
        sort_order += 1

    # Plan-Soll per owner (the Abrechnungsspitze subtrahend, ADR-0007).
    plan_by_owner = {a.owner_profile_id: _money(a.plan_advance_total) for a in inp.plan_advances}
    paid_by_owner = {a.owner_profile_id: _money(a.plan_advance_total) for a in inp.paid_advances}

    # The complete set of owners = those with cost cells ∪ those with a plan
    # advance (an owner with a plan but zero cost still gets a Spitze = -plan).
    all_owner_ids = set(owner_cost_lines) | set(plan_by_owner)

    owners: list[OwnerResult] = []
    plan_advances_total = Decimal("0.00")
    abr_total = Decimal("0.00")
    for owner_id in sorted(all_owner_ids, key=str):
        uml = owner_umlagefaehig.get(owner_id, Decimal("0.00"))
        nicht = owner_nicht.get(owner_id, Decimal("0.00"))
        res = owner_reserve.get(owner_id, Decimal("0.00"))
        owner_cost_total = _money(uml + nicht)
        plan_advance = plan_by_owner.get(owner_id, Decimal("0.00"))
        spitze = _money(owner_cost_total - plan_advance)
        plan_advances_total += plan_advance
        abr_total += spitze
        owners.append(
            OwnerResult(
                owner_profile_id=owner_id,
                cost_lines=tuple(owner_cost_lines.get(owner_id, [])),
                subtotal_umlagefaehig=_money(uml),
                subtotal_nicht_umlagefaehig=_money(nicht),
                subtotal_reserve=_money(res),
                owner_cost_total=owner_cost_total,
                plan_advance=plan_advance,
                abrechnungsspitze=spitze,
                paid_advances=paid_by_owner.get(owner_id),
            )
        )

    # Reserve development (m4 section 3.3) -- fail-loud invariant.
    opening = _money(inp.reserve.opening_balance)
    withdrawals = _money(inp.reserve.withdrawals_total)
    contributions = _money(reserve_contrib_total)
    closing = _money(opening + contributions - withdrawals)
    reserve_dev = ReserveDevelopment(
        opening_balance=opening,
        contributions_total=contributions,
        withdrawals_total=withdrawals,
        closing_balance=closing,
    )

    return WegResult(
        owners=tuple(owners),
        computation=tuple(computation),
        reserve=reserve_dev,
        cost_total=_money(cost_total),
        reserve_contributions_total=contributions,
        plan_advances_total=_money(plan_advances_total),
        abrechnungsspitze_total=_money(abr_total),
    )


def _position_note(pos: WegCostPositionInput) -> str:
    """A short, prueffest note for a WEG position line (m4 section 3/4)."""
    if pos.reserve_relevant:
        return "Zufuehrung Erhaltungsruecklage (keine Kostenumlage)"
    if pos.cost_group == COST_GROUP_NICHT_UMLAGEFAEHIG:
        return "nicht umlagefaehig (Eigentuemerkosten)"
    if pos.owner_distribution_key == "EXTERNE_ABRECHNUNG":
        return "extern abgerechnet"
    return ""
