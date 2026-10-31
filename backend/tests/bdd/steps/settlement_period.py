from decimal import Decimal
from pytest_bdd import given, when, then, parsers
from tests.settlement_period_support import period_property, booked, statement, create
from tests.settlement_period_support import period_data_cleanup  # noqa: F401
from tests.financial_coverage_helpers import attest_fixture
from tests.bdd.steps.browser_common import browser_step


@given("a mixed property with a tenancy and an April settlement start")
def april_property(world, period_data_cleanup):
    world.data["period_property"] = world.run(period_property(world.actor))


@given("advances booked for December 2025 and January 2026")
def cross_year_advances(world):
    for year, month in ((2025, 12), (2026, 1)):
        world.run(booked(world.actor, world.data["period_property"], year, month))


@when("the manager chooses the offered statement period 2025/2026")
def choose_period(world):
    data = world.data["period_property"]
    answer = world.act("GET", f"/api/properties/{data['property']['id']}/settlement-periods", params={"year": 2025})
    assert answer.status_code == 200, world.describe_response()
    offer = next(p for p in answer.json() if p["label"] == "2025/2026")
    answer = world.act("POST", "/api/settlements", json=statement(data, offer["period_start"], offer["period_end"]))
    assert answer.status_code == 201, world.describe_response()
    world.data["period_run"] = answer.json()


@then("the draft covers 1 April 2025 through 31 March 2026")
def draft_period(world):
    run = world.data["period_run"]
    assert (run["period_start"], run["period_end"]) == ("2025-04-01", "2026-03-31")


@then("both booked advances are credited")
def credited(world):
    answer = world.act("PATCH", f"/api/settlements/{world.data['period_run']['id']}/advance-payments-derived", params={"period_year": 2026})
    assert answer.status_code == 200, world.describe_response()
    assert Decimal(answer.json()["advance_payments_total"]) == Decimal("200.00")


@when("the manager finalizes the statement")
def finalize_statement(world):
    run_id = world.data["period_run"]["id"]
    world.run(attest_fixture(world.actor, "settlement", run_id,
        {"advance_payments_total": "200.00", "advance_payments_source": "DERIVED"}))
    answer = world.act("POST", f"/api/settlements/{run_id}/finalize", json={})
    assert answer.status_code == 200, world.describe_response()


@when("the manager tries to create an overlapping statement for that tenancy")
def overlapping_statement(world):
    world.act("POST", "/api/settlements", json=statement(world.data["period_property"], "2026-01-01", "2026-12-31"))


@then("the overlapping statement is refused")
def refused(world):
    assert world.response.status_code == 409, world.describe_response()


@when("the manager creates the property's economic plan for 2025")
def create_plan(world):
    world.act("POST", "/api/economic-plans", json={"property_id": world.data["period_property"]["property"]["id"],
        "plan_year": 2025})


@then("the plan covers 1 April 2025 through 31 March 2026")
def plan_period(world):
    assert world.response.status_code == 201, world.describe_response()
    plan = world.response.json()
    assert (plan["plan_year"], plan["period_start"], plan["period_end"]) == (2025, "2025-04-01", "2026-03-31")


@given("a calendar-year property where tenant A leaves on 30 September and tenant B moves in on 1 October")
def successive_tenants(world, period_data_cleanup):
    data = world.run(period_property(world.actor, month=1))
    changed = world.act("PATCH", f"/api/leases/{data['lease']['id']}", json={"valid_to": "2025-09-30"})
    assert changed.status_code == 200, world.describe_response()
    person = world.run(create(world.actor, "/api/persons", {"last_name": "Nachmieter B"}))
    tenant = world.run(create(world.actor, "/api/tenant-profiles", {"person_id": person["id"]}))
    lease = world.run(create(world.actor, "/api/leases", {
        "unit_id": data["unit"]["id"], "tenant_profile_id": tenant["id"],
        "valid_from": "2025-10-01", "base_rent": "700.00", "advance_payment": "100.00",
    }))
    world.data["successive_tenants"] = {"A": data, "B": {**data, "tenant": tenant, "lease": lease}}


