"""Property periods, validation and frozen historical periods through the API."""
from decimal import Decimal
import pytest
from tests.settlement_period_support import period_data_cleanup  # noqa: F401
from tests.settlement_period_support import period_property, create, statement, booked
from tests.financial_coverage_helpers import attest_fixture


pytestmark = pytest.mark.usefixtures("period_data_cleanup")


@pytest.fixture
async def april(client_a_admin):
    return await period_property(client_a_admin)


async def test_property_period_clips_to_lease_and_batch_repeat_skips_clipped_run(client_a_admin, april):
    api = client_a_admin
    changed = await api.patch(f"/api/leases/{april['lease']['id']}", json={"valid_to": "2025-09-30"})
    assert changed.status_code == 200, changed.text
    body = statement(april)
    run = await create(api, "/api/settlements", body)
    assert (run["period_start"], run["period_end"]) == ("2025-04-01", "2025-09-30")
    body.pop("lease_agreement_id")
    batch = await api.post("/api/settlements/batch", json=body)
    assert batch.status_code == 200, batch.text
    assert batch.json()["created"] == []
    assert batch.json()["skipped"][0]["settlement_run_id"] == run["id"]
    successor = await create(api, "/api/leases", {
        "unit_id": april["unit"]["id"], "tenant_profile_id": april["tenant"]["id"],
        "valid_from": "2025-10-01", "base_rent": "700.00", "advance_payment": "100.00",
    })
    batch = await api.post("/api/settlements/batch", json=body)
    assert batch.status_code == 200, batch.text
    created, = batch.json()["created"]
    assert (created["lease_agreement_id"], created["period_start"], created["period_end"]) == (
        successor["id"], "2025-10-01", "2026-03-31")
    single = await create(api, "/api/settlements", {**statement(april), "lease_agreement_id": successor["id"]})
    assert (single["period_start"], single["period_end"]) == (created["period_start"], created["period_end"])


@pytest.mark.parametrize("start,end", [("2025-03-31", "2025-09-30"), ("2025-04-01", "2025-10-01"), ("2025-10-01", "2025-12-31")])
async def test_free_statement_dates_outside_lease_return_structured_german_error(client_a_admin, april, start, end):
    api = client_a_admin
    changed = await api.patch(f"/api/leases/{april['lease']['id']}", json={"valid_to": "2025-03-31"})
    assert changed.status_code == 200, changed.text
    lease = await create(api, "/api/leases", {
        "unit_id": april["unit"]["id"], "tenant_profile_id": april["tenant"]["id"],
        "valid_from": "2025-04-01", "valid_to": "2025-09-30",
        "base_rent": "700.00", "advance_payment": "100.00",
    })
    april = {**april, "lease": lease}
    answer = await api.post("/api/settlements", json=statement(april, start, end))
    assert answer.status_code == 422, answer.text
    assert answer.json()["detail"] == {"code": "settlement_period_outside_lease", "message": "Der Abrechnungszeitraum muss innerhalb der Mietdauer liegen."}


async def test_month_assigned_nk_advance_is_prorated_and_finalizes_across_march_cut(client_a_admin, april):
    api = client_a_admin
    for month in range(1, 13):
        await booked(api, april, 2025, month)
    totals = []
    for start, end, expected in (("2025-01-01", "2025-03-14", "245.16"), ("2025-03-15", "2025-12-31", "954.84")):
        run = await create(api, "/api/settlements", statement(april, start, end))
        answer = await api.patch(f"/api/settlements/{run['id']}/advance-payments-derived", params={"period_year": 2025})
        assert answer.status_code == 200, answer.text
        assert Decimal(answer.json()["advance_payments_total"]) == Decimal(expected)
        await attest_fixture(api, "settlement", run["id"], {"advance_payments_total": expected, "advance_payments_source": "DERIVED"})
        final = await api.post(f"/api/settlements/{run['id']}/finalize", json={})
        assert final.status_code == 200, final.text
        totals.append(Decimal(final.json()["advance_payments_total"]))
    assert sum(totals) == Decimal("1200.00")


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("start,end", [("2025-04-01", "2026-04-01"), ("2024-02-29", "2025-03-01"), ("2026-01-02", "2026-01-01")])
async def test_overlong_or_reversed_statement_is_rejected(client_a_admin, april, batch, start, end):
    body = statement(april, start, end)
    if batch:
        body.pop("lease_agreement_id")
    answer = await client_a_admin.post("/api/settlements" + ("/batch" if batch else ""), json=body)
    assert answer.status_code == 422, answer.text


