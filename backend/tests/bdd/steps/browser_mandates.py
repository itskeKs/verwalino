"""Scoped mandate sources: audience-only listing, exact export grant, durable archive."""
import hashlib

from playwright.async_api import expect
from pytest_bdd import given, then, when

from tests.bdd.steps.browser_common import assert_fits_mobile, button, field, sign_in_context
from tests.bdd.steps.browser_setup import create

CLERK = ("clerk@alpha.de", "testpass1234")
CONTRACT = b"%PDF-1.4 synthetic original mandate contract"


def unique(world, prefix):
    return f"{prefix} {hashlib.sha256(world.data['scenario'].encode()).hexdigest()[:8]}"


@given("a WEG mandate with a scoped clerk grant exists")
def mandate_with_scope(world, seed_data):
    mandate = create(world, "/api/mandates", {
        "mandate_type": "WEG", "label": unique(world, "Scoped mandate QA"), "start_date": "2024-01-01",
    })
    home = create(world, "/api/properties", {
        "name": unique(world, "Source QA property"), "management_type": "WEG",
        "street": "Test 1", "zip_code": "12345", "city": "Test",
    })
    scope = create(world, "/api/accounting-scopes", {
        "property_id": home["id"], "management_mandate_id": mandate["id"], "scope_type": "WEG",
        "label": unique(world, "Reviewed source scope"), "valid_from": "2025-01-01", "whole_property": True,
    }, keyed=True)
    grant = create(world, f"/api/accounting-scopes/{scope['id']}/grants", {
        "user_id": str(seed_data["user_a_clerk_id"]), "capabilities": ["read", "prepare"],
        "reason": "Synthetic source reviewer",
    }, keyed=True)
    world.data.update(mandate=mandate, scope=scope, grant=grant)


@when("the clerk uploads a contract into the scope with a reason")
def clerk_uploads(browser_world):
    async def run():
        from urllib.parse import urlsplit

        async def upload(page):
            world = browser_world.world
            await page.goto(f"/mandate/{world.data['mandate']['id']}")
            await expect(page.get_by_role("heading", name="Zugängliche Dokumente", exact=True)).to_be_visible()
            await expect(page.get_by_text("keine vollständige Mandatsakte")).to_be_visible()
            await page.get_by_role("combobox", name="Bereich für neuen Upload *", exact=True).locator(
                f'option[value="{world.data["scope"]["id"]}"]').wait_for(state="attached")
            await page.get_by_role("combobox", name="Bereich für neuen Upload *", exact=True).select_option(world.data["scope"]["id"])
            await field(page, "Begründung der Bereichszuordnung").fill("Gesamter Dateiinhalt für diesen Bereich geprüft")
            await field(page, "Bezeichnung").fill("Scoped source contract")
            await page.locator("#mandate-file").set_input_files(
                {"name": "contract.pdf", "mimeType": "application/pdf", "buffer": CONTRACT})
            async with page.expect_response(
                    lambda response: response.request.method == "POST"
                    and urlsplit(response.url).path == f"/api/mandates/{world.data['mandate']['id']}/documents") as pending:
                await button(page, "Hochladen").click()
            answer = await pending.value
            assert answer.status == 201, await answer.text()
            document = await answer.json()
            assert document["accounting_scope_id"] == world.data["scope"]["id"], document
            world.data["document"] = document
            await expect(page.get_by_text("Scoped source contract", exact=True)).to_be_visible()

        context, page = await sign_in_context(browser_world, *CLERK)
        try:
            await upload(page)
        finally:
            await context.close()
    browser_world.run(run())


@then("the clerk sees the contract without a delete action")
def clerk_sees_contract(browser_world):
    async def run():
        context, page = await sign_in_context(browser_world, *CLERK)
        try:
            await page.goto(f"/mandate/{browser_world.world.data['mandate']['id']}")
            await expect(page.get_by_text("Scoped source contract", exact=True)).to_be_visible()
            assert await page.get_by_role("button", name="Löschen", exact=True).count() == 0
        finally:
            await context.close()
    browser_world.run(run())


@then("the administration cannot see the contract in the mandate's documents")
def admin_cannot_see(world):
    answer = world.look(world.actor, f"/api/mandates/{world.data['mandate']['id']}/documents")
    assert answer.status_code == 200, world.describe_response()
    assert answer.json() == [], answer.json()


@when('the clerk\'s grant gains "Exportieren"')
def grant_export(world, seed_data):
    data = world.data
    revoked = world.act("POST", f"/api/accounting-scopes/{data['scope']['id']}/grants/{data['grant']['id']}/revoke")
    assert revoked.status_code == 204, world.describe_response()
    data["grant"] = create(world, f"/api/accounting-scopes/{data['scope']['id']}/grants", {
        "user_id": str(seed_data["user_a_clerk_id"]), "capabilities": ["read", "prepare", "export"],
        "reason": "Synthetic reviewed export",
    }, keyed=True)


@then("the clerk cannot download the original without the export grant")
def clerk_download_refused(browser_world):
    async def run():
        context, page = await sign_in_context(browser_world, *CLERK)
        try:
            answer = await context.request.get(
                f"/api/mandates/{browser_world.world.data['mandate']['id']}/documents/{browser_world.world.data['document']['id']}/download")
            assert answer.status == 404, await answer.text()
        finally:
            await context.close()
    browser_world.run(run())


