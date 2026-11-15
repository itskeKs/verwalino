"""Bind features/dunning_owner_hausgeld_level.feature: one owner, two WEGs, no accounting scope."""
from pathlib import Path

from pytest_bdd import given, parsers, scenarios, then, when

from tests.financial_coverage_helpers import attest_fixture
from tests.settlement_period_support import create
from tests.settlement_period_support import period_data_cleanup as period_data_cleanup

scenarios(str(Path(__file__).resolve().parents[3] / "features" / "dunning_owner_hausgeld_level.feature"))


@given(parsers.parse(
    "Anna owns a unit in Gartenstraße 5 with a Hausgeld of {first:d} euros a month "
    "and one in Lindenweg 2 with {second:d} euros a month"))
def two_wegs(world, period_data_cleanup, first, second):
    # Owner notices without an Abrechnungskreis exist only in an organization that has none; the records of
    # this scenario are released at its end for the features that follow.
    world.actor = world.administrator_of("Verwaltung Alpha")
    person = world.run(create(world.actor, "/api/persons", {"first_name": "Anna", "last_name": "Hausgeld"}))
    owner = world.run(create(world.actor, "/api/owner-profiles", {"person_id": person["id"]}))
    plans = {}
    for name, monthly in (("Gartenstraße 5", first), ("Lindenweg 2", second)):
        prop = world.run(create(world.actor, "/api/properties", {
            "name": name, "management_type": "MISCH", "fiscal_year_start_month": 1, "mea_denominator": 1000}))
        unit = world.run(create(world.actor, "/api/units", {
            "property_id": prop["id"], "number": "1", "usage_type": "WOHNUNG", "area": "70", "mea": "1000"}))
        world.run(create(world.actor, "/api/ownership", {
            "unit_id": unit["id"], "owner_profile_id": owner["id"], "valid_from": "2020-01-01"}))
        plan = world.run(create(world.actor, "/api/economic-plans", {"property_id": prop["id"], "plan_year": 2026}))
        inputs = {"planned_costs": [{"category_code": "GARTENPFLEGE", "label": "Garten",
                                     "planned_amount": f"{monthly * 12}.00"}], "reserve_contribution_total": "0.00"}
        world.run(attest_fixture(world.actor, "economic_plan", plan["id"], inputs))
        answer = world.act("POST", f"/api/economic-plans/{plan['id']}/finalize", json=inputs)
        assert answer.status_code == 200, world.describe_response()
        plans[name] = plan["id"]
    world.data["hausgeld_wegs"] = {"owner": owner, "plans": plans}


def create_notice(world, name, day):
    data = world.data["hausgeld_wegs"]
    response = world.act("POST", "/api/dunning/owner", json={
        "owner_profile_id": data["owner"]["id"], "economic_plan_id": data["plans"][name],
        "period_start": "2026-03-01", "period_end": "2026-03-31", "reference_date": f"2026-03-{day:02d}"})
    assert response.status_code == 201, world.describe_response()
    return response.json()


@given(parsers.parse("Anna was reminded about the Hausgeld of March 2026 in {name} as of {day:d} March 2026"))
def reminded(world, name, day):
    notice = create_notice(world, name, day)
    issued = world.act("POST", f"/api/dunning/{notice['id']}/issue")
    assert issued.status_code == 200, world.describe_response()


@when(parsers.parse("the manager creates a notice for Anna's Hausgeld of March 2026 in {name} as of {day:d} March 2026"))
def create_march_notice(world, name, day):
    world.data["notice"] = create_notice(world, name, day)


@then(parsers.parse("the notice is a level {level:d} {kind} with {fee} fee and {total} total"))
def notice_level_fee_total(world, level, kind, fee, total):
    notice = world.data["notice"]
    assert (notice["level"], notice["fee_amount"], notice["total_amount"]) == (level, fee, total), notice
