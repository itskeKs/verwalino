"""Steps for the bank-statement-import feature."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.browser_setup import bank_account, create, rental
from tests.bdd.steps.records import create_bank_account, create_property

STATEMENTS = Path(__file__).resolve().parents[2] / "fixtures" / "bank"


@given('each organization has a confirmed cost and an outgoing bank debit')
def isolated_cost_debits(world):
    rows = {}
    for organization in world.organizations:
        client = world.administrator_of(organization)
        # The archive scenarios clear earlier synthetic records on teardown.
        home = create_property(world, organization, f'{organization} debit isolation property')
        account = create_bank_account(world, organization, 'Debit isolation account', property_id=home.id)
        category = world.send(client, 'GET', '/api/cost-categories').json()[0]
        answer = world.send(client, 'POST', '/api/cost-entries', json={
            'property_id': home.id, 'cost_category_id': category['id'],
            'cost_category_plan_version': category['plan_version'], 'gross_amount': '60.00',
            'invoice_date': '2026-01-15', 'service_period_from': '2026-01-01',
            'service_period_to': '2026-12-31', 'tenant_apportionable': 'UMLAGEFAEHIG',
        })
        assert answer.status_code == 201, world.describe_response()
        cost = answer.json()['id']
        assert world.send(client, 'POST', f'/api/cost-entries/{cost}/confirm').status_code == 200
        content = b'Buchungstag;Wertstellung;Betrag;Name;Verwendungszweck\n15.01.2026;15.01.2026;-60,00;Stadtwerke;Isolated electricity\n'
        answer = world.send(client, 'POST', f'/api/bank-accounts/{account.id}/imports', params={'dry_run': 'false'},
                            files={'file': ('isolated.csv', content, 'text/csv')})
        assert answer.status_code == 200, world.describe_response()
        answer = world.send(client, 'GET', '/api/bank-transactions', params={'bank_account_id': account.id})
        debit = next(row for row in answer.json()['items'] if row['purpose'] == 'Isolated electricity')
        rows[organization] = {'cost': cost, 'debit': debit['id'], 'account': account.id}
    world.data['isolated_cost_debits'] = rows


@when("Verwaltung Alpha tries to assign its debit to Verwaltung Beta's cost")
def foreign_cost_assignment(world):
    rows = world.data['isolated_cost_debits']
    world.act('POST', f"/api/bank-transactions/{rows['Verwaltung Alpha']['debit']}/assign",
              json={'cost_entry_id': rows['Verwaltung Beta']['cost']})


@then('the foreign cost is not found and both debits remain new')
def foreign_cost_untouched(world):
    assert world.response.status_code == 404, world.describe_response()
    for organization, records in world.data['isolated_cost_debits'].items():
        client = world.administrator_of(organization)
        answer = world.look(client, f"/api/cost-entries/{records['cost']}")
        assert Decimal(answer.json()['bank_open_amount']) == 60
        answer = world.run(client.get('/api/bank-transactions', params={'bank_account_id': records['account']}))
        row = next(row for row in answer.json()['items'] if row['id'] == records['debit'])
        assert row['status'] == 'NEU' and row['cost_entry_id'] is None


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


@given(parsers.parse('the Sparkasse statement "{filename}"'))
def sparkasse_statement(world, filename):
    world.data["statement"] = (filename, (STATEMENTS / filename).read_bytes())


def _anna_receipt(world):
    rows = _stored_transactions(world)
    matches = [row for row in rows if row["booking_date"] == "2026-04-07" and Decimal(row["amount"]) == Decimal("800.00")]
    assert len(matches) == 1, matches
    row = matches[0]
    assert row["counterparty_name"] == "Anna Beispiel"
    assert row["counterparty_iban"] == "DE89370400440532013000"
    return row


@then("Anna Beispiel's 800 EUR receipt on 07.04.2026 retains her name and IBAN")
def anna_receipt(world):
    _anna_receipt(world)


@given("a rental receipt and an owner for the rented unit")
def rental_receipt_with_owner(world, seed_data):
    rental(world, seed_data)
    account = bank_account(world)
    content = (b'Buchungstag;Wertstellung;Betrag;Name;Verwendungszweck\n'
               b'03.01.2026;03.01.2026;700,00;Anna Beispiel;Kaltmiete Januar 2026\n')
    response = world.act('POST', f"/api/bank-accounts/{account['id']}/imports",
                         params={'dry_run': 'false'}, files={'file': ('rent.csv', content, 'text/csv')})
    assert response.status_code == 200, world.describe_response()
    person = create(world, '/api/persons', {'first_name': 'Frida', 'last_name': 'Sonnenhof'})
    domain = create(world, '/api/person-data-domains', {'person_id': person['id'], 'reason_code': 'INITIAL_SETUP'}, keyed=True)
    create(world, f"/api/person-data-domains/{domain['id']}/grants", {
        'user_id': world.data['user_id'], 'capabilities': ['read', 'prepare', 'export'], 'reason_code': 'TEAM_ACCESS',
    }, keyed=True)
    owner = create(world, '/api/owner-profiles', {'person_id': person['id']})
    create(world, '/api/ownership', {
        'owner_profile_id': owner['id'], 'unit_id': world.data['units'][0]['id'], 'valid_from': '2026-01-01',
    })
    rows = _rental_receipts(world)
    world.data['rent_owner_id'] = owner['id']
    world.data['rent_receipt_id'] = next(row['id'] for row in rows if row['purpose'] == 'Kaltmiete Januar 2026')


def _rental_receipts(world):
    response = world.look(world.actor, f"/api/bank-accounts/{world.data['account']['id']}/transactions")
    assert response.status_code == 200, response.text
    return response.json()['items']


def _assign_base_rent(world, **target):
    world.act('POST', f"/api/bank-transactions/{world.data['rent_receipt_id']}/assign", json={'lines': [{
        'payment_type': 'KALTMIETE', 'amount': '700.00', 'period_year': 2026, 'period_month': 1, **target,
    }]})


@when("the manager submits the owner as the target for base rent")
def assign_rent_to_owner(world):
    _assign_base_rent(world, owner_profile_id=world.data['rent_owner_id'])


@then("the response explains that rent belongs to a tenancy and nothing is booked")
def rent_refusal_keeps_receipt_pending(world):
    assert world.response.status_code == 422, world.describe_response()
    assert world.response.json()['detail'] == {
        'code': 'PAYMENT_COUNTERPARTY_TYPE_MISMATCH',
        'message': 'Dieser Zahlungstyp gehört zu einem Mietverhältnis, nicht zu einem Eigentümer.',
    }
    receipt = next(row for row in _rental_receipts(world) if row['id'] == world.data['rent_receipt_id'])
    assert receipt['status'] == 'NEU'
    payments = world.look(world.actor, f"/api/payments?bank_account_id={world.data['account']['id']}")
    assert payments.status_code == 200 and payments.json() == [], payments.text


@when("the manager assigns the same rent to the tenancy")
def assign_rent_to_tenancy(world):
    _assign_base_rent(world, lease_agreement_id=world.data['leases'][0]['id'])


@then("the bank receipt is assigned and its exact amount is booked to the tenant")
def rent_booked_to_tenant(world):
    assert world.response.status_code == 200, world.describe_response()
    result = world.response.json()
    assert result['status'] == 'ZUGEORDNET'
    payment, = result['payments']
    assert payment['status'] == 'booked'
    assert payment['tenant_profile_id'] == world.data['tenants'][0]['id']
    assert Decimal(payment['amount']) == Decimal('700.00')
