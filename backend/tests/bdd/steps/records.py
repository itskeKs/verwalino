"""Master-data records created through the HTTP API, shared by several features.

Not a step module: it only holds the vocabulary (which word means which API
resource) and the helpers that create records the way a user would.
"""

from __future__ import annotations

from dataclasses import dataclass

from tests.bdd.world import World


@dataclass(frozen=True)
class Kind:
    singular: str      # the word used in feature files ("bank account")
    plural: str        # ... and its plural ("bank accounts")
    path: str          # collection route; a record lives at f"{path}/{id}"
    name_field: str    # the JSON field that carries the record's visible name
    change_method: str # HTTP verb the API uses for editing


PROPERTY = Kind("property", "properties", "/api/properties", "name", "PUT")
PERSON = Kind("person", "persons", "/api/persons", "last_name", "PUT")
BANK_ACCOUNT = Kind("bank account", "bank accounts", "/api/bank-accounts", "label", "PATCH")

KINDS = (PROPERTY, PERSON, BANK_ACCOUNT)


def kind_named(word: str) -> Kind:
    """Resolve "property" or "properties" (as written in a feature) to its Kind."""
    for kind in KINDS:
        if word in (kind.singular, kind.plural):
            return kind
    known = ", ".join(f'"{kind.singular}"' for kind in KINDS)
    raise AssertionError(f'Unknown kind of record "{word}"; the step library knows: {known}')


@dataclass(frozen=True)
class Record:
    kind: Kind
    organization: str
    id: str
    name: str


def _create(world: World, client, kind: Kind, payload: dict, organization: str) -> Record:
    answer = world.send(client, "POST", kind.path, json=payload)
    assert answer.status_code == 201, f"Could not set up a {kind.singular}: {world.describe_response()}"
    return Record(kind, organization, answer.json()["id"], payload[kind.name_field])


def create_property(world: World, organization: str, name: str) -> Record:
    client = world.administrator_of(organization)
    record = _create(world, client, PROPERTY, {"name": name, "management_type": "WEG"}, organization)
    world.records[(organization, PROPERTY.singular)] = record
    return record


def create_person(world: World, organization: str, name: str) -> Record:
    client = world.administrator_of(organization)
    record = _create(world, client, PERSON, {"last_name": name}, organization)
    world.records[(organization, PERSON.singular)] = record
    return record


def create_bank_account(
    world: World, organization: str, label: str, *, property_id: str, iban: str | None = None
) -> Record:
    client = world.administrator_of(organization)
    payload = {"property_id": property_id, "account_type": "LAUFEND", "label": label}
    if iban is not None:
        payload["iban"] = iban
    record = _create(world, client, BANK_ACCOUNT, payload, organization)
    world.records[(organization, BANK_ACCOUNT.singular)] = record
    return record


def record_of(world: World, organization: str, kind: Kind) -> Record:
    try:
        return world.records[(organization, kind.singular)]
    except KeyError:
        raise AssertionError(
            f'"{organization}" has no {kind.singular} in this scenario; add a Given step that creates one.'
        ) from None
