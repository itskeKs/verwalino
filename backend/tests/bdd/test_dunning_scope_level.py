"""Bind features/dunning_scope_level.feature: one rental agreement that two accounting scopes cover."""
from pathlib import Path

from pytest_bdd import given, parsers, scenarios, then, when

from tests.bdd.steps.browser_setup import bank_account, create_scope, rental
from tests.settlement_period_support import period_data_cleanup as period_data_cleanup

scenarios(str(Path(__file__).resolve().parents[3] / "features" / "dunning_scope_level.feature"))

PERIODS = {"June": ("2026-06-01", "2026-06-30"), "July": ("2026-07-01", "2026-07-31")}
MONTH_NUMBERS = {"July": 7, "August": 8}


@given("a rental agreement of 600 EUR rent that two accounting scopes of 2026 cover")
def two_scopes(world, seed_data, period_data_cleanup):
    # Scopes and issued notices are immutable, so the records of this scenario are released at its end
    # for the features that run after it.
    rental(world, seed_data, advance="0.00")
    response = world.act("PATCH", f"/api/leases/{world.data['leases'][0]['id']}", json={"base_rent": "600.00"})
    assert response.status_code == 200, world.describe_response()
    first = world.data["scope"]
    bank_account(world)
    world.data["scopes"] = {"first": first, "second": create_scope(world, world.data["user_id"], "Zweiter Mietkreis")}


def create_notice(world, scope, month, day, reference_month):
    reference_date = f"2026-{MONTH_NUMBERS[reference_month]:02d}-{day:02d}"
    start, end = PERIODS[month]
    response = world.act("POST", "/api/dunning/tenant", json={
        "tenant_profile_id": world.data["tenants"][0]["id"], "lease_agreement_id": world.data["leases"][0]["id"],
        "accounting_scope_id": world.data["scopes"][scope]["id"], "period_start": start, "period_end": end,
        "reference_date": reference_date})
    assert response.status_code == 201, world.describe_response()
    return response.json()


@given(parsers.parse("the first scope issued a reminder for {month} 2026 as of {day:d} {when} 2026"))
def issued_reminder(world, month, day, when):
    notice = create_notice(world, "first", month, day, when)
    issued = world.act("POST", f"/api/dunning/{notice['id']}/issue")
    assert issued.status_code == 200, world.describe_response()


@given(parsers.parse("the first scope prepared a reminder for {month} 2026 as of {day:d} {when} 2026 and did not issue it"))
def prepared_reminder(world, month, day, when):
    create_notice(world, "first", month, day, when)


@when(parsers.parse("the manager creates a notice for {month} 2026 in the second scope as of {day:d} {when} 2026"))
def create_second_scope_notice(world, month, day, when):
    world.data["notice"] = create_notice(world, "second", month, day, when)


@then(parsers.parse("the notice is a level {level:d} {kind} with {fee} fee and {total} total"))
def notice_level_fee_total(world, level, kind, fee, total):
    notice = world.data["notice"]
    assert (notice["level"], notice["fee_amount"], notice["total_amount"]) == (level, fee, total), notice
