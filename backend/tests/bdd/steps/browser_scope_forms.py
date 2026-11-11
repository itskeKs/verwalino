"""Scoped creation forms: only granted scopes, cleared on context change, replay-safe."""
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
from tests.declaration_fixtures import make_text_pdf


def unique(world, prefix):
    return f"{prefix} {hashlib.sha256(world.data['scenario'].encode()).hexdigest()[:8]}"


def granted_scope(world, home, kind, label, user_id, *, grant=True):
    scope = create(world, "/api/accounting-scopes", {
        "property_id": home["id"], "scope_type": kind, "label": label,
        "valid_from": "2025-01-01", "whole_property": True,
    }, keyed=True)
    if grant:
        create(world, f"/api/accounting-scopes/{scope['id']}/grants", {
            "user_id": user_id, "capabilities": ["read", "prepare"],
            "reason": "Synthetic creation acceptance",
        }, keyed=True)
    return scope


@given("a property with granted rental and WEG scopes and one scope without grant exists")
def scopes_for_forms(world, seed_data):
    user_id = str(seed_data["user_a_admin_id"])
    home = create(world, "/api/properties", {
        "name": unique(world, "Creation scope"), "management_type": "MISCH",
        "street": "Test 1", "zip_code": "12345", "city": "Test",
    })
    other = create(world, "/api/properties", {
        "name": unique(world, "Other creation"), "management_type": "MISCH",
        "street": "Test 2", "zip_code": "12345", "city": "Test",
    })
    world.data.update(
        property=home, other_property=other,
        rental=granted_scope(world, home, "MIET", unique(world, "Creation rental"), user_id),
        weg=granted_scope(world, home, "WEG", unique(world, "Creation WEG"), user_id),
        unavailable=granted_scope(world, home, "WEG", unique(world, "Configuration without grant"), user_id, grant=False),
        other_scope=granted_scope(world, other, "MIET", unique(world, "Other rental"), user_id),
    )


@when("the manager starts a new bank account for the property")
def start_bank_account(browser_world):
    async def run():
        page = browser_world.page
        world = browser_world.world
        await browser_world.visit("/bankkonten")
        await button(page, "+ Neues Bankkonto").click()
        group = page.get_by_role("group", name="Objekt *", exact=True)
        await group.get_by_label("Objekte durchsuchen").fill(world.data["property"]["name"])
        await browser_world.select("Objekt *", world.data["property"]["id"], scope=group)
    browser_world.run(run())


@when('the manager chooses the WEG scope and the account type "KAUTION"')
def choose_weg_deposit(browser_world):
    async def run():
        world = browser_world.world
        await browser_world.select("Abrechnungskreis", world.data["weg"]["id"])
        await browser_world.select("Kontotyp *", "KAUTION")
    browser_world.run(run())


@then("the scope selection is cleared and only rental scopes remain")
def cleared_to_rental(browser_world):
    async def run():
        page = browser_world.page
        world = browser_world.world
        selector = page.get_by_role("combobox", name="Abrechnungskreis", exact=True)
        # The selector remounts for the new account type: wait for its options before judging them.
        await selector.locator(f'option[value="{world.data["rental"]["id"]}"]').wait_for(state="attached")
        await expect(selector).to_have_value("")
        assert await selector.locator(f'option[value="{world.data["weg"]["id"]}"]').count() == 0
    browser_world.run(run())


@when("the manager chooses the rental scope and switches to the other property")
def switch_property(browser_world):
    async def run():
        page = browser_world.page
        world = browser_world.world
        await browser_world.select("Abrechnungskreis", world.data["rental"]["id"])
        group = page.get_by_role("group", name="Objekt *", exact=True)
        await group.get_by_label("Objekte durchsuchen").fill(world.data["other_property"]["name"])
        await browser_world.select("Objekt *", world.data["other_property"]["id"], scope=group)
    browser_world.run(run())


@then("the scope selection is cleared")
def scope_cleared(browser_world):
    async def run():
        selector = browser_world.page.get_by_role("combobox", name="Abrechnungskreis", exact=True)
        await expect(selector).to_have_value("")
    browser_world.run(run())


@then("the account form fits a 390 pixel wide viewport")
def bank_form_mobile(browser_world):
    async def run():
        await assert_fits_mobile(browser_world.page)
        await browser_world.page.set_viewport_size({"width": 1440, "height": 1000})
    browser_world.run(run())


@when("the manager returns to the first property and saves the account")
def save_bank_account(browser_world):
    async def run():
        from urllib.parse import urlsplit
        page = browser_world.page
        world = browser_world.world
        group = page.get_by_role("group", name="Objekt *", exact=True)
        await group.get_by_label("Objekte durchsuchen").fill(world.data["property"]["name"])
        await browser_world.select("Objekt *", world.data["property"]["id"], scope=group)
        await browser_world.select("Abrechnungskreis", world.data["rental"]["id"])
        await field(page, "Bezeichnung *").fill(unique(world, "Created scoped bank"))
        async with page.expect_response(
                lambda response: response.request.method == "POST" and urlsplit(response.url).path == "/api/bank-accounts") as pending:
            await button(page, "Speichern").click()
        answer = await pending.value
        assert answer.status == 201, await answer.text()
        world.data["created_bank"] = await answer.json()
        world.data["created_bank_scope"] = answer.request.post_data_json["accounting_scope_id"]
    browser_world.run(run())


