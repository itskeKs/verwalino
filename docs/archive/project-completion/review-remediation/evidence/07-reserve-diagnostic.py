"""DB-free observation of the original plan's WEG reserve boundary.

Run from the repository root with the existing project interpreter:
  env -u USER -u HV_OPENAI_API_KEY PYTHONPATH=backend backend/.venv/bin/python \
    docs/project-completion/review-remediation/evidence/07-reserve-diagnostic.py
These assertions preserve observed behavior; they are not V01 approval.
"""
from datetime import date
from decimal import Decimal
from uuid import UUID

from app.engine.types import (
    OwnerAdvanceInput, OwnerPeriodInput, PlannedCostInput, PropertyInput,
    UnitInput, WegCostPositionInput, WegSettlementInput, WirtschaftsplanInput,
)
from app.engine.weg import compute_weg_jahresabrechnung
from app.engine.wirtschaftsplan import compute_wirtschaftsplan

start, end = date(2025, 1, 1), date(2025, 12, 31)
owner_id, unit_id = UUID(int=21), UUID(int=11)
prop = PropertyInput(id=UUID(int=1), name="Synthetic WEG", management_type="WEG")
units = (UnitInput(id=unit_id, number="1", usage_type="WOHNUNG", mea=Decimal("1000")),)
owners = (OwnerPeriodInput(unit_id=unit_id, owner_profile_id=owner_id,
                           valid_from=start, valid_to=None),)
plan = compute_wirtschaftsplan(WirtschaftsplanInput(
    plan_year=2025, period_start=start, period_end=end, property=prop,
    units=units, owner_periods=owners,
    planned_costs=(PlannedCostInput(category_id=None, category_code="VERWALTUNG",
        label="Synthetic cost", planned_amount=Decimal("1000"),
        distribution_key="MEA", cost_group="NICHT_UMLAGEFAEHIG"),),
    reserve_contribution_total=Decimal("200"),
))
advance = sum((o.annual_advance for o in plan.owners if o.owner_profile_id == owner_id), Decimal(0))
result = compute_weg_jahresabrechnung(WegSettlementInput(
    period_start=start, period_end=end, property=prop, units=units,
    owner_periods=owners,
    cost_positions=(WegCostPositionInput(cost_entry_id=UUID(int=50),
        category_id=None, category_code="VERWALTUNG", label="Synthetic actual cost",
        gross_amount=Decimal("1000"), service_period_from=start,
        service_period_to=end, owner_distribution_key="MEA",
        cost_group="NICHT_UMLAGEFAEHIG"),),
    plan_advances=(OwnerAdvanceInput(owner_profile_id=owner_id, plan_advance_total=advance),),
))
owner = result.owners[0]
assert plan.total_advance_volume == advance == Decimal("1200.00")
assert owner.owner_cost_total == Decimal("1000.00")
assert owner.abrechnungsspitze == Decimal("-200.00")
print(f"planned_cost=1000.00 planned_reserve=200.00 owner_advance={advance}")
print(f"owner_cost_total={owner.owner_cost_total} abrechnungsspitze={owner.abrechnungsspitze}")
print("OBSERVATION CONFIRMED; target financial semantics remain blocked pending V01")
