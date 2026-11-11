"""Legacy payment review: provable rows only, replay-safe apply, honest revocation."""
import hashlib

from playwright.async_api import expect
from pytest_bdd import given, then, when

from tests.bdd.steps.browser_common import (
    button,
    expect_reply_replay,
    field,
    lose_first_committed_reply,
    wait_scope_usable,
)
from tests.bdd.steps.browser_setup import create


def unique(world, prefix):
    return f"{prefix} {hashlib.sha256(world.data['scenario'].encode()).hexdigest()[:8]}"


@given("a legacy bank account with two rent receipts and a reviewed scope exists")
def legacy_payments(world, seed_data):
    user_id = str(seed_data["user_a_admin_id"])
    home = create(world, "/api/properties", {
        "name": unique(world, "Legacy payment QA"), "management_type": "MIET",
        "street": "Test 1", "zip_code": "12345", "city": "Test",
    })
    bank = create(world, "/api/bank-accounts", {
        "property_id": home["id"], "account_type": "LAUFEND", "label": "Legacy QA bank",
    })
    person = create(world, "/api/persons", {"last_name": unique(world, "Legacy payment QA")})
    tenant = create(world, "/api/tenant-profiles", {"person_id": person["id"]})
    booked = []
    for value_date, amount in (("2025-06-01", "100.00"), ("2024-06-01", "200.00")):
        payment = create(world, "/api/payments", {
            "bank_account_id": bank["id"], "tenant_profile_id": tenant["id"], "direction": "IN",
            "amount": amount, "value_date": value_date, "payment_type": "KALTMIETE",
            "period_year": 2025, "period_month": 6,
        })
        answer = world.act("POST", f"/api/payments/{payment['id']}/book")
        assert answer.status_code == 200, world.describe_response()
        booked.append(answer.json())
    scope = create(world, "/api/accounting-scopes", {
        "property_id": home["id"], "scope_type": "MIET", "label": unique(world, "Legacy payment review"),
        "valid_from": "2025-01-01", "valid_to": "2025-12-31", "whole_property": True,
    }, keyed=True)
    grant = create(world, f"/api/accounting-scopes/{scope['id']}/grants", {
        "user_id": user_id, "capabilities": ["read", "prepare"], "reason": "Synthetic review grant",
    }, keyed=True)
    wait_scope_usable(world, scope["id"])
    create(world, f"/api/accounting-scopes/{scope['id']}/bindings", {
        "resource_type": "bank_account", "resource_id": bank["id"], "reason": "Synthetic reviewed original bank",
    })
    world.data.update(scope=scope, grant=grant, booked=booked, bank=bank)


@when("the manager opens the legacy-payment review for the scope")
def open_review(browser_world):
    async def run():
        page = browser_world.page
        await browser_world.visit(f"/berechtigungen/teamrechte/{browser_world.world.data['scope']['id']}")
        await button(page, "Altzahlungen prüfen").click()
    browser_world.run(run())


@then("the proven receipt is selectable and the historical one is blocked with a reason")
def review_rows(browser_world):
    async def run():
        page = browser_world.page
        proven = page.get_by_role("checkbox", name="Zahlung Legacy QA bank 2025-06-01 auswählen", exact=True)
        await expect(proven).to_be_visible()
        blocked = page.get_by_role("checkbox", name="Zahlung Legacy QA bank 2024-06-01 auswählen", exact=True)
        assert await blocked.is_disabled(), "a payment outside the scope period must not be selectable"
        await expect(page.get_by_text("Zeitraum, Mandat oder Zahlungsart passen nicht zum Kreis", exact=True)).to_be_visible()
    browser_world.run(run())


@when("the manager applies the proven receipt with a reason and the first committed reply is lost")
def apply_review(browser_world):
    async def run():
        page = browser_world.page
        world = browser_world.world
        proven = page.get_by_role("checkbox", name="Zahlung Legacy QA bank 2025-06-01 auswählen", exact=True)
        await proven.check()
        await field(page, "Begründung der Übernahme").fill("Originalkonto und Buchungen geprüft")
        path = f"/api/accounting-scopes/{world.data['scope']['id']}/legacy-payments"
        state = {"count": 0}
        handler = lose_first_committed_reply(page, state, path, "Antwort nach erfolgreicher Übernahme unterbrochen")
        await page.route(f"**{path}", handler)
        await button(page, "Auswahl verbindlich zuordnen").click()
        await expect(page.get_by_role("alert").filter(has_text=state["detail"])).to_be_visible()
        assert await proven.is_checked(), "the lost reply must keep the selection"
        applied = await expect_reply_replay(page, state, path, "Auswahl verbindlich zuordnen")
        assert applied["items"], applied
        await expect(page.get_by_text("1 Zahlungen wurden zugeordnet. Die ursprünglichen Buchungen bleiben erhalten.", exact=True)).to_be_visible()
    browser_world.run(run())


@then("the review reports one assigned payment and the original bookings remain")
def applied_once(browser_world):
    world = browser_world.world
    for payment in world.data["booked"]:
        answer = world.look(world.actor, f"/api/payments/{payment['id']}")
        assert answer.status_code == 200, world.describe_response()
        assert answer.json() == payment, "the original booking must stay unchanged"
    remaining = world.look(world.actor, f"/api/accounting-scopes/{world.data['scope']['id']}/legacy-payments").json()["items"]
    assert len(remaining) == 1 and remaining[0]["id"] == world.data["booked"][1]["id"], remaining
    assert remaining[0]["proof_status"] == "review_required", remaining


@when("the manager's grant is revoked and the preview is reloaded")
def revoke_and_reload(browser_world):
    world = browser_world.world
    answer = world.act("POST", f"/api/accounting-scopes/{world.data['scope']['id']}/grants/{world.data['grant']['id']}/revoke")
    assert answer.status_code == 204, world.describe_response()

    async def run():
        await button(browser_world.page, "Vorschau neu laden").click()
    browser_world.run(run())


@then("the review is unavailable without an empty-success claim")
def review_unavailable(browser_world):
    async def run():
        page = browser_world.page
        await expect(page.get_by_role("alert").filter(has_text="Vorschau nicht verfügbar")).to_be_visible()
        assert await page.get_by_text("Keine weiteren Altzahlungen", exact=False).count() == 0
    browser_world.run(run())
