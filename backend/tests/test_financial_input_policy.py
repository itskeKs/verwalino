"""Financial completeness is explicit, scoped and checked by legacy endpoints."""
import pytest
from tests.test_m3_settlement import m3_seed, _extras_for_exit
from tests.test_m4_weg import m4_seed


async def draft(client, seed, amount):
    response = await client.post("/api/settlements", json={
        "property_id": seed["property"], "lease_agreement_id": seed["lease_a"],
        "period_start": "2025-01-01", "period_end": "2025-12-31",
        "advance_payments_total": amount,
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def attest(client, kind, subject_id, values):
    if kind == 'settlement' and 'advance_payments_total' in values:
        current = await client.get(f'/api/settlements/{subject_id}')
        if current.status_code == 200 and current.json()['run_type'] == 'BK_MIETER':
            values = {'heating_advance_payments_total': current.json()['heating_advance_payments_total'],
                'heating_advance_payments_source': current.json()['heating_advance_payments_source'], **values}
    return await client.post("/api/financial-input-coverage", json={
        "subject_kind": kind, "subject_id": subject_id, "values": values,
        "reason": "Synthetic complete account records checked for this period",
        "confirmed_complete": True, "source_document_ids": [],
    })


@pytest.mark.asyncio
async def test_unknown_heating_total_is_not_silently_replaced_with_zero(client_a_admin, m3_seed):
    response = await client_a_admin.post('/api/settlements', json={
        'property_id': m3_seed['property'], 'lease_agreement_id': m3_seed['lease_a'],
        'period_start': '2025-01-01', 'period_end': '2025-06-30',
        'advance_payments_total': '0.00', 'heating_advance_payments_total': None})
    assert response.status_code == 201, response.text
    assert response.json()['heating_advance_payments_total'] is None
    run_id = response.json()['id']
    coverage = await attest(client_a_admin, 'settlement', run_id, {
        'advance_payments_total': '0.00', 'advance_payments_source': 'MANUAL',
        'heating_advance_payments_total': None, 'heating_advance_payments_source': 'MANUAL'})
    assert coverage.status_code == 422, coverage.text
    assert coverage.json()['detail']['code'] == 'financial_inputs_incomplete'


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [None, "0.00", "1200.00"])
async def test_unattested_amount_is_unknown_on_every_rental_path(client_a_admin, m3_seed, amount):
    run_id = await draft(client_a_admin, m3_seed, amount)
    for action in ["compute", "preview", "finalize"]:
        response = await client_a_admin.post(f"/api/settlements/{run_id}/{action}", json=_extras_for_exit(m3_seed))
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "financial_inputs_incomplete"


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", ["0.00", "1200.00"])
async def test_known_amount_and_explicit_zero_finalize_with_evidence(client_a_admin, client_b_admin, m3_seed, amount):
    run_id = await draft(client_a_admin, m3_seed, amount)
    values = {"advance_payments_total": amount, "advance_payments_source": "MANUAL"}
    foreign = await attest(client_b_admin, "settlement", run_id, values)
    assert foreign.status_code == 404
    coverage = await attest(client_a_admin, "settlement", run_id, values)
    assert coverage.status_code == 201, coverage.text
    finalized = await client_a_admin.post(f"/api/settlements/{run_id}/finalize", json=_extras_for_exit(m3_seed))
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["advance_payments_total"] == amount


@pytest.mark.asyncio
async def test_changed_amount_requires_new_declaration(client_a_admin, m3_seed):
    run_id = await draft(client_a_admin, m3_seed, "1200.00")
    response = await attest(client_a_admin, "settlement", run_id,
        {"advance_payments_total": "1200.00", "advance_payments_source": "MANUAL"})
    assert response.status_code == 201
    await client_a_admin.patch(f"/api/settlements/{run_id}/advance-payments", json={"advance_payments_total": "1000.00"})
    result = await client_a_admin.post(f"/api/settlements/{run_id}/finalize", json=_extras_for_exit(m3_seed))
    assert result.status_code == 422


@pytest.mark.asyncio
async def test_plan_and_asset_omission_cannot_finalize(client_a_admin, m4_seed):
    for create_url, payload, finalize_url in [
        ("/api/economic-plans", {"plan_year": 2025}, "/api/economic-plans"),
        ("/api/weg-settlements/asset-reports", {}, "/api/weg-settlements/asset-reports"),
    ]:
        created = await client_a_admin.post(create_url, json={**payload, "property_id": m4_seed["property"],
            "period_start": "2025-01-01", "period_end": "2025-12-31"})
        assert created.status_code == 201
        result = await client_a_admin.post(f"{finalize_url}/{created.json()['id']}/finalize", json={})
        assert result.status_code == 422


@pytest.mark.asyncio
async def test_readonly_cannot_attest_and_parallel_retry_reuses_evidence(client_a_admin, client_a_readonly, m3_seed):
    import asyncio
    run_id = await draft(client_a_admin, m3_seed, "0.00")
    values = {"advance_payments_total": "0.00", "advance_payments_source": "MANUAL"}
    assert (await attest(client_a_readonly, "settlement", run_id, values)).status_code == 403
    first, second = await asyncio.gather(attest(client_a_admin, "settlement", run_id, values),
                                         attest(client_a_admin, "settlement", run_id, values))
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


@pytest.mark.asyncio
async def test_coverage_is_append_only_tenant_scoped_and_frozen(client_a_admin, app_rw_session, m3_seed):
    import uuid
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from app.engine.canonical import canonical_json, sha256_hex
    run_id = await draft(client_a_admin, m3_seed, "0.00")
    values = {"advance_payments_total": "0.00", "advance_payments_source": "MANUAL"}
    coverage = await attest(client_a_admin, "settlement", run_id, values)
    finalized = await client_a_admin.post(f"/api/settlements/{run_id}/finalize", json=_extras_for_exit(m3_seed))
    assert finalized.status_code == 200, finalized.text
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(m3_seed["org_a_id"])})
    payload = (await app_rw_session.execute(text("SELECT snapshot_payload FROM settlement_run WHERE id=:id"), {"id": uuid.UUID(run_id)})).scalar_one()
    assert payload["financial_input_coverage"]["id"] == coverage.json()["id"]
    assert sha256_hex(canonical_json(payload)) == finalized.json()["snapshot_sha256"]
    for sql in ("UPDATE coverage_declaration SET reason='changed' WHERE id=:id", "DELETE FROM coverage_declaration WHERE id=:id"):
        with pytest.raises(DBAPIError):
            async with app_rw_session.begin_nested():
                await app_rw_session.execute(text(sql), {"id": uuid.UUID(coverage.json()["id"])})
    await app_rw_session.execute(text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(m3_seed["org_b_id"])})
    assert (await app_rw_session.execute(text("SELECT id FROM coverage_declaration WHERE id=:id"), {"id": uuid.UUID(coverage.json()["id"])})).first() is None
    assert (await attest(client_a_admin, "settlement", run_id, values)).status_code == 409


