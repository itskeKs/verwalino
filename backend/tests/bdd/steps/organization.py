"""Steps for features about what organizations see and may change.

Vocabulary: a *record* is a property, a person or a bank account (see
``records.py``); "the <record> of "<organization>"" is the one the Given step
created for that organization.
"""

from __future__ import annotations

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.records import (
    create_bank_account,
    create_person,
    create_property,
    kind_named,
    record_of,
)

# Statuses that count as "the API refused to touch a foreign record".
REFUSED = (404,)  # existence of another organization's records is never revealed (architecture.md)


@given(parsers.parse('the organizations "{first}" and "{second}"'))
def known_organizations(world, first, second):
    world.administrator_of(first)
    world.administrator_of(second)


@given("each organization has a property, a person and a bank account of its own")
def organizations_have_records(world):
    for organization in world.organizations:
        home = create_property(world, organization, f"{organization} property")
        create_person(world, organization, f"{organization} person")
        create_bank_account(world, organization, f"{organization} bank account", property_id=home.id)


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