@pytest.mark.parametrize("batch", [False, True])
async def test_nonidentical_overlaps_with_finalized_statements_are_rejected(client_a_admin, april, batch):
    api = client_a_admin
    run = await create(api, "/api/settlements", {
        **statement(april, "2025-07-01", "2025-09-30"), "advance_payments_total": "0.00"})
    await attest_fixture(api, "settlement", run["id"], {"advance_payments_total": "0.00", "advance_payments_source": "MANUAL"})
    finalized = await api.post(f"/api/settlements/{run['id']}/finalize", json={})
    assert finalized.status_code == 200, finalized.text
    path = "/api/settlements" + ("/batch" if batch else "")
    for start, end, status in (
        ("2025-04-01", "2025-08-31", 409),  # Straddles the start.
        ("2025-08-01", "2025-12-31", 409),  # Straddles the end.
        ("2025-04-01", "2025-07-01", 409),  # Inclusive boundary day.
        ("2025-09-30", "2025-12-31", 409),
        ("2025-08-01", "2025-08-31", 409),  # Contained.
        ("2025-07-01", "2025-08-31", 409),  # Shared start, contained.
        ("2025-08-01", "2025-09-30", 409),  # Shared end, contained.
        ("2025-04-01", "2025-12-31", 409),  # Containing.
        ("2025-07-01", "2025-12-31", 409),  # Shared start, containing.
        ("2025-04-01", "2025-09-30", 409),  # Shared end, containing.
        ("2025-07-01", "2025-09-30", 201),  # Identical; batch skips it.
        ("2025-04-01", "2025-06-30", 201),  # Adjacent.
        ("2025-10-01", "2025-12-31", 201),
    ):
        body = statement(april, start, end)
        if batch:
            body.pop("lease_agreement_id")
        answer = await api.post(path, json=body)
        covered = batch and "2025-07-01" <= start <= end <= "2025-09-30"
        assert answer.status_code == (200 if batch and (status == 201 or covered) else status), answer.text
        if covered:
            assert answer.json()["skipped"][0]["settlement_run_id"] == run["id"]
        if status == 409 and not covered:
            assert answer.json()["detail"] == {
                "code": "settlement_period_conflict",
                "message": "Abrechnungszeitraum überschneidet eine finalisierte Abrechnung dieses Mietvertrags.",
            }
        if batch and status == 201:
            identical = (start, end) == ("2025-07-01", "2025-09-30")
            assert len(answer.json()["skipped" if identical else "created"]) == 1, answer.text
    await create(api, "/api/settlements", statement(april, "2026-04-01", "2026-12-31"))


async def test_overlapping_drafts_do_not_block_single_statement_creation(client_a_admin, april):
    await create(client_a_admin, "/api/settlements", statement(april))
    body = statement(april, "2026-01-01", "2026-12-31")
    answer = await client_a_admin.post("/api/settlements", json=body)
    assert answer.status_code == 201, answer.text