@pytest.mark.asyncio
async def test_empty_owner_and_bank_lists_do_not_imply_coverage(client_a_admin, m4_seed):
    from tests.test_m4_weg import _create_and_finalize_plan
    plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
    for endpoint, extra, values, expected in [
        ("/api/weg-settlements", {"economic_plan_id": plan_id},
         {"reserve_override_reason": "Reviewed synthetic reserve totals", "reserve_opening_balance": "0", "reserve_withdrawals_total": "0", "paid_advances": []}, "owner_payment_coverage_missing"),
        ("/api/weg-settlements/asset-reports", {},
         {"reserve_override_reason": "Reviewed synthetic reserve totals", "reserve_opening_balance": "0", "reserve_withdrawals_total": "0", "reserve_contributions_total": "0",
          "bank_balances": [], "receivables": [], "payables": []}, "bank_balance_coverage_missing"),
    ]:
        created = await client_a_admin.post(endpoint, json={"property_id": m4_seed["property"],
            "period_start": "2025-01-01", "period_end": "2025-12-31", **extra})
        assert created.status_code == 201, created.text
        response = await attest(client_a_admin, "settlement", created.json()["id"], values)
        assert response.status_code == 422
        assert response.json()["detail"]["blockers"][0]["code"] == expected


@pytest.mark.asyncio
async def test_explicit_zero_budget_requires_and_accepts_declaration(client_a_admin, m4_seed):
    created = await client_a_admin.post("/api/economic-plans", json={"property_id": m4_seed["property"],
        "plan_year": 2025, "period_start": "2025-01-01", "period_end": "2025-12-31"})
    plan_id = created.json()["id"]
    values = {"planned_costs": [], "reserve_contribution_total": "0.00"}
    assert (await client_a_admin.post(f"/api/economic-plans/{plan_id}/finalize", json=values)).status_code == 422
    assert (await attest(client_a_admin, "economic_plan", plan_id, values)).status_code == 201
    finalized = await client_a_admin.post(f"/api/economic-plans/{plan_id}/finalize", json=values)
    assert finalized.status_code == 200, finalized.text


