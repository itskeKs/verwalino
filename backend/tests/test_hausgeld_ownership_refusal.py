"""#668: a history corrected after a plan was finalized, and what the Hausgeld Soll then says.

Pure functions, no database. The plan freezes its owner advances at its start; the Soll then
walks the dated history, so a corrected history the frozen lines cannot follow is refused until
the plan is computed anew. The engine's successor rule is not changed here.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.ownership import _corrected_share
from app.engine.mietkonto import OwnerAdvanceChargeInput, owner_expected_charges
from app.engine.types import OwnerPeriodInput, PropertyInput, UnitInput, WirtschaftsplanInput
from app.engine.wirtschaftsplan import compute_wirtschaftsplan
from app.services.payment_service import PaymentService

pytestmark = pytest.mark.no_db

START, END = date(2026, 1, 1), date(2026, 12, 31)
UNIT, OTHER_UNIT = uuid.UUID(int=10), uuid.UUID(int=20)
BERND, CARLA, DAVID = uuid.UUID(int=1), uuid.UUID(int=2), uuid.UUID(int=3)


def owners(carla_from: date) -> list[OwnerPeriodInput]:
    return [OwnerPeriodInput(UNIT, BERND, START, None, Decimal("60")),
            OwnerPeriodInput(UNIT, CARLA, carla_from, None, Decimal("40"))]


def frozen_lines(history) -> list[OwnerAdvanceChargeInput]:
    """The owner advances a plan of 4.800,00 freezes from a history: owners at its start only."""
    plan = compute_wirtschaftsplan(WirtschaftsplanInput(
        plan_year=2026, period_start=START, period_end=END,
        property=PropertyInput(uuid.UUID(int=30), "WEG", "WEG"),
        units=(UnitInput(UNIT, "1", "WOHNUNG", mea=Decimal("1000")),),
        owner_periods=tuple(history), planned_costs=(), reserve_contribution_total=Decimal("4800.00")))
    return [OwnerAdvanceChargeInput(o.owner_profile_id, o.unit_id, o.annual_advance) for o in plan.owners]


def soll(lines, owner, history):
    return owner_expected_charges(lines, owner, START, END, owner_periods=history, plan_start=START, plan_end=END)


def test_a_start_corrected_to_the_plan_start_is_followed_again_by_a_plan_computed_anew():
    entered, corrected = owners(date(2026, 3, 1)), owners(START)
    frozen = frozen_lines(entered)  # Carla joined after the key date: only Bernd carries the whole 4.800,00
    assert [(ln.owner_profile_id, ln.annual_amount) for ln in frozen] == [(BERND, Decimal("4800.00"))]
    with pytest.raises(ValueError, match="successors"):
        soll(frozen, BERND, entered)  # a co-owner joining without a departing one: the successor rule refuses
    with pytest.raises(ValueError, match="successors"):
        soll(frozen, BERND, corrected)  # the correction alone does not help: the frozen lines miss Carla
    renewed = frozen_lines(corrected)
    # By hand: 4.800,00 split 60 : 40 = 2.880,00 and 1.920,00 a year, 240,00 and 160,00 a month.
    assert [(ln.owner_profile_id, ln.annual_amount) for ln in renewed] == [
        (BERND, Decimal("2880.00")), (CARLA, Decimal("1920.00"))]
    for owner, monthly in ((BERND, "240.00"), (CARLA, "160.00")):
        charges = soll(renewed, owner, corrected)
        assert [(c.period_month, c.amount) for c in charges] == [(m, Decimal(monthly)) for m in range(1, 13)]


def test_a_coowner_joining_after_the_plan_start_stays_refused_whatever_the_plan_says():
    both_from_january, joined_in_march = owners(START), owners(date(2026, 3, 1))
    frozen = frozen_lines(both_from_january)
    assert [ln.annual_amount for ln in frozen] == [Decimal("2880.00"), Decimal("1920.00")]
    with pytest.raises(ValueError, match="successors"):
        soll(frozen, BERND, joined_in_march)  # lines for Carla, but she is no owner in January
    renewed = frozen_lines(joined_in_march)  # a plan computed anew carries only Bernd, Carla still joins
    with pytest.raises(ValueError, match="successors"):
        soll(renewed, BERND, joined_in_march)


def plan_of(year: int = 2026):
    return SimpleNamespace(plan_year=year, period_start=START, period_end=END)


def test_the_refusal_names_the_failing_unit_and_the_plan_correction():
    history = owners(date(2026, 3, 1)) + [OwnerPeriodInput(OTHER_UNIT, DAVID, date(2020, 1, 1), None)]
    lines = frozen_lines(owners(date(2026, 3, 1))) + [OwnerAdvanceChargeInput(DAVID, OTHER_UNIT, Decimal("1200.00"))]
    service = PaymentService.__new__(PaymentService)
    service._unit_numbers = {UNIT: "2", OTHER_UNIT: "1"}  # unit 1 is fine, so the failing unit is not the first one
    refusal = service._ownership_refusal(plan_of(), lines, lambda unit_lines: soll(unit_lines, BERND, history))
    assert (refusal["code"], refusal["unit_id"], refusal["unit_number"]) == ("hausgeld_ownership_invalid", str(UNIT), "2")
    message = refusal["message"]
    assert "Einheit 2" in message and "Einheit 1" not in message
    assert "Wirtschaftsplan 2026" in message and "„Korrektur erstellen“" in message and "Eigentümer-Historie" in message


def test_the_refusal_without_a_nameable_unit_still_names_the_way_out():
    service = PaymentService.__new__(PaymentService)
    service._unit_numbers = {}
    refusal = service._ownership_refusal(plan_of(2027), [], lambda unit_lines: [])
    assert (refusal["unit_id"], refusal["unit_number"]) == (None, None)
    assert "einer Einheit" in refusal["message"] and "„Korrektur erstellen“" in refusal["message"]


@pytest.mark.parametrize("typed,code", [
    ("999999,9999999", "ownership_correction_share"),  # would store as 1000000.000000
    ("0,0000001", "ownership_correction_share"),  # would store as 0.000000
    ("1E+30", "ownership_correction_share"), ("NaN", "ownership_correction_share"),
])
def test_a_share_that_rounds_out_of_range_is_refused_with_its_code(typed, code):
    with pytest.raises(HTTPException) as refused:
        _corrected_share(typed)
    assert refused.value.status_code == 422 and refused.value.detail["code"] == code


@pytest.mark.parametrize("typed,stored", [("50,0000001", "50.000000"), ("0,0000005", "0.000001"),
                                          ("999999,9999994", "999999.999999"), ("0,5", "0.500000")])
def test_a_share_is_rounded_to_six_decimals_before_it_is_used(typed, stored):
    assert str(_corrected_share(typed)) == stored
