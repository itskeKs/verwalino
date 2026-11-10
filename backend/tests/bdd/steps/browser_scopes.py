"""Scope creation, team grants and the scoped financial pause on the team-rights page."""
import hashlib
from uuid import uuid4

from playwright.async_api import expect
from pytest_bdd import given, then, when

from tests.bdd.steps.browser_common import (
    button,
    expect_reply_replay,
    field,
    lose_first_committed_reply,
    signed_in_client,
    wait_scope_usable,
)
from tests.bdd.steps.browser_setup import create

CLERK = ("clerk@alpha.de", "testpass1234")


def unique(world, prefix):
    return f"{prefix} {hashlib.sha256(world.data['scenario'].encode()).hexdigest()[:8]}"


def api_get(world, path):
    answer = world.look(world.actor, path)
    assert answer.status_code == 200, world.describe_response()
    return answer.json()


@given("a property without scopes exists")
def plain_property(world):
    home = create(world, "/api/properties", {
        "name": unique(world, "Scope browser acceptance"), "management_type": "MIET",
        "street": "Test 1", "zip_code": "12345", "city": "Test",
    })
    world.data.update(property=home, scope_label=unique(world, "Reviewed browser rental scope"))


@when("the manager creates the scope and the first committed reply is lost")
def create_scope(browser_world):
    async def run():
        page = browser_world.page
        home = browser_world.world.data["property"]
        await browser_world.visit("/berechtigungen/teamrechte")
        await field(page, "Objekte durchsuchen").fill(home["name"])
        await browser_world.select("Objekt", home["id"])
        await field(page, "Bezeichnung").fill(browser_world.world.data["scope_label"])
        await page.get_by_role("combobox", name="Verwaltungsart", exact=True).select_option("MIET")
        await field(page, "Gültig ab").fill("2025-01-01")
        state = {"count": 0}
        handler = lose_first_committed_reply(page, state, "/api/accounting-scopes", "Gespeichert, Antwort unterbrochen")
        await page.route("**/api/accounting-scopes", handler)
        await button(page, "Bereich verbindlich anlegen").click()
        scope = await expect_reply_replay(page, state, "/api/accounting-scopes", "Bereich verbindlich anlegen")
        assert scope["id"] == state["result"]["id"], "the retry must return the first committed scope"
        browser_world.world.data["scope"] = scope
    browser_world.run(run())


@then("exactly one scope exists for the property")
def one_scope(browser_world):
    world = browser_world.world
    items = api_get(world, f"/api/accounting-scopes/page?property_id={world.data['property']['id']}")["items"]
    assert len(items) == 1 and items[0]["label"] == world.data["scope_label"], items


@when('the manager grants the scope to the clerk with "Vorbereiten" and the first committed reply is lost')
def grant_scope(browser_world, seed_data):
    async def run():
        page = browser_world.page
        world = browser_world.world
        scope = world.data["scope"]
        await page.get_by_role("link", name=f"{scope['label']} – Teamrechte verwalten", exact=True).click()
        await expect(page.get_by_role("heading", name=f"{scope['label']} (MIET)", exact=True)).to_be_visible()
        await field(page, "Teammitglied suchen").fill(CLERK[0])
        await browser_world.select("Teammitglied", str(seed_data["user_a_clerk_id"]))
        await field(page, "Vorbereiten").check()
        await field(page, "Begründung").fill("Reviewed browser team assignment")
        state = {"count": 0}
        path = f"/api/accounting-scopes/{scope['id']}/grants"
        handler = lose_first_committed_reply(page, state, path, "Gespeichert, Antwort unterbrochen")
        await page.route(f"**{path}", handler)
        await button(page, "Freigabe erteilen").click()
        grant = await expect_reply_replay(page, state, path, "Freigabe erteilen")
        assert grant["id"] == state["result"]["id"], "the retry must return the first committed grant"
        await expect(page.get_by_role("cell", name="Clerk Alpha", exact=True)).to_be_visible()
    browser_world.run(run())


@then("the clerk can open the scope")
def clerk_opens_scope(browser_world):
    world = browser_world.world
    client = signed_in_client(world, *CLERK)
    try:
        answer = world.look(client, f"/api/accounting-scopes/{world.data['scope']['id']}")
        assert answer.status_code == 200, answer.text
    finally:
        world.run(client.aclose())


@when("the manager revokes the clerk's grant")
def revoke_grant(browser_world):
    async def run():
        page = browser_world.page
        await button(page, "Widerrufen").click()
        await expect(page.get_by_role("cell", name="Widerrufen", exact=True)).to_be_visible()
    browser_world.run(run())


@then("the clerk can no longer open the scope")
def clerk_blocked(browser_world):
    world = browser_world.world
    client = signed_in_client(world, *CLERK)
    try:
        answer = world.look(client, f"/api/accounting-scopes/{world.data['scope']['id']}")
        assert answer.status_code == 404, answer.text
    finally:
        world.run(client.aclose())


