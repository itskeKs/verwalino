"""Populated cost tables through real filters, scrolling and keyboard navigation."""
import os
from pathlib import Path
from uuid import uuid4

import pytest_asyncio
from playwright.async_api import expect
from pytest_bdd import given, parsers, then, when

from app.core.security import hash_password
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.user import User
from tests.bdd.steps.browser_common import browser_step, button
from tests.bdd.steps.browser_setup import create, create_scope
from tests.conftest import _api_test_client

ROOT = Path(__file__).resolve().parents[4]


@pytest_asyncio.fixture
async def isolated_cost_identity(migrator_session):
    """Keep unrelated long selector labels out of this table regression."""
    organization = Organization(id=uuid4(), name="Kostentabelle Testverwaltung")
    user = User(id=uuid4(), organization_id=organization.id,
                email=f"cost-table-{uuid4()}@example.test", display_name="Testverwaltung",
                password_hash=hash_password("testpass1234"))
    migrator_session.add(organization)
    await migrator_session.flush()
    migrator_session.add(user)
    await migrator_session.flush()
    migrator_session.add(Membership(organization_id=organization.id, user_id=user.id, role="OWNER_ADMIN"))
    await migrator_session.commit()
    async with _api_test_client() as client:
        response = await client.post("/api/auth/login", json={"email": user.email, "password": "testpass1234"})
        assert response.status_code == 200
        yield client, str(user.id), user.email


@given("an isolated cost list with Aprilhaus gardening and several additional costs")
def populated_costs(world, isolated_cost_identity):
    world.actor, user_id, email = isolated_cost_identity
    world.data.update(cost_email=email, cost_properties=[], costs=[])
    response = world.act("GET", "/api/cost-categories")
    assert response.status_code == 200, world.describe_response()
    category = next(row for row in response.json() if row["code"] == "GARTENPFLEGE")
    for name in ("Aprilhaus", "Maihaus"):
        home = create(world, "/api/properties", {
            "name": name, "management_type": "MIET",
            "data_audience": {"label": name, "reason": "Synthetic cost table journey", "grants": [{
                "user_id": user_id, "capabilities": ["read", "prepare", "approve", "export"],
                "reason": "Synthetic cost table journey",
            }]},
        }, keyed=True)
        world.data["property"] = home
        scope = create_scope(world, user_id, f"Mietkreis {name}")
        world.data["cost_properties"].append(home)
        for index, amount in enumerate(("1250.00", "2345.67", "9876.54")):
            cost = create(world, "/api/cost-entries", {
                "property_id": home["id"], "accounting_scope_id": scope["id"],
                "cost_category_id": category["id"], "cost_category_plan_version": category["plan_version"],
                "invoice_date": f"2026-12-{31 - index}", "service_period_from": "2026-01-01",
                "service_period_to": "2026-12-31", "gross_amount": amount,
                "tenant_distribution_key": "WOHNFLAECHE", "tenant_apportionable": "UMLAGEFAEHIG",
                "source": "MANUAL",
            })
            if index == 0:
                response = world.act("POST", f"/api/cost-entries/{cost['id']}/confirm")
                assert response.status_code == 200, world.describe_response()
            world.data["costs"].append(cost)


@when(parsers.parse("the manager opens the populated cost list at {width:d} pixels"))
@browser_step
async def open_costs(browser_world, width):
    b, page = browser_world, browser_world.page
    await page.set_viewport_size({"width": width, "height": 900})
    await b.context.clear_cookies()
    await b.visit("/kosten")
    await page.get_by_label("E-Mail", exact=True).fill(b.world.data["cost_email"])
    await page.get_by_label("Passwort", exact=True).fill("testpass1234")
    await button(page, "Anmelden").click()
    await expect(page.get_by_role("link", name="Objekte", exact=True)).to_be_visible()
    await b.visit("/kosten")
    await expect(page.get_by_role("heading", name="Kostenpositionen", exact=True)).to_be_visible()
    await expect(page.locator("tbody tr")).to_have_count(6)


async def assert_table_bounds(page):
    bounds = await page.get_by_role("table").evaluate("""table => {
        const card = table.parentElement;
        const rect = card.getBoundingClientRect();
        return {left: rect.left, right: rect.right, viewport: innerWidth,
                width: card.clientWidth, scroll: card.scrollWidth};
    }""")
    assert bounds["left"] >= 0 and bounds["right"] <= bounds["viewport"], bounds
    if page.viewport_size["width"] == 360:
        assert bounds["scroll"] > bounds["width"], bounds


