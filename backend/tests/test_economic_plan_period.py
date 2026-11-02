"""Property periods in economic plans, WEG payments and immutable originals."""
from decimal import Decimal
import pytest
from tests.settlement_period_support import period_data_cleanup  # noqa: F401
from tests.settlement_period_support import period_property, create, statement, booked, finalized_plan, period_scope
from tests.financial_coverage_helpers import attest_fixture


pytestmark = pytest.mark.usefixtures("period_data_cleanup")


RECHECK_PERIODS = [
    (("2025-01-01", "2025-12-31"), ("2025-07-01", "2026-06-30"), 409),
    (("2025-07-01", "2025-09-30"), ("2025-08-01", "2025-08-31"), 409),
    (("2025-07-01", "2025-09-30"), ("2025-04-01", "2025-12-31"), 409),
    (("2025-07-01", "2025-09-30"), ("2025-07-01", "2025-09-30"), 200),
]


async def period_draft_plans(api, data, first_period, second_period):
    first = await create(api, "/api/economic-plans", {
        "property_id": data["property"]["id"], "plan_year": 2025,
        "period_start": first_period[0], "period_end": first_period[1],
    })
    changed = await api.put(f"/api/properties/{data['property']['id']}", json={"fiscal_year_start_month": int(second_period[0][5:7])})
    assert changed.status_code == 200, changed.text
    second = await create(api, "/api/economic-plans", {
        "property_id": data["property"]["id"], "plan_year": 2025,
        "period_start": second_period[0], "period_end": second_period[1],
    })
    return first, second


@pytest.mark.parametrize("first_period,second_period,status", RECHECK_PERIODS)
async def test_plan_finalization_rechecks_drafts_against_newly_finalized_period(client_a_admin, first_period, second_period, status):
    api = client_a_admin
    data = await period_property(api, month=1)
    plans = await period_draft_plans(api, data, first_period, second_period)
    values = {"planned_costs": [], "reserve_contribution_total": "0.00"}
    for plan in plans:
        await attest_fixture(api, "economic_plan", plan["id"], values)
    first = await api.post(f"/api/economic-plans/{plans[0]['id']}/finalize", json=values)
    assert first.status_code == 200, first.text
    second = await api.post(f"/api/economic-plans/{plans[1]['id']}/finalize", json=values)
    assert second.status_code == status, second.text
    if status == 409:
        assert second.json()["detail"] == {
            "code": "plan_period_conflict",
            "message": "Der Zeitraum überschneidet einen finalisierten Wirtschaftsplan dieses Objekts.",
        }
    assert (await api.get(f"/api/economic-plans/{plans[1]['id']}")).json()["status"] == ("draft" if status == 409 else "finalized")


@pytest.mark.parametrize("first_period,second_period,status", RECHECK_PERIODS)
async def test_weg_finalization_rechecks_drafts_against_newly_finalized_period(client_a_admin, first_period, second_period, status):
    api = client_a_admin
    data = await period_property(api, month=1)
    plans = await period_draft_plans(api, data, first_period, second_period)
    plan_values = {"planned_costs": [], "reserve_contribution_total": "0.00"}
    await attest_fixture(api, "economic_plan", plans[0]["id"], plan_values)
    first_plan = await api.post(f"/api/economic-plans/{plans[0]['id']}/finalize", json=plan_values)
    assert first_plan.status_code == 200, first_plan.text
    runs = [await create(api, "/api/weg-settlements", {
        "property_id": data["property"]["id"], "economic_plan_id": plan["id"],
        "period_start": plan["period_start"], "period_end": plan["period_end"],
    }) for plan in plans]
    values = {"reserve_override_reason": "Reviewed synthetic reserve totals", "reserve_opening_balance": "0.00", "reserve_withdrawals_total": "0.00",
        "paid_advances": [{"owner_profile_id": data["owner"]["id"], "paid_total": "0.00"}]}
    await attest_fixture(api, "settlement", runs[0]["id"], values)
    first = await api.post(f"/api/weg-settlements/{runs[0]['id']}/finalize", json=values)
    assert first.status_code == 200, first.text
    # Retire the old plan so the second draft can use its own current finalized plan.
    correction = await api.post(f"/api/economic-plans/{plans[0]['id']}/correction")
    assert correction.status_code == 201, correction.text
    await attest_fixture(api, "economic_plan", plans[1]["id"], plan_values)
    second_plan = await api.post(f"/api/economic-plans/{plans[1]['id']}/finalize", json=plan_values)
    assert second_plan.status_code == 200, second_plan.text
    await attest_fixture(api, "settlement", runs[1]["id"], values)
    second = await api.post(f"/api/weg-settlements/{runs[1]['id']}/finalize", json=values)
    assert second.status_code == status, second.text
    if status == 409:
        assert second.json()["detail"] == {
            "code": "settlement_period_conflict",
            "message": "Abrechnungszeitraum überschneidet eine finalisierte WEG-Abrechnung dieses Objekts.",
        }
    assert (await api.get(f"/api/weg-settlements/{runs[1]['id']}")).json()["status"] == ("draft" if status == 409 else "finalized")