@then("the grant history shows the revocation")
def grant_history(browser_world):
    async def run():
        await expect(browser_world.page.get_by_role("cell", name="Widerrufen", exact=True)).to_be_visible()
    browser_world.run(run())


@given("a rental scope with a granted bank account exists")
def scoped_bank_account(world, seed_data):
    home = create(world, "/api/properties", {
        "name": unique(world, "Scoped control QA"), "management_type": "MIET",
        "street": "Test 1", "zip_code": "12345", "city": "Test",
    })
    scope = create(world, "/api/accounting-scopes", {
        "property_id": home["id"], "scope_type": "MIET", "label": unique(world, "Scoped control QA"),
        "valid_from": "2025-01-01", "whole_property": True,
    }, keyed=True)
    create(world, f"/api/accounting-scopes/{scope['id']}/grants", {
        "user_id": str(seed_data["user_a_admin_id"]), "capabilities": ["read", "prepare", "export"],
        "reason": "Synthetic control grant",
    }, keyed=True)
    wait_scope_usable(world, scope["id"])
    bank = create(world, "/api/bank-accounts", {
        "property_id": home["id"], "accounting_scope_id": scope["id"],
        "account_type": "LAUFEND", "label": unique(world, "Control QA bank"),
    })
    snapshot = world.look(world.actor, f"/api/bank-accounts/{bank['id']}")
    assert snapshot.status_code == 200, world.describe_response()
    world.data.update(property=home, scope=scope, bank=snapshot.json())


def resume_if_paused(world):
    """The pause covers the whole organization: never leave it behind for later scenarios."""
    state = world.look(world.actor, "/api/accounting-scopes/control").json()
    if state["mode"] == "paused":
        answer = world.act("POST", "/api/accounting-scopes/control", json={
            "mode": "compatibility", "expected_event_id": state["event"]["id"], "reason": "Scenario teardown",
        }, headers={"Idempotency-Key": str(uuid4())})
        assert answer.status_code == 201, world.describe_response()


@when("the manager pauses the scope-bound financial actions and the first committed reply is lost")
def pause_scopes(browser_world, request):
    request.addfinalizer(lambda: resume_if_paused(browser_world.world))

    async def run():
        page = browser_world.page
        await browser_world.visit(f"/berechtigungen/teamrechte/{browser_world.world.data['scope']['id']}")
        await expect(page.get_by_text("Bereichsbezogene Finanzaktionen nach bestehenden Rechten aktiv", exact=True)).to_be_visible()
        await expect(page.get_by_text("Lesen und Exportieren bleiben mit den vorhandenen Rechten möglich.")).to_be_visible()
        await field(page, "Begründung der Betriebsänderung").fill("Bereichsbezogene Arbeiten zur Prüfung pausieren")
        state = {"count": 0}
        handler = lose_first_committed_reply(page, state, "/api/accounting-scopes/control", "Antwort nach erfolgreicher Pause unterbrochen")
        await page.route("**/api/accounting-scopes/control", handler)
        await button(page, "Bereichsbezogene Finanzaktionen pausieren").click()
        event = await expect_reply_replay(page, state, "/api/accounting-scopes/control", "Bereichsbezogene Finanzaktionen pausieren")
        assert event["id"] == state["result"]["id"], "the retry must return the first committed event"
        await expect(page.get_by_text("Bereichsbezogene Finanzaktionen pausiert", exact=True)).to_be_visible()
    browser_world.run(run())


@then("the organization reports its scope-bound financial actions as paused")
def control_paused(browser_world):
    world = browser_world.world
    state = api_get(world, "/api/accounting-scopes/control")
    assert state["mode"] == "paused", state


@then("a new scoped bank account is refused")
def scoped_write_refused(browser_world):
    world = browser_world.world
    answer = world.act("POST", "/api/bank-accounts", json={
        "property_id": world.data["property"]["id"], "accounting_scope_id": world.data["scope"]["id"],
        "account_type": "LAUFEND", "label": unique(world, "Refused account"),
    })
    assert answer.status_code == 404, world.describe_response()


@then("the existing bank account is still readable")
def bank_still_readable(browser_world):
    world = browser_world.world
    answer = world.look(world.actor, f"/api/bank-accounts/{world.data['bank']['id']}")
    assert answer.status_code == 200, world.describe_response()
    assert answer.json() == world.data["bank"], "the pause must not change readable data"


@when("the manager resumes the scope-bound financial actions with a reason")
def resume_scopes(browser_world):
    async def run():
        page = browser_world.page
        await field(page, "Begründung der Betriebsänderung").fill("Prüfung abgeschlossen")
        await button(page, "Bereichsbezogene Finanzaktionen wieder aufnehmen").click()
        await expect(page.get_by_text("Bereichsbezogene Finanzaktionen nach bestehenden Rechten aktiv", exact=True)).to_be_visible()
    browser_world.run(run())


@then("the organization reports its scope-bound financial actions as active")
def control_active(browser_world):
    world = browser_world.world
    state = api_get(world, "/api/accounting-scopes/control")
    assert state["mode"] == "compatibility", state
