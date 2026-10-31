from decimal import Decimal
from pytest_bdd import given, when, then
from tests.settlement_period_support import period_property, booked, statement, create
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


@given("a calendar-year property where tenant A leaves on 30 September and tenant B moves in on 1 October")
def successive_tenants(world, period_data_cleanup):
    data = world.run(period_property(world.actor, month=1))
    changed = world.act("PATCH", f"/api/leases/{data['lease']['id']}", json={"valid_to": "2025-09-30"})
    assert changed.status_code == 200, world.describe_response()
    person = world.run(create(world.actor, "/api/persons", {"last_name": "Nachmieter B"}))
    tenant = world.run(create(world.actor, "/api/tenant-profiles", {"person_id": person["id"]}))
    lease = world.run(create(world.actor, "/api/leases", {
        "unit_id": data["unit"]["id"], "tenant_profile_id": tenant["id"],
        "valid_from": "2025-10-01", "base_rent": "700.00", "advance_payment": "100.00",
    }))
    world.data["successive_tenants"] = {"A": data, "B": {**data, "tenant": tenant, "lease": lease}}


def finalize_tenant_statement(world, data, start, end):
    run = world.run(create(world.actor, "/api/settlements", {
        **statement(data, start, end), "advance_payments_total": "0.00",
    }))
    finalize_zero_advance_statement(world, run["id"])
    return world.response.json()


def finalize_zero_advance_statement(world, run_id):
    world.run(attest_fixture(world.actor, "settlement", run_id, {
        "advance_payments_total": "0.00", "advance_payments_source": "MANUAL",
    }))
    answer = world.act("POST", f"/api/settlements/{run_id}/finalize", json={})
    assert answer.status_code == 200, world.describe_response()


@when("the manager finalizes statements for each tenant's occupied months")
def finalize_occupied_months(world):
    tenants = world.data["successive_tenants"]
    world.data["tenant_statements"] = {
        "A": finalize_tenant_statement(world, tenants["A"], "2025-01-01", "2025-09-30"),
        "B": finalize_tenant_statement(world, tenants["B"], "2025-10-01", "2025-12-31"),
    }


@then("A's statement covers January through September and B's covers October through December")
def occupied_statement_dates(world):
    runs = world.data["tenant_statements"]
    assert (runs["A"]["period_start"], runs["A"]["period_end"]) == ("2025-01-01", "2025-09-30")
    assert (runs["B"]["period_start"], runs["B"]["period_end"]) == ("2025-10-01", "2025-12-31")


@when("the manager tries to create a calendar-year statement for tenant A")
def former_tenant_calendar_statement(world):
    world.act("POST", "/api/settlements", json=statement(world.data["successive_tenants"]["A"], "2025-01-01", "2025-12-31"))


@when("the manager repeats A's January through September statement")
def repeat_former_tenant_statement(world):
    world.data["repeated_tenant_statement"] = world.run(create(world.actor, "/api/settlements", {
        **statement(world.data["successive_tenants"]["A"], "2025-01-01", "2025-09-30"),
        "advance_payments_total": "0.00",
    }))


@then("the identical statement can be finalized again")
def finalize_repeated_statement(world):
    run = world.data["repeated_tenant_statement"]
    assert run["id"] != world.data["tenant_statements"]["A"]["id"]
    finalize_zero_advance_statement(world, run["id"])


@given("annual operating costs of 3650 euros for that unit")
def annual_operating_cost(world):
    categories = world.act("GET", "/api/cost-categories")
    assert categories.status_code == 200, world.describe_response()
    category = next(c for c in categories.json() if c["code"] == "GRUNDSTEUER")
    entry = world.run(create(world.actor, "/api/cost-entries", {
        "property_id": world.data["successive_tenants"]["A"]["property"]["id"],
        "cost_category_id": category["id"], "invoice_date": "2025-01-01",
        "service_period_from": "2025-01-01", "service_period_to": "2025-12-31",
        "gross_amount": "3650.00", "tenant_distribution_key": "WOHNFLAECHE",
        "tenant_apportionable": "UMLAGEFAEHIG",
    }))
    answer = world.act("POST", f"/api/cost-entries/{entry['id']}/confirm")
    assert answer.status_code == 200, world.describe_response()


@when("the manager finalizes calendar-year calculations for both tenants")
def finalize_calendar_calculations(world):
    world.data["tenant_statements"] = {
        tenant: finalize_tenant_statement(world, data, "2025-01-01", "2025-12-31")
        for tenant, data in world.data["successive_tenants"].items()
    }


@then("A pays 2730 euros for January through September and B pays 920 euros for October through December")
def successive_tenant_shares(world):
    for tenant, amount, days in (("A", "2730.00", 273), ("B", "920.00", 92)):
        run = world.data["tenant_statements"][tenant]
        assert Decimal(run["total_cost"]) == Decimal(amount)
        line, = [line for line in run["lines"] if line["line_type"] == "COST"]
        assert line["time_share_days"] == days
        assert line["time_share_total_days"] == 365


@given("a calendar-year tenancy with a booked yearly advance of 1200 euros")
def yearly_advance(world, period_data_cleanup):
    data = world.run(period_property(world.actor, month=1))
    world.run(booked(world.actor, data, 2025, None, amount="1200.00"))
    world.data["yearly_advance_property"] = data


@when("the manager finalizes statements for January through June and July through December")
def adjacent_yearly_advance_statements(world):
    data = world.data["yearly_advance_property"]
    runs = []
    for start, end in (("2025-01-01", "2025-06-30"), ("2025-07-01", "2025-12-31")):
        run = world.run(create(world.actor, "/api/settlements", statement(data, start, end)))
        derived = world.act("PATCH", f"/api/settlements/{run['id']}/advance-payments-derived", params={"period_year": 2025})
        assert derived.status_code == 200, world.describe_response()
        world.run(attest_fixture(world.actor, "settlement", run["id"], {
            "advance_payments_total": derived.json()["advance_payments_total"], "advance_payments_source": "DERIVED",
        }))
        final = world.act("POST", f"/api/settlements/{run['id']}/finalize", json={})
        assert final.status_code == 200, world.describe_response()
        runs.append(final.json())
    world.data["yearly_advance_statements"] = runs


@then("each statement credits 600 euros of the yearly advance")
def yearly_advance_is_shared(world):
    totals = [Decimal(run["advance_payments_total"]) for run in world.data["yearly_advance_statements"]]
    assert totals == [Decimal("600.00"), Decimal("600.00")]
    assert sum(totals) == Decimal("1200.00")