def finalize_tenant_statement(world, data, start, end):
    run = world.run(create(world.actor, "/api/settlements", {
        **statement(data, start, end), "advance_payments_total": "0.00",
    }))
    finalize_zero_advance_statement(world, run["id"])
    return world.response.json()


def finalize_zero_advance_statement(world, run_id):
    world.run(attest_fixture(world.actor, "settlement", run_id, {
        "advance_payments_total": "0.00", "advance_payments_source": "MANUAL",
    }))
    answer = world.act("POST", f"/api/settlements/{run_id}/finalize", json={})
    assert answer.status_code == 200, world.describe_response()


@when("the manager finalizes statements for each tenant's occupied months")
def finalize_occupied_months(world):
    tenants = world.data["successive_tenants"]
    world.data["tenant_statements"] = {
        "A": finalize_tenant_statement(world, tenants["A"], "2025-01-01", "2025-09-30"),
        "B": finalize_tenant_statement(world, tenants["B"], "2025-10-01", "2025-12-31"),
    }


@then("A's statement covers January through September and B's covers October through December")
def occupied_statement_dates(world):
    runs = world.data["tenant_statements"]
    assert (runs["A"]["period_start"], runs["A"]["period_end"]) == ("2025-01-01", "2025-09-30")
    assert (runs["B"]["period_start"], runs["B"]["period_end"]) == ("2025-10-01", "2025-12-31")


@when("the manager tries to create a calendar-year statement for tenant A")
def former_tenant_calendar_statement(world):
    world.act("POST", "/api/settlements", json=statement(world.data["successive_tenants"]["A"], "2025-01-01", "2025-12-31"))


@when("the manager creates a property-period statement for tenant A")
def clipped_property_statement(world):
    former_tenant_calendar_statement(world)


@then("the created statement ends on 30 September")
def clipped_property_dates(world):
    assert world.response.status_code == 201, world.describe_response()
    assert (world.response.json()["period_start"], world.response.json()["period_end"]) == ("2025-01-01", "2025-09-30")


@when("the manager tries to create a free statement extending beyond tenant A's lease")
def outside_lease_statement(world):
    world.act("POST", "/api/settlements", json=statement(world.data["successive_tenants"]["A"], "2025-01-01", "2025-10-01"))


@then("the statement is refused with a German lease-period error")
def outside_lease_error(world):
    assert world.response.status_code == 422, world.describe_response()
    assert world.response.json()["detail"] == {
        "code": "settlement_period_outside_lease",
        "message": "Der Abrechnungszeitraum muss innerhalb der Mietdauer liegen.",
    }


@when("the manager opens the statement picker for tenant A")
@browser_step
async def open_lease_picker(browser_world):
    data = browser_world.world.data["successive_tenants"]["A"]
    browser_world.page.on("console", lambda message: browser_world.errors.append(message.text)
                          if message.type == "error" else None)
    await browser_world.visit("/abrechnungen")
    await browser_world.page.get_by_role("button", name="+ Neue Abrechnung", exact=True).click()
    await browser_world.select("Objekt *", data["property"]["id"])
    await browser_world.select("Mietvertrag *", data["lease"]["id"])


