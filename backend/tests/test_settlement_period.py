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


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("start,end", [("2025-04-01", "2026-04-01"), ("2024-02-29", "2025-03-01"), ("2026-01-02", "2026-01-01")])
async def test_overlong_or_reversed_statement_is_rejected(client_a_admin, april, batch, start, end):
    body = statement(april, start, end)
    if batch:
        body.pop("lease_agreement_id")
    answer = await client_a_admin.post("/api/settlements" + ("/batch" if batch else ""), json=body)
    assert answer.status_code == 422, answer.text


@pytest.mark.parametrize("batch", [False, True])
async def test_only_straddling_finalized_statement_periods_are_rejected(client_a_admin, april, batch):
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
        ("2025-08-01", "2025-08-31", 201),  # Contained.
        ("2025-07-01", "2025-08-31", 201),  # Shared start, contained.
        ("2025-08-01", "2025-09-30", 201),  # Shared end, contained.
        ("2025-04-01", "2025-12-31", 201),  # Containing.
        ("2025-07-01", "2025-12-31", 201),  # Shared start, containing.
        ("2025-04-01", "2025-09-30", 201),  # Shared end, containing.
        ("2025-07-01", "2025-09-30", 201),  # Identical; batch skips it.
        ("2025-04-01", "2025-06-30", 201),  # Adjacent.
        ("2025-10-01", "2025-12-31", 201),
    ):
        body = statement(april, start, end)
        if batch:
            body.pop("lease_agreement_id")
        answer = await api.post(path, json=body)
        assert answer.status_code == (200 if batch and status == 201 else status), answer.text
        if batch and status == 201:
            identical = (start, end) == ("2025-07-01", "2025-09-30")
            assert len(answer.json()["skipped" if identical else "created"]) == 1, answer.text
    await create(api, "/api/settlements", statement(april, "2026-04-01", "2026-12-31"))


@pytest.mark.parametrize("batch", [False, True])
async def test_overlapping_drafts_do_not_block_statement_creation(client_a_admin, april, batch):
    await create(client_a_admin, "/api/settlements", statement(april))
    body = statement(april, "2026-01-01", "2026-12-31")
    if batch:
        body.pop("lease_agreement_id")
    answer = await client_a_admin.post("/api/settlements" + ("/batch" if batch else ""), json=body)
    assert answer.status_code == (200 if batch else 201), answer.text


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
