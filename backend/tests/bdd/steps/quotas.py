"""Fill a representative scaled allowance through the document API."""
from dataclasses import replace
from pytest_bdd import given, when, then
from tests.bdd.steps.records import create_property


@given("a hosted organization has filled its storage allowance")
def filled_storage(world, monkeypatch, tmp_path):
    from app.core import quotas, storage
    # Scale the allowance, preserving real writes and the exact-limit boundary.
    monkeypatch.setattr(quotas, "FREE_PLAN", replace(quotas.FREE_PLAN, storage_bytes=8))
    monkeypatch.setattr(storage, "_store", storage.LocalFsDocumentStore(tmp_path))
    organization = "Verwaltung Alpha"
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
    clients = [world.administrator_of(name) for name in ("Verwaltung Alpha", "Verwaltung Beta")]
    before = [world.send(client, "GET", "/api/organizations/quota").json()["usage"]["properties"] for client in clients]
    create_property(world, "Verwaltung Alpha", "Zusätzliches Kontingentobjekt")
    after = [world.send(client, "GET", "/api/organizations/quota").json()["usage"]["properties"] for client in clients]
    world.data["quota_property_counts"] = before, after


@then("only that organization's property usage has increased")
def own_usage_only(world):
    before, after = world.data["quota_property_counts"]
    assert after == [before[0] + 1, before[1]]