@then("the offered and selected statement period ends on 30 September at desktop and mobile widths")
@browser_step
async def clipped_picker_widths(browser_world, request):
    from playwright.async_api import expect
    from tests.bdd.report import artifact_directory, attach_artifact

    page = browser_world.page
    assert page.url.endswith("/abrechnungen")
    assert await page.title() == "Hausverwaltung"
    await expect(page.get_by_label("Abrechnungszeitraum von *", exact=True)).to_have_value("2025-01-01")
    await expect(page.get_by_label("Abrechnungszeitraum bis *", exact=True)).to_have_value("2025-09-30")
    picker = page.get_by_role("combobox", name="Abrechnungszeitraum", exact=True)
    await expect(picker).to_contain_text("2025 · 01.01.2025 – 30.09.2025")
    # Explicit selection restores the clipped offer after a free edit.
    await page.get_by_label("Abrechnungszeitraum bis *", exact=True).fill("2025-08-31")
    await picker.select_option("2025-01-01")
    await expect(page.get_by_label("Abrechnungszeitraum bis *", exact=True)).to_have_value("2025-09-30")
    directory = artifact_directory(request.node)
    for width in (1280, 360):
        await page.set_viewport_size({"width": width, "height": 1000})
        if width == 360:
            await page.wait_for_function("document.querySelector('aside').getBoundingClientRect().right <= 0")
        await picker.scroll_into_view_if_needed()
        assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        if directory:
            capture = directory / f"statement-after-{width}.png"
            await page.screenshot(path=str(capture), full_page=True, animations="disabled")
            attach_artifact(request.node, capture)


@when("the manager repeats A's January through September statement")
def repeat_former_tenant_statement(world):
    world.data["repeated_tenant_statement"] = world.run(create(world.actor, "/api/settlements", {
        **statement(world.data["successive_tenants"]["A"], "2025-01-01", "2025-09-30"),
        "advance_payments_total": "0.00",
    }))


@then("the identical statement can be finalized again")
def finalize_repeated_statement(world):
    run = world.data["repeated_tenant_statement"]
    assert run["id"] != world.data["tenant_statements"]["A"]["id"]
    finalize_zero_advance_statement(world, run["id"])


@given("annual operating costs of 3650 euros for that unit")
def annual_operating_cost(world):
    categories = world.act("GET", "/api/cost-categories")
    assert categories.status_code == 200, world.describe_response()
    category = next(c for c in categories.json() if c["code"] == "GRUNDSTEUER")
    entry = world.run(create(world.actor, "/api/cost-entries", {
        "property_id": world.data["successive_tenants"]["A"]["property"]["id"],
        "cost_category_id": category["id"], "invoice_date": "2025-01-01",
        "service_period_from": "2025-01-01", "service_period_to": "2025-12-31",
        "gross_amount": "3650.00", "tenant_distribution_key": "WOHNFLAECHE",
        "tenant_apportionable": "UMLAGEFAEHIG",
    }))
    answer = world.act("POST", f"/api/cost-entries/{entry['id']}/confirm")
    assert answer.status_code == 200, world.describe_response()


@when("the manager finalizes clipped property-period calculations for both tenants")
def finalize_calendar_calculations(world):
    world.data["tenant_statements"] = {
        tenant: finalize_tenant_statement(world, data, "2025-01-01", "2025-12-31")
        for tenant, data in world.data["successive_tenants"].items()
    }


@then("A pays 2730 euros for January through September and B pays 920 euros for October through December")
def successive_tenant_shares(world):
    for tenant, amount, days in (("A", "2730.00", 273), ("B", "920.00", 92)):
        run = world.data["tenant_statements"][tenant]
        assert Decimal(run["total_cost"]) == Decimal(amount)
        line, = [line for line in run["lines"] if line["line_type"] == "COST"]
        assert line["time_share_days"] == days
        assert line["time_share_total_days"] == 365


@given("a calendar-year tenancy with a booked yearly advance of 1200 euros")
def yearly_advance(world, period_data_cleanup):
    data = world.run(period_property(world.actor, month=1))
    world.run(booked(world.actor, data, 2025, None, amount="1200.00"))
    world.data["yearly_advance_property"] = data


@when("the manager finalizes statements for January through June and July through December")
def adjacent_yearly_advance_statements(world):
    data = world.data["yearly_advance_property"]
    runs = []
    for start, end in (("2025-01-01", "2025-06-30"), ("2025-07-01", "2025-12-31")):
        run = world.run(create(world.actor, "/api/settlements", statement(data, start, end)))
        derived = world.act("PATCH", f"/api/settlements/{run['id']}/advance-payments-derived", params={"period_year": 2025})
        assert derived.status_code == 200, world.describe_response()
        world.run(attest_fixture(world.actor, "settlement", run["id"], {
            "advance_payments_total": derived.json()["advance_payments_total"], "advance_payments_source": "DERIVED",
        }))
        final = world.act("POST", f"/api/settlements/{run['id']}/finalize", json={})
        assert final.status_code == 200, world.describe_response()
        runs.append(final.json())
    world.data["yearly_advance_statements"] = runs