@pytest.mark.parametrize("start,end", [
    ("2025-04-01", "2026-03-31"),  # Covers the clipped span.
    ("2025-04-01", "2025-06-30"),  # Contained in it.
    ("2025-07-01", "2025-12-31"),  # Straddles its end.
    ("2025-09-30", "2025-12-31"),  # Overlaps on the inclusive last day.
])
async def test_batch_refuses_any_nonidentical_draft_overlap_after_clipping(client_a_admin, april, start, end):
    api = client_a_admin
    await create(api, "/api/settlements", statement(april, start, end))
    changed = await api.patch(f"/api/leases/{april['lease']['id']}", json={"valid_to": "2025-09-30"})
    assert changed.status_code == 200, changed.text
    body = statement(april)
    body.pop("lease_agreement_id")
    before = (await api.get("/api/settlements", params={"property_id": april['property']['id']})).json()
    answer = await api.post("/api/settlements/batch", json=body)
    assert answer.status_code == 409, answer.text
    assert answer.json()["detail"] == {
        "code": "settlement_period_conflict",
        "message": "Abrechnungszeitraum überschneidet einen bestehenden Entwurf dieses Mietvertrags.",
    }
    assert (await api.get("/api/settlements", params={"property_id": april['property']['id']})).json() == before


async def test_batch_refuses_partial_draft_overlap_without_clipping(client_a_admin, april):
    await create(client_a_admin, "/api/settlements", statement(april))
    body = statement(april, "2026-01-01", "2026-12-31")
    body.pop("lease_agreement_id")
    answer = await client_a_admin.post("/api/settlements/batch", json=body)
    assert answer.status_code == 409, answer.text


@pytest.mark.parametrize("batch", [False, True])
async def test_leap_day_statement_accepts_twelve_month_period(client_a_admin, april, batch):
    body = statement(april, "2024-02-29", "2025-02-28")
    if batch:
        body.pop("lease_agreement_id")
    answer = await client_a_admin.post("/api/settlements" + ("/batch" if batch else ""), json=body)
    assert answer.status_code == (200 if batch else 201), answer.text


async def test_reference_year_may_be_either_year_of_the_period(client_a_admin, april):
    api = client_a_admin
    run = await create(api, "/api/settlements", statement(april))
    for year, month in ((2025, 3), (2025, 4), (2025, 12), (2026, 1), (2026, 3), (2026, 4)):
        await booked(api, april, year, month)
    path = f"/api/settlements/{run['id']}/advance-payments-derived"
    for year in (2025, 2026):
        answer = await api.patch(path, params={"period_year": year})
        assert answer.status_code == 200, answer.text
        assert Decimal(answer.json()["advance_payments_total"]) == Decimal("400.00")
    assert (await api.patch(path, params={"period_year": 2024})).status_code == 422
    values = {"advance_payments_total": "400.00", "advance_payments_source": "DERIVED"}
    await attest_fixture(api, "settlement", run["id"], values)
    answer = await api.post(f"/api/settlements/{run['id']}/finalize", json={})
    assert answer.status_code == 200, answer.text


@pytest.mark.parametrize("start,end,status", [
    ("2025-04-01", "2025-08-31", 409),
    ("2025-08-01", "2025-08-31", 409),
    ("2025-04-01", "2025-12-31", 409),
    ("2025-07-01", "2025-09-30", 200),
])
async def test_statement_finalization_rechecks_drafts_against_newly_finalized_period(client_a_admin, start, end, status):
    api = client_a_admin
    data = await period_property(api, month=1)
    runs = [await create(api, "/api/settlements", {
        **statement(data, start, end), "advance_payments_total": "0.00",
    }) for start, end in (("2025-07-01", "2025-09-30"), (start, end))]
    for run in runs:
        await attest_fixture(api, "settlement", run["id"], {
            "advance_payments_total": "0.00", "advance_payments_source": "MANUAL",
        })
    first = await api.post(f"/api/settlements/{runs[0]['id']}/finalize", json={})
    assert first.status_code == 200, first.text
    second = await api.post(f"/api/settlements/{runs[1]['id']}/finalize", json={})
    assert second.status_code == status, second.text
    if status == 409:
        assert second.json()["detail"] == {
            "code": "settlement_period_conflict",
            "message": "Abrechnungszeitraum überschneidet eine finalisierte Abrechnung dieses Mietvertrags.",
        }
    assert (await api.get(f"/api/settlements/{runs[1]['id']}")).json()["status"] == ("draft" if status == 409 else "finalized")