# Isolated property/lease fixture; the declared contractual monthly advance alone
# is never evidence that those receipts have arrived.
from tests.test_m6_zahlungen import m6_seed


@pytest.mark.asyncio
async def test_reversal_invalidates_derived_total_and_requires_rederivation(client_a_admin, m6_seed):
    created = await client_a_admin.post("/api/settlements", json={
        "property_id": str(m6_seed["prop_b"]), "lease_agreement_id": str(m6_seed["lease_d"]),
        "period_start": "2025-01-01", "period_end": "2025-12-31"})
    run_id = created.json()["id"]
    payment = await client_a_admin.post("/api/payments", json={
        "bank_account_id": str(m6_seed["acct_b"]), "direction": "IN", "amount": "100.00",
        "value_date": "2025-01-05", "payment_type": "NK_VORAUSZAHLUNG",
        "tenant_profile_id": str(m6_seed["tenant_d"]), "lease_agreement_id": str(m6_seed["lease_d"]),
        "period_year": 2025, "period_month": 1})
    assert payment.status_code == 201, payment.text
    payment_id = payment.json()["id"]
    assert (await client_a_admin.post(f"/api/payments/{payment_id}/book")).status_code == 200
    derived_url = f"/api/settlements/{run_id}/advance-payments-derived"
    derived = await client_a_admin.patch(derived_url, params={"period_year": 2025})
    assert derived.status_code == 200, derived.text
    values = {"advance_payments_total": derived.json()["advance_payments_total"], "advance_payments_source": "DERIVED"}
    assert (await attest(client_a_admin, "settlement", run_id, values)).status_code == 201
    assert (await client_a_admin.post(f"/api/payments/{payment_id}/reverse")).status_code == 201
    for response in [await client_a_admin.post(f"/api/settlements/{run_id}/finalize", json={}),
                     await attest(client_a_admin, "settlement", run_id, values)]:
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["blockers"][0]["code"] == "derived_total_stale"
    updated = await client_a_admin.patch(derived_url, params={"period_year": 2025})
    values["advance_payments_total"] = updated.json()["advance_payments_total"]
    assert (await attest(client_a_admin, "settlement", run_id, values)).status_code == 201
    final = await client_a_admin.post(f"/api/settlements/{run_id}/finalize", json={})
    assert final.status_code == 200, final.text


