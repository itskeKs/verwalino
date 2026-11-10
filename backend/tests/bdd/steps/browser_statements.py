"""Scoped statements, open items and the deadline monitor follow the chosen scope."""
import hashlib
import re

from playwright.async_api import expect
from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.browser_common import button, euro, field, wait_scope_usable
from tests.bdd.steps.browser_setup import create


TENANT_DEBT = "24 ueberfaellige(r) Posten (Mahnwesen pruefen)."
ALL_CLEAR = re.compile("Keine überfälligen|Keine Fristen ermittelt|Alle fälligen")
DASHBOARD_UNAVAILABLE = "Fristenübersicht nicht verfügbar. Der Handlungsbedarf kann derzeit nicht ermittelt werden."
DASHBOARD_ALL_CLEAR = re.compile("alles im grünen Bereich|Alles erledigt|keine offenen Posten")
RETENTION_UNKNOWN = "Aufbewahrungsübersicht nicht verfügbar; die Belegzuordnung ist noch unvollständig."


def unique(world, prefix):
    return f"{prefix} {hashlib.sha256(world.data['scenario'].encode()).hexdigest()[:8]}"


def granted_scope(world, home, kind, label, user_id, capabilities):
    scope = create(world, "/api/accounting-scopes", {
        "property_id": home["id"], "scope_type": kind, "label": label,
        "valid_from": "2025-01-01", "valid_to": "2025-12-31", "whole_property": True,
    }, keyed=True)
    grant = create(world, f"/api/accounting-scopes/{scope['id']}/grants", {
        "user_id": user_id, "capabilities": capabilities, "reason": "Synthetic statement acceptance",
    }, keyed=True)
    return scope, grant


def lease_with_tenant(world, home):
    unit = create(world, "/api/units", {"property_id": home["id"], "number": "1", "usage_type": "WOHNUNG"})
    person = create(world, "/api/persons", {"last_name": unique(world, "Statement QA")})
    tenant = create(world, "/api/tenant-profiles", {"person_id": person["id"]})
    lease = create(world, "/api/leases", {
        "unit_id": unit["id"], "tenant_profile_id": tenant["id"], "valid_from": "2025-01-01",
        "base_rent": "500.00", "advance_payment": "100.00",
    })
    return unit, tenant, lease


@given("a lease with one booked payment in each of two granted scopes exists")
def two_scoped_payments(world, seed_data):
    user_id = str(seed_data["user_a_admin_id"])
    home = create(world, "/api/properties", {
        "name": unique(world, "Statement QA"), "management_type": "MIET",
        "street": "Test 1", "zip_code": "12345", "city": "Test",
    })
    _unit, tenant, lease = lease_with_tenant(world, home)
    scopes, grants = [], []
    for index, amount in enumerate(("100.00", "900.00")):
        scope, grant = granted_scope(world, home, "MIET", unique(world, f"Statement circle {index}"), user_id,
                                     ["read", "prepare", "confirm"])
        wait_scope_usable(world, scope["id"])
        bank = create(world, "/api/bank-accounts", {
            "property_id": home["id"], "accounting_scope_id": scope["id"],
            "account_type": "LAUFEND", "label": unique(world, f"Statement account {index}"),
        })
        payment = create(world, "/api/payments", {
            "bank_account_id": bank["id"], "direction": "IN", "amount": amount,
            "value_date": "2025-06-01", "payment_type": "KALTMIETE",
            "tenant_profile_id": tenant["id"], "lease_agreement_id": lease["id"],
            "period_year": 2025, "period_month": 6,
        })
        booked = world.act("POST", f"/api/payments/{payment['id']}/book")
        assert booked.status_code == 200, world.describe_response()
        scopes.append(scope)
        grants.append(grant)
    world.data.update(scopes=scopes, grants=grants, lease=lease)


@when("the manager opens the rent statement for the lease and the first scope")
def open_statement(browser_world):
    async def run():
        page = browser_world.page
        world = browser_world.world
        await browser_world.visit("/mietkonto")
        await browser_world.select("Mietvertrag *", world.data["lease"]["id"])
        await field(page, "Zeitraum von *").fill("2025-01-01")
        await field(page, "Zeitraum bis *").fill("2025-12-31")
        await browser_world.select("Abrechnungskreis", world.data["scopes"][0]["id"])
        await button(page, "Abrufen").click()
    browser_world.run(run())


