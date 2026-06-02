"""Orchestrates one Wirtschaftsplan computation -> PlanResult (pure).

m4-weg-abrechnung.md section 5, ADR-0007 (decision E4-3b).

Forward-looking; NO Ist costs, NO Soll-Ist. Planned costs per category + the
planned reserve contribution are distributed to owners by MEA, then turned into
12 EQUAL MONTHLY rates (the monatsbasierte Maßstab, ADR-0007):

    Jahresvorschuss_owner = (Σ planned costs + reserve contribution) * MEA-share
    monthly rates          = distribute_amount(Jahresvorschuss, [1]*12)
                             (Largest-Remainder, ADR-0005 -> Σ 12 rates ==
                              Jahresvorschuss exactly; no cent drift over the year)

DISTINCT from the Ist-Abrechnung (which stays tag-exact, ADR-0004): the plan
abstracts from an unterjaehriger Eigentuemerwechsel (the plan plans for the unit;
who pays in which month is a payment/transition question M6). We take the owner
as of `period_start` (co-owners there split the unit by mea_share, m4 §4.3).

EVERY cent split -- the MEA distribution AND the 12 monthly rates -- runs through
the single central distribute_amount (ADR-0005). No second rounding place.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.engine.rounding import CENT, DistributionError, distribute_amount
from app.engine.types import (
    COST_GROUP_NICHT_UMLAGEFAEHIG,
    COST_GROUP_UMLAGEFAEHIG,
    OwnerPeriodInput,
    PlanOwnerResult,
    PlanResult,
    PlannedCostInput,
    ResultLine,
    UnitInput,
    WirtschaftsplanInput,
)
from app.engine.weg import WegValidationError, _participates, _unit_weight

MONTHS = 12


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT)


def _owners_at(
    owner_periods: list[OwnerPeriodInput],
    unit_id: uuid.UUID,
    day,
) -> list[OwnerPeriodInput]:
    """All owner records covering `unit_id` on `day` (co-owners possible)."""
    return [
        op
        for op in owner_periods
        if op.unit_id == unit_id
        and op.valid_from <= day
        and (op.valid_to is None or op.valid_to >= day)
    ]


def _validate(inp: WirtschaftsplanInput) -> None:
    if inp.period_end < inp.period_start:
        raise WegValidationError(
            f"period_end ({inp.period_end}) < period_start ({inp.period_start})"
        )
    if not inp.units:
        raise WegValidationError("Wirtschaftsplan has no units")
    if inp.reserve_contribution_total < 0:
        raise WegValidationError(
            f"reserve_contribution_total is negative: {inp.reserve_contribution_total}"
        )
    for pc in inp.planned_costs:
        if pc.planned_amount < 0:
            raise WegValidationError(
                f"Planned cost {pc.category_code} has negative amount "
                f"{pc.planned_amount}"
            )
        if pc.distribution_key != "MEA":
            # M4 plan uses MEA (the WEG default). Other keys for the plan are a
            # later extension; escalate rather than silently mis-plan.
            raise WegValidationError(
                f"Planned cost {pc.category_code} uses distribution_key "
                f"{pc.distribution_key}; the M4 Wirtschaftsplan supports MEA only "
                f"(m4 section 5.1 / [§18])."
            )


def compute_wirtschaftsplan(inp: WirtschaftsplanInput) -> PlanResult:
    """Compute the Wirtschaftsplan (m4 section 5, ADR-0007 E4-3b)."""
    _validate(inp)

    units = list(inp.units)
    owner_periods = list(inp.owner_periods)

    # Sum the planned cost volume per sphere + the reserve contribution.
    subtotal_uml = Decimal("0.00")
    subtotal_nicht = Decimal("0.00")
    planned_lines: list[ResultLine] = []

    positions = sorted(inp.planned_costs, key=lambda p: (p.category_code, p.label))
    for pc in positions:
        amt = _money(pc.planned_amount)
        if pc.cost_group == COST_GROUP_NICHT_UMLAGEFAEHIG:
            subtotal_nicht += amt
        else:
            subtotal_uml += amt

    reserve_contribution = _money(inp.reserve_contribution_total)
    # The total advance volume = planned costs (all spheres) + reserve.
    total_volume = _money(subtotal_uml + subtotal_nicht + reserve_contribution)

    # ----- MEA distribution of the total volume over the units -----
    # Participating units = those with a positive MEA (the WEG universe).
    participating = [u for u in units if _participates(u, "MEA")]
    if not participating:
        raise WegValidationError(
            "No unit with a positive MEA -- cannot distribute the plan (escalate)."
        )
    weights = {u.id: _unit_weight(u, "MEA") for u in participating}
    weight_sum = sum(weights.values(), Decimal(0))
    if weight_sum == 0:
        raise DistributionError("Sum of MEA weights is zero -- cannot distribute plan.")

    # Build the per-unit -> per-owner cell list (co-owner split at period_start),
    # then ONE distribute_amount pass over all owner cells so Σ == total_volume.
    # Cell weight = unit_MEA_share * co_owner_mea_share.
    cell_specs: list[tuple[uuid.UUID, uuid.UUID, Decimal, Decimal, Decimal]] = []
    # (unit_id, owner_id, exact_weight, unit_mea, co_owner_mea_or_full)
    for unit in participating:
        unit_mea = weights[unit.id]
        unit_share = unit_mea / weight_sum
        owners_here = _owners_at(owner_periods, unit.id, inp.period_start)
        if not owners_here:
            raise WegValidationError(
                f"No owner for unit {unit.number} ({unit.id}) at plan start "
                f"{inp.period_start} -- cannot assign the plan advance (escalate)."
            )
        if len(owners_here) == 1:
            o = owners_here[0]
            cell_specs.append((unit.id, o.owner_profile_id, unit_share, unit_mea, unit_mea))
        else:
            # Co-ownership at plan start: split the unit share by mea_share.
            co_sum = sum(
                (o.mea_share for o in owners_here if o.mea_share is not None),
                Decimal(0),
            )
            if co_sum <= 0:
                raise WegValidationError(
                    f"Co-ownership of unit {unit.id} at plan start but total "
                    f"mea_share is {co_sum} -- cannot split (escalate, [F4-7])."
                )
            for o in owners_here:
                if o.mea_share is None or o.mea_share <= 0:
                    raise WegValidationError(
                        f"Co-owner {o.owner_profile_id} of unit {unit.id} has "
                        f"non-positive mea_share at plan start (escalate)."
                    )
                frac = o.mea_share / co_sum
                cell_specs.append(
                    (unit.id, o.owner_profile_id, unit_share * frac, unit_mea, o.mea_share)
                )

    cell_weights = [c[2] for c in cell_specs]
    annual_shares = distribute_amount(total_volume, cell_weights)

    owners_out: list[PlanOwnerResult] = []
    for (unit_id, owner_id, _w, unit_mea, co_mea), annual in zip(cell_specs, annual_shares):
        annual = _money(annual)
        # 12 equal monthly rates via the single central rounding (ADR-0005).
        monthly_rates = tuple(distribute_amount(annual, [Decimal(1)] * MONTHS))
        owners_out.append(
            PlanOwnerResult(
                owner_profile_id=owner_id,
                unit_id=unit_id,
                annual_advance=annual,
                monthly_rates=monthly_rates,
                monthly_amount=monthly_rates[0],
                distribution_base_share=unit_mea,
                distribution_base_total=weight_sum,
            )
        )

    # Build the planned-cost lines (per category + the reserve contribution),
    # each with its MEA basis -- the property-wide planned amount, not per owner.
    sort_order = 0
    for pc in positions:
        amt = _money(pc.planned_amount)
        planned_lines.append(
            ResultLine(
                line_type="PLANNED_COST",
                label=pc.label,
                amount=amt,
                sort_order=sort_order,
                category_id=pc.category_id,
                category_code=pc.category_code,
                distribution_key=pc.distribution_key,
                total_amount=amt,
                distribution_base_total=weight_sum,
                note=(
                    "nicht umlagefaehig"
                    if pc.cost_group == COST_GROUP_NICHT_UMLAGEFAEHIG
                    else ""
                ),
            )
        )
        sort_order += 1
    if reserve_contribution != Decimal("0.00") or True:
        # Always emit the reserve-contribution line (even if 0) so the document
        # explicitly shows the Vermoegensbildung sphere (m4 section 3/5.2).
        planned_lines.append(
            ResultLine(
                line_type="RESERVE_CONTRIBUTION",
                label="Zufuehrung Erhaltungsruecklage",
                amount=reserve_contribution,
                sort_order=sort_order,
                distribution_key="MEA",
                total_amount=reserve_contribution,
                distribution_base_total=weight_sum,
                note="Vermoegensbildung (keine Kostenumlage)",
            )
        )

    # Sort owners deterministically (by owner id, then unit id).
    owners_out.sort(key=lambda o: (str(o.owner_profile_id), str(o.unit_id)))

    return PlanResult(
        planned_lines=tuple(planned_lines),
        subtotal_umlagefaehig=_money(subtotal_uml),
        subtotal_nicht_umlagefaehig=_money(subtotal_nicht),
        reserve_contribution_total=reserve_contribution,
        total_advance_volume=total_volume,
        owners=tuple(owners_out),
    )
