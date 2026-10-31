from decimal import Decimal
from pytest_bdd import given, when, then
from tests.settlement_period_support import period_property, booked, statement
from tests.settlement_period_support import period_data_cleanup  # noqa: F401
from tests.financial_coverage_helpers import attest_fixture


@given("a mixed property with a tenancy and an April settlement start")
def april_property(world, period_data_cleanup):
    world.data["period_property"] = world.run(period_property(world.actor))


@given("advances booked for December 2025 and January 2026")
def cross_year_advances(world):
    for year, month in ((2025, 12), (2026, 1)):
        world.run(booked(world.actor, world.data["period_property"], year, month))


@when("the manager chooses the offered statement period 2025/2026")
def choose_period(world):
    data = world.data["period_property"]
    answer = world.act("GET", f"/api/properties/{data['property']['id']}/settlement-periods", params={"year": 2025})
    assert answer.status_code == 200, world.describe_response()
    offer = next(p for p in answer.json() if p["label"] == "2025/2026")
    answer = world.act("POST", "/api/settlements", json=statement(data, offer["period_start"], offer["period_end"]))
    assert answer.status_code == 201, world.describe_response()
    world.data["period_run"] = answer.json()


@then("the draft covers 1 April 2025 through 31 March 2026")
def draft_period(world):
    run = world.data["period_run"]
    assert (run["period_start"], run["period_end"]) == ("2025-04-01", "2026-03-31")


@then("both booked advances are credited")
def credited(world):
    answer = world.act("PATCH", f"/api/settlements/{world.data['period_run']['id']}/advance-payments-derived", params={"period_year": 2026})
    assert answer.status_code == 200, world.describe_response()
    assert Decimal(answer.json()["advance_payments_total"]) == Decimal("200.00")


@when("the manager finalizes the statement")
def finalize_statement(world):
    run_id = world.data["period_run"]["id"]
    world.run(attest_fixture(world.actor, "settlement", run_id,
        {"advance_payments_total": "200.00", "advance_payments_source": "DERIVED"}))
    answer = world.act("POST", f"/api/settlements/{run_id}/finalize", json={})
    assert answer.status_code == 200, world.describe_response()


@when("the manager tries to create an overlapping statement for that tenancy")
def overlapping_statement(world):
    world.act("POST", "/api/settlements", json=statement(world.data["period_property"], "2026-01-01", "2026-12-31"))


@then("the overlapping statement is refused")
def refused(world):
    assert world.response.status_code == 409, world.describe_response()


@when("the manager creates the property's economic plan for 2025")
def create_plan(world):
    world.act("POST", "/api/economic-plans", json={"property_id": world.data["period_property"]["property"]["id"],
        "plan_year": 2025})


@then("the plan covers 1 April 2025 through 31 March 2026")
def plan_period(world):
    assert world.response.status_code == 201, world.describe_response()
    plan = world.response.json()
    assert (plan["plan_year"], plan["period_start"], plan["period_end"]) == (2025, "2025-04-01", "2026-03-31")