@pytest.mark.asyncio
async def test_derived_subperiod_accepts_matching_year_and_blocks_wrong_year_but_manual_evidence_works(client_a_admin, m6_seed):
    created = await client_a_admin.post("/api/settlements", json={
        "property_id": str(m6_seed["prop_a"]), "lease_agreement_id": str(m6_seed["lease"]),
        "period_start": "2025-01-01", "period_end": "2025-06-30"})
    run_id = created.json()["id"]
    for year in (2024, 2026):
        result = await client_a_admin.patch(f"/api/settlements/{run_id}/advance-payments-derived", params={"period_year": year})
        assert result.status_code == 422, result.text
    result = await client_a_admin.patch(f"/api/settlements/{run_id}/advance-payments-derived", params={"period_year": 2025})
    assert result.status_code == 200, result.text
    assert result.json()["advance_payments_total"] == "0.00"
    assert result.json()["advance_payments_source"] == "DERIVED"
    manual = await client_a_admin.patch(f"/api/settlements/{run_id}/advance-payments", json={"advance_payments_total": "0.00"})
    assert manual.status_code == 200, manual.text
    assert manual.json()["advance_payments_source"] == "MANUAL"
    assert (await attest(client_a_admin, "settlement", run_id, {"advance_payments_total": "0.00", "advance_payments_source": "MANUAL"})).status_code == 201
    final = await client_a_admin.post(f"/api/settlements/{run_id}/finalize", json={})
    assert final.status_code == 200, final.text


@pytest.mark.asyncio
async def test_draft_and_superseded_plan_cannot_supply_weg_soll(client_a_admin, m4_seed):
    from tests.financial_coverage_helpers import weg_values
    plan = await client_a_admin.post("/api/economic-plans", json={"property_id": m4_seed["property"],
        "plan_year": 2025, "period_start": "2025-01-01", "period_end": "2025-12-31"})
    plan_id = plan.json()["id"]
    run = await client_a_admin.post("/api/weg-settlements", json={"property_id": m4_seed["property"],
        "economic_plan_id": plan_id, "period_start": "2025-01-01", "period_end": "2025-12-31"})
    run_id = run.json()["id"]
    values = weg_values(m4_seed)
    result = await attest(client_a_admin, "settlement", run_id, values)
    assert result.status_code == 422
    assert result.json()["detail"]["blockers"][0]["code"] == "linked_plan_not_current"
    budget = {"planned_costs": [], "reserve_contribution_total": "0.00"}
    assert (await attest(client_a_admin, "economic_plan", plan_id, budget)).status_code == 201
    assert (await client_a_admin.post(f"/api/economic-plans/{plan_id}/finalize", json=budget)).status_code == 200
    assert (await attest(client_a_admin, "settlement", run_id, values)).status_code == 201
    correction = await client_a_admin.post(f"/api/economic-plans/{plan_id}/correction")
    assert correction.status_code == 201, correction.text
    result = await client_a_admin.post(f"/api/weg-settlements/{run_id}/finalize", json=values)
    assert result.status_code == 422
    assert result.json()["detail"]["blockers"][0]["code"] == "linked_plan_not_current"


@pytest.mark.asyncio
async def test_plan_correction_waits_until_weg_snapshot_is_frozen(client_a_admin, m4_seed, monkeypatch):
    import asyncio
    from tests.test_m4_weg import _create_and_finalize_plan
    from tests.financial_coverage_helpers import weg_values
    from app.repositories.coverage_declaration_repo import CoverageDeclarationRepository
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from tests.conftest import MIGRATOR_URL
    plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
    run = await client_a_admin.post("/api/weg-settlements", json={"property_id": m4_seed["property"],
        "economic_plan_id": plan_id, "period_start": "2025-01-01", "period_end": "2025-12-31"})
    run_id = run.json()["id"]
    values = weg_values(m4_seed)
    assert (await attest(client_a_admin, "settlement", run_id, values)).status_code == 201
    locked, release = asyncio.Event(), asyncio.Event()
    original_revision = CoverageDeclarationRepository.source_revision

    async def paused_revision(self, property_id, *, reserve=False):
        assert property_id == m4_seed["property"] or str(property_id) == m4_seed["property"]
        assert reserve is True
        locked.set()
        await release.wait()
        return await original_revision(self, property_id, reserve=reserve)

    monkeypatch.setattr(CoverageDeclarationRepository, "source_revision", paused_revision)
    final_task = asyncio.create_task(client_a_admin.post(f"/api/weg-settlements/{run_id}/finalize", json=values))
    await asyncio.wait_for(locked.wait(), 5)
    correction_task = asyncio.create_task(client_a_admin.post(f"/api/economic-plans/{plan_id}/correction"))
    observer_engine = create_async_engine(MIGRATOR_URL)
    try:
        # Scope integration locks the financial inputs before creating a
        # correction row. Observe that real wait rather than an unreachable
        # repository-create hook after the lock.
        async with observer_engine.connect() as observer:
            blocked = False
            for _ in range(300):
                await observer.execute(text('SELECT pg_stat_clear_snapshot()'))
                blocked = await observer.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE wait_event='advisory' AND query LIKE 'SELECT pg_advisory_xact_lock(%')"))
                if blocked: break
                await asyncio.sleep(.01)
            assert blocked and not correction_task.done()
    finally:
        release.set()
        try:
            final, correction = await asyncio.wait_for(asyncio.gather(final_task, correction_task), 10)
        finally:
            await observer_engine.dispose()
    assert final.status_code == 200, final.text
    assert correction.status_code == 201, correction.text
    frozen_hash = final.json()["snapshot_sha256"]
    assert (await client_a_admin.get(f"/api/weg-settlements/{run_id}")).json()["snapshot_sha256"] == frozen_hash


