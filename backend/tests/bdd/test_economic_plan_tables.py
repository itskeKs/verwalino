"""Real calculation and geometry for the economic-plan output tables."""
import os
from pathlib import Path

from playwright.async_api import expect
from pytest_bdd import given, parsers, scenarios, then, when

from tests.bdd.steps.browser_setup import create
from tests.bdd.steps.records import create_person

scenarios(str(Path(__file__).resolve().parents[3] / "features/economic_plan_tables.feature"))


@given("eight equal WEG units with long owner names and five-digit planned costs", target_fixture="table_plan")
def table_plan(world, seed_data):
    return create_table_plan(world, seed_data)


@given("one WEG owner holds Vorderhaus and Hinterhaus with unequal shares", target_fixture="table_plan")
def two_unit_plan(world, seed_data):
    return create_table_plan(world, seed_data, shared_owner=True)


def create_table_plan(world, seed_data, shared_owner=False):
    world.actor = world.administrator_of("Verwaltung Alpha")
    world.actor_organization = "Verwaltung Alpha"
    user = str(seed_data["user_a_admin_id"])
    grants = [{"user_id": user, "capabilities": ["read", "prepare", "approve", "export"],
               "reason": "Synthetic economic plan"}]
    home = create(world, "/api/properties", {
        "name": "WEG Mustergarten", "management_type": "WEG", "mea_denominator": 1000,
        "data_audience": {"label": "Mustergarten", "reason": "Synthetic economic plan", "grants": grants},
    }, keyed=True)
    owner = None
    units = []
    unit_specs = [("01", "Vorderhaus", "250"), ("02", "Hinterhaus", "750")] if shared_owner else [
        (f"Wohnung {number:02}", "", "125") for number in range(1, 9)]
    for number, location, mea in unit_specs:
        unit = create(world, "/api/units", {"property_id": home["id"], "number": number,
            "location": location, "usage_type": "WOHNUNG", "mea": mea, "area": "100"})
        units.append(unit)
        if not shared_owner or owner is None:
            person = create_person(world, "Verwaltung Alpha", f"Friedrich-Wilhelm Muster-Beispielhausen-{len(units):02}")
            owner = create(world, "/api/owner-profiles", {"person_id": person.id})
        create(world, "/api/ownership", {"unit_id": unit["id"], "owner_profile_id": owner["id"],
            "valid_from": "2020-01-01"})
    scope = create(world, "/api/accounting-scopes", {"property_id": home["id"], "scope_type": "WEG",
        "label": "WEG Mustergarten", "valid_from": "2026-01-01", "whole_property": True}, keyed=True)
    create(world, f"/api/accounting-scopes/{scope['id']}/grants", {"user_id": user,
        "capabilities": ["read", "prepare", "approve", "confirm", "export"],
        "reason": "Synthetic economic plan"}, keyed=True)
    plan = create(world, "/api/economic-plans", {"property_id": home["id"], "accounting_scope_id": scope["id"],
        "plan_year": 2026, "period_start": "2026-01-01", "period_end": "2026-12-31"})
    category = next(row for row in world.act("GET", "/api/cost-categories").json() if row["code"] == "GRUNDSTEUER")
    return {"id": plan["id"], "category": category, "units": units, "shared_owner": shared_owner,
            "cost": "1200.00" if shared_owner else "12345.67", "reserve": "0" if shared_owner else "12345.67"}


@when(parsers.parse("I calculate and display their {state} economic plan"))
def display_plan(browser_world, world, table_plan, state):
    assert state in ("calculated", "finalized")
    b, w = browser_world, table_plan

    async def calculate():
        await b.page.set_viewport_size({"width": 360, "height": 900})
        await b.visit(f"/wirtschaftsplaene/{w['id']}")
        await b.page.get_by_role("button", name="+ Position", exact=True).click()
        await b.page.get_by_role("combobox", name="Kategorie 1", exact=True).select_option(w["category"]["code"])
        await b.page.get_by_label(f"{w['category']['label']} – Jahresbetrag", exact=True).fill(w["cost"])
        await b.page.get_by_label("Rücklagenzuführung (Jahr)", exact=True).fill(w["reserve"])
        await b.page.get_by_label("Grundlage der Prüfung", exact=True).fill("Synthetic fixture: complete period records checked")
        await b.page.get_by_role("checkbox").check()
        await b.page.get_by_role("button", name="Datengrundlage speichern", exact=True).click()
        await expect(b.page.get_by_role("status")).to_contain_text("Prüfung gespeichert.")
        await b.page.get_by_role("button", name="Berechnen", exact=True).click()
        await expect(b.page.get_by_role("heading", name="Hausgeld-Vorschüsse je Eigentümer", exact=True)).to_be_visible()
        if state == "finalized":
            b.page.once("dialog", lambda dialog: dialog.accept())
            await b.page.get_by_role("button", name="Finalisieren", exact=True).click()
            await expect(b.page.get_by_role("heading", name="Wirtschaftsplan 2026 Finalisiert", exact=True)).to_be_visible()

    b.run(calculate())
    response = world.act("GET", f"/api/economic-plans/{w['id']}")
    assert response.status_code == 200, response.text
    w.update(state=state, saved=response.json())
    owners = [line for line in w["saved"]["lines"] if line["owner_profile_id"]]
    if w["shared_owner"]:
        assert {line["unit_id"]: (line["planned_amount"], line["monthly_amount"]) for line in owners} == {
            w["units"][0]["id"]: ("300.00", "25.00"), w["units"][1]["id"]: ("900.00", "75.00")}
        return
    # 24,691.34 / 8: two shares of 3,086.41 and six of 3,086.42.
    assert sorted(line["planned_amount"] for line in owners) == ["3086.41"] * 2 + ["3086.42"] * 6
    assert [line["monthly_amount"] for line in owners] == ["257.21"] * 8


