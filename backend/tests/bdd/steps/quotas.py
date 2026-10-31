"""Exercise quota boundaries in tenants provisioned by the scenarios themselves."""
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from pytest_bdd import given, when, then

from tests.bdd.world import target_url
from tests.conftest import _api_test_client
from tests.bdd.steps.records import create_property


@pytest.fixture
def quota_organizations(world, mailpit, monkeypatch):
    from app.core.config import settings
    from tests.bdd.steps.signup import emailed_token
    from tests.signup_support import BODY

    shared = {name: world.administrator_of(name) for name in ("Verwaltung Alpha", "Verwaltung Beta")}

    def shared_usage():
        usage = {}
        for name, client in shared.items():
            response = world.look(client, "/api/organizations/quota")
            assert response.status_code == 200, response.text
            usage[name] = response.json()["usage"]
        return usage

    before = shared_usage()
    clients = []
    monkeypatch.setattr(settings, "public_host", "signup.example.test")
    world.data["mailpit"] = mailpit

    def sign_up(organization):
        client = httpx.AsyncClient(base_url=target_url(), timeout=60) if target_url() else _api_test_client()
        clients.append(client)
        body = BODY | {"email": f"quota-{uuid4().hex}@example.test", "organization_name": organization}
        assert world.send(client, "POST", "/api/signup", json=body).status_code == 202, world.describe_response()
        token = emailed_token(world, body["email"])
        answer = world.send(client, "POST", "/api/signup/confirm", json={"token": token, "password": body["password"]})
        assert answer.status_code == 201, world.describe_response()
        world.organizations[organization] = client
        return organization

    try:
        yield sign_up
    finally:
        # No shared records need restoring, even if setup or an assertion fails.
        try:
            after = shared_usage()
            assert after == before, f"Quota scenario changed shared usage: {before} -> {after}"
            print(f"Shared quota usage before/after: {before} -> {after}")
        finally:
            for client in clients:
                world.run(client.aclose())


@given("another hosted organization has filled its property allowance")
def filled_other_properties(world, quota_organizations):
    from app.core.quotas import FREE_PLAN
    organization = quota_organizations("Erschöpfte Kontingentverwaltung")
    for number in range(FREE_PLAN.properties):
        create_property(world, organization, f"Vorheriges Objekt {number + 1}")
    response = world.look(world.administrator_of(organization), "/api/organizations/quota")
    assert response.json()["usage"]["properties"] == FREE_PLAN.properties


@given("a hosted organization has filled its storage allowance")
def filled_storage(world, quota_organizations, monkeypatch, tmp_path):
    from app.core import quotas, storage
    # Scale the allowance, preserving real writes and the exact-limit boundary.
    monkeypatch.setattr(quotas, "FREE_PLAN", replace(quotas.FREE_PLAN, storage_bytes=8))
    monkeypatch.setattr(storage, "_store", storage.LocalFsDocumentStore(tmp_path))
    organization = quota_organizations("Kontingentverwaltung")
    world.actor = world.administrator_of(organization)
    prop = create_property(world, organization, "Kontingentobjekt")
    world.data["quota_property"] = prop.id
    response = world.act("POST", "/api/attachments", data={"property_id": prop.id},
                         files={"file": ("a.pdf", b"%PDF-123", "application/pdf")})
    assert response.status_code == 201, world.describe_response()


@when("a member uploads another document")
def another_document(world):
    world.act("POST", "/api/attachments", data={"property_id": world.data["quota_property"]},
              files={"file": ("b.pdf", b"%PDF-456", "application/pdf")})


@then("the upload is refused with the German storage quota message")
def refused_document(world):
    assert world.response.status_code == 413, world.describe_response()
    assert world.response.json()["detail"]["message"].startswith("Speicherkontingent erschöpft")


@when("the administrator adds a property and reads the organization quota")
def change_only_own_usage(world):
    organizations = world.data["quota_organizations"]
    clients = [world.administrator_of(name) for name in organizations]
    before = [world.send(client, "GET", "/api/organizations/quota").json()["usage"]["properties"] for client in clients]
    create_property(world, organizations[0], "Zusätzliches Kontingentobjekt")
    after = [world.send(client, "GET", "/api/organizations/quota").json()["usage"]["properties"] for client in clients]
    world.data["quota_property_counts"] = before, after


@given("two newly signed-up hosted organizations")
def independent_quota_organizations(world, quota_organizations):
    world.data["quota_organizations"] = [quota_organizations(name) for name in (
        "Eigene Kontingentverwaltung", "Andere Kontingentverwaltung",
    )]
    world.actor_organization = world.data["quota_organizations"][0]
    world.actor = world.administrator_of(world.actor_organization)


@then("only that organization's property usage has increased")
def own_usage_only(world):
    before, after = world.data["quota_property_counts"]
    assert after == [before[0] + 1, before[1]]
