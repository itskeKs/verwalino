"""Bind features/dunning_set_off.feature: a lease whose June was paid beyond its charge."""
from calendar import monthrange
from decimal import Decimal
from pathlib import Path

from pytest_bdd import given, parsers, scenarios, then, when

from tests.bdd.steps.browser_setup import bank_account, create, rental
from tests.settlement_period_support import period_data_cleanup as period_data_cleanup

scenarios(str(Path(__file__).resolve().parents[3] / "features" / "dunning_set_off.feature"))

YEAR = {"period_start": "2026-01-01", "period_end": "2026-12-31"}


@given(parsers.parse("a rental agreement of {rent:d} EUR rent and {advance:d} EUR advance payment per month in 2026"))
def lease(world, seed_data, period_data_cleanup, rent, advance):
    # Scopes and drafts are immutable, so the records of this scenario are released at its end. That release truncates
    # the second factors of the administrators too, so a scenario that changes the dunning settings names this step
    # first: its fixtures tear down in reverse, and the settings are restored (a call that a hosted organization
    # refuses without a second factor) before the release.
    rental(world, seed_data, advance=f"{advance}.00")
    response = world.act("PATCH", f"/api/leases/{world.data['leases'][0]['id']}", json={"base_rent": f"{rent}.00"})
    assert response.status_code == 200, world.describe_response()


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
PAYMENT_TYPES = {"rent": "KALTMIETE"}


def month_number(name):
    return MONTHS.index(name) + 1


def book(world, amount, kind, month, value_date):
    """A booked payment of the lease for a month of 2026, into the one rental account of the scenario."""
    account = world.data.get("account") or bank_account(world, deposit=kind == "KAUTION")
    payment = create(world, "/api/payments", {
        "accounting_scope_id": world.data["scope"]["id"], "bank_account_id": account["id"],
        "tenant_profile_id": world.data["tenants"][0]["id"], "lease_agreement_id": world.data["leases"][0]["id"],
        "amount": f"{amount}.00", "direction": "IN", "value_date": value_date, "payment_type": kind,
        "period_year": 2026, "period_month": month,
    })
    assert world.act("POST", f"/api/payments/{payment['id']}/book").status_code == 200, world.describe_response()


@given(parsers.re(r"(?P<amount>\d+) EUR is booked as (?P<kind>rent|[A-Z_]+) for (?P<month>[A-Za-z]+) 2026$"))
def book_for_month(world, amount, kind, month):
    book(world, amount, PAYMENT_TYPES.get(kind, kind), month_number(month), f"2026-{month_number(month):02d}-02")


@given(parsers.re(r"(?P<amount>\d+) EUR is booked as rent for (?P<month>[A-Za-z]+) 2026 on (?P<day>\d+) "
                  r"(?P<paid_month>[A-Za-z]+) 2026$"))
def book_on_a_day(world, amount, month, day, paid_month):
    book(world, amount, "KALTMIETE", month_number(month), f"2026-{month_number(paid_month):02d}-{int(day):02d}")


@when("the manager looks at the open items of 2026 as of 15 January 2027")
def open_items(world):
    # The rent account of the lease: its open items, with the reference date of the notice (no clock involved).
    response = world.act("GET", f"/api/payments/mietkonto/lease/{world.data['leases'][0]['id']}",
                         params={**YEAR, "accounting_scope_id": world.data["scope"]["id"], "as_of": "2027-01-15"})
    assert response.status_code == 200, world.describe_response()
    world.data["open_items"] = [row for row in response.json()["open_items"] if Decimal(row["open_amount"]) != 0]


def create_notice(world, period_start, period_end, reference_date):
    world.act("POST", "/api/dunning/tenant", json={
        "tenant_profile_id": world.data["tenants"][0]["id"], "lease_agreement_id": world.data["leases"][0]["id"],
        "accounting_scope_id": world.data["scope"]["id"], "period_start": period_start, "period_end": period_end,
        "reference_date": reference_date})


@when("the manager creates a notice for 2026 as of 15 January 2027")
def create_year_notice(world):
    create_notice(world, "2026-01-01", "2026-12-31", "2027-01-15")


@when(parsers.re(r"the manager creates a notice for (?P<first>[A-Za-z]+)(?: to (?P<last>[A-Za-z]+))? 2026 as of "
                 r"(?P<day>\d+) (?P<month>[A-Za-z]+) (?P<year>\d{4})$"))
def create_notice_for_months(world, first, last, day, month, year):
    first_month, last_month = month_number(first), month_number(last or first)
    create_notice(world, f"2026-{first_month:02d}-01", f"2026-{last_month:02d}-{monthrange(2026, last_month)[1]}",
                  f"{year}-{month_number(month):02d}-{int(day):02d}")


@then(parsers.parse("the open items add up to {amount:d} EUR"))
def open_items_total(world, amount):
    assert sum(Decimal(row["open_amount"]) for row in world.data["open_items"]) == Decimal(amount), world.data["open_items"]


@then(parsers.parse("the open items show a credit of {amount:d} EUR"))
def open_items_credit(world, amount):
    assert [Decimal(row["open_amount"]) for row in world.data["open_items"]] == [-Decimal(amount)], world.data["open_items"]


@then(parsers.parse("the notice demands {amount:d} EUR for the positions the open items list"))
def notice_matches_open_items(world, amount):
    assert world.response.status_code == 201, world.describe_response()
    notice = world.response.json()
    assert Decimal(notice["principal_amount"]) == Decimal(amount), notice["principal_amount"]

    def positions(rows):
        return sorted((row["period_month"], row["payment_type"], row["open_amount"]) for row in rows)

    assert positions(notice["included_items"]) == positions(world.data["open_items"])


@then("no notice is created because nothing is overdue")
def no_notice(world):
    assert world.response.status_code == 422, world.describe_response()
    assert world.response.json()["detail"] == "No overdue items found for this tenant in the given period"


@then(parsers.parse("the notice demands {amount:d} EUR"))
def notice_principal(world, amount):
    assert world.response.status_code == 201, world.describe_response()
    assert Decimal(world.response.json()["principal_amount"]) == Decimal(amount), world.response.json()


@then(parsers.parse("the notice demands {amount:d} EUR with {interest} EUR interest"))
def notice_interest(world, amount, interest):
    assert world.response.status_code == 201, world.describe_response()
    notice = world.response.json()
    assert (Decimal(notice["principal_amount"]), notice["interest_amount"]) == (Decimal(amount), interest), notice