async def assert_page_bounds(page):
    bounds = await page.locator("main").evaluate("""main => ({
        client: main.clientWidth, scroll: main.scrollWidth,
        document: document.documentElement.scrollWidth, viewport: innerWidth
    })""")
    assert bounds["scroll"] <= bounds["client"], bounds
    assert bounds["document"] <= bounds["viewport"], bounds


@then("the cost table stays inside the page and exposes every column")
@browser_step
async def contained_costs(browser_world, tmp_path):
    page = browser_world.page
    table = page.get_by_role("table")
    await expect(table.get_by_role("columnheader")).to_have_text([
        "Datum", "Kategorie", "Objekt", "Brutto", "Status", "Quelle", "",
    ])
    april = table.get_by_role("row").filter(has=page.get_by_role("cell", name="Aprilhaus", exact=True))
    gardening = april.filter(has=page.get_by_role("cell", name="2026-12-31", exact=True))
    await expect(gardening.get_by_role("cell")).to_have_text([
        "2026-12-31", "GARTENPFLEGE Gartenpflege", "Aprilhaus", "1250.00", "Bestaetigt", "Manuell", "Details",
    ])
    for amount in ("1250.00", "2345.67", "9876.54"):
        await expect(table.get_by_role("cell", name=amount, exact=True)).to_have_count(2)
    state = await table.evaluate("table => getComputedStyle(table.parentElement).overflowX === 'auto' ? 'after' : 'before'")
    directory = ROOT / "docs/assets/design" if os.environ.get("VERWALINO_CAPTURE_DESIGN") == "1" else tmp_path
    await page.screenshot(path=str(directory / f"cost-table-{state}-{page.viewport_size['width']}.png"), full_page=True)
    await assert_table_bounds(page)
    await assert_page_bounds(page)
    await table.evaluate("table => { table.parentElement.scrollLeft = table.parentElement.scrollWidth; }")
    await expect(gardening.get_by_role("link", name="Details", exact=True)).to_be_in_viewport()
    await assert_page_bounds(page)


@then("the cost filters and expanded creation form preserve the populated table")
@browser_step
async def cost_filters_and_form(browser_world):
    b, page = browser_world, browser_world.page
    april, may = b.world.data["cost_properties"]
    await b.select("Objektfilter", april["id"])
    await expect(page.locator("tbody tr")).to_have_count(3)
    await b.select("Statusfilter", "confirmed")
    await expect(page.locator("tbody tr")).to_have_count(1)
    await expect(page.get_by_role("cell", name="Aprilhaus", exact=True)).to_be_visible()
    await b.select("Objektfilter", may["id"])
    await expect(page.get_by_role("cell", name="Maihaus", exact=True)).to_be_visible()
    await b.select("Objektfilter", "")
    await expect(page.locator("tbody tr")).to_have_count(2)
    await b.select("Statusfilter", "")
    await expect(page.locator("tbody tr")).to_have_count(6)
    await button(page, "+ Neue Kostenposition").click()
    await expect(page.get_by_role("heading", name="Neue Kostenposition anlegen", exact=True)).to_be_visible()
    await b.select("Objekt *", april["id"])
    await assert_table_bounds(page)
    await expect(page.locator("tbody tr")).to_have_count(6)
    await button(page, "Abbrechen").click()
    await expect(page.get_by_role("heading", name="Neue Kostenposition anlegen", exact=True)).to_have_count(0)
    await assert_page_bounds(page)


@then("the manager opens the confirmed gardening cost details using the keyboard")
@browser_step
async def cost_keyboard_details(browser_world):
    b, page = browser_world, browser_world.page
    await b.select("Objektfilter", b.world.data["cost_properties"][0]["id"])
    await b.select("Statusfilter", "confirmed")
    await expect(page.locator("tbody tr")).to_have_count(1)
    details = page.get_by_role("link", name="Details", exact=True)
    await button(page, "CSV herunterladen").focus()
    await page.keyboard.press("Tab")
    await expect(details).to_be_focused()
    await expect(details).to_be_in_viewport()
    await assert_page_bounds(page)
    await page.keyboard.press("Enter")
    await expect(page).to_have_url(f"{b.session.base_url}/kosten/{b.world.data['costs'][0]['id']}")
    await expect(page.get_by_role("cell", name="1250.00", exact=True)).to_be_visible()
