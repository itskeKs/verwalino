"""Declaration import start: replay-safe queueing and durable principal cancellation."""
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from playwright.async_api import expect
from pytest_bdd import then, when

from tests.bdd.steps.browser_common import assert_fits_mobile, button, field, sign_in_context, signed_in_client

CLERK = ("clerk@alpha.de", "testpass1234")
PDF = b"%PDF-1.4\n%%EOF"


def cancel_import_job(world):
    """Do not leave a queued import behind: the next claim of this organization would pick it up."""
    client = signed_in_client(world, *CLERK)
    try:
        answer = world.run(client.post(f"/api/declaration-imports/{world.data['job']}/cancel"))
        # Cancelling twice answers 200; 409 means the job already reached a terminal state.
        assert answer.status_code in (200, 409), answer.text
    finally:
        world.run(client.aclose())


@when("the clerk uploads a PDF and starts the import and the first committed reply is lost")
def clerk_starts_import(browser_world, request):
    async def run():
        context, page = await sign_in_context(browser_world, *CLERK)
        try:
            await page.goto("/teilungserklaerung/import")
            await page.wait_for_url("**/teilungserklaerung/import/*")
            job = urlsplit(page.url).path.rsplit("/", 1)[-1]
            browser_world.world.data["job"] = job
            request.addfinalizer(lambda: cancel_import_job(browser_world.world))
            start_path = f"/api/declaration-imports/{job}/start"
            await page.locator("#declaration-files:enabled").wait_for()
            await field(page, "PDF-Dateien auswählen").set_input_files(
                {"name": "synthetic.pdf", "mimeType": "application/pdf", "buffer": PDF})
            await button(page, "Ausgewählte PDFs hochladen").click()
            await expect(button(page, "Import starten")).to_be_enabled()
            state = {"keys": [], "receipt": None}

            async def handler(route):
                request = route.request
                if request.method != "POST" or urlsplit(request.url).path != start_path:
                    await route.continue_()
                    return
                state["keys"].append(request.headers.get("idempotency-key"))
                if len(state["keys"]) == 1:
                    response = await route.fetch()
                    assert response.status == 200, await response.text()
                    state["receipt"] = (await response.json())["queue_request_id"]
                    await route.abort("failed")
                else:
                    await route.continue_()

            await page.route(f"**{start_path}", handler)
            await button(page, "Import starten").click()
            await expect(page.get_by_text("Der Vorgang konnte nicht abgeschlossen werden.", exact=True)).to_be_visible()
            async with page.expect_response(
                    lambda response: response.request.method == "POST" and urlsplit(response.url).path == start_path) as pending:
                await button(page, "Import starten").click()
            answer = await pending.value
            assert answer.status == 200, await answer.text()
            await page.unroute(f"**{start_path}", handler)
            assert len(state["keys"]) == 2 and state["keys"][0] == state["keys"][1], state["keys"]
            assert state["keys"][0], "the start must carry a command key"
            browser_world.world.data["start_keys"] = state["keys"]
            browser_world.world.data["receipt"] = state["receipt"]
            await page.wait_for_function(
                "() => [...document.querySelectorAll('button')].some(button => button.textContent === 'Import starten' && button.disabled)")
        finally:
            await context.close()
    browser_world.run(run())


@then("the import is queued with its receipt")
def import_queued(browser_world):
    world = browser_world.world
    client = signed_in_client(world, *CLERK)
    try:
        job = world.look(client, f"/api/declaration-imports/{world.data['job']}").json()
        assert job["status"] == "QUEUED", job
        assert job["queue_request_id"] == world.data["receipt"], job
    finally:
        world.run(client.aclose())


def set_clerk_role(world, role):
    answer = world.act("PATCH", f"/api/users/{world.data['clerk_id']}", json={"role": role})
    assert answer.status_code == 200, world.describe_response()