@then("the statement shows 100,00 € and no 1.000,00 €")
def statement_scoped(browser_world):
    async def run():
        table = browser_world.page.locator(".info-table")
        await expect(table.get_by_role("cell", name=euro("100.00"), exact=True)).to_be_visible()
        await expect(table.get_by_role("cell", name=euro("1000.00"), exact=True)).to_have_count(0)
    browser_world.run(run())


@when(parsers.parse("the manager requests the statement from {start}"))
def statement_from(browser_world, start):
    async def run():
        await field(browser_world.page, "Zeitraum von *").fill(start)
    browser_world.run(run())


@then("the statement reports the period as outside the accounting scope")
def statement_outside(browser_world):
    async def run():
        page = browser_world.page
        await expect(page.get_by_role("heading", name="Kontoauszug", exact=True)).to_have_count(0)
        await button(page, "Abrufen").click()
        await expect(page.locator(".error-text").filter(has_text="outside accounting scope")).to_be_visible()
    browser_world.run(run())


@when("the manager restores the period and retrieves the statement again")
def statement_restored(browser_world):
    async def run():
        page = browser_world.page
        await field(page, "Zeitraum von *").fill("2025-01-01")
        await button(page, "Abrufen").click()
    browser_world.run(run())


@when("the manager opens the open items for the first scope")
def open_open_items(browser_world):
    async def run():
        page = browser_world.page
        world = browser_world.world
        await browser_world.visit("/offene-posten")
        await field(page, "Zeitraum von *").fill("2025-01-01")
        await field(page, "Zeitraum bis *").fill("2025-12-31")
        await browser_world.select("Abrechnungskreis", world.data["scopes"][0]["id"])
        await button(page, "Offene Posten laden").click()
    browser_world.run(run())


@then("the open items show 7.100,00 € outstanding")
def open_items_amount(browser_world):
    async def run():
        await expect(browser_world.page.get_by_text(f"{euro('7100.00')} offen", exact=True)).to_be_visible()
    browser_world.run(run())


@when("the grant for the first scope is revoked and the open items are reloaded")
def revoke_first_scope(browser_world):
    world = browser_world.world
    answer = world.act("POST", f"/api/accounting-scopes/{world.data['scopes'][0]['id']}/grants/{world.data['grants'][0]['id']}/revoke")
    assert answer.status_code == 204, world.describe_response()

    async def run():
        await button(browser_world.page, "Offene Posten laden").click()
    browser_world.run(run())


@then("the open items are reported as not accessible")
def open_items_blocked(browser_world):
    async def run():
        await expect(browser_world.page.get_by_role("alert").filter(has_text="not accessible")).to_be_visible()
    browser_world.run(run())


@then("neither stale amounts nor an empty success are shown")
def no_false_empty(browser_world):
    async def run():
        page = browser_world.page
        await expect(page.get_by_text(f"{euro('7100.00')} offen", exact=True)).to_have_count(0)
        await expect(page.get_by_text("Keine offenen Posten", exact=False)).to_have_count(0)
    browser_world.run(run())


@when("the manager opens the dashboard after the revocation")
def dashboard_after_revocation(browser_world):
    async def run():
        await browser_world.visit("/")
    browser_world.run(run())


@then("the dashboard reports the deadline overview as unavailable without an all-clear")
def dashboard_unavailable(browser_world):
    async def run():
        page = browser_world.page
        await expect(page.get_by_text(DASHBOARD_UNAVAILABLE, exact=True)).to_be_visible()
        await expect(page.get_by_text(DASHBOARD_ALL_CLEAR)).to_have_count(0)
    browser_world.run(run())


@given("a mixed-use property with confirmed costs in a rental and a WEG scope exists")
def scoped_deadlines(world, seed_data):
    user_id = str(seed_data["user_a_admin_id"])
    home = create(world, "/api/properties", {
        "name": unique(world, "Deadline QA"), "management_type": "MISCH",
        "street": "Test 1", "zip_code": "12345", "city": "Test",
    })
    lease_with_tenant(world, home)
    categories = world.look(world.actor, "/api/cost-categories").json()
    category = next(row for row in categories if row["code"] == "GRUNDSTEUER")
    scopes, grants = {}, {}
    for kind in ("MIET", "WEG"):
        scope, grant = granted_scope(world, home, kind, unique(world, f"Deadline {kind}"), user_id,
                                     ["read", "prepare", "confirm"])
        wait_scope_usable(world, scope["id"])
        create(world, "/api/bank-accounts", {
            "property_id": home["id"], "accounting_scope_id": scope["id"],
            "account_type": "LAUFEND", "label": unique(world, f"Deadline bank {kind}"),
        })
        cost = create(world, "/api/cost-entries", {
            "property_id": home["id"], "accounting_scope_id": scope["id"], "cost_category_id": category["id"],
            "invoice_date": "2025-06-01", "service_period_from": "2025-01-01", "service_period_to": "2025-12-31",
            "gross_amount": "100.00", "tenant_apportionable": "UMLAGEFAEHIG",
        })
        confirmed = world.act("POST", f"/api/cost-entries/{cost['id']}/confirm")
        assert confirmed.status_code == 200, world.describe_response()
        scopes[kind] = scope
        grants[kind] = grant
    world.data.update(deadline_scopes=scopes, deadline_grants=grants)