@then("each statement credits 600 euros of the yearly advance")
def yearly_advance_is_shared(world):
    totals = [Decimal(run["advance_payments_total"]) for run in world.data["yearly_advance_statements"]]
    assert totals == [Decimal("600.00"), Decimal("600.00")]
    assert sum(totals) == Decimal("1200.00")


def finalize_derived_statement(world, data, start, end):
    run = world.run(create(world.actor, "/api/settlements", statement(data, start, end)))
    derived = world.act("PATCH", f"/api/settlements/{run['id']}/advance-payments-derived", params={"period_year": 2025})
    assert derived.status_code == 200, world.describe_response()
    world.run(attest_fixture(world.actor, "settlement", run["id"], {
        "advance_payments_total": derived.json()["advance_payments_total"], "advance_payments_source": "DERIVED"}))
    final = world.act("POST", f"/api/settlements/{run['id']}/finalize", json={})
    assert final.status_code == 200, world.describe_response()
    return final.json()


@given(parsers.parse("an open tenancy with {kind} payments and a finalized first statement"))
def first_advance_statement(world, period_data_cleanup, kind):
    data = world.run(period_property(world.actor))
    monthly = kind == "monthly"
    for month in ([1, 2, 3] if monthly else [None]):
        world.run(booked(world.actor, data, 2025, month, amount="100.00" if monthly else "1200.00"))
    first = finalize_derived_statement(world, data, "2025-01-01" if monthly else "2024-04-01",
        "2025-03-14" if monthly else "2025-03-31")
    assert Decimal(first["advance_payments_total"]) == Decimal("245.16" if monthly else "300.00")
    world.data.update(period_property=data, late_monthly=monthly, late_first=first)


@when("the manager enters the late lease end and finalizes the remaining statement")
def late_lease_end(world):
    data, monthly = world.data["period_property"], world.data["late_monthly"]
    end = "2025-03-20" if monthly else "2025-09-30"
    answer = world.act("PATCH", f"/api/leases/{data['lease']['id']}", json={"valid_to": end})
    assert answer.status_code == 200, world.describe_response()
    world.data["late_second"] = finalize_derived_statement(world, data,
        "2025-03-15" if monthly else "2025-04-01", end)


@then(parsers.parse("the two frozen statements credit {total} euros in total"))
def late_credit_total(world, total):
    first, second = world.data["late_first"], world.data["late_second"]
    assert Decimal(first["advance_payments_total"]) + Decimal(second["advance_payments_total"]) == Decimal(total)
    assert world.act("GET", f"/api/settlements/{first['id']}").json() == first


@given("a computed draft with a July advance and an open tenancy")
def open_lease_notice_draft(world, period_data_cleanup):
    data = world.run(period_property(world.actor, month=1))
    world.run(booked(world.actor, data, 2025, 7, amount="1234.56"))
    run = world.run(create(world.actor, "/api/settlements", statement(data, "2025-01-01", "2025-12-31")))
    world.data.update(period_property=data, notice_run=run)
    compute_notice_draft(world)


def compute_notice_draft(world):
    run = world.data["notice_run"]
    answer = world.act("PATCH", f"/api/settlements/{run['id']}/advance-payments-derived", params={"period_year": 2025})
    assert answer.status_code == 200, world.describe_response()
    world.run(attest_fixture(world.actor, "settlement", run["id"], {
        "advance_payments_total": answer.json()["advance_payments_total"], "advance_payments_source": "DERIVED"}))
    answer = world.act("POST", f"/api/settlements/{run['id']}/compute", json={})
    assert answer.status_code == 200, world.describe_response()


