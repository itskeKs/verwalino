"""Bind features/dunning_levy_level.feature to the shared special levy steps."""
from pathlib import Path

from pytest_bdd import given, parsers, scenarios, then, when

from tests.bdd.steps.browser_setup import bank_account, create
from tests.bdd.steps.special_levy import levy_payload
from tests.settlement_period_support import period_data_cleanup as period_data_cleanup

scenarios(str(Path(__file__).resolve().parents[3] / "features" / "dunning_levy_level.feature"))


@given("two roof repair levies of 5000 euros fall due on 5 June and on 25 June 2026")
def two_levies_in_june(world, period_data_cleanup):
    # An organization with special levies answers no unscoped statement read, so the records of
    # this scenario are released at its end for the features that run after it.
    for due_date in ("2026-06-05", "2026-06-25"):
        create(world, "/api/special-levies", {
            **levy_payload(world), "amount": "5000.00", "due_date": due_date,
            "resolution_id": world.data["levy_resolution"]["id"]})


def create_notice(world, reference_date):
    response = world.act("POST", "/api/dunning/owner", json={
        "owner_profile_id": world.data["levy_owners"][0]["id"], "period_start": "2026-06-01",
        "period_end": "2026-06-30", "reference_date": reference_date,
        "accounting_scope_id": world.data["scope"]["id"]})
    assert response.status_code == 201, world.describe_response()
    return response.json()


@given("Anna was reminded about the first levy on 10 June and again on 12 June 2026")
def reminded_twice(world):
    for reference_date, expected in (("2026-06-10", (1, "0.00", "3000.00")), ("2026-06-12", (2, "2.50", "3002.50"))):
        notice = create_notice(world, reference_date)
        assert (notice["level"], notice["fee_amount"], notice["total_amount"]) == expected, notice
        issued = world.act("POST", f"/api/dunning/{notice['id']}/issue")
        assert issued.status_code == 200, world.describe_response()


@given("Anna paid her 3000 euro share of the first levy on 20 June 2026")
def paid_first_levy(world):
    account = bank_account(world)
    payment = create(world, "/api/payments", {
        "bank_account_id": account["id"], "direction": "IN", "amount": "3000.00", "value_date": "2026-06-20",
        "payment_type": "SONDERZAHLUNG", "owner_profile_id": world.data["levy_owners"][0]["id"],
        "period_year": 2026, "period_month": 6})
    booked = world.act("POST", f"/api/payments/{payment['id']}/book")
    assert booked.status_code == 200, world.describe_response()


@when(parsers.parse("the manager creates a notice for Anna's June levy arrears as of {day:d} June 2026"))
def create_june_notice(world, day):
    world.data["notice"] = create_notice(world, f"2026-06-{day:02d}")


@then(parsers.parse("the notice is a level {level:d} {kind} with {fee} fee and {total} total"))
def notice_level_fee_total(world, level, kind, fee, total):
    notice = world.data["notice"]
    assert (notice["level"], notice["fee_amount"], notice["total_amount"]) == (level, fee, total), notice