@pytest.fixture
async def april(client_a_admin):
    return await period_property(client_a_admin)


async def test_admin_without_plan_dates_keeps_property_period(client_a_admin, april):
    plan = await create(client_a_admin, "/api/economic-plans", {
        "property_id": april["property"]["id"], "plan_year": 2025,
    })
    assert (plan["plan_year"], plan["period_start"], plan["period_end"]) == (2025, "2025-04-01", "2026-03-31")


async def test_plan_transition_closes_gap_after_start_month_change(client_a_admin):
    api = client_a_admin
    data = await period_property(api, month=1)
    original = await finalized_plan(api, data, "2025-01-01", "2025-12-31")
    changed = await api.put(f"/api/properties/{data['property']['id']}", json={"fiscal_year_start_month": 4})
    assert changed.status_code == 200, changed.text
    transition = await finalized_plan(api, data, "2026-01-01", "2026-03-31")
    assert (transition["period_start"], transition["period_end"]) == ("2026-01-01", "2026-03-31")
    # Recreating the identical finalized transition remains possible too.
    await create(api, "/api/economic-plans", {"property_id": data["property"]["id"], "plan_year": 2026,
        "period_start": "2026-01-01", "period_end": "2026-03-31"})
    next_plan = await create(api, "/api/economic-plans", {"property_id": data["property"]["id"], "plan_year": 2026})
    assert (next_plan["period_start"], next_plan["period_end"]) == ("2026-04-01", "2027-03-31")
    # The gap plan can supply the actual WEG settlement for precisely those days.
    run = await create(api, "/api/weg-settlements", {"property_id": data["property"]["id"],
        "economic_plan_id": transition["id"], "period_start": "2026-01-01", "period_end": "2026-03-31"})
    values = {"reserve_override_reason": "Reviewed synthetic reserve totals", "reserve_opening_balance": "0.00", "reserve_withdrawals_total": "0.00",
        "paid_advances": [{"owner_profile_id": data["owner"]["id"], "paid_total": "0.00"}]}
    await attest_fixture(api, "settlement", run["id"], values)
    result = await api.post(f"/api/weg-settlements/{run['id']}/finalize", json=values)
    assert result.status_code == 200, result.text
    assert (result.json()["period_start"], result.json()["period_end"]) == ("2026-01-01", "2026-03-31")
    persisted = (await api.get(f"/api/economic-plans/{original['id']}")).json()
    assert persisted["snapshot_sha256"] == original["snapshot_sha256"]


async def test_transition_plan_must_close_entire_gap(client_a_admin):
    api = client_a_admin
    data = await period_property(api, month=1)
    await finalized_plan(api, data, "2025-01-01", "2025-12-31")
    response = await api.put(f"/api/properties/{data['property']['id']}", json={"fiscal_year_start_month": 4})
    assert response.status_code == 200, response.text
    for start, end in (("2026-01-02", "2026-03-31"), ("2026-01-01", "2026-03-30")):
        invalid = await api.post("/api/economic-plans", json={"property_id": data["property"]["id"],
            "plan_year": 2026, "period_start": start, "period_end": end})
        assert invalid.status_code == 422, invalid.text