async def capture_notice(browser, request, stage):
    from playwright.async_api import expect
    from tests.bdd.report import artifact_directory, attach_artifact

    await browser.visit(f"/abrechnungen/{browser.world.data['notice_run']['id']}")
    page = browser.page
    await expect(page.get_by_role("heading", name="Berechnete Kostenpositionen")).to_be_visible()
    directory = artifact_directory(request.node)
    for width in (1280, 360):
        await page.set_viewport_size({"width": width, "height": 1000})
        if width == 360:
            await page.wait_for_function("document.querySelector('aside').getBoundingClientRect().right <= 0")
        notice = page.get_by_text("Nicht angerechnet: Zahlungen außerhalb des Mietverhältnisses: 1.234,56 €", exact=True)
        await page.get_by_role("heading", name="Berechnete Kostenpositionen").locator("..").scroll_into_view_if_needed()
        if stage == "after":
            await expect(notice).to_be_visible()
            await notice.scroll_into_view_if_needed()
        else:
            await expect(notice).to_have_count(0)
        assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        if directory:
            capture = directory / f"statement-notice-{stage}-{width}.png"
            await page.screenshot(path=str(capture), full_page=True, animations="disabled")
            attach_artifact(request.node, capture)
    assert not browser.errors


@when("the manager shortens the tenancy to June and recomputes its statement")
def shorten_notice_lease(world, browser_world, request):
    browser_world.run(capture_notice(browser_world, request, "before"))
    data = world.data["period_property"]
    answer = world.act("PATCH", f"/api/leases/{data['lease']['id']}", json={"valid_to": "2025-06-30"})
    assert answer.status_code == 200, world.describe_response()
    compute_notice_draft(world)


@then("the uncredited payment notice is visible on desktop and mobile")
def notice_visible(browser_world, request):
    browser_world.run(capture_notice(browser_world, request, "after"))


@given(parsers.parse("a calendar-year tenancy starting on {start} with {kind} advances"))
def lease_bounded_advances(world, period_data_cleanup, start, kind):
    data = world.run(period_property(world.actor, month=1))
    response = world.act("PATCH", f"/api/leases/{data['lease']['id']}", json={"valid_from": start})
    assert response.status_code == 200, world.describe_response()
    assert kind in {"ten monthly 100", "one year-only 600"}
    for month in (range(3, 13) if kind == "ten monthly 100" else [None]):
        world.run(booked(world.actor, data, 2025, month, amount="100.00" if month else "600.00"))
    world.data["period_property"] = data


@when(parsers.parse("the manager finalizes the lease's clipped statement with derived advances of {total} euros"))
def finalize_lease_bounded_advances(world, total):
    run = world.run(create(world.actor, "/api/settlements", statement(world.data["period_property"], "2025-01-01", "2025-12-31")))
    answer = world.act("PATCH", f"/api/settlements/{run['id']}/advance-payments-derived", params={"period_year": 2025})
    assert answer.status_code == 200, world.describe_response()
    assert Decimal(answer.json()["advance_payments_total"]) == Decimal(total)
    world.run(attest_fixture(world.actor, "settlement", run["id"], {
        "advance_payments_total": total, "advance_payments_source": "DERIVED"}))
    answer = world.act("POST", f"/api/settlements/{run['id']}/finalize", json={})
    assert answer.status_code == 200, world.describe_response()


@then(parsers.parse("the finalized clipped statement credits {total} euros"))
def lease_bounded_advance_total(world, total):
    assert Decimal(world.response.json()["advance_payments_total"]) == Decimal(total)


def confirmed_period_cost(world, data, code, amount, start, end, key):
    categories = world.act("GET", "/api/cost-categories")
    assert categories.status_code == 200, world.describe_response()
    category = next(c for c in categories.json() if c["code"] == code)
    entry = world.run(create(world.actor, "/api/cost-entries", {
        "property_id": data["property"]["id"], "cost_category_id": category["id"],
        "invoice_date": end, "service_period_from": start, "service_period_to": end,
        "gross_amount": amount, "tenant_distribution_key": key, "tenant_apportionable": "UMLAGEFAEHIG",
    }))
    answer = world.act("POST", f"/api/cost-entries/{entry['id']}/confirm")
    assert answer.status_code == 200, world.describe_response()


