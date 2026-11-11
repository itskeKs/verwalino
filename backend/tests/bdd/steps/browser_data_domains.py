"""Shared building-data domains: replay-safe preparation and durable revocation."""
import hashlib

from playwright.async_api import expect
from pytest_bdd import given, then, when

from tests.bdd.steps.browser_common import (
    assert_fits_mobile,
    button,
    expect_reply_replay,
    field,
    lose_first_committed_reply,
)
from tests.bdd.steps.browser_setup import create


def unique(world, prefix):
    return f"{prefix} {hashlib.sha256(world.data['scenario'].encode()).hexdigest()[:8]}"


@given("a WEG property exists")
def weg_property(world):
    home = create(world, "/api/properties", {
        "name": unique(world, "Shared data QA"), "management_type": "WEG",
    })
    world.data.update(property=home, domain_label=unique(world, "Gemeinsame Quellen QA"))


@when("the manager prepares the domain from the permissions page and the first committed reply is lost")
def prepare_domain(browser_world):
    async def run():
        page = browser_world.page
        world = browser_world.world
        await browser_world.visit("/berechtigungen")
        await page.get_by_role("link", name="Gebäudedaten-Rechte", exact=True).click()
        await expect(page.get_by_role("heading", name="Gemeinsame Gebäudedaten", exact=True)).to_be_visible()
        await field(page, "Objekte durchsuchen").fill(world.data["property"]["name"])
        await browser_world.select("Objekt", world.data["property"]["id"])
        await field(page, "Bezeichnung").fill(world.data["domain_label"])
        await field(page, "Begründung").fill("Geprüfter gemeinsamer Datenbereich")
        state = {"count": 0}
        handler = lose_first_committed_reply(page, state, "/api/property-data-domains", "Gespeichert, Antwort unterbrochen")
        await page.route("**/api/property-data-domains", handler)
        await button(page, "Datenbereich vorbereiten").click()
        domain = await expect_reply_replay(page, state, "/api/property-data-domains", "Datenbereich vorbereiten")
        assert domain["id"] == state["result"]["id"], "the retry must return the first committed domain"
        assert domain["enforcement_status"] == "PREPARATION", domain
        world.data["domain"] = domain
    browser_world.run(run())


@then("the domain is prepared once and its team page opens")
def domain_once(browser_world):
    world = browser_world.world
    items = world.look(world.actor, f"/api/property-data-domains/page?property_id={world.data['property']['id']}").json()["items"]
    assert len(items) == 1 and items[0]["label"] == world.data["domain_label"], items

    async def run():
        page = browser_world.page
        await page.get_by_role("link", name=f"{world.data['domain_label']} – Teamfreigaben vorbereiten", exact=True).click()
        await expect(page.get_by_role("heading", name=world.data["domain_label"], exact=True)).to_be_visible()
    browser_world.run(run())


@when('the manager prepares a grant for the clerk with "Bearbeiten"')
def prepare_grant(browser_world, seed_data):
    async def run():
        page = browser_world.page
        await field(page, "Teammitglied suchen").fill("clerk@alpha.de")
        await browser_world.select("Teammitglied", str(seed_data["user_a_clerk_id"]))
        await field(page, "Bearbeiten").check()
        await field(page, "Begründung").fill("Gemeinsame Gebäudeunterlagen geprüft")
        await button(page, "Teamfreigabe vorbereiten").click()
    browser_world.run(run())


@then("the grant shows as prepared without expiry")
def grant_prepared(browser_world):
    async def run():
        await expect(browser_world.page.get_by_role("cell", name="Vorbereitet ohne Ablauf", exact=True)).to_be_visible()
    browser_world.run(run())


@when("the manager revokes the grant")
def revoke_grant(browser_world):
    async def run():
        await button(browser_world.page, "Widerrufen").click()
    browser_world.run(run())


@then("the revocation stays visible in the grant history")
def grant_revoked(browser_world):
    async def run():
        await expect(browser_world.page.get_by_role("cell", name="Widerrufen", exact=True)).to_be_visible()
    browser_world.run(run())

    world = browser_world.world
    history = world.look(world.actor, f"/api/property-data-domains/{world.data['domain']['id']}/grants/page").json()["items"]
    assert len(history) == 1 and history[0]["revoked_at"], history


@then("the domain page fits a 390 pixel wide viewport")
def domain_mobile(browser_world):
    browser_world.run(assert_fits_mobile(browser_world.page))