async def test_overlapping_plan_drafts_are_allowed(client_a_admin, april):
    body = {"property_id": april["property"]["id"], "plan_year": 2025,
        "period_start": "2025-07-01", "period_end": "2025-09-30"}
    await create(client_a_admin, "/api/economic-plans", body)
    short = await create(client_a_admin, "/api/economic-plans", {
        **body, "period_start": "2025-08-01", "period_end": "2025-12-31"})
    assert short["period_end"] == "2025-12-31"


async def test_nonidentical_overlaps_with_finalized_plans_are_rejected(client_a_admin, april):
    api = client_a_admin
    await finalized_plan(api, april, "2025-07-01", "2025-09-30")
    body = {"property_id": april["property"]["id"], "plan_year": 2025,
        "period_start": "2025-07-01", "period_end": "2025-09-30"}
    for start, end, status in (
        ("2025-04-01", "2025-08-31", 409),
        ("2025-08-01", "2025-12-31", 409),
        ("2025-04-01", "2025-07-01", 409),
        ("2025-09-30", "2025-12-31", 409),
        ("2025-08-01", "2025-08-31", 409),
        ("2025-07-01", "2025-08-31", 409),
        ("2025-08-01", "2025-09-30", 409),
        ("2025-04-01", "2025-12-31", 409),
        ("2025-07-01", "2025-12-31", 409),
        ("2025-04-01", "2025-09-30", 409),
        ("2025-07-01", "2025-09-30", 201),
        ("2025-04-01", "2025-06-30", 201),
        ("2025-10-01", "2025-12-31", 201),
    ):
        answer = await api.post("/api/economic-plans", json={**body, "period_start": start, "period_end": end})
        assert answer.status_code == status, answer.text
        if status == 409:
            assert answer.json()["detail"] == {
                "code": "plan_period_conflict",
                "message": "Der Zeitraum überschneidet einen finalisierten Wirtschaftsplan dieses Objekts.",
            }


@pytest.mark.parametrize("start,end", [("2025-04-01", "2026-04-01"),
    ("2025-01-01", "2025-12-31"), ("2025-07-01", "2025-06-30")])
async def test_invalid_explicit_plan_period_is_rejected(client_a_admin, april, start, end):
    answer = await client_a_admin.post("/api/economic-plans", json={"property_id": april["property"]["id"],
        "plan_year": 2025, "period_start": start, "period_end": end})
    assert answer.status_code == 422, answer.text


async def test_midyear_scope_accepts_explicit_short_plan(client_a_admin, seed_data):
    api = client_a_admin
    data = await period_property(api, month=1)
    scope_id = await period_scope(api, data, "2025-07-01", seed_data["user_a_admin_id"])
    plan = await create(api, "/api/economic-plans", {"property_id": data["property"]["id"],
        "accounting_scope_id": scope_id, "plan_year": 2025,
        "period_start": "2025-07-01", "period_end": "2025-12-31"})
    assert (plan["period_start"], plan["period_end"]) == ("2025-07-01", "2025-12-31")


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
    bk = await create(api, "/api/settlements", {**statement(april), 'heating_advance_payments_total': '0.00', "advance_payments_total": "0.00"})
    await attest_fixture(api, "settlement", bk["id"], {'heating_advance_payments_total': '0.00', 'heating_advance_payments_source': "MANUAL", "advance_payments_total": "0.00", "advance_payments_source": "MANUAL"})
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


@pytest.mark.parametrize("month", [1, 4])
async def test_year_only_hausgeld_is_split_between_calendar_year_months(client_a_admin, month):
    api = client_a_admin
    data = await period_property(api, month=month)
    await booked(api, data, 2025, None, hausgeld=True)
    totals = []
    for year in (2024, 2025):
        start = f"{year}-{month:02d}-01"
        end = f"{year}-12-31" if month == 1 else f"{year + 1}-03-31"
        plan = await finalized_plan(api, data, start, end)
        run = await create(api, "/api/weg-settlements", {"property_id": data["property"]["id"],
            "economic_plan_id": plan["id"], "period_start": start, "period_end": end})
        answer = await api.post(f"/api/weg-settlements/{run['id']}/paid-advances-derived")
        assert answer.status_code == 200, answer.text
        totals.append(Decimal(answer.json()[0]["paid_total"]))
    assert totals == ([Decimal("0.00"), Decimal("100.00")] if month == 1 else [Decimal("24.99"), Decimal("75.01")])


