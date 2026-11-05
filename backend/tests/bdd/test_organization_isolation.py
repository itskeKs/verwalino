"""Binds features/organization_isolation.feature; the steps live in tests/bdd/steps/."""

from pathlib import Path

from pytest_bdd import given, when, then, scenarios

FEATURE = Path(__file__).resolve().parents[3] / "features" / "organization_isolation.feature"

scenarios(str(FEATURE))


@given("a payment, economic plan and annual WEG settlement belong to Verwaltung Alpha", target_fixture="financial_details")
def financial_details(world, seed_data):
    from tests.bdd.test_weg_settlement import create_balance_world
    from tests.bdd.steps.browser_setup import create

    records = create_balance_world(world, seed_data)
    world.data['isolated_owner_schedule'] = {'owner': records['owners'][0], 'plan': records['plan']}
    payment = create(world, "/api/payments", {"bank_account_id": records["bank"],
        "owner_profile_id": records["owners"][0], "direction": "IN", "amount": "600.00",
        "value_date": "2026-01-05", "payment_type": "HAUSGELD_VORSCHUSS"})
    return [
        (f"/api/payments/{payment['id']}", "/api/payments/", "Zahlung nicht gefunden."),
        (f"/api/economic-plans/{records['plan']}", "/api/economic-plans/", "Wirtschaftsplan nicht gefunden."),
        (f"/api/weg-settlements/{records['run']}", "/api/weg-settlements/", "WEG-Abrechnung nicht gefunden."),
        (f"/api/weg-settlements/{records['run']}/input-owners", "/api/weg-settlements/", "WEG-Abrechnung nicht gefunden."),
    ]


@when("Verwaltung Beta opens those financial details and their input-owner list", target_fixture="foreign_financial_responses")
def foreign_financial_responses(world, financial_details):
    foreign = world.administrator_of("Verwaltung Beta")
    original = world.administrator_of("Verwaltung Alpha")
    responses = []
    for path, prefix, message in financial_details:
        missing = prefix + "00000000-0000-0000-0000-000000000000"
        if path.endswith("/input-owners"):
            missing += "/input-owners"
        assert world.look(original, path).status_code == 200
        responses.append((world.look(foreign, path), world.look(foreign, missing), message))
    return responses


@then("the foreign financial records have the same German explanation as missing records")
def foreign_financial_explanations(foreign_financial_responses):
    for foreign, missing, message in foreign_financial_responses:
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json() == {"detail": message}


@when("Verwaltung Beta requests Alpha's owner ledger at a selected due-date cutoff", target_fixture='foreign_owner_schedule')
def foreign_owner_schedule(world, financial_details):
    data = world.data['isolated_owner_schedule']
    query = '?period_start=2026-07-01&period_end=2026-07-31&as_of=2026-07-02&economic_plan_id='
    path = f"/api/payments/mietkonto/owner/{data['owner']}" + query + data['plan']
    own = world.look(world.administrator_of('Verwaltung Alpha'), path)
    assert own.status_code == 200, own.text
    assert own.json()['open_items'][0]['due_date'] == '2026-07-01'
    foreign = world.administrator_of('Verwaltung Beta')
    missing = '/api/payments/mietkonto/owner/00000000-0000-0000-0000-000000000000' + query + '00000000-0000-0000-0000-000000000000'
    return world.look(foreign, path), world.look(foreign, missing)


@then('the foreign owner schedule looks missing and exposes no financial amounts')
def missing_owner_schedule(foreign_owner_schedule):
    foreign, missing = foreign_owner_schedule
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    assert set(foreign.json()) == {'detail'}