@then("both economic plan tables scroll independently on a 360 pixel phone and retain their desktop values")
def contained_tables(browser_world, world, table_plan):
    b, w = browser_world, table_plan
    failures, console_errors = [], []

    async def capture(width, suffix=""):
        if os.environ.get("VERWALINO_CAPTURE_DESIGN") == "1":
            directory = Path(__file__).resolve().parents[3] / "docs/assets/design/economic-plan-tables"
            directory.mkdir(parents=True, exist_ok=True)
            await b.page.screenshot(path=str(directory / f"{w['state']}{suffix}-after-{width}.png"), animations="disabled")

    async def check():
        b.page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        assert b.page.url.endswith(f"/wirtschaftsplaene/{w['id']}")
        assert await b.page.title()
        tables = [b.page.get_by_role("heading", name=heading, exact=True).locator("..").locator("table")
                  for heading in ("Hausgeld-Vorschüsse je Eigentümer", "Geplante Kosten (berechnet)")]
        expected_rows = None
        for width in (360, 1280):
            await b.page.set_viewport_size({"width": width, "height": 900})
            if width == 360:
                await b.page.wait_for_function("() => { const aside = document.querySelector('aside'); return !aside || aside.getBoundingClientRect().right <= 0; }")
            await tables[0].evaluate("el => el.closest('.card').scrollIntoView({block: 'start'})")
            await capture(width)
            geometry = await b.page.locator("main").evaluate("el => ({width: el.clientWidth, content: el.scrollWidth})")
            if geometry["content"] > geometry["width"]:
                failures.append(f"{width}: main content overflows: {geometry}")
            if w["state"] == "finalized":
                digest = b.page.locator("code").filter(has_text=w["saved"]["snapshot_sha256"])
                hash_geometry = await digest.evaluate("""el => {
                    const range = document.createRange(); range.selectNodeContents(el);
                    const box = range.getBoundingClientRect(), card = el.closest('.card').getBoundingClientRect();
                    return {right: box.right, cardRight: card.right, width: box.width};
                }""")
                if hash_geometry["right"] > hash_geometry["cardRight"]:
                    failures.append(f"{width}: snapshot hash extends beyond its card: {hash_geometry}")
            rows = [await table.locator("tbody tr").all_text_contents() for table in tables]
            assert len(rows[0]) == 8 and len(rows[1]) == 2
            assert any("Friedrich-Wilhelm Muster-Beispielhausen-05" in row for row in rows[0])
            assert all("12345.67 EUR" in row for row in rows[1])
            assert len(await tables[0].get_by_role("columnheader").all()) == 4
            assert len(await tables[1].get_by_role("columnheader").all()) == 3
            if expected_rows is None:
                expected_rows = rows
            assert rows == expected_rows, "Desktop values differ from phone values"
            for index, table in enumerate(tables):
                container = table.locator("..")
                metrics = await container.evaluate("""el => {
                    const box = el.getBoundingClientRect(), card = el.closest('.card').getBoundingClientRect();
                    return {overflow: getComputedStyle(el).overflowX, width: el.clientWidth,
                        content: el.scrollWidth, contained: box.left >= card.left && box.right <= card.right};
                }""")
                if metrics["overflow"] != "auto" or not metrics["contained"]:
                    failures.append(f"{width}: table {index} lacks bounded scrolling: {metrics}")
                if width == 360 and metrics["content"] <= metrics["width"]:
                    failures.append(f"{width}: table {index} has no local horizontal range")
                if width == 1280 and metrics["content"] > metrics["width"]:
                    failures.append(f"{width}: desktop table {index} needs horizontal scrolling")
                amounts = table.locator("tbody td").filter(has_text="EUR")
                for amount in await amounts.all():
                    if not await amount.evaluate("""el => {
                        const range = document.createRange(); range.selectNodeContents(el);
                        return new Set(Array.from(range.getClientRects(), r => r.top)).size === 1;
                    }"""):
                        failures.append(f"{width}: table {index} breaks a monetary value")
                    await container.evaluate("(el, cell) => { el.scrollLeft += cell.getBoundingClientRect().right - el.getBoundingClientRect().right; }", await amount.element_handle())
                    if not await amount.evaluate("""el => {
                        const range = document.createRange(); range.selectNodeContents(el);
                        const box = range.getBoundingClientRect(), clip = el.closest('table').parentElement.getBoundingClientRect();
                        return box.left >= clip.left && box.right <= clip.right + 1;
                    }"""):
                        failures.append(f"{width}: table {index} cannot reach a complete amount")
                await container.evaluate("el => { el.scrollLeft = 0; }")
            if width == 360:
                await tables[0].locator("..").evaluate("el => { el.scrollLeft = el.scrollWidth; }")
                first_left = await tables[0].locator("..").evaluate("el => el.scrollLeft")
                second_left = await tables[1].locator("..").evaluate("el => el.scrollLeft")
                if first_left <= 0 or second_left != 0:
                    failures.append("Owner table does not scroll independently")
                await tables[1].locator("..").evaluate("el => { el.scrollLeft = el.scrollWidth; }")
                if await tables[0].locator("..").evaluate("el => el.scrollLeft") != first_left:
                    failures.append("Cost scrolling moves the owner table")
                if await b.page.locator("main").evaluate("el => el.scrollLeft") != 0:
                    failures.append("Table scrolling moves the page sideways")
                await capture(width, "-amounts")
            for table in tables:
                await table.locator("..").evaluate("el => { el.scrollLeft = 0; }")
            if width == 360:
                await tables[1].evaluate("el => el.closest('.card').scrollIntoView({block: 'start'})")
                await capture(width, "-costs")
                await tables[1].locator("..").evaluate("el => { el.scrollLeft = el.scrollWidth; }")
                await capture(width, "-cost-amounts")
        assert not failures, "\n".join(failures)
        assert not console_errors and not b.errors, (console_errors, b.errors)

    b.run(check())
    assert world.act("GET", f"/api/economic-plans/{w['id']}").json() == w["saved"], "Viewing changed the saved plan"


