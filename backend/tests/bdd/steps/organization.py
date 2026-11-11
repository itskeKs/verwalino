"""Steps for features about what organizations see and may change.

Vocabulary: a *record* is a property, a person, a bank account or a unit (see
``records.py``); "the <record> of "<organization>"" is the one the Given step
created for that organization.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.records import (
    create_bank_account,
    create_person,
    create_property,
    create_unit,
    kind_named,
    record_of,
)

# Statuses that count as "the API refused to touch a foreign record".
REFUSED = (404,)  # existence of another organization's records is never revealed (architecture.md)


@given('an empty asset report belongs to Verwaltung Alpha')
def empty_annual_draft(world, seed_data):
    from tests.test_m4_weg import _seed_weg
    from tests.test_weg_draft_deletion import asset_draft
    alpha = world.administrator_of('Verwaltung Alpha')
    world.data['empty_annual_draft'] = world.run(asset_draft(alpha, _seed_weg(seed_data)))


@when('Verwaltung Beta tries to delete the empty annual draft')
def foreign_annual_delete(world):
    run = world.data['empty_annual_draft']
    beta = world.administrator_of('Verwaltung Beta')
    world.data['annual_delete_refusals'] = [world.send(beta, 'DELETE', path) for path in (
        f'/api/weg-settlements/{run}', f'/api/weg-settlements/asset-reports/{run}')]


@then('the annual draft is not found and remains available to Verwaltung Alpha')
def annual_draft_retained(world):
    assert all(response.status_code == 404 for response in world.data['annual_delete_refusals'])
    run = world.data['empty_annual_draft']
    assert world.look(world.administrator_of('Verwaltung Alpha'), f'/api/weg-settlements/{run}').status_code == 200


@given(parsers.parse('the organizations "{first}" and "{second}"'))
def known_organizations(world, first, second):
    world.administrator_of(first)
    world.administrator_of(second)


@pytest.fixture(scope="module")
def organization_records():
    # Isolation scenarios read records or attempt refused foreign mutations.
    # One set per feature leaves quota headroom for later hosted scenarios.
    return {}


@given("each organization has a property, a person and a bank account of its own")
def organizations_have_records(world, organization_records):
    if not organization_records:
        for organization in world.organizations:
            home = create_property(world, organization, f"{organization} property")
            create_person(world, organization, f"{organization} person")
            create_bank_account(world, organization, f"{organization} bank account", property_id=home.id)
        organization_records.update(world.records)
    world.records.update(organization_records)


@given(parsers.parse('"{organization}" has a unit named "{name}"'))
def organization_has_unit(world, organization, name):
    parent = record_of(world, organization, kind_named("property"))
    create_unit(world, organization, name, property_id=parent.id)


@when(parsers.parse("the user lists the {kinds}"))
def user_lists(world, kinds):
    kind = kind_named(kinds)
    answer = world.act("GET", kind.path)
    assert answer.status_code == 200, world.describe_response()
    world.data["listed_ids"] = {item["id"] for item in answer.json()}


@when(parsers.parse('the user opens the {kind} of "{organization}" by its id'))
def user_opens(world, kind, organization):
    record = record_of(world, organization, kind_named(kind))
    world.act("GET", f"{record.kind.path}/{record.id}")


@when(parsers.parse('the user tries to rename the {kind} of "{organization}" to "{new_name}"'))
def user_renames(world, kind, organization, new_name):
    record = record_of(world, organization, kind_named(kind))
    world.act(record.kind.change_method, f"{record.kind.path}/{record.id}", json={record.kind.name_field: new_name})


@when(parsers.parse('the user submits an invalid area for that unit and for a new unit in "{organization}"'))
def invalid_foreign_unit_area(world, organization):
    unit = record_of(world, organization, kind_named("unit"))
    home = record_of(world, organization, kind_named("property"))
    world.data["foreign_area_attempts"] = [
        world.act("PUT", f"/api/units/{unit.id}", json={"area": "abc"}),
        world.act("POST", "/api/units", json={
            "property_id": home.id, "number": "New unit", "usage_type": "WOHNUNG", "area": "abc",
        }),
    ]


@then("neither attempt reveals the foreign property or unit")
def foreign_area_refused(world):
    for response in world.data["foreign_area_attempts"]:
        assert response.status_code == 404, response.text


@when(parsers.parse('the user tries to add or import another "{number}" into the property of "{organization}"'))
def user_adds_foreign_unit(world, number, organization):
    from tests.test_master_import import body

    property_id = record_of(world, organization, kind_named("property")).id
    unit = {"property_id": property_id, "number": number, "usage_type": "WOHNUNG"}
    value = body([{
        "kind": "unit", "key": "U1", "property_key": property_id, "number": number, "usage_type": "WOHNUNG",
    }])
    world.data["foreign_unit_creates"] = [
        world.act("POST", "/api/units", json=unit),
        world.act("POST", "/api/properties/import/apply", json={
            **value, "expected_digest": "0" * 64, "request_id": str(uuid4()),
        }),
    ]


@then("the property is reported as not found for both attempts")
def foreign_unit_creates_refused(world):
    for response in world.data["foreign_unit_creates"]:
        assert response.status_code == 404, response.text


@then(parsers.parse('the list shows the {kinds} of "{organization}"'))
def list_shows(world, kinds, organization):
    record = record_of(world, organization, kind_named(kinds))
    assert record.id in world.data["listed_ids"], (
        f'The {record.kind.singular} "{record.name}" of "{organization}" is missing from the list '
        f"({len(world.data['listed_ids'])} entries)."
    )


@then(parsers.parse('the list does not show the {kinds} of "{organization}"'))
def list_hides(world, kinds, organization):
    record = record_of(world, organization, kind_named(kinds))
    assert record.id not in world.data["listed_ids"], (
        f'The {record.kind.singular} "{record.name}" of "{organization}" leaked into the list '
        f'of "{world.actor_organization}".'
    )


@then(parsers.parse("the {kind} is reported as not found"))
def reported_not_found(world, kind):
    assert world.response is not None and world.response.status_code == 404, world.describe_response()


@then("the change is refused")
def change_refused(world):
    assert world.response is not None and world.response.status_code in REFUSED, (
        f"Expected the change to be refused ({'/'.join(map(str, REFUSED))}); " + world.describe_response()
    )


@then(parsers.parse('"{organization}" still has its {kind} unchanged'))
def record_unchanged(world, organization, kind):
    record = record_of(world, organization, kind_named(kind))
    owner = world.administrator_of(organization)
    answer = world.look(owner, f"{record.kind.path}/{record.id}")
    assert answer.status_code == 200, (
        f'The {record.kind.singular} of "{organization}" can no longer be read by its owner '
        f"(answered {answer.status_code})."
    )
    assert answer.json()[record.kind.name_field] == record.name, (
        f'The {record.kind.singular} of "{organization}" was changed: '
        f'expected "{record.name}", found "{answer.json()[record.kind.name_field]}".'
    )


@when(parsers.parse('the user opens the settlement period offers of "{organization}"'))
def user_opens_periods(world, organization):
    record = record_of(world, organization, kind_named("property"))
    world.act("GET", f"/api/properties/{record.id}/settlement-periods", params={"year": 2025})


@when(parsers.parse('the administrator of "{organization}" tries to create one user with an own-organization address and one with Alpha\'s administrator address'))
def reuse_signin_addresses(world, organization):
    def owner_address(administrator):
        staff = world.send(administrator, "GET", "/api/users")
        assert staff.status_code == 200, world.describe_response()
        return next(user["email"] for user in staff.json() if user["role"] == "OWNER_ADMIN")

    beta = world.administrator_of(organization)
    world.data["alpha_signin_address"] = owner_address(world.administrator_of("Verwaltung Alpha"))
    world.data["reused_receipts"] = []
    for address in (owner_address(beta), world.data["alpha_signin_address"]):
        answer = world.send(beta, "POST", "/api/users", json={
            "email": address, "password": "reused-address-password",
            "display_name": "Reused address", "role": "CLERK",
        })
        world.data["reused_receipts"].append((answer.status_code, answer.json()))


@then("both attempts are refused with the same generic receipt")
def reused_addresses_refused_generically(world):
    own, foreign = world.data["reused_receipts"]
    assert own[0] == 409, own
    assert foreign == own, (
        "The cross-organization attempt must be indistinguishable from an "
        f"own-organization duplicate: {foreign} != {own}"
    )


@then("Alpha's administrator can still sign in")
def alpha_administrator_still_signs_in(world):
    answer = world.send(world.anonymous, "POST", "/api/auth/login", json={
        "email": world.data["alpha_signin_address"], "password": "testpass1234",
    })
    assert answer.status_code == 200, world.describe_response()


@when("the administrator signs in again")
def administrator_signs_in_again(world):
    session = world.act("GET", "/api/auth/session")
    assert session.status_code == 200, world.describe_response()
    users = world.act("GET", "/api/users")
    assert users.status_code == 200, world.describe_response()
    email = next(user["email"] for user in users.json() if user["id"] == session.json()["user_id"])
    answer = world.send(world.anonymous, "POST", "/api/auth/login", json={"email": email, "password": "testpass1234"})
    assert answer.status_code == 200, world.describe_response()
    world.actor = world.anonymous


@then(parsers.parse('the signed-in session belongs to "{organization}"'))
def signed_in_session_belongs_to(world, organization):
    answer = world.act("GET", "/api/organizations/me")
    assert answer.status_code == 200, world.describe_response()
    assert answer.json()["name"] == organization


@given('an audited draft reserve contribution correction belongs to Verwaltung Alpha')
def reserve_correction_for_isolation(world, seed_data):
    from tests.test_reserve_ledger import draft_unit_correction
    alpha = world.administrator_of('Verwaltung Alpha')
    w, path = world.run(draft_unit_correction(alpha, seed_data))
    reduced = world.send(alpha, 'POST', path + '/reduce', json={'reduced_to': '100.00', 'reason': 'Reviewed reduction'})
    assert reduced.status_code == 201, reduced.text
    world.data['reserve_correction'] = (w, path)
    world.data['reserve_ledger_before'] = world.look(alpha, path.rsplit('/overrides/', 1)[0]).json()


@when('Verwaltung Beta tries to reduce and delete that contribution correction')
def foreign_reserve_correction(world):
    _, path = world.data['reserve_correction']
    beta = world.administrator_of('Verwaltung Beta')
    world.data['reserve_refusals'] = [
        world.send(beta, 'POST', path + '/reduce', json={'reduced_to': '300.00', 'reason': 'Foreign restoration'}),
        world.send(beta, 'POST', path + '/delete', json={'reason': 'Foreign deletion'})]


@then('the reserve contribution correction is not found and remains unchanged')
def reserve_correction_unchanged(world):
    _, path = world.data['reserve_correction']
    assert all(response.status_code == 404 for response in world.data['reserve_refusals'])
    assert world.look(world.administrator_of('Verwaltung Alpha'), path.rsplit('/overrides/', 1)[0]).json() == world.data['reserve_ledger_before']


@given("a unit with saved household history belongs to Verwaltung Alpha")
def household_alpha(world):
    from tests.settlement_period_support import create
    from tests.persons_key_support import ANNA_HOUSEHOLD
    parent = record_of(world, 'Verwaltung Alpha', kind_named('property'))
    api = world.administrator_of('Verwaltung Alpha')
    unit = world.run(create(api, '/api/units', {'property_id': parent.id, 'number': 'Household isolation', 'usage_type': 'WOHNUNG'}))
    response = world.send(api, 'PUT', f"/api/units/{unit['id']}/household", json=ANNA_HOUSEHOLD)
    assert response.status_code == 200, response.text
    world.data['household_unit'] = unit['id']


@when("Verwaltung Beta tries to read or replace that unit's households")
def household_foreign(world):
    beta = world.administrator_of('Verwaltung Beta')
    url = f"/api/units/{world.data['household_unit']}/household"
    world.data['household_refusals'] = [world.send(beta, 'GET', url), world.send(beta, 'PUT', url, json=[])]


@then("the households are not found and Verwaltung Alpha retains its rows")
def household_retained(world):
    from tests.persons_key_support import ANNA_HOUSEHOLD
    assert all(r.status_code == 404 for r in world.data['household_refusals'])
    response = world.look(world.administrator_of('Verwaltung Alpha'), f"/api/units/{world.data['household_unit']}/household")
    assert response.json() == ANNA_HOUSEHOLD
