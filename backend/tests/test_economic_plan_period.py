"""Property periods in economic plans, WEG payments and immutable originals."""
from decimal import Decimal
import pytest
from tests.settlement_period_support import period_property, create, statement, booked
from tests.financial_coverage_helpers import attest_fixture


@pytest.fixture
async def april(client_a_admin):
    return await period_property(client_a_admin)


async def test_plan_dates_follow_property_and_overlap_survives_start_month_change(client_a_admin, april):
    api = client_a_admin
    body = {"property_id": april["property"]["id"], "plan_year": 2025,
        "period_start": "2025-01-01", "period_end": "2025-12-31"}
    plan = await create(api, "/api/economic-plans", body)
    assert (plan["period_start"], plan["period_end"]) == ("2025-04-01", "2026-03-31")
    changed = await api.put(f"/api/properties/{april['property']['id']}", json={"fiscal_year_start_month": 1})
    assert changed.status_code == 200, changed.text
    assert (await api.post("/api/economic-plans", json={**body, "plan_year": 2026})).status_code == 409
    persisted = (await api.get(f"/api/economic-plans/{plan['id']}")).json()
    assert persisted["period_end"] == "2026-03-31"


async def test_previous_proposal_uses_previous_property_period(client_a_admin, april):
    api = client_a_admin
    categories = (await api.get("/api/cost-categories")).json()
    category = next(c for c in categories if c["code"] == "GARTENPFLEGE")
    # Invoice date deliberately differs from the service period.
    for start, end, amount in (("2024-04-01", "2025-03-31", "1234.56"), ("2024-01-01", "2024-03-31", "9999.00")):
        cost = await create(api, "/api/cost-entries", {
            "property_id": april["property"]["id"], "cost_category_id": category["id"],
            "invoice_date": "2025-07-01", "service_period_from": start, "service_period_to": end,
            "gross_amount": amount, "owner_distribution_key": "MEA", "tenant_distribution_key": "WOHNFLAECHE",
            "tenant_apportionable": "UMLAGEFAEHIG",
        })
        answer = await api.post(f"/api/cost-entries/{cost['id']}/confirm")
        assert answer.status_code == 200, answer.text
    body = {"property_id": april["property"]["id"], "plan_year": 2025}
    answer = await api.post("/api/economic-plans/previous/preview", json=body)
    assert answer.status_code == 200, answer.text
    preview = answer.json()
    assert (preview["period_start"], preview["period_end"]) == ("2024-04-01", "2025-03-31")
    assert preview["planned_costs"][0]["planned_amount"] == "1234.56"
    plan = await create(api, "/api/economic-plans/previous", {**body,
        "expected_source_digest": preview["source_digest"], "planned_costs": preview["planned_costs"],
        "reserve_contribution_total": preview["reserve_contribution_total"]})
    assert (plan["period_start"], plan["period_end"]) == ("2025-04-01", "2026-03-31")


async def test_paid_hausgeld_and_finalized_periods_survive_property_change(client_a_admin, april):
    api = client_a_admin
    plan = await create(api, "/api/economic-plans", {"property_id": april["property"]["id"],
        "plan_year": 2025, "period_start": "2025-04-01", "period_end": "2026-03-31"})
    inputs = {"planned_costs": [{"category_code": "GARTENPFLEGE", "label": "Garten", "planned_amount": "1200.00"}],
        "reserve_contribution_total": "0.00"}
    await attest_fixture(api, "economic_plan", plan["id"], inputs)
    answer = await api.post(f"/api/economic-plans/{plan['id']}/finalize", json=inputs)
    assert answer.status_code == 200, answer.text
    frozen_plan = answer.json()
    run = await create(api, "/api/weg-settlements", {"property_id": april["property"]["id"],
        "economic_plan_id": plan["id"], "period_start": "2025-04-01", "period_end": "2026-03-31"})
    for year, month in ((2025, 3), (2025, 4), (2025, 12), (2026, 1), (2026, 3), (2026, 4)):
        await booked(api, april, year, month, hausgeld=True)
    # Draft and reversed payments must not enter the derived total.
    payment = await booked(api, april, 2026, 2, hausgeld=True, amount="999.00")
    answer = await api.post(f"/api/payments/{payment['id']}/reverse", json={"note": "Test reversal"})
    assert answer.status_code == 201, answer.text
    answer = await api.post(f"/api/weg-settlements/{run['id']}/paid-advances-derived")
    assert answer.status_code == 200, answer.text
    assert Decimal(answer.json()[0]["paid_total"]) == Decimal("400.00")
    # Finalize a tenant run as well and prove no historical dates/hash are rewritten.
    bk = await create(api, "/api/settlements", {**statement(april), "advance_payments_total": "0.00"})
    await attest_fixture(api, "settlement", bk["id"], {"advance_payments_total": "0.00", "advance_payments_source": "MANUAL"})
    answer = await api.post(f"/api/settlements/{bk['id']}/finalize", json={})
    assert answer.status_code == 200, answer.text
    frozen_bk = answer.json()
    answer = await api.put(f"/api/properties/{april['property']['id']}", json={"fiscal_year_start_month": 10})
    assert answer.status_code == 200, answer.text
    for path, frozen in ((f"/api/economic-plans/{plan['id']}", frozen_plan), (f"/api/settlements/{bk['id']}", frozen_bk)):
        current = (await api.get(path)).json()
        for field in ("period_start", "period_end", "snapshot_sha256", "lines"):
            assert current[field] == frozen[field]


async def test_identical_plan_period_stays_allowed(client_a_admin, april):
    body = {"property_id": april["property"]["id"], "plan_year": 2025,
        "period_start": "2025-04-01", "period_end": "2026-03-31"}
    first = await create(client_a_admin, "/api/economic-plans", body)
    second = await create(client_a_admin, "/api/economic-plans", body)
    assert first["id"] != second["id"]
    assert (second["period_start"], second["period_end"]) == (first["period_start"], first["period_end"])