@when("the manager opens the deadline monitor and chooses the rental scope")
def open_monitor_rental(browser_world):
    async def run():
        page = browser_world.page
        await browser_world.visit("/fristen")
        await field(page, "Stichtag").fill("2026-12-31")
        await browser_world.select("Abrechnungskreis", browser_world.world.data["deadline_scopes"]["MIET"]["id"])
        await expect(page.get_by_text("Betriebskostenabrechnung", exact=True)).to_be_visible()
    browser_world.run(run())


@then("the monitor shows the rental deadline and no WEG annual statement")
def monitor_rental(browser_world):
    async def run():
        page = browser_world.page
        await expect(page.get_by_text("WEG-Jahresabrechnung", exact=True)).to_have_count(0)
        await expect(page.get_by_text(TENANT_DEBT, exact=True)).to_be_visible()
        await expect(page.get_by_text(RETENTION_UNKNOWN, exact=True)).to_be_visible()
    browser_world.run(run())


@when("the manager chooses the WEG scope")
def choose_weg_scope(browser_world):
    async def run():
        await browser_world.select("Abrechnungskreis", browser_world.world.data["deadline_scopes"]["WEG"]["id"])
        await expect(browser_world.page.get_by_text("WEG-Jahresabrechnung", exact=True)).to_be_visible()
    browser_world.run(run())


@then("the monitor shows the WEG annual statement and no rental deadline")
def monitor_weg(browser_world):
    async def run():
        page = browser_world.page
        await expect(page.get_by_text("Betriebskostenabrechnung", exact=True)).to_have_count(0)
        await expect(page.get_by_text(TENANT_DEBT, exact=True)).to_have_count(0)
        await expect(page.get_by_text("Wirtschaftsplan", exact=True)).to_have_count(0)
    browser_world.run(run())


@when("the manager opens the dashboard with the WEG scope")
def dashboard_weg(browser_world):
    async def run():
        await browser_world.visit("/")
        await browser_world.select("Abrechnungskreis", browser_world.world.data["deadline_scopes"]["WEG"]["id"])
    browser_world.run(run())


@then("the dashboard does not invent tenant debt for the WEG scope")
def dashboard_no_debt(browser_world):
    async def run():
        page = browser_world.page
        await expect(page.get_by_text("Für diesen Bereich nicht ausgewiesen", exact=True)).to_be_visible()
        assert await page.get_by_role("alert").count() == 0
    browser_world.run(run())


@when("the grant for the WEG scope is revoked and the monitor is requeried")
def revoke_weg_scope(browser_world):
    world = browser_world.world
    weg_id = world.data["deadline_scopes"]["WEG"]["id"]

    async def run():
        page = browser_world.page
        await browser_world.visit("/fristen")
        await browser_world.select("Abrechnungskreis", weg_id)
        await expect(page.get_by_text("WEG-Jahresabrechnung", exact=True)).to_be_visible()
    browser_world.run(run())

    answer = world.act("POST", f"/api/accounting-scopes/{weg_id}/grants/{world.data['deadline_grants']['WEG']['id']}/revoke")
    assert answer.status_code == 204, world.describe_response()

    async def requery():
        await field(browser_world.page, "Stichtag").fill("2026-12-30")
    browser_world.run(requery())


@then("the monitor reports the loss without an all-clear")
def monitor_revoked(browser_world):
    async def run():
        page = browser_world.page
        await expect(page.get_by_role("alert").filter(has_text="not accessible")).to_be_visible()
        await expect(page.get_by_text("WEG-Jahresabrechnung", exact=True)).to_have_count(0)
        # The summary sentence shares its paragraph with the legal note, so match a fragment.
        await expect(page.get_by_text(ALL_CLEAR)).to_have_count(0)
    browser_world.run(run())