@pytest.mark.parametrize("month", [1, 4])
async def test_year_only_nk_advance_is_split_between_calendar_year_months_and_finalizes(client_a_admin, month):
    api = client_a_admin
    data = await period_property(api, month=month)
    await booked(api, data, 2025, None, amount="1200.00")
    totals = []
    for year in (2024, 2025):
        start = f"{year}-{month:02d}-01"
        end = f"{year}-12-31" if month == 1 else f"{year + 1}-03-31"
        run = await create(api, "/api/settlements", statement(data, start, end))
        derived = await api.patch(f"/api/settlements/{run['id']}/advance-payments-derived", params={"period_year": year})
        assert derived.status_code == 200, derived.text
        expected = ("1200.00" if year == 2025 else "0.00") if month == 1 else ("900.00" if year == 2025 else "300.00")
        assert Decimal(derived.json()["advance_payments_total"]) == Decimal(expected)
        await attest_fixture(api, "settlement", run["id"], {
            "advance_payments_total": expected, "advance_payments_source": "DERIVED",
        })
        final = await api.post(f"/api/settlements/{run['id']}/finalize", json={})
        assert final.status_code == 200, final.text
        totals.append(Decimal(final.json()["advance_payments_total"]))
    assert sum(totals) == Decimal("1200.00")


@pytest.mark.parametrize("amount,periods,expected", [
    ("1200.00", [("2025-01-01", "2025-06-30"), ("2025-07-01", "2025-12-31")], ["600.00", "600.00"]),
    ("1200.00", [("2025-01-01", "2025-03-31"), ("2025-04-01", "2026-03-31")], ["300.00", "900.00"]),
    ("1200.00", [("2025-01-01", "2025-12-31")], ["1200.00"]),
    ("1200.00", [("2025-01-01", "2025-01-15"), ("2025-01-16", "2025-12-31")], ["48.38", "1151.62"]),
    ("1000.00", [(f"2025-{m:02d}-01", f"2025-{m:02d}-{days}")
                 for m, days in enumerate((31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31), 1)],
     ["83.33"] * 11 + ["83.37"]),
])
async def test_year_only_nk_advance_is_not_credited_twice_across_adjacent_periods(
    client_a_admin, amount, periods, expected,
):
    api = client_a_admin
    data = await period_property(api, month=1)
    await booked(api, data, 2025, None, amount=amount)
    totals = []
    for (start, end), share in zip(periods, expected):
        run = await create(api, "/api/settlements", statement(data, start, end))
        derived = await api.patch(f"/api/settlements/{run['id']}/advance-payments-derived", params={"period_year": 2025})
        assert derived.status_code == 200, derived.text
        assert Decimal(derived.json()["advance_payments_total"]) == Decimal(share)
        await attest_fixture(api, "settlement", run["id"], {
            "advance_payments_total": share, "advance_payments_source": "DERIVED",
        })
        final = await api.post(f"/api/settlements/{run['id']}/finalize", json={})
        assert final.status_code == 200, final.text
        totals.append(Decimal(final.json()["advance_payments_total"]))
    assert sum(totals) == Decimal(amount)


async def test_year_only_nk_advance_cannot_be_omitted_from_derived_finalization(client_a_admin):
    api = client_a_admin
    data = await period_property(api, month=1)
    run = await create(api, "/api/settlements", statement(data, "2025-01-01", "2025-12-31"))
    derived = await api.patch(f"/api/settlements/{run['id']}/advance-payments-derived", params={"period_year": 2025})
    assert derived.status_code == 200, derived.text
    await attest_fixture(api, "settlement", run["id"], {
        "advance_payments_total": "0.00", "advance_payments_source": "DERIVED",
    })
    await booked(api, data, 2025, None, amount="1200.00")
    final = await api.post(f"/api/settlements/{run['id']}/finalize", json={})
    assert final.status_code == 422, final.text
    assert final.json()["detail"]["blockers"][0]["code"] == "derived_total_stale"