@pytest.mark.asyncio
async def test_rollback_switch_preserves_final_read_and_blocks_all_compute_families(client_a_admin, m3_seed, m4_seed, monkeypatch):
    from app.core.config import settings
    from tests.test_m4_weg import _create_and_finalize_plan
    from tests.financial_coverage_helpers import weg_values
    final_id = await draft(client_a_admin, m3_seed, "0.00")
    values = {"advance_payments_total": "0.00", "advance_payments_source": "MANUAL"}
    assert (await attest(client_a_admin, "settlement", final_id, values)).status_code == 201
    final = await client_a_admin.post(f"/api/settlements/{final_id}/finalize", json=_extras_for_exit(m3_seed))
    assert final.status_code == 200
    plan_id = await _create_and_finalize_plan(client_a_admin, m4_seed)
    rental_id = await draft(client_a_admin, m3_seed, "0.00")
    weg = await client_a_admin.post("/api/weg-settlements", json={"property_id": m4_seed["property"],
        "economic_plan_id": plan_id, "period_start": "2025-01-01", "period_end": "2025-12-31"})
    monkeypatch.setattr(settings, "financial_computation_enabled", False)
    for action in ("compute", "preview", "finalize"):
        assert (await client_a_admin.post(f"/api/settlements/{rental_id}/{action}", json=_extras_for_exit(m3_seed))).status_code == 503
    for action in ("compute", "finalize"):
        assert (await client_a_admin.post(f"/api/weg-settlements/{weg.json()['id']}/{action}", json=weg_values(m4_seed))).status_code == 503
    for endpoint, create_extra, body in [
        ("/api/economic-plans", {"plan_year": 2025}, {"planned_costs": [], "reserve_contribution_total": "0"}),
        ("/api/weg-settlements/asset-reports", {}, {"reserve_override_reason": "Reviewed synthetic reserve totals", "reserve_opening_balance": "0", "reserve_withdrawals_total": "0",
          "reserve_contributions_total": "0", "bank_balances": [], "receivables": [], "payables": []}),
    ]:
        created = await client_a_admin.post(endpoint, json={"property_id": m4_seed["property"],
            "period_start": "2025-01-01", "period_end": "2025-12-31", **create_extra})
        for action in ("compute", "finalize"):
            response = await client_a_admin.post(f"{endpoint}/{created.json()['id']}/{action}", json=body)
            assert response.status_code == 503, response.text
    read = await client_a_admin.get(f"/api/settlements/{final_id}")
    assert read.json()["snapshot_sha256"] == final.json()["snapshot_sha256"]
    assert (await client_a_admin.get(f"/api/settlements/{final_id}/pdf")).status_code == 200
