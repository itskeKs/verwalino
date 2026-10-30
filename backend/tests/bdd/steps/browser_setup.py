"""Shared synthetic browser prerequisites, created only through the API World."""
import hashlib
from uuid import uuid4

from pytest_bdd import given

from tests.bdd.steps.records import create_bank_account, create_person


def create(world, path, payload, *, keyed=False):
    headers = {"Idempotency-Key": str(uuid4())} if keyed else {}
    response = world.act("POST", path, json=payload, headers=headers)
    assert response.status_code == 201, world.describe_response()
    return response.json()


def rental(world, seed_data, *, count=1, advance="150.00", deposit=None):
    world.actor = world.administrator_of("Verwaltung Alpha")
    world.actor_organization = "Verwaltung Alpha"
    user_id = str(seed_data["user_a_admin_id"])
    # Long but stable names exercise real option labels and table rows.
    suffix = hashlib.sha256(world.data["scenario"].encode()).hexdigest()[:8]
    property_name = f"Mietobjekt am langen Sonnenhof mit Vorderhaus und Seitenflügel {suffix}"
    home = create(world, "/api/properties", {
        "name": property_name, "management_type": "MIET", "street": "Sonnenweg 5", "zip_code": "12345", "city": "Teststadt",
        "data_audience": {"label": property_name, "reason": "Synthetic browser acceptance", "grants": [{"user_id": user_id, "capabilities": ["read", "prepare", "approve", "export"], "reason": "Synthetic browser acceptance"}]},
    }, keyed=True)
    world.data.update(property=home, tenants=[], leases=[], units=[], people=[])
    for index in range(count):
        unit = create(world, "/api/units", {"property_id": home["id"], "number": f"Wohnung {index + 1}", "usage_type": "WOHNUNG", "area": "70"})
        person = create_person(world, "Verwaltung Alpha", f"{('Ada', 'Berta', 'Clara')[index]} Synthetisch {suffix}")
        domain = create(world, "/api/person-data-domains", {"person_id": person.id, "reason_code": "INITIAL_SETUP"}, keyed=True)
        create(world, f"/api/person-data-domains/{domain['id']}/grants", {
            "user_id": user_id, "capabilities": ["read", "prepare", "export"], "reason_code": "TEAM_ACCESS",
        }, keyed=True)
        tenant = create(world, "/api/tenant-profiles", {"person_id": person.id})
        lease = create(world, "/api/leases", {
            "unit_id": unit["id"], "tenant_profile_id": tenant["id"], "valid_from": "2026-01-01",
            "base_rent": "700.00", "advance_payment": advance, "deposit": deposit,
        })
        world.data["units"].append(unit)
        world.data["people"].append(person)
        world.data["tenants"].append(tenant)
        world.data["leases"].append(lease)
    scope = create_scope(world, user_id, "Mietkreis Sonnenhof")
    world.data.update(scope=scope, user_id=user_id)
    return home


def create_scope(world, user_id, label):
    scope = create(world, "/api/accounting-scopes", {
        "property_id": world.data["property"]["id"], "scope_type": "MIET", "label": label,
        "valid_from": "2026-01-01", "valid_to": "2026-12-31", "whole_property": True,
    }, keyed=True)
    create(world, f"/api/accounting-scopes/{scope['id']}/grants", {
        "user_id": user_id, "capabilities": ["read", "prepare", "approve", "confirm", "export"], "reason": "Synthetic browser acceptance",
    }, keyed=True)
    return scope


def bank_account(world, *, deposit=False):
    account = create(world, "/api/bank-accounts", {
        "property_id": world.data["property"]["id"], "accounting_scope_id": world.data["scope"]["id"],
        "account_type": "KAUTION" if deposit else "LAUFEND", "label": "Kautionskonto" if deposit else "Mietkonto Sonnenhof",
        "iban": "DE02120300000000202051",
    })
    world.data["account"] = account
    return account


@given("a rental bank account ready for a statement")
def statement_account(world, seed_data):
    rental(world, seed_data)
    bank_account(world)
