"""Steps for the bank-statement-import feature."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.records import create_bank_account, create_property

STATEMENTS = Path(__file__).resolve().parents[2] / "fixtures" / "bank"


def _iso(day: str) -> str:
    return datetime.strptime(day, "%d.%m.%Y").date().isoformat()


def _upload(world, *, dry_run):
    filename, content = world.data["statement"]
    return world.act(
        "POST",
        f"/api/bank-accounts/{world.data['account_id']}/imports",
        params={"dry_run": str(dry_run).lower()},
        files={"file": (filename, content, "application/octet-stream")},
    )


def _import_result(world):
    assert world.response is not None and world.response.status_code == 200, world.describe_response()
    return world.response.json()


def _stored_transactions(world):
    answer = world.look(world.actor, f"/api/bank-accounts/{world.data['account_id']}/transactions")
    assert answer.status_code == 200, f"Listing the transactions answered {answer.status_code}: {answer.text[:300]}"
    return answer.json()["items"]


@given(parsers.parse('a bank account with the IBAN "{iban}"'))
def bank_account_with_iban(world, iban):
    organization = world.actor_organization
    home = create_property(world, organization, f"{organization} statement property")
    account = create_bank_account(world, organization, "Statement account", property_id=home.id, iban=iban)
    world.data["account_id"] = account.id


@given(parsers.parse('the DKB statement "{filename}"'))
def dkb_statement(world, filename):
    world.data["statement"] = (filename, (STATEMENTS / filename).read_bytes())


@given("the statement has been imported")
def statement_has_been_imported(world):
    _upload(world, dry_run=False)
    assert _import_result(world)["imported_count"] > 0, "Setup failed, nothing was imported: " + world.describe_response()


@when("the user previews the statement")
def user_previews(world):
    _upload(world, dry_run=True)


@when("the user imports the statement")
def user_imports(world):
    _upload(world, dry_run=False)


@then(parsers.parse("the preview shows {count:d} new transactions"))
def preview_shows_new(world, count):
    result = _import_result(world)
    assert (result["count"], result["new_count"], result["duplicate_count"]) == (count, count, 0), (
        f"Expected {count} new transactions; got count={result['count']}, new={result['new_count']}, "
        f"already imported={result['duplicate_count']}."
    )


@then(parsers.parse("the preview totals {total} EUR for the period from {start} to {end}"))
def preview_totals(world, total, start, end):
    result = _import_result(world)
    assert Decimal(result["total_amount"]) == Decimal(total), f"Total is {result['total_amount']}, expected {total}."
    assert (result["date_from"], result["date_to"]) == (_iso(start), _iso(end)), (
        f"Period is {result['date_from']} to {result['date_to']}, expected {_iso(start)} to {_iso(end)}."
    )


@then(parsers.parse("{count:d} transactions are imported"))
def transactions_imported(world, count):
    assert _import_result(world)["imported_count"] == count, (
        f"Expected {count} imported transactions; " + world.describe_response()
    )


@then("no transaction is imported")
def nothing_imported(world):
    transactions_imported(world, 0)


@then(parsers.parse("{count:d} transactions are recognised as already imported"))
def recognised_as_duplicates(world, count):
    assert _import_result(world)["duplicate_count"] == count, (
        f"Expected {count} duplicates; " + world.describe_response()
    )


@then("the bank account holds no transactions")
def account_is_empty(world):
    account_holds(world, 0)


@then(parsers.parse("the bank account holds {count:d} transactions"))
def account_holds(world, count):
    stored = _stored_transactions(world)
    assert len(stored) == count, f"The bank account holds {len(stored)} transactions, expected {count}."