async def test_weg_rejects_overlong_period(client_a_admin, april):
    plan = await finalized_plan(client_a_admin, april, "2025-04-01", "2026-03-31")
    answer = await client_a_admin.post("/api/weg-settlements", json={"property_id": april["property"]["id"],
        "economic_plan_id": plan["id"], "period_start": "2025-04-01", "period_end": "2028-03-31"})
    assert answer.status_code == 422, answer.text


async def test_weg_overlap_checks_only_finalized_runs_for_same_property(client_a_admin, april):
    api = client_a_admin
    plan = await finalized_plan(api, april, "2025-07-01", "2025-09-30")
    body = {"property_id": april["property"]["id"], "economic_plan_id": plan["id"],
        "period_start": "2025-07-01", "period_end": "2025-09-30"}
    run = await create(api, "/api/weg-settlements", body)
    await create(api, "/api/weg-settlements", {
        **body, "period_start": "2025-08-01", "period_end": "2025-12-31"})
    other = await period_property(api)
    other_plan = await finalized_plan(api, other, "2025-04-01", "2026-03-31")
    values = {"reserve_override_reason": "Reviewed synthetic reserve totals", "reserve_opening_balance": "0.00", "reserve_withdrawals_total": "0.00",
        "paid_advances": [{"owner_profile_id": april["owner"]["id"], "paid_total": "0.00"}]}
    await attest_fixture(api, "settlement", run["id"], values)
    result = await api.post(f"/api/weg-settlements/{run['id']}/finalize", json=values)
    assert result.status_code == 200, result.text
    for start, end, status in (
        ("2025-04-01", "2025-08-31", 409),
        ("2025-08-01", "2025-12-31", 409),
        ("2025-04-01", "2025-07-01", 409),
        ("2025-09-30", "2025-12-31", 409),
        ("2025-08-01", "2025-08-31", 409),
        ("2025-07-01", "2025-08-31", 409),
        ("2025-08-01", "2025-09-30", 409),
        ("2025-04-01", "2025-12-31", 409),
        ("2025-07-01", "2025-12-31", 409),
        ("2025-04-01", "2025-09-30", 409),
        ("2025-07-01", "2025-09-30", 201),
        ("2025-04-01", "2025-06-30", 201),
        ("2025-10-01", "2025-12-31", 201),
    ):
        answer = await api.post("/api/weg-settlements", json={**body, "period_start": start, "period_end": end})
        assert answer.status_code == status, answer.text
        if status == 409:
            assert answer.json()["detail"] == {
                "code": "settlement_period_conflict",
                "message": "Abrechnungszeitraum überschneidet eine finalisierte WEG-Abrechnung dieses Objekts.",
            }
    await create(api, "/api/weg-settlements", {**body, "property_id": other["property"]["id"],
        "economic_plan_id": other_plan["id"], "period_start": "2025-08-01", "period_end": "2025-12-31"})


async def test_weg_overlap_does_not_cross_accounting_contexts(client_a_admin, april, seed_data):
    api = client_a_admin
    plan = await finalized_plan(api, april, "2025-04-01", "2026-03-31")
    body = {"property_id": april["property"]["id"], "economic_plan_id": plan["id"],
        "period_start": "2025-04-01", "period_end": "2026-03-31"}
    run = await create(api, "/api/weg-settlements", body)
    values = {"reserve_override_reason": "Reviewed synthetic reserve totals", "reserve_opening_balance": "0.00", "reserve_withdrawals_total": "0.00",
        "paid_advances": [{"owner_profile_id": april["owner"]["id"], "paid_total": "0.00"}]}
    await attest_fixture(api, "settlement", run["id"], values)
    response = await api.post(f"/api/weg-settlements/{run['id']}/finalize", json=values)
    assert response.status_code == 200, response.text
    scope_id = await period_scope(api, april, "2025-04-01", seed_data["user_a_admin_id"])
    scoped_plan = await create(api, "/api/economic-plans", {"property_id": april["property"]["id"],
        "plan_year": 2025, "period_start": "2025-04-01", "period_end": "2026-03-31",
        "accounting_scope_id": scope_id})
    await create(api, "/api/weg-settlements", {**body, "economic_plan_id": scoped_plan["id"],
        "accounting_scope_id": scope_id, "period_start": "2026-01-01", "period_end": "2026-12-31"})