@given(parsers.parse("two units with annual water quantities of 1000 each and a {invoice} invoice of {amount:d} euros"))
def metered_successive_tenants(world, invoice, amount):
    data = world.data["successive_tenants"]["A"]
    unit = world.run(create(world.actor, "/api/units", {
        "property_id": data["property"]["id"], "number": "2", "usage_type": "WOHNUNG", "area": "70", "mea": "0",
    }))
    world.data["water_meters"] = []
    for item in (data["unit"], unit):
        meter = world.run(create(world.actor, "/api/meters", {
            "property_id": data["property"]["id"], "unit_id": item["id"],
            "meter_number": f"Water-{item['number']}", "meter_type": "KALTWASSER",
        }))
        for day, value in (("2025-01-01", "0"), ("2025-12-31", "1000")):
            world.run(create(world.actor, f"/api/meters/{meter['id']}/readings", {"reading_date": day, "value": value}))
        world.data["water_meters"].append(meter)
    assert invoice in {"water", "December water"}
    confirmed_period_cost(world, data, "KALTWASSER", str(amount),
                          "2025-12-01" if invoice == "December water" else "2025-01-01", "2025-12-31", "VERBRAUCH")


@given(parsers.parse("an interim water reading of 600 on {day}"))
def interim_water(world, day):
    meter = world.data["water_meters"][0]
    world.run(create(world.actor, f"/api/meters/{meter['id']}/readings", {"reading_date": day, "value": "600"}))


@when("the manager requests water suggestions for tenant A")
def water_suggestions(world):
    data = world.data["successive_tenants"]["A"]
    run = world.run(create(world.actor, "/api/settlements", statement(data, "2025-01-01", "2025-12-31")))
    world.act("GET", f"/api/settlements/{run['id']}/consumption-suggestions")


@then("the suggestion explains the day fallback")
def water_fallback_reason(world):
    assert world.response.status_code == 200, world.describe_response()
    unit = world.data["successive_tenants"]["A"]["unit"]["id"]
    row = next(r for r in world.response.json() if r["unit_id"] == unit)
    assert row["consumption"] == "1000.0000"
    assert "Tagen" in (row["reason"] or "")


@then(parsers.parse("A pays {a} euros and B pays {b} euros for water"))
def water_shares(world, a, b):
    assert [Decimal(world.data["tenant_statements"][tenant]["total_cost"]) for tenant in ("A", "B")] == [Decimal(a), Decimal(b)]


@given("a calendar-year tenancy with insurance of 3650 euros for July 2024 through June 2025")
def cross_boundary_insurance(world, period_data_cleanup):
    data = world.run(period_property(world.actor, month=1))
    world.data["period_property"] = data
    confirmed_period_cost(world, data, "GEBAEUDEVERSICHERUNG", "3650.00", "2024-07-01", "2025-06-30", "WOHNFLAECHE")


@when("the manager finalizes property statements for 2024 and 2025")
def cross_boundary_statements(world):
    world.data["cross_boundary_statements"] = [finalize_tenant_statement(
        world, world.data["period_property"], f"{year}-01-01", f"{year}-12-31") for year in (2024, 2025)]


@then("the insurance shares are 1840.00 and 1810.00 euros")
def insurance_shares(world):
    assert [Decimal(run["total_cost"]) for run in world.data["cross_boundary_statements"]] == [Decimal("1840.00"), Decimal("1810.00")]


