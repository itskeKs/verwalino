"""Exercise the existing guarded activation and real source/finance reads."""
from uuid import uuid4

import pytest
from pytest_bdd import given, then, when

from tests.bdd.steps.browser_setup import create
from tests.conftest import _api_test_client
from tests.declaration_fixtures import make_text_pdf
from tests.test_source_security_lifecycle import accepted, status, transition, transition_body


def grant(world, path, user_id, capabilities):
    return create(world, path + "/grants", {
        "user_id": user_id, "capabilities": capabilities, "reason": "Explicit reviewed source access",
    }, keyed=True)


@pytest.fixture
def source_staff_client(world):
    client = _api_test_client()
    try:
        yield client
    finally:
        world.run(client.aclose())


@given("a property with a reviewed original and a financial account")
def reviewed_property(world, source_staff_client):
    admin = world.actor
    user_id = world.look(admin, "/api/auth/session").json()["user_id"]
    home = create(world, "/api/properties", {
        "name": "Haus am langen Sonnenhof mit Vorderhaus", "management_type": "MIET",
    })
    domain = create(world, "/api/property-data-domains", {
        "property_id": home["id"], "label": "Gebäudedaten Sonnenhof", "reason": "Explicit reviewed audience",
    }, keyed=True)
    scope = create(world, "/api/accounting-scopes", {
        "property_id": home["id"], "scope_type": "MIET", "label": "Mietkreis Sonnenhof",
        "valid_from": "2026-01-01", "valid_to": "2026-12-31", "whole_property": True,
    }, keyed=True)
    grant(world, f"/api/accounting-scopes/{scope['id']}", user_id, ["read", "prepare", "export"])
    account = create(world, "/api/bank-accounts", {
        "property_id": home["id"], "accounting_scope_id": scope["id"], "account_type": "LAUFEND", "label": "Mietkonto Sonnenhof",
    })
    draft = create(world, "/api/declaration-imports", {"mode": "EXISTING_PROPERTY", "target_property_id": home["id"]})
    pdf = make_text_pdf("Synthetic original for the reviewed source access journey")
    uploaded = world.act("POST", f"/api/declaration-imports/{draft['id']}/documents",
                         files={"file": ("sonnenhof.pdf", pdf, "application/pdf")},
                         data={"document_role": "DECLARATION", "sequence_no": "1"})
    assert uploaded.status_code == 201, world.describe_response()
    document = uploaded.json()
    # Preparation permits a legacy attachment without current destination authority.
    # Granting it afterwards requires an explicit admission review, not inferred proof.
    grant(world, f"/api/property-data-domains/{domain['id']}", user_id, ["read", "prepare", "approve", "export"])
    pending = world.look(admin, "/api/property-source-admission-reviews/pending/page").json()["items"]
    source = next(item for item in pending if item["document_id"] == document["id"])
    preview = world.look(admin, f"/api/property-source-admission-reviews/originals/{document['id']}/content")
    assert preview.status_code == 200 and preview.content == pdf
    create(world, "/api/property-source-admission-reviews", {
        "document_id": document["id"], "import_id": draft["id"], "draft_revision": source["draft_revision"],
        "content_sha256": source["content_sha256"], "reason": "Original and destination reviewed",
    }, keyed=True)
    staff = create(world, "/api/users", {
        "email": f"source-access-{uuid4()}@example.test", "password": "source-access-testpass",
        "display_name": "Sachbearbeitung Sonnenhof", "role": "CLERK",
    })
    login = world.send(source_staff_client, "POST", "/api/auth/login", json={"email": staff["email"], "password": "source-access-testpass"})
    assert login.status_code == 200, world.describe_response()
    world.data.update(source_admin=admin, source_staff=source_staff_client, source_staff_id=staff["id"],
                      source_property=home, source_domain=domain, source_scope=scope,
                      source_account=account, source_draft=draft, source_document=document, source_pdf=pdf)


@given("before activation the ungranted staff member can open the property and original, but not the scoped financial account")
def preparation_access_baseline(world):
    assert world.run(status(world.data["source_admin"]))["mode"] == "PREPARATION"
    open_protected_data(world)
    # Classified finance needs its exact scope grant in every mode; only the
    # property/original denial below depends on activating source protection.
    assert [answer.status_code for answer in world.data["source_answers"]] == [200, 200, 404]


@given("source access protection has been activated after review")
def activate_source_access(world):
    admin = world.data["source_admin"]
    decision = world.run(accepted(admin))
    response = world.run(transition(admin, transition_body(world.run(status(admin)), "ENFORCED", decision)))
    assert response.status_code == 201, response.text
    assert world.run(status(admin))["mode"] == "ENFORCED"


@when("a staff member without a grant opens the property's original and financial account")
@when("the staff member opens the property's original and financial account again")
def open_protected_data(world):
    data = world.data
    staff = data["source_staff"]
    paths = [f"/api/properties/{data['source_property']['id']}",
             f"/api/declaration-imports/{data['source_draft']['id']}/documents/{data['source_document']['id']}/content",
             f"/api/bank-accounts/{data['source_account']['id']}"]
    data["source_answers"] = [world.look(staff, path) for path in paths]


@then("the original and financial account are reported as not found")
def protected_data_not_found(world):
    assert [answer.status_code for answer in world.data["source_answers"]] == [404, 404, 404]


@when("the administrator grants that staff member access to the property's sources and financial data")
def grant_source_access(world):
    data = world.data
    world.actor = data["source_admin"]
    grant(world, f"/api/property-data-domains/{data['source_domain']['id']}", data["source_staff_id"], ["read", "export"])
    grant(world, f"/api/accounting-scopes/{data['source_scope']['id']}", data["source_staff_id"], ["read"])


@then("the original and financial account are visible")
def protected_data_visible(world):
    home, original, account = world.data["source_answers"]
    assert [home.status_code, original.status_code, account.status_code] == [200, 200, 200]
    assert home.json()["name"] == world.data["source_property"]["name"]
    assert original.content == world.data["source_pdf"]
    assert account.json()["label"] == world.data["source_account"]["label"]