@then("the clerk can download the original bytes")
def clerk_downloads(browser_world):
    async def run():
        context, page = await sign_in_context(browser_world, *CLERK)
        try:
            answer = await context.request.get(
                f"/api/mandates/{browser_world.world.data['mandate']['id']}/documents/{browser_world.world.data['document']['id']}/download")
            assert answer.status == 200, await answer.text()
            assert await answer.body() == CONTRACT, "the download must return the original bytes"
        finally:
            await context.close()
    browser_world.run(run())


@when("the clerk archives the contract with a reason")
def clerk_archives(browser_world):
    async def run():
        context, page = await sign_in_context(browser_world, *CLERK)
        try:
            await page.goto(f"/mandate/{browser_world.world.data['mandate']['id']}")
            row = page.get_by_role("row").filter(has=page.get_by_text("Scoped source contract", exact=True))
            await button(row, "Archivieren").click()
            await field(page, "Begründung der Archivierung").fill("Durch neue Vertragsfassung ersetzt")
            await button(page, "Archivierung speichern").click()
            await expect(page.get_by_text("Keine zugänglichen Unterlagen für diese Auswahl.", exact=True)).to_be_visible()
        finally:
            await context.close()
    browser_world.run(run())


@then("the archived contract shows its reason and stays downloadable")
def archived_visible(browser_world):
    async def run():
        context, page = await sign_in_context(browser_world, *CLERK)
        try:
            await page.goto(f"/mandate/{browser_world.world.data['mandate']['id']}")
            await field(page, "Archivierte Unterlagen anzeigen").check()
            await expect(page.get_by_text("Scoped source contract", exact=True)).to_be_visible()
            await expect(page.get_by_text("Archiviert · Durch neue Vertragsfassung ersetzt", exact=True)).to_be_visible()
            assert await page.get_by_role("button", name="Archivieren", exact=True).count() == 0
        finally:
            await context.close()
    browser_world.run(run())

    async def download():
        context, page = await sign_in_context(browser_world, *CLERK)
        try:
            answer = await context.request.get(
                f"/api/mandates/{browser_world.world.data['mandate']['id']}/documents/{browser_world.world.data['document']['id']}/download")
            assert answer.status == 200, await answer.text()
            assert await answer.body() == CONTRACT, "the archive must keep the original bytes"
        finally:
            await context.close()
    browser_world.run(download())


@then("the mandate page fits a 390 pixel wide viewport")
def mandate_fits_mobile(browser_world):
    async def run():
        context, page = await sign_in_context(browser_world, *CLERK)
        try:
            await page.goto(f"/mandate/{browser_world.world.data['mandate']['id']}")
            await field(page, "Archivierte Unterlagen anzeigen").check()
            await expect(page.get_by_text("Scoped source contract", exact=True)).to_be_visible()
            await assert_fits_mobile(page)
        finally:
            await context.close()
    browser_world.run(run())


@when("the documents fail to reload")
def fail_reload(browser_world, request):
    def close_clerk_context():
        context = browser_world.world.data.pop("clerk_context", None)
        if context:
            browser_world.run(context.close())

    request.addfinalizer(close_clerk_context)

    async def run():
        world = browser_world.world

        async def interruption(route):
            if route.request.method == "GET" and route.request.url.split("?")[0].endswith("/documents"):
                await route.fulfill(status=503, content_type="application/json",
                                    body='{"detail": "Synthetic list interruption"}')
            else:
                await route.continue_()

        context, page = await sign_in_context(browser_world, *CLERK)
        world.data["clerk_context"] = context
        await page.goto(f"/mandate/{world.data['mandate']['id']}")
        await field(page, "Archivierte Unterlagen anzeigen").check()
        await expect(page.get_by_text("Scoped source contract", exact=True)).to_be_visible()
        await page.route("**/api/mandates/*/documents*", interruption)
        await button(page, "Unterlagen neu laden").click()
        await expect(page.get_by_role("alert").filter(has_text="Unterlagen derzeit nicht verfügbar")).to_be_visible()
    browser_world.run(run())


@then("the stale list is hidden without an empty-success claim")
def stale_hidden(browser_world):
    async def run():
        context = browser_world.world.data.pop("clerk_context")
        try:
            page = context.pages[0]
            assert await page.get_by_text("Scoped source contract", exact=True).count() == 0
            assert await page.get_by_text("Keine zugänglichen Unterlagen für diese Auswahl.", exact=True).count() == 0
        finally:
            await context.close()
    browser_world.run(run())


@when("the clerk's grant is revoked")
def revoke_clerk_grant(world):
    data = world.data
    answer = world.act("POST", f"/api/accounting-scopes/{data['scope']['id']}/grants/{data['grant']['id']}/revoke")
    assert answer.status_code == 204, world.describe_response()


@then("the mandate shows no accessible documents")
def no_documents(browser_world):
    async def run():
        context, page = await sign_in_context(browser_world, *CLERK)
        try:
            download = await context.request.get(
                f"/api/mandates/{browser_world.world.data['mandate']['id']}/documents/{browser_world.world.data['document']['id']}/download")
            assert download.status == 404, await download.text()
            await page.goto(f"/mandate/{browser_world.world.data['mandate']['id']}")
            await expect(page.get_by_text("Keine zugänglichen Unterlagen für diese Auswahl.", exact=True)).to_be_visible()
        finally:
            await context.close()
    browser_world.run(run())