@given(parsers.parse("a calendar-year tenancy with a {status} legacy statement and a subsequently shortened lease"))
def legacy_statement(world, period_data_cleanup, status):
    data = world.run(period_property(world.actor, month=1))
    run = world.run(create(world.actor, "/api/settlements", {
        **statement(data, "2025-01-01", "2025-12-31"), "advance_payments_total": "0.00",
    }))
    # Seed the historical oversized span before freezing; the edit guard now
    # prevents constructing this legacy state by shortening after finalization.
    answer = world.act("PATCH", f"/api/leases/{data['lease']['id']}", json={"valid_to": "2025-09-30"})
    assert answer.status_code == 200, world.describe_response()
    if status == "finalized":
        finalize_zero_advance_statement(world, run["id"])
    else:
        assert status == "draft"
    world.data.update(period_property=data, legacy_run=run)


@when("the manager creates a calendar-year batch for that property")
def calendar_batch(world):
    world.act("POST", "/api/settlements/batch", json={
        "property_id": world.data["period_property"]["property"]["id"],
        "period_start": "2025-01-01", "period_end": "2025-12-31",
    })


@then("the finalized legacy statement is skipped with the clipped-period reason")
def legacy_skipped(world):
    assert world.response.status_code == 200, world.describe_response()
    result = world.response.json()
    assert result["created"] == result.get("errors", []) == []
    skipped, = result["skipped"]
    assert skipped["settlement_run_id"] == world.data["legacy_run"]["id"]
    assert skipped["reason"] == "Eine finalisierte Abrechnung deckt den gekürzten Mietzeitraum bereits ab."


@then("the draft legacy statement causes an overlap error")
def legacy_draft_error(world):
    assert world.response.status_code == 409, world.describe_response()
    assert world.response.json()["detail"]["code"] == "settlement_period_conflict"


@given("a person-based cost source for that property")
def person_based_cost(world):
    data = world.data["successive_tenants"]["A"]
    confirmed_period_cost(world, data, "MUELLBESEITIGUNG", "1234.56",
                          "2025-01-01", "2025-12-31", "PERSONENZAHL")
    world.data["manual_run"] = world.run(create(world.actor, "/api/settlements", statement(data, "2025-01-01", "2025-12-31")))


@when("the manager opens the structured inputs of tenant A's clipped statement")
@browser_step
async def open_manual_inputs(browser_world):
    world = browser_world.world
    run = world.data["manual_run"]
    page = browser_world.page
    page.on("console", lambda message: browser_world.errors.append(message.text) if message.type == "error" else None)
    await browser_world.visit(f"/abrechnungen/{run['id']}")
    await page.get_by_role("button", name="Strukturierte Eingaben anzeigen", exact=True).click()


@then("manual consumption and persons explain the property-year basis on desktop and mobile")
@browser_step
async def manual_input_hints(browser_world, request):
    from playwright.async_api import expect
    from tests.bdd.report import artifact_directory, attach_artifact

    page = browser_world.page
    assert "/abrechnungen/" in page.url
    assert await page.title() == "Hausverwaltung"
    await expect(page.get_by_role("heading", name="Verbrauch / Personenzahl", exact=True)).to_be_visible()
    directory = artifact_directory(request.node)
    for width in (1280, 360):
        await page.set_viewport_size({"width": width, "height": 1000})
        if width == 360:
            await page.wait_for_function("document.querySelector('aside').getBoundingClientRect().right <= 0")
        await page.get_by_role("heading", name="Verbrauch / Personenzahl", exact=True).scroll_into_view_if_needed()
        if directory:
            capture = directory / f"settlement-inputs-{width}.png"
            await page.screenshot(path=str(capture), full_page=True, animations="disabled")
            attach_artifact(request.node, capture)
        assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    await expect(page.get_by_text("Verbrauch der Einheit im gesamten Objekt-Abrechnungsjahr eingeben, auch bei gekürztem Mietzeitraum.", exact=True)).to_be_visible()
    await expect(page.get_by_text("Personenzahlen je Einheit für das gesamte Objekt-Abrechnungsjahr in allen zugehörigen Abrechnungen gleich eingeben.", exact=True)).to_be_visible()
    field = page.get_by_role("textbox", name="Verbrauch (1)", exact=True)
    await field.fill("1000")
    await expect(field).to_have_value("1000")
    assert not browser_world.errors
