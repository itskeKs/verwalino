"""Property periods, validation and frozen historical periods through the API."""
from decimal import Decimal
import pytest
from tests.settlement_period_support import period_property, create, statement, booked
from tests.financial_coverage_helpers import attest_fixture


@pytest.fixture
async def april(client_a_admin):
    return await period_property(client_a_admin)


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("start,end", [("2025-04-01", "2026-04-01"), ("2024-02-29", "2025-02-28"), ("2026-01-02", "2026-01-01")])
async def test_overlong_or_reversed_statement_is_rejected(client_a_admin, april, batch, start, end):
    body = statement(april, start, end)
    if batch:
        body.pop("lease_agreement_id")
    answer = await client_a_admin.post("/api/settlements" + ("/batch" if batch else ""), json=body)
    assert answer.status_code == 422, answer.text


@pytest.mark.parametrize("batch", [False, True])
async def test_overlap_is_rejected_but_identical_and_short_adjacent_periods_work(client_a_admin, april, batch):
    api = client_a_admin
    await create(api, "/api/settlements", statement(april))
    # Identical periods are allowed; the batch's existing skip semantics remain.
    identical = statement(april)
    overlap = statement(april, "2026-01-01", "2026-12-31")
    if batch:
        identical.pop("lease_agreement_id")
        overlap.pop("lease_agreement_id")
    path = "/api/settlements" + ("/batch" if batch else "")
    assert (await api.post(path, json=identical)).status_code in (200, 201)
    assert (await api.post(path, json=overlap)).status_code == 409
    await create(api, "/api/settlements", statement(april, "2026-04-01", "2026-12-31"))


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