@when("the administrator revokes the clerk's permission")
def revoke_permission(browser_world, request):
    world = browser_world.world
    answer = world.act("GET", "/api/users/page?q=clerk%40alpha.de")
    assert answer.status_code == 200, world.describe_response()
    world.data["clerk_id"] = answer.json()["items"][0]["id"]
    set_clerk_role(world, "READ_ONLY")
    request.addfinalizer(lambda: set_clerk_role(world, "CLERK"))


@then("the import is durably cancelled")
def import_cancelled(browser_world):
    from app.core.db import get_worker_tenant_session
    from app.repositories.declaration_import_repo import DeclarationImportRepository

    world = browser_world.world
    org = world.look(world.actor, "/api/organizations/me").json()["id"]

    async def claim():
        async with get_worker_tenant_session(uuid.UUID(org)) as session:
            return await DeclarationImportRepository(session, uuid.UUID(org)).claim_next(
                "bdd-principal", datetime.now(timezone.utc), 120)

    lease = world.run(claim())
    assert lease is None or str(lease.import_id) != str(world.data["job"]), "a revoked principal's job must not be claimable"
    job_path = f"/api/declaration-imports/{world.data['job']}"
    client = signed_in_client(world, *CLERK)
    try:
        assert world.look(client, job_path).status_code == 403, "the revoked clerk must not read the job"
        set_clerk_role(world, "CLERK")
        job = world.look(client, job_path).json()
        assert job["status"] == "CANCELLED" and job["error_code"] == "JOB_PERMISSION_CHANGED", job
    finally:
        world.run(client.aclose())


@then("the clerk's page explains the cancellation")
def page_explains(browser_world):
    async def run():
        context, page = await sign_in_context(browser_world, *CLERK)
        try:
            await page.goto(f"/teilungserklaerung/import/{browser_world.world.data['job']}")
            await expect(page.get_by_text("Berechtigung des auslösenden Benutzers gilt nicht mehr")).to_be_visible()
            await expect(button(page, "Erneut versuchen")).to_be_visible()
        finally:
            await context.close()
    browser_world.run(run())


@then("the clerk's page fits a 390 pixel wide viewport")
def cancelled_page_fits_mobile(browser_world):
    async def run():
        context, page = await sign_in_context(browser_world, *CLERK)
        try:
            await page.goto(f"/teilungserklaerung/import/{browser_world.world.data['job']}")
            await expect(button(page, "Erneut versuchen")).to_be_visible()
            await assert_fits_mobile(page)
        finally:
            await context.close()
    browser_world.run(run())


@when("the clerk retries the import")
def clerk_retries(browser_world):
    async def run():
        context, page = await sign_in_context(browser_world, *CLERK)
        try:
            await page.goto(f"/teilungserklaerung/import/{browser_world.world.data['job']}")
            retry_path = f"/api/declaration-imports/{browser_world.world.data['job']}/retry"
            async with page.expect_response(
                    lambda response: response.request.method == "POST" and urlsplit(response.url).path == retry_path) as pending:
                await button(page, "Erneut versuchen").click()
            answer = await pending.value
            assert answer.status == 200, await answer.text()
            retried = await answer.json()
            assert retried["status"] == "QUEUED", retried
            assert retried["queue_request_id"] != browser_world.world.data["receipt"], "a retry creates a new receipt"
            browser_world.world.data["retry_receipt"] = retried["queue_request_id"]
        finally:
            await context.close()
    browser_world.run(run())


@then("the retry creates a new receipt while the old command acknowledges the original")
def replay_acknowledges(browser_world):
    world = browser_world.world
    client = signed_in_client(world, *CLERK)
    try:
        replay = world.run(client.post(
            f"/api/declaration-imports/{world.data['job']}/start",
            headers={"Idempotency-Key": world.data["start_keys"][0]}))
        assert replay.status_code == 200, replay.text
        replayed = replay.json()
        assert replayed["command_receipt_id"] == world.data["receipt"], replayed
        assert replayed["queue_request_id"] == world.data["retry_receipt"], replayed
    finally:
        world.run(client.aclose())