@then("each unit identifies its own annual and monthly advance on desktop and phone")
def identified_advances(browser_world, world, table_plan):
    b, w = browser_world, table_plan

    async def check():
        console_errors = []
        b.page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        table = b.page.get_by_role("heading", name="Hausgeld-Vorschüsse je Eigentümer", exact=True).locator("..").locator("table")
        failures = []
        expected = [("01 – Vorderhaus", "300.00 EUR", "25.00 EUR"), ("02 – Hinterhaus", "900.00 EUR", "75.00 EUR")]
        for width in (1280, 360):
            await b.page.set_viewport_size({"width": width, "height": 900})
            if width == 360:
                await b.page.wait_for_function("() => { const aside = document.querySelector('aside'); return !aside || aside.getBoundingClientRect().right <= 0; }")
            await table.evaluate("el => el.closest('.card').scrollIntoView({block: 'start'})")
            phase = os.environ.get("VERWALINO_CAPTURE_UNIT_IDENTITY")
            if phase in ("before", "after"):
                directory = Path(__file__).resolve().parents[3] / "docs/assets/design/economic-plan-unit-identity"
                directory.mkdir(parents=True, exist_ok=True)
                await b.page.screenshot(path=str(directory / f"{w['state']}-{phase}-{width}.png"), animations="disabled")
            assert b.page.url.endswith(f"/wirtschaftsplaene/{w['id']}") and await b.page.title()
            assert await table.locator("tbody tr").count() == 2
            for label, annual, monthly in expected:
                row = table.locator("tbody tr").filter(has=b.page.get_by_text(annual, exact=True))
                assert await row.count() == 1
                await expect(row).to_contain_text("Friedrich-Wilhelm Muster-Beispielhausen-01")
                await expect(row).to_contain_text(monthly)
                if f"Einheit {label}" not in await row.inner_text():
                    failures.append(f"{width}: {label} is not associated with {annual} / {monthly}")
                await expect(row).to_be_visible()
            geometry = await b.page.locator("main").evaluate("el => ({width: el.clientWidth, content: el.scrollWidth})")
            assert geometry["content"] <= geometry["width"], geometry
            if width == 360:
                await table.locator("..").evaluate("el => { el.scrollLeft = el.scrollWidth; }")
                for amount in await table.locator("tbody td").filter(has_text="EUR").all():
                    assert await amount.evaluate("""el => {
                        const range = document.createRange(); range.selectNodeContents(el);
                        const box = range.getBoundingClientRect(), clip = el.closest('table').parentElement.getBoundingClientRect();
                        return box.left >= clip.left && box.right <= clip.right + 1;
                    }"""), "A unit's complete amount is unreachable"
                if phase in ("before", "after"):
                    await b.page.screenshot(path=str(directory / f"{w['state']}-amounts-{phase}-{width}.png"), animations="disabled")
                await table.locator("..").evaluate("el => { el.scrollLeft = 0; }")
        assert not failures, "\n".join(failures)
        assert not console_errors and not b.errors, (console_errors, b.errors)

    b.run(check())
    assert world.act("GET", f"/api/economic-plans/{w['id']}").json() == w["saved"], "Viewing changed the saved plan"