@pytest.mark.parametrize("month,year,start,end,scope_start", [
    (1, 2025, "2025-07-01", "2025-12-31", "2025-07-01"),
    (4, 2026, "2026-01-01", "2026-03-31", "2026-01-01"),
])
async def test_requested_dates_reach_scope_validation_unchanged(monkeypatch, month, year, start, end, scope_start):
    from datetime import date
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4
    from fastapi import HTTPException
    from app.services import economic_plan_service as module

    async def prepare(plan, scope_id):
        if plan.period_start < date.fromisoformat(scope_start):
            raise HTTPException(422, "Financial period is outside accounting scope")

    scope = SimpleNamespace(lock_inputs=AsyncMock(), prepare_new=AsyncMock(side_effect=prepare))
    monkeypatch.setattr(module, "FinancialScopeService", lambda *args: scope)
    prop = SimpleNamespace(fiscal_year_start_month=month)
    monkeypatch.setattr(module, "PropertyRepository", lambda *args:
        SimpleNamespace(get_by_id=AsyncMock(return_value=prop)))
    service = module.EconomicPlanService(AsyncMock(), uuid4(), uuid4())
    service._plans = AsyncMock()
    service._plans.has_conflicting_period.return_value = False
    service._plans.last_finalized_before.return_value = SimpleNamespace(period_end=date(2025, 12, 31))
    service._plans.create.side_effect = lambda plan: plan
    service._audit = AsyncMock()
    result = await service.create_draft(uuid4(), year, date.fromisoformat(start), date.fromisoformat(end))
    assert (result.period_start.isoformat(), result.period_end.isoformat()) == (start, end)


async def test_year_only_hausgeld_is_not_credited_twice_across_adjacent_periods(client_a_admin):
    api = client_a_admin
    data = await period_property(api, month=1)
    await booked(api, data, 2025, None, hausgeld=True, amount="1200.00")
    totals = []
    for start, end in (("2025-01-01", "2025-06-30"), ("2025-07-01", "2025-12-31")):
        plan = await finalized_plan(api, data, start, end)
        run = await create(api, "/api/weg-settlements", {"property_id": data["property"]["id"],
            "economic_plan_id": plan["id"], "period_start": start, "period_end": end})
        answer = await api.post(f"/api/weg-settlements/{run['id']}/paid-advances-derived")
        assert answer.status_code == 200, answer.text
        total = Decimal(answer.json()[0]["paid_total"])
        assert total == Decimal("600.00")
        totals.append(total)
    assert sum(totals) == Decimal("1200.00")


async def test_month_assigned_hausgeld_reconciles_adjacent_march_cut(client_a_admin):
    api = client_a_admin
    data = await period_property(api, month=1)
    for month in range(1, 13):
        await booked(api, data, 2025, month, hausgeld=True)
    totals = []
    for start, end, expected in (("2025-01-01", "2025-03-14", "245.16"), ("2025-03-15", "2025-12-31", "954.84")):
        plan = await finalized_plan(api, data, start, end)
        run = await create(api, "/api/weg-settlements", {"property_id": data["property"]["id"],
            "economic_plan_id": plan["id"], "period_start": start, "period_end": end})
        answer = await api.post(f"/api/weg-settlements/{run['id']}/paid-advances-derived")
        assert answer.status_code == 200, answer.text
        totals.append(Decimal(answer.json()[0]["paid_total"]))
        assert totals[-1] == Decimal(expected)
    assert sum(totals) == Decimal("1200.00")