@then("the account is created with the rental scope")
def bank_scope_persisted(browser_world):
    world = browser_world.world
    assert world.data["created_bank_scope"] == world.data["rental"]["id"], world.data["created_bank"]


SCREENS = (
    ("cost", "/kosten", "+ Neue Kostenposition", "Objekt *", None),
    ("settlement", "/abrechnungen", "+ Neue Abrechnung", "Objekt *", "MIET"),
    ("plan", "/wirtschaftsplaene", "+ Neuer Wirtschaftsplan", "WEG-Objekt *", "WEG"),
    ("report", "/vermoegensberichte", "+ Neuer Vermögensbericht", "WEG-Objekt *", "WEG"),
)


@when("the manager starts a cost, a settlement, a plan and a report for the property")
def start_forms(browser_world):
    async def run():
        page = browser_world.page
        world = browser_world.world
        offered = {}
        for screen, route, opener, property_label, _kind in SCREENS:
            await browser_world.visit(route)
            await button(page, opener).click()
            if screen == "settlement":
                # The batch section above carries a second "Objekt *" select.
                form = page.locator("form").filter(has=page.get_by_role("combobox", name="Mietvertrag *"))
                await browser_world.select(property_label, world.data["property"]["id"], scope=form)
            else:
                await browser_world.select(property_label, world.data["property"]["id"])
            selector = page.get_by_role("combobox", name="Abrechnungskreis", exact=True)
            expected = world.data["rental"]["id"] if screen in ("cost", "settlement") else world.data["weg"]["id"]
            await selector.locator(f'option[value="{expected}"]').wait_for(state="attached")
            offered[screen] = set(await selector.locator("option").evaluate_all("options => options.map(option => option.value)"))
        world.data["offered_scopes"] = offered
    browser_world.run(run())


@then("each form offers only the scope type matching the screen")
def offered_scope_types(browser_world):
    world = browser_world.world
    offered = world.data["offered_scopes"]
    rental, weg = world.data["rental"]["id"], world.data["weg"]["id"]
    assert rental in offered["cost"] and weg in offered["cost"], offered
    assert world.data["unavailable"]["id"] not in offered["cost"], offered
    assert rental in offered["settlement"] and weg not in offered["settlement"], offered
    for screen in ("plan", "report"):
        assert weg in offered[screen] and rental not in offered[screen], offered


@given("an uploaded invoice exists")
def uploaded_invoice(world):
    answer = world.act("POST", "/api/invoice-documents/upload",
                       files={"file": ("scope.pdf", make_text_pdf("Synthetic scoped cost source"), "application/pdf")})
    assert answer.status_code == 201, world.describe_response()
    world.data["invoice"] = answer.json()


@when("the manager starts the cost draft from the invoice and the first committed reply is lost")
def cost_draft_from_invoice(browser_world):
    world = browser_world.world
    categories = world.look(world.actor, "/api/cost-categories").json()
    category = next(row for row in categories if row["code"] == "GRUNDSTEUER")

    async def run():
        page = browser_world.page
        await browser_world.visit(f"/belege/{world.data['invoice']['id']}")
        await button(page, "Kostenposition erstellen").click()
        await browser_world.select("Objekt *", world.data["property"]["id"])
        await browser_world.select("Kategorie *", category["id"])
        await field(page, "Rechnungsdatum *").fill("2025-01-01")
        await field(page, "Leistungszeitraum von *").fill("2025-01-01")
        await field(page, "Leistungszeitraum bis *").fill("2025-12-31")
        await field(page, "Bruttobetrag *").fill("119.00")
        await browser_world.select("Abrechnungskreis", world.data["rental"]["id"])
        state = {"count": 0}
        handler = lose_first_committed_reply(page, state, "/api/cost-entries", "Gespeichert, Antwort unterbrochen")
        await page.route("**/api/cost-entries", handler)
        await button(page, "Entwurf erstellen").click()
        notice = page.locator(".error-text").filter(has_text=state["detail"])
        await expect(notice).to_be_visible()
        selector = page.get_by_role("combobox", name="Abrechnungskreis", exact=True)
        assert await selector.input_value() == world.data["rental"]["id"], "the lost reply must retain the chosen scope"
        draft = await expect_reply_replay(page, state, "/api/cost-entries", "Entwurf erstellen", notice=notice)
        assert draft["id"] == state["result"]["id"], "the retry must return the first committed draft"
        await page.wait_for_url(f"**/kosten/{draft['id']}")
        world.data["cost_draft"] = draft
        world.data["posted_scope_id"] = state["body"].get("accounting_scope_id")
    browser_world.run(run())


@then("the draft is created once with the chosen scope")
def draft_scoped(browser_world):
    world = browser_world.world
    answer = world.look(world.actor, f"/api/cost-entries/{world.data['cost_draft']['id']}")
    assert answer.status_code == 200, world.describe_response()
    assert world.data["posted_scope_id"] == world.data["rental"]["id"], "the chosen scope must travel with the draft"