@pytest.mark.parametrize("today,expected_year", [("2026-10-31", 2024), ("2026-11-01", 2025)])
async def test_default_november_offers_end_before_today(client_a_admin, monkeypatch, today, expected_year):
    from datetime import date
    from app.api import properties

    class Today(date):
        @classmethod
        def today(cls):
            return date.fromisoformat(today)

    monkeypatch.setattr(properties, "date", Today)
    data = await period_property(client_a_admin, month=11)
    response = await client_a_admin.get(f"/api/properties/{data['property']['id']}/settlement-periods")
    assert response.status_code == 200, response.text
    offers = response.json()
    assert offers[0]["year"] == expected_year
    assert offers[0]["period_start"] == f"{expected_year}-11-01"
    assert offers[0]["period_end"] == f"{expected_year + 1}-10-31"
    assert all(offer["period_end"] < today for offer in offers)


@pytest.mark.parametrize("conflict_first", [True, False])
async def test_batch_reports_straddling_lease_and_creates_others(client_a_admin, conflict_first):
    api = client_a_admin
    data = await period_property(api, month=1)
    other_leases = []
    for number in (2, 3):
        unit = await create(api, "/api/units", {
            "property_id": data["property"]["id"], "number": str(number),
            "usage_type": "WOHNUNG", "area": "70", "mea": "0",
        })
        other_leases.append(await create(api, "/api/leases", {
            "unit_id": unit["id"], "tenant_profile_id": data["tenant"]["id"],
            "valid_from": "2021-01-01" if conflict_first else "2019-01-01",
            "base_rent": "700.00", "advance_payment": "100.00",
        }))
    run = await create(api, "/api/settlements", {
        **statement(data, "2025-01-01", "2025-12-31"), "advance_payments_total": "0.00",
    })
    await attest_fixture(api, "settlement", run["id"], {
        "advance_payments_total": "0.00", "advance_payments_source": "MANUAL",
    })
    finalized = await api.post(f"/api/settlements/{run['id']}/finalize", json={})
    assert finalized.status_code == 200, finalized.text
    body = statement(data, "2025-07-01", "2026-06-30")
    existing = await create(api, "/api/settlements", {**body, "lease_agreement_id": other_leases[0]["id"]})
    body.pop("lease_agreement_id")
    response = await api.post("/api/settlements/batch", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert [item["lease_agreement_id"] for item in result["created"]] == [other_leases[1]["id"]]
    assert result["skipped"] == [{"lease_agreement_id": other_leases[0]["id"], "settlement_run_id": existing["id"]}]
    assert result["errors"] == [{"lease_agreement_id": data["lease"]["id"],
        "code": "settlement_period_conflict",
        "error": "Abrechnungszeitraum überschneidet eine finalisierte Abrechnung dieses Mietvertrags."}]
    repeated = await api.post("/api/settlements/batch", json=body)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["created"] == []
    assert {item["settlement_run_id"] for item in repeated.json()["skipped"]} == {existing["id"], result["created"][0]["id"]}
    assert repeated.json()["errors"] == result["errors"]


async def test_batch_skips_finalized_legacy_run_covering_later_clipped_lease(client_a_admin, april):
    api = client_a_admin
    run = await create(api, "/api/settlements", {**statement(april), "advance_payments_total": "0.00"})
    await attest_fixture(api, "settlement", run["id"], {"advance_payments_total": "0.00", "advance_payments_source": "MANUAL"})
    final = await api.post(f"/api/settlements/{run['id']}/finalize", json={})
    assert final.status_code == 200, final.text
    changed = await api.patch(f"/api/leases/{april['lease']['id']}", json={"valid_to": "2025-09-30"})
    assert changed.status_code == 200, changed.text
    single = await api.post("/api/settlements", json=statement(april))
    assert single.status_code == 409, single.text
    body = statement(april)
    body.pop("lease_agreement_id")
    batch = await api.post("/api/settlements/batch", json=body)
    assert batch.status_code == 200, batch.text
    assert batch.json()["created"] == []
    skipped, = batch.json()["skipped"]
    assert skipped["settlement_run_id"] == run["id"]
    assert skipped["reason"] == "Eine finalisierte Abrechnung deckt den gekürzten Mietzeitraum bereits ab."
